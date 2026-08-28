import io
import sys
import tarfile
from pathlib import Path

import pytest

from jat.archive import ArchiveAdapter, parse_verbose_listing
from jat.hauler import HaulerAdapter
from jat.process import ProcessResult, ProcessRunner
from jat.safety import validate_archive_members


class RecordingRunner:
    def __init__(self, responses=None):
        self.calls = []
        self.responses = list(responses or [])

    def run(self, argv, timeout=None, foreground=False, secrets=()):
        self.calls.append((argv, timeout, foreground, secrets))
        if self.responses:
            return self.responses.pop(0)
        return ProcessResult(argv=argv, exit_status=0)


def result(argv=(), exit_status=0, stdout="", stderr=""):
    return ProcessResult(argv=list(argv), exit_status=exit_status, stdout=stdout, stderr=stderr)


def test_process_runner_records_argv_and_bounds_redacted_diagnostics():
    secret = "never-log-this"
    completed = ProcessRunner(diagnostics_limit=128).run(
        [sys.executable, "-c", f"print('{secret}' * 100)"], secrets=(secret,)
    )
    assert completed.argv[0] == sys.executable
    assert completed.exit_status == 0
    assert secret not in completed.diagnostics
    assert "<redacted>" in completed.diagnostics
    assert len(completed.diagnostics) <= 128


def test_process_runner_reports_timeout():
    completed = ProcessRunner().run([sys.executable, "-c", "import time; time.sleep(2)"], timeout=0.01)
    assert completed.exit_status == 124
    assert completed.timed_out is True
    assert "timed out" in completed.diagnostics


def test_process_runner_terminates_child_on_ctrl_c(monkeypatch):
    class InterruptedProcess:
        returncode = -15
        pid = 4242

        def __init__(self):
            self.communications = 0
            self.terminated = False

        def communicate(self, timeout=None):
            self.communications += 1
            if self.communications == 1:
                raise KeyboardInterrupt
            return ("", "")

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            return self.returncode

    process = InterruptedProcess()
    popen_options = {}

    def fake_popen(*args, **kwargs):
        popen_options.update(kwargs)
        return process

    signals = []
    monkeypatch.setattr("jat.process.subprocess.Popen", fake_popen)
    monkeypatch.setattr("jat.process.os.killpg", lambda pid, signum: signals.append((pid, signum)))
    with pytest.raises(KeyboardInterrupt):
        ProcessRunner().run(["hauler", "store", "serve"], foreground=True)
    assert popen_options["start_new_session"] is True
    assert signals == [(process.pid, __import__("signal").SIGTERM)]


def test_archive_resolves_gtar_then_capable_path_tar():
    runner = RecordingRunner([result(exit_status=0)])
    adapter = ArchiveAdapter(runner, which=lambda name: "/tools/gtar" if name == "gtar" else None)
    assert adapter.executable == "/tools/gtar"
    assert runner.calls[0][0] == ["/tools/gtar", "--zstd", "--version"]

    runner = RecordingRunner([result(exit_status=1), result(exit_status=0)])
    locations = {"gtar": "/bad/gtar", "tar": "/usr/bin/tar"}
    adapter = ArchiveAdapter(runner, which=locations.get)
    assert adapter.executable == "/usr/bin/tar"


def test_archive_does_not_resolve_linuxbrew_keg_only_tar():
    runner = RecordingRunner(
        [
            result(["brew", "--prefix", "gnu-tar"], stdout="/home/linuxbrew/.linuxbrew/opt/gnu-tar\n"),
            result(exit_status=0),
        ]
    )
    with pytest.raises(RuntimeError, match="GNU tar with --zstd is unavailable"):
        ArchiveAdapter(runner, which=lambda name: "/tools/brew" if name == "brew" else None)
    assert all(call[0][0] != "/tools/brew" for call in runner.calls)


def test_archive_uses_exact_create_extract_and_list_argv(tmp_path):
    runner = RecordingRunner(
        [
            result(exit_status=0),
            result(exit_status=0),
            result(
                stdout=(
                    "drwxr-xr-x 1000/1000 0 2026-01-01 00:00:00 'project'\n"
                    "-rw-r--r-- 1000/1000 4 2026-01-01 00:00:00 'project/file name'\n"
                )
            ),
        ]
    )
    adapter = ArchiveAdapter(runner, executable="/usr/bin/tar")
    source = tmp_path / "project"
    source.mkdir()
    archive = tmp_path / "workspace.tar.zst"
    destination = tmp_path / "restore"
    adapter.create(source, archive)
    adapter.extract(archive, destination)
    members = adapter.members(archive)
    assert runner.calls[0][0] == [
        "/usr/bin/tar",
        "--zstd",
        "-cpf",
        str(archive),
        "-C",
        str(tmp_path),
        "--",
        "project",
    ]
    assert runner.calls[1][0] == ["/usr/bin/tar", "--zstd", "-xpf", str(archive), "-C", str(destination)]
    assert [(member.name, member.kind) for member in members] == [
        ("project", "directory"),
        ("project/file name", "file"),
    ]


def test_verbose_listing_exposes_links_for_fail_closed_validation():
    members = parse_verbose_listing(
        "lrwxrwxrwx 0/0 0 2026-01-01 00:00:00 'root/link' -> '../outside'\n"
        "hrw-r--r-- 0/0 0 2026-01-01 00:00:00 'root/hard' link to 'root/file'\n"
    )
    assert [member.kind for member in members] == ["symlink", "hardlink"]


