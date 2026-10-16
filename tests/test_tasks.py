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


def test_windows_runtime_persists_archive_receipt_and_freeze_evidence():
    workflow = Path(".github/workflows/windows-runtime.yml").read_text()
    assert "jat-runtime-windows.rcca" in workflow
    assert "jat-runtime-windows.json" in workflow
    assert "jat_git_sha" in workflow
    assert "specification_digest" in workflow
    assert "legacy_blueprint_key" in workflow
    assert "verified_tasks" in workflow
    assert '"archive"' in workflow
    assert "Get-FileHash" in workflow
    assert ".Length" in workflow
    assert "rcc_version" in workflow
    assert "platform" in workflow


def test_rcc_manifest_pins_current_linux_and_windows_assets_without_future_placeholders():
    manifest_path = Path("runtime/rcc.json")
    manifest_text = manifest_path.read_text()
    manifest = json.loads(manifest_text)
    assert manifest["schema_version"] == 1
    assert manifest["version"] == "v18.19.7"
    assert manifest["source"] == {
        "repository": "https://github.com/joshyorko/rcc",
        "tag": "v18.19.7",
        "commit": "758c97808bef8ae8bc5779d82c23ee11abfa47b3",
    }
    assert manifest["platforms"] == {
        "linux_amd64": {
            "asset": "rcc-linux64",
            "url": "https://github.com/joshyorko/rcc/releases/download/v18.19.7/rcc-linux64",
            "sha256": "f8e0394be7f47ee84d6f4cd734822b87b492a4d6102f977f6a18f29453703d48",
        },
        "windows_amd64": {
            "asset": "rcc-windows64.exe",
            "url": "https://github.com/joshyorko/rcc/releases/download/v18.19.7/rcc-windows64.exe",
            "sha256": "d3338be49ef22bbd9f3e42b606e01231cb5c5d86716b18d24fadf85e7630e0be",
        },
    }
    for pin in manifest["platforms"].values():
        assert re.fullmatch(r"[0-9a-f]{64}", pin["sha256"])


def test_current_artifact_candidate_pins_official_hauler_v211_checksums():
    manifest = json.loads(Path("runtime/hauler.json").read_text())["hauler"]
    assert manifest["version"] == "v2.1.1"
    assert manifest["platforms"]["linux-amd64"] == {
        "asset": "hauler_2.1.1_linux_amd64.tar.gz",
        "url": "https://github.com/hauler-dev/hauler/releases/download/v2.1.1/hauler_2.1.1_linux_amd64.tar.gz",
        "sha256": "faa4d9602fdf92c2cb96eaaa3ba0889a54d2c415ba8806cda394cde5f9355b18",
        "executable": "hauler",
    }
    assert manifest["platforms"]["windows-amd64"] == {
        "asset": "hauler_2.1.1_windows_amd64.tar.gz",
        "url": "https://github.com/hauler-dev/hauler/releases/download/v2.1.1/hauler_2.1.1_windows_amd64.tar.gz",
        "sha256": "f6f4f524854f8efa9999a33924b93848eaacb12877181677442ebc0fe6d9d67a",
        "executable": "hauler.exe",
    }


def test_runtime_release_dispatch_binds_publication_to_exact_current_main_sha():
    workflow = Path(".github/workflows/windows-runtime.yml").read_text()
    assert "source_sha:" in workflow
    assert "Exact current main commit whose JAT runtimes should be qualified and published" in workflow
    assert "JAT_RELEASE_SOURCE_SHA: ${{ github.event_name == 'workflow_dispatch' && inputs.source_sha || github.sha }}" in workflow
    assert "JAT_RELEASE_TAG: v0.1.13-jat-runtime" in workflow
    assert "reject-non-main-release-dispatch:" in workflow
    assert "if: github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/main'" in workflow
    assert 'test "$(git rev-parse HEAD)" = "$SOURCE_SHA"' in workflow
    assert 'test "$(git rev-parse origin/main)" = "$SOURCE_SHA"' in workflow
    assert 'refs/tags/${JAT_RELEASE_TAG}^{}' in workflow
    assert "5b59fcafc832be67eaca3b8f42ff9f7b06719b76" not in workflow
    assert "v0.1.11-jat-runtime" not in workflow


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
    assert "Run the JAT Python suite in the pinned RCC environment" in workflow
    assert 'python -m pip install --quiet pytest jsonschema' in workflow
    assert 'suite_root="$RUNNER_TEMP/jat-python-suite"' in workflow
    assert 'export ROBOCORP_HOME="$RUNNER_TEMP/jat-linux-verifier"' in workflow
    assert 'JAT_RUN_DIR="$suite_root/run"' in workflow
    assert 'TMPDIR="$suite_root/tmp"' in workflow
    assert "docker pull busybox:1.36" in workflow
    assert "docker image inspect busybox:1.36" in workflow
    assert 'os.chdir(sys.argv[1])' in workflow
    assert 'pytest.main(sys.argv[2:])' in workflow
    assert '"$GITHUB_WORKSPACE" -q "$GITHUB_WORKSPACE/tests"' in workflow


def test_publisher_accepts_platform_specific_receipts_and_archive_names():
    script = Path("scripts/publish_environment_artifact.sh").read_text()
    assert "windows_amd64" in script
    assert "archive_filename" in script
    assert "archive.filename == $archive_filename" in script
    assert "platform-specific" in script


def test_multi_platform_publisher_accepts_linux_and_windows_pairs():
    script = Path("scripts/publish_environment_artifacts.sh")
    body = script.read_text()
    assert script.is_file()
    assert "--linux-archive" in body
    assert "--linux-receipt" in body
    assert "--windows-archive" in body
    assert "--windows-receipt" in body
    assert "publish_environment_artifact.sh" in body


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
