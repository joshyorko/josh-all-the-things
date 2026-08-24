#!/usr/bin/env bash
set -euo pipefail

robot_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
output="$robot_root/dist/hololib.zip"
receipt="$robot_root/dist/hololib.json"

while (($#)); do
  case "$1" in
    --output) output=$(realpath -m -- "$2"); shift 2 ;;
    --receipt) receipt=$(realpath -m -- "$2"); shift 2 ;;
    *) printf 'Unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done

[[ ! -e $output ]] || { printf 'Output already exists: %s\n' "$output" >&2; exit 2; }
[[ ! -e $receipt ]] || { printf 'Receipt already exists: %s\n' "$receipt" >&2; exit 2; }
command -v dagger >/dev/null

dagger_module=${RCC_DAGGER_MODULE:-}
if [[ -z $dagger_module ]]; then
  for candidate in "${CODEX_HOME:-$HOME/.codex}"/plugins/cache/plugins/rcc/*/dagger; do
    [[ -f $candidate/dagger.json ]] && dagger_module=$candidate
  done
fi
[[ -n $dagger_module && -f $dagger_module/dagger.json ]] || {
  printf 'Set RCC_DAGGER_MODULE to the RCC Automation plugin dagger directory.\n' >&2
  exit 2
}

stage=$(mktemp -d)
trap 'rm -rf -- "$stage"' EXIT
jat_sha=$(git -C "$robot_root" rev-parse HEAD)
command="task script -r hololib.robot.yaml -- env JAT_HOLOLIB_DAGGER=1 python scripts/build_hololib.py --robot robot.yaml --ephemeral-shared-root --jat-git-sha $jat_sha --output dist/hololib.zip --receipt dist/hololib.json"

DAGGER_NO_NAG=1 dagger --progress=plain -m "$dagger_module" call rcc-with-output \
  --c "$command" \
  --source="$robot_root" \
  --output-path=dist \
  --rcc-version=v18.18.1 \
  export --path="$stage/result"

mkdir -p "$(dirname "$output")" "$(dirname "$receipt")"
cp --no-clobber "$stage/result/hololib.zip" "$output"
cp --no-clobber "$stage/result/hololib.json" "$receipt"
cat "$receipt"
