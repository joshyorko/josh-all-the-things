import json
import os
import subprocess
from pathlib import Path

import pytest

from scripts.build_hololib import clean_environment, main, reset_ephemeral_shared_root


def test_clean_environment_removes_active_rcc_and_python_state(tmp_path):
    environment = clean_environment(
        {
            "HOME": "/home/test",
            "PATH": "/home/test/.robocorp/holotree/space/bin:/usr/bin:/bin",
            "CONDA_PREFIX": "/active",
            "RCC_ENVIRONMENT_HASH": "old",
            "ROBOCORP_HOME": "/old",
            "PYTHONPATH": "/source",
            "SAFE": "yes",
        },
        tmp_path / "rcc-home",
        tmp_path / "home",
    )

    assert environment["ROBOCORP_HOME"] == str(tmp_path / "rcc-home")
    assert environment["HOME"] == str(tmp_path / "home")
    assert environment["PATH"] == "/usr/bin:/bin"
    assert environment["SAFE"] == "yes"
    assert not {"CONDA_PREFIX", "RCC_ENVIRONMENT_HASH", "PYTHONPATH"} & environment.keys()


def test_builder_rejects_existing_outputs(tmp_path):
    output = tmp_path / "hololib.zip"
    output.write_bytes(b"existing")

    with pytest.raises(FileExistsError):
        main(["--output", str(output), "--receipt", str(tmp_path / "receipt.json")])


def test_shared_root_reset_is_dagger_only(tmp_path, monkeypatch):
    monkeypatch.delenv("JAT_HOLOLIB_DAGGER", raising=False)
    with pytest.raises(RuntimeError, match="Dagger"):
        reset_ephemeral_shared_root(Path("/opt/robocorp"))
    monkeypatch.setenv("JAT_HOLOLIB_DAGGER", "1")
    with pytest.raises(ValueError, match="/opt/robocorp"):
        reset_ephemeral_shared_root(tmp_path)


def test_builder_contract_is_documented_and_ignored():
    root = Path(__file__).parents[1]
    assert "scripts/build_hololib.sh" in (root / "README.md").read_text()
    ignored = (root / ".gitignore").read_text().splitlines()
    assert "hololib.zip" in ignored
    assert "dist/" in ignored
    assert os.access(root / "scripts/build_hololib.sh", os.X_OK)
    wrapper = (root / "scripts/build_hololib.sh").read_text()
    assert "RCC_DAGGER_MODULE" in wrapper
    assert "dagger" in wrapper
    assert os.access(root / "scripts/publish_hololib.sh", os.X_OK)


def test_publish_script_uses_receipt_and_never_passes_token_in_argv(tmp_path):
    root = Path(__file__).parents[1]
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "oras.log"
    oras = fake_bin / "oras"
    oras.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >>\"$ORAS_LOG\"\n"
        "case \"$1 $2\" in\n"
        "  'login ghcr.io') cat >/dev/null ;;\n"
        "  'manifest fetch') printf '%s\\n' '{\"digest\":\"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\"}' ;;\n"
        "esac\n"
    )
    oras.chmod(0o755)
    archive = tmp_path / "hololib.zip"
    archive.write_bytes(b"synthetic archive")
    digest = __import__("hashlib").sha256(archive.read_bytes()).hexdigest()
    receipt = tmp_path / "hololib.json"
    receipt.write_text(json.dumps({
        "environment_hash": "environment",
        "format_version": 1,
        "jat_git_sha": "a" * 40,
        "operation": "build-hololib",
        "platform": "linux_amd64",
        "rcc_version": "v18.18.1",
        "success": True,
        "verified_no_build": True,
        "zip": {"filename": "hololib.zip", "sha256": digest, "size": archive.stat().st_size},
    }))
    environment = dict(os.environ)
    environment.update({
        "GITHUB_TOKEN": "synthetic-token",
        "ORAS_LOG": str(log),
        "PATH": f"{fake_bin}:{environment['PATH']}",
    })

    completed = subprocess.run(
        [str(root / "scripts/publish_hololib.sh"), "--zip", str(archive), "--receipt", str(receipt), "--repository", "ghcr.io/example/hololib"],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert "ghcr.io/example/hololib@sha256:" in completed.stdout
    assert "synthetic-token" not in log.read_text()
    assert json.loads((root / "docs/hololib-receipt.schema.json").read_text())["properties"]["environment_hash"]
    descriptor = (root / "hololib.robot.yaml").read_text()
    assert "conda.yaml" in descriptor
    assert "preRunScripts" not in descriptor
    assert "install_dependencies" not in descriptor
