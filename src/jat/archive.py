"""Contained archive adapters for the JAT compressed archive boundary."""

import os
import shlex
import sys
import tarfile
from collections.abc import Callable
from pathlib import Path
from pathlib import PurePosixPath
from shutil import which as system_which

from .process import ProcessRunner
from .safety import ArchiveMember, validate_archive_members


class _WindowsArchiveBackend:
    def __init__(self, zstandard_module=None):
        if zstandard_module is None:
            try:
                import zstandard as zstandard_module
            except ImportError as error:
                raise RuntimeError("Windows JAT archive backend requires contained zstandard") from error
        self.zstandard = zstandard_module

    def create(self, source: Path, archive: Path) -> None:
        paths = [source, *sorted(source.rglob("*"), key=lambda path: path.relative_to(source).as_posix())]
        with archive.open("wb") as raw:
            compressor = self.zstandard.ZstdCompressor(level=3)
            with compressor.stream_writer(raw, closefd=False) as compressed:
                with tarfile.open(fileobj=compressed, mode="w|", format=tarfile.PAX_FORMAT) as tar:
                    for path in paths:
                        if path.is_symlink():
                            raise ValueError(f"archive source contains an unsupported symbolic link: {path}")
                        if not path.is_file() and not path.is_dir():
                            raise ValueError(f"archive source contains an unsupported entry: {path}")
                        relative = path.relative_to(source).as_posix()
                        name = source.name if relative == "." else f"{source.name}/{relative}"
                        info = tar.gettarinfo(str(path), arcname=name)
                        info.mtime = 0
                        info.uid = 0
                        info.gid = 0
                        info.uname = ""
                        info.gname = ""
                        info.pax_headers = {}
                        if info.isfile():
                            with path.open("rb") as content:
                                tar.addfile(info, content)
                        elif info.isdir():
                            tar.addfile(info)

    def members(self, archive: Path) -> list[ArchiveMember]:
        result = []
        with self._open_reader(archive) as tar:
            for member in tar:
                if member.isfile():
                    kind = "file"
                elif member.isdir():
                    kind = "directory"
                elif member.issym():
                    kind = "symlink"
                elif member.islnk():
                    kind = "hardlink"
                elif member.isdev():
                    kind = "device"
                else:
                    kind = "other"
                result.append(ArchiveMember(member.name, kind))
        return result

    def extract(self, archive: Path, destination: Path, strip_components: int = 0) -> None:
        validate_archive_members(self.members(archive))
        destination.mkdir(parents=True, exist_ok=True)
        with self._open_reader(archive) as tar:
            for member in tar:
                parts = PurePosixPath(member.name).parts[strip_components:]
                if not parts:
                    continue
                target = _safe_target(destination, "/".join(parts))
                if member.isdir():
                    if target.exists() and (target.is_symlink() or not target.is_dir()):
                        raise ValueError(f"archive extraction target is not a directory: {target}")
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if not member.isfile():
                    raise ValueError(f"archive member has unsupported member type: {member.name}")
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.is_symlink() or target.is_dir():
                    raise ValueError(f"archive extraction target is not a regular file: {target}")
                content = tar.extractfile(member)
                if content is None:
                    raise ValueError(f"archive member cannot be read: {member.name}")
                with target.open("wb") as output:
                    while chunk := content.read(1024 * 1024):
                        output.write(chunk)
                try:
                    os.chmod(target, member.mode & 0o777)
                except OSError:
                    pass

    def _open_reader(self, archive: Path):
        return _ZstdTarReader(self.zstandard, archive)


class _ZstdTarReader:
    def __init__(self, zstandard_module, archive: Path):
        self.zstandard = zstandard_module
        self.archive = archive
        self.raw = None
        self.reader = None
        self.tar = None

    def __enter__(self):
        self.raw = self.archive.open("rb")
        self.reader = self.zstandard.ZstdDecompressor().stream_reader(self.raw, closefd=False)
        self.tar = tarfile.open(fileobj=self.reader, mode="r|")
        return self.tar

    def __exit__(self, exc_type, exc_value, traceback):
        if self.tar is not None:
            self.tar.close()
        if self.reader is not None:
            self.reader.close()
        if self.raw is not None:
            self.raw.close()


def _safe_target(destination: Path, name: str) -> Path:
    path = PurePosixPath(name)
    if path.is_absolute() or "\\" in name or ".." in path.parts:
        raise ValueError(f"archive member contains an unsafe path: {name}")
    target = destination.joinpath(*path.parts)
    resolved_destination = destination.resolve()
    resolved_target = target.resolve(strict=False)
    if resolved_target != resolved_destination and resolved_destination not in resolved_target.parents:
        raise ValueError(f"archive member escapes extraction destination: {name}")
    return target


class ArchiveAdapter:
    def __init__(
        self,
        runner: ProcessRunner,
        executable: str | None = None,
        which: Callable[[str], str | None] = system_which,
        timeout: float = 3600,
        platform_name: str | None = None,
        zstandard_module=None,
    ):
        self.runner = runner
        self.timeout = timeout
        self.platform_name = platform_name or sys.platform
        self.windows = self.platform_name in {"win32", "windows"}
        if self.windows:
            self.executable = None
            self._windows_backend = _WindowsArchiveBackend(zstandard_module)
        else:
            self.executable = executable or self._resolve(which)
            self._windows_backend = None

    def _resolve(self, which: Callable[[str], str | None]) -> str:
        for name in ("gtar", "tar"):
            candidate = which(name)
            if candidate and self.runner.run([candidate, "--zstd", "--version"], timeout=30).success:
                return candidate
        raise RuntimeError("GNU tar with --zstd is unavailable (gtar or capable tar required)")

    def create(self, source: Path, archive: Path) -> None:
        if self.windows:
            self._windows_backend.create(source, archive)
            return
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
        if self.windows:
            self._windows_backend.extract(archive, destination, strip_components)
            return
        argv = [self.executable, "--zstd", "-xpf", str(archive), "-C", str(destination)]
        if strip_components:
            argv.append(f"--strip-components={strip_components}")
        self._require(self.runner.run(argv, timeout=self.timeout))

    def members(self, archive: Path) -> list[ArchiveMember]:
        if self.windows:
            return self._windows_backend.members(archive)
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
