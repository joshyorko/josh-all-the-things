"""Exercise fixture ownership without mutating a real Docker daemon."""

import hashlib
import inspect
import subprocess
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
import test_real_vertical as vertical


class DockerRecorder:
    def __init__(self):
        self.images = {"jat-api:dev": "developer-api", "jat-worker:dev": "developer-worker", "source:test": "seed"}
        self.calls = []
        self.removed_containers = []

    def run(self, argv, **kwargs):
        args = list(argv)[1:]
        self.calls.append(args)
        stdout = ""
        status = 0
        if args[:2] == ["image", "inspect"]:
            if "--format" in args:
                stdout = "sha256:registry-fixture\n"
            else:
                status = int(args[-1] not in self.images)
        elif args[0] == "pull":
            self.images[args[-1]] = "seed"
        elif args[0] == "tag":
            self.images[args[2]] = self.images[args[1]]
        elif args[:2] == ["image", "rm"]:
            self.images.pop(args[-1], None)
        elif args[0] == "import":
            self.images[args[-1]] = "synthetic"
        elif args[0] == "run":
            Path(args[args.index("--cidfile") + 1]).write_text("owned-container-id\n")
            stdout = "owned-container-id\n"
        elif args[:2] == ["rm", "--force"]:
            self.removed_containers.append(args[-1])
        return subprocess.CompletedProcess(argv, status, stdout=stdout, stderr="")


def test_manifest_fixture_preserves_preexisting_developer_tags(tmp_path, monkeypatch):
    docker = DockerRecorder()
    original = dict(docker.images)
    monkeypatch.setattr(vertical.subprocess, "run", docker.run)
    monkeypatch.setattr(vertical.subprocess, "check_output", lambda *a, **k: b"password")
    monkeypatch.setattr(vertical, "DOCKER", "docker")

    @contextmanager
    def registry(_monkeypatch):
        yield 1234

    class Service:
        def manifest(self, request):
            request.output.write_text("fixture manifest\n")
            return SimpleNamespace(
                success=True,
                diagnostics="",
                complete=True,
                manifest=SimpleNamespace(
                    mappings=[SimpleNamespace(source_local=image, digest="sha256:fixture") for image in request.images],
                    registry_prefix=request.registry_prefix,
                ),
            )

    monkeypatch.setattr(vertical, "authenticated_local_registry", registry)
    monkeypatch.setattr(vertical, "make_service", Service)
    scenario = vertical.test_real_manifest_local_publish_fresh_pull_and_multi_image_bootstrap
    kwargs = {}
    if "local_images" in inspect.signature(scenario).parameters:
        kwargs["local_images"] = ("owned-api:test", "owned-worker:test")
        docker.images.update({image: "synthetic" for image in kwargs["local_images"]})
    scenario(tmp_path, monkeypatch, **kwargs)
    assert docker.images["jat-api:dev"] == original["jat-api:dev"]
    assert docker.images["jat-worker:dev"] == original["jat-worker:dev"]
    assert not any(call[0] == "pull" for call in docker.calls)


def test_seed_publishes_existing_image_and_cleans_only_its_alias(monkeypatch):
    docker = DockerRecorder()
    original = dict(docker.images)
    monkeypatch.setattr(vertical.subprocess, "run", docker.run)
    monkeypatch.setattr(vertical, "DOCKER", "docker")
    reference = vertical.seed_local_image(1234, "source:test")
    assert reference.startswith("127.0.0.1:1234/")
    assert docker.images == original
    assert not any(call[0] == "pull" for call in docker.calls)


@pytest.mark.parametrize("authenticated", [False, True])
def test_registry_cleanup_removes_owned_container_even_after_body_failure(monkeypatch, authenticated):
    docker = DockerRecorder()
    processes = []

    class StuckClient:
        def __init__(self, *args, **kwargs):
            self.waits = 0
            processes.append(self)

        def terminate(self):
            pass

        def wait(self, timeout=None):
            self.waits += 1
            if self.waits == 1:
                raise subprocess.TimeoutExpired("docker", timeout)
            return 0

        def kill(self):
            pass

    monkeypatch.setattr(vertical, "DOCKER", "docker")
    monkeypatch.setattr(vertical.subprocess, "run", docker.run)
    monkeypatch.setattr(vertical.subprocess, "Popen", StuckClient)
    monkeypatch.setattr(vertical.subprocess, "check_output", lambda *a, **k: b"password")
    monkeypatch.setattr(vertical.urllib.request, "urlopen", lambda *a, **k: SimpleNamespace(close=lambda: None))
    registry = vertical.authenticated_local_registry(monkeypatch) if authenticated else vertical.local_registry()
    with pytest.raises(RuntimeError, match="body failure"), registry:
        raise RuntimeError("body failure")
    assert docker.removed_containers == ["owned-container-id"]
    assert not processes, "detached startup must not leave an attached Docker client"
    assert "--detach" in next(call for call in docker.calls if call[0] == "run")


