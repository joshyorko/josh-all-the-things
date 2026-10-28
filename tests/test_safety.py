import os
import signal
import threading

import pytest

from jat.safety import (
    ArchiveMember,
    empty_destination,
    existing_file,
    new_output_path,
    validate_archive_members,
)
from jat.staging import OwnedStage


def test_new_output_is_create_only_and_requires_real_parent(tmp_path):
    output = tmp_path / "haul.tar.zst"
    assert new_output_path(output) == output.resolve()
    output.write_bytes(b"existing")
    with pytest.raises(ValueError, match="already exists"):
        new_output_path(output)

    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)
    with pytest.raises(ValueError, match="real directory"):
        new_output_path(linked / "haul.tar.zst")


def test_existing_file_rejects_missing_directory_and_symlink(tmp_path):
    regular = tmp_path / "haul.tar.zst"
    regular.write_bytes(b"haul")
    assert existing_file(regular) == regular.resolve()
    with pytest.raises(ValueError, match="regular file"):
        existing_file(tmp_path / "missing")
    directory = tmp_path / "directory"
    directory.mkdir()
    with pytest.raises(ValueError, match="regular file"):
        existing_file(directory)
    linked = tmp_path / "linked"
    linked.symlink_to(regular)
    with pytest.raises(ValueError, match="symbolic link"):
        existing_file(linked)


def test_restore_destination_must_be_absent_or_known_empty(tmp_path):
    haul = tmp_path / "haul.tar.zst"
    haul.write_bytes(b"haul")
    absent = tmp_path / "absent"
    assert empty_destination(absent, haul) == absent.resolve()

    empty = tmp_path / "empty"
    empty.mkdir()
    assert empty_destination(empty, haul) == empty.resolve()
    (empty / "explanation.txt").write_text("not empty")
    with pytest.raises(ValueError, match="must be empty"):
        empty_destination(empty, haul)

    linked = tmp_path / "linked"
    linked.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic link"):
        empty_destination(linked, haul)
    with pytest.raises(ValueError, match="overlaps"):
        empty_destination(haul, haul)


@pytest.mark.parametrize(
    ("members", "message"),
    [
        ([], "empty"),
        ([ArchiveMember("/absolute", "file")], "unsafe path"),
        ([ArchiveMember("../escape", "file")], "unsafe path"),
        ([ArchiveMember("root/../../escape", "file")], "unsafe path"),
        ([ArchiveMember("root/file\nname", "file")], "line break"),
        ([ArchiveMember("one/file", "file"), ArchiveMember("two/file", "file")], "one top-level"),
        (
            [ArchiveMember("root", "directory"), ArchiveMember("root/link", "symlink")],
            "root/link.*target is missing",
        ),
        (
            [ArchiveMember("root", "directory"), ArchiveMember("root/hard", "hardlink")],
            "root/hard.*unsupported member type",
        ),
        (
            [ArchiveMember("root", "directory"), ArchiveMember("root/file", "file"), ArchiveMember("root/file", "file")],
            "duplicate",
        ),
        (
            [ArchiveMember("root", "directory"), ArchiveMember("root/file", "file"), ArchiveMember("root/file/child", "file")],
            "collision",
        ),
    ],
)
def test_archive_members_fail_closed(members, message):
    with pytest.raises(ValueError, match=message):
        validate_archive_members(members)


def test_archive_members_allow_one_regular_tree():
    validate_archive_members(
        [
            ArchiveMember("project", "directory"),
            ArchiveMember("project/README.md", "file"),
            ArchiveMember("project/src", "directory"),
            ArchiveMember("project/src/main.py", "file"),
        ]
    )


def test_archive_members_allow_safe_internal_symlink():
    validate_archive_members(
        [
            ArchiveMember("project", "directory"),
            ArchiveMember("project/file.txt", "file"),
            ArchiveMember("project/link", "symlink", "file.txt"),
        ]
    )


def test_archive_members_allow_safe_chained_symlinks():
    validate_archive_members(
        [
            ArchiveMember("project", "directory"),
            ArchiveMember("project/sub", "directory"),
            ArchiveMember("project/sub/file.txt", "file"),
            ArchiveMember("project/a", "symlink", "sub"),
            ArchiveMember("project/b", "symlink", "a/file.txt"),
        ]
    )


