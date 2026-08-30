#!/usr/bin/env bash
set -euo pipefail

robot_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
help_output=$(bash "$robot_root/joshs-all-the-things.sh" --help)
[[ $help_output == *'usage: jat'* ]]
[[ $help_output == *'{build,restore,inspect,extract,serve,export,copy,doctor}'* ]]
printf '%s\n' 'PASS: legacy Bash entrypoint delegates to the RCC-first jat CLI'
