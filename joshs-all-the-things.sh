#!/usr/bin/env bash
set -euo pipefail

readonly WORKSPACE_ARTIFACT='joshs-all-the-things-workspace.tar.zst'
readonly WORKSPACE_REFERENCE='hauler/joshs-all-the-things-workspace.tar.zst:latest'
readonly WORKSPACE_RESTORE_DIRECTORY='workspace'
readonly BREW_RECOVERY_RESTORE_DIRECTORY='homebrew-recovery'
readonly BREW_RECOVERY_BREWFILE='Brewfile'
readonly SCRIPT_SOURCE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/$(basename -- "${BASH_SOURCE[0]}")"

declare -a TEMP_PATHS=()
declare -a TEMP_ROOTS=()
declare -a BUILD_IMAGES=()
declare -a INTERACTIVE_ARGS=()
RESTORE_CREATED_DESTINATION=
RESTORE_COMPLETE=0
BREW=
CHECKSUM_COMMAND=
ARCHIVE_COMMAND=

color_enabled() {
  local output_fd=$1
  [[ -z ${NO_COLOR:-} ]] || return 1

  case ${JAT_COLOR:-auto} in
    always) return 0 ;;
    never) return 1 ;;
    auto|'') [[ -t $output_fd && ${TERM:-dumb} != dumb ]] ;;
    *) return 1 ;;
  esac
}

status_line() {
  local output_fd=$1 label=$2 color=$3
  shift 3

  if color_enabled "$output_fd"; then
    printf '\033[%sm[%s]\033[0m %s\n' "$color" "$label" "$*" >&"$output_fd"
  else
    printf '[%s] %s\n' "$label" "$*" >&"$output_fd"
  fi
}

phase() {
  status_line 1 phase '1;34' "$*"
}

info() {
  status_line 1 info '36' "$*"
}

success() {
  status_line 1 ok '32' "$*"
}

warn() {
  status_line 1 warn '33' "$*"
}

error() {
  status_line 2 error '31' "$*"
}

script_path() {
  printf '%s\n' "$SCRIPT_SOURCE"
}

usage() {
  cat <<'EOF'
Usage:
  joshs-all-the-things.sh
  joshs-all-the-things.sh build --folder PATH [--brew PATH] [--all-images | --image REF ...] --output FILE
  joshs-all-the-things.sh restore --haul FILE --destination PATH
  joshs-all-the-things.sh serve --haul FILE

Run without arguments in a terminal for interactive mode.
EOF
}

die() {
  local status=$1
  shift
  error "$*"
  exit "$status"
}

