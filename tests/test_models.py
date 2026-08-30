import json

import pytest
from pydantic import ValidationError

from jat.models import (
    ANCHOR_KINDS,
    ArtifactOutput,
    BuildRequest,
    ContentEntry,
    CopyRequest,
    EnvironmentArtifactMetadata,
    ExportRequest,
    ExtractRequest,
    InspectRequest,
    OperationResult,
    RestoreRequest,
    ServeEndpoints,
    ServeRequest,
    TransferReceipt,
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
        specification_digest="sha256:" + "b" * 64,
        legacy_blueprint_key="c" * 16,
        archive="rcc-environment.rcca",
        archive_sha256="d" * 64,
        archive_size=4,
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


def test_build_request_accepts_and_bounds_new_capture_contract():
    request = BuildRequest(
        folder="src",
        output="out.tar.zst",
        images_files=["./images.txt", "https://example.test/images.txt"],
        hauler_manifests=["./airgap.yaml"],
        exclude_extras=True,
        chunk_size="500MB",
        retries=1,
    )
    assert request.images_files[1].startswith("https://")
    assert request.chunk_size == "500MB"
    assert request.retries == 1
    for bad_retries in (0, -2):
        with pytest.raises(ValidationError):
            BuildRequest(folder="s", output="o", retries=bad_retries)
    with pytest.raises(ValidationError):
        BuildRequest(folder="s", output="o", chunk_size="five-hundred")
    with pytest.raises(ValidationError):
        BuildRequest(folder="s", output="o", images_files=["./images.txt", "ftp://example.test/images.txt"])


def test_serve_request_modes_and_ports_are_strict():
    serve = ServeRequest(haul="haul.tar.zst", mode="both", fileserver_port=8081, registry_port=5001)
    assert serve.mode == "both"
    assert serve.fileserver_port == 8081
    assert ServeRequest(haul="h").mode == "auto"
    with pytest.raises(ValidationError):
        ServeRequest(haul="h", mode="cluster")
    with pytest.raises(ValidationError):
        ServeRequest(haul="h", registry_port=0)


def test_inspect_extract_export_copy_requests_are_strict():
    inspect = InspectRequest(haul="haul.tar.zst")
    extract = ExtractRequest(haul="haul.tar.zst", reference="hauler/x:latest", destination="out")
    export = ExportRequest(haul="haul.tar.zst", output="images.tar")
    copy = CopyRequest(haul="haul.tar.zst", to="registry://reg.example.test", retries=1)
    assert extract.reference == "hauler/x:latest"
    assert export.format == "containerd"
    with pytest.raises(ValidationError):
        ExtractRequest(haul="haul.tar.zst", reference="two tokens", destination="out")
    with pytest.raises(ValidationError):
        ExtractRequest(haul="haul.tar.zst", reference="", destination="out")
    with pytest.raises(ValidationError):
        ExportRequest(haul="haul.tar.zst", output="o.tar", format="docker")
    with pytest.raises(ValidationError):
        CopyRequest(haul="haul.tar.zst", to="s3://bucket")
    with pytest.raises(ValidationError):
        CopyRequest(haul="haul.tar.zst", to="registry://reg", retries=0)


def test_content_entry_normalizes_and_bounds_hauler_metadata():
    entry = ContentEntry.from_hauler(
        {
            "Reference": "hauler/probe.txt:latest",
            "Type": "file",
            "Platform": "-",
            "Digest": "sha256:" + "a" * 64,
            "Layers": 1,
            "Size": 14,
            "Extra": {"nested": "value"},
        }
    )
    assert entry.reference == "hauler/probe.txt:latest"
    assert entry.metadata == {"Extra": '{"nested": "value"}'}
    bloated = ContentEntry(
        reference="r",
        type="file",
        metadata={f"key{index}": "x" * 600 for index in range(40)},
    )
    assert len(bloated.metadata) == 32
    assert all(len(value) <= 512 for value in bloated.metadata.values())


def test_operation_result_v2_requires_explicit_version_transition(tmp_path):
    structured = OperationResult(
        format_version=2,
        operation="inspect",
        success=True,
        exit_status=0,
        producer_version="synthetic",
        inventory=[ContentEntry(reference="hauler/x:latest", type="image")],
        anchors=dict.fromkeys(ANCHOR_KINDS, False),
        complete=True,
    )
    assert structured.anchors == {
        "workspace": False,
        "brew": False,
        "rcc_environment": False,
        "rcc_metadata": False,
    }
    with pytest.raises(ValidationError):
        OperationResult(
            operation="build",
            success=True,
            exit_status=0,
            producer_version="synthetic",
            payloads=[ArtifactOutput(path=tmp_path / "haul.tar.zst", size=4, sha256="a" * 64)],
        )
    legacy = OperationResult(
        operation="build",
        success=True,
        exit_status=0,
        payload_path=tmp_path / "haul.tar.zst",
        payload_size=4,
        sha256="a" * 64,
        producer_version="synthetic",
    )
    body = json.loads(legacy.model_dump_json(exclude_none=True))
    assert body["format_version"] == 1
    assert "payloads" not in body and "complete" not in body and "inventory" not in body


def test_operation_result_v2_represents_multi_output_serve_and_transfer(tmp_path):
    chunked = OperationResult(
        format_version=2,
        operation="build",
        success=True,
        exit_status=0,
        producer_version="synthetic",
        payloads=[
            ArtifactOutput(path=tmp_path / "haul_0.tar.zst", size=10, sha256="a" * 64),
            ArtifactOutput(path=tmp_path / "haul_1.tar.zst", size=10, sha256="b" * 64),
        ],
        complete=True,
    )
    assert [output.path.name for output in chunked.payloads] == ["haul_0.tar.zst", "haul_1.tar.zst"]
    served = OperationResult(
        format_version=2,
        operation="serve",
        success=True,
        exit_status=0,
        producer_version="synthetic",
        serve=ServeEndpoints(
            mode="both",
            fileserver_url="http://127.0.0.1:8080",
            registry_url="http://127.0.0.1:5000",
            fileserver_bind="all-interfaces",
            registry_bind="loopback",
        ),
        complete=True,
    )
    assert served.serve.registry_bind == "loopback"
    copied = OperationResult(
        format_version=2,
        operation="copy",
        success=True,
        exit_status=0,
        producer_version="synthetic",
        transfer=TransferReceipt(
            destination="registry://reg.example.test",
            transport="remote-registry",
            requested_retries=2,
            effective_retries=2,
        ),
        complete=True,
    )
    assert copied.transfer.requested_retries == 2
