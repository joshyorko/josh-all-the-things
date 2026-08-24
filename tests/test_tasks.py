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
