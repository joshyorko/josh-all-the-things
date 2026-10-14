"""Regression guards for read-only qualification of immutable runtime releases."""

from pathlib import Path


WORKFLOWS = Path('.github/workflows')


def verifier():
    path = WORKFLOWS / 'verify-published-runtime.yml'
    assert path.is_file(), 'published verification needs its own read-only entrypoint'
    return path.read_text()


def test_published_verifier_has_no_publication_dependency_or_write_permissions():
    workflow = verifier()
    assert 'workflow_dispatch:' in workflow
    assert 'workflow_call:' in workflow
    assert 'contents: read' in workflow
    for forbidden in ('contents: write', 'packages: write', 'publish-jat-runtime', 'gh release create', 'gh release upload', 'gh release edit', 'env publish'):
        assert forbidden not in workflow
    assert 'needs: validate-release' in workflow
    assert 'JAT_RELEASE_TAG: ${{ inputs.release_tag }}' in workflow
    assert 'default: v0.1.12-jat-runtime' in workflow
    assert 'JAT_RELEASE_SOURCE_SHA: ${{ inputs.source_sha }}' in workflow
    assert 'JAT_VERIFIER_CODE_SHA: ${{ github.workflow_sha }}' in workflow
    assert 'release["immutable"] is True' in workflow
    assert 'release["draft"] is False' in workflow
    assert 'refs/tags/${JAT_RELEASE_TAG}^{}' in workflow
    assert 'test "$tag_sha" = "$JAT_RELEASE_SOURCE_SHA"' in workflow
    assert workflow.count('ref: ${{ env.JAT_RELEASE_SOURCE_SHA }}') == 2


def test_publisher_calls_the_same_verifier_instead_of_copying_it():
    workflow = (WORKFLOWS / 'windows-runtime.yml').read_text()
    assert 'uses: ./.github/workflows/verify-published-runtime.yml' in workflow
    assert 'needs: publish-jat-runtime' in workflow
    assert 'verify-published-windows:' not in workflow
    assert 'verify-published-linux:' not in workflow


def test_windows_exec_keeps_raw_child_stdout_separate_from_validated_receipts():
    workflow = verifier().split('  verify-published-windows:', 1)[1]
    commands = [line for line in workflow.splitlines() if '& $rcc --no-build env exec' in line]
    assert len(commands) == 7
    for command in commands:
        options, _ = command.split(' -- ', 1)
        assert '--artifact $artifact' in options
        assert '--inherit-streams --receipt-file' in options
        assert '--json' not in options
    for name in ('variables', 'hauler', 'haulerPython', 'doctor', 'build', 'restore', 'serve'):
        assert f'Assert-ExecutionReceipt ${name}Receipt' in workflow
    assert '$execution.artifactDigest -cne $artifact' in workflow
    assert '$execution.exitCode -ne 0' in workflow
    assert '$execution.verification.valid -ne $true' in workflow
    assert '$runtimeVariables = Get-Content -Raw $variablesPath | ConvertFrom-Json' in workflow
    assert 'taskkill.exe /PID $serve.Id' not in workflow
    assert 'process.wait(timeout=10)' in workflow


def test_verifier_retains_runtime_integrity_and_real_operation_gates():
    workflow = verifier()
    for required in (
        'receipt["archive"]["sha256"]', 'receipt["archive"]["size"]',
        '$receipt.archive.sha256', '$receipt.archive.size',
        '$acquired.artifactDigest -ne $receipt.artifact_digest',
        '$acquired.verification.valid -ne $true',
        '$runtimeVariables.conda_prefix', 'hauler.exe version',
        'r.startswith(p+os.sep)', 'doctor --json', 'build --folder',
        'restore --haul', '"jat.cli", "serve"',
        'joshs-all-the-things-workspace.tar.zst',
    ):
        assert required in workflow
