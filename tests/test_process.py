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


def test_streaming_run_stops_process_on_timeout(tmp_path):
    script = streaming_script(
        tmp_path, "import time\nprint('started', flush=True)\ntime.sleep(30)\n"
    )
    started = time.monotonic()
    completed = ProcessRunner().run([sys.executable, str(script)], timeout=1, on_line=lambda line: None)
    elapsed = time.monotonic() - started
    assert completed.timed_out is True
    assert completed.exit_status == 124
    assert elapsed < 15


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
    assert sorted(signals) == [(4242 + len(argv), __import__("signal").SIGTERM) for argv in (["hauler", "one"], ["hauler", "two"])]


def test_supervise_rejects_an_empty_command_list():
    with pytest.raises(ValueError):
        ProcessRunner().supervise([])
