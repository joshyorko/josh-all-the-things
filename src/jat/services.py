"""Shared JAT build, restore, serve, and doctor service layer."""

import hashlib
import json
import os
import re
import subprocess
from contextlib import contextmanager
from pathlib import Path
from shutil import which as system_which
from urllib.parse import quote

from robocorp import log

from .archive import ArchiveAdapter
from .hauler import HaulerAdapter
from .models import (
    ANCHOR_KINDS,
    ArtifactOutput,
    BuildRequest,
    ContentEntry,
    CopyRequest,
    EnvironmentArtifactMetadata,
    ExportRequest,
    ExtractRequest,
    InspectRequest,
    OperationResult,
    RestoreRequest,
    ServeEndpoints,
    ServeRequest,
    TransferReceipt,
    is_remote_source,
)
from .process import ProcessRunner
from .rcc_artifacts import RCCArtifactAdapter
from .safety import (
    empty_destination,
    existing_directory,
    existing_file,
    new_output_directory,
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

COPY_REMOTE_SCHEMES = ("registry://", "reg://", "oci://")
COPY_LOCAL_SCHEMES = ("dir://", "directory://")


class _ProgressSink:
    """Forward bounded truthful Hauler transfer lines without a second engine."""

    def __init__(self, emit=None, line_limit: int = 2000):
        self.emit = emit
        self.line_limit = line_limit
        self.forwarded = 0

    def __call__(self, line: str) -> None:
        if not line or self.forwarded >= self.line_limit:
            return
        self.forwarded += 1
        if self.emit is not None:
            self.emit(line)
        else:
            log.info(f"hauler: {line}")


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
        announce=None,
        progress=None,
    ):
        self.runner = runner or ProcessRunner()
        self._archive_adapter = archive
        self.progress = _ProgressSink(progress)
        self.announce = announce
        self.hauler = hauler or HaulerAdapter(self.runner, progress=self.progress)
        self.rcc = rcc
        self.which = which
        self.root = root or Path(__file__).parents[2]
        self.producer_version = producer_version or _git_version(self.root)

    @property
    def archive(self):
        if self._archive_adapter is None:
            self._archive_adapter = ArchiveAdapter(self.runner)
        return self._archive_adapter

    @contextmanager
    def _loaded_capsule(self, haul: Path, stage_parent: Path, operation: str):
        """One owned temporary load of a haul; never a persistent store."""
        with OwnedStage(stage_parent, operation) as stage:
            store = stage.path / "store"
            temp = stage.path / "hauler-temp"
            temp.mkdir()
            self.hauler.load(store, temp, haul)
            inventory = self.hauler.inventory(store, temp)
            yield stage, store, temp, inventory

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
            images_files = _validate_capture_sources(request.images_files, "images-file")
            hauler_manifests = _validate_capture_sources(request.hauler_manifests, "hauler-manifest")
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
                retries = request.retries
                exclude_extras = request.exclude_extras
                if os.name == "nt" and hasattr(self.hauler, "sync_files"):
                    self.hauler.sync_files(
                        build_store, temp, artifact_files, images, retries=retries, exclude_extras=exclude_extras
                    )
                else:
                    self.hauler.sync(
                        build_store, temp, manifest, retries=retries, exclude_extras=exclude_extras
                    )
                # User Hauler manifests are passed exactly as provided: pinned
                # v2.0.3 resolves chart valuesFiles relative to the manifest
                # file, so JAT must not relocate or rewrite them.
                for source in hauler_manifests:
                    self.hauler.sync(build_store, temp, source, retries=retries, exclude_extras=exclude_extras)
                if images_files:
                    self.hauler.sync_image_txt(
                        build_store, temp, images_files, retries=retries, exclude_extras=exclude_extras
                    )
                staged = stage.path / output.name
                _require_chunkable_output_name(output.name, request.chunk_size)
                self.hauler.save(build_store, temp, staged, chunk_size=request.chunk_size)
                if request.chunk_size:
                    chunks = _chunk_files(staged)
                    if not chunks:
                        raise RuntimeError("chunked Hauler save produced no chunk files")
                    entrypoint = chunks[0]
                else:
                    chunks = [staged]
                    entrypoint = staged
                self.hauler.load(validation_store, validation_temp, entrypoint)
                inventory = self.hauler.inventory(validation_store, validation_temp)
                _validate_inventory(inventory, brew is not None, rcc_archive is not None)
                outputs = _promote_all_or_nothing(chunks, output.parent)
            if request.chunk_size:
                return OperationResult(
                    format_version=2,
                    operation="build",
                    success=True,
                    exit_status=0,
                    producer_version=self.producer_version,
                    payloads=outputs,
                    complete=True,
                    environment_artifact=rcc_metadata,
                )
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
            endpoints = None
            with self._loaded_capsule(haul, runtime_directory, "serve") as (stage, store, temp, inventory):
                files_only = _inventory_is_files_only(inventory)
                mode = request.mode
                if mode == "auto":
                    mode = "files" if files_only else "registry"
                registry = stage.path / "registry"
                files_directory = stage.path / "fileserver"
                registry.mkdir()
                files_directory.mkdir()
                config = stage.path / "registry.yaml"
                config.write_text(_registry_config(registry, request.registry_port))
                endpoints = ServeEndpoints(
                    mode=mode,
                    fileserver_url=f"http://127.0.0.1:{request.fileserver_port}" if mode in ("files", "both") else None,
                    registry_url=f"http://127.0.0.1:{request.registry_port}" if mode in ("registry", "both") else None,
                    fileserver_bind="all-interfaces" if mode in ("files", "both") else None,
                    registry_bind="loopback" if mode in ("registry", "both") else None,
                )
                live = []
                if endpoints.fileserver_url:
                    live.append(f"fileserver ({endpoints.fileserver_bind}) at {endpoints.fileserver_url}")
                if endpoints.registry_url:
                    live.append(f"registry ({endpoints.registry_bind}) at {endpoints.registry_url}")
                self._announce(f"serving capsule [{haul.name}] mode [{mode}]: " + "; ".join(live))
                if mode == "files":
                    self.hauler.serve_files(store, temp, files_directory, port=request.fileserver_port)
                elif mode == "registry":
                    self.hauler.serve(store, temp, registry, config)
                elif mode == "both":
                    # One loaded capsule backs both read-only servers; the real
                    # vertical test proves concurrent read access on v2.0.3.
                    completed = self.runner.supervise(
                        [
                            self.hauler.serve_fileserver_command(
                                store, temp, files_directory, request.fileserver_port
                            ),
                            self.hauler.serve_registry_command(store, temp, registry, config),
                        ]
                    )
                    if not completed.success:
                        raise RuntimeError(completed.diagnostics or "a served endpoint exited unexpectedly")
                else:
                    raise ValueError(f"unsupported serve mode: {mode}")
            return OperationResult(
                format_version=2,
                operation="serve",
                success=True,
                exit_status=0,
                producer_version=self.producer_version,
                serve=endpoints,
                complete=True,
            )
        except (OSError, RuntimeError, ValueError) as error:
            return self._failure("serve", error)

    def inspect(self, request: InspectRequest) -> OperationResult:
        try:
            haul = existing_file(request.haul)
            with self._loaded_capsule(haul, _operation_runtime_directory(), "inspect") as (stage, store, temp, inventory):
                entries = [_normalize_inventory_entry(item) for item in inventory]
                anchors = _identify_anchors(inventory)
            return OperationResult(
                format_version=2,
                operation="inspect",
                success=True,
                exit_status=0,
                producer_version=self.producer_version,
                inventory=entries,
                anchors=anchors,
                complete=True,
            )
        except (OSError, RuntimeError, ValueError) as error:
            return self._failure("inspect", error)

    def extract(self, request: ExtractRequest) -> OperationResult:
        try:
            haul = existing_file(request.haul)
            reference = request.reference
            destination = new_output_directory(request.destination)
            with self._loaded_capsule(haul, destination.parent, "extract") as (stage, store, temp, inventory):
                references = _inventory_references(inventory)
                if reference not in references:
                    known = ", ".join(sorted(references)[:20])
                    raise ValueError(
                        f"haul does not contain reference {reference!r}; known references: {known}"
                    )
                extracted = stage.path / "extracted"
                extracted.mkdir()
                self.hauler.extract(reference, store, temp, extracted)
                produced = _relative_regular_files(extracted)
                if not produced:
                    raise ValueError(f"reference {reference!r} produced no regular files")
                _promote_restore(extracted, destination)
            outputs = []
            for relative in produced:
                promoted = destination / relative
                outputs.append(
                    ArtifactOutput(path=promoted, size=promoted.stat().st_size, sha256=_sha256(promoted))
                )
            return OperationResult(
                format_version=2,
                operation="extract",
                success=True,
                exit_status=0,
                payload_path=destination,
                producer_version=self.producer_version,
                payloads=outputs,
                complete=True,
            )
        except (OSError, RuntimeError, ValueError) as error:
            return self._failure("extract", error)

    def export(self, request: ExportRequest) -> OperationResult:
        try:
            haul = existing_file(request.haul)
            output = new_output_path(request.output)
            with self._loaded_capsule(haul, output.parent, "export") as (stage, store, temp, inventory):
                staged = stage.path / output.name
                # The adapter rejects any chunk-size/--containerd combination.
                self.hauler.save(store, temp, staged, containerd=True)
                os.link(staged, output)
            return OperationResult(
                format_version=2,
                operation="export",
                success=True,
                exit_status=0,
                payload_path=output,
                payload_size=output.stat().st_size,
                sha256=_sha256(output),
                producer_version=self.producer_version,
                payloads=[
                    ArtifactOutput(path=output, size=output.stat().st_size, sha256=_sha256(output))
                ],
                complete=True,
            )
        except (OSError, RuntimeError, ValueError) as error:
            return self._failure("export", error)

    def copy(self, request: CopyRequest) -> OperationResult:
        try:
            haul = existing_file(request.haul)
            target = request.to
            scheme = target.split("://", 1)[0].lower() + "://"
            if scheme in COPY_REMOTE_SCHEMES:
                transport = "remote-registry"
            elif scheme in COPY_LOCAL_SCHEMES:
                transport = "local-directory"
            else:
                supported = ", ".join((*COPY_REMOTE_SCHEMES, *COPY_LOCAL_SCHEMES))
                raise ValueError(f"unsupported copy target scheme {scheme!r}; supported: {supported}")
            with self._loaded_capsule(haul, _operation_runtime_directory(), "copy") as (stage, store, temp, inventory):
                self.hauler.copy(
                    store,
                    temp,
                    target,
                    retries=request.retries,
                    plain_http=request.plain_http,
                    insecure=request.insecure,
                )
            return OperationResult(
                format_version=2,
                operation="copy",
                success=True,
                exit_status=0,
                producer_version=self.producer_version,
                transfer=TransferReceipt(
                    destination=target,
                    transport=transport,
                    requested_retries=request.retries,
                    effective_retries=request.retries,
                ),
                complete=True,
            )
        except (OSError, RuntimeError, ValueError) as error:
            return self._failure("copy", error)

    def _announce(self, message: str) -> None:
        if self.announce is not None:
            self.announce(message)

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
    identities = set()
    for item in inventory:
        # Multi-platform images legitimately repeat a reference per platform
        # variant (including referrer attestations), so uniqueness is per
        # (reference, platform, digest) identity.
        identity = (item["Reference"], item.get("Platform"), item.get("Digest"))
        if identity in identities:
            raise ValueError("validated Hauler store contains duplicate artifact references")
        identities.add(identity)
    references = _inventory_references(inventory)
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


