"""Bounded, redacted subprocess execution with cancellation cleanup."""

import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from robocorp import log


@dataclass
class ProcessResult:
    argv: list[str]
    exit_status: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    cancelled: bool = False
    diagnostics_limit: int = field(default=2048, repr=False)

    @property
    def success(self) -> bool:
        return self.exit_status == 0

    @property
    def diagnostics(self) -> str:
        return "\n".join(part for part in (self.stdout, self.stderr) if part)[: self.diagnostics_limit]


class ProcessRunner:
    def __init__(self, diagnostics_limit: int = 2048):
        self.diagnostics_limit = diagnostics_limit

    def run(
        self,
        argv: list[str],
        timeout: float | None = None,
        foreground: bool = False,
        secrets: Iterable[str] = (),
        cwd: str | os.PathLike[str] | None = None,
        on_line: Callable[[str], None] | None = None,
        line_limit: int = 2000,
    ) -> ProcessResult:
        secret_values = tuple(secret for secret in secrets if secret)
        for secret in secret_values:
            log.hide_from_output(secret)
        log.info(f"Starting process: {Path(argv[0]).name}")
        if foreground or on_line is None:
            return self._run_capturing(argv, timeout, foreground, secret_values, cwd)
        return self._run_streaming(argv, timeout, secret_values, cwd, on_line, line_limit)

    def _run_capturing(
        self,
        argv: list[str],
        timeout: float | None,
        foreground: bool,
        secret_values: tuple[str, ...],
        cwd: str | os.PathLike[str] | None,
    ) -> ProcessResult:
        process = subprocess.Popen(
            argv,
            stdin=None,
            stdout=None if foreground else subprocess.PIPE,
            stderr=None if foreground else subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            start_new_session=os.name != "nt",
            cwd=cwd,
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._stop(process)
            stdout, stderr = process.communicate()
            log.warn(f"Process timed out: {Path(argv[0]).name}")
            return self._result(argv, 124, stdout, stderr or "operation timed out", secret_values, timed_out=True)
        except KeyboardInterrupt:
            self._stop(process)
            process.communicate()
            log.warn(f"Process cancelled: {Path(argv[0]).name}")
            raise
        log.info(f"Process finished with exit status {process.returncode}: {Path(argv[0]).name}")
        return self._result(argv, process.returncode, stdout, stderr, secret_values)

    def _run_streaming(
        self,
        argv: list[str],
        timeout: float | None,
        secret_values: tuple[str, ...],
        cwd: str | os.PathLike[str] | None,
        on_line: Callable[[str], None],
        line_limit: int,
    ) -> ProcessResult:
        """Capture bounded output while forwarding each truthful line."""
        process = subprocess.Popen(
            argv,
            stdin=None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            start_new_session=os.name != "nt",
            cwd=cwd,
        )
        state = {"forwarded": 0}
        tails: dict[str, list[str]] = {"stdout": [], "stderr": []}
        counts: dict[str, int] = {"stdout": 0, "stderr": 0}

        def sink(stream: str, line: str) -> None:
            redacted = self._redact(line.rstrip("\n"), secret_values)
            counts[stream] += 1
            tail = tails[stream]
            tail.append(redacted)
            del tail[:-50]
            if state["forwarded"] < line_limit:
                state["forwarded"] += 1
                try:
                    on_line(redacted)
                except Exception:
                    pass

        def pump(stream: str, pipe) -> None:
            try:
                for line in iter(pipe.readline, ""):
                    sink(stream, line)
            finally:
                pipe.close()

        readers = [
            threading.Thread(target=pump, args=(stream, pipe), daemon=True)
            for stream, pipe in (("stdout", process.stdout), ("stderr", process.stderr))
        ]
        for reader in readers:
            reader.start()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._stop(process)
            log.warn(f"Process timed out: {Path(argv[0]).name}")
            self._join(readers)
            return self._result(
                argv,
                124,
                self._streamed_tail(tails, counts, "stdout", True),
                self._streamed_tail(tails, counts, "stderr", False),
                secret_values,
                timed_out=True,
            )
        except KeyboardInterrupt:
            self._stop(process)
            self._join(readers)
            log.warn(f"Process cancelled: {Path(argv[0]).name}")
            raise
        self._join(readers)
        log.info(f"Process finished with exit status {process.returncode}: {Path(argv[0]).name}")
        return self._result(
            argv,
            process.returncode,
            self._streamed_tail(tails, counts, "stdout", False),
            self._streamed_tail(tails, counts, "stderr", False),
            secret_values,
        )

    @staticmethod
    def _join(readers: list[threading.Thread]) -> None:
        for reader in readers:
            reader.join(timeout=10)

    def _streamed_tail(self, tails, counts, stream: str, timed_out: bool) -> str:
        lines = list(tails[stream])
        dropped = counts[stream] - len(lines)
        if timed_out and stream == "stdout":
            lines.append("operation timed out")
        if dropped > 0:
            lines.insert(0, f"... {dropped} earlier lines omitted")
        return "\n".join(lines)

    def supervise(
        self,
        argvs: list[list[str]],
        timeout: float | None = None,
        secrets: Iterable[str] = (),
    ) -> ProcessResult:
        """Run sibling foreground children; failure or cancellation stops all."""
        if not argvs:
            raise ValueError("supervise requires at least one command")
        secret_values = tuple(secret for secret in secrets if secret)
        for secret in secret_values:
            log.hide_from_output(secret)
        processes = []
        try:
            for argv in argvs:
                log.info(f"Starting supervised process: {Path(argv[0]).name}")
                processes.append(
                    subprocess.Popen(
                        argv,
                        stdin=None,
                        stdout=None,
                        stderr=None,
                        start_new_session=os.name != "nt",
                    )
                )
            deadline = None if timeout is None else time.monotonic() + timeout
            while True:
                statuses = [process.poll() for process in processes]
                if all(status is not None for status in statuses):
                    break
                if any(status is not None and status != 0 for status in statuses):
                    self._stop_all(processes)
                    failed = [
                        Path(argv[0]).name
                        for argv, status in zip(argvs, statuses)
                        if status is not None and status != 0
                    ]
                    log.warn(f"Supervised process failed, siblings stopped: {', '.join(failed)}")
                    return ProcessResult(
                        argv=[argv for argv in argvs for argv in argv],
                        exit_status=next(status for status in statuses if status not in (None, 0)),
                        stderr=f"supervised process exited unexpectedly: {', '.join(failed)}",
                        diagnostics_limit=self.diagnostics_limit,
                    )
                if deadline is not None and time.monotonic() >= deadline:
                    self._stop_all(processes)
                    log.warn("Supervised processes timed out")
                    return ProcessResult(
                        argv=[argv for argv in argvs for argv in argv],
                        exit_status=124,
                        stderr="operation timed out",
                        timed_out=True,
                        diagnostics_limit=self.diagnostics_limit,
                    )
                time.sleep(0.2)
            exit_status = next((status for status in statuses if status != 0), 0)
            log.info(f"Supervised processes finished with exit status {exit_status}")
            return ProcessResult(
                argv=[argv for argv in argvs for argv in argv],
                exit_status=exit_status,
                diagnostics_limit=self.diagnostics_limit,
            )
        except BaseException:
            # KeyboardInterrupt, SystemExit (SIGTERM via OwnedStage), or any
            # failure must never leave an orphan Hauler child behind.
            self._stop_all(processes)
            log.warn("Supervised processes cancelled")
            raise

    def _stop_all(self, processes: list[subprocess.Popen]) -> None:
        for process in processes:
            if process.poll() is None:
                self._stop(process)

    def _result(self, argv, status, stdout, stderr, secrets, timed_out=False) -> ProcessResult:
        return ProcessResult(
            argv=list(argv),
            exit_status=status,
            stdout=self._redact(stdout or "", secrets),
            stderr=self._redact(stderr or "", secrets),
            timed_out=timed_out,
            diagnostics_limit=self.diagnostics_limit,
        )

    @staticmethod
    def _redact(value: str, secrets: Iterable[str]) -> str:
        for secret in secrets:
            value = value.replace(secret, "<redacted>")
        return value

    @staticmethod
    def _stop(process: subprocess.Popen) -> None:
        if os.name == "nt":
            process.terminate()
        else:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                process.kill()
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.wait()
