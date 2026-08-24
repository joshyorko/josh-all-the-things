import hashlib
import os
import subprocess
from pathlib import Path

from robocorp import log

from .models import BuildRequest, OperationResult, RestoreRequest, ServeRequest


class LegacyBashService:
    def __init__(self, root: Path | None = None, timeout: float = 3600):
        self.root = root or Path(__file__).parents[2]
        self.script = self.root / "joshs-all-the-things.sh"
        self.timeout = timeout

    def build(self, request: BuildRequest) -> OperationResult:
        argv = ["bash", str(self.script), "build", "--folder", str(request.folder)]
        if request.brew:
            argv += ["--brew", str(request.brew)]
        for image in request.images:
            argv += ["--image", image]
        if request.all_images:
            argv.append("--all-images")
        argv += ["--output", str(request.output)]
        result = self._run("build", argv)
        if result.success and request.output.is_file():
            result.payload_path = request.output
            result.payload_size = request.output.stat().st_size
            result.sha256 = _sha256(request.output)
        return result

    def restore(self, request: RestoreRequest) -> OperationResult:
        argv = ["bash", str(self.script), "restore", "--haul", str(request.haul), "--destination", str(request.destination)]
        result = self._run("restore", argv)
        if result.success:
            result.payload_path = request.destination
        return result

    def serve(self, request: ServeRequest) -> OperationResult:
        argv = ["bash", str(self.script), "serve", "--haul", str(request.haul)]
        return self._run("serve", argv, foreground=True)

    def doctor(self) -> OperationResult:
        missing = [command for command in ("bash", "hauler", "zstd") if not _which(command)]
        return OperationResult(
            operation="doctor",
            success=not missing,
            exit_status=0 if not missing else 1,
            producer_version=self._version(),
            diagnostics="" if not missing else f"missing commands: {', '.join(missing)}",
        )

    def _run(self, operation: str, argv: list[str], foreground: bool = False) -> OperationResult:
        log.info(f"Starting JAT {operation}")
        try:
            completed = subprocess.run(
                argv,
                cwd=os.environ.get("JAT_RUN_DIR") or os.getcwd(),
                capture_output=not foreground,
                text=True,
                timeout=self.timeout,
                check=False,
            )
            diagnostics = "" if foreground else "\n".join(filter(None, (completed.stdout, completed.stderr)))
            result = OperationResult(
                operation=operation,
                success=completed.returncode == 0,
                exit_status=completed.returncode,
                producer_version=self._version(),
                diagnostics=diagnostics,
            )
            log.info(f"Finished JAT {operation} with exit status {completed.returncode}")
            return result
        except subprocess.TimeoutExpired:
            log.warn(f"JAT {operation} timed out")
            return OperationResult(
                operation=operation,
                success=False,
                exit_status=124,
                producer_version=self._version(),
                diagnostics="operation timed out",
            )

    def _version(self) -> str:
        result = subprocess.run(["git", "-C", str(self.root), "rev-parse", "HEAD"], capture_output=True, text=True, check=False)
        return result.stdout.strip() if result.returncode == 0 else "unknown"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _which(command: str) -> str | None:
    from shutil import which

    return which(command)
