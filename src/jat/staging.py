"""Operation-owned adjacent staging with signal-safe cleanup."""

import shutil
import signal
import tempfile
from pathlib import Path
from types import FrameType
from typing import Any, Self


class OwnedStage:
    def __init__(self, parent: Path, operation: str):
        self.parent = parent
        self.operation = operation
        self.path: Path
        self._identity: tuple[int, int] | None = None
        self._previous_handlers: dict[int, Any] = {}
        self._active = False

    def __enter__(self) -> Self:
        self.path = Path(tempfile.mkdtemp(prefix=f".jat-{self.operation}-", dir=self.parent))
        stat = self.path.stat(follow_symlinks=False)
        self._identity = (stat.st_dev, stat.st_ino)
        self._active = True
        if signal.getsignal(signal.SIGINT) is not None:
            for signum in (signal.SIGINT, signal.SIGTERM):
                self._previous_handlers[signum] = signal.getsignal(signum)
                signal.signal(signum, self.handle_signal)
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.cleanup()

    def handle_signal(self, signum: int, frame: FrameType | None) -> None:
        previous = self._previous_handlers.get(signum, signal.SIG_DFL)
        self.cleanup()
        if callable(previous):
            previous(signum, frame)
            return
        if previous == signal.SIG_IGN:
            return
        if signum == signal.SIGINT:
            raise KeyboardInterrupt
        raise SystemExit(128 + signum)

    def cleanup(self) -> None:
        if not self._active:
            return
        self._active = False
        for signum, previous in self._previous_handlers.items():
            signal.signal(signum, previous)
        self._previous_handlers.clear()
        try:
            stat = self.path.stat(follow_symlinks=False)
        except FileNotFoundError:
            return
        if self._identity != (stat.st_dev, stat.st_ino):
            return
        if self.path.is_symlink():
            self.path.unlink()
        else:
            shutil.rmtree(self.path)
