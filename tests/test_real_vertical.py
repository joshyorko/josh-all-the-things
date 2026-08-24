from shutil import which

import pytest

from jat.models import BuildRequest, RestoreRequest
from jat.services import JATService

pytestmark = pytest.mark.skipif(
    not which("hauler") or not (which("gtar") or which("tar")),
    reason="real Hauler and GNU tar are required",
)


def test_real_hauler_gnu_tar_build_restore_bytes_modes_and_layout(tmp_path):
    source = tmp_path / "synthetic-project"
    nested = source / "bin"
    nested.mkdir(parents=True)
    regular = source / "README.md"
    executable = nested / "tool.sh"
    regular.write_bytes(b"synthetic workspace\n")
    executable.write_bytes(b"#!/usr/bin/env bash\nprintf 'synthetic tool\\n'\n")
    regular.chmod(0o640)
    executable.chmod(0o750)
    brew = tmp_path / "synthetic-homebrew-recovery"
    brew.mkdir()
    brewfile = brew / "Brewfile"
    brewfile.write_text('brew "hauler"\n')
    brewfile.chmod(0o600)
    haul = tmp_path / "workspace-haul.tar.zst"
    restored = tmp_path / "restored"

    service = JATService(producer_version="synthetic-test")
    built = service.build(BuildRequest(folder=source, output=haul, brew=brew))
    assert built.success, built.diagnostics
    hydrated = service.restore(RestoreRequest(haul=haul, destination=restored))
    assert hydrated.success, hydrated.diagnostics

    restored_root = restored / "workspace" / source.name
    assert (restored_root / "README.md").read_bytes() == regular.read_bytes()
    assert (restored_root / "bin" / "tool.sh").read_bytes() == executable.read_bytes()
    assert (restored_root / "README.md").stat().st_mode & 0o777 == 0o640
    assert (restored_root / "bin" / "tool.sh").stat().st_mode & 0o777 == 0o750
    restored_brewfile = restored / "homebrew-recovery" / "Brewfile"
    assert restored_brewfile.read_bytes() == brewfile.read_bytes()
    assert restored_brewfile.stat().st_mode & 0o777 == 0o600
