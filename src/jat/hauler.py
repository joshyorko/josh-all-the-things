"""Exact Hauler subprocess contract."""

from pathlib import Path

from .process import ProcessRunner


class HaulerAdapter:
    def __init__(self, runner: ProcessRunner, executable: str = "hauler", timeout: float = 3600):
        self.runner = runner
        self.executable = executable
        self.timeout = timeout

    def sync(self, store: Path, temp: Path, manifest: Path):
        return self._run(["store", "sync", "--store", str(store), "--tempdir", str(temp), "--filename", str(manifest)])

    def save(self, store: Path, temp: Path, haul: Path):
        return self._run(["store", "save", "--store", str(store), "--tempdir", str(temp), "--filename", str(haul)])

    def load(self, store: Path, temp: Path, haul: Path):
        return self._run(["store", "load", "--store", str(store), "--tempdir", str(temp), "--filename", str(haul)])

    def info(self, store: Path, temp: Path):
        return self._run(["store", "info", "--store", str(store), "--tempdir", str(temp)])

    def extract(self, reference: str, store: Path, temp: Path, output: Path):
        return self._run(
            ["store", "extract", reference, "--store", str(store), "--tempdir", str(temp), "--output", str(output)]
        )

    def serve(self, store: Path, temp: Path, directory: Path, config: Path):
        return self._run(
            [
                "store",
                "serve",
                "registry",
                "--store",
                str(store),
                "--tempdir",
                str(temp),
                "--directory",
                str(directory),
                "--config",
                str(config),
            ],
            foreground=True,
        )

    def _run(self, arguments: list[str], foreground: bool = False):
        completed = self.runner.run(
            [self.executable, *arguments], timeout=self.timeout, foreground=foreground
        )
        if not completed.success:
            raise RuntimeError(completed.diagnostics or "Hauler operation failed")
        return completed
