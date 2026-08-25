"""RCC portable environment artifact adapter."""

import json
import re
from pathlib import Path

from .models import EnvironmentArtifactMetadata
from .process import ProcessRunner


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
            archive=archive,
            rcc_version=version,
            robot=robot or source,
        )

    def acquire(self, archive: Path, robot: Path | None = None, rcc_version: str | None = None) -> EnvironmentArtifactMetadata:
        acquired = self._run(
            [
                self.executable,
                "env",
                "acquire",
                "--archive",
                str(archive),
                "--permissive-local",
                "--json",
            ]
        )
        artifact = _artifact(_json_object(acquired.stdout))
        return EnvironmentArtifactMetadata(
            artifact=artifact,
            archive=archive,
            rcc_version=rcc_version or self.version(),
            robot=robot or Path("robot.yaml"),
            acquired=True,
        )

    def verify(self, robot: Path) -> None:
        self._run([self.executable, "--no-build", "ht", "vars", "--robot", str(robot)])

    def version(self) -> str:
        result = self._run([self.executable, "version"])
        match = re.search(r"(?:^|\s)v?(\d+\.\d+\.\d+)(?:\s|$)", result.stdout)
        if not match:
            raise RuntimeError("RCC version output did not contain a semantic version")
        return match.group(1)

    def _run(self, argv: list[str]):
        result = self.runner.run(argv, timeout=self.timeout)
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
    value = payload.get("artifact") or payload.get("artifact_digest") or payload.get("digest")
    if not isinstance(value, str) or not value.startswith("sha256:"):
        raise RuntimeError("RCC JSON output did not contain an artifact digest")
    return value
