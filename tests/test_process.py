import os
import signal
import subprocess
import sys
import time

import pytest

from jat.process import ProcessRunner


def streaming_script(tmp_path, body: str):
    script = tmp_path / "script.py"
    script.write_text(body)
    return script


def test_streaming_run_forwards_redacted_lines_and_bounds_diagnostics(tmp_path):
    secret = "super-secret-token"
    script = streaming_script(
        tmp_path,
        "import sys, time\n"
        "for index in range(5):\n"
        "    print('transferring blob %d' % index, flush=True)\n"
        f"    print('{secret}', file=sys.stderr, flush=True)\n"
        "    time.sleep(0.02)\n",
    )
    lines = []
    completed = ProcessRunner(diagnostics_limit=200).run(
        [sys.executable, str(script)], on_line=lines.append, secrets=(secret,)
    )
    assert completed.success
    assert len(lines) == 10
    assert sum(line.startswith("transferring blob") for line in lines) == 5
    assert all(secret not in line for line in lines)
    assert sum("<redacted>" in line for line in lines) == 5
    assert secret not in completed.diagnostics
    assert "transferring blob" in completed.diagnostics


def test_streaming_run_reports_failure_and_keeps_tail(tmp_path):
    script = streaming_script(
        tmp_path,
        "import sys\nprint('out-line', flush=True)\nprint('err-line', file=sys.stderr, flush=True)\nsys.exit(3)\n",
    )
    completed = ProcessRunner().run([sys.executable, str(script)], on_line=lambda line: None)
    assert completed.exit_status == 3
    assert "out-line" in completed.stdout
    assert "err-line" in completed.stderr


def test_streaming_run_stops_process_on_timeout_and_keeps_stderr(tmp_path):
    script = streaming_script(
        tmp_path,
        "import sys, time\n"
        "print('started', flush=True)\n"
        "print('transfer failed', file=sys.stderr, flush=True)\n"
        "time.sleep(30)\n",
    )
    started = time.monotonic()
    completed = ProcessRunner().run([sys.executable, str(script)], timeout=1, on_line=lambda line: None)
    elapsed = time.monotonic() - started
    assert completed.timed_out is True
    assert completed.exit_status == 124
    assert elapsed < 15
    assert "transfer failed" in completed.stderr, "timeout receipts must retain the stderr tail"


def test_supervise_runs_children_to_completion():
    completed = ProcessRunner().supervise(
        [
            [sys.executable, "-c", "print('one')"],
            [sys.executable, "-c", "print('two')"],
        ],
        timeout=30,
    )
    assert completed.success
    assert completed.exit_status == 0


def test_supervise_stops_the_sibling_when_one_child_fails(tmp_path):
    long_sleep = streaming_script(tmp_path, "import time\ntime.sleep(60)\n")
    runner = ProcessRunner()
    started = time.monotonic()
    completed = runner.supervise(
        [
            [sys.executable, str(long_sleep)],
            [sys.executable, "-c", "import sys\nsys.exit(7)"],
        ],
        timeout=30,
    )
    elapsed = time.monotonic() - started
    assert completed.success is False
    assert completed.exit_status == 7
    assert "supervised process exited unexpectedly" in completed.diagnostics
    assert elapsed < 20, "the failing child must stop the operation promptly"


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group signal test")
def test_supervisor_cleanup_does_not_signal_a_reaped_child_group(monkeypatch):
    class Child:
        def __init__(self, status):
            self.status = status

        def poll(self):
            return self.status

    reaped, running = Child(0), Child(None)
    stopped = []
    runner = ProcessRunner()
    monkeypatch.setattr(runner, "_stop", stopped.append)

    runner._stop_all([reaped, running])

    assert stopped == [running]


