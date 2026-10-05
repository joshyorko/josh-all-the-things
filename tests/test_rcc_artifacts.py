import json
import warnings
import zipfile

import pytest

from jat.process import ProcessResult
from jat.rcc_artifacts import RCCArtifactAdapter


class RecordingRunner:
    def __init__(self, responses):
        self.calls = []
        self.responses = list(responses)

    def run(self, argv, timeout=None, foreground=False, secrets=(), env=None):
        self.calls.append((argv, timeout, foreground, secrets, env))
        return self.responses.pop(0)


def result(argv=(), stdout="", exit_status=0, stderr=""):
    return ProcessResult(argv=list(argv), stdout=stdout, stderr=stderr, exit_status=exit_status)


def write_rcca(path, *, artifact=None, specification=None, legacy=None, platform="linux_amd64"):
    manifest = {
        "artifactDigest": artifact or "sha256:" + "a" * 64,
        "specification": {
            "digest": specification or "sha256:" + "b" * 64,
            "platform": {"rccPlatform": platform},
        },
        "legacyBlueprint": {"legacyBlueprintKey": legacy or "c" * 16},
        "platform": {"rccPlatform": platform},
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("rcc-environment/manifest.json", json.dumps(manifest))
    return manifest


def verified_acquire_payload(artifact="sha256:" + "a" * 64, platform="linux_amd64", valid=True):
    return {
        "artifactDigest": artifact,
        "materializationId": "fixture-materialization",
        "path": "/fixture/cache/environment",
        "cacheHit": "provider",
        "compatibility": {"schemaVersion": 1, "status": "compatible"},
        "verification": {
            "valid": valid,
            "code": "valid" if valid else "invalid",
            "artifactDigest": artifact,
            "platform": platform,
        },
    }


def test_rcc_adapter_uses_released_json_commands_and_returns_metadata(tmp_path):
    source = tmp_path / "robot.yaml"
    source.write_text("tasks: {}\n")
    archive = tmp_path / "environment.rcca"
    manifest = write_rcca(archive)
    runner = RecordingRunner(
        [
            result(stdout="rcc v18.19.5\n"),
            result(stdout=json.dumps({"artifactDigest": "sha256:" + "a" * 64, "specificationDigest": "sha256:" + "b" * 64, "legacyBlueprintKey": "c" * 16})),
            result(stdout="published\n"),
            result(stdout=json.dumps(verified_acquire_payload(manifest["artifactDigest"]))),
            result(stdout="rcc v18.19.5\n"),
            result(stdout="[]\n"),
        ]
    )
    adapter = RCCArtifactAdapter(runner, executable="/tools/rcc")

    metadata = adapter.publish_and_export(source, archive)
    acquired = adapter.acquire(archive)
    adapter.verify(source)

    assert metadata.artifact == "sha256:" + "a" * 64
    assert metadata.specification_digest == "sha256:" + "b" * 64
    assert metadata.legacy_blueprint_key == "c" * 16
    assert metadata.rcc_version == "v18.19.5"
    assert acquired.artifact == metadata.artifact
    assert [call[0] for call in runner.calls] == [
        ["/tools/rcc", "version"],
            ["/tools/rcc", "env", "publish", "--environment", str(source), "--provider", "local", "--json"],
        ["/tools/rcc", "env", "export", "--artifact", metadata.artifact, "--provider", "local", "--output", str(archive)],
        ["/tools/rcc", "env", "acquire", "--archive", str(archive), "--permissive-local", "--json"],
        ["/tools/rcc", "version"],
        ["/tools/rcc", "--no-build", "ht", "vars", "--robot", str(source), "--json"],
    ]


def test_rcc_adapter_rejects_non_json_verification_output(tmp_path):
    runner = RecordingRunner([result(stdout="rcc v18.19.5\n"), result(stdout="not-json")])
    adapter = RCCArtifactAdapter(runner)
    try:
        adapter.verify(tmp_path / "robot.yaml")
    except (ValueError, TypeError, RuntimeError):
        pass
    else:
        raise AssertionError("non-JSON verification output was accepted")


def test_rcc_adapter_verifies_saved_archive_identity_in_private_runtime_home(tmp_path):
    archive = tmp_path / "saved.rcca"
    manifest = write_rcca(archive)
    runtime_home = tmp_path / "private-rcc-home"
    runtime_home.mkdir()
    runner = RecordingRunner([result(stdout=json.dumps(verified_acquire_payload()))])

    metadata = RCCArtifactAdapter(runner, executable="/tools/rcc").acquire(
        archive,
        rcc_version="v18.19.5",
        specification_digest=manifest["specification"]["digest"],
        legacy_blueprint_key=manifest["legacyBlueprint"]["legacyBlueprintKey"],
        artifact_digest="sha256:" + "a" * 64,
        expected_platform="linux_amd64",
        runtime_home=runtime_home,
        strict_identity=True,
    )

    assert metadata.platform == "linux_amd64"
    argv, _, _, _, env = runner.calls[0]
    assert argv == [
        "/tools/rcc", "env", "acquire", "--archive", str(archive),
        "--artifact", "sha256:" + "a" * 64, "--permissive-local", "--json",
    ]
    assert env["ROBOCORP_HOME"] == str(runtime_home)


def test_rcc_adapter_rejects_saved_specification_mismatch(tmp_path):
    archive = tmp_path / "saved.rcca"
    write_rcca(archive)
    runner = RecordingRunner([result(stdout=json.dumps(verified_acquire_payload()))])

    try:
        RCCArtifactAdapter(runner, executable="/tools/rcc").acquire(
            archive,
            rcc_version="v18.19.5",
            specification_digest="sha256:" + "d" * 64,
            legacy_blueprint_key="c" * 16,
            artifact_digest="sha256:" + "a" * 64,
            expected_platform="linux_amd64",
            runtime_home=tmp_path / "private-rcc-home",
            strict_identity=True,
        )
    except ValueError as error:
        assert "specification digest" in str(error)
    else:
        raise AssertionError("mismatched saved RCC specification was accepted")


def test_strict_acquire_reads_identity_from_verified_canonical_archive(tmp_path):
    archive = tmp_path / "saved.rcca"
    manifest = write_rcca(archive)
    runner = RecordingRunner([result(stdout=json.dumps(verified_acquire_payload()))])

    metadata = RCCArtifactAdapter(runner, executable="/tools/rcc").acquire(
        archive,
        rcc_version="v18.19.5",
        specification_digest=manifest["specification"]["digest"],
        legacy_blueprint_key=manifest["legacyBlueprint"]["legacyBlueprintKey"],
        artifact_digest=manifest["artifactDigest"],
        expected_platform="linux_amd64",
        strict_identity=True,
    )

    assert metadata.artifact == manifest["artifactDigest"]
    assert metadata.specification_digest == manifest["specification"]["digest"]
    assert metadata.legacy_blueprint_key == manifest["legacyBlueprint"]["legacyBlueprintKey"]
    assert metadata.platform == "linux_amd64"


def test_strict_acquire_rejects_unverified_archive(tmp_path):
    archive = tmp_path / "saved.rcca"
    write_rcca(archive)
    runner = RecordingRunner([result(stdout=json.dumps(verified_acquire_payload(valid=False)))])

    try:
        RCCArtifactAdapter(runner).acquire(archive, artifact_digest="sha256:" + "a" * 64, strict_identity=True)
    except RuntimeError as error:
        assert "verification" in str(error).lower()
    else:
        raise AssertionError("RCC acquire result without valid verification was accepted")


def test_strict_acquire_rejects_archive_manifest_digest_mismatch(tmp_path):
    archive = tmp_path / "saved.rcca"
    manifest = write_rcca(archive, artifact="sha256:" + "d" * 64)
    runner = RecordingRunner([result(stdout=json.dumps(verified_acquire_payload()))])

    try:
        RCCArtifactAdapter(runner).acquire(archive, artifact_digest="sha256:" + "a" * 64, strict_identity=True)
    except ValueError as error:
        assert "artifact digest" in str(error).lower()
    else:
        raise AssertionError(f"inconsistent archive manifest was accepted: {manifest['artifactDigest']}")


def test_strict_acquire_rejects_archive_platform_mismatch(tmp_path):
    archive = tmp_path / "saved.rcca"
    write_rcca(archive, platform="linux_arm64")
    runner = RecordingRunner([result(stdout=json.dumps(verified_acquire_payload()))])

    with pytest.raises(ValueError, match="platform"):
        RCCArtifactAdapter(runner).acquire(
            archive,
            artifact_digest="sha256:" + "a" * 64,
            expected_platform="linux_amd64",
            strict_identity=True,
        )


def test_strict_acquire_rejects_duplicate_archive_members(tmp_path):
    archive = tmp_path / "duplicate.rcca"
    manifest = write_rcca(archive)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(archive, "a") as zipped:
            zipped.writestr("rcc-environment/manifest.json", json.dumps(manifest))
    runner = RecordingRunner([result(stdout=json.dumps(verified_acquire_payload()))])

    with pytest.raises(RuntimeError, match="duplicate members"):
        RCCArtifactAdapter(runner).acquire(
            archive,
            artifact_digest="sha256:" + "a" * 64,
            expected_platform="linux_amd64",
            strict_identity=True,
        )


def test_strict_acquire_rejects_oversized_manifest(tmp_path):
    archive = tmp_path / "oversized.rcca"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("rcc-environment/manifest.json", b" " * (1024 * 1024 + 1))
    runner = RecordingRunner([result(stdout=json.dumps(verified_acquire_payload()))])

    with pytest.raises(RuntimeError, match="size limits"):
        RCCArtifactAdapter(runner).acquire(
            archive,
            artifact_digest="sha256:" + "a" * 64,
            expected_platform="linux_amd64",
            strict_identity=True,
        )


def test_strict_acquire_rejects_tampered_manifest_header_even_when_identity_matches(tmp_path):
    archive = tmp_path / "tampered.rcca"
    write_rcca(archive)
    with archive.open("r+b") as stream:
        assert stream.read(4) == b"PK\x03\x04"
        stream.seek(0)
        stream.write(b"XX\x03\x04")
    runner = RecordingRunner([result(stdout=json.dumps(verified_acquire_payload()))])

    with pytest.raises(RuntimeError, match="canonical manifest could not be read safely"):
        RCCArtifactAdapter(runner).acquire(
            archive,
            artifact_digest="sha256:" + "a" * 64,
            expected_platform="linux_amd64",
            strict_identity=True,
        )


def test_strict_acquire_rejects_archive_symlink(tmp_path):
    target = tmp_path / "target.rcca"
    write_rcca(target)
    archive = tmp_path / "saved.rcca"
    archive.symlink_to(target)
    runner = RecordingRunner([result(stdout=json.dumps(verified_acquire_payload()))])

    with pytest.raises(RuntimeError, match="opened safely"):
        RCCArtifactAdapter(runner).acquire(
            archive,
            artifact_digest="sha256:" + "a" * 64,
            expected_platform="linux_amd64",
            strict_identity=True,
        )


def test_strict_acquire_rejects_duplicate_manifest_json_keys(tmp_path):
    archive = tmp_path / "duplicate-json.rcca"
    manifest = json.dumps(write_rcca(tmp_path / "valid.rcca"))
    manifest = manifest.replace('"artifactDigest":', '"artifactDigest":"sha256:' + "a" * 64 + '","artifactDigest":', 1)
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("rcc-environment/manifest.json", manifest)
    runner = RecordingRunner([result(stdout=json.dumps(verified_acquire_payload()))])

    with pytest.raises(RuntimeError, match="valid JSON"):
        RCCArtifactAdapter(runner).acquire(
            archive,
            artifact_digest="sha256:" + "a" * 64,
            expected_platform="linux_amd64",
            strict_identity=True,
        )


def test_rcc_adapter_rejects_unsupported_version(tmp_path):
    adapter = RCCArtifactAdapter(RecordingRunner([result(stdout="rcc v18.19.2\n")]))
    try:
        adapter.publish_and_export(tmp_path, tmp_path / "environment.rcca")
    except RuntimeError as error:
        assert "v18.19.5" in str(error)
    else:
        raise AssertionError("unsupported RCC version was accepted")
