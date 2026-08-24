"""Shared JAT build, restore, serve, and doctor service layer."""

import hashlib
import json
import os
import subprocess
from pathlib import Path
from shutil import which as system_which

from robocorp import log

from .archive import ArchiveAdapter
from .hauler import HaulerAdapter
from .models import BuildRequest, OperationResult, RestoreRequest, ServeRequest
from .process import ProcessRunner
from .safety import (
    empty_destination,
    existing_directory,
    existing_file,
    new_output_path,
    validate_archive_members,
)
from .staging import OwnedStage

WORKSPACE_ARTIFACT = "joshs-all-the-things-workspace.tar.zst"
WORKSPACE_REFERENCE = "hauler/joshs-all-the-things-workspace.tar.zst:latest"
BREW_ARTIFACT = "homebrew-recovery.tar.zst"
BREW_REFERENCE = "hauler/homebrew-recovery.tar.zst:latest"


class JATService:
    def __init__(
        self,
        archive=None,
        hauler=None,
        runner: ProcessRunner | None = None,
        producer_version: str | None = None,
        which=system_which,
        root: Path | None = None,
    ):
        self.runner = runner or ProcessRunner()
        self._archive_adapter = archive
        self.hauler = hauler or HaulerAdapter(self.runner)
        self.which = which
        self.root = root or Path(__file__).parents[2]
        self.producer_version = producer_version or _git_version(self.root)

    @property
    def archive(self):
        if self._archive_adapter is None:
            self._archive_adapter = ArchiveAdapter(self.runner)
        return self._archive_adapter

    def build(self, request: BuildRequest) -> OperationResult:
        log.info("Starting JAT build service")
        try:
            folder = existing_directory(request.folder)
            output = new_output_path(request.output)
            brew = existing_directory(request.brew) if request.brew else None
            if brew:
                _validate_brew_recovery(brew)
            images = self._select_images(request)
            with OwnedStage(output.parent, "build") as stage:
                workspace_archive = stage.path / WORKSPACE_ARTIFACT
                self.archive.create(folder, workspace_archive)
                brew_archive = None
                if brew:
                    brew_archive = stage.path / BREW_ARTIFACT
                    self.archive.create(brew, brew_archive)
                manifest = stage.path / "manifest.yaml"
                _write_manifest(manifest, workspace_archive, brew_archive, images)
                build_store = stage.path / "build-store"
                validation_store = stage.path / "validation-store"
                temp = stage.path / "hauler-temp"
                validation_temp = stage.path / "validation-temp"
                temp.mkdir()
                validation_temp.mkdir()
                self.hauler.sync(build_store, temp, manifest)
                staged = stage.path / output.name
                self.hauler.save(build_store, temp, staged)
                self.hauler.load(validation_store, validation_temp, staged)
                inventory = self.hauler.inventory(validation_store, validation_temp)
                _validate_inventory(inventory, brew is not None)
                os.link(staged, output)
            return OperationResult(
                operation="build",
                success=True,
                exit_status=0,
                payload_path=output,
                payload_size=output.stat().st_size,
                sha256=_sha256(output),
                producer_version=self.producer_version,
            )
        except (OSError, RuntimeError, ValueError) as error:
            log.warn(f"JAT build failed: {error}")
            return self._failure("build", error)

    def restore(self, request: RestoreRequest) -> OperationResult:
        log.info("Starting JAT restore service")
        try:
            haul = existing_file(request.haul)
            destination = empty_destination(request.destination, haul)
            with OwnedStage(destination.parent, "restore") as stage:
                store = stage.path / "store"
                temp = stage.path / "hauler-temp"
                extracted = stage.path / "extracted"
                assembled = stage.path / "restored"
                temp.mkdir()
                extracted.mkdir()
                assembled.mkdir()
                self.hauler.load(store, temp, haul)
                inventory = self.hauler.inventory(store, temp)
                _validate_inventory(inventory, BREW_REFERENCE in _inventory_references(inventory))
                self.hauler.extract(WORKSPACE_REFERENCE, store, temp, extracted)
                if BREW_REFERENCE in _inventory_references(inventory):
                    self.hauler.extract(BREW_REFERENCE, store, temp, extracted)
                workspace_archives = _find_regular_files(extracted, WORKSPACE_ARTIFACT)
                if len(workspace_archives) != 1:
                    raise ValueError(f"haul must contain exactly one workspace artifact named {WORKSPACE_ARTIFACT}")
                workspace_archive = workspace_archives[0]
                validate_archive_members(self.archive.members(workspace_archive))
                brew_archives = _find_regular_files(extracted, BREW_ARTIFACT)
                if len(brew_archives) > 1:
                    raise ValueError(f"haul must contain at most one Homebrew recovery artifact named {BREW_ARTIFACT}")
                workspace_destination = assembled / "workspace"
                workspace_destination.mkdir()
                self.archive.extract(workspace_archive, workspace_destination)
                if brew_archives:
                    brew_archive = brew_archives[0]
                    validate_archive_members(self.archive.members(brew_archive))
                    brew_destination = assembled / "homebrew-recovery"
                    brew_destination.mkdir()
                    self.archive.extract(brew_archive, brew_destination, strip_components=1)
                    _validate_brew_recovery(brew_destination)
                _promote_restore(assembled, destination)
            return OperationResult(
                operation="restore",
                success=True,
                exit_status=0,
                payload_path=destination,
                producer_version=self.producer_version,
            )
        except (OSError, RuntimeError, ValueError) as error:
            log.warn(f"JAT restore failed: {error}")
            return self._failure("restore", error)

    def serve(self, request: ServeRequest) -> OperationResult:
        try:
            haul = existing_file(request.haul)
            with OwnedStage(Path.cwd(), "serve") as stage:
                store = stage.path / "store"
                temp = stage.path / "hauler-temp"
                registry = stage.path / "registry"
                config = stage.path / "registry.yaml"
                temp.mkdir()
                registry.mkdir()
                config.write_text(_registry_config(registry))
                self.hauler.load(store, temp, haul)
                self.hauler.info(store, temp)
                self.hauler.serve(store, temp, registry, config)
            return OperationResult(
                operation="serve", success=True, exit_status=0, producer_version=self.producer_version
            )
        except (OSError, RuntimeError, ValueError) as error:
            return self._failure("serve", error)

    def doctor(self) -> OperationResult:
        missing = []
        if not self.which("hauler"):
            missing.append("hauler")
        try:
            _ = self.archive
        except RuntimeError:
            missing.append("GNU tar with --zstd")
        return OperationResult(
            operation="doctor",
            success=not missing,
            exit_status=0 if not missing else 1,
            producer_version=self.producer_version,
            diagnostics="" if not missing else f"missing prerequisites: {', '.join(missing)}",
        )

    def _select_images(self, request: BuildRequest) -> list[str]:
        docker = self.which("docker")
        if request.images:
            if not docker:
                raise ValueError("Docker is required when explicit images are selected")
            self._docker_ready(docker, required=True)
            selected = []
            for image in request.images:
                if any(character.isspace() for character in image):
                    raise ValueError(f"Docker image reference contains whitespace: {image}")
                inspected = self.runner.run([docker, "image", "inspect", image], timeout=60)
                if not inspected.success:
                    raise ValueError(f"local Docker image not found: {image}")
                selected.append(image)
            return selected
        if not request.all_images or not docker or not self._docker_ready(docker, required=False):
            return []
        listed = self.runner.run([docker, "image", "ls", "--format", "{{.Repository}}:{{.Tag}}"], timeout=60)
        if not listed.success:
            return []
        return [
            image
            for image in listed.stdout.splitlines()
            if image and not image.startswith("<none>:") and not image.endswith(":<none>")
        ]

    def _docker_ready(self, docker: str, required: bool) -> bool:
        ready = self.runner.run([docker, "info"], timeout=60).success
        if required and not ready:
            raise ValueError("Docker is not reachable; explicit images require a running local Docker daemon")
        return ready

    def _failure(self, operation: str, error: Exception) -> OperationResult:
        return OperationResult(
            operation=operation,
            success=False,
            exit_status=1,
            producer_version=self.producer_version,
            diagnostics=str(error),
        )


