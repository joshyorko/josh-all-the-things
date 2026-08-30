import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from shutil import which

import pytest

from jat.models import (
    BuildRequest,
    CopyRequest,
    ExportRequest,
    ExtractRequest,
    InspectRequest,
    RestoreRequest,
    ServeRequest,
)
from jat.services import (
    JATService,
    WORKSPACE_ARTIFACT,
    WORKSPACE_REFERENCE,
)

pytestmark = pytest.mark.skipif(
    not which("hauler") or not (which("gtar") or which("tar")),
    reason="real Hauler and GNU tar are required",
)

DOCKER = which("docker")


def docker_available():
    if not DOCKER:
        return False
    try:
        return subprocess.run([DOCKER, "info"], timeout=30, capture_output=True).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


requires_docker = pytest.mark.skipif(not docker_available(), reason="local Docker daemon required")


def make_workspace(root: Path) -> Path:
    source = root / "synthetic-project"
    nested = source / "bin"
    nested.mkdir(parents=True)
    (source / "README.md").write_bytes(b"synthetic workspace\n")
    (nested / "tool.sh").write_bytes(b"#!/usr/bin/env bash\nprintf 'synthetic tool\\n'\n")
    (source / "README.md").chmod(0o640)
    (nested / "tool.sh").chmod(0o750)
    return source


