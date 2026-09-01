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
    target: str | None = None


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


def new_output_directory(path: Path) -> Path:
    """A create-only directory destination that overwrites nothing."""
    absolute = _absolute(path)
    if absolute == Path("/"):
        raise ValueError("destination must not be the filesystem root")
    if absolute.exists() or absolute.is_symlink():
        raise ValueError(f"destination already exists and will not be overwritten: {absolute}")
    _real_directory(absolute.parent, "destination parent")
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


def _member_kind(path: tuple[str, ...], member_map: dict[tuple[str, ...], ArchiveMember]) -> str | None:
    member = member_map.get(path)
    if member is not None:
        return member.kind
    if any(candidate[: len(path)] == path for candidate in member_map if len(candidate) > len(path)):
        return "directory"
    return None


def _resolve_symlink(
    member: ArchiveMember,
    parts: tuple[str, ...],
    member_map: dict[tuple[str, ...], ArchiveMember],
    top_level: str,
) -> None:
    def target_parts(owner: ArchiveMember) -> tuple[str, ...]:
        target = owner.target
        if not target:
            reason = "symlink target is missing" if owner is member else "dangling symlink target"
            raise ValueError(f"archive member {member.name} has a {reason}")
        if "\\" in target or any(character in target for character in "\n\r\x00"):
            raise ValueError(f"archive member {member.name} has a symlink target with an unsafe separator")
        path = PurePosixPath(target)
        if path.is_absolute():
            raise ValueError(f"archive member {member.name} has an absolute symlink target")
        return path.parts

    directory_intent = bool(member.target and (member.target.endswith("/") or member.target.endswith("/.")))
    resolved: list[str] = []

    def resolve_components(components: tuple[str, ...], active: list[tuple[str, ...]]) -> None:
        for index, part in enumerate(components):
            if part in {"", "."}:
                continue
            if part == "..":
                if not resolved or (len(resolved) == 1 and resolved[0] == top_level):
                    raise ValueError(f"archive member {member.name} has a symlink target that escapes the archive root")
                if _member_kind(tuple(resolved), member_map) != "directory":
                    raise ValueError(f"archive member {member.name} traverses a non-directory target")
                resolved.pop()
                continue

            candidate = tuple([*resolved, part])
            candidate_member = member_map.get(candidate)
            if candidate_member is not None and candidate_member.kind == "symlink":
                if candidate in active:
                    raise ValueError(f"archive member {member.name} has a symlink cycle")
                active.append(candidate)
                resolve_components(target_parts(candidate_member), active)
                active.pop()
                continue

            kind = _member_kind(candidate, member_map)
            if kind is None:
                raise ValueError(f"archive member {member.name} has a dangling symlink target")
            if index < len(components) - 1 and kind != "directory":
                raise ValueError(f"archive member {member.name} traverses a non-directory target")
            resolved.append(part)
            if resolved[0] != top_level:
                raise ValueError(f"archive member {member.name} has a symlink target that escapes the archive root")

    resolve_components(tuple(parts[:-1]) + target_parts(member), [])

    if not resolved or resolved[0] != top_level:
        raise ValueError(f"archive member {member.name} has a symlink target that escapes the archive root")
    if directory_intent and _member_kind(tuple(resolved), member_map) != "directory":
        raise ValueError(f"archive member {member.name} has a symlink target that must resolve to a directory")


def validate_archive_members(members: Iterable[ArchiveMember]) -> None:
    member_map: dict[tuple[str, ...], ArchiveMember] = {}
    for member in members:
        name = member.name
        if "\n" in name or "\r" in name:
            raise ValueError(f"archive member contains a line break: {name}")
        path = PurePosixPath(name)
        raw_parts = name.split("/")
        if not name or name == "." or path.is_absolute() or ".." in raw_parts:
            raise ValueError(f"archive member contains an unsafe path: {name}")
        if member.kind not in {"file", "directory", "symlink"}:
            raise ValueError(f"archive member {name} has unsupported member type: {member.kind}")
        parts = tuple(part for part in path.parts if part not in {"", "."})
        if not parts:
            raise ValueError(f"archive member contains an unsafe path: {name}")
        if parts in member_map:
            raise ValueError(f"archive contains a duplicate member: {name}")
        member_map[parts] = member

    if not member_map:
        raise ValueError("archive is empty")
    top_levels = [parts for parts in member_map if len(parts) == 1]
    if len(top_levels) != 1:
        raise ValueError("archive must contain exactly one top-level entry")
    top_level = top_levels[0][0]
    root = member_map[top_levels[0]]
    if root.kind != "directory":
        raise ValueError(f"archive member {root.name} top-level entry must be a directory")
    for parts, member in member_map.items():
        if parts[0] != top_level:
            raise ValueError(f"archive member {member.name} is outside the top-level entry")
        for index in range(1, len(parts)):
            parent = parts[:index]
            parent_member = member_map.get(parent)
            if parent_member is not None and parent_member.kind != "directory":
                parent_name = "/".join(parent)
                raise ValueError(
                    f"archive member {member.name} has a collision with a non-directory parent {parent_name}"
                )
    for parts, member in member_map.items():
        if member.kind == "symlink":
            _resolve_symlink(member, parts, member_map, top_level)
