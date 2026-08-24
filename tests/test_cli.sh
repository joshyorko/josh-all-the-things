#!/usr/bin/env bash
set -euo pipefail

robot_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)

if ! help_output=$(bash "$robot_root/joshs-all-the-things.sh" --help); then
  printf '%s\n' 'FAIL: --help exited with a nonzero status' >&2
  exit 1
fi

if [[ $help_output != *'Usage:'* ]]; then
  printf '%s\n' 'FAIL: --help did not print usage' >&2
  exit 1
fi

if [[ $help_output != *'--brew PATH'* ]]; then
  printf '%s\n' 'FAIL: --help did not advertise --brew' >&2
  exit 1
fi

printf '%s\n' 'PASS: standalone --help prints usage'

tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT
fake_bin="$tmp/bin"
fixture_artifacts="$tmp/fixture-artifacts"
mkdir -p "$fake_bin" "$fixture_artifacts"

cat >"$fake_bin/hauler" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

case "$1 $2" in
  'store save')
    while (($#)); do
      if [[ $1 == --filename ]]; then
        touch -- "$2"
        exit 0
      fi
      shift
    done
    exit 1
    ;;
  'store extract')
    while (($#)); do
      if [[ $1 == --output ]]; then
        mkdir -p -- "$2"
        cp -a -- "$FAKE_HAUL_EXTRACT"/. "$2"/
        exit 0
      fi
      shift
    done
    exit 1
    ;;
  'store sync'|'store load'|'store info') exit 0 ;;
  *) exit 1 ;;
esac
EOF
chmod +x "$fake_bin/hauler"

source_folder="$tmp/source"
invalid_recovery="$tmp/invalid-recovery"
valid_recovery="$tmp/valid-recovery"
mkdir -p "$source_folder" "$invalid_recovery" "$valid_recovery"
printf '%s\n' 'brew "hauler"' >"$valid_recovery/Brewfile"

if PATH="$fake_bin:$PATH" bash "$robot_root/joshs-all-the-things.sh" build \
  --folder "$source_folder" \
  --brew "$invalid_recovery" \
  --output "$tmp/invalid.tar.zst" >"$tmp/invalid.log" 2>&1; then
  printf '%s\n' 'FAIL: build accepted a directory without a Brewfile' >&2
  exit 1
fi
if ! grep -Fq 'must contain a readable regular Brewfile' "$tmp/invalid.log"; then
  printf '%s\n' 'FAIL: invalid Homebrew recovery export did not report the contract' >&2
  exit 1
fi

PATH="$fake_bin:$PATH" bash "$robot_root/joshs-all-the-things.sh" build \
  --folder "$source_folder" \
  --brew "$valid_recovery" \
  --output "$tmp/valid.tar.zst" >/dev/null

outside="$tmp/outside"
malicious_workspace="$tmp/malicious-workspace"
mkdir -p "$outside" "$malicious_workspace"
ln -s -- "$outside" "$malicious_workspace/homebrew-recovery"
tar --zstd -cpf "$fixture_artifacts/joshs-all-the-things-workspace.tar.zst" \
  -C "$malicious_workspace" homebrew-recovery
tar --zstd -cpf "$fixture_artifacts/homebrew-recovery.tar.zst" \
  -C "$tmp" valid-recovery
touch "$tmp/restore-haul.tar.zst"
restore_destination="$tmp/restored-malicious"
PATH="$fake_bin:$PATH" FAKE_HAUL_EXTRACT="$fixture_artifacts" \
  bash "$robot_root/joshs-all-the-things.sh" restore \
  --haul "$tmp/restore-haul.tar.zst" \
  --destination "$restore_destination" >/dev/null
if [[ -e $outside/Brewfile ]]; then
  printf '%s\n' 'FAIL: recovery extraction followed a workspace-created symlink' >&2
  exit 1
fi
if [[ ! -f $restore_destination/homebrew-recovery/Brewfile ]]; then
  printf '%s\n' 'FAIL: recovery export was not restored to its reserved directory' >&2
  exit 1
fi

collision_workspace="$tmp/collision-workspace"
mkdir -p "$collision_workspace/homebrew-recovery"
printf '%s\n' workspace >"$collision_workspace/homebrew-recovery/workspace.txt"
tar --zstd -cpf "$fixture_artifacts/joshs-all-the-things-workspace.tar.zst" \
  -C "$collision_workspace" homebrew-recovery
collision_destination="$tmp/restored-collision"
PATH="$fake_bin:$PATH" FAKE_HAUL_EXTRACT="$fixture_artifacts" \
  bash "$robot_root/joshs-all-the-things.sh" restore \
  --haul "$tmp/restore-haul.tar.zst" \
  --destination "$collision_destination" >/dev/null
if [[ ! -f $collision_destination/workspace/homebrew-recovery/workspace.txt ]]; then
  printf '%s\n' 'FAIL: workspace homebrew-recovery directory was not preserved' >&2
  exit 1
fi
if [[ ! -f $collision_destination/homebrew-recovery/Brewfile ]]; then
  printf '%s\n' 'FAIL: recovery export collided with workspace contents' >&2
  exit 1
fi

archive_bin="$tmp/archive-bin"
mkdir -p "$archive_bin"
cat >"$archive_bin/tar" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if [[ $* == *'--zstd'* ]]; then
  printf '%s\n' "tar: unrecognized option '--zstd'" >&2
  exit 1
fi
exec /usr/bin/tar "$@"
EOF
chmod +x "$archive_bin/tar"
cat >"$archive_bin/gtar" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"$GNU_TAR_LOG"
exec /usr/bin/tar "$@"
EOF
chmod +x "$archive_bin/gtar"
export GNU_TAR_LOG="$tmp/gnu-tar.log"
archive_source="$tmp/archive-source"
mkdir -p "$archive_source"
printf '%s\n' archive >"$archive_source/file.txt"
if ! PATH="$archive_bin:$fake_bin:$PATH" bash "$robot_root/joshs-all-the-things.sh" build \
  --folder "$archive_source" \
  --output "$tmp/archive.tar.zst" >/dev/null 2>&1; then
  printf '%s\n' 'FAIL: standalone build did not select a GNU tar implementation' >&2
  exit 1
fi
if ! grep -Fq -- '--zstd -cpf' "$GNU_TAR_LOG"; then
  printf '%s\n' 'FAIL: standalone build did not use gtar for zstd archives' >&2
  exit 1
fi
if ! PATH="$archive_bin:$fake_bin:$PATH" FAKE_HAUL_EXTRACT="$fixture_artifacts" \
  bash "$robot_root/joshs-all-the-things.sh" restore \
  --haul "$tmp/restore-haul.tar.zst" \
  --destination "$tmp/archive-restored" >/dev/null 2>&1; then
  printf '%s\n' 'FAIL: standalone restore did not select a GNU tar implementation' >&2
  exit 1
fi
if ! grep -Fq -- '--zstd -tf' "$GNU_TAR_LOG" || ! grep -Fq -- '--zstd -xpf' "$GNU_TAR_LOG"; then
  printf '%s\n' 'FAIL: standalone restore did not use gtar for zstd archives' >&2
  exit 1
fi
printf '%s\n' 'PASS: standalone archive path selects GNU tar when tar lacks --zstd'

keg_tmp=$(mktemp -d)
keg_bin="$keg_tmp/bin"
keg_prefix="$keg_tmp/gnu-tar"
mkdir -p "$keg_bin" "$keg_prefix/bin"
cat >"$keg_bin/brew" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if [[ $1 == --prefix && ${2-} == gnu-tar ]]; then
  printf '%s\n' "$FAKE_GNU_TAR_PREFIX"
  exit 0
fi
exit 1
EOF
chmod +x "$keg_bin/brew"
cat >"$keg_bin/tar" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if [[ $* == *'--zstd'* ]]; then
  printf '%s\n' "tar: unrecognized option '--zstd'" >&2
  exit 1
fi
exec /usr/bin/tar "$@"
EOF
chmod +x "$keg_bin/tar"
cat >"$keg_prefix/bin/tar" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"$GNU_TAR_LOG"
exec /usr/bin/tar "$@"
EOF
chmod +x "$keg_prefix/bin/tar"
if ! PATH="$keg_bin:$fake_bin:$PATH" \
  FAKE_GNU_TAR_PREFIX="$keg_prefix" GNU_TAR_LOG="$keg_tmp/gnu-tar.log" \
  bash "$robot_root/joshs-all-the-things.sh" build \
  --folder "$archive_source" \
  --output "$keg_tmp/archive.tar.zst" >/dev/null 2>&1; then
  printf '%s\n' 'FAIL: Linuxbrew keg-only GNU tar was not selected' >&2
  exit 1
fi
if ! grep -Fq -- '--zstd -cpf' "$keg_tmp/gnu-tar.log"; then
  printf '%s\n' 'FAIL: Linuxbrew keg-only GNU tar path was not used' >&2
  exit 1
fi
printf '%s\n' 'PASS: Linuxbrew keg-only GNU tar path is selected'

tmp_install=$(mktemp -d)
trap 'rm -rf -- "$tmp_install"' EXIT
fake_homebrew_bin="$tmp_install/bin"
mkdir -p "$fake_homebrew_bin" "$tmp_install/prefix/bin"
cat >"$fake_homebrew_bin/brew" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"$FAKE_BREW_LOG"
case "$1" in
  config)
    exit 0
    ;;
  trust)
    [[ $* == 'trust hauler-dev/tap' ]] || {
      printf '%s\n' "unexpected trust args: $*" >&2
      exit 1
    }
    exit 0
    ;;
  bundle)
    exit 0
    ;;
  --version)
    printf '%s\n' 'Homebrew 6.0.15'
    exit 0
    ;;
  *)
    exit 0
    ;;
esac
EOF
chmod +x "$fake_homebrew_bin/brew"
cat >"$fake_homebrew_bin/hauler" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
chmod +x "$fake_homebrew_bin/hauler"
export FAKE_BREW_LOG="$tmp_install/brew.log"
PATH="$fake_homebrew_bin:$PATH" env -u CONDA_PREFIX \
  bash "$robot_root/scripts/install_dependencies.sh" 1 >/tmp/install-deps.log 2>&1
if ! grep -Fq 'trust hauler-dev/tap' "$FAKE_BREW_LOG"; then
  printf '%s\n' 'FAIL: install script did not explicitly trust hauler-dev/tap' >&2
  exit 1
fi
if ! grep -Fq 'bundle --file=' "$FAKE_BREW_LOG"; then
  printf '%s\n' 'FAIL: install script did not run brew bundle after trusting tap' >&2
  exit 1
fi
printf '%s\n' 'PASS: install script explicitly trusts hauler-dev/tap before bundle'

printf '%s\n' 'PASS: Homebrew recovery export validation and isolated restore layout'
