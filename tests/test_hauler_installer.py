import hashlib
import io
import json
import os
import shlex
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from scripts.install_hauler import install, resolve_platform

ROOT = Path(__file__).parents[1]


def _archive(path: Path, executable: str = "hauler", version: str = "v2.1.1") -> bytes:
    payload = f"test-hauler\nGitVersion:    {version}\n".encode()
    with tarfile.open(path, "w:gz") as archive:
        info = tarfile.TarInfo(executable)
        info.mode = 0o755
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
        license_info = tarfile.TarInfo("LICENSE")
        license_payload = b"license\n"
        license_info.mode = 0o644
        license_info.size = len(license_payload)
        archive.addfile(license_info, io.BytesIO(license_payload))
    return path.read_bytes()


def _manifest(tmp_path: Path, *, platform: str, asset: str, payload: bytes, version: str = "v2.1.1") -> Path:
    manifest = tmp_path / "hauler.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "hauler": {
                    "version": version,
                    "platforms": {
                        platform: {
                            "asset": asset,
                            "url": f"https://github.com/hauler-dev/hauler/releases/download/{version}/{asset}",
                            "sha256": hashlib.sha256(payload).hexdigest(),
                            "executable": "hauler.exe" if platform == "windows-amd64" else "hauler",
                        }
                    },
                },
            }
        )
    )
    return manifest


def _download(payload: bytes):
    def download(_url: str, destination: Path) -> None:
        destination.write_bytes(payload)

    return download



@pytest.fixture
def simulated_hauler_process(monkeypatch):
    """Keep synthetic archive tests independent of the host executable format."""
    def run(arguments, **_kwargs):
        assert arguments[1:] == ["version"]
        payload = Path(arguments[0]).read_bytes()
        assert payload.startswith(b"test-hauler\n")
        return subprocess.CompletedProcess(arguments, 0, payload.decode().split("\n", 1)[1], "")

    monkeypatch.setattr("scripts.install_hauler.subprocess.run", run)


def test_resolve_platform_maps_supported_linux_and_windows_x64():
    assert resolve_platform("Linux", "x86_64") == "linux-amd64"
    assert resolve_platform("Windows", "AMD64") == "windows-amd64"
    with pytest.raises(ValueError, match="unsupported"):
        resolve_platform("Darwin", "x86_64")


def test_canonical_manifest_contains_official_linux_and_windows_pins():
    manifest = json.loads((ROOT / "runtime" / "hauler.json").read_text())
    assert manifest == {
        "schema_version": 1,
        "hauler": {
            "version": "v2.1.1",
            "platforms": {
                "linux-amd64": {
                    "asset": "hauler_2.1.1_linux_amd64.tar.gz",
                    "url": "https://github.com/hauler-dev/hauler/releases/download/v2.1.1/hauler_2.1.1_linux_amd64.tar.gz",
                    "sha256": "faa4d9602fdf92c2cb96eaaa3ba0889a54d2c415ba8806cda394cde5f9355b18",
                    "executable": "hauler",
                },
                "windows-amd64": {
                    "asset": "hauler_2.1.1_windows_amd64.tar.gz",
                    "url": "https://github.com/hauler-dev/hauler/releases/download/v2.1.1/hauler_2.1.1_windows_amd64.tar.gz",
                    "sha256": "f6f4f524854f8efa9999a33924b93848eaacb12877181677442ebc0fe6d9d67a",
                    "executable": "hauler.exe",
                },
            },
        },
    }


def test_simulated_windows_installs_exe_into_conda_scripts_without_admin(tmp_path, simulated_hauler_process):
    archive = tmp_path / "archive.tar.gz"
    payload = _archive(archive, executable="hauler.exe")
    conda = tmp_path / "conda"
    conda.mkdir()
    manifest = _manifest(
        tmp_path,
        platform="windows-amd64",
        asset="hauler_2.1.1_windows_amd64.tar.gz",
        payload=payload,
    )

    target = install(
        manifest,
        conda,
        system="Windows",
        machine="AMD64",
        download=_download(payload),
    )

    assert target == conda / "Scripts" / "hauler.exe"
    assert target.read_bytes() == b"test-hauler\nGitVersion:    v2.1.1\n"


def test_simulated_linux_installs_into_conda_bin_and_reuses_matching_target(tmp_path, simulated_hauler_process):
    archive = tmp_path / "archive.tar.gz"
    payload = _archive(archive)
    conda = tmp_path / "conda"
    conda.mkdir()
    manifest = _manifest(
        tmp_path,
        platform="linux-amd64",
        asset="hauler_2.1.1_linux_amd64.tar.gz",
        payload=payload,
    )
    calls = []

    def download(url: str, destination: Path) -> None:
        calls.append(url)
        destination.write_bytes(payload)

    target = install(manifest, conda, system="Linux", machine="x86_64", download=download)
    assert target == conda / "bin" / "hauler"
    assert len(calls) == 1
    assert install(manifest, conda, system="Linux", machine="x86_64", download=download) == target
    assert len(calls) == 1


