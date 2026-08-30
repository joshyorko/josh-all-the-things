"""Standalone human CLI around the shared JAT service."""

import argparse
import sys
from collections.abc import Callable, Sequence

from pydantic import ValidationError

from .models import (
    BuildRequest,
    CopyRequest,
    ExportRequest,
    ExtractRequest,
    InspectRequest,
    OperationResult,
    RestoreRequest,
    ServeRequest,
)
from .runtime import configure_runtime
from .services import JATService

_OPERATIONS = frozenset(("build", "restore", "inspect", "extract", "serve", "export", "copy", "doctor"))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="jat", description="Capture, inspect, and project portable capability capsules."
    )
    subcommands = root.add_subparsers(dest="command")

    build = subcommands.add_parser("build", help="Build a portable capability capsule")
    build.add_argument("--folder", required=True)
    build.add_argument("--output", required=True)
    build.add_argument("--brew")
    images = build.add_mutually_exclusive_group()
    images.add_argument("--image", action="append", default=[], dest="images")
    images.add_argument("--all-images", action="store_true")
    build.add_argument(
        "--images-file",
        action="append",
        default=[],
        dest="images_files",
        help="Local or HTTP(S) images.txt consumed by Hauler's native --image-txt",
    )
    build.add_argument(
        "--hauler-manifest",
        action="append",
        default=[],
        dest="hauler_manifests",
        help="Advanced declarative composition: sync a Hauler Files/Images/Charts manifest",
    )
    build.add_argument(
        "--exclude-extras",
        action="store_true",
        help="Slim acquisition: exclude cosign signatures, attestations, SBOMs, and referrers",
    )
    build.add_argument("--chunk-size", help="Split the haul into chunks (e.g. 500M, 1G, 500MB)")
    build.add_argument(
        "--retries",
        type=int,
        default=3,
        help="Transfer reliability policy for retry-capable Hauler operations (minimum 1)",
    )
    build.add_argument("--rcc-environment", choices=("off", "auto", "required"), default="off")
    build.add_argument("--rcc-robot")
    build.add_argument("--json", action="store_true")

    restore = subcommands.add_parser("restore", help="Restore a workspace from a capsule")
    restore.add_argument("--haul", required=True)
    restore.add_argument("--destination", required=True)
    restore.add_argument("--json", action="store_true")

    inspect = subcommands.add_parser("inspect", help="List capsule content without restoring anything")
    inspect.add_argument("--haul", required=True)
    inspect.add_argument("--json", action="store_true")

    extract = subcommands.add_parser("extract", help="Extract one selected reference from a capsule")
    extract.add_argument("--haul", required=True)
    extract.add_argument("--reference", required=True)
    extract.add_argument("--destination", required=True)
    extract.add_argument("--json", action="store_true")

    serve = subcommands.add_parser("serve", help="Serve capsule content")
    serve.add_argument("--haul", required=True)
    serve.add_argument("--mode", choices=("auto", "files", "registry", "both"), default="auto")
    serve.add_argument("--fileserver-port", type=int, default=8080)
    serve.add_argument("--registry-port", type=int, default=5000)
    serve.add_argument("--json", action="store_true")

    export = subcommands.add_parser("export", help="Materialize capsule images for containerd")
    export.add_argument("--haul", required=True)
    export.add_argument("--format", choices=("containerd",), default="containerd")
    export.add_argument("--output", required=True)
    export.add_argument("--json", action="store_true")

    copy = subcommands.add_parser("copy", help="Seed an external Hauler target from a capsule")
    copy.add_argument("--haul", required=True)
    copy.add_argument("--to", required=True, help="registry://... or dir://... target")
    copy.add_argument("--retries", type=int, default=3, help="Per-artifact retry policy for registry pushes (minimum 1)")
    copy.add_argument("--plain-http", action="store_true")
    copy.add_argument("--insecure", action="store_true")
    copy.add_argument("--json", action="store_true")

    doctor = subcommands.add_parser("doctor", help="Check JAT runtime prerequisites")
    doctor.add_argument("--json", action="store_true")
    return root


def main(
    argv: Sequence[str] | None = None,
    *,
    service: JATService | None = None,
    input_fn: Callable[[str], str] = input,
) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        arguments = _interactive_arguments(input_fn)
    parsed = parser().parse_args(arguments)
    if service is None:
        configure_runtime()
        service = JATService()
        if not parsed.json:
            service.announce = print
    try:
        result = _invoke(service, parsed)
    except ValidationError as error:
        result = _invalid_request_result(service, parsed, error)
    _print_result(result, parsed.json)
    return result.exit_status


def _invalid_request_result(
    service: JATService, parsed: argparse.Namespace, error: ValidationError
) -> OperationResult:
    """Invalid options still produce the documented machine-readable result."""
    operation = parsed.command if parsed.command in _OPERATIONS else "doctor"
    return OperationResult(
        operation=operation,
        success=False,
        exit_status=1,
        producer_version=getattr(service, "producer_version", "unknown"),
        diagnostics=f"invalid request: {_format_validation_error(error)}",
    )


