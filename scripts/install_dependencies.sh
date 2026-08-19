#!/usr/bin/env bash
set -euo pipefail

readonly HOMEBREW_VERSION='6.0.15'
readonly INSTALL_REVISION=${1:-}
readonly SCRIPT_DIRECTORY="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"

[[ $INSTALL_REVISION == 1 ]] || {
  printf 'Expected Homebrew install revision 1.\n' >&2
  exit 2
}
[[ -n ${CONDA_PREFIX:-} ]] || {
  printf 'CONDA_PREFIX is required.\n' >&2
  exit 2
}

if existing_brew=$(command -v brew 2>/dev/null) && "$existing_brew" config >/dev/null 2>&1; then
  printf 'Using existing Homebrew: %s\n' "$existing_brew"
  "$existing_brew" --version
  export PATH="$(dirname -- "$existing_brew"):$PATH"
  "$existing_brew" bundle --file="$SCRIPT_DIRECTORY/Brewfile" --no-upgrade
  command -v hauler >/dev/null 2>&1 || {
    printf 'Hauler installation completed but hauler was not found on PATH.\n' >&2
    exit 1
  }
  exit 0
fi

brew_repository="$CONDA_PREFIX/Homebrew"
brew_executable="$CONDA_PREFIX/bin/brew"

mkdir -p \
  "$CONDA_PREFIX/bin" \
  "$CONDA_PREFIX/.cache/Homebrew" \
  "$CONDA_PREFIX/.logs/Homebrew" \
  "$CONDA_PREFIX/.tmp/Homebrew"

if [[ ! -x $brew_repository/bin/brew ]]; then
  git clone \
    --depth 1 \
    --branch "$HOMEBREW_VERSION" \
    https://github.com/Homebrew/brew.git \
    "$brew_repository"
fi

ln -sfn ../Homebrew/bin/brew "$brew_executable"

export HOMEBREW_CACHE="$CONDA_PREFIX/.cache/Homebrew"
export HOMEBREW_LOGS="$CONDA_PREFIX/.logs/Homebrew"
export HOMEBREW_TEMP="$CONDA_PREFIX/.tmp/Homebrew"
export HOMEBREW_NO_ANALYTICS=1
export HOMEBREW_NO_AUTO_UPDATE=1
export HOMEBREW_NO_ENV_HINTS=1
export HOMEBREW_NO_INSTALL_CLEANUP=1

"$brew_executable" --version

export PATH="$CONDA_PREFIX/bin:$PATH"
"$brew_executable" bundle --file="$SCRIPT_DIRECTORY/Brewfile" --no-upgrade
command -v hauler >/dev/null 2>&1 || {
  printf 'Hauler installation completed but hauler was not found on PATH.\n' >&2
  exit 1
}
