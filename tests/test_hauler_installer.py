import hashlib
import json
import os
import stat
import subprocess
import tarfile
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "install_hauler.sh"


def _archive(path: Path, version: str = "v2.0.3") -> bytes:
    payload = f"#!/bin/sh\nprintf 'GitVersion:    {version}\\n'\n".encode()
    with tarfile.open(path, "w:gz") as archive:
        info = tarfile.TarInfo("hauler")
        info.mode = 0o755
        info.size = len(payload)
        import io

        archive.addfile(info, io.BytesIO(payload))
    return path.read_bytes()


def _run(
    tmp_path: Path,
    payload: bytes,
    *,
    target: bytes | None = None,
    expected: str = "v2.0.3",
    manifest_payload: bytes | None = None,
):
    conda = tmp_path / "conda"
    (conda / "bin").mkdir(parents=True)
    manifest = tmp_path / "hauler.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "hauler": {
                    "version": expected,
                    "platform": "linux-amd64",
                    "asset": "hauler_linux_amd64.tar.gz",
                    "url": "https://github.com/hauler-dev/hauler/releases/download/v2.0.3/hauler_linux_amd64.tar.gz",
                    "sha256": hashlib.sha256(manifest_payload or payload).hexdigest(),
                },
            }
        )
    )
    fixture = tmp_path / "fixture.tar.gz"
    fixture.write_bytes(payload)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    curl = fake_bin / "curl"
    curl.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        "output=\n"
        "while [ \"$#\" -gt 0 ]; do\n"
        "  if [ \"$1\" = \"--output\" ]; then output=$2; shift 2; else shift; fi\n"
        "done\n"
        "cp -- \"$HAULER_FIXTURE\" \"$output\"\n"
    )
    curl.chmod(0o755)
    destination = conda / "bin" / "hauler"
    if target is not None:
        destination.write_bytes(target)
        destination.chmod(0o755)
    environment = {
        **os.environ,
        "CONDA_PREFIX": str(conda),
        "HAULER_MANIFEST": str(manifest),
        "HAULER_FIXTURE": str(fixture),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
    }
    result = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, env=environment, check=False)
    return result, destination


def test_canonical_manifest_contains_official_linux_amd64_pin():
    manifest = json.loads((ROOT / "runtime" / "hauler.json").read_text())
    assert manifest == {
        "schema_version": 1,
        "hauler": {
            "version": "v2.0.3",
            "platform": "linux-amd64",
            "asset": "hauler_2.0.3_linux_amd64.tar.gz",
            "url": "https://github.com/hauler-dev/hauler/releases/download/v2.0.3/hauler_2.0.3_linux_amd64.tar.gz",
            "sha256": "6685eb1ba86291566f3694d69a8b7e80c928e5a589853691cccf51b26bc61617",
        },
    }


def test_installer_verifies_before_extracting_and_promotes_atomically(tmp_path):
    archive = tmp_path / "archive.tar.gz"
    payload = _archive(archive)
    result, destination = _run(tmp_path, payload)

    assert result.returncode == 0, result.stderr
    assert destination.read_text() == "#!/bin/sh\nprintf 'GitVersion:    v2.0.3\\n'\n"
    assert stat.S_IMODE(destination.stat().st_mode) == 0o755


def test_checksum_mismatch_fails_before_promotion(tmp_path):
    archive = tmp_path / "archive.tar.gz"
    payload = _archive(archive)
    result, destination = _run(tmp_path, payload + b"tampered", manifest_payload=payload)

    assert result.returncode != 0
    assert "SHA256" in result.stderr
    assert not destination.exists()


def test_matching_existing_installation_is_reused_without_download(tmp_path):
    archive = tmp_path / "archive.tar.gz"
    payload = _archive(archive)
    existing = b"#!/bin/sh\nprintf 'GitVersion:    v2.0.3\\n'\n"
    result, destination = _run(tmp_path, payload, target=existing)

    assert result.returncode == 0, result.stderr
    assert destination.read_bytes() == existing


def test_mismatched_existing_installation_fails_closed(tmp_path):
    archive = tmp_path / "archive.tar.gz"
    payload = _archive(archive)
    existing = b"#!/bin/sh\nprintf 'GitVersion:    v1.0.0\\n'\n"
    result, destination = _run(tmp_path, payload, target=existing)

    assert result.returncode != 0
    assert "does not match" in result.stderr
    assert destination.read_bytes() == existing