def _format_validation_error(error: ValidationError) -> str:
    """Render validation issues without the rejected input values.

    Pydantic's default str includes the full rejected value, which would copy
    credentials from a rejected target or URL straight into receipts and logs.
    """
    issues = []
    for issue in error.errors(include_url=False, include_context=False, include_input=False):
        location = ".".join(str(part) for part in issue.get("loc", ()) or ())
        issues.append(f"{location or '<request>'}: {issue.get('msg', 'invalid value')}")
    return "; ".join(issues)


def _invoke(service: JATService, parsed: argparse.Namespace) -> OperationResult:
    if parsed.command == "build":
        return service.build(
            BuildRequest(
                folder=parsed.folder,
                output=parsed.output,
                brew=parsed.brew,
                images=parsed.images,
                all_images=parsed.all_images,
                images_files=parsed.images_files,
                hauler_manifests=parsed.hauler_manifests,
                exclude_extras=parsed.exclude_extras,
                chunk_size=parsed.chunk_size,
                retries=parsed.retries,
                rcc_environment=parsed.rcc_environment,
                rcc_robot=parsed.rcc_robot,
            )
        )
    if parsed.command == "restore":
        return service.restore(RestoreRequest(haul=parsed.haul, destination=parsed.destination))
    if parsed.command == "inspect":
        return service.inspect(InspectRequest(haul=parsed.haul))
    if parsed.command == "extract":
        return service.extract(
            ExtractRequest(haul=parsed.haul, reference=parsed.reference, destination=parsed.destination)
        )
    if parsed.command == "serve":
        return service.serve(
            ServeRequest(
                haul=parsed.haul,
                mode=parsed.mode,
                fileserver_port=parsed.fileserver_port,
                registry_port=parsed.registry_port,
            )
        )
    if parsed.command == "export":
        return service.export(ExportRequest(haul=parsed.haul, format=parsed.format, output=parsed.output))
    if parsed.command == "copy":
        return service.copy(
            CopyRequest(
                haul=parsed.haul,
                to=parsed.to,
                retries=parsed.retries,
                plain_http=parsed.plain_http,
                insecure=parsed.insecure,
            )
        )
    if parsed.command == "doctor":
        return service.doctor()
    raise ValueError(f"unsupported command: {parsed.command}")


def _interactive_arguments(input_fn: Callable[[str], str]) -> list[str]:
    action = input_fn("Action (build, restore, serve): ").strip().lower()
    if action == "build":
        folder = input_fn("Folder to capture: ").strip()
        output = input_fn("Output haul: ").strip()
        brew = input_fn("Homebrew recovery directory (optional): ").strip()
        arguments = ["build", "--folder", folder, "--output", output]
        if brew:
            arguments.extend(("--brew", brew))
        return arguments
    if action == "restore":
        haul = input_fn("Haul to restore: ").strip()
        destination = input_fn("Empty destination directory: ").strip()
        return ["restore", "--haul", haul, "--destination", destination]
    if action == "serve":
        haul = input_fn("Haul to serve: ").strip()
        return ["serve", "--haul", haul]
    raise SystemExit(f"unknown action: {action}")


_V1_JSON_FIELDS = frozenset(
    (
        "format_version",
        "operation",
        "success",
        "exit_status",
        "payload_path",
        "payload_size",
        "sha256",
        "producer_version",
        "diagnostics",
        "environment_artifact",
    )
)


def _print_result(result: OperationResult, as_json: bool) -> None:
    if as_json:
        if result.format_version == 1:
            # Legacy receipts keep their exact v1 field set, optional fields
            # serialized as explicit nulls, with no v2-only keys.
            print(result.model_dump_json(indent=2, include=_V1_JSON_FIELDS))
        else:
            print(result.model_dump_json(indent=2, exclude_none=True))
        return
    stream = sys.stdout if result.success else sys.stderr
    if not result.success:
        print(result.diagnostics or f"{result.operation} failed", file=stream)
        return
    print(f"{result.operation} completed", file=stream)
    if result.payload_path:
        print(f"  payload: {result.payload_path}", file=stream)
    for output in result.payloads or []:
        print(f"  output: {output.path} ({output.size} bytes, sha256 {output.sha256[:16]}...)", file=stream)
    if result.inventory is not None:
        for entry in result.inventory:
            print(f"  {entry.type}: {entry.reference}", file=stream)
        present = [kind for kind, exists in (result.anchors or {}).items() if exists]
        print(f"  JAT anchors: {', '.join(present) if present else 'none'}", file=stream)
    if result.serve is not None:
        if result.serve.fileserver_url:
            print(f"  fileserver ({result.serve.fileserver_bind}): {result.serve.fileserver_url}", file=stream)
        if result.serve.registry_url:
            print(f"  registry ({result.serve.registry_bind}): {result.serve.registry_url}", file=stream)
    if result.transfer is not None:
        print(f"  transferred to: {result.transfer.destination} ({result.transfer.transport})", file=stream)


if __name__ == "__main__":
    raise SystemExit(main())