cleanup() {
  local status=$?
  trap - EXIT INT TERM

  if [[ $RESTORE_CREATED_DESTINATION && $RESTORE_COMPLETE -eq 0 && -d $RESTORE_CREATED_DESTINATION ]]; then
    rm -rf -- "$RESTORE_CREATED_DESTINATION"
  fi

  local index
  for ((index=${#TEMP_PATHS[@]} - 1; index >= 0; index--)); do
    [[ -e ${TEMP_PATHS[index]} || -L ${TEMP_PATHS[index]} ]] && rm -rf -- "${TEMP_PATHS[index]}"
  done
  for ((index=${#TEMP_ROOTS[@]} - 1; index >= 0; index--)); do
    rmdir -- "${TEMP_ROOTS[index]}" 2>/dev/null || true
  done
  exit "$status"
}

trap cleanup EXIT INT TERM

make_temp_dir() {
  local destination_variable=$1 directory temp_root="$PWD/.tmp"
  mkdir -p -- "$temp_root"
  TEMP_ROOTS+=("$temp_root")
  directory=$(mktemp -d "$temp_root/joshs-all-the-things.XXXXXX")
  TEMP_PATHS+=("$directory")
  printf -v "$destination_variable" '%s' "$directory"
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die 1 "Required command is unavailable after bootstrap: $1"
}

find_brew() {
  local configured_prefix=${JAT_LINUXBREW_PREFIX:-}
  if command -v brew >/dev/null 2>&1; then
    command -v brew
  elif [[ $configured_prefix && -x $configured_prefix/bin/brew ]]; then
    printf '%s/bin/brew\n' "$configured_prefix"
  elif [[ -z $configured_prefix && -x /home/linuxbrew/.linuxbrew/bin/brew ]]; then
    printf '%s\n' /home/linuxbrew/.linuxbrew/bin/brew
  else
    return 1
  fi
}

select_checksum_command() {
  if command -v sha256sum >/dev/null 2>&1; then
    CHECKSUM_COMMAND=sha256sum
  elif command -v shasum >/dev/null 2>&1; then
    CHECKSUM_COMMAND='shasum -a 256'
  else
    die 1 'Required SHA-256 command is unavailable (sha256sum or shasum)'
  fi
}

bootstrap_dependencies() {
  require_command hauler
  require_command zstd
  select_archive_command
  select_checksum_command
}

select_archive_command() {
  if command -v gtar >/dev/null 2>&1 && gtar --zstd --version >/dev/null 2>&1; then
    ARCHIVE_COMMAND=gtar
  elif command -v tar >/dev/null 2>&1 && tar --zstd --version >/dev/null 2>&1; then
    ARCHIVE_COMMAND=tar
  else
    die 1 'Required GNU tar implementation with --zstd is unavailable (gtar or tar)'
  fi
}

absolute_existing_directory() {
  (cd -- "$1" && pwd -P)
}

absolute_existing_file() {
  local directory basename
  directory=$(cd -- "$(dirname -- "$1")" && pwd -P)
  basename=$(basename -- "$1")
  printf '%s/%s\n' "$directory" "$basename"
}

absolute_new_path() {
  local directory basename
  directory=$(cd -- "$(dirname -- "$1")" && pwd -P)
  basename=$(basename -- "$1")
  printf '%s/%s\n' "$directory" "$basename"
}

reject_line_breaks() {
  [[ $1 != *$'\n'* && $1 != *$'\r'* ]] || die 1 "$2 contains a line break"
}

require_option_value() {
  local option=$1 value=${2-}
  [[ -n $value && $value != --* ]] || die 2 "$option requires a value"
}

yaml_quote() {
  local value=$1
  value=${value//\\/\\\\}
  value=${value//\"/\\\"}
  printf '"%s"' "$value"
}

shell_quote() {
  printf '%q' "$1"
}

use_invocation_directory() {
  local invocation_directory=${JAT_RUN_DIR:-}
  [[ -n ${ROBOT_ROOT:-} ]] || return 0

  if [[ -z $invocation_directory && -L /proc/$PPID/cwd ]]; then
    invocation_directory=$(readlink -f -- "/proc/$PPID/cwd")
  fi
  [[ -n $invocation_directory ]] || invocation_directory=$HOME
  [[ -d $invocation_directory && -w $invocation_directory ]] ||
    die 1 "Invocation directory is unavailable or not writable: $invocation_directory"
  cd -- "$invocation_directory"
}

expand_interactive_path() {
  case $1 in
    '~') printf '%s\n' "$HOME" ;;
    '~/'*) printf '%s/%s\n' "$HOME" "${1#\~/}" ;;
    *) printf '%s\n' "$1" ;;
  esac
}

validate_brew_recovery_export() {
  local recovery_directory=$1 brewfile="$1/$BREW_RECOVERY_BREWFILE"
  [[ -f $brewfile && ! -L $brewfile && -r $brewfile ]] ||
    die 1 "Homebrew recovery export must contain a readable regular $BREW_RECOVERY_BREWFILE: $recovery_directory"
}

prompt_value() {
  local destination_variable=$1 prompt=$2 default_value=${3-} value
  if [[ -n $default_value ]]; then
    prompt="$prompt [$default_value]: "
  else
    prompt="$prompt: "
  fi

  IFS= read -r -p "$prompt" value || {
    printf '\n' >&2
    die 130 'Interactive input cancelled.'
  }
  [[ -n $value ]] || value=$default_value
  printf -v "$destination_variable" '%s' "$value"
}

interactive_wizard() {
  local action folder output image_choice image brew_choice brew haul destination folder_name
  local -a images=()

  printf "Josh's All the Things\n\n"
  printf '%s\n' 'What do you want to do?' '  1) Build a haul' '  2) Restore a folder' '  3) Serve images'
  prompt_value action 'Choose 1, 2, or 3' '1'

  case ${action,,} in
    1|b|build)
      prompt_value folder 'Folder to pack'
      [[ -n $folder ]] || die 2 'A folder path is required.'
      folder=$(expand_interactive_path "$folder")

      printf '\n%s\n' 'Which local Docker images should be included?' '  1) None (folder only)' '  2) All tagged local images' '  3) Select images'
      prompt_value image_choice 'Choose 1, 2, or 3' '1'
      case ${image_choice,,} in
        1|n|none) ;;
        2|a|all) images=(--all-images) ;;
        3|s|select|selected)
          printf 'Enter one image at a time. Press Enter on a blank line when finished.\n'
          while true; do
            prompt_value image 'Image reference'
            [[ -n $image ]] || break
            images+=(--image "$image")
          done
          ;;
        *) die 2 "Unknown image choice: $image_choice" ;;
      esac

      printf '\n%s\n' 'Do you want to include a Homebrew recovery directory?' '  1) No (folder only or folder plus images)' '  2) Yes'
      prompt_value brew_choice 'Choose 1 or 2' '1'
      case ${brew_choice,,} in
        1|n|no) ;;
        2|y|yes)
          prompt_value brew 'Recovery directory'
          [[ -n $brew ]] || die 2 'A recovery directory path is required.'
          brew=$(expand_interactive_path "$brew")
          [[ -d $brew ]] || die 1 "Recovery directory does not exist: $brew"
          images+=(--brew "$brew")
          ;;
        *) die 2 "Unknown recovery directory choice: $brew_choice" ;;
      esac

      folder_name=$(basename -- "${folder%/}")
      prompt_value output 'Output haul' "./${folder_name}-haul.tar.zst"
      output=$(expand_interactive_path "$output")
      INTERACTIVE_ARGS=(build --folder "$folder" "${images[@]}" --output "$output")
      ;;
    2|r|restore)
      prompt_value haul 'Haul file to restore'
      [[ -n $haul ]] || die 2 'A haul file is required.'
      prompt_value destination 'Empty destination directory'
      [[ -n $destination ]] || die 2 'A destination path is required.'
      INTERACTIVE_ARGS=(restore --haul "$(expand_interactive_path "$haul")" --destination "$(expand_interactive_path "$destination")")
      ;;
    3|s|serve)
      prompt_value haul 'Haul file to serve'
      [[ -n $haul ]] || die 2 'A haul file is required.'
      INTERACTIVE_ARGS=(serve --haul "$(expand_interactive_path "$haul")")
      ;;
    q|quit|exit)
      exit 0
      ;;
    *) die 2 "Unknown action: $action" ;;
  esac

  printf '\n'
}