def _write_manifest(path: Path, workspace: Path, brew: Path | None, images: list[str]) -> None:
    documents = [
        "\n".join(
            [
                "apiVersion: content.hauler.cattle.io/v1",
                "kind: Files",
                "metadata:",
                "  name: joshs-all-the-things-workspace",
                "spec:",
                "  files:",
                f"    - path: {json.dumps(str(workspace))}",
                f"      name: {WORKSPACE_ARTIFACT}",
            ]
        )
    ]
    if brew:
        documents.append(
            "\n".join(
                [
                    "apiVersion: content.hauler.cattle.io/v1",
                    "kind: Files",
                    "metadata:",
                    "  name: joshs-all-the-things-homebrew-recovery",
                    "spec:",
                    "  files:",
                    f"    - path: {json.dumps(str(brew))}",
                    f"      name: {BREW_ARTIFACT}",
                ]
            )
        )
    if images:
        lines = [
            "apiVersion: content.hauler.cattle.io/v1",
            "kind: Images",
            "metadata:",
            "  name: joshs-all-the-things-images",
            "spec:",
            "  images:",
        ]
        for image in images:
            lines.extend((f"    - name: {json.dumps(image)}", "      local: true"))
        documents.append("\n".join(lines))
    path.write_text("\n---\n".join(documents) + "\n")


