import json

import pytest
from pydantic import ValidationError

from jat.models import (
    BuildRequest,
    EnvironmentArtifactMetadata,
    OperationResult,
    RestoreRequest,
    ServeRequest,
)


def test_build_request_parses_typed_json_and_rejects_conflicting_image_modes(tmp_path):
    request = BuildRequest.model_validate(
        {
            "folder": str(tmp_path / "source"),
            "output": str(tmp_path / "output.tar.zst"),
            "brew": str(tmp_path / "brew"),
            "images": ["ghcr.io/example/app:latest"],
            "all_images": False,
        }
    )
    assert request.folder == tmp_path / "source"
    assert request.images == ["ghcr.io/example/app:latest"]
    with pytest.raises(ValidationError):
        BuildRequest(folder=tmp_path, output=tmp_path / "out", images=["image"], all_images=True)


def test_build_request_defaults_rcc_environment_off_and_accepts_robot(tmp_path):
    request = BuildRequest(folder=tmp_path, output=tmp_path / "out", rcc_robot=tmp_path / "robot.yaml")
    assert request.rcc_environment == "off"
    assert request.rcc_robot == tmp_path / "robot.yaml"


def test_restore_and_serve_requests_are_strict(tmp_path):
    restore = RestoreRequest.model_validate({"haul": str(tmp_path / "haul.tar.zst"), "destination": str(tmp_path / "restored")})
    serve = ServeRequest.model_validate({"haul": str(tmp_path / "haul.tar.zst")})
    assert restore.destination == tmp_path / "restored"
    assert serve.haul == restore.haul
    with pytest.raises(ValidationError):
        RestoreRequest.model_validate({"haul": "haul", "destination": "restored", "unknown": True})


def test_operation_result_is_versioned_bounded_and_stable(tmp_path):
    result = OperationResult(
        operation="build",
        success=True,
        exit_status=0,
        payload_path=tmp_path / "haul.tar.zst",
        payload_size=4,
        sha256="a" * 64,
        producer_version="synthetic",
        diagnostics="x" * 4096,
    )
    assert result.format_version == 1
    assert len(result.diagnostics) == 2048
    destination = tmp_path / "result.json"
    result.write(destination)
    body = json.loads(destination.read_text())
    assert body == {
        "diagnostics": "x" * 2048,
        "exit_status": 0,
        "format_version": 1,
        "operation": "build",
        "payload_path": str(tmp_path / "haul.tar.zst"),
        "payload_size": 4,
        "producer_version": "synthetic",
        "sha256": "a" * 64,
        "success": True,
    }


def test_operation_result_can_carry_environment_artifact_metadata():
    metadata = EnvironmentArtifactMetadata(
        artifact="sha256:" + "a" * 64,
        archive="rcc-environment.rcca",
        rcc_version="18.19.1",
        robot="robot.yaml",
    )
    result = OperationResult(
        operation="restore",
        success=True,
        exit_status=0,
        producer_version="synthetic",
        environment_artifact=metadata,
    )
    assert result.environment_artifact == metadata