def test_checksum_mismatch_fails_before_promotion(tmp_path):
    archive = tmp_path / "archive.tar.gz"
    payload = _archive(archive)
    manifest = _manifest(
        tmp_path,
        platform="linux-amd64",
        asset="hauler_2.1.1_linux_amd64.tar.gz",
        payload=payload + b"tampered",
    )
    conda = tmp_path / "conda"
    conda.mkdir()

    with pytest.raises(ValueError, match="SHA256"):
        install(manifest, conda, system="Linux", machine="x86_64", download=_download(payload))
    assert not (conda / "bin" / "hauler").exists()


def test_unsafe_archive_fails_before_promotion(tmp_path):
    archive = tmp_path / "archive.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        escaped = tarfile.TarInfo("../escaped")
        payload = b"escaped"
        escaped.mode = 0o600
        escaped.size = len(payload)
        tar.addfile(escaped, io.BytesIO(payload))
    payload = archive.read_bytes()
    manifest = _manifest(
        tmp_path,
        platform="linux-amd64",
        asset="hauler_2.1.1_linux_amd64.tar.gz",
        payload=payload,
    )
    conda = tmp_path / "conda"
    conda.mkdir()

    with pytest.raises(ValueError, match="unsafe"):
        install(manifest, conda, system="Linux", machine="x86_64", download=_download(payload))
    assert not (conda / "bin" / "hauler").exists()
    assert not (conda / "escaped").exists()


def test_mismatched_existing_installation_fails_closed(tmp_path, simulated_hauler_process):
    archive = tmp_path / "archive.tar.gz"
    payload = _archive(archive, version="v1.0.0")
    manifest = _manifest(
        tmp_path,
        platform="linux-amd64",
        asset="hauler_2.1.1_linux_amd64.tar.gz",
        payload=payload,
        version="v2.1.1",
    )
    conda = tmp_path / "conda"
    target = conda / "bin" / "hauler"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"test-hauler\nGitVersion:    v1.0.0\n")
    target.chmod(0o755)

    with pytest.raises(ValueError, match="does not match"):
        install(manifest, conda, system="Linux", machine="x86_64", download=_download(payload))
    assert target.read_bytes().endswith(b"v1.0.0\n")


def test_unprefixed_hauler_git_version_matches_v_prefixed_release_pin(tmp_path, simulated_hauler_process):
    archive = tmp_path / "archive.tar.gz"
    payload = _archive(archive, version="2.1.1")
    manifest = _manifest(
        tmp_path,
        platform="linux-amd64",
        asset="hauler_2.1.1_linux_amd64.tar.gz",
        payload=payload,
        version="v2.1.1",
    )
    conda = tmp_path / "conda"
    conda.mkdir()

    target = install(manifest, conda, system="Linux", machine="x86_64", download=_download(payload))

    assert target == conda / "bin" / "hauler"



def test_wrong_downloaded_version_is_not_promoted_and_corrected_retry_succeeds(tmp_path, simulated_hauler_process):
    archive = tmp_path / "archive.tar.gz"
    payload = _archive(archive, version="v1.0.0")
    manifest = _manifest(tmp_path, platform="linux-amd64", asset="hauler.tar.gz", payload=payload)
    conda = tmp_path / "conda"
    conda.mkdir()

    with pytest.raises(ValueError, match="pinned version check"):
        install(manifest, conda, system="Linux", machine="x86_64", download=_download(payload))
    assert not (conda / "bin" / "hauler").exists()
    assert not list(conda.glob(".hauler-install-*"))

    payload = _archive(archive)
    _manifest(tmp_path, platform="linux-amd64", asset="hauler.tar.gz", payload=payload)
    target = install(manifest, conda, system="Linux", machine="x86_64", download=_download(payload))
    assert target.read_bytes() == b"test-hauler\nGitVersion:    v2.1.1\n"