def _find_regular_files(root: Path, name: str) -> list[Path]:
    matches = []
    for directory, directories, files in os.walk(root, followlinks=False):
        directories[:] = [entry for entry in directories if not (Path(directory) / entry).is_symlink()]
        if name in files:
            candidate = Path(directory) / name
            if candidate.is_symlink() or not candidate.is_file():
                raise ValueError(f"haul contains an unsafe artifact path: {candidate}")
            matches.append(candidate)
    return matches


def _inventory_references(inventory: list[dict]) -> set[str]:
    return {item["Reference"] for item in inventory}


def _validate_inventory(inventory: list[dict], expect_brew: bool) -> None:
    references = _inventory_references(inventory)
    if WORKSPACE_REFERENCE not in references:
        raise ValueError("validated Hauler store is missing the workspace artifact")
    if expect_brew and BREW_REFERENCE not in references:
        raise ValueError("validated Hauler store is missing the Homebrew recovery artifact")


def _promote_restore(assembled: Path, destination: Path) -> None:
    removed_empty = False
    if destination.exists():
        if destination.is_symlink() or not destination.is_dir() or any(destination.iterdir()):
            raise ValueError(f"destination changed and is no longer known-empty: {destination}")
        destination.rmdir()
        removed_empty = True
    try:
        os.rename(assembled, destination)
    except BaseException:
        if removed_empty and not destination.exists():
            destination.mkdir()
        raise


def _validate_brew_recovery(directory: Path) -> None:
    brewfile = directory / "Brewfile"
    if brewfile.is_symlink() or not brewfile.is_file() or not os.access(brewfile, os.R_OK):
        raise ValueError("Homebrew recovery directory must contain a readable regular Brewfile")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_version(root: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    )
    return completed.stdout.strip() if completed.returncode == 0 else "unknown"


def _registry_config(registry: Path) -> str:
    return f'''version: 0.1
log:
  level: info
storage:
  filesystem:
    rootdirectory: {json.dumps(str(registry))}
  cache:
    blobdescriptor: inmemory
  maintenance:
    readonly:
      enabled: true
catalog:
  maxentries: 1000
http:
  addr: ":5000"
validation:
  manifests:
    urls:
      allow:
        - ".+"
'''
