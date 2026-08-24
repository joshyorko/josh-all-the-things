import json
import os
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
    assert json.loads((root / "docs/hololib-receipt.schema.json").read_text())["properties"]["environment_hash"]
    descriptor = (root / "hololib.robot.yaml").read_text()
    assert "conda.yaml" in descriptor
    assert "preRunScripts" not in descriptor
    assert "install_dependencies" not in descriptor
