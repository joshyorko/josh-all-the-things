"""RCC portable environment artifact adapter."""

import hashlib
import json
import os
import re
from pathlib import Path

from .models import EnvironmentArtifactMetadata
from .process import ProcessRunner

EXPECTED_RCC_VERSION = "v18.19.5"


class RCCArtifactAdapter:
    def __init__(self, runner: ProcessRunner, executable: str = "rcc", timeout: float = 600):
        self.runner = runner
        self.executable = executable
        self.timeout = timeout

    def publish_and_export(self, source: Path, archive: Path, robot: Path | None = None) -> EnvironmentArtifactMetadata:
        version = self.version()
        publish_source = ["--robot", str(robot)] if robot else ["--environment", str(source)]
        publish = self._run(
            [self.executable, "env", "publish", *publish_source, "--provider", "local", "--json"]
        )
        payload = _json_object(publish.stdout)
        artifact = _artifact(payload)
        specification = _required_digest(payload, "specificationDigest")
        legacy = _required_string(payload, "legacyBlueprintKey")
        self._run(
            [
                self.executable,
                "env",
                "export",
                "--artifact",
                artifact,
                "--provider",
                "local",
                "--output",
                str(archive),
            ]
        )
        return EnvironmentArtifactMetadata(
            artifact=artifact,
            specification_digest=specification,
            legacy_blueprint_key=legacy,
            archive=archive,
            archive_sha256=_sha256(archive),
            archive_size=archive.stat().st_size,
            rcc_version=version,
            robot=robot or source,
        )

    def acquire(
        self,
        archive: Path,
        robot: Path | None = None,
        rcc_version: str | None = None,
        specification_digest: str | None = None,
        legacy_blueprint_key: str | None = None,
        *,
        artifact_digest: str | None = None,
        expected_platform: str | None = None,
        runtime_home: Path | None = None,
        strict_identity: bool = False,
    ) -> EnvironmentArtifactMetadata:
        arguments = [
            self.executable,
            "env",
            "acquire",
            "--archive",
            str(archive),
        ]
        if artifact_digest is not None:
            arguments.extend(("--artifact", artifact_digest))
        arguments.extend(("--permissive-local", "--json"))
        environment = None
        if runtime_home is not None:
            environment = os.environ.copy()
            environment["ROBOCORP_HOME"] = str(runtime_home)
        acquired = self._run(
            arguments,
            env=environment,
        )
        payload = _json_object(acquired.stdout)
        artifact = _artifact(payload)
        specification = (
            _required_digest(payload, "specificationDigest")
            if strict_identity
            else _optional_digest(payload, "specificationDigest", specification_digest)
        )
        legacy = (
            _required_string(payload, "legacyBlueprintKey")
            if strict_identity
            else _optional_string(payload, "legacyBlueprintKey", legacy_blueprint_key)
        )
        artifact_platform = payload.get("platform")
        if strict_identity and (not isinstance(artifact_platform, str) or not artifact_platform):
            raise RuntimeError("RCC acquire verification did not return a platform")
        if artifact_digest is not None and artifact != artifact_digest:
            raise ValueError("acquired RCC environment artifact digest did not match saved metadata")
        if specification_digest is not None and specification != specification_digest:
            raise ValueError("acquired RCC specification digest did not match saved metadata")
        if legacy_blueprint_key is not None and legacy != legacy_blueprint_key:
            raise ValueError("acquired RCC legacy blueprint key did not match saved metadata")
        if expected_platform is not None and artifact_platform != expected_platform:
            raise ValueError("acquired RCC platform did not match saved metadata")
        return EnvironmentArtifactMetadata(
            artifact=artifact,
            specification_digest=specification,
            legacy_blueprint_key=legacy,
            archive=archive,
            archive_sha256=_sha256(archive),
            archive_size=archive.stat().st_size,
            rcc_version=rcc_version or self.version(),
            platform=artifact_platform,
            robot=robot or Path("robot.yaml"),
            acquired=True,
        )

    def verify(self, robot: Path) -> None:
        result = self._run([self.executable, "--no-build", "ht", "vars", "--robot", str(robot), "--json"])
        value = json.loads(result.stdout)
        if not isinstance(value, list):
            raise TypeError("RCC ht vars JSON output was not an array")

    def version(self) -> str:
        result = self._run([self.executable, "version"])
        match = re.search(r"(?:^|\s)v?(\d+\.\d+\.\d+)(?:\s|$)", result.stdout)
        if not match:
            raise RuntimeError("RCC version output did not contain a semantic version")
        version = f"v{match.group(1)}"
        if version != EXPECTED_RCC_VERSION:
            raise RuntimeError(f"RCC {EXPECTED_RCC_VERSION} is required; found {version}")
        return version

    def _run(self, argv: list[str], env: dict[str, str] | None = None):
        if env is None:
            result = self.runner.run(argv, timeout=self.timeout)
        else:
            result = self.runner.run(argv, timeout=self.timeout, env=env)
        if not result.success:
            raise RuntimeError(result.diagnostics or f"RCC command failed: {' '.join(argv)}")
        return result


def _json_object(output: str) -> dict:
    try:
        value = json.loads(output)
    except json.JSONDecodeError:
        start = output.find("{")
        end = output.rfind("}")
        if start < 0 or end < start:
            raise RuntimeError("RCC JSON output was not an object")
        value = json.loads(output[start : end + 1])
    if not isinstance(value, dict):
        raise TypeError("RCC JSON output was not an object")
    return value


def _artifact(payload: dict) -> str:
    value = payload.get("artifactDigest") or payload.get("artifact") or payload.get("artifact_digest") or payload.get("digest")
    if not isinstance(value, str) or not value.startswith("sha256:"):
        raise RuntimeError("RCC JSON output did not contain an artifact digest")
    return value


def _required_digest(payload: dict, key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise RuntimeError(f"RCC JSON output did not contain {key}")
    return value


def _required_string(payload: dict, key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"RCC JSON output did not contain {key}")
    return value


def _optional_digest(payload: dict, key: str, fallback: str | None) -> str:
    value = payload.get(key, fallback)
    if not isinstance(value, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise RuntimeError(f"RCC JSON output did not contain {key}")
    return value


def _optional_string(payload: dict, key: str, fallback: str | None) -> str:
    value = payload.get(key, fallback)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"RCC JSON output did not contain {key}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
