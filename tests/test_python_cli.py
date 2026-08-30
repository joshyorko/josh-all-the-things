import json
from pathlib import Path

import pytest

from jat.cli import main
from jat.models import ContentEntry, OperationResult, ServeEndpoints, TransferReceipt


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

    def inspect(self, request):
        self.calls.append(("inspect", request))
        return OperationResult(
            format_version=2,
            operation="inspect",
            success=True,
            exit_status=0,
            producer_version="synthetic",
            inventory=[ContentEntry(reference="hauler/x:latest", type="image")],
            anchors={"workspace": False, "brew": False, "rcc_environment": False, "rcc_metadata": False},
            complete=True,
        )

    def extract(self, request):
        self.calls.append(("extract", request))
        return OperationResult(
            format_version=2, operation="extract", success=True, exit_status=0, producer_version="synthetic", complete=True
        )

    def export(self, request):
        self.calls.append(("export", request))
        return OperationResult(
            format_version=2, operation="export", success=True, exit_status=0, producer_version="synthetic", complete=True
        )

    def copy(self, request):
        self.calls.append(("copy", request))
        return OperationResult(
            format_version=2, operation="copy", success=True, exit_status=0, producer_version="synthetic", complete=True
        )

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
            "--rcc-environment",
            "required",
            "--rcc-robot",
            "/workspace/robot.yaml",
            "--json",
        ],
        service=service,
    )
    assert status == 0
    operation, request = service.calls[0]
    assert operation == "build"
    assert request.images == ["example/one:latest", "example/two:v1"]
    assert request.rcc_environment == "required"
    assert request.rcc_robot == Path("/workspace/robot.yaml")
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


def test_robot_jat_task_invokes_python_cli():
    body = Path("robot.yaml").read_text()
    task = body.split("  JAT:", 1)[1].split("\ndevTasks:", 1)[0]
    assert "python -m jat.cli" in task
    assert "joshs-all-the-things.sh" not in task


def test_legacy_bash_entrypoint_is_only_an_rcc_jat_shim():
    body = Path("joshs-all-the-things.sh").read_text()
    assert "rcc task script" in body
    assert '"$robot_root/jat"' in body
    assert "build_haul()" not in body
    assert "restore_haul()" not in body


def test_build_cli_forwards_the_full_capture_contract(capsys):
    service = RecordingService()
    status = main(
        [
            "build",
            "--folder", "/workspace",
            "--output", "/tmp/haul.tar.zst",
            "--images-file", "./images.txt",
            "--hauler-manifest", "./airgap.yaml",
            "--hauler-manifest", "https://example.test/product.yaml",
            "--exclude-extras",
            "--chunk-size", "500MB",
            "--retries", "2",
            "--json",
        ],
        service=service,
    )
    assert status == 0
    _, request = service.calls[0]
    assert request.images_files == ["./images.txt"]
    assert request.hauler_manifests == ["./airgap.yaml", "https://example.test/product.yaml"]
    assert request.exclude_extras is True
    assert request.chunk_size == "500MB"
    assert request.retries == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["format_version"] == 1


def test_inspect_extract_export_copy_cli_contracts(capsys):
    service = RecordingService()
    assert main(["inspect", "--haul", "h.tar.zst", "--json"], service=service) == 0
    assert main(
        ["extract", "--haul", "h.tar.zst", "--reference", "hauler/x:latest", "--destination", "out", "--json"],
        service=service,
    ) == 0
    assert main(["export", "--haul", "h.tar.zst", "--output", "images.tar", "--json"], service=service) == 0
    assert main(
        [
            "copy",
            "--haul", "h.tar.zst",
            "--to", "registry://registry.example.test",
            "--retries", "5",
            "--plain-http",
            "--json",
        ],
        service=service,
    ) == 0
    assert service.calls[0][0] == "inspect"
    assert service.calls[1][1].reference == "hauler/x:latest"
    assert service.calls[2][1].format == "containerd"
    copy_request = service.calls[3][1]
    assert copy_request.to == "registry://registry.example.test"
    assert copy_request.retries == 5 and copy_request.plain_http is True


def test_serve_cli_defaults_to_auto_with_conventional_ports():
    service = RecordingService()
    assert main(["serve", "--haul", "h.tar.zst"], service=service) == 0
    _, request = service.calls[0]
    assert request.mode == "auto"
    assert request.fileserver_port == 8080
    assert request.registry_port == 5000
    service = RecordingService()
    assert main(
        ["serve", "--haul", "h.tar.zst", "--mode", "both", "--fileserver-port", "8081", "--registry-port", "5001"],
        service=service,
    ) == 0
    _, request = service.calls[0]
    assert request.mode == "both"
    assert request.fileserver_port == 8081
    assert request.registry_port == 5001


def test_inspect_human_output_lists_content_and_anchors(capsys):
    service = RecordingService()
    assert main(["inspect", "--haul", "h.tar.zst"], service=service) == 0
    output = capsys.readouterr().out
    assert "hauler/x:latest" in output
    assert "JAT anchors" in output


def test_invalid_cli_options_produce_normal_machine_readable_failure(capsys):
    service = RecordingService()
    status = main(
        ["build", "--folder", "/workspace", "--output", "/tmp/haul.tar.zst", "--retries", "0", "--json"],
        service=service,
    )
    assert status == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["operation"] == "build"
    assert payload["success"] is False
    assert payload["exit_status"] == 1
    assert "retries" in payload["diagnostics"]

    for arguments, operation in (
        (["build", "--folder", "/w", "--output", "/tmp/h.tar.zst", "--chunk-size", "1MiB", "--json"], "build"),
        (["copy", "--haul", "h.tar.zst", "--to", "registry://user:token@host", "--json"], "copy"),
        (["serve", "--haul", "h.tar.zst", "--registry-port", "0", "--json"], "serve"),
    ):
        service = RecordingService()
        assert main(arguments, service=service) == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["operation"] == operation
        assert payload["success"] is False
        assert "invalid request:" in payload["diagnostics"]
