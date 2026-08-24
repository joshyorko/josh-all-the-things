#!/usr/bin/env bash
set -euo pipefail

robot_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
archive="$robot_root/dist/hololib.zip"
receipt="$robot_root/dist/hololib.json"
repository="ghcr.io/joshyorko/josh-all-the-things-hololib"
username="${GITHUB_ACTOR:-joshyorko}"

while (($#)); do
  case "$1" in
    --zip) archive=$(realpath -- "$2"); shift 2 ;;
    --receipt) receipt=$(realpath -- "$2"); shift 2 ;;
    --repository) repository=$2; shift 2 ;;
    --username) username=$2; shift 2 ;;
    *) printf 'Unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done

: "${GITHUB_TOKEN:?GITHUB_TOKEN must be exported before publishing}"
command -v jq >/dev/null
command -v oras >/dev/null
[[ -f $archive && -f $receipt ]] || {
  printf 'Verified hololib ZIP and receipt are required.\n' >&2
  exit 2
}

jq -e '
  .format_version == 1 and
  .operation == "build-hololib" and
  .success == true and
  .verified_no_build == true and
  (.environment_hash | type == "string" and length > 0) and
  (.jat_git_sha | test("^[0-9a-f]{40}$")) and
  (.rcc_version | type == "string" and length > 1) and
  (.platform | type == "string" and length > 0) and
  (.zip.sha256 | test("^[0-9a-f]{64}$")) and
  (.zip.size | type == "number" and . > 0)
' "$receipt" >/dev/null

expected_sha=$(jq -r '.zip.sha256' "$receipt")
actual_sha=$(sha256sum "$archive" | cut -d' ' -f1)
expected_size=$(jq -r '.zip.size' "$receipt")
actual_size=$(stat --printf='%s' "$archive")
[[ $actual_sha == "$expected_sha" && $actual_size == "$expected_size" ]] || {
  printf 'Hololib ZIP does not match its verified receipt.\n' >&2
  exit 2
}

rcc_version=$(jq -r '.rcc_version | ltrimstr("v")' "$receipt")
platform=$(jq -r '.platform' "$receipt")
environment_hash=$(jq -r '.environment_hash' "$receipt")
jat_sha=$(jq -r '.jat_git_sha' "$receipt")
tag="rcc-${rcc_version}-${platform}-${environment_hash}-shared-${actual_sha:0:12}"
reference="${repository}:${tag}"

printf '%s' "$GITHUB_TOKEN" | oras login ghcr.io --username "$username" --password-stdin
oras push "$reference" \
  --artifact-type application/vnd.joshyorko.rcc-hololib.v1 \
  --annotation org.opencontainers.image.source=https://github.com/joshyorko/josh-all-the-things \
  --annotation "org.opencontainers.image.revision=$jat_sha" \
  "$archive:application/vnd.joshyorko.rcc-hololib.v1+zip" \
  "$receipt:application/vnd.joshyorko.rcc-hololib.receipt.v1+json"

manifest_digest=$(oras manifest fetch --descriptor "$reference" | jq -r '.digest')
[[ $manifest_digest =~ ^sha256:[0-9a-f]{64}$ ]] || {
  printf 'Registry did not return a valid manifest digest.\n' >&2
  exit 2
}
printf '%s@%s\n' "$repository" "$manifest_digest"