def test_archive_members_allow_a_path_scoped_symlink_revisit():
    validate_archive_members(
        [
            ArchiveMember("project", "directory"),
            ArchiveMember("project/sub", "directory"),
            ArchiveMember("project/a", "symlink", "sub"),
            ArchiveMember("project/link", "symlink", "a/../a"),
        ]
    )


def test_archive_members_bound_repeated_acyclic_symlink_expansion():
    members = [ArchiveMember("root", "directory"), ArchiveMember("root/a0", "symlink", ".")]
    members.extend(ArchiveMember(f"root/a{i}", "symlink", f"a{i - 1}/a{i - 1}") for i in range(1, 17))
    with pytest.raises(ValueError, match="symlink resolution limit"):
        validate_archive_members(members)


def test_archive_members_bound_deep_acyclic_symlink_chain():
    members = [ArchiveMember("root", "directory"), ArchiveMember("root/a0", "symlink", ".")]
    members.extend(ArchiveMember(f"root/a{i}", "symlink", f"a{i - 1}") for i in range(1, 300))
    with pytest.raises(ValueError, match="symlink resolution limit"):
        validate_archive_members(members)


def test_archive_members_allow_directory_intent_for_a_trailing_target_separator():
    validate_archive_members(
        [
            ArchiveMember("project", "directory"),
            ArchiveMember("project/sub", "directory"),
            ArchiveMember("project/link", "symlink", "sub/"),
        ]
    )


def test_archive_members_reject_trailing_directory_target_to_regular_file():
    with pytest.raises(ValueError, match="project/link.*directory"):
        validate_archive_members(
            [
                ArchiveMember("project", "directory"),
                ArchiveMember("project/file.txt", "file"),
                ArchiveMember("project/link", "symlink", "file.txt/"),
            ]
        )


def test_archive_members_reject_parent_traversal_through_regular_file():
    with pytest.raises(ValueError, match="project/link.*non-directory"):
        validate_archive_members(
            [
                ArchiveMember("project", "directory"),
                ArchiveMember("project/file.txt", "file"),
                ArchiveMember("project/link", "symlink", "file.txt/.."),
            ]
        )


def test_archive_members_require_a_regular_directory_top_level_root():
    with pytest.raises(ValueError, match="project.*top-level.*directory"):
        validate_archive_members([ArchiveMember("project", "file")])


def test_archive_members_reject_dangling_symlink_target_with_member_name():
    with pytest.raises(ValueError, match="project/link.*dangling"):
        validate_archive_members(
            [
                ArchiveMember("project", "directory"),
                ArchiveMember("project/link", "symlink", "missing.txt"),
            ]
        )


def test_archive_members_reject_symlink_traversal_through_non_directory():
    with pytest.raises(ValueError, match="project/link.*non-directory"):
        validate_archive_members(
            [
                ArchiveMember("project", "directory"),
                ArchiveMember("project/file.txt", "file"),
                ArchiveMember("project/link", "symlink", "file.txt/child"),
            ]
        )


def test_archive_members_reject_symlink_cycle_with_member_name():
    with pytest.raises(ValueError, match="project/a.*cycle"):
        validate_archive_members(
            [
                ArchiveMember("project", "directory"),
                ArchiveMember("project/a", "symlink", "b"),
                ArchiveMember("project/b", "symlink", "a"),
            ]
        )


def test_archive_members_rejects_transitive_symlink_escape_with_member_name():
    with pytest.raises(ValueError, match="project/b.*escapes the archive root"):
        validate_archive_members(
            [
                ArchiveMember("project", "directory"),
                ArchiveMember("project/sub", "directory"),
                ArchiveMember("project/a", "symlink", "sub/.."),
                ArchiveMember("project/b", "symlink", "a/.."),
                ArchiveMember("project/c", "symlink", "b/.."),
                ArchiveMember("project/link", "symlink", "c/../outside"),
            ]
        )


@pytest.mark.parametrize(
    ("target", "message"),
    [
        ("/etc/passwd", "absolute"),
        ("../../outside", "escapes"),
    ],
)
def test_archive_members_reject_unsafe_symlink_targets_with_member_name(target, message):
    with pytest.raises(ValueError, match=rf"project/link.*{message}"):
        validate_archive_members(
            [ArchiveMember("project", "directory"), ArchiveMember("project/link", "symlink", target)]
        )


