#!/usr/bin/env bash
set -euo pipefail

robot_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
exec rcc task script -r "$robot_root/robot.yaml" -- "$robot_root/jat" "$@"
