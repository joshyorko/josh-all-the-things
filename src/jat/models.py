import json
import os
import re
import tempfile
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

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
    retries: int | None = Field(default=None, ge=1)
    concurrency: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def image_modes_are_exclusive(self):
        if self.images and self.all_images:
            raise ValueError("images and all_images are mutually exclusive")
        return self

    @field_validator("chunk_size")
    @classmethod
    def chunk_size_has_hauler_shape(cls, value):
        """Hauler v2.1 accepts binary K/KB/M/MB/G/GB/T/TB units or byte counts."""
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
            if _REMOTE_URL_PATTERN.match(source):
                # Hauler logs the original source URL in its processing lines,
                # which JAT forwards to progress output and failure
                # diagnostics, so secrets must never travel inside a capture
                # source URL.
                parsed = urlparse(source)
                if parsed.username or parsed.password:
                    raise ValueError(
                        "remote capture sources must not embed credentials in their userinfo; "
                        "fetch through an anonymous URL or pre-stage the file locally"
                    )
                if parsed.query or parsed.fragment:
                    raise ValueError(
                        "remote capture sources must not contain query or fragment "
                        "components (signed URLs leak their signature through Hauler logs)"
                    )
            elif "://" in source:
                raise ValueError("remote capture sources must use HTTP(S)")
        return value


class RestoreRequest(RequestModel):
    haul: Path
    destination: Path

class ManifestRequest(RequestModel):
    output: Path
    images: list[str] = Field(default_factory=list)
    images_files: list[str] = Field(default_factory=list)
    platform: str | None = None
    registry_prefix: str | None = None
    publish_local: bool = False
    concurrency: int | None = Field(default=None, ge=1)
    retries: int | None = Field(default=None, ge=1)
    ca_file: Path | None = None
    insecure_skip_tls_verify: bool = False
    plain_http: bool = False
    check: bool = False

    @field_validator("images")
    @classmethod
    def image_refs_are_safe(cls, values):
        for value in values:
            if not value or value.strip() != value or any(char.isspace() for char in value):
                raise ValueError("image references must be non-empty and contain no whitespace")
            if "://" in value:
                raise ValueError("image references must not contain URL schemes")
            if "@" in value and not re.fullmatch(r".+@sha256:[0-9a-f]{64}", value):
                raise ValueError("digest-qualified image references must use a sha256 digest without credentials")
        return values

    @field_validator("images_files")
    @classmethod
    def image_sources_are_safe(cls, values):
        return BuildRequest.capture_sources_are_local_or_https(values)

    @field_validator("registry_prefix")
    @classmethod
    def registry_prefix_is_safe(cls, value):
        if value is None:
            return value
        if (
            not value
            or value.strip() != value
            or any(char.isspace() for char in value)
            or "://" in value
            or "@" in value
            or "?" in value
            or "#" in value
        ):
            raise ValueError("registry prefix must be registry-authority[/namespace/path], without credentials or URL components")
        parts = value.split("/")
        authority = parts[0]
        if not authority or ":" in authority and authority.endswith(":"):
            raise ValueError("registry prefix has an invalid authority")
        if any(not part or part in {".", ".."} for part in parts[1:]):
            raise ValueError("registry prefix must not contain empty or traversal path components")
        return value

    @model_validator(mode="after")
    def manifest_inputs_are_consistent(self):
        if not self.images and not self.images_files:
            raise ValueError("at least one --image or --images-file is required")
        if self.publish_local and not self.registry_prefix:
            raise ValueError("--publish-local requires --registry-prefix")
        if self.ca_file is not None and self.insecure_skip_tls_verify:
            raise ValueError("--ca-file and --insecure-skip-tls-verify are mutually exclusive")
        return self



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
    def target_uses_supported_scheme_without_credentials(cls, value):
        if "://" not in value or any(character.isspace() for character in value):
            raise ValueError("copy target must be a scheme-qualified Hauler target")
        scheme, _, remainder = value.partition("://")
        if scheme.lower() + "://" not in COPY_SCHEMES:
            raise ValueError(
                f"unsupported copy target scheme {scheme!r}; supported: {', '.join(COPY_SCHEMES)}"
            )
        # Credentials never travel in the target: JAT uses Hauler's normal
        # auth contract (hauler login / helpers), and the target is copied
        # verbatim into receipts and diagnostics.
        authority = remainder.split("/", 1)[0]
        if "@" in authority:
            raise ValueError(
                "copy target must not embed credentials in its userinfo; "
                "authenticate with hauler login or a credential helper"
            )
        if "?" in value or "#" in value:
            raise ValueError("copy target must not contain query or fragment components")
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

class ManifestMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_local: str
    published_remote: str
    digest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")


class ManifestReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    output: ArtifactOutput
    store_id: str | None = None
    registry_prefix: str | None = None
    tls_verification: Literal["default", "custom-ca", "disabled"] = "default"
    mappings: list[ManifestMapping] = Field(default_factory=list)
    concurrency: int | None = Field(default=None, ge=1)
    retries: int | None = Field(default=None, ge=1)
    integrity_checked: bool = False
    integrity_passed: bool | None = None


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
    operation: Literal["build", "restore", "serve", "doctor", "inspect", "extract", "export", "copy", "manifest"]
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
    manifest: ManifestReceipt | None = None
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
                self.manifest is not None,
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
