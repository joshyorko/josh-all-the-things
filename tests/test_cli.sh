#!/usr/bin/env bash
set -euo pipefail

robot_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
help_output=$(bash "$robot_root/joshs-all-the-things.sh" --help)
[[ $help_output == *'usage: 3tc'* ]]
[[ $help_output == *'{build,restore,serve,doctor}'* ]]
printf '%s\n' 'PASS: legacy Bash entrypoint delegates to the RCC-first 3tc CLI'
