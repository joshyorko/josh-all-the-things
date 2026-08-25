import json
import os
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BuildRequest(RequestModel):
    folder: Path
    output: Path
    brew: Path | None = None
    images: list[str] = Field(default_factory=list)
    all_images: bool = False
    rcc_environment: Literal["off", "auto", "required"] = "off"
    rcc_robot: Path | None = None

    @model_validator(mode="after")
    def image_modes_are_exclusive(self):
        if self.images and self.all_images:
            raise ValueError("images and all_images are mutually exclusive")
        return self


class RestoreRequest(RequestModel):
    haul: Path
    destination: Path


class ServeRequest(RequestModel):
    haul: Path


class EnvironmentArtifactMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    archive: Path
    rcc_version: str
    robot: Path
    provider: Literal["local"] = "local"
    acquired: bool = False


class OperationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format_version: Literal[1] = 1
    operation: Literal["build", "restore", "serve", "doctor", "inspect"]
    success: bool
    exit_status: int
    payload_path: Path | None = None
    payload_size: int | None = Field(default=None, ge=0)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    producer_version: str
    diagnostics: str = ""
    environment_artifact: EnvironmentArtifactMetadata | None = None

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
