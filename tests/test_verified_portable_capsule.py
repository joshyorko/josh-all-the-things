"""Opt-in acceptance uses real RCC/JAT processes; no fake executable."""
import base64
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'verify_portable_capsule.py'
SPEC = importlib.util.spec_from_file_location('observe_capsule_packages', ROOT / 'scripts' / 'observe_capsule_packages.py')
OBSERVATION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(OBSERVATION)


class PackageObservationTests(unittest.TestCase):
    def test_hauler_gitversion_must_match_the_exact_pin(self):
        for version in ('v2.1.1', '2.1.1'):
            self.assertEqual(OBSERVATION.pinned_hauler_version(f'GitVersion: {version}\nGitCommit: abc'), '2.1.1')
        for output in ('GitVersion: v2.1.10', 'GitVersion: v12.1.1', 'GitVersion: v2.1.1-dev',
                       'unrelated output 2.1.1', 'GitVersion: v2.1.1\nGitVersion: v2.1.1'):
            with self.subTest(output=output), self.assertRaises(ValueError):
                OBSERVATION.pinned_hauler_version(output)

    def test_python_record_hashes_are_retained_and_compared_with_installed_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory)
            payload = prefix / 'payload.py'
            payload.write_bytes(b'original package bytes')
            recorded = base64.urlsafe_b64encode(hashlib.sha256(payload.read_bytes()).digest()).decode().rstrip('=')
            dist_info = prefix / 'fixture-1.0.dist-info'
            dist_info.mkdir()
            (dist_info / 'METADATA').write_text('Name: fixture\nVersion: 1.0\n')
            (dist_info / 'RECORD').write_text(f'payload.py,sha256={recorded},22\n')
            distribution = importlib.metadata.Distribution.at(dist_info)
            observed = OBSERVATION.python_packages(prefix, [distribution])[0]
            self.assertEqual((observed['name'], observed['version']), ('fixture', '1.0'))
            self.assertEqual(observed['fileHashes'][0]['recordedHash']['value'], recorded)
            self.assertTrue(observed['fileHashes'][0]['matchesRecordedHash'])
            payload.write_bytes(b'changed installed bytes')
            changed = OBSERVATION.python_packages(prefix, [distribution])[0]['fileHashes'][0]
            self.assertEqual(changed['recordedHash']['value'], recorded)
            self.assertEqual(changed['observedSHA256'], hashlib.sha256(payload.read_bytes()).hexdigest())
            self.assertFalse(changed['matchesRecordedHash'])

    def test_conda_archive_and_installed_hashes_keep_their_distinct_meanings(self):
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory)
            payload = prefix / 'payload'
            payload.write_bytes(b'installed bytes')
            installed = hashlib.sha256(payload.read_bytes()).hexdigest()
            conda_meta = prefix / 'conda-meta'
            conda_meta.mkdir()
            package = {'name': 'fixture', 'version': '1.0', 'build': '0', 'sha256': 'a' * 64, 'md5': 'b' * 32,
                       'url': 'https://user:private@example.invalid/package',
                       'paths_data': {'paths': [{'_path': 'payload', 'sha256': 'c' * 64, 'sha256_in_prefix': installed}]}}
            (conda_meta / 'fixture.json').write_text(json.dumps(package))
            observed = OBSERVATION.conda_packages(prefix)[0]
            self.assertEqual(observed['archiveHashes'], {'sha256': 'a' * 64, 'md5': 'b' * 32})
            self.assertEqual(observed['fileHashes'][0]['recordedHashes'], {'sha256': 'c' * 64, 'sha256_in_prefix': installed})
            self.assertEqual(observed['fileHashes'][0]['observedSHA256'], installed)
            self.assertTrue(observed['fileHashes'][0]['matchesRecordedHash'])
            self.assertNotIn('private', json.dumps(observed))
            (conda_meta / 'unknown.json').write_text(json.dumps({'name': 'unknown', 'version': '1.0'}))
            unknown = OBSERVATION.conda_packages(prefix)[1]
            self.assertEqual(unknown['archiveHashes'], {})
            self.assertEqual(unknown['fileHashes'], [])

    def test_missing_or_outside_environment_files_have_no_observed_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory) / 'environment'
            prefix.mkdir()
            outside = Path(directory) / 'outside'
            outside.write_bytes(b'not environment owned')
            self.assertIsNone(OBSERVATION.installed_sha256(outside, prefix))
            self.assertIsNone(OBSERVATION.installed_sha256(prefix / 'missing', prefix))


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
