"""Fail-closed path and archive layout validation."""

import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal


@dataclass(frozen=True)
class ArchiveMember:
    name: str
    kind: Literal["file", "directory", "symlink", "hardlink", "device", "other"]


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(path))


def _real_directory(path: Path, label: str) -> Path:
    absolute = _absolute(path)
    if not absolute.is_dir() or absolute.is_symlink() or absolute.resolve() != absolute:
        raise ValueError(f"{label} must be an existing real directory: {path}")
    return absolute


def new_output_path(path: Path) -> Path:
    absolute = _absolute(path)
    _real_directory(absolute.parent, "output parent")
    if absolute.exists() or absolute.is_symlink():
        raise ValueError(f"output already exists and will not be overwritten: {absolute}")
    return absolute


def existing_file(path: Path) -> Path:
    absolute = _absolute(path)
    if absolute.is_symlink():
        raise ValueError(f"file must not be a symbolic link: {absolute}")
    _real_directory(absolute.parent, "file parent")
    if not absolute.is_file():
        raise ValueError(f"path must be an existing regular file: {absolute}")
    return absolute


def existing_directory(path: Path) -> Path:
    return _real_directory(path, "directory")


def empty_destination(path: Path, haul: Path) -> Path:
    absolute = _absolute(path)
    if absolute == Path("/"):
        raise ValueError("destination must not be the filesystem root")
    if absolute.is_symlink():
        raise ValueError(f"destination must not be a symbolic link: {absolute}")
    _real_directory(absolute.parent, "destination parent")
    if absolute == existing_file(haul):
        raise ValueError("destination is unsafe or overlaps the haul file")
    if absolute.exists() and not absolute.is_dir():
        raise ValueError(f"destination is not a directory: {absolute}")
    if absolute.exists() and any(absolute.iterdir()):
        raise ValueError(f"destination must be empty: {absolute}")
    return absolute


def validate_archive_members(members: Iterable[ArchiveMember]) -> None:
    seen: dict[tuple[str, ...], str] = {}
    top_level: str | None = None
    count = 0

    for member in members:
        name = member.name
        if "\n" in name or "\r" in name:
            raise ValueError("archive member contains a line break")
        path = PurePosixPath(name)
        raw_parts = name.split("/")
        if not name or name == "." or path.is_absolute() or ".." in raw_parts:
            raise ValueError(f"archive member contains an unsafe path: {name}")
        if member.kind not in {"file", "directory"}:
            raise ValueError(f"archive member has unsupported member type: {member.kind}")

        parts = tuple(part for part in path.parts if part not in {"", "."})
        if not parts:
            raise ValueError(f"archive member contains an unsafe path: {name}")
        if top_level is None:
            top_level = parts[0]
        elif parts[0] != top_level:
            raise ValueError("archive must contain exactly one top-level entry")
        if parts in seen:
            raise ValueError(f"archive contains a duplicate member: {name}")
        for index in range(1, len(parts)):
            parent = parts[:index]
            if parent in seen and seen[parent] != "directory":
                raise ValueError(f"archive member has a collision with a non-directory parent: {name}")
        if member.kind != "directory" and any(existing[: len(parts)] == parts for existing in seen):
            raise ValueError(f"archive member has a collision with an existing child: {name}")
        seen[parts] = member.kind
        count += 1

    if count == 0:
        raise ValueError("archive is empty")
