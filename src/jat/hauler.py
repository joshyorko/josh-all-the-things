"""Exact Hauler subprocess contract."""

import json
import os
import re
import sys
from pathlib import Path

from .process import ProcessRunner


class HaulerAdapter:
    def __init__(
        self,
        runner: ProcessRunner,
        executable: str = "hauler",
        timeout: float = 3600,
        platform_name: str | None = None,
        progress=None,
    ):
        self.runner = runner
        self.executable = executable
        self.timeout = timeout
        self.windows = (platform_name or sys.platform) in {"win32", "windows"}
        self.progress = progress

    def sync_files(
        self,
        store: Path,
        temp: Path,
        files: list[tuple[Path, str]],
        images: list[str] | None = None,
        retries: int | None = None,
        exclude_extras: bool = False,
    ):
        """Add local files/images through Hauler's Windows-supported API."""
        for path, name in files:
            payload_name = _safe_windows_payload_name(path)
            self._run(
                [
                    "--store",
                    str(store),
                    "--tempdir",
                    str(temp),
                    "store",
                    "add",
                    "file",
                    payload_name,
                    "--name",
                    name,
                ],
                cwd=path.parent,
            )
        for image in images or []:
            arguments = [
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
            if exclude_extras:
                arguments.append("--exclude-extras")
            arguments.extend(_retries_arguments(retries))
            self._run(arguments)

    def sync(self, store: Path, temp: Path, *manifests: str | Path, retries: int | None = None, exclude_extras: bool = False):
        """Sync one or more Hauler manifests, passed exactly as the user gave them."""
        arguments = [
            "store",
            "sync",
            "--store",
            str(store),
            "--tempdir",
            str(temp),
        ]
        if exclude_extras:
            arguments.append("--exclude-extras")
        for manifest in manifests:
            arguments.extend(("--filename", str(manifest)))
        arguments.extend(_retries_arguments(retries))
        return self._run(arguments, stream=True)

    def sync_image_txt(self, store: Path, temp: Path, sources: list[str], retries: int | None = None, exclude_extras: bool = False):
        """Delegate image list acquisition to Hauler's native --image-txt."""
        if not sources:
            return None
        arguments = [
            "store",
            "sync",
            "--store",
            str(store),
            "--tempdir",
            str(temp),
        ]
        if exclude_extras:
            arguments.append("--exclude-extras")
        for source in sources:
            arguments.extend(("--image-txt", str(source)))
        arguments.extend(_retries_arguments(retries))
        return self._run(arguments, stream=True)

    def save(self, store: Path, temp: Path, haul: Path, chunk_size: str | None = None, containerd: bool = False):
        if chunk_size and containerd:
            raise ValueError("Hauler v2 chunking and --containerd export are mutually exclusive")
        arguments = [
            "store",
            "save",
            "--store",
            str(store),
            "--tempdir",
            str(temp),
            "--filename",
            str(haul),
        ]
        if chunk_size:
            arguments.extend(("--chunk-size", str(chunk_size)))
        if containerd:
            arguments.append("--containerd")
        return self._run(arguments, stream=True)

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

    def copy(self, store: Path, temp: Path, target: str, retries: int | None = None, plain_http: bool = False, insecure: bool = False):
        """Delegate movement to Hauler's store copy for a supported target."""
        arguments = [
            "store",
            "copy",
            "--store",
            str(store),
            "--tempdir",
            str(temp),
        ]
        if plain_http:
            arguments.append("--plain-http")
        if insecure:
            arguments.append("--insecure")
        arguments.extend(_retries_arguments(retries))
        arguments.append(str(target))
        return self._run(arguments, stream=True)

    def extract(self, reference: str, store: Path, temp: Path, output: Path):
        return self._run(
            ["store", "extract", reference, "--store", str(store), "--tempdir", str(temp), "--output", str(output)]
        )

    def serve(self, store: Path, temp: Path, directory: Path, config: Path, port: int | None = None):
        arguments = [
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
        ]
        if port is not None:
            arguments.extend(("--port", str(port)))
        log_level = os.environ.get("JAT_HAULER_LOG_LEVEL")
        if log_level:
            arguments = ["--log-level", log_level, *arguments]
        return self._run(
            arguments,
            foreground=True,
        )

    def serve_files(self, store: Path, temp: Path, directory: Path, port: int = 8080):
        command = self.serve_fileserver_command(store, temp, directory, port)
        completed = self.runner.run(command, timeout=self.timeout, foreground=True)
        if not completed.success:
            raise RuntimeError(completed.diagnostics or "Hauler operation failed")
        return completed

    def serve_fileserver_command(self, store: Path, temp: Path, directory: Path, port: int = 8080) -> list[str]:
        arguments = [
            "--store",
            str(store),
            "--tempdir",
            str(temp),
            "store",
            "serve",
            "fileserver",
            "--directory",
            str(directory),
            "--port",
            str(port),
        ]
        return self._command_arguments(arguments)

    def serve_registry_command(self, store: Path, temp: Path, directory: Path, config: Path) -> list[str]:
        # The registry config file owns the loopback bind address and port.
        arguments = [
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
        ]
        return self._command_arguments(arguments)

    def _command_arguments(self, arguments: list[str]) -> list[str]:
        log_level = os.environ.get("JAT_HAULER_LOG_LEVEL")
        if log_level:
            return [self.executable, "--log-level", log_level, *arguments]
        return [self.executable, *arguments]

    def _run(self, arguments: list[str], foreground: bool = False, cwd: Path | None = None, stream: bool = False):
        options = {}
        if stream and self.progress is not None:
            options["on_line"] = self.progress
        completed = self.runner.run(
            [self.executable, *arguments], timeout=self.timeout, foreground=foreground, cwd=cwd, **options
        )
        if not completed.success:
            raise RuntimeError(completed.diagnostics or "Hauler operation failed")
        return completed


def _retries_arguments(retries: int | None) -> list[str]:
    """Hauler v2.0.3 --retries is a persistent store option; default 3."""
    if retries is None:
        return []
    return ["--retries", str(int(retries))]


_WINDOWS_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL"} | {
    f"COM{number}" for number in range(1, 10)
} | {f"LPT{number}" for number in range(1, 10)}


def _safe_windows_payload_name(path: Path) -> str:
    name = path.name
    stem = re.split(r"[.]", name, maxsplit=1)[0].upper()
    if (
        not name
        or name in {".", ".."}
        or "/" in name
        or "\\" in name
        or ":" in name
        or name.endswith((".", " "))
        or stem in _WINDOWS_RESERVED_NAMES
    ):
        raise ValueError(f"unsafe Windows payload filename: {name!r}")
    return name
