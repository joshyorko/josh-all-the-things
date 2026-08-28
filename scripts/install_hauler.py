#!/usr/bin/env python3
"""Install the pinned Hauler binary into the RCC environment being built."""

from __future__ import annotations

import hashlib
import json
import os
import platform as platform_module
import posixpath
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path
from typing import Callable


OFFICIAL_RELEASE_PREFIX = "https://github.com/hauler-dev/hauler/releases/download/"
VERSION_PATTERN = re.compile(r"^v\d+\.\d+\.\d+$")
DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def resolve_platform(system: str | None = None, machine: str | None = None) -> str:
    system = system or platform_module.system()
    machine = (machine or platform_module.machine()).lower()
    architecture = {"x86_64": "amd64", "amd64": "amd64"}.get(machine, machine)
    platform_name = f"{system.lower()}-{architecture}"
    if platform_name not in {"linux-amd64", "windows-amd64"}:
        raise ValueError(f"unsupported JAT Hauler platform: {system}/{machine}")
    return platform_name


def _read_manifest(path: Path, platform_name: str) -> tuple[str, dict[str, str]]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"unable to read JAT Hauler manifest: {error}") from error
    if document.get("schema_version") != 1 or not isinstance(document.get("hauler"), dict):
        raise ValueError("invalid JAT Hauler manifest schema")
    hauler = document["hauler"]
    version = hauler.get("version")
    platforms = hauler.get("platforms")
    pin = platforms.get(platform_name) if isinstance(platforms, dict) else None
    if not isinstance(version, str) or not VERSION_PATTERN.fullmatch(version):
        raise ValueError("JAT Hauler version is not an exact release")
    if not isinstance(pin, dict):
        raise ValueError(f"JAT Hauler manifest has no pin for {platform_name}")
    fields = ("asset", "url", "sha256", "executable")
    if any(not isinstance(pin.get(field), str) or not pin[field] for field in fields):
        raise ValueError(f"JAT Hauler manifest is incomplete for {platform_name}")
    if not DIGEST_PATTERN.fullmatch(pin["sha256"]):
        raise ValueError("JAT Hauler SHA256 is invalid")
    if not pin["url"].startswith(OFFICIAL_RELEASE_PREFIX):
        raise ValueError("JAT Hauler URL is not the official upstream release host")
    if pin["url"] != f"{OFFICIAL_RELEASE_PREFIX}{version}/{pin['asset']}":
        raise ValueError("JAT Hauler URL does not match its pinned release asset")
    expected_executable = "hauler.exe" if platform_name == "windows-amd64" else "hauler"
    if pin["executable"] != expected_executable:
        raise ValueError(f"JAT Hauler executable is invalid for {platform_name}")
    return version, pin


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "JAT-Hauler-Installer"})
    with urllib.request.urlopen(request) as response, destination.open("wb") as output:
        shutil.copyfileobj(response, output, length=1024 * 1024)
        output.flush()
        os.fsync(output.fileno())


def _safe_member(member: tarfile.TarInfo) -> bool:
    normalized = posixpath.normpath(member.name)
    return bool(member.name) and not member.name.startswith("/") and "\\" not in member.name and normalized not in {".", ".."} and not normalized.startswith("../") and not (member.issym() or member.islnk() or member.isdev()) and (member.isfile() or member.isdir())


def _read_executable(archive_path: Path, executable: str) -> bytes:
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            members = archive.getmembers()
            if any(not _safe_member(member) for member in members):
                raise ValueError("unsafe Hauler archive member")
            matches = [member for member in members if member.isfile() and member.name == executable]
            if len(matches) != 1:
                raise ValueError(f"Hauler archive must contain exactly one regular file named {executable!r}")
            stream = archive.extractfile(matches[0])
            if stream is None:
                raise ValueError("Hauler executable cannot be read from archive")
            return stream.read()
    except (OSError, tarfile.TarError) as error:
        raise ValueError(f"unsafe Hauler archive: {error}") from error


def _version_matches(target: Path, version: str) -> bool:
    try:
        result = subprocess.run(
            [str(target), "version"], capture_output=True, text=True, encoding="utf-8",
            errors="replace", check=False, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return False
    output = f"{result.stdout}\n{result.stderr}"
    return result.returncode == 0 and re.search(rf"GitVersion:\s*{re.escape(version)}(?:\s|$)", output) is not None


def install(
    manifest_path: Path,
    conda_prefix: Path,
    *,
    system: str | None = None,
    machine: str | None = None,
    download: Callable[[str, Path], None] | None = None,
) -> Path:
    platform_name = resolve_platform(system, machine)
    version, pin = _read_manifest(manifest_path, platform_name)
    windows = platform_name == "windows-amd64"
    target = conda_prefix / ("Scripts" if windows else "bin") / pin["executable"]
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        if not target.is_file() or target.is_symlink() or (not windows and not os.access(target, os.X_OK)):
            raise ValueError(f"existing Hauler target is not a regular executable: {target}")
        if not _version_matches(target, version):
            raise ValueError(f"existing Hauler does not match pinned version {version}: {target}")
        return target

    stage_directory = Path(tempfile.mkdtemp(prefix=".hauler-install-", dir=conda_prefix))
    try:
        archive_path = stage_directory / pin["asset"]
        (download or _download)(pin["url"], archive_path)
        observed = _sha256(archive_path)
        if observed != pin["sha256"]:
            raise ValueError(f"Hauler archive SHA256 mismatch: expected {pin['sha256']}, got {observed}")
        payload = _read_executable(archive_path, pin["executable"])
        staged_target = stage_directory / pin["executable"]
        staged_target.write_bytes(payload)
        staged_target.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        os.replace(staged_target, target)
        if not _version_matches(target, version):
            raise ValueError(f"installed Hauler failed the pinned version check: {target}")
        return target
    finally:
        shutil.rmtree(stage_directory, ignore_errors=True)


def main() -> int:
    prefix = os.environ.get("CONDA_PREFIX")
    if not prefix:
        raise SystemExit("CONDA_PREFIX is required for the JAT Hauler installation.")
    manifest = Path(os.environ.get("HAULER_MANIFEST", Path(__file__).parents[1] / "runtime" / "hauler.json"))
    target = install(manifest, Path(prefix))
    print(f"Using verified Hauler at {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
