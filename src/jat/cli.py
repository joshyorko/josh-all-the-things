"""Standalone human CLI around the shared JAT service."""

import argparse
import sys
from collections.abc import Callable, Sequence

from .models import BuildRequest, OperationResult, RestoreRequest, ServeRequest
from .runtime import configure_runtime
from .services import JATService


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="3tc", description="Build, restore, and serve portable workspace hauls.")
    subcommands = root.add_subparsers(dest="command")

    build = subcommands.add_parser("build", help="Build a portable workspace haul")
    build.add_argument("--folder", required=True)
    build.add_argument("--output", required=True)
    build.add_argument("--brew")
    images = build.add_mutually_exclusive_group()
    images.add_argument("--image", action="append", default=[], dest="images")
    images.add_argument("--all-images", action="store_true")
    build.add_argument("--json", action="store_true")

    restore = subcommands.add_parser("restore", help="Restore a portable workspace haul")
    restore.add_argument("--haul", required=True)
    restore.add_argument("--destination", required=True)
    restore.add_argument("--json", action="store_true")

    serve = subcommands.add_parser("serve", help="Serve images from a portable haul")
    serve.add_argument("--haul", required=True)
    serve.add_argument("--json", action="store_true")

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
    result = _invoke(service, parsed)
    _print_result(result, parsed.json)
    return result.exit_status


def _invoke(service: JATService, parsed: argparse.Namespace) -> OperationResult:
    if parsed.command == "build":
        return service.build(
            BuildRequest(
                folder=parsed.folder,
                output=parsed.output,
                brew=parsed.brew,
                images=parsed.images,
                all_images=parsed.all_images,
            )
        )
    if parsed.command == "restore":
        return service.restore(RestoreRequest(haul=parsed.haul, destination=parsed.destination))
    if parsed.command == "serve":
        return service.serve(ServeRequest(haul=parsed.haul))
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


def _print_result(result: OperationResult, as_json: bool) -> None:
    if as_json:
        print(result.model_dump_json(indent=2))
        return
    stream = sys.stdout if result.success else sys.stderr
    if result.success:
        message = f"{result.operation} completed"
        if result.payload_path:
            message += f": {result.payload_path}"
    else:
        message = result.diagnostics or f"{result.operation} failed"
    print(message, file=stream)


if __name__ == "__main__":
    raise SystemExit(main())