parse_build() {
  BUILD_FOLDER=
  BUILD_OUTPUT=
  BUILD_BREW=
  BUILD_ALL_IMAGES=0
  BUILD_IMAGES=()

  while (($#)); do
    case $1 in
      --folder)
        (($# >= 2)) || die 2 '--folder requires a value'
        require_option_value --folder "$2"
        [[ -z $BUILD_FOLDER ]] || die 2 '--folder may be specified only once'
        BUILD_FOLDER=$2
        shift 2
        ;;
      --output)
        (($# >= 2)) || die 2 '--output requires a value'
        require_option_value --output "$2"
        [[ -z $BUILD_OUTPUT ]] || die 2 '--output may be specified only once'
        BUILD_OUTPUT=$2
        shift 2
        ;;
      --brew)
        (($# >= 2)) || die 2 '--brew requires a value'
        require_option_value --brew "$2"
        [[ -z $BUILD_BREW ]] || die 2 '--brew may be specified only once'
        BUILD_BREW=$2
        shift 2
        ;;
      --all-images)
        [[ $BUILD_ALL_IMAGES -eq 0 ]] || die 2 '--all-images may be specified only once'
        BUILD_ALL_IMAGES=1
        shift
        ;;
      --image)
        (($# >= 2)) || die 2 '--image requires a value'
        require_option_value --image "$2"
        BUILD_IMAGES+=("$2")
        shift 2
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *) die 2 "Unknown build option: $1" ;;
    esac
  done

  [[ -n $BUILD_FOLDER ]] || die 2 'build requires --folder PATH'
  [[ -n $BUILD_OUTPUT ]] || die 2 'build requires --output FILE'
  ! ((BUILD_ALL_IMAGES && ${#BUILD_IMAGES[@]} > 0)) || die 2 '--all-images cannot be combined with --image'
  [[ -d $BUILD_FOLDER ]] || die 1 "Source folder does not exist: $BUILD_FOLDER"
  [[ -r $BUILD_FOLDER && -x $BUILD_FOLDER ]] || die 1 "Source folder is not readable: $BUILD_FOLDER"
  if [[ -n $BUILD_BREW ]]; then
    [[ -d $BUILD_BREW ]] || die 1 "Homebrew recovery directory does not exist: $BUILD_BREW"
    [[ -r $BUILD_BREW && -x $BUILD_BREW ]] || die 1 "Homebrew recovery directory is not readable: $BUILD_BREW"
    validate_brew_recovery_export "$BUILD_BREW"
  fi
  [[ -d $(dirname -- "$BUILD_OUTPUT") ]] || die 1 "Output parent directory does not exist: $(dirname -- "$BUILD_OUTPUT")"
  [[ -w $(dirname -- "$BUILD_OUTPUT") ]] || die 1 "Output parent directory is not writable: $(dirname -- "$BUILD_OUTPUT")"
  [[ ! -e $BUILD_OUTPUT && ! -L $BUILD_OUTPUT ]] || die 1 "Output already exists and will not be overwritten: $BUILD_OUTPUT"
  reject_line_breaks "$BUILD_FOLDER" 'Source folder path'
  reject_line_breaks "$BUILD_BREW" 'Homebrew recovery path'
  reject_line_breaks "$BUILD_OUTPUT" 'Output path'
}

parse_restore() {
  RESTORE_HAUL=
  RESTORE_DESTINATION=
  while (($#)); do
    case $1 in
      --haul)
        (($# >= 2)) || die 2 '--haul requires a value'
        require_option_value --haul "$2"
        [[ -z $RESTORE_HAUL ]] || die 2 '--haul may be specified only once'
        RESTORE_HAUL=$2
        shift 2
        ;;
      --destination)
        (($# >= 2)) || die 2 '--destination requires a value'
        require_option_value --destination "$2"
        [[ -z $RESTORE_DESTINATION ]] || die 2 '--destination may be specified only once'
        RESTORE_DESTINATION=$2
        shift 2
        ;;
      -h|--help) usage; exit 0 ;;
      *) die 2 "Unknown restore option: $1" ;;
    esac
  done
  [[ -n $RESTORE_HAUL ]] || die 2 'restore requires --haul FILE'
  [[ -n $RESTORE_DESTINATION ]] || die 2 'restore requires --destination PATH'
  [[ -f $RESTORE_HAUL && -r $RESTORE_HAUL ]] || die 1 "Haul is not a readable file: $RESTORE_HAUL"
  reject_line_breaks "$RESTORE_HAUL" 'Haul path'
  reject_line_breaks "$RESTORE_DESTINATION" 'Destination path'
}

parse_serve() {
  SERVE_HAUL=
  while (($#)); do
    case $1 in
      --haul)
        (($# >= 2)) || die 2 '--haul requires a value'
        require_option_value --haul "$2"
        [[ -z $SERVE_HAUL ]] || die 2 '--haul may be specified only once'
        SERVE_HAUL=$2
        shift 2
        ;;
      -h|--help) usage; exit 0 ;;
      *) die 2 "Unknown serve option: $1" ;;
    esac
  done
  [[ -n $SERVE_HAUL ]] || die 2 'serve requires --haul FILE'
  [[ -f $SERVE_HAUL && -r $SERVE_HAUL ]] || die 1 "Haul is not a readable file: $SERVE_HAUL"
  reject_line_breaks "$SERVE_HAUL" 'Haul path'
}

select_build_images() {
  local -a requested=("${BUILD_IMAGES[@]}")
  BUILD_IMAGES=()

  if ((${#requested[@]} > 0)); then
    command -v docker >/dev/null 2>&1 || die 1 'Docker is required when --image is used, but the Docker CLI is unavailable'
    docker info >/dev/null 2>&1 || die 1 'Docker is not reachable; explicit --image requests require a running local Docker daemon'
    local image
    for image in "${requested[@]}"; do
      reject_line_breaks "$image" 'Docker image reference'
      [[ $image != *[[:space:]]* ]] || die 1 "Docker image reference contains whitespace: $image"
      docker image inspect "$image" >/dev/null 2>&1 || die 1 "Local Docker image not found: $image"
      BUILD_IMAGES+=("$image")
    done
  elif ((BUILD_ALL_IMAGES)); then
    if ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
      info 'No local Docker images found; building a folder-only haul.'
      return
    fi
    local image listed_images
    if ! listed_images=$(docker image ls --format '{{.Repository}}:{{.Tag}}'); then
      warn 'Could not list local Docker images; building a folder-only haul.'
      return
    fi
    while IFS= read -r image; do
      [[ -n $image && $image != '<none>:'* && $image != *':<none>' ]] || continue
      BUILD_IMAGES+=("$image")
    done <<<"$listed_images"
    ((${#BUILD_IMAGES[@]} > 0)) || info 'No local Docker images found; building a folder-only haul.'
  fi
}

write_manifest() {
  local manifest=$1 archive=$2 brew_archive=${3-} image
  {
    printf '%s\n' 'apiVersion: content.hauler.cattle.io/v1'
    printf '%s\n' 'kind: Files'
    printf '%s\n' 'metadata:' '  name: joshs-all-the-things-workspace' 'spec:' '  files:'
    printf '    - path: %s\n' "$(yaml_quote "$archive")"
    printf '      name: %s\n' "$WORKSPACE_ARTIFACT"
    if [[ -n $brew_archive ]]; then
      printf '%s\n' '---' 'apiVersion: content.hauler.cattle.io/v1' 'kind: Files' 'metadata:' '  name: joshs-all-the-things-homebrew-recovery' 'spec:' '  files:'
      printf '    - path: %s\n' "$(yaml_quote "$brew_archive")"
      printf '      name: %s\n' 'homebrew-recovery.tar.zst'
    fi
    if ((${#BUILD_IMAGES[@]} > 0)); then
      printf '%s\n' '---' 'apiVersion: content.hauler.cattle.io/v1' 'kind: Images' 'metadata:' '  name: joshs-all-the-things-images' 'spec:' '  images:'
      for image in "${BUILD_IMAGES[@]}"; do
        printf '    - name: %s\n' "$(yaml_quote "$image")"
        printf '%s\n' '      local: true'
      done
    fi
  } >"$manifest"
}

sha256_file() {
  if [[ $CHECKSUM_COMMAND == sha256sum ]]; then
    sha256sum -- "$1" | awk '{print $1}'
  else
    shasum -a 256 -- "$1" | awk '{print $1}'
  fi
}

build_haul() {
  local folder output folder_name folder_parent work archive brew_archive manifest build_store validation_store
  local output_parent output_name stage_dir staged size checksum script brew_folder brew_name brew_parent
  folder=$(absolute_existing_directory "$BUILD_FOLDER")
  output=$(absolute_new_path "$BUILD_OUTPUT")
  [[ ! -e $output && ! -L $output ]] || die 1 "Output already exists and will not be overwritten: $output"
  folder_name=$(basename -- "$folder")
  folder_parent=$(dirname -- "$folder")
  output_parent=$(dirname -- "$output")
  output_name=$(basename -- "$output")

  select_build_images
  make_temp_dir work
  archive="$work/$WORKSPACE_ARTIFACT"
  brew_archive=
  manifest="$work/manifest.yaml"
  build_store="$work/build-store"
  validation_store="$work/validation-store"
  mkdir -p "$work/hauler-temp" "$work/validation-temp"

  phase 'Creating compressed workspace archive (this may take a while)'
  info "Folder: $folder"
  "$ARCHIVE_COMMAND" --zstd -cpf "$archive" -C "$folder_parent" -- "$folder_name"
  success 'Workspace archive created.'
  if [[ -n $BUILD_BREW ]]; then
    brew_folder=$(absolute_existing_directory "$BUILD_BREW")
    brew_name=$(basename -- "$brew_folder")
    brew_parent=$(dirname -- "$brew_folder")
    brew_archive="$work/homebrew-recovery.tar.zst"
    phase 'Creating compressed Homebrew recovery archive'
    info "Recovery directory: $brew_folder"
    "$ARCHIVE_COMMAND" --zstd -cpf "$brew_archive" -C "$brew_parent" -- "$brew_name"
    success 'Homebrew recovery archive created.'
  fi
  write_manifest "$manifest" "$archive" "$brew_archive"

  phase 'Syncing content into an isolated Hauler store (this may take a while)'
  hauler store sync --store "$build_store" --tempdir "$work/hauler-temp" --filename "$manifest"
  success 'Content synchronized into the temporary Hauler store.'

  stage_dir=$(mktemp -d "$output_parent/.joshs-all-the-things-stage.XXXXXX")
  TEMP_PATHS+=("$stage_dir")
  staged="$stage_dir/$output_name"
  phase 'Saving the portable haul (this may take a while)'
  hauler store save --store "$build_store" --tempdir "$work/hauler-temp" --filename "$staged"
  phase 'Validating saved haul in a separate isolated store'
  hauler store load --store "$validation_store" --tempdir "$work/validation-temp" --filename "$staged"
  hauler store info --store "$validation_store" --tempdir "$work/validation-temp" >/dev/null

  ln -- "$staged" "$output" || die 1 "Output appeared during build and was not overwritten: $output"
  rm -f -- "$staged"

  size=$(stat -c '%s' -- "$output")
  checksum=$(sha256_file "$output")
  script=$(script_path)
  success 'Haul validated and published without overwriting an existing file.'
  printf '\nHaul: %s\nSize: %s bytes\nSHA-256: %s\n' "$output" "$size" "$checksum"
  info 'This haul is the only file you need to transfer.'
  printf 'Next:\n'
  printf '  Transfer this one file:\n    %s\n' "$(shell_quote "$output")"
  printf '  Restore the folder:\n    %s restore --haul %s --destination %s\n' "$(shell_quote "$script")" "$(shell_quote "$output")" "$(shell_quote "$output_parent/${folder_name}-restored")"
  printf '  Serve images:\n    %s serve --haul %s\n' "$(shell_quote "$script")" "$(shell_quote "$output")"
}

destination_is_empty() {
  [[ -z $(find "$1" -mindepth 1 -maxdepth 1 -print -quit) ]]
}

prepare_restore_destination() {
  local haul=$1 destination=$2 parent destination_abs haul_abs
  [[ $destination != / ]] || die 1 'Destination must not be the filesystem root'
  [[ ! -L $destination ]] || die 1 "Destination must not be a symbolic link: $destination"
  [[ ! -e $destination || -d $destination ]] || die 1 "Destination is not a directory: $destination"

  parent=$(dirname -- "$destination")
  [[ -d $parent && ! -L $parent ]] || die 1 "Destination parent must be an existing real directory: $parent"
  [[ -w $parent ]] || die 1 "Destination parent is not writable: $parent"
  destination_abs=$(absolute_new_path "$destination")
  haul_abs=$(absolute_existing_file "$haul")
  [[ $destination_abs != / && $destination_abs != "$haul_abs" ]] || die 1 'Destination is unsafe or overlaps the haul file'

  if [[ -d $destination ]]; then
    destination_is_empty "$destination" || die 1 "Destination must be empty: $destination"
  fi
  printf '%s\n' "$destination_abs"
}

validate_archive_members() {
  local archive=$1 member count=0 top=
  while IFS= read -r member; do
    [[ -n $member ]] || continue
    [[ $member != /* && $member != .. && $member != ../* && $member != */../* && $member != */.. ]] || die 1 "Workspace artifact contains an unsafe path: $member"
    if [[ -z $top ]]; then
      top=${member%%/*}
    elif [[ ${member%%/*} != "$top" ]]; then
      die 1 'Workspace artifact contains more than one top-level entry'
    fi
    count=$((count + 1))
  done < <("$ARCHIVE_COMMAND" --zstd -tf "$archive")
  ((count > 0)) || die 1 'Workspace artifact is empty'
}

restore_haul() {
  local haul destination work store extracted artifact script brew_artifact brew_destination workspace_destination
  local -a artifacts=() brew_artifacts=()
  haul=$(absolute_existing_file "$RESTORE_HAUL")
  destination=$(prepare_restore_destination "$haul" "$RESTORE_DESTINATION")
  make_temp_dir work
  store="$work/store"
  extracted="$work/extracted"
  mkdir -p "$extracted" "$work/hauler-temp"

  phase 'Loading haul into an isolated temporary store'
  hauler store load --store "$store" --tempdir "$work/hauler-temp" --filename "$haul"
  hauler store info --store "$store" --tempdir "$work/hauler-temp" >/dev/null
  success 'Haul loaded and inspected.'
  phase 'Extracting the workspace archive into a private staging area'
  hauler store extract "$WORKSPACE_REFERENCE" --store "$store" --tempdir "$work/hauler-temp" --output "$extracted"
  mapfile -d '' artifacts < <(find "$extracted" -type f -name "$WORKSPACE_ARTIFACT" -print0)
  ((${#artifacts[@]} == 1)) || die 1 "Haul must contain exactly one workspace artifact named $WORKSPACE_ARTIFACT"
  artifact=${artifacts[0]}
  validate_archive_members "$artifact"

  mapfile -d '' brew_artifacts < <(find "$extracted" -type f -name 'homebrew-recovery.tar.zst' -print0)
  if ((${#brew_artifacts[@]} == 1)); then
    brew_artifact=${brew_artifacts[0]}
    validate_archive_members "$brew_artifact"
  elif ((${#brew_artifacts[@]} > 1)); then
    die 1 'Haul must contain at most one Homebrew recovery artifact named homebrew-recovery.tar.zst'
  fi

  if [[ ! -d $destination ]]; then
    mkdir -- "$destination"
    RESTORE_CREATED_DESTINATION=$destination
  fi
  workspace_destination="$destination/$WORKSPACE_RESTORE_DIRECTORY"
  brew_destination="$destination/$BREW_RECOVERY_RESTORE_DIRECTORY"
  mkdir -- "$workspace_destination"
  if [[ -n ${brew_artifact:-} ]]; then
    mkdir -- "$brew_destination"
  fi
  phase 'Restoring folder contents to the destination'
  "$ARCHIVE_COMMAND" --zstd -xpf "$artifact" -C "$workspace_destination"
  if [[ -n ${brew_artifact:-} ]]; then
    phase 'Restoring Homebrew recovery contents to the destination'
    "$ARCHIVE_COMMAND" --zstd -xpf "$brew_artifact" -C "$brew_destination" --strip-components=1
    validate_brew_recovery_export "$brew_destination"
  fi
  RESTORE_COMPLETE=1
  script=$(script_path)
  success 'Folder restored safely.'
  printf 'Restored destination: %s\n' "$destination"
  printf 'Next:\n'
  printf '  Serve images from this haul:\n    %s serve --haul %s\n' "$(shell_quote "$script")" "$(shell_quote "$haul")"
}

serve_haul() {
  local haul work store registry_dir registry_config
  haul=$(absolute_existing_file "$SERVE_HAUL")
  make_temp_dir work
  store="$work/store"
  registry_dir="$work/registry"
  registry_config="$work/registry.yaml"
  mkdir -p "$work/hauler-temp" "$registry_dir"

  cat >"$registry_config" <<EOF
version: 0.1
log:
  level: info
storage:
  filesystem:
    rootdirectory: $(yaml_quote "$registry_dir")
  cache:
    blobdescriptor: inmemory
  maintenance:
    readonly:
      enabled: true
catalog:
  maxentries: 1000
http:
  addr: ":5000"
  headers:
    X-Content-Type-Options:
      - nosniff
validation:
  manifests:
    urls:
      allow:
        - ".+"
EOF

  phase 'Loading haul into an isolated temporary store'
  hauler store load --store "$store" --tempdir "$work/hauler-temp" --filename "$haul"
  success 'Haul loaded.'
  printf 'Loaded haul inventory:\n'
  hauler store info --store "$store" --tempdir "$work/hauler-temp"
  printf '\nRegistry: http://127.0.0.1:5000\n'
  printf 'Next:\n'
  printf '  In a second terminal while this foreground registry is running:\n'
  printf '    curl http://127.0.0.1:5000/v2/_catalog\n'
  printf '    curl http://127.0.0.1:5000/v2/REPOSITORY/tags/list\n'
  printf '    docker pull 127.0.0.1:5000/REPOSITORY:TAG\n'
  printf '  Copy the temporary store to a permanent registry:\n'
  printf '    hauler store copy --store %s registry://REGISTRY\n' "$(shell_quote "$store")"
  info 'The temporary store is removed when this command exits.'
  phase 'Starting the local registry in the foreground'
  hauler store serve registry --store "$store" --tempdir "$work/hauler-temp" --directory "$registry_dir" --config "$registry_config"
}

main() {
  use_invocation_directory
  if (($# == 0)); then
    [[ -t 0 ]] || {
      usage >&2
      exit 2
    }
    interactive_wizard
    set -- "${INTERACTIVE_ARGS[@]}"
  fi
  local command=$1
  shift
  case $command in
    build)
      parse_build "$@"
      bootstrap_dependencies
      build_haul
      ;;
    restore)
      parse_restore "$@"
      bootstrap_dependencies
      restore_haul
      ;;
    serve)
      parse_serve "$@"
      bootstrap_dependencies
      serve_haul
      ;;
    -h|--help)
      usage
      ;;
    *)
      usage >&2
      die 2 "Unknown command: $command"
      ;;
  esac
}

main "$@"
