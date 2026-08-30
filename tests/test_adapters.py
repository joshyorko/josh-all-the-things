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
        self.cwds = []
        self.responses = list(responses or [])

    def run(self, argv, timeout=None, foreground=False, secrets=(), cwd=None):
        self.calls.append((argv, timeout, foreground, secrets))
        self.cwds.append(cwd)
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


def test_process_runner_decodes_external_output_without_locale_failures():
    completed = ProcessRunner().run(
        [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'valid\\x90\\xff\\n')"]
    )
    assert completed.exit_status == 0
    assert "valid" in completed.stdout
    assert "�" in completed.stdout


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


def test_hauler_serve_debug_level_is_forwarded_without_shell_framing(tmp_path, monkeypatch):
    monkeypatch.setenv("JAT_HAULER_LOG_LEVEL", "debug")
    runner = RecordingRunner()
    adapter = HaulerAdapter(runner, executable="/tools/hauler")
    adapter.serve(tmp_path / "store", tmp_path / "temp", tmp_path / "registry", tmp_path / "registry.yaml")
    assert runner.calls[-1][0][:3] == ["/tools/hauler", "--log-level", "debug"]
    assert runner.calls[-1][2] is True


def test_hauler_serve_files_uses_exact_fileserver_argv(tmp_path):
    runner = RecordingRunner()
    adapter = HaulerAdapter(runner, executable="/tools/hauler.exe")
    adapter.serve_files(tmp_path / "store", tmp_path / "temp", tmp_path / "files", port=8080)
    assert runner.calls[-1][0] == [
        "/tools/hauler.exe",
        "--store",
        str(tmp_path / "store"),
        "--tempdir",
        str(tmp_path / "temp"),
        "store",
        "serve",
        "fileserver",
        "--directory",
        str(tmp_path / "files"),
        "--port",
        "8080",
    ]
    assert runner.calls[-1][2] is True


def test_windows_hauler_adapter_adds_local_files_without_files_manifest(tmp_path):
    runner = RecordingRunner()
    adapter = HaulerAdapter(runner, executable="/tools/hauler.exe", platform_name="windows")
    store = tmp_path / "store"
    temp = tmp_path / "temp"
    workspace = tmp_path / "workspace file.tar.zst"
    workspace.write_bytes(b"workspace")
    adapter.sync_files(store, temp, [(workspace, "workspace file.tar.zst")], ["example/image:latest"])

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
            workspace.name,
            "--name",
            "workspace file.tar.zst",
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
    assert runner.cwds[0] == workspace.parent


def test_windows_hauler_adapter_rejects_unsafe_relative_payload_names(tmp_path):
    runner = RecordingRunner()
    adapter = HaulerAdapter(runner, executable="/tools/hauler.exe", platform_name="windows")
    store = tmp_path / "store"
    temp = tmp_path / "temp"
    for name in ("bad:name", r"bad\name", "CON", "payload."):
        payload = tmp_path / name
        with pytest.raises(ValueError, match="unsafe Windows payload filename"):
            adapter.sync_files(store, temp, [(payload, "payload")])
    assert runner.calls == []


def test_hauler_sync_accepts_multiple_manifests_with_retries_and_slim_policy(tmp_path):
    runner = RecordingRunner()
    adapter = HaulerAdapter(runner, executable="/tools/hauler")
    adapter.sync(tmp_path / "store", tmp_path / "temp", "base.yaml", "https://example.test/product.yaml", retries=2, exclude_extras=True)
    assert runner.calls[0][0] == [
        "/tools/hauler",
        "store",
        "sync",
        "--store",
        str(tmp_path / "store"),
        "--tempdir",
        str(tmp_path / "temp"),
        "--exclude-extras",
        "--filename",
        "base.yaml",
        "--filename",
        "https://example.test/product.yaml",
        "--retries",
        "2",
    ]


def test_hauler_sync_image_txt_delegates_native_ingestion(tmp_path):
    runner = RecordingRunner()
    adapter = HaulerAdapter(runner, executable="/tools/hauler")
    adapter.sync_image_txt(tmp_path / "store", tmp_path / "temp", ["./images.txt", "https://example.test/images.txt"], retries=4)
    assert runner.calls[0][0] == [
        "/tools/hauler",
        "store",
        "sync",
        "--store",
        str(tmp_path / "store"),
        "--tempdir",
        str(tmp_path / "temp"),
        "--image-txt",
        "./images.txt",
        "--image-txt",
        "https://example.test/images.txt",
        "--retries",
        "4",
    ]
    adapter.sync_image_txt(tmp_path / "store", tmp_path / "temp", [])
    assert len(runner.calls) == 1