def test_archive_members_rejects_top_level_symlink_target_outside_root():
    with pytest.raises(ValueError, match="project.*top-level entry must be a directory"):
        validate_archive_members([ArchiveMember("project", "symlink", ".")])


def test_archive_members_reject_symlink_target_with_unsafe_separator():
    with pytest.raises(ValueError, match="project/link.*unsafe separator"):
        validate_archive_members(
            [ArchiveMember("project", "directory"), ArchiveMember("project/link", "symlink", r"file\name")]
        )


def test_archive_members_reject_symlink_as_parent_with_member_name():
    with pytest.raises(ValueError, match="project/link.*non-directory parent"):
        validate_archive_members(
            [
                ArchiveMember("project", "directory"),
                ArchiveMember("project/link", "symlink", "file.txt"),
                ArchiveMember("project/link/child.txt", "file"),
            ]
        )


def test_owned_stage_cleans_only_its_root_on_error_and_interrupt(tmp_path):
    unrelated = tmp_path / "keep.txt"
    unrelated.write_text("keep")
    stage_path = None
    with pytest.raises(KeyboardInterrupt), OwnedStage(tmp_path, "restore") as stage:
        stage_path = stage.path
        (stage.path / "partial").write_text("partial")
        raise KeyboardInterrupt
    assert stage_path is not None and not stage_path.exists()
    assert unrelated.read_text() == "keep"


def test_owned_stage_cleans_before_forwarding_termination(tmp_path):
    stage = OwnedStage(tmp_path, "restore")
    stage.__enter__()
    path = stage.path
    (path / "partial").write_text("partial")
    with pytest.raises(SystemExit) as raised:
        stage.handle_signal(signal.SIGTERM, None)
    assert raised.value.code == 128 + signal.SIGTERM
    assert not path.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX ignored signal disposition")
def test_owned_stage_preserves_ignored_signal_until_context_exit(tmp_path):
    previous = signal.signal(signal.SIGTERM, signal.SIG_IGN)
    try:
        with OwnedStage(tmp_path, "restore") as stage:
            payload = stage.path / "payload"
            payload.write_text("keep until exit")
            os.kill(os.getpid(), signal.SIGTERM)
            assert payload.read_text() == "keep until exit"
        assert not stage.path.exists()
    finally:
        signal.signal(signal.SIGTERM, previous)


@pytest.mark.skipif(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0), reason="unprivileged POSIX directory permissions")
def test_owned_stage_removes_readonly_directories_without_following_symlinks(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("unrelated")
    outside.chmod(0o555)
    stage = OwnedStage(tmp_path, "restore")
    path = None
    try:
        with stage:
            path = stage.path
            readonly = path / "readonly"
            readonly.mkdir()
            (readonly / "payload").write_text("staged")
            (readonly / "outside").symlink_to(outside, target_is_directory=True)
            readonly.chmod(0o555)
        assert not path.exists()
        assert outside.stat().st_mode & 0o777 == 0o555
        assert (outside / "keep").read_text() == "unrelated"
    finally:
        outside.chmod(0o755)
        if path is not None and path.exists():
            (path / "readonly").chmod(0o755)
            stage._active = True
            stage.cleanup()


def test_owned_stage_rolls_back_when_handler_registration_fails(tmp_path):
    errors = []

    def enter_from_worker():
        try:
            with OwnedStage(tmp_path, "restore"):
                pytest.fail("worker-thread signal registration unexpectedly succeeded")
        except ValueError as error:
            errors.append(error)

    worker = threading.Thread(target=enter_from_worker)
    worker.start()
    worker.join(timeout=5)
    assert not worker.is_alive()
    assert len(errors) == 1
    assert not list(tmp_path.glob(".jat-restore-*")), "failed entry leaked its stage"


def test_owned_stage_preserves_primary_error_and_can_retry_cleanup(tmp_path, monkeypatch):
    import shutil

    original = shutil.rmtree
    stage = OwnedStage(tmp_path, "restore")

    def locked(*args, **kwargs):
        raise PermissionError("locked stage")

    monkeypatch.setattr("jat.staging.shutil.rmtree", locked)
    with pytest.raises(ValueError, match="original failure") as raised, stage:
        raise ValueError("original failure")
    assert any("Stage cleanup failed" in note for note in raised.value.__notes__)
    assert stage.path.exists()
    monkeypatch.setattr("jat.staging.shutil.rmtree", original)
    stage.cleanup()
    assert not stage.path.exists()
