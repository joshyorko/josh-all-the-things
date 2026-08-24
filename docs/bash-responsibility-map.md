# Bash responsibility map

| Bash function | Current responsibility | Python destination |
| --- | --- | --- |
| `color_enabled`, `status_line`, `phase`, `info`, `success`, `warn`, `error` | Human UI | `src/jat/cli.py` |
| `usage`, `prompt_value`, `interactive_wizard`, `expand_interactive_path` | CLI help and prompting | `src/jat/cli.py` |
| `parse_build`, `parse_restore`, `parse_serve`, `require_option_value` | CLI parsing | `src/jat/cli.py`, `src/jat/models.py` |
| `die`, `cleanup`, `make_temp_dir` | Exit and owned cleanup | `src/jat/staging.py`, CLI exception mapping |
| `require_command`, `find_brew`, `bootstrap_dependencies` | Capability bootstrap | `scripts/install_dependencies.sh`, `Doctor` |
| `select_archive_command`, `select_checksum_command` | Tool capability selection | `src/jat/archive.py`, `src/jat/process.py` |
| `absolute_existing_directory`, `absolute_existing_file`, `absolute_new_path`, `reject_line_breaks` | Fail-closed path validation | `src/jat/safety.py` |
| `yaml_quote`, `shell_quote`, `write_manifest` | Hauler manifest rendering | `src/jat/hauler.py` |
| `use_invocation_directory` | Invocation-root selection | `src/jat/cli.py`, RCC request loader |
| `validate_brew_recovery_export` | Homebrew recovery validation | `src/jat/safety.py` |
| `select_build_images` | Docker image policy | `src/jat/services.py`, `src/jat/hauler.py` |
| `sha256_file` | Artifact identity | `src/jat/process.py` |
| `build_haul` | Build orchestration | `src/jat/services.py` |
| `destination_is_empty`, `prepare_restore_destination`, `validate_archive_members` | Restore safety | `src/jat/safety.py`, `src/jat/staging.py` |
| `restore_haul` | Restore orchestration | `src/jat/services.py` |
| `serve_haul` | Foreground registry lifecycle | `src/jat/services.py`, `src/jat/hauler.py` |
| `main` | Dispatch | `src/jat/cli.py`; RCC dispatch stays in `tasks.py` |

The Bash implementation remains the behavior oracle until Python CLI, RCC JSON
tasks, and real Hauler/GNU tar verticals are all green.
