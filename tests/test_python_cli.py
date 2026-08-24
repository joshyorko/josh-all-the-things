import json
from pathlib import Path

import pytest

from jat.cli import main
from jat.models import OperationResult


class RecordingService:
    def __init__(self):
        self.calls = []

    def build(self, request):
        self.calls.append(("build", request))
        return OperationResult(
            operation="build",
            success=True,
            exit_status=0,
            payload_path=request.output,
            payload_size=4,
            sha256="a" * 64,
            producer_version="synthetic",
        )

    def restore(self, request):
        self.calls.append(("restore", request))
        return OperationResult(
            operation="restore",
            success=True,
            exit_status=0,
            payload_path=request.destination,
            producer_version="synthetic",
        )

    def serve(self, request):
        self.calls.append(("serve", request))
        return OperationResult(operation="serve", success=True, exit_status=0, producer_version="synthetic")

    def doctor(self):
        self.calls.append(("doctor", None))
        return OperationResult(operation="doctor", success=True, exit_status=0, producer_version="synthetic")


def test_build_cli_calls_shared_service_and_prints_stable_json(capsys):
    service = RecordingService()
    status = main(
        [
            "build",
            "--folder",
            "/workspace",
            "--brew",
            "/recovery",
            "--image",
            "example/one:latest",
            "--image",
            "example/two:v1",
            "--output",
            "/tmp/haul.tar.zst",
            "--json",
        ],
        service=service,
    )
    assert status == 0
    operation, request = service.calls[0]
    assert operation == "build"
    assert request.images == ["example/one:latest", "example/two:v1"]
    payload = json.loads(capsys.readouterr().out)
    assert payload["format_version"] == 1
    assert payload["operation"] == "build"


def test_restore_and_serve_cli_contracts():
    service = RecordingService()
    assert main(["restore", "--haul", "haul.tar.zst", "--destination", "restored"], service=service) == 0
    assert main(["serve", "--haul", "haul.tar.zst"], service=service) == 0
    assert service.calls[0][0] == "restore"
    assert service.calls[1][0] == "serve"


def test_doctor_failure_returns_stable_exit(capsys):
    class FailingDoctor(RecordingService):
        def doctor(self):
            return OperationResult(
                operation="doctor",
                success=False,
                exit_status=1,
                producer_version="synthetic",
                diagnostics="missing hauler",
            )

    assert main(["doctor", "--json"], service=FailingDoctor()) == 1
    assert json.loads(capsys.readouterr().out)["diagnostics"] == "missing hauler"


def test_interactive_wizard_stays_in_cli_and_builds_restore_request():
    answers = iter(["restore", "workspace-haul.tar.zst", "restored"])
    service = RecordingService()
    assert main([], service=service, input_fn=lambda prompt: next(answers)) == 0
    operation, request = service.calls[0]
    assert operation == "restore"
    assert str(request.haul) == "workspace-haul.tar.zst"
    assert str(request.destination) == "restored"


def test_cli_rejects_conflicting_image_modes():
    with pytest.raises(SystemExit) as raised:
        main(
            [
                "build",
                "--folder",
                "/workspace",
                "--image",
                "example/image:latest",
                "--all-images",
                "--output",
                "/tmp/haul.tar.zst",
            ],
            service=RecordingService(),
        )
    assert raised.value.code == 2


def test_robot_3tc_task_invokes_python_cli():
    body = Path("robot.yaml").read_text()
    task = body.split("  3tc:", 1)[1].split("\ndevTasks:", 1)[0]
    assert "python -m jat.cli" in task
    assert "joshs-all-the-things.sh" not in task


def test_legacy_bash_entrypoint_is_only_an_rcc_3tc_shim():
    body = Path("joshs-all-the-things.sh").read_text()
    assert "rcc task script" in body
    assert '"$robot_root/3tc"' in body
    assert "build_haul()" not in body
    assert "restore_haul()" not in body
