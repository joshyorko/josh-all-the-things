"""Native file-sharing controls; runnable through RCC without extra packages."""

import ctypes
import os
import tempfile
import unittest
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path
from unittest.mock import patch

from scripts.install_hauler import _promote_executable


@contextmanager
def deny_rename(path):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    # Permit reads/writes, but deliberately omit FILE_SHARE_DELETE.
    handle = kernel.CreateFileW(str(path), 0x80000000, 1 | 2, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    closed = False

    def release():
        nonlocal closed
        if not closed:
            if not kernel.CloseHandle(handle):
                raise ctypes.WinError(ctypes.get_last_error())
            closed = True

    try:
        yield release
    finally:
        release()


@unittest.skipUnless(os.name == "nt", "native Windows sharing semantics")
class WindowsPromotionTests(unittest.TestCase):
    def test_released_sharing_lock_allows_atomic_promotion(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory) / "stage.exe", Path(directory) / "installed.exe"
            source.write_bytes(b"verified replacement")
            target.write_bytes(b"original")
            with deny_rename(source) as release:
                def unlock(_delay):
                    self.assertEqual(target.read_bytes(), b"original")
                    release()

                with patch("scripts.install_hauler.time.sleep", side_effect=unlock) as sleep:
                    _promote_executable(source, target, windows=True)
                sleep.assert_called_once()
            self.assertFalse(source.exists())
            self.assertEqual(target.read_bytes(), b"verified replacement")

    def test_persistent_sharing_lock_fails_without_changing_target(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory) / "stage.exe", Path(directory) / "installed.exe"
            source.write_bytes(b"verified replacement")
            target.write_bytes(b"original")
            with deny_rename(source), patch("scripts.install_hauler.time.sleep") as sleep:
                with self.assertRaises(PermissionError) as failure:
                    _promote_executable(source, target, windows=True)
                self.assertIn(failure.exception.winerror, (32, 33))
                self.assertGreater(sleep.call_count, 0)
                self.assertLessEqual(sleep.call_count, 5)
                self.assertLessEqual(sum(call.args[0] for call in sleep.call_args_list), 3.2)
                self.assertEqual(target.read_bytes(), b"original")
                self.assertEqual(source.read_bytes(), b"verified replacement")


if __name__ == "__main__":
    unittest.main()
