"""Exact Hauler subprocess contract."""

import json
import sys
from pathlib import Path

from .process import ProcessRunner


class HaulerAdapter:
    def __init__(self, runner: ProcessRunner, executable: str = "hauler", timeout: float = 3600, platform_name: str | None = None):
        self.runner = runner
        self.executable = executable
        self.timeout = timeout
        self.windows = (platform_name or sys.platform) in {"win32", "windows"}

    def sync_files(self, store: Path, temp: Path, files: list[tuple[Path, str]], images: list[str] | None = None):
        """Add local files/images through Hauler's Windows-supported API."""
        for path, name in files:
            self._run(
                [
                    "--store",
                    str(store),
                    "--tempdir",
                    str(temp),
                    "store",
                    "add",
                    "file",
                    str(path),
                    "--name",
                    name,
                ]
            )
        for image in images or []:
            self._run(
                [
                    "--store",
                    str(store),
                    "--tempdir",
                    str(temp),
                    "store",
                    "add",
                    "image",
                    image,
                    "--local",
                ]
            )

    def sync(self, store: Path, temp: Path, manifest: Path):
        return self._run(["store", "sync", "--store", str(store), "--tempdir", str(temp), "--filename", str(manifest)])

    def save(self, store: Path, temp: Path, haul: Path):
        return self._run(["store", "save", "--store", str(store), "--tempdir", str(temp), "--filename", str(haul)])

    def load(self, store: Path, temp: Path, haul: Path):
        return self._run(["store", "load", "--store", str(store), "--tempdir", str(temp), "--filename", str(haul)])

    def info(self, store: Path, temp: Path):
        return self._run(["store", "info", "--store", str(store), "--tempdir", str(temp)])

    def inventory(self, store: Path, temp: Path) -> list[dict]:
        completed = self._run(
            ["store", "info", "--store", str(store), "--tempdir", str(temp), "--output", "json"]
        )
        inventory = json.loads(completed.stdout)
        if not isinstance(inventory, list) or any(
            not isinstance(item, dict) or not isinstance(item.get("Reference"), str) for item in inventory
        ):
            raise ValueError("Hauler returned an invalid JSON inventory contract")
        return inventory

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
