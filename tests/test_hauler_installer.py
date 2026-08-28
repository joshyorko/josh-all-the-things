import hashlib
import io
import json
import stat
import tarfile
from pathlib import Path

import pytest

from scripts.install_hauler import install, resolve_platform


ROOT = Path(__file__).parents[1]


def _archive(path: Path, executable: str = "hauler", version: str = "v2.0.3") -> bytes:
    payload = f"#!/bin/sh\nprintf 'GitVersion:    {version}\\n'\n".encode()
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


def _manifest(tmp_path: Path, *, platform: str, asset: str, payload: bytes, version: str = "v2.0.3") -> Path:
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
            "version": "v2.0.3",
            "platforms": {
                "linux-amd64": {
                    "asset": "hauler_2.0.3_linux_amd64.tar.gz",
                    "url": "https://github.com/hauler-dev/hauler/releases/download/v2.0.3/hauler_2.0.3_linux_amd64.tar.gz",
                    "sha256": "6685eb1ba86291566f3694d69a8b7e80c928e5a589853691cccf51b26bc61617",
                    "executable": "hauler",
                },
                "windows-amd64": {
                    "asset": "hauler_2.0.3_windows_amd64.tar.gz",
                    "url": "https://github.com/hauler-dev/hauler/releases/download/v2.0.3/hauler_2.0.3_windows_amd64.tar.gz",
                    "sha256": "e272b51f8323e6ca9a017f81821294a3cc55019f5e67cca525fa0efb8536b8c0",
                    "executable": "hauler.exe",
                },
            },
        },
    }


def test_windows_installs_exe_into_conda_scripts_without_admin(tmp_path):
    archive = tmp_path / "archive.tar.gz"
    payload = _archive(archive, executable="hauler.exe")
    conda = tmp_path / "conda"
    conda.mkdir()
    manifest = _manifest(
        tmp_path,
        platform="windows-amd64",
        asset="hauler_2.0.3_windows_amd64.tar.gz",
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
    assert target.read_bytes() == b"#!/bin/sh\nprintf 'GitVersion:    v2.0.3\\n'\n"
    assert stat.S_IMODE(target.stat().st_mode) & 0o111


def test_linux_installs_into_conda_bin_and_reuses_matching_target(tmp_path):
    archive = tmp_path / "archive.tar.gz"
    payload = _archive(archive)
    conda = tmp_path / "conda"
    conda.mkdir()
    manifest = _manifest(
        tmp_path,
        platform="linux-amd64",
        asset="hauler_2.0.3_linux_amd64.tar.gz",
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
        asset="hauler_2.0.3_linux_amd64.tar.gz",
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
        asset="hauler_2.0.3_linux_amd64.tar.gz",
        payload=payload,
    )
    conda = tmp_path / "conda"
    conda.mkdir()

    with pytest.raises(ValueError, match="unsafe"):
        install(manifest, conda, system="Linux", machine="x86_64", download=_download(payload))
    assert not (conda / "bin" / "hauler").exists()
    assert not (conda / "escaped").exists()


def test_mismatched_existing_installation_fails_closed(tmp_path):
    archive = tmp_path / "archive.tar.gz"
    payload = _archive(archive, version="v1.0.0")
    manifest = _manifest(
        tmp_path,
        platform="linux-amd64",
        asset="hauler_2.0.3_linux_amd64.tar.gz",
        payload=payload,
        version="v2.0.3",
    )
    conda = tmp_path / "conda"
    target = conda / "bin" / "hauler"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"#!/bin/sh\nprintf 'GitVersion:    v1.0.0\\n'\n")
    target.chmod(0o755)

    with pytest.raises(ValueError, match="does not match"):
        install(manifest, conda, system="Linux", machine="x86_64", download=_download(payload))
    assert target.read_bytes().endswith(b"v1.0.0\\n'\n")