def test_hauler_save_chunk_size_and_containerd_are_exact_and_exclusive(tmp_path):
    runner = RecordingRunner()
    adapter = HaulerAdapter(runner, executable="/tools/hauler")
    adapter.save(tmp_path / "store", tmp_path / "temp", tmp_path / "haul.tar.zst", chunk_size="500MB")
    assert runner.calls[0][0] == [
        "/tools/hauler",
        "store",
        "save",
        "--store",
        str(tmp_path / "store"),
        "--tempdir",
        str(tmp_path / "temp"),
        "--filename",
        str(tmp_path / "haul.tar.zst"),
        "--chunk-size",
        "500MB",
    ]
    adapter.save(tmp_path / "store", tmp_path / "temp", tmp_path / "images.tar", containerd=True)
    assert runner.calls[1][0][-1] == "--containerd"
    with pytest.raises(ValueError, match="mutually exclusive"):
        adapter.save(tmp_path / "store", tmp_path / "temp", tmp_path / "x.tar.zst", chunk_size="1G", containerd=True)
    assert len(runner.calls) == 2


def test_hauler_copy_targets_are_passed_through_with_transport_flags(tmp_path):
    runner = RecordingRunner()
    adapter = HaulerAdapter(runner, executable="/tools/hauler")
    adapter.copy(tmp_path / "store", tmp_path / "temp", "registry://registry.example.test", retries=5)
    adapter.copy(tmp_path / "store", tmp_path / "temp", "dir:///tmp/exported", plain_http=True, insecure=True)
    assert [call[0][-1] for call in runner.calls] == [
        "registry://registry.example.test",
        "dir:///tmp/exported",
    ]
    assert runner.calls[0][0][-3:] == ["--retries", "5", "registry://registry.example.test"]
    assert "--plain-http" in runner.calls[1][0] and "--insecure" in runner.calls[1][0]


def test_hauler_windows_image_adds_forward_slim_policy(tmp_path):
    runner = RecordingRunner()
    adapter = HaulerAdapter(runner, executable="/tools/hauler.exe", platform_name="windows")
    adapter.sync_files(tmp_path / "store", tmp_path / "temp", [], ["example/image:latest"], retries=2, exclude_extras=True)
    assert runner.calls[0][0] == [
        "/tools/hauler.exe",
        "--store",
        str(tmp_path / "store"),
        "--tempdir",
        str(tmp_path / "temp"),
        "store",
        "add",
        "image",
        "example/image:latest",
        "--local",
        "--exclude-extras",
        "--retries",
        "2",
    ]


def test_hauler_serve_commands_include_executable_and_optional_log_level(tmp_path, monkeypatch):
    monkeypatch.delenv("JAT_HAULER_LOG_LEVEL", raising=False)
    adapter = HaulerAdapter(RecordingRunner(), executable="/tools/hauler")
    fileserver = adapter.serve_fileserver_command(tmp_path / "store", tmp_path / "temp", tmp_path / "files", 8080)
    registry = adapter.serve_registry_command(
        tmp_path / "store", tmp_path / "temp", tmp_path / "registry", tmp_path / "registry.yaml"
    )
    assert fileserver == [
        "/tools/hauler",
        "--store",
        str(tmp_path / "store"),
        "--tempdir",
        str(tmp_path / "temp"),
        "store",
        "serve",
        "fileserver",
        "--directory",
        str(tmp_path / "files"),
        "--port",
        "8080",
    ]
    assert registry == [
        "/tools/hauler",
        "store",
        "serve",
        "registry",
        "--store",
        str(tmp_path / "store"),
        "--tempdir",
        str(tmp_path / "temp"),
        "--directory",
        str(tmp_path / "registry"),
        "--config",
        str(tmp_path / "registry.yaml"),
    ]
    monkeypatch.setenv("JAT_HAULER_LOG_LEVEL", "debug")
    assert adapter.serve_registry_command(
        tmp_path / "store", tmp_path / "temp", tmp_path / "registry", tmp_path / "registry.yaml"
    )[:3] == ["/tools/hauler", "--log-level", "debug"]


def test_hauler_long_operations_stream_progress_when_a_sink_is_attached(tmp_path):
    class StreamingRunner(RecordingRunner):
        def run(self, argv, timeout=None, foreground=False, secrets=(), cwd=None, on_line=None):
            self.calls.append((argv, timeout, foreground, secrets))
            if on_line is not None:
                on_line("transferring blob 1/2")
            return result(argv=argv)

    sink_lines = []
    adapter = HaulerAdapter(StreamingRunner(), executable="/tools/hauler", progress=sink_lines.append)
    adapter.sync(tmp_path / "store", tmp_path / "temp", "manifest.yaml")
    assert sink_lines == ["transferring blob 1/2"]

    quiet = HaulerAdapter(RecordingRunner(), executable="/tools/hauler")
    quiet.sync(tmp_path / "store", tmp_path / "temp", "manifest.yaml")
    assert all("on_line" not in str(call) for call in quiet.runner.calls)
