import json
from pathlib import Path

import pytest

from jat.io import load_request, write_result
from jat.models import BuildRequest, OperationResult
from jat.runtime import configure_runtime
from jat.task_runner import translate_args


def test_load_request_accepts_exact_json_input_contract(tmp_path):
    request_path = tmp_path / "build.json"
    request_path.write_text(json.dumps({"folder": str(tmp_path / "source"), "output": str(tmp_path / "haul.tar.zst")}))
    request = load_request(BuildRequest, ["--json-input", str(request_path)])
    assert request.folder == tmp_path / "source"
    with pytest.raises(ValueError, match="--json-input"):
        load_request(BuildRequest, [])
    with pytest.raises(ValueError, match="unexpected"):
        load_request(BuildRequest, ["--json-input", str(request_path), "extra"])


def test_write_result_uses_output_result_json(tmp_path, monkeypatch):
    monkeypatch.setattr("jat.io.get_output_dir", lambda: tmp_path)
    result = OperationResult(operation="doctor", success=True, exit_status=0, producer_version="synthetic")
    destination = write_result(result)
    assert destination == tmp_path / "result.json"
    assert json.loads(destination.read_text())["operation"] == "doctor"


def test_robot_declares_typed_task_surface():
    body = Path("robot.yaml").read_text()
    for task in ("Build", "Restore", "Serve"):
        assert f"  {task}:" in body
        assert f"-t {task}" in body
    assert "devTasks:" in body
    assert "  Doctor:" in body
    assert "  JAT:" in body
    assert "  JaTT:" not in body
    assert "  JoshAllTheThings:" not in body


def test_linux_environment_contract_installs_hauler_before_freeze():
    conda = Path("conda.yaml").read_text()
    robot = Path("robot.yaml").read_text()
    freeze = Path("environment_linux_amd64_freeze.yaml")

    assert "rccPostInstall:" in conda
    assert "bash scripts/install_hauler.sh" in conda
    assert "preRunScripts:" not in robot
    assert "install_dependencies.sh" not in robot
    assert freeze.is_file()
    assert "rccPostInstall:" in freeze.read_text()


def test_normal_runtime_contract_has_no_homebrew_install_or_probe():
    assert "brew" not in Path("robot.yaml").read_text().lower()
    assert "homebrew" not in Path("robot.yaml").read_text().lower()
    assert "brew" not in Path("src/jat/archive.py").read_text().lower()
    assert not Path("scripts/install_dependencies.sh").exists()
    assert not Path("Brewfile").exists()


def test_rcc_tasks_use_python_service_not_legacy_bash():
    body = Path("tasks.py").read_text()
    assert "JATService" in body
    assert "LegacyBashService" not in body


def test_task_runner_translates_only_public_json_input_spelling():
    assert translate_args(["run", "tasks.py", "--", "--json-input", "request.json"]) == [
        "run",
        "tasks.py",
        "--",
        "--json_input",
        "request.json",
    ]


def test_runtime_uses_native_truststore_and_robocorp_log(monkeypatch):
    calls = []
    monkeypatch.setattr("jat.runtime.truststore.inject_into_ssl", lambda: calls.append("truststore"))
    monkeypatch.setattr("jat.runtime.log.info", lambda message: calls.append(message))

    configure_runtime()

    assert calls == ["truststore", "JAT runtime initialized"]


def test_runtime_dependencies_are_explicitly_pinned():
    body = Path("conda.yaml").read_text()
    assert "robocorp-log==" in body
    assert "robocorp-truststore==" in body
