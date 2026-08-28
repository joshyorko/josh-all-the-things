import json
import re
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
    assert "python scripts/install_hauler.py" in conda
    assert "preRunScripts:" not in robot
    assert "install_dependencies.sh" not in robot
    assert freeze.is_file()
    assert "rccPostInstall:" in freeze.read_text()


def test_windows_source_environment_avoids_linux_only_tools():
    robot = Path("robot.yaml").read_text()
    windows = Path("environment_windows_amd64.yaml")
    assert windows.is_file()
    assert robot.index("environment_windows_amd64_freeze.yaml") < robot.index("environment_windows_amd64.yaml")
    assert robot.index("environment_windows_amd64.yaml") < robot.index("environment_linux_amd64_freeze.yaml")
    source = windows.read_text()
    assert "python=3.13.11" in source
    assert "python scripts/install_hauler.py" in source
    for dependency in ("bash", "coreutils", "curl", "tar", "zstd"):
        assert f"  - {dependency}" not in source


def test_linux_source_contract_keeps_archive_tools():
    source = Path("conda.yaml").read_text()
    for dependency in ("coreutils", "curl", "tar", "zstd"):
        assert f"  - {dependency}" in source
    freeze = Path("environment_linux_amd64_freeze.yaml").read_text()
    for dependency in ("coreutils", "tar", "zstd"):
        assert dependency in freeze


def test_windows_artifact_proof_uses_cmd_to_resolve_hauler_inside_child_path():
    workflow = Path(".github/workflows/windows-runtime.yml").read_text()
    assert "-- cmd.exe /d /c hauler.exe version" in workflow


def test_windows_local_file_probe_uses_artifact_python_argv():
    workflow = Path(".github/workflows/windows-runtime.yml").read_text()
    assert "subprocess.run([shutil.which('hauler.exe')" in workflow
    assert "cwd=str(payload.parent)" in workflow


def test_windows_serve_smoke_downloads_workspace_from_fileserver():
    workflow = Path(".github/workflows/windows-runtime.yml").read_text()
    assert "127.0.0.1:8080/joshs-all-the-things-workspace.tar.zst" in workflow
    assert "Invoke-WebRequest -Uri $workspaceUrl -OutFile $downloadedWorkspace -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop | Out-Null" in workflow
    assert "Get-Item -LiteralPath $downloadedWorkspace" in workflow
    assert "$response.StatusCode" not in workflow


def test_rcc_manifest_pins_current_linux_and_windows_assets_without_future_placeholders():
    manifest_path = Path("runtime/rcc.json")
    manifest_text = manifest_path.read_text()
    manifest = json.loads(manifest_text)
    assert manifest["schema_version"] == 1
    assert manifest["version"] == "v18.19.2"
    assert "v18.19.3" not in manifest_text
    assert manifest["platforms"] == {
        "linux_amd64": {
            "asset": "rcc-linux64",
            "url": "https://github.com/joshyorko/rcc/releases/download/v18.19.2/rcc-linux64",
            "sha256": "3a90a331325feb5b75b3ebc7492303a964438ce017347f451aeee3ed7d578b3d",
        },
        "windows_amd64": {
            "asset": "rcc-windows64.exe",
            "url": "https://github.com/joshyorko/rcc/releases/download/v18.19.2/rcc-windows64.exe",
            "sha256": "43acaf8ba0ab4c22c60832ef7e0ef4556a32843147e5ca33539796da648bb470",
        },
    }
    for pin in manifest["platforms"].values():
        assert re.fullmatch(r"[0-9a-f]{64}", pin["sha256"])


def test_workflow_has_native_linux_producer_and_reuses_canonical_receipt_flow():
    workflow = Path(".github/workflows/windows-runtime.yml").read_text()
    assert "linux-runtime:" in workflow
    assert "runs-on: ubuntu-24.04" in workflow
    assert "runtime/rcc.json" in workflow
    assert "$output = & $rcc version" in workflow
    assert "$rccExit = $LASTEXITCODE" in workflow
    assert "$actualVersion = ($output | Select-Object -First 1).Trim()" in workflow
    assert "$rccExit -ne 0 -or $actualVersion -cne $manifest.version" in workflow
    assert "scripts/build_environment_artifact.py" in workflow
    assert "env publish" in workflow
    assert "env export" in workflow
    assert "--no-build" in workflow
    assert "jat-linux-runtime-evidence" in workflow


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


def test_production_runtime_does_not_carry_unused_host_or_test_tools():
    body = Path("conda.yaml").read_text()
    assert "robocorp-tasks==4.1.1" in body
    for dependency in ("      - robocorp==", "  - uv=", "  - git", "  - gzip", "      - pytest==", "      - ruff=="):
        assert dependency not in body


def test_producer_version_fails_soft_when_git_is_unavailable(monkeypatch, tmp_path):
    from jat.services import _git_version

    def unavailable(*_args, **_kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr("jat.services.subprocess.run", unavailable)
    assert _git_version(tmp_path) == "unknown"
