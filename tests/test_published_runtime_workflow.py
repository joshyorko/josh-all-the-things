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
    assert 'default: v0.1.15-jat-runtime' in workflow
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
        'validate_runtime_receipt.py', '--archive', '--receipt', '--source-sha', '--platform',
        '$acquired.artifactDigest -ne $receipt.artifact_digest',
        '$acquired.verification.valid -ne $true',
        '$runtimeVariables.conda_prefix', 'hauler.exe version',
        'verify_hauler_runtime.py', 'doctor --json', 'build --folder',
        'restore --haul', '"jat.cli", "serve"',
        'joshs-all-the-things-workspace.tar.zst',
    ):
        assert required in workflow


def test_receipt_validation_uses_trusted_tools_and_release_source_before_acquire():
    workflow = verifier()
    assert workflow.count('ref: ${{ env.JAT_VERIFIER_CODE_SHA }}') == 2
    assert workflow.count('shell: python') == 2
    assert workflow.count('runtime-verifier-requirements.txt') >= 2
    for platform, job in [('Linux', 'verify-published-linux'), ('Windows', 'verify-published-windows')]:
        body = workflow.split('  '+job+':', 1)[1]
        assert body.index('Stage trusted receipt and runtime validators') < body.index('ref: ${{ env.JAT_RELEASE_SOURCE_SHA }}')
        assert body.index('Validate the complete '+platform+' producer receipt') < body.index('env acquire')
        assert '--source-root' in body and '--source-sha' in body
        assert 'PYTHONPATH: ${{ runner.temp }}/jat-receipt-tools' in body


def test_linux_no_build_probe_uses_the_acquired_artifact_identity():
    workflow = verifier().split('  verify-published-linux:', 1)[1].split('  verify-published-windows:', 1)[0]
    assert '--no-build ht vars --robot "$PWD/robot.yaml"' not in workflow
    assert '--receipt-file "$RUNNER_TEMP/linux-release-no-build.json"' in workflow
    assert 'execution["verification"]["valid"] is not True' in workflow