def test_supervise_stops_all_children_on_cancellation(monkeypatch):
    signals = []
    monkeypatch.setattr("jat.process.os.killpg", lambda pid, signum: signals.append((pid, signum)))

    class FakeProcess:
        def __init__(self, argv, _):
            self.argv = argv
            self.pid = 4242 + len(argv)
            self.returncode = None

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            return self.returncode

    monkeypatch.setattr("jat.process.subprocess.Popen", lambda argv, **kwargs: FakeProcess(argv, kwargs))
    monkeypatch.setattr("jat.process.time.sleep", lambda seconds: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        ProcessRunner().supervise([["hauler", "one"], ["hauler", "two"]])
    assert sorted(signals) == sorted(
        (4242 + len(argv), signum)
        for argv in (["hauler", "one"], ["hauler", "two"])
        for signum in (signal.SIGTERM, signal.SIGKILL)
    )


def test_supervise_rejects_an_empty_command_list():
    with pytest.raises(ValueError):
        ProcessRunner().supervise([])


def test_supervise_passes_explicit_environment_to_children(tmp_path):
    destination = tmp_path / "child-env.txt"
    script = streaming_script(
        tmp_path,
        "import os, sys\n"
        "with open(sys.argv[1], 'w') as output:\n"
        "    output.write(os.environ['JAT_TEST_VALUE'])\n",
    )

    completed = ProcessRunner().supervise(
        [[sys.executable, str(script), str(destination)]],
        timeout=30,
        env={"JAT_TEST_VALUE": "scoped"},
    )

    assert completed.success
    assert destination.read_text() == "scoped"


def wait_for_file(path, timeout=5):
    deadline = time.monotonic() + timeout
    while not path.exists():
        if time.monotonic() >= deadline:
            pytest.fail(f"child did not become ready: {path}")
        time.sleep(0.01)


@pytest.fixture
def child_groups(monkeypatch):
    """Own and reap every subprocess created by these POSIX regressions."""
    processes = []
    original = subprocess.Popen

    def spawn(*args, **kwargs):
        process = original(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr("jat.process.subprocess.Popen", spawn)
    yield processes
    for process in processes:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)
        for pipe in (process.stdout, process.stderr):
            if pipe is not None:
                pipe.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX signal cancellation")
@pytest.mark.parametrize("streaming", [False, True])
def test_run_reaps_child_before_propagating_system_exit(tmp_path, child_groups, streaming):
    ready = tmp_path / "ready"
    script = streaming_script(tmp_path, f"from pathlib import Path\nimport time\nPath({str(ready)!r}).touch()\ntime.sleep(60)\n")
    previous = signal.getsignal(signal.SIGALRM)

    def cancel(signum, frame):
        wait_for_file(ready)
        raise SystemExit(143)

    signal.signal(signal.SIGALRM, cancel)
    signal.setitimer(signal.ITIMER_REAL, 0.2)
    try:
        with pytest.raises(SystemExit) as raised:
            ProcessRunner().run([sys.executable, str(script)], on_line=(lambda line: None) if streaming else None)
        assert raised.value.code == 143
        assert child_groups[0].poll() is not None, "cancellation left the child running"
        assert all(pipe is None or pipe.closed for pipe in (child_groups[0].stdout, child_groups[0].stderr))
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux subreaper and POSIX groups")
@pytest.mark.parametrize("streaming", [False, True])
def test_timeout_kills_descendant_even_when_leader_exits(tmp_path, child_groups, streaming):
    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)
    previous_subreaper = ctypes.c_int()
    assert libc.prctl(37, ctypes.byref(previous_subreaper), 0, 0, 0) == 0
    assert libc.prctl(36, 1, 0, 0, 0) == 0
    descendant = tmp_path / "descendant"
    child = (
        "import os, signal, time\nfrom pathlib import Path\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"Path({str(descendant)!r}).write_text(str(os.getpid()))\n"
        "time.sleep(60)\n"
    )
    script = streaming_script(
        tmp_path,
        f"import subprocess, sys, time\nsubprocess.Popen([sys.executable, '-c', {child!r}])\ntime.sleep(60)\n",
    )
    previous = signal.getsignal(signal.SIGALRM)

    def deadline(signum, frame):
        pytest.fail("timeout cleanup exceeded eight seconds")

    signal.signal(signal.SIGALRM, deadline)
    signal.setitimer(signal.ITIMER_REAL, 8)
    try:
        completed = ProcessRunner().run(
            [sys.executable, str(script)], timeout=0.5, on_line=(lambda line: None) if streaming else None
        )
        assert completed.timed_out
        wait_for_file(descendant)
        pid = int(descendant.read_text())
        end = time.monotonic() + 1
        while time.monotonic() < end:
            reaped, _ = os.waitpid(pid, os.WNOHANG)
            if reaped:
                break
            time.sleep(0.01)
        assert reaped == pid, "descendant survived the operation timeout"
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        for process in child_groups:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)
        if descendant.exists():
            try:
                os.waitpid(int(descendant.read_text()), 0)
            except ChildProcessError:
                pass
        assert libc.prctl(36, previous_subreaper.value, 0, 0, 0) == 0


def test_streaming_bounds_unterminated_records_and_redacts_chunk_boundaries(tmp_path):
    secret = "boundary-secret"
    script = streaming_script(
        tmp_path,
        "import sys\n"
        f"sys.stdout.write('a' * 8188 + {secret!r} + 'x' * 4000000)\n"
        "sys.stderr.write('y' * 4000000)\n",
    )
    lines = []
    completed = ProcessRunner(diagnostics_limit=8192).run(
        [sys.executable, str(script)], timeout=5, on_line=lines.append, secrets=(secret,)
    )
    assert completed.success
    assert max(len(line.encode()) for line in lines) <= 8192
    assert len(completed.stdout.encode()) <= 8292
    assert len(completed.stderr.encode()) <= 8292
    assert secret not in "\n".join(lines)


def test_streaming_bounds_total_tail_bytes(tmp_path):
    script = streaming_script(tmp_path, "for _ in range(100):\n    print('é' * 1000)\n")
    completed = ProcessRunner(diagnostics_limit=1024).run([sys.executable, str(script)], on_line=lambda line: None)
    assert completed.success
    assert len(completed.stdout.encode()) <= 1124
