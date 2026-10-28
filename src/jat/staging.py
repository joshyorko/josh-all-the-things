"""Operation-owned adjacent staging with signal-safe cleanup."""

import os
import shutil
import signal
import stat as stat_modes
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
        try:
            if signal.getsignal(signal.SIGINT) is not None:
                for signum in (signal.SIGINT, signal.SIGTERM):
                    previous = signal.getsignal(signum)
                    if previous != signal.SIG_IGN:
                        signal.signal(signum, self.handle_signal)
                        self._previous_handlers[signum] = previous
        except BaseException as error:
            self._cleanup_preserving(error)
            raise
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if exc_value is None:
            self.cleanup()
        else:
            self._cleanup_preserving(exc_value)

    def _cleanup_preserving(self, error: BaseException) -> None:
        try:
            self.cleanup()
        except BaseException as cleanup_error:  # noqa: BLE001 - preserve the original operation error.
            error.add_note(f"Stage cleanup failed: {cleanup_error}")

    def handle_signal(self, signum: int, frame: FrameType | None) -> None:
        previous = self._previous_handlers.get(signum, signal.SIG_DFL)
        if previous == signal.SIG_IGN:
            return
        if callable(previous):
            try:
                previous(signum, frame)
            except BaseException as error:
                self._cleanup_preserving(error)
                raise
            return
        error = KeyboardInterrupt() if signum == signal.SIGINT else SystemExit(128 + signum)
        self._cleanup_preserving(error)
        raise error

    def cleanup(self) -> None:
        if not self._active:
            return
        for signum, previous in self._previous_handlers.items():
            signal.signal(signum, previous)
        self._previous_handlers.clear()
        try:
            stat = self.path.stat(follow_symlinks=False)
        except FileNotFoundError:
            self._active = False
            return
        if self._identity != (stat.st_dev, stat.st_ino):
            self._active = False
            return
        if self.path.is_symlink():
            self.path.unlink()
        else:
            try:
                shutil.rmtree(self.path)
            except PermissionError:
                self._repair_directory_permissions()
                # Recheck identity after repair before retrying deletion.
                stat = self.path.stat(follow_symlinks=False)
                if self._identity != (stat.st_dev, stat.st_ino):
                    return
                shutil.rmtree(self.path)
        self._active = False

    def _repair_directory_permissions(self) -> None:
        """Repair only owned directories, using descriptors without symlinks."""
        if os.name == "nt":
            return
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        root = os.open(self.path, flags)

        def repair(directory: int) -> None:
            stat = os.fstat(directory)
            if stat.st_uid == os.getuid():
                os.fchmod(directory, stat_modes.S_IMODE(stat.st_mode) | stat_modes.S_IRWXU)
            with os.scandir(directory) as entries:
                for entry in entries:
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                    try:
                        child = os.open(entry.name, flags, dir_fd=directory)
                    except (FileNotFoundError, NotADirectoryError):
                        continue
                    try:
                        repair(child)
                    finally:
                        os.close(child)

        try:
            stat = os.fstat(root)
            if self._identity == (stat.st_dev, stat.st_ino):
                repair(root)
        finally:
            os.close(root)
