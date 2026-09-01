import hashlib
import json
import shutil
from pathlib import Path

import pytest

from jat.archive import ArchiveAdapter
from jat.models import (
    BuildRequest,
    CopyRequest,
    EnvironmentArtifactMetadata,
    ExportRequest,
    ExtractRequest,
    InspectRequest,
    RestoreRequest,
    ServeRequest,
)
from jat.process import ProcessResult, ProcessRunner
from pydantic import ValidationError
from jat.safety import ArchiveMember
from jat.services import (
    JATService,
    WORKSPACE_ARTIFACT as WORKSPACE_ARTIFACT_NAME,
    WORKSPACE_REFERENCE,
    _local_file_reference,
    _registry_config,
)


class FakeArchive:
    def __init__(self, members=None, fail_extract=False):
        self.calls = []
        self._members = members or [ArchiveMember("project", "directory"), ArchiveMember("project/file.txt", "file")]
        self.fail_extract = fail_extract

    def create(self, source, archive):
        self.calls.append(("create", source, archive))
        archive.write_bytes(b"archive")

    def members(self, archive):
        self.calls.append(("members", archive))
        return self._members

    def extract(self, archive, destination, strip_components=0):
        self.calls.append(("extract", archive, destination, strip_components))
        if self.fail_extract:
            raise RuntimeError("synthetic extraction failure")
        root = destination if strip_components else destination / "project"
        root.mkdir(parents=True, exist_ok=True)
        name = "Brewfile" if strip_components else "file.txt"
        (root / name).write_text("restored")
        if not strip_components:
            (root / "robot.yaml").write_text("tasks: {}\n")


class FakeHauler:
    def __init__(self, extracted_workspace=None, extracted_brew=None, on_info=None):
        self.calls = []
        self.extracted_workspace = extracted_workspace
        self.extracted_brew = extracted_brew
        self.on_info = on_info

    def sync(self, store, temp, *manifests, retries=None, exclude_extras=False):
        self.calls.append("sync")

    def sync_image_txt(self, store, temp, sources, retries=None, exclude_extras=False):
        self.calls.append("sync_image_txt")

    def save(self, store, temp, haul, chunk_size=None, containerd=False):
        self.calls.append("save")
        haul.write_bytes(b"synthetic-haul")

    def load(self, store, temp, haul):
        self.calls.append("load")

    def info(self, store, temp):
        self.calls.append("info")
        if self.on_info:
            self.on_info()

    def inventory(self, store, temp):
        self.calls.append("inventory")
        if self.on_info:
            self.on_info()
        references = [{"Reference": "hauler/joshs-all-the-things-workspace.tar.zst:latest", "Type": "file"}]
        if self.extracted_brew:
            references.append({"Reference": "hauler/homebrew-recovery.tar.zst:latest", "Type": "file"})
        return references

    def extract(self, reference, store, temp, output):
        self.calls.append("extract")
        output.mkdir(parents=True, exist_ok=True)
        if self.extracted_workspace and reference.endswith("joshs-all-the-things-workspace.tar.zst:latest"):
            target = output / "content" / "joshs-all-the-things-workspace.tar.zst"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"workspace")
        if self.extracted_brew and reference.endswith("homebrew-recovery.tar.zst:latest"):
            target = output / "recovery" / "homebrew-recovery.tar.zst"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"brew")


class RccHauler(FakeHauler):
    def __init__(
        self,
        *args,
        metadata_robot="robot.yaml",
        inventory_rcc=True,
        inventory_metadata=True,
        extract_rcc=True,
        extract_metadata=True,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.metadata_robot = metadata_robot
        self.inventory_rcc = inventory_rcc
        self.inventory_metadata = inventory_metadata
        self.extract_rcc = extract_rcc
        self.extract_metadata = extract_metadata

    def inventory(self, store, temp):
        references = super().inventory(store, temp)
        if self.inventory_rcc:
            references.append({"Reference": "hauler/rcc-environment.rcca:latest", "Type": "file"})
        if self.inventory_metadata:
            references.append({"Reference": "hauler/rcc-environment-metadata.json:latest", "Type": "file"})
        return references

    def extract(self, reference, store, temp, output):
        super().extract(reference, store, temp, output)
        if self.extract_rcc and reference.endswith("rcc-environment.rcca:latest"):
            target = output / "rcc" / "rcc-environment.rcca"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"rcca")
        if self.extract_metadata and reference.endswith("rcc-environment-metadata.json:latest"):
            target = output / "rcc" / "rcc-environment-metadata.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps({
                "artifact": "sha256:" + "b" * 64,
                "specification_digest": "sha256:" + "c" * 64,
                "legacy_blueprint_key": "d" * 16,
                "archive": str(target.parent / "rcc-environment.rcca"),
                "archive_sha256": hashlib.sha256(b"rcca").hexdigest(),
                "archive_size": 4,
                "rcc_version": "v18.19.3",
                "robot": self.metadata_robot,
                "provider": "local",
                "acquired": False,
            }))


class FakeRcc:
    def __init__(self):
        self.calls = []

    def publish_and_export(self, source, archive, robot=None):
        self.calls.append(("publish_and_export", source, archive, robot))
        archive.write_bytes(b"rcca")
        return EnvironmentArtifactMetadata(
            artifact="sha256:" + "b" * 64,
            specification_digest="sha256:" + "c" * 64,
            legacy_blueprint_key="d" * 16,
            archive=archive,
            archive_sha256=hashlib.sha256(b"rcca").hexdigest(),
            archive_size=4,
            rcc_version="v18.19.3",
            robot=robot or source / "robot.yaml",
        )

    def acquire(self, archive, robot, rcc_version=None, specification_digest=None, legacy_blueprint_key=None):
        self.calls.append(("acquire", archive, robot, rcc_version, specification_digest, legacy_blueprint_key))
        return EnvironmentArtifactMetadata(
            artifact="sha256:" + "b" * 64,
            specification_digest="sha256:" + "c" * 64,
            legacy_blueprint_key="d" * 16,
            archive=archive,
            archive_sha256=hashlib.sha256(b"rcca").hexdigest(),
            archive_size=4,
            rcc_version=rcc_version or "v18.19.3",
            robot=robot,
            acquired=True,
        )

    def verify(self, robot):
        self.calls.append(("verify", robot))


