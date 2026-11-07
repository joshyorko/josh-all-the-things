"""Opt-in acceptance uses real RCC/JAT processes; no fake executable."""
import hashlib
import os
import subprocess
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'verify_portable_capsule.py'


class CandidateBoundaryTests(unittest.TestCase):
    def test_hash_mismatch_denies_before_creating_consumer_state(self):
        # A candidate substitution must fail before any environment command.
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory) / 'acceptance'
            result = subprocess.run(
                [sys.executable, str(SCRIPT), '--rcc', sys.executable,
                 '--rcc-sha256', '0' * 64, '--rcc-source', 'a' * 40,
                 '--archive', str(Path(directory) / 'unused.rcca'),
                 '--archive-sha256', '0' * 64, '--work', str(work)],
                capture_output=True, text=True, check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('candidate SHA256 mismatch', result.stderr)
            self.assertFalse(work.exists())

    def test_bad_source_identity_denies_before_creating_state(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory) / 'acceptance'
            result = subprocess.run(
                [sys.executable, str(SCRIPT), '--rcc', sys.executable,
                 '--rcc-sha256', hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
                 '--rcc-source', 'main', '--archive', str(Path(directory) / 'unused.rcca'),
                 '--archive-sha256', '0' * 64, '--work', str(work)],
                capture_output=True, text=True, check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('full RCC source commit required', result.stderr)
            self.assertFalse(work.exists())

    @unittest.skipUnless(os.environ.get('JAT_VERIFIED_CAPSULE_RCC'), 'opt-in exact candidate binary required')
    def test_real_signed_capsule(self):
        binary = Path(os.environ['JAT_VERIFIED_CAPSULE_RCC']).resolve()
        archive = Path(os.environ['JAT_VERIFIED_CAPSULE_ARCHIVE']).resolve()
        subprocess.run(
            [sys.executable, str(SCRIPT), '--rcc', str(binary),
             '--rcc-sha256', hashlib.sha256(binary.read_bytes()).hexdigest(),
             '--rcc-source', os.environ['JAT_VERIFIED_CAPSULE_SOURCE'],
             '--archive', str(archive), '--archive-sha256', os.environ['JAT_VERIFIED_CAPSULE_ARCHIVE_SHA256'],
             '--work', os.environ['JAT_VERIFIED_CAPSULE_WORK']], check=True,
        )


if __name__ == '__main__':
    unittest.main()