def test_real_gnu_tar_round_trip_and_member_contract(tmp_path):
    source = tmp_path / "project"
    source.mkdir()
    original = source / "file name.txt"
    original.write_text("synthetic\n")
    original.chmod(0o640)
    archive = tmp_path / "workspace.tar.zst"
    restored = tmp_path / "restored"
    restored.mkdir()

    adapter = ArchiveAdapter(ProcessRunner())
    adapter.create(source, archive)
    validate_archive_members(adapter.members(archive))
    adapter.extract(archive, restored)

    restored_file = restored / "project" / "file name.txt"
    assert restored_file.read_bytes() == original.read_bytes()
    assert restored_file.stat().st_mode & 0o777 == 0o640


def test_windows_archive_backend_is_contained_deterministic_and_round_trips(tmp_path):
    pytest.importorskip("zstandard")
    source = tmp_path / "project"
    source.mkdir()
    (source / "nested").mkdir()
    (source / "nested" / "file.txt").write_text("synthetic\n")
    first = tmp_path / "first.tar.zst"
    second = tmp_path / "second.tar.zst"
    restored = tmp_path / "restored"
    restored.mkdir()
    stripped = tmp_path / "stripped"
    stripped.mkdir()

    adapter = ArchiveAdapter(RecordingRunner(), platform_name="windows")
    adapter.create(source, first)
    adapter.create(source, second)

    assert first.read_bytes() == second.read_bytes()
    validate_archive_members(adapter.members(first))
    adapter.extract(first, restored)
    assert (restored / "project" / "nested" / "file.txt").read_text() == "synthetic\n"
    adapter.extract(first, stripped, strip_components=1)
    assert (stripped / "nested" / "file.txt").read_text() == "synthetic\n"


def test_windows_archive_backend_rejects_traversal_before_extraction(tmp_path):
    zstandard = pytest.importorskip("zstandard")
    archive = tmp_path / "unsafe.tar.zst"
    with archive.open("wb") as raw:
        with zstandard.ZstdCompressor(level=3).stream_writer(raw, closefd=False) as compressed:
            with tarfile.open(fileobj=compressed, mode="w|") as tar:
                member = tarfile.TarInfo("../outside.txt")
                payload = b"outside"
                member.size = len(payload)
                tar.addfile(member, io.BytesIO(payload))
    destination = tmp_path / "restored"
    destination.mkdir()

    adapter = ArchiveAdapter(RecordingRunner(), platform_name="windows")
    with pytest.raises(ValueError, match="unsafe path"):
        adapter.extract(archive, destination)
    assert not (tmp_path / "outside.txt").exists()


def test_hauler_adapter_owns_exact_argv(tmp_path):
    runner = RecordingRunner()
    adapter = HaulerAdapter(runner, executable="/tools/hauler", timeout=42)
    store = tmp_path / "store"
    temp = tmp_path / "temp"
    manifest = tmp_path / "manifest.yaml"
    haul = tmp_path / "haul.tar.zst"
    output = tmp_path / "extracted"

    adapter.sync(store, temp, manifest)
    adapter.save(store, temp, haul)
    adapter.load(store, temp, haul)
    adapter.info(store, temp)
    runner.responses.append(result(stdout="[]"))
    adapter.inventory(store, temp)
    adapter.extract("hauler/workspace:latest", store, temp, output)
    adapter.serve(store, temp, tmp_path / "registry", tmp_path / "registry.yaml")

    assert [call[0] for call in runner.calls] == [
        ["/tools/hauler", "store", "sync", "--store", str(store), "--tempdir", str(temp), "--filename", str(manifest)],
        ["/tools/hauler", "store", "save", "--store", str(store), "--tempdir", str(temp), "--filename", str(haul)],
        ["/tools/hauler", "store", "load", "--store", str(store), "--tempdir", str(temp), "--filename", str(haul)],
        ["/tools/hauler", "store", "info", "--store", str(store), "--tempdir", str(temp)],
        [
            "/tools/hauler",
            "store",
            "info",
            "--store",
            str(store),
            "--tempdir",
            str(temp),
            "--output",
            "json",
        ],
        [
            "/tools/hauler",
            "store",
            "extract",
            "hauler/workspace:latest",
            "--store",
            str(store),
            "--tempdir",
            str(temp),
            "--output",
            str(output),
        ],
        [
            "/tools/hauler",
            "store",
            "serve",
            "registry",
            "--store",
            str(store),
            "--tempdir",
            str(temp),
            "--directory",
            str(tmp_path / "registry"),
            "--config",
            str(tmp_path / "registry.yaml"),
        ],
    ]
    assert runner.calls[-1][2] is True


def test_windows_hauler_adapter_adds_local_files_without_files_manifest(tmp_path):
    runner = RecordingRunner()
    adapter = HaulerAdapter(runner, executable="/tools/hauler.exe", platform_name="windows")
    store = tmp_path / "store"
    temp = tmp_path / "temp"
    workspace = tmp_path / "workspace.tar.zst"
    workspace.write_bytes(b"workspace")
    adapter.sync_files(store, temp, [(workspace, "workspace.tar.zst")], ["example/image:latest"])

    assert [call[0] for call in runner.calls] == [
        [
            "/tools/hauler.exe",
            "--store",
            str(store),
            "--tempdir",
            str(temp),
            "store",
            "add",
            "file",
            str(workspace),
            "--name",
            "workspace.tar.zst",
        ],
        [
            "/tools/hauler.exe",
            "--store",
            str(store),
            "--tempdir",
            str(temp),
            "store",
            "add",
            "image",
            "example/image:latest",
            "--local",
        ],
    ]
