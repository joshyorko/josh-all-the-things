from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from jat.capture import select_workspace
from jat.cli import main
from jat.models import BuildRequest, OperationResult, RestoreRequest
from jat.services import JATService


@pytest.mark.parametrize("rule", ["", "/root", "../x", "a/../b", "a//b", "./a", "a/", "a\\b", "C:/a", "x\ny"])
def test_invalid_rules(rule):
    with pytest.raises(ValidationError):
        BuildRequest(folder="x", output="y", workspace_exclusions=[rule])


def workspace(root):
    source = root / "project"
    source.mkdir()
    for name, data in {
        "main.py": b"print('keep')\n", "package-lock.json": b"lock\n", ".env": b"config\n",
        "database.db": b"user data\x00", "[cache] space/data": b"omit", "cacheX/data": b"keep",
        ".git/HEAD": b"ref: refs/heads/main\n", "nested/.git": b"gitdir: /unavailable/private\n",
        "build/out": b"generated", "uploads/image": b"image\x00",
    }.items():
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    (source / "generated").mkdir()
    (source / "generated/python").symlink_to("/unavailable/python")
    return source


def test_literal_selection_and_policy_difference(tmp_path):
    source = workspace(tmp_path)
    selected = select_workspace(source, ["[cache] space", "generated"], "source-only")
    assert selected.complete and not selected.findings
    assert "cacheX/data" in selected.paths
    assert "[cache] space/data" not in selected.paths
    assert not any(".git" in Path(path).parts for path in selected.paths)
    full = select_workspace(source, ["generated"], "full-repository")
    assert ".git/HEAD" in full.paths
    assert selected.summary("source-only", [])['policy_sha256'] != full.summary("full-repository", [])['policy_sha256']


def test_excluded_symlink_target_and_retained_external_link_fail(tmp_path):
    source = workspace(tmp_path)
    (source / "alias").symlink_to("build/out")
    assert select_workspace(source, ["generated", "build"], "source-only").findings
    assert select_workspace(source, [], "source-only").findings


def test_incomplete_and_unreadable_scan_are_not_clean(tmp_path):
    source = workspace(tmp_path)
    limited = select_workspace(source, [], "source-only", limit=1)
    assert not limited.complete and limited.findings
    with patch("jat.capture.os.scandir", side_effect=PermissionError):
        unreadable = select_workspace(source, [], "source-only")
    assert not unreadable.complete and unreadable.findings


