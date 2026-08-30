import json
import os
import re
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


ServeMode = Literal["auto", "files", "registry", "both"]

_CHUNK_SIZE_PATTERN = re.compile(r"^[1-9][0-9]*(?:[KMGT](?:B)?)?$", re.IGNORECASE)
_REMOTE_URL_PATTERN = re.compile(r"^https?://", re.IGNORECASE)


def is_remote_source(source: str) -> bool:
    """True when a capture input is an HTTP(S) source delegated to Hauler."""
    return bool(_REMOTE_URL_PATTERN.match(source))


class BuildRequest(RequestModel):
    folder: Path
    output: Path
    brew: Path | None = None
    images: list[str] = Field(default_factory=list)
    all_images: bool = False
    rcc_environment: Literal["off", "auto", "required"] = "off"
    rcc_robot: Path | None = None
    images_files: list[str] = Field(default_factory=list)
    hauler_manifests: list[str] = Field(default_factory=list)
    exclude_extras: bool = False
    chunk_size: str | None = None
    retries: int = Field(default=3, ge=1)

    @model_validator(mode="after")
    def image_modes_are_exclusive(self):
        if self.images and self.all_images:
            raise ValueError("images and all_images are mutually exclusive")
        return self

    @field_validator("chunk_size")
    @classmethod
    def chunk_size_has_hauler_shape(cls, value):
        """Only units pinned v2.0.3 parses: K/KB/M/MB/G/GB/T/TB or bare bytes.

        Hauler treats suffixes as binary multiples (1K = 1024) and rejects
        forms like 1B, 1Mi, or 1KiB; JAT rejects those before any capture work.
        """
        if value is None:
            return value
        if not _CHUNK_SIZE_PATTERN.fullmatch(value):
            raise ValueError(
                "chunk_size must be a positive byte count with an optional "
                "Hauler unit (K, KB, M, MB, G, GB, T, TB), e.g. 500M, 1G, or 500MB"
            )
        return value

    @field_validator("images_files", "hauler_manifests")
    @classmethod
    def capture_sources_are_local_or_https(cls, value):
        for source in value:
            if not source or source.strip() != source:
                raise ValueError("capture sources must be non-empty paths or HTTP(S) URLs")
            if "://" in source and not _REMOTE_URL_PATTERN.match(source):
                raise ValueError("remote capture sources must use HTTP(S)")
        return value


class RestoreRequest(RequestModel):
    haul: Path
    destination: Path


class ServeRequest(RequestModel):
    haul: Path
    mode: ServeMode = "auto"
    fileserver_port: int = Field(default=8080, ge=1, le=65535)
    registry_port: int = Field(default=5000, ge=1, le=65535)


class InspectRequest(RequestModel):
    haul: Path


class ExtractRequest(RequestModel):
    haul: Path
    reference: str
    destination: Path

    @field_validator("reference")
    @classmethod
    def reference_is_one_token(cls, value):
        if not value or any(character.isspace() for character in value):
            raise ValueError("reference must be a single non-empty Hauler reference")
        return value


class ExportRequest(RequestModel):
    haul: Path
    format: Literal["containerd"] = "containerd"
    output: Path


COPY_SCHEMES = ("registry://", "reg://", "oci://", "dir://", "directory://")


class CopyRequest(RequestModel):
    haul: Path
    to: str
    retries: int = Field(default=3, ge=1)
    plain_http: bool = False
    insecure: bool = False

    @field_validator("to")
    @classmethod
    def target_uses_supported_scheme(cls, value):
        if "://" not in value or any(character.isspace() for character in value):
            raise ValueError("copy target must be a scheme-qualified Hauler target")
        scheme = value.split("://", 1)[0].lower() + "://"
        if scheme not in COPY_SCHEMES:
            raise ValueError(f"unsupported copy target scheme {scheme!r}; supported: {', '.join(COPY_SCHEMES)}")
        return value


class EnvironmentArtifactMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    specification_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    legacy_blueprint_key: str = Field(min_length=1)
    archive: Path
    archive_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    archive_size: int = Field(ge=1)
    rcc_version: str
    robot: Path
    provider: Literal["local"] = "local"
    acquired: bool = False


class ArtifactOutput(BaseModel):
    """One produced output artifact with identity evidence."""

    model_config = ConfigDict(extra="forbid")

    path: Path
    size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ContentEntry(BaseModel):
    """Normalized Hauler inventory entry with bounded extra metadata."""

    model_config = ConfigDict(extra="forbid")

    reference: str
    type: str
    platform: str | None = None
    digest: str | None = None
    layers: int | None = None
    size: int | None = Field(default=None, ge=0)
    metadata: dict[str, str] = Field(default_factory=dict)

    @classmethod
    def from_hauler(cls, item: dict) -> "ContentEntry":
        """Normalize one raw Hauler JSON inventory entry, bounded and safe."""
        known = {"Reference", "Type", "Platform", "Digest", "Layers", "Size"}
        metadata = {}
        for key, value in item.items():
            if key not in known and value is not None:
                metadata[key] = value if isinstance(value, (str, int, float, bool)) else json.dumps(value)
        layers = item.get("Layers")
        size = item.get("Size")
        return cls(
            reference=item["Reference"],
            type=str(item.get("Type", "")),
            platform=item.get("Platform"),
            digest=item.get("Digest"),
            layers=int(layers) if isinstance(layers, int) else None,
            size=size if isinstance(size, int) and size >= 0 else None,
            metadata=metadata,
        )

    @field_validator("metadata")
    @classmethod
    def metadata_is_bounded(cls, value):
        bounded = {str(key)[:256]: str(item)[:512] for key, item in value.items()}
        return dict(list(bounded.items())[:32])


class ServeEndpoints(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: ServeMode
    fileserver_url: str | None = None
    registry_url: str | None = None
    fileserver_bind: Literal["all-interfaces", "loopback"] | None = None
    registry_bind: Literal["all-interfaces", "loopback"] | None = None


class TransferReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    destination: str
    transport: Literal["remote-registry", "local-directory"]
    requested_retries: int = Field(ge=1)
    effective_retries: int = Field(ge=1)


ANCHOR_KINDS = ("workspace", "brew", "rcc_environment", "rcc_metadata")


class OperationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format_version: Literal[1, 2] = 1
    operation: Literal["build", "restore", "serve", "doctor", "inspect", "extract", "export", "copy"]
    success: bool
    exit_status: int
    payload_path: Path | None = None
    payload_size: int | None = Field(default=None, ge=0)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    producer_version: str
    diagnostics: str = ""
    environment_artifact: EnvironmentArtifactMetadata | None = None
    payloads: list[ArtifactOutput] | None = None
    inventory: list[ContentEntry] | None = None
    anchors: dict[Literal["workspace", "brew", "rcc_environment", "rcc_metadata"], bool] | None = None
    serve: ServeEndpoints | None = None
    transfer: TransferReceipt | None = None
    complete: bool | None = None

    @model_validator(mode="after")
    def structured_details_require_format_version_two(self):
        structured = any(
            (
                self.payloads is not None,
                self.inventory is not None,
                self.anchors is not None,
                self.serve is not None,
                self.transfer is not None,
                self.complete is not None,
            )
        )
        if structured and self.format_version != 2:
            raise ValueError("structured result details require format_version 2")
        return self

    @field_validator("diagnostics", mode="before")
    @classmethod
    def bound_diagnostics(cls, value):
        return str(value or "")[:2048]

    def write(self, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
        temp = Path(temp_name)
        try:
            with os.fdopen(fd, "w") as handle:
                json.dump(self.model_dump(mode="json", exclude_none=True), handle, sort_keys=True, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, destination)
        finally:
            temp.unlink(missing_ok=True)
