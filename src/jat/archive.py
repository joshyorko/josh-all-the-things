"""GNU tar adapter for the JAT compressed archive boundary."""

import shlex
from collections.abc import Callable
from pathlib import Path
from shutil import which as system_which

from .process import ProcessRunner
from .safety import ArchiveMember


class ArchiveAdapter:
    def __init__(
        self,
        runner: ProcessRunner,
        executable: str | None = None,
        which: Callable[[str], str | None] = system_which,
        timeout: float = 3600,
    ):
        self.runner = runner
        self.timeout = timeout
        self.executable = executable or self._resolve(which)

    def _resolve(self, which: Callable[[str], str | None]) -> str:
        for name in ("gtar", "tar"):
            candidate = which(name)
            if candidate and self.runner.run([candidate, "--zstd", "--version"], timeout=30).success:
                return candidate
        raise RuntimeError("GNU tar with --zstd is unavailable (gtar or capable tar required)")

    def create(self, source: Path, archive: Path) -> None:
        self._require(
            self.runner.run(
                [
                    self.executable,
                    "--zstd",
                    "-cpf",
                    str(archive),
                    "-C",
                    str(source.parent),
                    "--",
                    source.name,
                ],
                timeout=self.timeout,
            )
        )

    def extract(self, archive: Path, destination: Path, strip_components: int = 0) -> None:
        argv = [self.executable, "--zstd", "-xpf", str(archive), "-C", str(destination)]
        if strip_components:
            argv.append(f"--strip-components={strip_components}")
        self._require(self.runner.run(argv, timeout=self.timeout))

    def members(self, archive: Path) -> list[ArchiveMember]:
        completed = self.runner.run(
            [
                self.executable,
                "--zstd",
                "-tvf",
                str(archive),
                "--numeric-owner",
                "--full-time",
                "--quoting-style=shell-always",
            ],
            timeout=self.timeout,
        )
        self._require(completed)
        return parse_verbose_listing(completed.stdout)

    @staticmethod
    def _require(completed) -> None:
        if not completed.success:
            raise RuntimeError(completed.diagnostics or "GNU tar operation failed")


def parse_verbose_listing(listing: str) -> list[ArchiveMember]:
    members = []
    kinds = {"-": "file", "d": "directory", "l": "symlink", "h": "hardlink", "b": "device", "c": "device"}
    for line in listing.splitlines():
        if not line:
            continue
        fields = shlex.split(line)
        if len(fields) < 6 or not fields[0]:
            raise ValueError("GNU tar returned an unrecognized archive listing")
        kind = kinds.get(fields[0][0], "other")
        members.append(ArchiveMember(fields[5], kind))
    return members
