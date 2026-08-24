"""Bounded, redacted subprocess execution with cancellation cleanup."""

import os
import signal
import subprocess
from collections.abc import Iterable
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
    ) -> ProcessResult:
        secret_values = tuple(secret for secret in secrets if secret)
        for secret in secret_values:
            log.hide_from_output(secret)
        log.info(f"Starting process: {Path(argv[0]).name}")
        process = subprocess.Popen(
            argv,
            stdin=None,
            stdout=None if foreground else subprocess.PIPE,
            stderr=None if foreground else subprocess.PIPE,
            text=True,
            start_new_session=os.name != "nt",
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