def service(tmp_path, archive=None, hauler=None, rcc=None):
    return JATService(
        archive=archive or FakeArchive(),
        hauler=hauler or FakeHauler(),
        rcc=rcc,
        producer_version="synthetic-version",
        which=lambda command: f"/tools/{command}",
    )


def test_windows_hauler_manifest_file_reference_is_a_file_uri():
    assert _local_file_reference(Path("D:/work/payload.tar.zst"), windows=True) == "file://D:/work/payload.tar.zst"
    assert _local_file_reference(Path("D:/work dir/payload.tar.zst"), windows=True) == "file://D:/work%20dir/payload.tar.zst"
    with pytest.raises(ValueError, match="UNC"):
        _local_file_reference(Path("//server/share/payload.tar.zst"), windows=True)


def test_linux_hauler_manifest_file_reference_escapes_spaces():
    assert _local_file_reference(Path("/tmp/work dir/payload.tar.zst"), windows=False) == "file:///tmp/work%20dir/payload.tar.zst"


def test_registry_config_uses_loopback_and_quotes_windows_root_path():
    config = _registry_config(Path("D:/josh room/registry"))
    assert 'rootdirectory: "D:/josh room/registry"' in config
    assert 'addr: "127.0.0.1:5000"' in config


def test_build_publishes_one_rcc_environment_artifact_when_selected(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "robot.yaml").write_text("tasks: {}\n")
    output = tmp_path / "haul.tar.zst"
    rcc = FakeRcc()
    result = service(tmp_path, hauler=RccHauler(), rcc=rcc).build(
        BuildRequest(folder=source, output=output, rcc_environment="required")
    )
    assert result.success, result.diagnostics
    assert rcc.calls[0][0] == "publish_and_export"
    assert rcc.calls[0][3] == source / "robot.yaml"
    assert result.environment_artifact.artifact == "sha256:" + "b" * 64