@pytest.mark.parametrize("winerror", [32, 33])
def test_windows_promotion_retries_sharing_violation(tmp_path, simulated_hauler_process, monkeypatch, winerror):
    payload = _archive(tmp_path / "archive.tar.gz", executable="hauler.exe")
    manifest = _manifest(tmp_path, platform="windows-amd64", asset="hauler.tar.gz", payload=payload)
    original_replace = os.replace
    attempts = []
    delays = []

    def replace(source, destination):
        attempts.append((source, destination))
        if len(attempts) == 1:
            error = PermissionError("synthetic Windows sharing violation")
            error.winerror = winerror
            raise error
        original_replace(source, destination)

    monkeypatch.setattr("scripts.install_hauler.os.replace", replace)
    monkeypatch.setattr("time.sleep", delays.append)
    target = install(manifest, tmp_path / "conda", system="Windows", machine="AMD64", download=_download(payload))
    assert target.read_bytes() == b"test-hauler\nGitVersion:    v2.1.1\n"
    assert len(attempts) == 2 and delays
    assert not list((tmp_path / "conda").glob(".hauler-install-*"))


@pytest.mark.parametrize("windows,winerror", [(True, 5), (False, 32)])
def test_promotion_does_not_retry_other_permission_errors(tmp_path, monkeypatch, windows, winerror):
    from unittest.mock import Mock

    from scripts.install_hauler import _promote_executable

    error = PermissionError("synthetic permission denial")
    error.winerror = winerror
    replace = Mock(side_effect=error)
    sleep = Mock()
    monkeypatch.setattr("scripts.install_hauler.os.replace", replace)
    monkeypatch.setattr("scripts.install_hauler.time.sleep", sleep)
    with pytest.raises(PermissionError):
        _promote_executable(tmp_path / "source", tmp_path / "target", windows=windows)
    replace.assert_called_once()
    sleep.assert_not_called()


@pytest.mark.parametrize("descriptor", ["conda.yaml", "environment_linux_amd64_freeze.yaml", "environment_windows_amd64.yaml"])
def test_rcc_descriptor_pin_matches_canonical_manifest(descriptor, tmp_path, simulated_hauler_process):
    command = (ROOT / descriptor).read_text().split("rccPostInstall:\n", 1)[1].strip().removeprefix("- ")
    arguments = shlex.split(command)
    expected_version = arguments[arguments.index("--expected-version") + 1]
    canonical = json.loads((ROOT / "runtime" / "hauler.json").read_text())["hauler"]["version"]
    payload = _archive(tmp_path / "archive.tar.gz", version=canonical)
    manifest = _manifest(tmp_path, platform="linux-amd64", asset="hauler.tar.gz", payload=payload, version=canonical)
    conda = tmp_path / "conda"
    conda.mkdir()

    target = install(manifest, conda, expected_version=expected_version, system="Linux", machine="x86_64", download=_download(payload))
    assert target.is_file()


def test_stale_descriptor_pin_fails_before_prefix_mutation(tmp_path):
    payload = _archive(tmp_path / "archive.tar.gz")
    manifest = _manifest(tmp_path, platform="linux-amd64", asset="hauler.tar.gz", payload=payload)
    conda = tmp_path / "missing-conda"

    with pytest.raises(ValueError, match="environment expects v2.0.3.*manifest pins v2.1.1"):
        install(manifest, conda, expected_version="v2.0.3", system="Linux", machine="x86_64")
    assert not conda.exists()


def test_cli_rejects_stale_descriptor_pin(tmp_path):
    environment = os.environ | {"CONDA_PREFIX": str(tmp_path / "missing-conda"), "HAULER_MANIFEST": str(ROOT / "runtime" / "hauler.json")}
    result = subprocess.run([sys.executable, str(ROOT / "scripts" / "install_hauler.py"), "--expected-version", "v0.0.0"], env=environment, capture_output=True, text=True, check=False)
    assert result.returncode != 0
    assert "environment expects v0.0.0" in result.stderr
    assert not (tmp_path / "missing-conda").exists()


@pytest.mark.skipif(os.name != "nt", reason="requires native Windows RCC environment and its verified hauler.exe")
def test_native_windows_installs_and_executes_environment_hauler(tmp_path):
    """Exercise a real PE executable without the synthetic process fixture."""
    source = Path(os.environ["CONDA_PREFIX"]) / "Scripts" / "hauler.exe"
    assert source.is_file(), "native Windows qualification requires environment-owned Hauler"
    version = json.loads((ROOT / "runtime" / "hauler.json").read_text())["hauler"]["version"]
    archive_path = tmp_path / "archive.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        archive.add(source, arcname="hauler.exe")
    payload = archive_path.read_bytes()
    manifest = _manifest(tmp_path, platform="windows-amd64", asset="hauler.tar.gz", payload=payload, version=version)
    conda = tmp_path / "conda"
    conda.mkdir()

    target = install(manifest, conda, download=_download(payload))
    result = subprocess.run([str(target), "version"], capture_output=True, text=True, check=True)
    assert version.removeprefix("v") in result.stdout