def test_bounded_summary(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    for number in range(101):
        (source / str(number)).touch()
    summary = select_workspace(source, [], "source-only").summary("source-only", [])
    assert summary['complete'] and summary['summary_truncated']
    assert len(summary['included_sample']) == 100


def test_cli_request_parity(capsys):
    class Service:
        def build(self, request):
            assert request.workspace_exclusions == ["literal [x]"]
            assert request.capture_mode == "source-only" and request.dry_run
            return OperationResult(operation="build", success=True, exit_status=0, producer_version="test")
    assert main(["build", "--folder", "x", "--output", "y", "--workspace-exclude", "literal [x]",
                 "--capture-mode", "source-only", "--dry-run", "--json"], service=Service()) == 0


def test_dry_run_no_output_and_no_archive(tmp_path):
    source = workspace(tmp_path)
    result = JATService().build(BuildRequest(folder=source, output=tmp_path / "dry.haul", dry_run=True,
        workspace_exclusions=["generated"], capture_mode="source-only"))
    assert result.success and result.complete
    assert not (tmp_path / "dry.haul").exists()
    assert result.capture_policy["version"] == 1


def test_real_filtered_capsule_round_trip(tmp_path):
    source = workspace(tmp_path)
    before = {p.relative_to(source).as_posix(): p.read_bytes() for p in source.rglob('*') if p.is_file()}
    exclusions = ["generated", "[cache] space", "build"]
    service = JATService(producer_version="filtered-capture-test")
    haul = tmp_path / "filtered.haul"
    result = service.build(BuildRequest(folder=source, output=haul, workspace_exclusions=exclusions,
                                       capture_mode="source-only"))
    assert result.success, result.diagnostics
    assert before == {p.relative_to(source).as_posix(): p.read_bytes() for p in source.rglob('*') if p.is_file()}
    source.rename(tmp_path / "original-unavailable")
    destination = tmp_path / "restore"
    restored = service.restore(RestoreRequest(haul=haul, destination=destination))
    assert restored.success, restored.diagnostics
    expected = {k: v for k, v in before.items() if ".git" not in Path(k).parts
                and not any(k == rule or k.startswith(rule + '/') for rule in exclusions)}
    restored_source = destination / 'workspace' / 'project'
    actual = {p.relative_to(restored_source).as_posix(): p.read_bytes() for p in restored_source.rglob('*') if p.is_file()}
    assert actual == expected
    assert not any(p.name == '.git' for p in destination.rglob('*'))
    assert result.sha256 and result.capture_policy['complete']


def test_cancelled_scan_and_failed_dry_run(tmp_path):
    source = workspace(tmp_path)
    with patch("jat.capture.os.scandir", side_effect=KeyboardInterrupt):
        result = JATService().build(BuildRequest(folder=source, output=tmp_path / "none.haul", dry_run=True))
    assert not result.success and result.complete is False
    assert result.capture_policy['findings'] == ['workspace scan cancelled']


def test_retained_link_rejected_before_archive(tmp_path):
    source = workspace(tmp_path)
    with patch('jat.archive.ArchiveAdapter.create') as create:
        result = JATService().build(BuildRequest(folder=source, output=tmp_path / 'none.haul', capture_mode='source-only'))
    assert not result.success
    create.assert_not_called()


def test_rule_summary_bounded():
    from jat.capture import Selection
    summary = Selection().summary('source-only', [f'item/{n}' for n in range(101)])
    assert len(summary['workspace_exclusions']) == 100
    assert summary['exclusion_rules_truncated']


def test_windows_filtered_archive_omits_excluded_unsafe_link(tmp_path):
    pytest.importorskip('zstandard')
    from jat.archive import ArchiveAdapter
    from jat.process import ProcessRunner
    from jat.safety import validate_archive_members
    source = workspace(tmp_path)
    selection = select_workspace(source, ['generated', '[cache] space'], 'source-only')
    archive = tmp_path / 'selected.tar.zst'
    backend = ArchiveAdapter(ProcessRunner(), platform_name='windows')
    backend.create(source, archive, selected_paths=selection.paths)
    members = backend.members(archive)
    validate_archive_members(members)
    assert not any('/.git' in member.name or '/generated' in member.name for member in members)
    backend.extract(archive, tmp_path / 'extracted')
    assert (tmp_path / 'extracted/project/database.db').read_bytes() == b'user data\x00'


def test_policy_provenance_is_inspectable(tmp_path):
    from jat.models import ExtractRequest, InspectRequest
    source = workspace(tmp_path)
    service = JATService()
    haul = tmp_path / 'policy.haul'
    built = service.build(BuildRequest(folder=source, output=haul, capture_mode='source-only',
                                       workspace_exclusions=['generated']))
    assert built.success, built.diagnostics
    inventory = service.inspect(InspectRequest(haul=haul))
    assert inventory.success
    reference = 'hauler/jat-workspace-capture-policy.json:latest'
    assert reference in [item.reference for item in inventory.inventory]
    extracted = service.extract(ExtractRequest(haul=haul, reference=reference, destination=tmp_path / 'policy'))
    assert extracted.success, extracted.diagnostics
    import json
    files = list((tmp_path / 'policy').rglob('jat-workspace-capture-policy.json'))
    policy = json.loads(files[0].read_text())
    assert policy['version'] == 1
    assert policy['workspace_exclusions'] == ['generated']
    assert policy['policy_sha256'] == built.capture_policy['policy_sha256']


def test_time_bound_and_invalid_mode(tmp_path):
    source = workspace(tmp_path)
    selection = select_workspace(source, [], 'source-only', timeout=0)
    assert not selection.complete
    assert selection.findings == ['workspace scan time limit reached']
    with pytest.raises(ValueError, match='unsupported'):
        select_workspace(source, [], 'unknown')


def test_rcc_json_request_matches_cli_contract(tmp_path):
    import json

    from jat.io import load_request
    payload = {'folder': 'source', 'output': 'out.haul', 'capture_mode': 'source-only',
               'workspace_exclusions': ['literal [x]', 'literal [x]'], 'dry_run': True}
    request_file = tmp_path / 'request.json'
    request_file.write_text(json.dumps(payload))
    assert load_request(BuildRequest, ['--json-input', str(request_file)]) == BuildRequest(**payload)
