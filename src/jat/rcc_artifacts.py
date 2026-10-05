"""RCC portable environment artifact adapter."""

import hashlib
import json
import os
import re
import stat
import zipfile
import zlib
from pathlib import Path

from .models import EnvironmentArtifactMetadata
from .process import ProcessRunner

EXPECTED_RCC_VERSION = "v18.19.5"
_MAX_RCC_ARCHIVE_SIZE = 2 * 1024 * 1024 * 1024
_MAX_RCC_ARCHIVE_MEMBERS = 100_000
_MAX_RCC_MANIFEST_SIZE = 1024 * 1024
_MAX_RCC_JSON_DEPTH = 64


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
        verification = payload.get("verification")
        if not isinstance(verification, dict) or verification.get("valid") is not True:
            raise RuntimeError("RCC acquire did not return valid artifact verification")
        verified_digest = _required_digest(verification, "artifactDigest")
        if verified_digest != artifact:
            raise ValueError("RCC acquire verification artifact digest did not match acquire result")
        artifact_platform = _required_string(verification, "platform")
        manifest, archive_sha256, archive_size = _canonical_archive_manifest(archive)
        if _required_digest(manifest, "artifactDigest") != verified_digest:
            raise ValueError("verified RCC artifact digest did not match canonical archive manifest")
        specification_data = manifest.get("specification")
        if not isinstance(specification_data, dict):
            raise RuntimeError("RCCA manifest did not contain specification metadata")
        specification = _required_digest(specification_data, "digest")
        legacy_data = manifest.get("legacyBlueprint")
        if not isinstance(legacy_data, dict):
            raise RuntimeError("RCCA manifest did not contain legacy blueprint metadata")
        legacy = _required_string(legacy_data, "legacyBlueprintKey")
        platform_data = manifest.get("platform")
        if not isinstance(platform_data, dict):
            raise RuntimeError("RCCA manifest did not contain platform metadata")
        manifest_platform = _required_string(platform_data, "rccPlatform")
        specification_platform = specification_data.get("platform")
        if not isinstance(specification_platform, dict):
            raise RuntimeError("RCCA specification did not contain platform metadata")
        if _required_string(specification_platform, "rccPlatform") != manifest_platform:
            raise ValueError("RCCA specification platform did not match canonical archive manifest")
        if manifest_platform != artifact_platform:
            raise ValueError("RCC verification platform did not match canonical archive manifest")
        if strict_identity and (artifact_digest is None or expected_platform is None):
            raise RuntimeError("strict RCC acquire requires saved artifact and platform identities")
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
            archive_sha256=archive_sha256,
            archive_size=archive_size,
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


def _canonical_archive_manifest(archive: Path) -> tuple[dict, str, int]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(archive, flags)
    except OSError as error:
        raise RuntimeError("RCCA archive could not be opened safely") from error
    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_size <= 0 or file_stat.st_size > _MAX_RCC_ARCHIVE_SIZE:
            raise RuntimeError("RCCA archive was not a bounded regular file")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            with zipfile.ZipFile(stream) as archive_file:
                members = archive_file.infolist()
                if len(members) > _MAX_RCC_ARCHIVE_MEMBERS:
                    raise RuntimeError("RCCA archive contained too many members")
                names = [member.filename for member in members]
                if len(names) != len(set(names)):
                    raise RuntimeError("RCCA archive contained duplicate members")
                manifest_members = [member for member in members if member.filename == "rcc-environment/manifest.json"]
                if len(manifest_members) != 1:
                    raise RuntimeError("RCCA archive did not contain one canonical manifest")
                member = manifest_members[0]
                mode = member.external_attr >> 16
                if stat.S_ISLNK(mode) or member.flag_bits & 1:
                    raise RuntimeError("RCCA canonical manifest was not a regular unencrypted member")
                if member.file_size > _MAX_RCC_MANIFEST_SIZE or member.compress_size > _MAX_RCC_MANIFEST_SIZE:
                    raise RuntimeError("RCCA canonical manifest exceeded size limits")
                raw = archive_file.read(member)
            current_stat = os.fstat(descriptor)
            if (file_stat.st_dev, file_stat.st_ino, file_stat.st_size, file_stat.st_mtime_ns) != (
                current_stat.st_dev,
                current_stat.st_ino,
                current_stat.st_size,
                current_stat.st_mtime_ns,
            ):
                raise RuntimeError("RCCA archive changed while its canonical manifest was read")
            stream.seek(0)
            digest = hashlib.sha256()
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return _bounded_json_object(raw), digest.hexdigest(), file_stat.st_size
    except (OSError, zipfile.BadZipFile, RuntimeError, EOFError, json.JSONDecodeError, zlib.error) as error:
        if isinstance(error, RuntimeError) and str(error).startswith("RCCA"):
            raise
        raise RuntimeError("RCCA canonical manifest could not be read safely") from error
    finally:
        os.close(descriptor)


def _bounded_json_object(raw: bytes) -> dict:
    if len(raw) > _MAX_RCC_MANIFEST_SIZE:
        raise RuntimeError("RCCA canonical manifest exceeded JSON size limit")
    try:
        text = raw.decode("utf-8")
        depth = 0
        in_string = False
        escaped = False
        for character in text:
            if in_string:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == '"':
                    in_string = False
            elif character == '"':
                in_string = True
            elif character in "[{":
                depth += 1
                if depth > _MAX_RCC_JSON_DEPTH:
                    raise RuntimeError("RCCA canonical manifest exceeded JSON nesting limit")
            elif character in "]}":
                depth -= 1

        def reject_duplicate_keys(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("RCCA canonical manifest contained duplicate JSON keys")
                result[key] = value
            return result

        value = json.loads(text, object_pairs_hook=reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as error:
        raise RuntimeError("RCCA canonical manifest was not bounded valid JSON") from error
    if not isinstance(value, dict):
        raise RuntimeError("RCCA canonical manifest was not a JSON object")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