def test_local_images_are_unique_and_cleanup_survives_body_failure(tmp_path, monkeypatch):
    docker = DockerRecorder()
    original = dict(docker.images)
    monkeypatch.setattr(vertical.subprocess, "run", docker.run)
    monkeypatch.setattr(vertical, "DOCKER", "docker")
    fixture = vertical.local_images.__wrapped__(tmp_path)
    images = next(fixture)
    assert len(set(images)) == 2
    assert all(image.startswith("jat-fixture-") for image in images)
    assert all(image in docker.images for image in images)
    with pytest.raises(RuntimeError, match="body failure"):
        fixture.throw(RuntimeError("body failure"))
    assert docker.images == original
    assert not any(call[0] == "pull" for call in docker.calls)


@pytest.mark.parametrize("defect", ["checksum", "staging"])
def test_file_scenario_rejects_bad_checksum_and_staging_leaks(tmp_path, monkeypatch, defect):
    class Service:
        def build(self, request):
            return SimpleNamespace(success=True, diagnostics="", format_version=1)

        def inspect(self, request):
            if defect == "staging":
                parent = Path(vertical.os.environ.get("JAT_RUN_DIR", str(tmp_path)))
                (parent / ".jat-inspect-leak").mkdir()
            return SimpleNamespace(
                success=True, diagnostics="", format_version=2,
                inventory=[SimpleNamespace(reference=vertical.WORKSPACE_REFERENCE, type="file")],
                anchors={"workspace": True, "brew": False, "rcc_environment": False, "rcc_metadata": False},
            )

        def extract(self, request):
            request.destination.mkdir()
            (request.destination / vertical.WORKSPACE_ARTIFACT).write_bytes(b"payload")
            checksum = "0" * 64 if defect == "checksum" else hashlib.sha256(b"payload").hexdigest()
            return SimpleNamespace(success=True, diagnostics="", payloads=[SimpleNamespace(sha256=checksum)])

        def restore(self, request):
            target = request.destination / "workspace" / "synthetic-project"
            target.mkdir(parents=True)
            (target / "README.md").write_bytes(b"synthetic workspace\n")
            return SimpleNamespace(success=True, diagnostics="")

    monkeypatch.delenv("JAT_RUN_DIR", raising=False)
    monkeypatch.setattr(vertical, "make_service", Service)
    scenario = vertical.test_real_files_only_capsule_build_inspect_extract_and_restore
    kwargs = {"monkeypatch": monkeypatch} if "monkeypatch" in inspect.signature(scenario).parameters else {}
    with pytest.raises(AssertionError):
        scenario(tmp_path, **kwargs)


def test_containerd_listing_uses_selected_gnu_tar(tmp_path, monkeypatch):
    class Service:
        def build(self, request):
            return SimpleNamespace(success=True)

        def export(self, request):
            request.output.write_bytes(b"archive")
            payload = SimpleNamespace(path=request.output, size=7, sha256="digest")
            return SimpleNamespace(success=True, diagnostics="", format_version=2, payloads=[payload], sha256="digest")

    def run(argv, **kwargs):
        assert argv[0] == "/fixture/gtar"
        return SimpleNamespace(stdout="blobs/sha256/test\noci-layout\nhauler-index.json\n")

    monkeypatch.setattr(vertical, "GNU_TAR", "/fixture/gtar", raising=False)
    monkeypatch.setattr(vertical, "make_service", Service)
    monkeypatch.setattr(vertical.subprocess, "run", run)
    vertical.test_real_containerd_export_produces_hauler_containerd_layout(tmp_path)


def test_registry_startup_timeout_still_removes_owned_container(monkeypatch):
    docker = DockerRecorder()

    def run(argv, **kwargs):
        completed = docker.run(argv, **kwargs)
        if argv[1] == "run":
            raise subprocess.TimeoutExpired(argv, 30)
        return completed

    monkeypatch.setattr(vertical, "DOCKER", "docker")
    monkeypatch.setattr(vertical.subprocess, "run", run)
    with pytest.raises(subprocess.TimeoutExpired), vertical.local_registry():
        pytest.fail("startup must not yield after timing out")
    assert docker.removed_containers == ["owned-container-id"]


def test_mixed_scenario_requires_only_its_provisioned_local_image(tmp_path, monkeypatch):
    def build(request):
        assert request.images == ["owned-fixture:test"]
        raise RuntimeError("owned image reached build")

    monkeypatch.setattr(vertical, "make_service", lambda: SimpleNamespace(build=build))
    scenario = vertical.test_real_mixed_capsule_keeps_extras_visible_and_restore_selective
    kwargs = {"local_images": ("owned-fixture:test",)} if "local_images" in inspect.signature(scenario).parameters else {}
    with pytest.raises(RuntimeError, match="owned image reached build"):
        scenario(tmp_path, **kwargs)
