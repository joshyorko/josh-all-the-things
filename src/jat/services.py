"""Shared JAT build, restore, serve, and doctor service layer."""

import hashlib
import json
import os
import subprocess
from pathlib import Path
from shutil import which as system_which
from urllib.parse import quote

from robocorp import log

from .archive import ArchiveAdapter
from .hauler import HaulerAdapter
from .models import (
    BuildRequest,
    EnvironmentArtifactMetadata,
    OperationResult,
    RestoreRequest,
    ServeRequest,
)
from .process import ProcessRunner
from .rcc_artifacts import RCCArtifactAdapter
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
RCC_ARTIFACT = "rcc-environment.rcca"
RCC_REFERENCE = "hauler/rcc-environment.rcca:latest"
RCC_METADATA_ARTIFACT = "rcc-environment-metadata.json"
RCC_METADATA_REFERENCE = "hauler/rcc-environment-metadata.json:latest"


class JATService:
    def __init__(
        self,
        archive=None,
        hauler=None,
        rcc=None,
        runner: ProcessRunner | None = None,
        producer_version: str | None = None,
        which=system_which,
        root: Path | None = None,
    ):
        self.runner = runner or ProcessRunner()
        self._archive_adapter = archive
        self.hauler = hauler or HaulerAdapter(self.runner)
        self.rcc = rcc
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
            _validate_robocorp_home(folder)
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
                rcc_archive = None
                rcc_metadata = None
                rcc_metadata_path = None
                rcc_robot = self._select_rcc_robot(request, folder) if request.rcc_environment != "off" else None
                if request.rcc_environment == "required" and rcc_robot is None:
                    raise ValueError("RCC environment is required but no regular robot.yaml was found")
                if rcc_robot is not None and request.rcc_environment != "off":
                    if self.rcc is not None or self.which("rcc"):
                        rcc_archive = stage.path / RCC_ARTIFACT
                        rcc_metadata = self._rcc_adapter().publish_and_export(folder, rcc_archive, rcc_robot)
                        rcc_metadata = rcc_metadata.model_copy(
                            update={"archive": Path(RCC_ARTIFACT), "robot": rcc_robot.relative_to(folder)}
                        )
                        rcc_metadata_path = stage.path / RCC_METADATA_ARTIFACT
                        rcc_metadata_path.write_text(json.dumps(rcc_metadata.model_dump(mode="json"), indent=2, sort_keys=True) + "\n")
                    elif request.rcc_environment == "required":
                        raise ValueError("RCC environment is required but rcc is unavailable")
                manifest = stage.path / "manifest.yaml"
                _write_manifest(manifest, workspace_archive, brew_archive, rcc_archive, rcc_metadata_path, images)
                build_store = stage.path / "build-store"
                validation_store = stage.path / "validation-store"
                temp = stage.path / "hauler-temp"
                validation_temp = stage.path / "validation-temp"
                temp.mkdir()
                validation_temp.mkdir()
                artifact_files = [(workspace_archive, WORKSPACE_ARTIFACT)]
                if brew_archive:
                    artifact_files.append((brew_archive, BREW_ARTIFACT))
                if rcc_archive:
                    artifact_files.append((rcc_archive, RCC_ARTIFACT))
                if rcc_metadata_path:
                    artifact_files.append((rcc_metadata_path, RCC_METADATA_ARTIFACT))
                if os.name == "nt" and hasattr(self.hauler, "sync_files"):
                    self.hauler.sync_files(build_store, temp, artifact_files, images)
                else:
                    self.hauler.sync(build_store, temp, manifest)
                staged = stage.path / output.name
                self.hauler.save(build_store, temp, staged)
                self.hauler.load(validation_store, validation_temp, staged)
                inventory = self.hauler.inventory(validation_store, validation_temp)
                _validate_inventory(inventory, brew is not None, rcc_archive is not None)
                os.link(staged, output)
            return OperationResult(
                operation="build",
                success=True,
                exit_status=0,
                payload_path=output,
                payload_size=output.stat().st_size,
                sha256=_sha256(output),
                producer_version=self.producer_version,
                environment_artifact=rcc_metadata,
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
                references = _inventory_references(inventory)
                has_rcc = RCC_REFERENCE in references and RCC_METADATA_REFERENCE in references
                _validate_inventory(inventory, BREW_REFERENCE in references, has_rcc)
                self.hauler.extract(WORKSPACE_REFERENCE, store, temp, extracted)
                if BREW_REFERENCE in _inventory_references(inventory):
                    self.hauler.extract(BREW_REFERENCE, store, temp, extracted)
                if has_rcc:
                    self.hauler.extract(RCC_REFERENCE, store, temp, extracted)
                    self.hauler.extract(RCC_METADATA_REFERENCE, store, temp, extracted)
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
                environment_metadata = None
                rcc_archives = _find_regular_files(extracted, RCC_ARTIFACT)
                metadata_files = _find_regular_files(extracted, RCC_METADATA_ARTIFACT)
                if has_rcc:
                    if len(rcc_archives) != 1:
                        raise ValueError(f"haul must contain exactly one RCC environment artifact named {RCC_ARTIFACT}")
                    if len(metadata_files) != 1:
                        raise ValueError(f"haul must contain exactly one RCC metadata artifact named {RCC_METADATA_ARTIFACT}")
                    expected = EnvironmentArtifactMetadata.model_validate_json(metadata_files[0].read_text())
                    if expected.archive_size != rcc_archives[0].stat().st_size or expected.archive_sha256 != _sha256(rcc_archives[0]):
                        raise ValueError("RCC environment metadata does not match the embedded archive")
                    robot_file = _saved_robot_path(workspace_destination, expected.robot)
                    environment_metadata = self._rcc_adapter().acquire(
                        rcc_archives[0],
                        robot_file,
                        expected.rcc_version,
                        expected.specification_digest,
                        expected.legacy_blueprint_key,
                    )
                    if environment_metadata.artifact != expected.artifact:
                        raise ValueError("acquired RCC environment artifact digest did not match metadata")
                    self._rcc_adapter().verify(robot_file)
                    environment_metadata = environment_metadata.model_copy(
                        update={"archive": Path(RCC_ARTIFACT), "robot": expected.robot}
                    )
                _promote_restore(assembled, destination)
            return OperationResult(
                operation="restore",
                success=True,
                exit_status=0,
                payload_path=destination,
                producer_version=self.producer_version,
                environment_artifact=environment_metadata,
            )
        except (OSError, RuntimeError, ValueError) as error:
            log.warn(f"JAT restore failed: {error}")
            return self._failure("restore", error)

    def serve(self, request: ServeRequest) -> OperationResult:
        try:
            haul = existing_file(request.haul)
            runtime_directory = _serve_runtime_directory()
            with OwnedStage(runtime_directory, "serve") as stage:
                store = stage.path / "store"
                temp = stage.path / "hauler-temp"
                registry = stage.path / "registry"
                config = stage.path / "registry.yaml"
                temp.mkdir()
                registry.mkdir()
                config.write_text(_registry_config(registry))
                self.hauler.load(store, temp, haul)
                inventory = self.hauler.inventory(store, temp)
                if _inventory_is_files_only(inventory):
                    self.hauler.serve_files(store, temp, registry, port=8080)
                else:
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
            missing.append("environment-owned tar+zstd archive backend")
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

    def _rcc_adapter(self):
        if self.rcc is None:
            executable = self.which("rcc")
            if not executable:
                raise ValueError("RCC is required for environment artifacts")
            self.rcc = RCCArtifactAdapter(self.runner, executable=executable)
        return self.rcc

    @staticmethod
    def _select_rcc_robot(request: BuildRequest, folder: Path) -> Path | None:
        source = folder.resolve(strict=True)
        candidate = request.rcc_robot or folder / "robot.yaml"
        if not candidate.is_absolute():
            candidate = folder / candidate
        if candidate.is_symlink():
            return None
        if not candidate.exists():
            return None
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(source)
        except (OSError, ValueError):
            raise ValueError("RCC robot descriptor must be under the workspace source")
        if not resolved.is_file():
            return None
        return resolved

    def _failure(self, operation: str, error: Exception) -> OperationResult:
        return OperationResult(
            operation=operation,
            success=False,
            exit_status=1,
            producer_version=self.producer_version,
            diagnostics=str(error),
        )


def _serve_runtime_directory() -> Path:
    runtime_directory = Path(os.environ.get("JAT_RUN_DIR") or Path.cwd()).expanduser().resolve()
    conda_prefix = os.environ.get("CONDA_PREFIX")
    if conda_prefix:
        environment_directory = Path(conda_prefix).expanduser().resolve()
        try:
            runtime_directory.relative_to(environment_directory)
        except ValueError:
            pass
        else:
            raise ValueError("JAT Serve runtime directory must be outside the acquired environment")
    runtime_directory.mkdir(parents=True, exist_ok=True)
    return runtime_directory


def _write_manifest(
    path: Path, workspace: Path, brew: Path | None, rcc_archive: Path | None, rcc_metadata: Path | None, images: list[str]
) -> None:
    documents = [
        "\n".join(
            [
                "apiVersion: content.hauler.cattle.io/v1",
                "kind: Files",
                "metadata:",
                "  name: joshs-all-the-things-workspace",
                "spec:",
                "  files:",
                f"    - path: {json.dumps(_local_file_reference(workspace))}",
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
                    f"    - path: {json.dumps(_local_file_reference(brew))}",
                    f"      name: {BREW_ARTIFACT}",
                ]
            )
        )
    if rcc_archive:
        documents.append(
            "\n".join(
                [
                    "apiVersion: content.hauler.cattle.io/v1",
                    "kind: Files",
                    "metadata:",
                    "  name: joshs-all-the-things-rcc-environment",
                    "spec:",
                    "  files:",
                    f"    - path: {json.dumps(_local_file_reference(rcc_archive))}",
                    f"      name: {RCC_ARTIFACT}",
                ]
            )
        )
    if rcc_metadata:
        documents.append(
            "\n".join(
                [
                    "apiVersion: content.hauler.cattle.io/v1",
                    "kind: Files",
                    "metadata:",
                    "  name: joshs-all-the-things-rcc-environment-metadata",
                    "spec:",
                    "  files:",
                    f"    - path: {json.dumps(_local_file_reference(rcc_metadata))}",
                    f"      name: {RCC_METADATA_ARTIFACT}",
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


def _local_file_reference(path: Path, windows: bool | None = None) -> str:
    """Render a local path in the URI form Hauler's file getter accepts."""
    if windows is None:
        windows = os.name == "nt"
    if not windows:
        return path.resolve().as_uri()
    value = path.as_posix()
    if value.startswith("//"):
        raise ValueError("UNC paths are not supported for Hauler local file sources")
    if len(value) >= 3 and value[1:3] == ":/":
        drive = quote(value[:2], safe=":")
        remainder = quote(value[2:], safe="/~")
        return f"file://{drive}{remainder}"
    return Path(value).resolve().as_uri()


def _validate_robocorp_home(source: Path) -> None:
    configured = os.environ.get("ROBOCORP_HOME")
    if not configured:
        return
    active_home = Path(configured).expanduser().resolve(strict=False)
    try:
        active_home.relative_to(source)
    except ValueError:
        return
    raise ValueError("ROBOCORP_HOME must not be equal to or beneath the workspace source")


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


def _saved_robot_path(workspace: Path, relative: Path) -> Path:
    if relative.is_absolute() or not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError("saved robot path must be a relative path within the restored workspace")
    roots = [entry for entry in workspace.iterdir() if entry.is_dir() and not entry.is_symlink()]
    if len(roots) != 1:
        raise ValueError("restored workspace must contain exactly one regular root directory")
    root = roots[0]
    candidate = root.joinpath(*relative.parts)
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("saved robot path contains a symlink")
    try:
        candidate.resolve(strict=True).relative_to(workspace.resolve(strict=True))
    except (OSError, ValueError):
        raise ValueError("saved robot path escapes the restored workspace")
    if not candidate.is_file():
        raise ValueError("saved robot path is absent or not a regular file")
    return candidate


def _inventory_references(inventory: list[dict]) -> set[str]:
    return {item["Reference"] for item in inventory}


def _inventory_is_files_only(inventory: list[dict]) -> bool:
    return bool(inventory) and all(str(item.get("Type", "")).lower() == "file" for item in inventory)


def _validate_inventory(inventory: list[dict], expect_brew: bool, expect_rcc: bool = False) -> None:
    references = _inventory_references(inventory)
    if len(references) != len(inventory):
        raise ValueError("validated Hauler store contains duplicate artifact references")
    unexpected_rcc = [
        reference for reference in references if reference.endswith(".rcca:latest") and reference != RCC_REFERENCE
    ]
    if unexpected_rcc:
        raise ValueError(f"validated Hauler store contains unexpected RCC artifacts: {', '.join(sorted(unexpected_rcc))}")
    if (RCC_REFERENCE in references) != (RCC_METADATA_REFERENCE in references):
        raise ValueError("RCC environment artifact and metadata must appear together")
    if WORKSPACE_REFERENCE not in references:
        raise ValueError("validated Hauler store is missing the workspace artifact")
    if expect_brew and BREW_REFERENCE not in references:
        raise ValueError("validated Hauler store is missing the Homebrew recovery artifact")
    if expect_rcc and RCC_REFERENCE not in references:
        raise ValueError("validated Hauler store is missing the RCC environment artifact")
    if expect_rcc and RCC_METADATA_REFERENCE not in references:
        raise ValueError("validated Hauler store is missing the RCC environment metadata artifact")


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
    pinned = os.environ.get("JAT_GIT_SHA") or os.environ.get("JOSH_ROOM_JAT_SHA")
    if pinned:
        return pinned
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", check=False
        )
    except OSError:
        return "unknown"
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
  addr: "127.0.0.1:5000"
validation:
  manifests:
    urls:
      allow:
        - ".+"
'''