def make_service(progress=None) -> JATService:
    return JATService(producer_version="synthetic-test", progress=progress)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def write_manifest(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return path


@contextmanager
def local_registry():
    """A hermetic loopback OCI registry; go-containerregistry uses plain HTTP
    for loopback, so no Docker Hub access or TLS is involved."""
    port = free_port()
    registry = subprocess.Popen(
        [DOCKER, "run", "--rm", "-p", f"127.0.0.1:{port}:5000", "registry:2"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 60
        while True:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/v2/", timeout=2)
                break
            except OSError:
                if time.monotonic() > deadline:
                    pytest.fail("local registry fixture did not become ready")
                time.sleep(0.5)
        yield port
    finally:
        registry.terminate()
        try:
            registry.wait(timeout=15)
        except subprocess.TimeoutExpired:
            registry.kill()


def seed_local_image(port: int, image: str = "busybox:1.36") -> str:
    """Publish a locally cached image into the loopback registry."""
    reference = f"127.0.0.1:{port}/{image}"
    subprocess.run([DOCKER, "tag", image, reference], check=True, capture_output=True)
    subprocess.run([DOCKER, "push", reference], check=True, capture_output=True)
    return reference


def test_real_files_only_capsule_build_inspect_extract_and_restore(tmp_path):
    source = make_workspace(tmp_path)
    haul = tmp_path / "capsule.tar.zst"
    service = make_service()

    built = service.build(BuildRequest(folder=source, output=haul))
    assert built.success, built.diagnostics
    assert built.format_version == 1, "unchanged non-chunked builds keep the v1 receipt"

    inspected = service.inspect(InspectRequest(haul=haul))
    assert inspected.success, inspected.diagnostics
    assert inspected.format_version == 2
    references = {entry.reference for entry in inspected.inventory}
    assert WORKSPACE_REFERENCE in references
    assert all(entry.type == "file" for entry in inspected.inventory)
    assert inspected.anchors == {
        "workspace": True,
        "brew": False,
        "rcc_environment": False,
        "rcc_metadata": False,
    }
    run_dir = tmp_path / "inspect-run"
    run_dir.mkdir()
    assert not any(run_dir.iterdir()), "inspect must leave no workspace materialization"

    extracted_dir = tmp_path / "extracted"
    extracted = service.extract(
        ExtractRequest(haul=haul, reference=WORKSPACE_REFERENCE, destination=extracted_dir)
    )
    assert extracted.success, extracted.diagnostics
    payload = extracted_dir / WORKSPACE_ARTIFACT
    assert payload.is_file()
    assert extracted.payloads[0].sha256 == extracted.payloads[0].sha256
    assert len(extracted.payloads[0].sha256) == 64

    restored = tmp_path / "restored"
    hydrated = service.restore(RestoreRequest(haul=haul, destination=restored))
    assert hydrated.success, hydrated.diagnostics
    assert (restored / "workspace" / source.name / "README.md").read_bytes() == b"synthetic workspace\n"


@requires_docker
def test_real_mixed_capsule_keeps_extras_visible_and_restore_selective(tmp_path):
    source = make_workspace(tmp_path)
    extra = write_manifest(
        tmp_path / "extras.yaml",
        "\n".join(
            [
                "apiVersion: content.hauler.cattle.io/v1",
                "kind: Files",
                "metadata:",
                "  name: synthetic-extra",
                "spec:",
                "  files:",
                f"    - path: {json.dumps(str(tmp_path / 'extra-note.txt'))}",
                "      name: extra-note.txt",
            ]
        )
        + "\n",
    )
    (tmp_path / "extra-note.txt").write_text("extra content\n")
    haul = tmp_path / "mixed.tar.zst"
    service = make_service()

    built = service.build(
        BuildRequest(
            folder=source,
            output=haul,
            images=["busybox:1.36"],
            hauler_manifests=[str(extra)],
        )
    )
    assert built.success, built.diagnostics

    inspected = service.inspect(InspectRequest(haul=haul))
    assert inspected.success, inspected.diagnostics
    entries = {entry.reference: entry for entry in inspected.inventory}
    assert WORKSPACE_REFERENCE in entries
    assert "hauler/busybox:1.36" in entries or any("busybox" in reference for reference in entries), sorted(entries)
    assert "hauler/extra-note.txt:latest" in entries
    image_entries = [entry for entry in entries.values() if entry.type == "image"]
    assert image_entries and image_entries[0].platform == "linux/amd64"

    restored = tmp_path / "restored"
    hydrated = service.restore(RestoreRequest(haul=haul, destination=restored))
    assert hydrated.success, hydrated.diagnostics
    assert (restored / "workspace" / source.name / "README.md").exists()
    assert not (restored / "workspace" / "extra-note.txt").exists(), "restore stays anchor-selective"


@requires_docker
def test_real_images_file_and_http_manifest_are_hauler_native(tmp_path):
    source = make_workspace(tmp_path)
    with local_registry() as registry_port:
        image_reference = seed_local_image(registry_port)
        images_file = tmp_path / "images.txt"
        images_file.write_text(f"# synthetic list\n{image_reference}\n")
        remote_manifest = write_manifest(
            tmp_path / "remote-payload.yaml",
            "\n".join(
                [
                    "apiVersion: content.hauler.cattle.io/v1",
                    "kind: Files",
                    "metadata:",
                    "  name: remote-extra",
                    "spec:",
                    "  files:",
                    f"    - path: {json.dumps(str(tmp_path / 'http-note.txt'))}",
                    "      name: http-note.txt",
                ]
            )
            + "\n",
        )
        (tmp_path / "http-note.txt").write_text("served over http\n")
        port = free_port()
        server = subprocess.Popen(
            [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
            cwd=str(tmp_path),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        haul = tmp_path / "http-capsule.tar.zst"
        try:
            deadline = time.monotonic() + 10
            while True:
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/images.txt", timeout=2)
                    break
                except OSError:
                    if time.monotonic() > deadline:
                        raise
                    time.sleep(0.2)
            service = make_service()
            built = service.build(
                BuildRequest(
                    folder=source,
                    output=haul,
                    images_files=[f"http://127.0.0.1:{port}/images.txt"],
                    hauler_manifests=[f"http://127.0.0.1:{port}/remote-payload.yaml"],
                )
            )
        finally:
            server.terminate()
            server.wait(timeout=10)
        assert built.success, built.diagnostics

        inspected = service.inspect(InspectRequest(haul=haul))
        references = {entry.reference for entry in inspected.inventory}
        assert any("busybox" in reference for reference in references), sorted(references)
        assert "hauler/http-note.txt:latest" in references


@requires_docker
def test_real_helm_manifest_valuesfiles_and_image_closure(tmp_path, monkeypatch):
    source = make_workspace(tmp_path)
    with local_registry() as registry_port:
        image_reference = seed_local_image(registry_port)
        chart = tmp_path / "chart"
        (chart / "templates").mkdir(parents=True)
        (chart / "charts" / "sub").mkdir(parents=True)
        (chart / "Chart.yaml").write_text(
            "apiVersion: v2\n"
            "name: jat-synthetic\n"
            "version: 0.1.0\n"
            "annotations:\n"
            "  images: |\n"
            f"    - image: {image_reference}\n"
            "      name: synthetic\n"
            "dependencies:\n"
            "  - name: sub\n"
            "    version: 0.1.0\n"
            "    repository: file://charts/sub\n"
        )
        (chart / "charts" / "sub" / "Chart.yaml").write_text("apiVersion: v2\nname: sub\nversion: 0.1.0\n")
        (chart / "values.yaml").write_text("message: default\n")
        (chart / "values-prod.yaml").write_text("message: prod\n")
        (chart / "templates" / "configmap.yaml").write_text(
            "apiVersion: v1\n"
            "kind: ConfigMap\n"
            "metadata:\n"
            "  name: jat-synthetic\n"
            "data:\n"
            "  message: {{ .Values.message | quote }}\n"
        )
        manifest = write_manifest(
            tmp_path / "manifests" / "airgap.yaml",
            "\n".join(
                [
                    "apiVersion: content.hauler.cattle.io/v1",
                    "kind: Charts",
                    "metadata:",
                    "  name: jat-synthetic-chart",
                    "spec:",
                    "  charts:",
                    "    - name: chart",
                    "      repoURL: .",
                    "      version: 0.1.0",
                    "      add-images: true",
                    "      add-dependencies: true",
                    "      valuesFiles:",
                    "        - ../chart/values-prod.yaml",
                ]
            )
            + "\n",
        )

        haul = tmp_path / "helm-capsule.tar.zst"
        service = make_service()
        # Hauler resolves local chart repoURL relative to its own working
        # directory, while valuesFiles resolve relative to the manifest file:
        # chdir to the fixture root and keep the manifest in a subdirectory so
        # both semantics are exercised exactly as pinned v2.0.3 implements them.
        monkeypatch.chdir(tmp_path)
        built = service.build(
            BuildRequest(folder=source, output=haul, hauler_manifests=[str(manifest)])
        )
        assert built.success, built.diagnostics

        inspected = service.inspect(InspectRequest(haul=haul))
        references = {entry.reference for entry in inspected.inventory}
        assert any("jat-synthetic" in reference for reference in references), sorted(references)
        assert any("busybox" in reference for reference in references), (
            "pinned v2.0.3 must discover chart-declared images during acquisition",
            sorted(references),
        )

    restored = tmp_path / "restored"
    hydrated = service.restore(RestoreRequest(haul=haul, destination=restored))
    assert hydrated.success, hydrated.diagnostics


def test_real_chunked_build_round_trip_through_documented_entrypoint(tmp_path):
    source = make_workspace(tmp_path)
    # Incompressible payload so 1MB chunks split into multiple outputs.
    (source / "blob.bin").write_bytes(os.urandom(3 * 1024 * 1024))
    haul = tmp_path / "chunked.tar.zst"
    service = make_service()
    built = service.build(BuildRequest(folder=source, output=haul, chunk_size="1MB"))
    assert built.success, built.diagnostics
    assert built.format_version == 2
    assert built.payload_path is None
    assert len(built.payloads) >= 2, [payload.path.name for payload in built.payloads]
    for payload in built.payloads:
        assert payload.path.is_file()
        assert payload.size > 0 and len(payload.sha256) == 64

    entrypoint = tmp_path / "chunked_0.tar.zst"
    assert entrypoint.is_file(), "v2.0.3 names chunks <base>_<index><ext> from zero"
    inspected = service.inspect(InspectRequest(haul=entrypoint))
    assert inspected.success, inspected.diagnostics
    assert WORKSPACE_REFERENCE in {entry.reference for entry in inspected.inventory}

    restored = tmp_path / "restored"
    hydrated = service.restore(RestoreRequest(haul=entrypoint, destination=restored))
    assert hydrated.success, hydrated.diagnostics
    assert (restored / "workspace" / source.name / "bin" / "tool.sh").exists()


def test_real_containerd_export_produces_hauler_containerd_layout(tmp_path):
    source = make_workspace(tmp_path)
    haul = tmp_path / "capsule.tar.zst"
    service = make_service()
    assert service.build(BuildRequest(folder=source, output=haul)).success

    output = tmp_path / "images.tar"
    exported = service.export(ExportRequest(haul=haul, format="containerd", output=output))
    assert exported.success, exported.diagnostics
    assert exported.format_version == 2
    assert exported.payloads[0].path == output
    assert exported.payloads[0].size == output.stat().st_size
    assert exported.payloads[0].sha256 == exported.sha256

    # Pinned Hauler always writes a zstd-compressed tar for containerd
    # exports, and the declared RCC Python (3.13.11) cannot decode zstd with
    # tarfile, so read the member list through GNU tar like the archive
    # boundary does.
    listing = subprocess.run(
        ["tar", "--zstd", "-tf", str(output)], capture_output=True, text=True, check=True
    )
    names = listing.stdout.splitlines()
    assert any(name.startswith("blobs/sha256/") for name in names)
    assert not any(name == "oci-layout" for name in names), "containerd compatibility removes oci-layout"


def test_real_copy_to_directory_projection(tmp_path):
    source = make_workspace(tmp_path)
    haul = tmp_path / "capsule.tar.zst"
    service = make_service()
    assert service.build(BuildRequest(folder=source, output=haul)).success

    target = tmp_path / "exported"
    copied = service.copy(CopyRequest(haul=haul, to=f"dir://{target}"))
    assert copied.success, copied.diagnostics
    assert copied.transfer.transport == "local-directory"
    assert (target / WORKSPACE_ARTIFACT).is_file()


@requires_docker
def test_real_copy_to_local_registry_uses_hauler_transfer(tmp_path):
    source = make_workspace(tmp_path)
    haul = tmp_path / "capsule.tar.zst"
    service = make_service()
    assert service.build(BuildRequest(folder=source, output=haul)).success

    with local_registry() as port:
        copied = service.copy(
            CopyRequest(haul=haul, to=f"registry://127.0.0.1:{port}", plain_http=True, retries=1)
        )
        assert copied.success, copied.diagnostics
        assert copied.transfer.transport == "remote-registry"
        catalog = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/v2/_catalog", timeout=10).read())
        assert any("workspace" in name for name in catalog.get("repositories", [])), catalog


def hauler_serve_pids() -> set[int]:
    listing = subprocess.run(["pgrep", "-f", "hauler .*store serve"], capture_output=True, text=True)
    return {int(pid) for pid in listing.stdout.split()}


def test_real_serve_both_endpoints_cancel_and_leave_no_orphans(tmp_path):
    source = make_workspace(tmp_path)
    haul = tmp_path / "capsule.tar.zst"
    service = make_service()
    assert service.build(BuildRequest(folder=source, output=haul)).success

    before = hauler_serve_pids()
    fileserver_port = free_port()
    registry_port = free_port()
    run_dir = tmp_path / "serve-run"
    run_dir.mkdir()
    environment = dict(os.environ)
    environment["JAT_RUN_DIR"] = str(run_dir)
    environment["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
    process = subprocess.Popen(
        [sys.executable, "-m", "jat.cli", "serve", "--haul", str(haul), "--mode", "both",
         "--fileserver-port", str(fileserver_port), "--registry-port", str(registry_port), "--json"],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 60

        def wait_for(url):
            while time.monotonic() < deadline:
                try:
                    return urllib.request.urlopen(url, timeout=2)
                except OSError:
                    time.sleep(0.3)
            return None

        fileserver = wait_for(f"http://127.0.0.1:{fileserver_port}/{WORKSPACE_ARTIFACT}")
        assert fileserver is not None, process.communicate() if process.poll() is not None else "fileserver never became ready"
        assert fileserver.status == 200
        body = fileserver.read()
        assert len(body) > 0, "the fileserver must expose the file artifacts of the mixed capsule"
        registry = wait_for(f"http://127.0.0.1:{registry_port}/v2/_catalog")
        assert registry is not None, "registry never became ready"
        assert registry.status == 200
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
    assert process.returncode is not None

    orphans = hauler_serve_pids() - before
    assert not orphans, f"orphaned Hauler serve processes remain: {sorted(orphans)}"


@requires_docker
def test_real_streamed_progress_flows_during_remote_acquisition(tmp_path):
    source = make_workspace(tmp_path)
    with local_registry() as registry_port:
        image_reference = seed_local_image(registry_port)
        images_file = tmp_path / "images.txt"
        images_file.write_text(f"{image_reference}\n")
        haul = tmp_path / "streamed.tar.zst"
        lines = []
        timestamps = []

        def sink(line: str) -> None:
            lines.append(line)
            timestamps.append(time.monotonic())

        service = make_service(progress=sink)
        started = time.monotonic()
        built = service.build(BuildRequest(folder=source, output=haul, images_files=[str(images_file)]))
        finished = time.monotonic()
        assert built.success, built.diagnostics
        assert lines, "Hauler transfer lines must reach the sink"
        assert all(started <= timestamp <= finished for timestamp in timestamps)
        assert any("image" in line.lower() for line in lines), lines[:10]


@requires_docker
def test_real_retry_policy_requests_one_attempt_on_a_retry_capable_path(tmp_path):
    source = make_workspace(tmp_path)
    # Nothing listens on this loopback port: image acquisition fails, and the
    # hauler log exposes how many attempts were actually made.
    unreachable_port = free_port()
    images_file = tmp_path / "images.txt"
    images_file.write_text(f"127.0.0.1:{unreachable_port}/busybox:1.36\n")
    lines = []
    service = make_service(progress=lines.append)
    built = service.build(
        BuildRequest(
            folder=source,
            output=tmp_path / "haul.tar.zst",
            images_files=[str(images_file)],
            retries=1,
        )
    )
    assert built.success is False
    assert any("attempt 1/1" in line for line in lines), lines[:20]
    assert not any("/3" in line for line in lines), "JAT retries=1 must not widen to hauler's default 3"


def test_real_chunked_build_rejects_non_reloadable_output_names_before_capture(tmp_path):
    source = make_workspace(tmp_path)
    (source / "blob.bin").write_bytes(os.urandom(3 * 1024 * 1024))
    service = make_service()

    # The pinned binary splits any container but cannot reload zip chunk sets
    # (io.ReaderAt/io.Seeker constraint), so JAT rejects the name up front.
    built = service.build(BuildRequest(folder=source, output=tmp_path / "capsule.zip", chunk_size="1MB"))
    assert built.success is False
    assert "must be a .tar or .tar.zst archive name" in built.diagnostics
    assert not list(tmp_path.glob("capsule*")), "no capture work may happen for a rejected output name"


def test_real_chunked_leading_dot_output_fails_closed_before_capture(tmp_path):
    source = make_workspace(tmp_path)
    (source / "blob.bin").write_bytes(os.urandom(3 * 1024 * 1024))
    service = make_service()

    # Verified against the pinned binary: hidden-base chunk sets split fine
    # but reassembly fails on load (truncated blob), so the name is rejected
    # before any capture work and no chunk is ever published.
    built = service.build(BuildRequest(folder=source, output=tmp_path / ".capsule.tar.zst", chunk_size="1MB"))
    assert built.success is False
    assert "must not start with a dot" in built.diagnostics
    assert not list(tmp_path.glob("_0*")), "no chunk may be published for a rejected output name"


def test_real_manifest_injection_of_omitted_optional_anchor_fails_closed(tmp_path):
    source = make_workspace(tmp_path)
    injection = write_manifest(
        tmp_path / "brew-injection.yaml",
        "\n".join(
            [
                "apiVersion: content.hauler.cattle.io/v1",
                "kind: Files",
                "metadata:",
                "  name: brew-injection",
                "spec:",
                "  files:",
                f"    - path: {json.dumps(str(tmp_path / 'fake-brew'))}",
                "      name: homebrew-recovery.tar.zst",
            ]
        )
        + "\n",
    )
    (tmp_path / "fake-brew").write_text("not a real brew export\n")
    haul = tmp_path / "injected.tar.zst"

    built = make_service().build(
        BuildRequest(folder=source, output=haul, hauler_manifests=[str(injection)])
    )
    assert built.success is False
    assert "Homebrew recovery artifact but none was requested" in built.diagnostics or (
        "collides with the reserved JAT anchor" in built.diagnostics
    ), built.diagnostics
    assert not haul.exists(), "injected content must never be published"