def test_build_rejects_resolved_robocorp_home_beneath_source_before_archive_creation(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    embedded_home = source / "embedded-home"
    embedded_home.mkdir()
    home_link = tmp_path / "active-home"
    home_link.symlink_to(embedded_home, target_is_directory=True)
    monkeypatch.setenv("ROBOCORP_HOME", str(home_link / "nonexistent-tail"))
    archive = FakeArchive()

    result = service(tmp_path, archive=archive).build(
        BuildRequest(folder=source, output=tmp_path / "haul.tar.zst")
    )

    assert result.success is False
    assert "ROBOCORP_HOME" in result.diagnostics
    assert archive.calls == []


def test_auto_rcc_without_descriptor_keeps_legacy_build(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    output = tmp_path / "haul.tar.zst"
    result = JATService(
        archive=FakeArchive(),
        hauler=FakeHauler(),
        producer_version="synthetic-version",
        which=lambda command: "/tools/rcc" if command == "rcc" else f"/tools/{command}",
    ).build(BuildRequest(folder=source, output=output, rcc_environment="auto"))
    assert result.success, result.diagnostics
    assert result.environment_artifact is None


def test_explicit_rcc_robot_under_source_wins_over_default(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "robot.yaml").write_text("default\n")
    nested = source / "robots" / "portable.yaml"
    nested.parent.mkdir()
    nested.write_text("explicit\n")
    rcc = FakeRcc()
    result = service(tmp_path, hauler=RccHauler(), rcc=rcc).build(
        BuildRequest(folder=source, output=tmp_path / "haul.tar.zst", rcc_environment="required", rcc_robot=nested)
    )
    assert result.success, result.diagnostics
    assert rcc.calls[0][3] == nested


def test_explicit_rcc_robot_rejects_resolved_escape_and_symlink_parent(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    robot = outside / "robot.yaml"
    robot.write_text("tasks: {}\n")
    link = source / "linked"
    link.symlink_to(outside, target_is_directory=True)

    escaped = service(tmp_path, hauler=RccHauler(), rcc=FakeRcc()).build(
        BuildRequest(
            folder=source,
            output=tmp_path / "escaped.tar.zst",
            rcc_environment="required",
            rcc_robot=Path("../outside/robot.yaml"),
        )
    )
    linked = service(tmp_path, hauler=RccHauler(), rcc=FakeRcc()).build(
        BuildRequest(
            folder=source,
            output=tmp_path / "linked.tar.zst",
            rcc_environment="required",
            rcc_robot=link / "robot.yaml",
        )
    )

    assert escaped.success is False
    assert linked.success is False
    assert "under the workspace source" in escaped.diagnostics
    assert "under the workspace source" in linked.diagnostics


def test_restore_acquires_and_verifies_rcc_before_promotion(tmp_path):
    haul = tmp_path / "haul.tar.zst"
    haul.write_bytes(b"haul")
    destination = tmp_path / "restored"
    rcc = FakeRcc()
    result = service(tmp_path, hauler=RccHauler(extracted_workspace=True), rcc=rcc).restore(
        RestoreRequest(haul=haul, destination=destination)
    )
    assert result.success, result.diagnostics
    assert [call[0] for call in rcc.calls] == ["acquire", "verify"]
    assert destination.exists()


def test_restore_rejects_claimed_rcc_artifact_missing_after_extraction(tmp_path):
    haul = tmp_path / "haul.tar.zst"
    haul.write_bytes(b"haul")
    destination = tmp_path / "restored"
    result = service(
        tmp_path,
        hauler=RccHauler(extracted_workspace=True, extract_rcc=False),
        rcc=FakeRcc(),
    ).restore(RestoreRequest(haul=haul, destination=destination))

    assert result.success is False
    assert "exactly one RCC environment artifact" in result.diagnostics
    assert not destination.exists()


def test_restore_rejects_metadata_only_rcc_inventory(tmp_path):
    haul = tmp_path / "haul.tar.zst"
    haul.write_bytes(b"haul")
    result = service(
        tmp_path,
        hauler=RccHauler(extracted_workspace=True, inventory_rcc=False, inventory_metadata=True),
        rcc=FakeRcc(),
    ).restore(RestoreRequest(haul=haul, destination=tmp_path / "restored"))

    assert result.success is False
    assert "RCC environment artifact and metadata must appear together" in result.diagnostics


def test_restore_returns_only_stable_environment_artifact_paths(tmp_path):
    haul = tmp_path / "haul.tar.zst"
    haul.write_bytes(b"haul")
    result = service(tmp_path, hauler=RccHauler(extracted_workspace=True), rcc=FakeRcc()).restore(
        RestoreRequest(haul=haul, destination=tmp_path / "restored")
    )

    assert result.success, result.diagnostics
    assert result.environment_artifact.archive == Path("rcc-environment.rcca")
    assert result.environment_artifact.robot == Path("robot.yaml")


def test_restore_rejects_when_metadata_robot_path_does_not_match_restored_workspace(tmp_path):
    haul = tmp_path / "haul.tar.zst"
    haul.write_bytes(b"haul")
    result = service(tmp_path, hauler=RccHauler(extracted_workspace=True, metadata_robot="nested/portable.yaml"), rcc=FakeRcc()).restore(
        RestoreRequest(haul=haul, destination=tmp_path / "restored")
    )
    assert result.success is False
    assert "saved robot path" in result.diagnostics


def test_build_is_create_only_and_validates_before_publication(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "file.txt").write_text("source")
    output = tmp_path / "haul.tar.zst"
    hauler = FakeHauler()
    result = service(tmp_path, hauler=hauler).build(BuildRequest(folder=source, output=output))
    assert result.success is True
    assert output.read_bytes() == b"synthetic-haul"
    assert result.payload_size == len(b"synthetic-haul")
    assert len(result.sha256) == 64
    assert hauler.calls == ["sync", "save", "load", "inventory"]

    second = service(tmp_path).build(BuildRequest(folder=source, output=output))
    assert second.success is False
    assert output.read_bytes() == b"synthetic-haul"
    assert "already exists" in second.diagnostics


def test_build_accepts_workspace_with_safe_internal_symlink(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "file.txt").write_text("source")
    (source / "current.txt").symlink_to("file.txt")
    output = tmp_path / "haul.tar.zst"
    hauler = FakeHauler()

    result = service(tmp_path, archive=ArchiveAdapter(ProcessRunner()), hauler=hauler).build(
        BuildRequest(folder=source, output=output)
    )

    assert result.success, result.diagnostics
    assert hauler.calls == ["sync", "save", "load", "inventory"]


@pytest.mark.parametrize("target", ["/etc/passwd", "../outside.txt"])
def test_build_rejects_unsafe_workspace_symlink_before_hauler_publication(tmp_path, target):
    source = tmp_path / "source"
    source.mkdir()
    (source / "file.txt").write_text("source")
    (tmp_path / "outside.txt").write_text("outside")
    (source / "current.txt").symlink_to(target)
    output = tmp_path / "haul.tar.zst"
    hauler = FakeHauler()

    result = service(tmp_path, archive=ArchiveAdapter(ProcessRunner()), hauler=hauler).build(
        BuildRequest(folder=source, output=output)
    )

    assert result.success is False
    assert "current.txt" in result.diagnostics
    assert hauler.calls == []
    assert not output.exists()


def test_build_race_never_overwrites_competing_output(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    output = tmp_path / "haul.tar.zst"
    hauler = FakeHauler(on_info=lambda: output.write_bytes(b"competitor"))
    result = service(tmp_path, hauler=hauler).build(BuildRequest(folder=source, output=output))
    assert result.success is False
    assert output.read_bytes() == b"competitor"


def test_explicit_images_fail_closed_but_all_images_may_degrade(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    output = tmp_path / "haul.tar.zst"
    unavailable = JATService(
        archive=FakeArchive(),
        hauler=FakeHauler(),
        producer_version="synthetic-version",
        which=lambda command: None if command == "docker" else f"/tools/{command}",
    )
    explicit = unavailable.build(BuildRequest(folder=source, output=output, images=["example/image:latest"]))
    assert explicit.success is False
    assert "Docker" in explicit.diagnostics

    degraded = unavailable.build(BuildRequest(folder=source, output=output, all_images=True))
    assert degraded.success is True


def test_restore_promotes_separate_reserved_directories(tmp_path):
    haul = tmp_path / "haul.tar.zst"
    haul.write_bytes(b"haul")
    destination = tmp_path / "restored"
    archive = FakeArchive()
    hauler = FakeHauler(extracted_workspace=True, extracted_brew=True)
    result = service(tmp_path, archive=archive, hauler=hauler).restore(
        RestoreRequest(haul=haul, destination=destination)
    )
    assert result.success is True
    assert (destination / "workspace" / "project" / "file.txt").read_text() == "restored"
    assert (destination / "homebrew-recovery" / "Brewfile").read_text() == "restored"


def test_restore_rejects_nonempty_and_preserves_it_on_failure(tmp_path):
    haul = tmp_path / "haul.tar.zst"
    haul.write_bytes(b"haul")
    destination = tmp_path / "restored"
    destination.mkdir()
    existing = destination / "existing.txt"
    existing.write_text("keep")
    result = service(tmp_path).restore(RestoreRequest(haul=haul, destination=destination))
    assert result.success is False
    assert existing.read_text() == "keep"

    empty = tmp_path / "empty"
    empty.mkdir()
    failure = service(
        tmp_path,
        archive=FakeArchive(fail_extract=True),
        hauler=FakeHauler(extracted_workspace=True),
    ).restore(RestoreRequest(haul=haul, destination=empty))
    assert failure.success is False
    assert empty.exists() and not any(empty.iterdir())


def test_restore_rejects_unsafe_archive_before_destination_promotion(tmp_path):
    haul = tmp_path / "haul.tar.zst"
    haul.write_bytes(b"haul")
    destination = tmp_path / "restored"
    archive = FakeArchive(
        members=[ArchiveMember("project", "directory"), ArchiveMember("project/link", "symlink")]
    )
    result = service(tmp_path, archive=archive, hauler=FakeHauler(extracted_workspace=True)).restore(
        RestoreRequest(haul=haul, destination=destination)
    )
    assert result.success is False
    assert not destination.exists()
    assert "project/link" in result.diagnostics
    assert "symlink target is missing" in result.diagnostics


def test_restore_rejects_transitive_symlink_escape_without_external_write(tmp_path):
    source = tmp_path / "project"
    (source / "sub").mkdir(parents=True)
    (source / "a").symlink_to("sub/..", target_is_directory=True)
    (source / "b").symlink_to("a/..", target_is_directory=True)
    (source / "c").symlink_to("b/..", target_is_directory=True)
    (source / "link").symlink_to("c/../outside", target_is_directory=True)
    archive_path = tmp_path / "workspace.tar.zst"
    ArchiveAdapter(ProcessRunner()).create(source, archive_path)

    class ArchiveHauler(FakeHauler):
        def extract(self, reference, store, temp, output):
            self.calls.append("extract")
            output.mkdir(parents=True, exist_ok=True)
            target = output / "workspace" / WORKSPACE_ARTIFACT_NAME
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(archive_path, target)

    class TrackingArchive(ArchiveAdapter):
        def members(self, archive):
            self.outside = archive.parent / "outside"
            self.outside.mkdir()
            self.victim = self.outside / "victim.txt"
            self.victim.write_text("keep")
            result = super().members(archive)
            self.victim_after = self.victim.read_text()
            return result

        def extract(self, archive, destination, strip_components=0):
            try:
                return super().extract(archive, destination, strip_components)
            finally:
                self.victim_after = self.victim.read_text() if self.victim.exists() else None

    adapter = TrackingArchive(ProcessRunner())
    destination = tmp_path / "restored"
    haul = tmp_path / "haul.tar.zst"
    haul.write_bytes(b"haul")
    result = service(tmp_path, archive=adapter, hauler=ArchiveHauler()).restore(
        RestoreRequest(haul=haul, destination=destination)
    )

    assert result.success is False
    assert "project/link" in result.diagnostics
    assert "escapes the archive root" in result.diagnostics
    assert not destination.exists()
    assert adapter.victim_after == "keep"


def test_serve_uses_explicit_runtime_stage_directory_instead_of_cwd(tmp_path, monkeypatch):
    haul = tmp_path / "haul.tar.zst"
    haul.write_bytes(b"haul")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    observed = {}

    class ServingHauler(FakeHauler):
        def serve_files(self, store, temp, directory, port):
            self.calls.append("serve_files")
            observed["stage_parent"] = Path(store).parent.parent

    monkeypatch.setenv("JAT_RUN_DIR", str(runtime))
    result = service(tmp_path, hauler=ServingHauler()).serve(ServeRequest(haul=haul))

    assert result.success is True, result.diagnostics
    assert observed["stage_parent"] == runtime


def test_serve_uses_fileserver_for_files_only_inventory(tmp_path, monkeypatch):
    haul = tmp_path / "haul.tar.zst"
    haul.write_bytes(b"haul")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    observed = {}

    class ServingHauler(FakeHauler):
        def serve(self, *args):
            raise AssertionError("Files-only Serve must not invoke the OCI registry")

        def serve_files(self, store, temp, directory, port):
            observed.update(store=store, temp=temp, directory=directory, port=port)

    monkeypatch.setenv("JAT_RUN_DIR", str(runtime))
    result = service(tmp_path, hauler=ServingHauler()).serve(ServeRequest(haul=haul))

    assert result.success is True, result.diagnostics
    assert observed["port"] == 8080
    assert Path(observed["store"]).parent.parent == runtime


def test_serve_uses_registry_for_image_inventory(tmp_path, monkeypatch):
    haul = tmp_path / "haul.tar.zst"
    haul.write_bytes(b"haul")
    observed = []

    class ImageHauler(FakeHauler):
        def inventory(self, store, temp):
            return [
                {"Reference": "hauler/app:latest", "Type": "image"},
                {"Reference": "hauler/workspace.tar.zst:latest", "Type": "file"},
            ]

        def serve(self, *args):
            observed.append("registry")

        def serve_files(self, *args):
            observed.append("fileserver")

    result = service(tmp_path, hauler=ImageHauler()).serve(ServeRequest(haul=haul))

    assert result.success is True, result.diagnostics
    assert observed == ["registry"]


def test_serve_rejects_runtime_stage_directory_inside_conda_prefix(tmp_path, monkeypatch):
    haul = tmp_path / "haul.tar.zst"
    haul.write_bytes(b"haul")
    conda_prefix = tmp_path / "holotree"
    runtime = conda_prefix / "output"
    runtime.mkdir(parents=True)

    class ServingHauler(FakeHauler):
        def serve(self, store, temp, directory, config):
            self.calls.append("serve")

    monkeypatch.setenv("CONDA_PREFIX", str(conda_prefix))
    monkeypatch.setenv("JAT_RUN_DIR", str(runtime))
    result = service(tmp_path, hauler=ServingHauler()).serve(ServeRequest(haul=haul))

    assert result.success is False
    assert "outside the acquired environment" in result.diagnostics


class CapsuleRunner:
    def __init__(self, supervise_result=None):
        self.supervised = None
        self.supervise_result = supervise_result

    def supervise(self, argvs, timeout=None, secrets=()):
        self.supervised = argvs
        return self.supervise_result or ProcessResult(argv=[a for argv in argvs for a in argv], exit_status=0)


class CapsuleHauler:
    def __init__(self, inventory, extracted=None, chunk_count=0):
        self.calls = []
        self.inventory_data = inventory
        self.extracted = extracted or {}
        self.chunk_count = chunk_count

    def load(self, store, temp, haul):
        self.calls.append(("load", Path(haul).name))

    def inventory(self, store, temp):
        self.calls.append(("inventory",))
        return self.inventory_data

    def extract(self, reference, store, temp, output):
        self.calls.append(("extract", reference))
        for name, content in self.extracted.get(reference, {}).items():
            target = output / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)

    def save(self, store, temp, haul, chunk_size=None, containerd=False):
        self.calls.append(("save", chunk_size, containerd))
        if containerd:
            Path(haul).write_bytes(b"containerd-tar")
            return
        if chunk_size:
            name = Path(haul).name
            base = name.split(".", 1)[0]
            ext = name[len(base):]
            parent = Path(haul).parent
            for index in range(self.chunk_count):
                (parent / f"{base}_{index}{ext}").write_bytes(f"chunk{index}".encode())
            return
        Path(haul).write_bytes(b"synthetic-haul")

    def copy(self, store, temp, target, retries=None, plain_http=False, insecure=False):
        self.calls.append(("copy", target, retries, plain_http, insecure))

    def serve_files(self, store, temp, directory, port):
        self.calls.append(("serve_files", port))

    def serve(self, store, temp, directory, config, port=None):
        self.calls.append(("serve_registry",))

    def serve_fileserver_command(self, store, temp, directory, port):
        return ["hauler", "fileserver", str(port)]

    def serve_registry_command(self, store, temp, directory, config):
        return ["hauler", "registry"]

    def sync(self, store, temp, *manifests, retries=None, exclude_extras=False):
        self.calls.append(("sync", manifests, retries, exclude_extras))

    def sync_image_txt(self, store, temp, sources, retries=None, exclude_extras=False):
        self.calls.append(("sync_image_txt", sources, retries, exclude_extras))


WORKSPACE_ONLY_INVENTORY = [{"Reference": WORKSPACE_REFERENCE, "Type": "file"}]
MIXED_INVENTORY = [
    {"Reference": WORKSPACE_REFERENCE, "Type": "file"},
    {"Reference": "hauler/app:latest", "Type": "image", "Platform": "linux/amd64", "Size": 1234},
    {"Reference": "hauler/extra-notes.txt:latest", "Type": "file"},
]


def capsule_service(tmp_path, hauler, runner=None):
    return JATService(
        archive=FakeArchive(),
        hauler=hauler,
        runner=runner,
        producer_version="synthetic-version",
        which=lambda command: f"/tools/{command}",
    )


def test_inspect_returns_normalized_inventory_and_anchors_without_restoring(tmp_path, monkeypatch):
    monkeypatch.setenv("JAT_RUN_DIR", str(tmp_path / "run"))
    haul = tmp_path / "haul.tar.zst"
    haul.write_bytes(b"haul")
    result = capsule_service(tmp_path, CapsuleHauler(MIXED_INVENTORY)).inspect(InspectRequest(haul=haul))
    assert result.success, result.diagnostics
    assert result.format_version == 2
    assert [entry.reference for entry in result.inventory] == [
        WORKSPACE_REFERENCE,
        "hauler/app:latest",
        "hauler/extra-notes.txt:latest",
    ]
    image = result.inventory[1]
    assert image.type == "image" and image.size == 1234 and image.platform == "linux/amd64"
    assert result.anchors == {"workspace": True, "brew": False, "rcc_environment": False, "rcc_metadata": False}
    assert result.complete is True
    assert not any((tmp_path / "run").iterdir()), "inspect must clean up its owned stage"


def test_extract_extracts_one_reference_and_records_outputs(tmp_path):
    haul = tmp_path / "haul.tar.zst"
    haul.write_bytes(b"haul")
    hauler = CapsuleHauler(
        MIXED_INVENTORY,
        extracted={"hauler/extra-notes.txt:latest": {"content/notes.txt": b"notes"}},
    )
    destination = tmp_path / "out"
    result = capsule_service(tmp_path, hauler).extract(
        ExtractRequest(haul=haul, reference="hauler/extra-notes.txt:latest", destination=destination)
    )
    assert result.success, result.diagnostics
    assert (destination / "content" / "notes.txt").read_bytes() == b"notes"
    assert result.payloads[0].path == destination / "content" / "notes.txt"
    assert result.payloads[0].sha256 == hashlib.sha256(b"notes").hexdigest()
    assert ("extract", "hauler/extra-notes.txt:latest") in hauler.calls


def test_extract_rejects_missing_reference_without_touching_destination(tmp_path):
    haul = tmp_path / "haul.tar.zst"
    haul.write_bytes(b"haul")
    destination = tmp_path / "out"
    result = capsule_service(tmp_path, CapsuleHauler(MIXED_INVENTORY)).extract(
        ExtractRequest(haul=haul, reference="hauler/absent:latest", destination=destination)
    )
    assert result.success is False
    assert "does not contain reference" in result.diagnostics
    assert not destination.exists()


def test_extract_never_overwrites_an_existing_destination(tmp_path):
    haul = tmp_path / "haul.tar.zst"
    haul.write_bytes(b"haul")
    destination = tmp_path / "out"
    destination.mkdir()
    keep = destination / "keep.txt"
    keep.write_text("keep")
    result = capsule_service(tmp_path, CapsuleHauler(MIXED_INVENTORY)).extract(
        ExtractRequest(haul=haul, reference="hauler/extra-notes.txt:latest", destination=destination)
    )
    assert result.success is False
    assert keep.read_text() == "keep"


def test_export_delegates_containerd_save_and_records_evidence(tmp_path):
    haul = tmp_path / "haul.tar.zst"
    haul.write_bytes(b"haul")
    output = tmp_path / "images.tar"
    hauler = CapsuleHauler(MIXED_INVENTORY)
    result = capsule_service(tmp_path, hauler).export(ExportRequest(haul=haul, output=output))
    assert result.success, result.diagnostics
    assert ("save", None, True) in hauler.calls
    assert output.read_bytes() == b"containerd-tar"
    assert result.payloads[0].path == output
    assert result.payloads[0].sha256 == hashlib.sha256(b"containerd-tar").hexdigest()
    assert result.payloads[0].size == len(b"containerd-tar")


def test_copy_rejects_unsupported_targets_and_reports_transfer_policy(tmp_path, monkeypatch):
    monkeypatch.setenv("JAT_RUN_DIR", str(tmp_path / "run"))
    haul = tmp_path / "haul.tar.zst"
    haul.write_bytes(b"haul")
    hauler = CapsuleHauler(MIXED_INVENTORY)
    service = capsule_service(tmp_path, hauler)
    with pytest.raises(ValidationError):
        CopyRequest(haul=haul, to="s3://bucket/prefix")
    moved = service.copy(CopyRequest(haul=haul, to="registry://registry.example.test", retries=5))
    assert moved.success, moved.diagnostics
    assert ("copy", "registry://registry.example.test", 5, False, False) in hauler.calls
    assert moved.transfer.destination == "registry://registry.example.test"
    assert moved.transfer.transport == "remote-registry"
    assert moved.transfer.requested_retries == 5


def test_serve_modes_select_endpoints_explicitly(tmp_path, monkeypatch):
    haul = tmp_path / "haul.tar.zst"
    haul.write_bytes(b"haul")
    runtime = tmp_path / "runtime"
    runtime.mkdir()

    monkeypatch.setenv("JAT_RUN_DIR", str(runtime))
    hauler = CapsuleHauler(MIXED_INVENTORY)
    files_result = capsule_service(tmp_path, hauler).serve(ServeRequest(haul=haul, mode="files"))
    assert files_result.success, files_result.diagnostics
    assert ("serve_files", 8080) in hauler.calls
    assert files_result.serve.fileserver_bind == "all-interfaces"

    hauler = CapsuleHauler(MIXED_INVENTORY)
    registry_result = capsule_service(tmp_path, hauler).serve(ServeRequest(haul=haul, mode="registry", registry_port=5001))
    assert ("serve_registry",) in hauler.calls
    assert registry_result.serve.registry_url == "http://127.0.0.1:5001"

    files_only = capsule_service(tmp_path, CapsuleHauler(WORKSPACE_ONLY_INVENTORY)).serve(
        ServeRequest(haul=haul, mode="auto")
    )
    assert files_only.serve.mode == "files"
    mixed_auto = capsule_service(tmp_path, CapsuleHauler(MIXED_INVENTORY)).serve(
        ServeRequest(haul=haul, mode="auto")
    )
    assert mixed_auto.serve.mode == "registry"


def test_serve_both_supervises_two_children_from_one_capsule(tmp_path, monkeypatch):
    haul = tmp_path / "haul.tar.zst"
    haul.write_bytes(b"haul")
    monkeypatch.setenv("JAT_RUN_DIR", str(tmp_path / "runtime"))
    runner = CapsuleRunner()
    hauler = CapsuleHauler(MIXED_INVENTORY)
    result = capsule_service(tmp_path, hauler, runner=runner).serve(
        ServeRequest(haul=haul, mode="both", fileserver_port=8081, registry_port=5001)
    )
    assert result.success, result.diagnostics
    assert runner.supervised == [["hauler", "fileserver", "8081"], ["hauler", "registry"]]
    assert result.serve.mode == "both"
    assert result.serve.fileserver_url == "http://127.0.0.1:8081"
    assert result.serve.registry_url == "http://127.0.0.1:5001"


def test_serve_both_fails_when_a_sibling_exits_unexpectedly(tmp_path, monkeypatch):
    haul = tmp_path / "haul.tar.zst"
    haul.write_bytes(b"haul")
    monkeypatch.setenv("JAT_RUN_DIR", str(tmp_path / "runtime"))
    runner = CapsuleRunner(supervise_result=ProcessResult(argv=["hauler"], exit_status=1, stderr="boom"))
    result = capsule_service(tmp_path, CapsuleHauler(MIXED_INVENTORY), runner=runner).serve(
        ServeRequest(haul=haul, mode="both")
    )
    assert result.success is False
    assert not (tmp_path / "runtime" / ".jat-serve-tar.zst").exists()


def test_build_merge_user_manifests_images_file_and_retry_policy(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    images_file = tmp_path / "images.txt"
    images_file.write_text("busybox:1.36\n")
    remote_manifest = "https://example.test/product.yaml"
    output = tmp_path / "haul.tar.zst"
    hauler = CapsuleHauler([{ "Reference": WORKSPACE_REFERENCE, "Type": "file"}])
    result = capsule_service(tmp_path, hauler).build(
        BuildRequest(
            folder=source,
            output=output,
            images_files=[str(images_file)],
            hauler_manifests=[remote_manifest],
            exclude_extras=True,
            retries=2,
        )
    )
    assert result.success, result.diagnostics
    syncs = [call for call in hauler.calls if call[0] == "sync"]
    assert len(syncs[0][1]) == 1 and str(syncs[0][1][0]).endswith("manifest.yaml")
    assert syncs[0][2] == 2 and syncs[0][3] is True
    assert syncs[1] == ("sync", (remote_manifest,), 2, True), "user manifests are passed exactly as provided"
    assert [call for call in hauler.calls if call[0] == "sync_image_txt"] == [
        ("sync_image_txt", [str(images_file)], 2, True)
    ]
    assert ("save", None, False) in hauler.calls


def test_build_rejects_absent_local_capture_sources_before_staging(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    result = capsule_service(tmp_path, CapsuleHauler(MIXED_INVENTORY)).build(
        BuildRequest(folder=source, output=tmp_path / "haul.tar.zst", hauler_manifests=["./absent.yaml"])
    )
    assert result.success is False
    assert "absent.yaml" in result.diagnostics


def test_build_chunked_promotes_all_chunks_or_none(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    output = tmp_path / "haul.tar.zst"
    hauler = CapsuleHauler(WORKSPACE_ONLY_INVENTORY, chunk_count=3)
    result = capsule_service(tmp_path, hauler).build(
        BuildRequest(folder=source, output=output, chunk_size="1MB")
    )
    assert result.success, result.diagnostics
    assert result.format_version == 2
    assert result.payload_path is None, "one path must never silently mean a set"
    names = [chunk.path.name for chunk in result.payloads]
    assert names == ["haul_0.tar.zst", "haul_1.tar.zst", "haul_2.tar.zst"]
    for chunk in result.payloads:
        assert chunk.size == len(f"chunk{names.index(chunk.path.name)}")
        assert chunk.sha256 == hashlib.sha256(f"chunk{names.index(chunk.path.name)}".encode()).hexdigest()
    assert ("save", "1MB", False) in hauler.calls
    loads = [call for call in hauler.calls if call[0] == "load"]
    assert loads[-1] == ("load", "haul_0.tar.zst"), "validation reloads the documented chunk entrypoint"
    assert result.complete is True


def test_build_chunked_failure_leaves_no_partial_final_set(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    output = tmp_path / "haul.tar.zst"

    class ExplodingCapsuleHauler(CapsuleHauler):
        def inventory(self, store, temp):
            raise RuntimeError("validation failed")

    result = capsule_service(tmp_path, ExplodingCapsuleHauler(WORKSPACE_ONLY_INVENTORY, chunk_count=2)).build(
        BuildRequest(folder=source, output=output, chunk_size="1MB")
    )
    assert result.success is False
    assert not list(tmp_path.glob("haul_*.tar.zst"))


def test_registry_config_accepts_port_override():
    config = _registry_config(Path("D:/josh room/registry"), 5001)
    assert 'addr: "127.0.0.1:5001"' in config
    assert 'rootdirectory: "D:/josh room/registry"' in config


def test_build_chunked_rejects_output_names_hauler_cannot_reload(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    # Hauler v2.0.3 can split any container, but its own store load cannot
    # re-consume anything but tar/tar.zst chunk sets; JAT rejects the rest
    # before capture instead of producing an unreadable capsule.
    for output_name in ("capsule.zip", "haul", ".hidden-haul", "capsule.tar.gz"):
        hauler = CapsuleHauler(WORKSPACE_ONLY_INVENTORY, chunk_count=2)
        result = capsule_service(tmp_path, hauler).build(
            BuildRequest(folder=source, output=tmp_path / output_name, chunk_size="1MB")
        )
        assert result.success is False, output_name
        assert "must be a .tar or .tar.zst archive name" in result.diagnostics
        assert ("save", "1MB", False) not in hauler.calls, output_name

    assert capsule_service(tmp_path, CapsuleHauler(WORKSPACE_ONLY_INVENTORY, chunk_count=1)).build(
        BuildRequest(folder=source, output=tmp_path / "capsule.TAR.ZST", chunk_size="1MB")
    ).success is True, "case-insensitive tar.zst names stay acceptable"


def test_build_chunked_publication_is_all_or_nothing(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    output = tmp_path / "haul.tar.zst"
    # A stale sibling from an interrupted previous attempt must not produce a
    # partial public set on a failing retry.
    competitor = tmp_path / "haul_1.tar.zst"
    competitor.write_bytes(b"pre-existing")
    hauler = CapsuleHauler(WORKSPACE_ONLY_INVENTORY, chunk_count=2)

    result = capsule_service(tmp_path, hauler).build(
        BuildRequest(folder=source, output=output, chunk_size="1MB")
    )

    assert result.success is False
    assert "already exists" in result.diagnostics
    assert not (tmp_path / "haul_0.tar.zst").exists(), "failed promotion must roll back created links"
    assert competitor.read_bytes() == b"pre-existing"


def test_build_chunked_rolls_back_when_a_competing_output_appears_mid_promotion(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    output = tmp_path / "haul.tar.zst"

    class RacingCapsuleHauler(CapsuleHauler):
        def save(self, store, temp, haul, chunk_size=None, containerd=False):
            super().save(store, temp, haul, chunk_size=chunk_size, containerd=containerd)
            if chunk_size:
                # Materialize after save, before promotion: a mid-promotion
                # competitor collides with the second chunk target.
                    (Path(haul).parent.parent / "haul_1.tar.zst").write_bytes(b"raced")

    result = capsule_service(tmp_path, RacingCapsuleHauler(WORKSPACE_ONLY_INVENTORY, chunk_count=2)).build(
        BuildRequest(folder=source, output=output, chunk_size="1MB")
    )
    assert result.success is False
    assert not (tmp_path / "haul_0.tar.zst").exists(), "created links must be rolled back"
    competitor = tmp_path / "haul_1.tar.zst"
    assert competitor.read_bytes() == b"raced", "data JAT did not create is never deleted"


def test_build_rejects_user_manifest_colliding_with_reserved_anchor_names(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    output = tmp_path / "haul.tar.zst"
    collision = tmp_path / "clobber.yaml"
    collision.write_text(
        "\n".join(
            [
                "apiVersion: content.hauler.cattle.io/v1",
                "kind: Files",
                "metadata:",
                "  name: sneaky",
                "spec:",
                "  files:",
                f"    - path: {json.dumps(str(collision))}",
                f"      name: {WORKSPACE_ARTIFACT_NAME}",
            ]
        )
        + "\n"
    )

    class ClobberingCapsuleHauler(CapsuleHauler):
        def __init__(self, inventory):
            super().__init__(inventory)
            self.clobbered = False

        def sync(self, store, temp, *manifests, retries=None, exclude_extras=False):
            super().sync(store, temp, *manifests, retries=retries, exclude_extras=exclude_extras)
            for manifest in manifests:
                if Path(manifest).name == "manifest.yaml":
                    continue  # the JAT-owned core manifest legitimately names the anchors
                if WORKSPACE_ARTIFACT_NAME in Path(manifest).read_text():
                    self.clobbered = True

        def inventory(self, store, temp):
            rows = super().inventory(store, temp)
            if self.clobbered:
                return [
                    {**row, "Digest": "sha256:" + "f" * 64} if row["Reference"] == WORKSPACE_REFERENCE else row
                    for row in rows
                ]
            return rows

    result = capsule_service(tmp_path, ClobberingCapsuleHauler(WORKSPACE_ONLY_INVENTORY)).build(
        BuildRequest(folder=source, output=output, hauler_manifests=[str(collision)])
    )
    assert result.success is False
    assert "collides with the reserved JAT anchor" in result.diagnostics
    assert not output.exists()


def test_build_accepts_user_content_that_leaves_anchors_untouched(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    extra = tmp_path / "extra.yaml"
    extra.write_text(
        "\n".join(
            [
                "apiVersion: content.hauler.cattle.io/v1",
                "kind: Files",
                "metadata:",
                "  name: benign",
                "spec:",
                "  files:",
                f"    - path: {json.dumps(str(extra))}",
                "      name: extra.txt",
            ]
        )
        + "\n"
    )
    result = capsule_service(tmp_path, CapsuleHauler(WORKSPACE_ONLY_INVENTORY)).build(
        BuildRequest(folder=source, output=tmp_path / "haul.tar.zst", hauler_manifests=[str(extra)])
    )
    assert result.success, result.diagnostics


def test_copy_rejects_local_directory_targets_that_would_be_overwritten(tmp_path, monkeypatch):
    monkeypatch.setenv("JAT_RUN_DIR", str(tmp_path / "run"))
    haul = tmp_path / "haul.tar.zst"
    haul.write_bytes(b"haul")
    service = capsule_service(tmp_path, CapsuleHauler(MIXED_INVENTORY))

    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "existing.txt").write_text("keep")
    result = service.copy(CopyRequest(haul=haul, to=f"dir://{occupied}"))
    assert result.success is False
    assert "destination must be empty" in result.diagnostics
    assert (occupied / "existing.txt").read_text() == "keep"

    result = service.copy(CopyRequest(haul=haul, to="dir:///"))
    assert result.success is False
    assert "destination must not be" in result.diagnostics

    copied_file = tmp_path / "plain-file"
    copied_file.write_text("data")
    result = service.copy(CopyRequest(haul=haul, to=f"dir://{copied_file}"))
    assert result.success is False
    assert copied_file.read_text() == "data"


def test_copy_redacts_credential_echo_from_failure_diagnostics(tmp_path, monkeypatch):
    monkeypatch.setenv("JAT_RUN_DIR", str(tmp_path / "run"))
    haul = tmp_path / "haul.tar.zst"
    haul.write_bytes(b"haul")
    secret_target = "registry://user:token@registry.example.test"

    class EchoingCapsuleHauler(CapsuleHauler):
        def copy(self, store, temp, target, retries=None, plain_http=False, insecure=False):
            raise RuntimeError(f"push to {target} failed after 3 attempts")

    # model_construct bypasses validation to exercise the defense-in-depth
    # redaction of driver-level credential echoes in failure diagnostics.
    result = capsule_service(tmp_path, EchoingCapsuleHauler(MIXED_INVENTORY)).copy(
        CopyRequest.model_construct(
            haul=haul, to=secret_target, retries=1, plain_http=True, insecure=False
        )
    )
    assert result.success is False
    assert "token" not in result.diagnostics
    assert "<redacted>" in result.diagnostics
