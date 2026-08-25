#!/usr/bin/env bash
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
schema="$root/docs/environment-artifact-receipt.schema.json"
archive="$root/dist/jat-runtime.rcca"
receipt="$root/dist/jat-runtime.json"
repository="ghcr.io/joshyorko/josh-all-the-things-jat-runtime"
username="${GITHUB_ACTOR:-joshyorko}"
while (($#)); do
  case "$1" in
    --archive) archive=$(realpath -- "$2"); shift 2;;
    --receipt) receipt=$(realpath -- "$2"); shift 2;;
    --repository) repository=$2; shift 2;;
    --username) username=$2; shift 2;;
    *) printf 'Unknown argument: %s\n' "$1" >&2; exit 2;;
  esac
done
: "${GITHUB_TOKEN:?GITHUB_TOKEN must be exported before publishing}"
command -v jq >/dev/null
command -v oras >/dev/null
[[ -f $archive && -f $receipt ]] || { printf 'Verified JAT artifact and receipt are required.\n' >&2; exit 2; }
[[ -f $schema ]] || { printf 'Receipt schema is required: %s\n' "$schema" >&2; exit 2; }
jq -e '.format_version == 2 and .verified_acquire == true and .verified_no_build == true and .verified_exec == true and (.artifact_digest | test("^sha256:[0-9a-f]{64}$")) and (.specification_digest | test("^sha256:[0-9a-f]{64}$")) and (.legacy_blueprint_key | type == "string" and length > 0) and (.archive.sha256 | test("^[0-9a-f]{64}$")) and (.archive.size | type == "number" and . > 0)' "$receipt" >/dev/null
[[ $(sha256sum "$archive" | cut -d' ' -f1) == "$(jq -r .archive.sha256 "$receipt")" ]] || { printf 'Artifact does not match its receipt.\n' >&2; exit 2; }
[[ $(stat --printf='%s' "$archive") == "$(jq -r .archive.size "$receipt")" ]] || { printf 'Artifact size does not match its receipt.\n' >&2; exit 2; }
artifact=$(jq -r .artifact_digest "$receipt")
reference="$repository:$(jq -r .platform "$receipt")-${artifact#sha256:}"
printf '%s' "$GITHUB_TOKEN" | oras login ghcr.io --username "$username" --password-stdin
(cd "$(dirname "$archive")" && oras push "$reference" --artifact-type application/vnd.joshyorko.rcc-environment-artifact.v2 \
  "$(basename "$archive"):application/vnd.joshyorko.rcc-environment-artifact.v2+rcca" \
  "$(basename "$receipt"):application/vnd.joshyorko.rcc-environment-artifact-receipt.v2+json")
digest=$(oras manifest fetch --descriptor "$reference" | jq -r .digest)
[[ $digest =~ ^sha256:[0-9a-f]{64}$ ]] || { printf 'Registry did not return a valid manifest digest.\n' >&2; exit 2; }
printf '%s@%s\n' "$repository" "$digest"
