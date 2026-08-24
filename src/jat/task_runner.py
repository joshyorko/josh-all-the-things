"""CLI spelling adapter for robocorp.tasks.

Robocorp exposes Python parameter names with underscores. JAT's public RCC
contract uses the conventional kebab-case spelling.
"""

import runpy
import sys

from .runtime import configure_runtime


def translate_args(argv: list[str]) -> list[str]:
    return ["--json_input" if argument == "--json-input" else argument for argument in argv]


def main() -> None:
    configure_runtime()
    sys.argv = ["robocorp.tasks", *translate_args(sys.argv[1:])]
    runpy.run_module("robocorp.tasks", run_name="__main__")


if __name__ == "__main__":
    main()
