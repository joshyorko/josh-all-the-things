#!/usr/bin/env bash
set -euo pipefail

root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
publisher="$root/scripts/publish_environment_artifact.sh"
repository="ghcr.io/joshyorko/josh-all-the-things-jat-runtime"
username="${GITHUB_ACTOR:-joshyorko}"
linux_archive=""
linux_receipt=""
windows_archive=""
windows_receipt=""

while (($#)); do
  case "$1" in
    --linux-archive) linux_archive=$(realpath -- "$2"); shift 2;;
    --linux-receipt) linux_receipt=$(realpath -- "$2"); shift 2;;
    --windows-archive) windows_archive=$(realpath -- "$2"); shift 2;;
    --windows-receipt) windows_receipt=$(realpath -- "$2"); shift 2;;
    --repository) repository=$2; shift 2;;
    --username) username=$2; shift 2;;
    *) printf 'Unknown argument: %s\n' "$1" >&2; exit 2;;
  esac
done

published=0
if [[ -n $linux_archive || -n $linux_receipt ]]; then
  [[ -n $linux_archive && -n $linux_receipt ]] || { printf 'Linux archive and receipt must be supplied together.\n' >&2; exit 2; }
  "$publisher" --archive "$linux_archive" --receipt "$linux_receipt" --repository "$repository" --username "$username"
  published=1
fi
if [[ -n $windows_archive || -n $windows_receipt ]]; then
  [[ -n $windows_archive && -n $windows_receipt ]] || { printf 'Windows archive and receipt must be supplied together.\n' >&2; exit 2; }
  "$publisher" --archive "$windows_archive" --receipt "$windows_receipt" --repository "$repository" --username "$username"
  published=1
fi
((published == 1)) || { printf 'At least one platform archive and receipt pair is required.\n' >&2; exit 2; }
