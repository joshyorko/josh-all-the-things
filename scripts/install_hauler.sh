#!/usr/bin/env bash
set -euo pipefail

readonly SCRIPT_DIRECTORY="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly MANIFEST_PATH="${HAULER_MANIFEST:-$SCRIPT_DIRECTORY/../runtime/hauler.json}"

[[ -n ${CONDA_PREFIX:-} ]] || {
  printf 'CONDA_PREFIX is required for the JAT Hauler installation.\n' >&2
  exit 2
}
[[ -f $MANIFEST_PATH && ! -L $MANIFEST_PATH ]] || {
  printf 'JAT Hauler manifest is unavailable: %s\n' "$MANIFEST_PATH" >&2
  exit 2
}

python_executable="$CONDA_PREFIX/bin/python"
if [[ ! -x $python_executable ]]; then
  python_executable=$(command -v python3 || command -v python || true)
fi
[[ -n $python_executable && -x $python_executable ]] || {
  printf 'An environment-owned Python executable is required to read the Hauler manifest.\n' >&2
  exit 2
}

manifest_line=$(
  "$python_executable" - "$MANIFEST_PATH" <<'PY'
import json
import re
import sys

path = sys.argv[1]
with open(path, encoding="utf-8") as stream:
    document = json.load(stream)
if document.get("schema_version") != 1 or not isinstance(document.get("hauler"), dict):
    raise SystemExit("invalid JAT Hauler manifest schema")
hauler = document["hauler"]
fields = ("version", "platform", "asset", "url", "sha256")
if any(not isinstance(hauler.get(field), str) or not hauler[field] for field in fields):
    raise SystemExit("JAT Hauler manifest is incomplete")
if hauler["platform"] != "linux-amd64":
    raise SystemExit(f"unsupported JAT Hauler platform: {hauler['platform']}")
if not re.fullmatch(r"v\d+\.\d+\.\d+", hauler["version"]):
    raise SystemExit("JAT Hauler version is not an exact release")
if not re.fullmatch(r"[0-9a-f]{64}", hauler["sha256"]):
    raise SystemExit("JAT Hauler SHA256 is invalid")
if not hauler["url"].startswith("https://github.com/hauler-dev/hauler/releases/download/"):
    raise SystemExit("JAT Hauler URL is not the official upstream release host")
print("\t".join(hauler[field] for field in fields))
PY
)
IFS=$'\t' read -r version platform asset url expected_sha256 <<< "$manifest_line"

case "$(uname -s):$(uname -m)" in
  Linux:x86_64|Linux:amd64) ;;
  *)
    printf 'JAT Hauler manifest is only supported on linux-amd64: %s:%s\n' "$(uname -s)" "$(uname -m)" >&2
    exit 2
    ;;
esac

bin_directory="$CONDA_PREFIX/bin"
target="$bin_directory/hauler"
mkdir -p -- "$bin_directory"

if [[ -e $target || -L $target ]]; then
  [[ -f $target && ! -L $target && -x $target ]] || {
    printf 'Existing Hauler target is not a regular executable: %s\n' "$target" >&2
    exit 1
  }
  existing_version=$("$target" version 2>&1 || true)
  [[ $existing_version == *"$version"* ]] || {
    printf 'Existing Hauler does not match pinned version %s: %s\n' "$version" "$target" >&2
    exit 1
  }
  printf 'Using verified Hauler %s at %s\n' "$version" "$target"
  exit 0
fi

stage_directory=$(mktemp -d "$CONDA_PREFIX/.hauler-install.XXXXXX")
cleanup() {
  rm -rf -- "$stage_directory"
}
trap cleanup EXIT

archive="$stage_directory/$asset"
curl --fail --location --silent --show-error --proto '=https' --tlsv1.2 --output "$archive" "$url"
observed_sha256=$(sha256sum "$archive" | cut -d ' ' -f 1)
[[ $observed_sha256 == "$expected_sha256" ]] || {
  printf 'Hauler archive SHA256 mismatch: expected %s, got %s\n' "$expected_sha256" "$observed_sha256" >&2
  exit 1
}

extract_directory="$stage_directory/extract"
mkdir -- "$extract_directory"
tar --extract --gzip --file "$archive" --directory "$extract_directory" --no-same-owner --no-same-permissions
candidate="$extract_directory/hauler"
[[ -f $candidate && ! -L $candidate && -x $candidate ]] || {
  printf 'Pinned Hauler archive does not contain a regular executable at its expected path.\n' >&2
  exit 1
}

staged_target=$(mktemp "$bin_directory/.hauler.XXXXXX")
chmod 0755 "$staged_target"
cp -- "$candidate" "$staged_target"
chmod 0755 "$staged_target"
ln -- "$staged_target" "$target"
rm -- "$staged_target"

installed_version=$("$target" version 2>&1 || true)
[[ $installed_version == *"$version"* ]] || {
  printf 'Installed Hauler failed the pinned version check: %s\n' "$target" >&2
  exit 1
}
printf 'Installed verified Hauler %s at %s\n' "$version" "$target"
