"""JAT-owned literal workspace selection; never interprets shell/ignore syntax."""

import hashlib
import json
import os
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path

from .safety import ArchiveMember, validate_archive_members

CAPTURE_WARNING = (
    "Excluded files are not recoverable. Source-only capsules omit Git history and staging state; "
    "excluded worktree files are not backed up. JAT does not guarantee Git worktree relocation."
)


def validate_exclusions(values: list[str]) -> list[str]:
    for value in values:
        if (
            not value or len(value) > 4096 or "\\" in value or ":" in value
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
            or any(part in {"", ".", ".."} for part in value.split("/"))
        ):
            raise ValueError("workspace exclusions must be normalized relative POSIX literal paths")
    if len(values) > 10000:
        raise ValueError("workspace exclusions exceed the 10000-rule limit")
    return sorted(set(values))


@dataclass
class Selection:
    paths: list[str] = field(default_factory=lambda: ["."])
    excluded: list[str] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)
    complete: bool = True

    def summary(self, mode: str, exclusions: list[str]) -> dict:
        policy = {"version": 1, "capture_mode": mode, "workspace_exclusions": exclusions}
        return {
            **policy,
            "workspace_exclusions": exclusions[:100],
            "exclusion_rules_count": len(exclusions),
            "exclusion_rules_truncated": len(exclusions) > 100,
            "policy_sha256": hashlib.sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest(),
            "complete": self.complete,
            "included_count": len(self.paths),
            "excluded_count": len(self.excluded),
            "included_sample": self.paths[:100],
            "excluded_sample": self.excluded[:100],
            "summary_truncated": len(self.paths) > 100 or len(self.excluded) > 100,
            "findings": self.findings[:100],
            "warning": CAPTURE_WARNING,
        }


class _ScanBoundReached(Exception):
    pass


def select_workspace(
    source: Path, exclusions: list[str], mode: str, limit: int = 1000000, timeout: float = 30.0,
) -> Selection:
    """Enumerate without following links; incomplete coverage always fails closed."""
    if mode not in {"full-repository", "source-only"}:
        raise ValueError("unsupported workspace capture mode")
    selection = Selection()
    deadline = time.monotonic() + timeout
    members = [ArchiveMember(source.name, "directory")]
    exclusions = set(validate_exclusions(exclusions))
    pending = [source]
    visited = 0
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                bounded_entries = []
                for entry in entries:
                    visited += 1
                    if visited > limit:
                        raise _ScanBoundReached("workspace scan entry limit reached")
                    if time.monotonic() >= deadline:
                        raise _ScanBoundReached("workspace scan time limit reached")
                    bounded_entries.append(entry)
                for entry in sorted(bounded_entries, key=lambda item: item.name):
                    if time.monotonic() >= deadline:
                        raise _ScanBoundReached("workspace scan time limit reached")
                    relative = Path(entry.path).relative_to(source).as_posix()
                    if (mode == "source-only" and entry.name == ".git") or any(
                        "/".join(relative.split("/")[:index]) in exclusions
                        for index in range(1, len(relative.split("/")) + 1)
                    ):
                        selection.excluded.append(relative)
                        continue
                    info = entry.stat(follow_symlinks=False)
                    target = None
                    if stat.S_ISDIR(info.st_mode):
                        kind = "directory"
                        pending.append(Path(entry.path))
                    elif stat.S_ISREG(info.st_mode):
                        kind = "file"
                    elif stat.S_ISLNK(info.st_mode):
                        kind = "symlink"
                        target = os.readlink(entry.path)
                    else:
                        kind = "other"
                    selection.paths.append(relative)
                    members.append(ArchiveMember(f"{source.name}/{relative}", kind, target))
        except _ScanBoundReached as error:
            selection.complete = False
            selection.findings.append(str(error))
            break
        except KeyboardInterrupt:
            selection.complete = False
            selection.findings.append("workspace scan cancelled")
            break
        except OSError:
            selection.complete = False
            selection.findings.append("workspace subtree could not be read")
        if len(selection.findings) >= 100:
            selection.complete = False
            break
    selection.paths.sort()
    selection.excluded.sort()
    if selection.complete:
        try:
            validate_archive_members(members)
        except ValueError as error:
            selection.findings.append(str(error)[:1024])
    return selection
