from jat.models import BuildRequest, RestoreRequest
from jat.safety import ArchiveMember
from jat.services import JATService


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


class FakeHauler:
    def __init__(self, extracted_workspace=None, extracted_brew=None, on_info=None):
        self.calls = []
        self.extracted_workspace = extracted_workspace
        self.extracted_brew = extracted_brew
        self.on_info = on_info

    def sync(self, store, temp, manifest):
        self.calls.append("sync")

    def save(self, store, temp, haul):
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


def service(tmp_path, archive=None, hauler=None):
    return JATService(
        archive=archive or FakeArchive(),
        hauler=hauler or FakeHauler(),
        producer_version="synthetic-version",
        which=lambda command: f"/tools/{command}",
    )


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
    archive = FakeArchive(members=[ArchiveMember("project/link", "symlink")])
    result = service(tmp_path, archive=archive, hauler=FakeHauler(extracted_workspace=True)).restore(
        RestoreRequest(haul=haul, destination=destination)
    )
    assert result.success is False
    assert not destination.exists()
    assert "unsupported member type" in result.diagnostics