def _registry_config(registry: Path, port: int = 5000) -> str:
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
  addr: "127.0.0.1:{port}"
validation:
  manifests:
    urls:
      allow:
        - ".+"
'''


def _validate_capture_sources(sources: list[str], label: str) -> list[str]:
    """Local capture sources must exist; HTTP(S) sources stay Hauler-owned."""
    for source in sources:
        if is_remote_source(source):
            continue
        existing_file(Path(source))
    return list(sources)


def _require_chunkable_output_name(name: str, chunk_size: str | None) -> None:
    """Restrict chunked output names to archives pinned v2.0.3 can reload.

    Hauler derives chunk names by stripping every extension of the output
    filename, but its unarchiver can only load tar/tar.zst containers (a zip
    chunk set fails on load with an io.ReaderAt/io.Seeker constraint). JAT
    rejects any other output name before capture instead of producing a haul
    no consumer operation can open.
    """
    if chunk_size is None:
        return
    lowered = name.lower()
    if not lowered.endswith((".tar", ".tar.zst")):
        raise ValueError(
            f"chunked output must be a .tar or .tar.zst archive name "
            f"(Hauler v2.0.3 cannot reload other chunk containers): {name!r}"
        )


def _chunk_files(staged: Path) -> list[Path]:
    """Observed Hauler v2.0.3 chunk naming: <base>_<index><ext>, from 0.

    Hauler derives <ext> by stripping every extension of the requested output
    filename (capsule.zip -> capsule_0.zip; haul.tar.zst -> haul_0.tar.zst),
    so the matcher mirrors that derivation for any accepted output name.
    """
    name = staged.name
    if "." in name:
        base, ext = name.split(".", 1)
        ext = f".{ext}"
    else:
        base, ext = name, ""
    if not base:
        return []
    pattern = re.compile(rf"^{re.escape(base)}_(?P<index>[0-9]+){re.escape(ext)}$")
    candidates = []
    for candidate in staged.parent.iterdir():
        chunk_match = pattern.match(candidate.name)
        if chunk_match is not None and candidate.is_file():
            candidates.append((int(chunk_match.group("index")), candidate))
    return [candidate for _, candidate in sorted(candidates)]


def _promote_all_or_nothing(chunks: list[Path], destination_directory: Path) -> list[ArtifactOutput]:
    """Link every chunk to its public name, or leave nothing behind.

    Every sibling name is reserved create-only before promotion; if any link
    fails (pre-existing or racing target), the links created so far are rolled
    back so a failed build never leaves a misleading partial set.
    """
    promoted: list[Path] = []
    try:
        for chunk in chunks:
            target = new_output_path(destination_directory / chunk.name)
            os.link(chunk, target)
            promoted.append(target)
    except BaseException:
        for target in promoted:
            target.unlink(missing_ok=True)
        raise
    return [
        ArtifactOutput(path=target, size=target.stat().st_size, sha256=_sha256(target))
        for target in promoted
    ]


def _normalize_inventory_entry(item: dict) -> ContentEntry:
    return ContentEntry.from_hauler(item)


def _identify_anchors(inventory: list[dict]) -> dict[str, bool]:
    references = _inventory_references(inventory)
    return {
        "workspace": WORKSPACE_REFERENCE in references,
        "brew": BREW_REFERENCE in references,
        "rcc_environment": RCC_REFERENCE in references and RCC_METADATA_REFERENCE in references,
        "rcc_metadata": RCC_REFERENCE in references and RCC_METADATA_REFERENCE in references,
    }


def _relative_regular_files(root: Path) -> list[Path]:
    relatives = []
    for directory, directories, files in os.walk(root, followlinks=False):
        directories[:] = [entry for entry in directories if not (Path(directory) / entry).is_symlink()]
        for name in files:
            candidate = Path(directory) / name
            if candidate.is_symlink() or not candidate.is_file():
                raise ValueError(f"extracted content contains an unsafe path: {candidate}")
            relatives.append(candidate.relative_to(root))
    return sorted(relatives)


def _operation_runtime_directory() -> Path:
    runtime_directory = Path(os.environ.get("JAT_RUN_DIR") or Path.cwd()).expanduser().resolve()
    conda_prefix = os.environ.get("CONDA_PREFIX")
    if conda_prefix:
        environment_directory = Path(conda_prefix).expanduser().resolve()
        try:
            runtime_directory.relative_to(environment_directory)
        except ValueError:
            pass
        else:
            raise ValueError("JAT runtime directory must be outside the acquired environment")
    runtime_directory.mkdir(parents=True, exist_ok=True)
    return runtime_directory
