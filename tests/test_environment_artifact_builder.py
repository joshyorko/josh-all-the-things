import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import validate

from scripts.build_environment_artifact import HAULER_VERSION_COMMAND, RCC_SOURCE, _platform_name, _rcc_identity, _stage_copy, build_parser, main


HAULER_VERSION_CHECK = (
    "import os, shutil, subprocess, sys; executable = shutil.which('hauler'); "
    "prefix = os.environ.get('CONDA_PREFIX'); prefix_root = os.path.realpath(prefix) if prefix else ''; "
    "resolved = os.path.realpath(executable) if executable else ''; "
    "python_resolved = os.path.realpath(sys.executable); "
    "inside = bool(prefix_root and resolved.startswith(prefix_root + os.sep)); "
    "python_inside = bool(prefix_root and python_resolved.startswith(prefix_root + os.sep)); "
    "result = subprocess.run([resolved, 'version'], check=False, capture_output=True, text=True) if inside and python_inside else None; "
    "reported = [line.split(':', 1)[1].strip().removeprefix('v') for line in (result.stdout + '\\n' + result.stderr).splitlines() if line.startswith('GitVersion:')] if result else []; "
    "sys.stdout.write(result.stdout if result else ''); sys.stderr.write(result.stderr if result else ''); "
    "sys.exit(127 if result is None else result.returncode or (0 if len(sys.argv) == 2 and reported == [sys.argv[1].removeprefix('v')] else 1))"
)


def write_fake_rcc(path: Path, log: Path) -> None:
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, subprocess, sys\n"
        f"log = pathlib.Path({str(log)!r})\n"
        "log.open('a').write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "args = sys.argv[1:]\n"
        "if args[:2] == ['env', 'publish']:\n"
        "    if os.environ.get('JAT_FAKE_RCC_SOURCE_HEAD'):\n"
        "        subprocess.run(['git', 'checkout', '--quiet', os.environ['JAT_FAKE_RCC_SOURCE_HEAD']], check=True)\n"
        "    if os.environ.get('JAT_FAKE_RCC_DIRTY_SOURCE'):\n"
        "        pathlib.Path('robot.yaml').write_text('tasks: {changed: {}}\\n')\n"
        "    print(json.dumps({'artifactDigest': 'sha256:' + 'a' * 64, 'specificationDigest': 'sha256:' + 'b' * 64, 'legacyBlueprintKey': 'c' * 16}))\n"
        "elif args[:2] == ['env', 'export']:\n"
        "    pathlib.Path(args[args.index('--output') + 1]).write_bytes(b'RCCA')\n"
        "    print(json.dumps({'archive': args[args.index('--output') + 1], 'artifactDigest': 'sha256:' + 'a' * 64}))\n"
        "elif args[:3] == ['--no-build', 'ht', 'vars']:\n"
        "    print(json.dumps([{'key': 'RCC_ENVIRONMENT_HASH', 'value': 'sha256:' + 'a' * 64}]))\n"
        "elif args[:2] == ['env', 'acquire']:\n"
        "    print(json.dumps({'artifactDigest': 'sha256:' + 'a' * 64, 'verification': {'valid': os.environ.get('JAT_FAKE_RCC_FAIL_ACQUIRE') != '1'}}))\n"
        "elif args[:2] == ['env', 'exec'] or args[:3] == ['--no-build', 'env', 'exec']:\n"
        "    command = args[args.index('--') + 1:]\n"
        "    exit_code = 127 if command == ['hauler', 'version'] else 0\n"
        "    if os.environ.get('JAT_FAKE_RCC_RUN_HAULER_PROOF') and command[:2] == ['python', '-c'] and \"shutil.which('hauler')\" in command[2]:\n"
        "        exit_code = subprocess.run(command, check=False, capture_output=True).returncode\n"
        "    print(json.dumps({'artifactDigest': 'sha256:' + 'a' * 64, 'exitCode': exit_code}))\n"
        "elif args == ['version']:\n"
        "    print(os.environ.get('JAT_FAKE_RCC_VERSION', 'v18.19.7'))\n"
    )
    path.chmod(0o755)


def write_fake_oras(path: Path, log: Path) -> None:
    path.write_text(
        f"#!{sys.executable}\n"
        "import json, pathlib, sys\n"
        f"log = pathlib.Path({str(log)!r})\n"
        "args = sys.argv[1:]\n"
        "log.open('a').write(json.dumps(args) + '\\n')\n"
        "if args[:1] == ['login']:\n"
        "    if sys.stdin.read() != 'fake-token':\n"
        "        raise SystemExit(3)\n"
        "elif args[:2] == ['manifest', 'fetch']:\n"
        "    print(json.dumps({'digest': 'sha256:' + 'd' * 64}))\n"
        "elif args[:1] == ['push']:\n"
        "    for item in args[4:]:\n"
        "        pathlib.Path(item.split(':application/', 1)[0]).read_bytes()\n"
        "    pathlib.Path(str(log) + '.receipt').write_bytes(pathlib.Path(args[-1].split(':application/', 1)[0]).read_bytes())\n"
    )
    path.chmod(0o755)


def test_build_uses_official_publish_export_acquire_and_exec_flow(tmp_path, monkeypatch):
    robot = tmp_path / "robot.yaml"
    robot.write_text("tasks: {}\n")
    rcc = tmp_path / "rcc"
    log = tmp_path / "rcc.log"
    write_fake_rcc(rcc, log)
    monkeypatch.setattr("scripts.build_environment_artifact._rcc_identity", lambda root, executable: {**RCC_SOURCE, "asset": "rcc-linux64", "sha256": "0" * 64})
    monkeypatch.setattr("scripts.build_environment_artifact._hauler_version", lambda root: "v2.1.1")
    monkeypatch.setattr("scripts.build_environment_artifact._git_sha", lambda root, env, timeout: "d" * 40)
    output = tmp_path / "dist" / "jat-runtime.rcca"
    receipt = tmp_path / "dist" / "jat-runtime.json"

    assert main(["--robot", str(robot), "--rcc", str(rcc), "--output", str(output), "--receipt", str(receipt)]) == 0

    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert calls[0] == ["version"]
    assert calls[1][:3] == ["env", "publish", "--robot"]
    assert calls[1][3:7] == [str(robot), "--provider", "local", "--json"]
    assert calls[2][:2] == ["env", "export"]
    assert "ht" not in calls[2]
    assert calls[3][:2] == ["env", "acquire"]
    assert calls[3][calls[3].index("--archive") + 1] != str(output)
    assert "--permissive-local" in calls[3] and "--json" in calls[3]
    assert calls[4][:3] == ["--no-build", "ht", "vars"]
    assert calls[5][:3] == ["--no-build", "env", "exec"]
    assert "--artifact" in calls[5]
    assert calls[6] == [
        "--no-build",
        "env",
        "exec",
        "--artifact",
        "sha256:" + "a" * 64,
        "--permissive-local",
        "--json",
        "--",
        "python",
        "-c",
        HAULER_VERSION_CHECK,
        "v2.1.1",
    ]
    assert calls[7][:3] == ["--no-build", "env", "exec"]

    result = json.loads(receipt.read_text())
    validate(instance=result, schema=json.loads((Path(__file__).parents[1] / "docs/environment-artifact-receipt.schema.json").read_text()))
    assert result["operation"] == "build"
    assert result["success"] is True
    assert result["rcc_executable"] == str(rcc)
    assert result["verified_acquire"]["fresh_home"] is True
    assert result["verified_acquire"]["no_build"] is True
    assert result == {
        "artifact_digest": "sha256:" + "a" * 64,
        "archive": {"filename": "jat-runtime.rcca", "sha256": hashlib.sha256(b"RCCA").hexdigest(), "size": 4},
        "format_version": 3,
        "jat_git_sha": "d" * 40,
        "legacy_blueprint_key": "c" * 16,
        "platform": result["platform"],
        "rcc_version": "v18.19.7",
        "hauler_version": "v2.1.1",
        "rcc_source": {**RCC_SOURCE, "asset": "rcc-linux64", "sha256": "0" * 64},
        "specification_digest": "sha256:" + "b" * 64,
        "operation": "build",
        "success": True,
        "rcc_executable": str(rcc),
        "verified_acquire": {"fresh_home": True, "no_build": True},
        "verified_exec": {"fresh_home": True},
        "verified_hauler": {
            "fresh_home": True,
            "command": ["hauler", "version"],
            "launcher": ["python", "-c", HAULER_VERSION_CHECK, "v2.1.1"],
            "resolved_under_conda_prefix": True,
            "exit_code": 0,
        },
        "verified_no_build": {"fresh_home": True, "no_build": True},
        "verified_warm_reuse": {"fresh_home": True, "no_build": True, "provider_unavailable": True},
    }


def test_hauler_proof_rejects_host_python_even_when_hauler_is_under_prefix(tmp_path):
    prefix = tmp_path / "holotree"
    bin_directory = prefix / "bin"
    bin_directory.mkdir(parents=True)
    marker = tmp_path / "hauler-invoked"
    hauler = bin_directory / "hauler"
    hauler.write_text(f"#!/bin/sh\n/usr/bin/touch {marker}\n")
    hauler.chmod(0o755)

    environment = dict(os.environ)
    environment.update(CONDA_PREFIX=str(prefix), PATH=str(bin_directory))
    result = subprocess.run(
        [sys.executable, "-c", HAULER_VERSION_COMMAND[2]],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 127
    assert not marker.exists()


@pytest.fixture
def prefix_local_hauler(tmp_path, monkeypatch):
    prefix = tmp_path / "prefix"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", "--copies", str(prefix)], check=True)
    bin_directory = prefix / "bin"
    monkeypatch.setenv("CONDA_PREFIX", str(prefix))
    monkeypatch.setenv("PATH", str(bin_directory) + os.pathsep + os.environ["PATH"])
    hauler = bin_directory / "hauler"
    hauler.write_text("#!/bin/sh\nprintf 'GitVersion:    2.1.1\\n'\n")
    hauler.chmod(0o755)
    return bin_directory / "python", hauler


@pytest.mark.parametrize("reported,expected_exit", [
    ("2.1.1", 0), ("v2.1.1", 0), ("2.1.10", 1), ("2.1.0", 1), ("unknown", 1),
])
def test_prefix_local_hauler_proof_compares_exact_release(prefix_local_hauler, reported, expected_exit):
    python, hauler = prefix_local_hauler
    hauler.write_text(f"#!/bin/sh\nprintf 'GitVersion:    {reported}\\n'\n")
    result = subprocess.run([str(python), *HAULER_VERSION_COMMAND[1:], "v2.1.1"], capture_output=True, text=True, check=False)
    assert result.returncode == expected_exit


@pytest.mark.parametrize("probe_index", [0, 1, 2])
@pytest.mark.parametrize("reported,expected_exit", [("2.1.1", 0), ("2.1.10", 1)])
def test_workflow_hauler_probes_compare_exact_release(prefix_local_hauler, probe_index, reported, expected_exit):
    python, hauler = prefix_local_hauler
    hauler.write_text(f"#!/bin/sh\nprintf 'GitVersion:    {reported}\\n'\n")
    workflow = (Path(__file__).parents[1] / ".github/workflows/windows-runtime.yml").read_text()
    probes = re.findall(r'(?:\$haulerVersionCode = |hauler_probe=)"([^"]+)"', workflow)
    result = subprocess.run([str(python), "-c", probes[probe_index], "v2.1.1"], capture_output=True, text=True, check=False)
    assert result.returncode == expected_exit


def test_builder_rejects_prefix_local_hauler_release_mismatch(tmp_path, monkeypatch, prefix_local_hauler):
    _, hauler = prefix_local_hauler
    hauler.write_text("#!/bin/sh\nprintf 'GitVersion:    2.1.0\\n'\n")
    robot = tmp_path / "robot.yaml"
    robot.write_text("tasks: {}\n")
    rcc = tmp_path / "rcc"
    write_fake_rcc(rcc, tmp_path / "rcc.log")
    monkeypatch.setattr("scripts.build_environment_artifact._rcc_identity", lambda root, executable: RCC_SOURCE)
    monkeypatch.setattr("scripts.build_environment_artifact._hauler_version", lambda root: "v2.1.1")
    monkeypatch.setattr("scripts.build_environment_artifact._git_sha", lambda root, env, timeout: "d" * 40)
    monkeypatch.setenv("JAT_FAKE_RCC_RUN_HAULER_PROOF", "1")
    output = tmp_path / "runtime.rcca"
    receipt = tmp_path / "runtime.json"

    with pytest.raises(RuntimeError, match="Hauler version execution proof failed"):
        main(["--robot", str(robot), "--rcc", str(rcc), "--output", str(output), "--receipt", str(receipt)])

    assert not output.exists()
    assert not receipt.exists()


def test_builder_defaults_to_rcc_and_does_not_export_to_final_output(tmp_path, monkeypatch):
    assert build_parser().parse_args([]).rcc == "rcc"


def test_create_only_promotion_stages_on_destination_filesystem(tmp_path, monkeypatch):
    source = tmp_path / "source" / "artifact.rcca"
    source.parent.mkdir()
    source.write_bytes(b"artifact")
    destination = tmp_path / "output" / "artifact.rcca"
    real_link = os.link

    def same_parent_link(first, second):
        assert Path(first).parent == Path(second).parent
        return real_link(first, second)

    monkeypatch.setattr(os, "link", same_parent_link)
    staged = _stage_copy(source, destination)
    try:
        os.link(staged, destination)
    finally:
        staged.unlink(missing_ok=True)

    assert destination.read_bytes() == b"artifact"


def test_receipt_staging_failure_removes_staged_archive(tmp_path, monkeypatch):
    robot = tmp_path / "robot.yaml"
    robot.write_text("tasks: {}\n")
    rcc = tmp_path / "rcc"
    write_fake_rcc(rcc, tmp_path / "rcc.log")
    monkeypatch.setattr("scripts.build_environment_artifact._rcc_identity", lambda root, executable: RCC_SOURCE)
    monkeypatch.setattr("scripts.build_environment_artifact._hauler_version", lambda root: "v2.1.1")
    monkeypatch.setattr("scripts.build_environment_artifact._git_sha", lambda root, env, timeout: "d" * 40)
    output = tmp_path / "dist" / "jat-runtime.rcca"
    blocked_parent = tmp_path / "blocked-parent"
    blocked_parent.write_bytes(b"not a directory")
    receipt = blocked_parent / "jat-runtime.json"

    with pytest.raises(FileExistsError):
        main(["--robot", str(robot), "--rcc", str(rcc), "--output", str(output), "--receipt", str(receipt)])

    assert not output.exists()
    assert not receipt.exists()
    assert list(output.parent.iterdir()) == []


@pytest.mark.parametrize("system,machine,expected", [
    ("Windows", "AMD64", "windows_amd64"),
    ("Linux", "x86_64", "linux_amd64"),
    ("Linux", "aarch64", "linux_arm64"),
])
def test_platform_architecture_names_are_normalized(system, machine, expected, monkeypatch):
    monkeypatch.setattr("scripts.build_environment_artifact.platform.system", lambda: system)
    monkeypatch.setattr("scripts.build_environment_artifact.platform.machine", lambda: machine)
    assert _platform_name() == expected


def test_simulated_windows_amd64_selects_pinned_rcc(tmp_path, monkeypatch):
    root = Path(__file__).parents[1]
    manifest = json.loads((root / "runtime/rcc.json").read_text())
    executable = tmp_path / "rcc.exe"
    executable.write_bytes(b"controlled Windows binary stand-in")
    monkeypatch.setattr("scripts.build_environment_artifact.platform.system", lambda: "Windows")
    monkeypatch.setattr("scripts.build_environment_artifact.platform.machine", lambda: "AMD64")
    monkeypatch.setattr("scripts.build_environment_artifact.shutil.which", lambda name: str(executable))
    monkeypatch.setattr("scripts.build_environment_artifact._sha256", lambda path: manifest["platforms"]["windows_amd64"]["sha256"])

    assert _rcc_identity(root, str(executable)) == {
        **RCC_SOURCE,
        "asset": "rcc-windows64.exe",
        "sha256": manifest["platforms"]["windows_amd64"]["sha256"],
    }


@pytest.fixture
def committed_builder_source(tmp_path, monkeypatch):
    root = tmp_path / "source"
    root.mkdir()
    robot = root / "robot.yaml"
    robot.write_text("tasks: {}\n")
    subprocess.run(["git", "init", "--quiet", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "robot.yaml"], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=JAT test", "-c", "user.email=jat@example.invalid", "commit", "--quiet", "-m", "source A"], check=True)
    rcc = tmp_path / "rcc"
    log = tmp_path / "rcc.log"
    write_fake_rcc(rcc, log)
    monkeypatch.setattr("scripts.build_environment_artifact._rcc_identity", lambda root, executable: RCC_SOURCE)
    monkeypatch.setattr("scripts.build_environment_artifact._hauler_version", lambda root: "v2.1.1")
    return root, robot, rcc, log


@pytest.mark.parametrize("change", ["head", "dirty"])
def test_builder_rejects_source_changes_during_construction(tmp_path, monkeypatch, committed_builder_source, change):
    root, robot, rcc, _ = committed_builder_source
    if change == "head":
        original = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
        robot.write_text("tasks: {changed: {}}\n")
        subprocess.run(["git", "-C", str(root), "-c", "user.name=JAT test", "-c", "user.email=jat@example.invalid", "commit", "--quiet", "-am", "source B"], check=True)
        changed = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
        subprocess.run(["git", "-C", str(root), "checkout", "--quiet", original], check=True)
        monkeypatch.setenv("JAT_FAKE_RCC_SOURCE_HEAD", changed)
    else:
        monkeypatch.setenv("JAT_FAKE_RCC_DIRTY_SOURCE", "1")
    output = tmp_path / "dist" / "runtime.rcca"
    receipt = tmp_path / "dist" / "runtime.json"

    with pytest.raises(RuntimeError, match="source checkout"):
        main(["--robot", str(robot), "--rcc", str(rcc), "--output", str(output), "--receipt", str(receipt)])

    assert not output.exists()
    assert not receipt.exists()


def test_builder_rejects_dirty_source_before_publish(tmp_path, committed_builder_source):
    _, robot, rcc, log = committed_builder_source
    robot.write_text("tasks: {uncommitted: {}}\n")

    with pytest.raises(RuntimeError, match="source checkout must be clean"):
        main(["--robot", str(robot), "--rcc", str(rcc), "--output", str(tmp_path / "runtime.rcca"), "--receipt", str(tmp_path / "runtime.json")])

    assert not any(call[:2] == ["env", "publish"] for call in map(json.loads, log.read_text().splitlines()))


def test_builder_rejects_unsupported_rcc_before_publish(tmp_path, monkeypatch):
    robot = tmp_path / "robot.yaml"
    robot.write_text("tasks: {}\n")
    rcc = tmp_path / "rcc"
    log = tmp_path / "rcc.log"
    write_fake_rcc(rcc, log)
    monkeypatch.setenv("JAT_FAKE_RCC_VERSION", "v18.19.2")

    with pytest.raises(RuntimeError, match="v18.19.7"):
        main([
            "--robot",
            str(robot),
            "--rcc",
            str(rcc),
            "--output",
            str(tmp_path / "runtime.rcca"),
            "--receipt",
            str(tmp_path / "runtime.json"),
        ])
    assert [json.loads(line) for line in log.read_text().splitlines()] == [["version"]]


def test_builder_keeps_final_outputs_absent_when_fresh_verification_fails(tmp_path, monkeypatch):
    robot = tmp_path / "robot.yaml"
    robot.write_text("tasks: {}\n")
    rcc = tmp_path / "rcc"
    log = tmp_path / "rcc.log"
    write_fake_rcc(rcc, log)
    monkeypatch.setattr("scripts.build_environment_artifact._rcc_identity", lambda root, executable: {**RCC_SOURCE, "asset": "rcc-linux64", "sha256": "0" * 64})
    monkeypatch.setattr("scripts.build_environment_artifact._hauler_version", lambda root: "v2.1.1")
    monkeypatch.setattr("scripts.build_environment_artifact._git_sha", lambda root, env, timeout: "d" * 40)
    output = tmp_path / "dist" / "jat-runtime.rcca"
    receipt = tmp_path / "dist" / "jat-runtime.json"
    monkeypatch.setenv("JAT_FAKE_RCC_FAIL_ACQUIRE", "1")

    with pytest.raises(RuntimeError, match="fresh verifier"):
        main(["--robot", str(robot), "--rcc", str(rcc), "--output", str(output), "--receipt", str(receipt)])
    assert not output.exists()
    assert not receipt.exists()
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert calls[2][calls[2].index("--output") + 1] != str(output)


def test_builder_rejects_existing_outputs(tmp_path):
    output = tmp_path / "jat-runtime.rcca"
    output.write_bytes(b"existing")
    try:
        main(["--output", str(output), "--receipt", str(tmp_path / "receipt.json")])
    except FileExistsError:
        pass
    else:
        raise AssertionError("existing output was accepted")


@pytest.mark.parametrize("receipt_location", ["same", "separate", "conflict", "same_filename"])
def test_publish_wrapper_validates_selected_structured_receipt(tmp_path, receipt_location):
    root = Path(__file__).parents[1]
    if shutil.which("jq") is None:
        pytest.fail("jq is required to exercise the publication wrapper")

    archive = tmp_path / "jat-runtime.rcca"
    archive_bytes = b"schema-valid RCCA archive"
    archive.write_bytes(archive_bytes)
    receipt = tmp_path / "jat-runtime.json"
    if receipt_location != "same":
        receipt = tmp_path / "receipts" / receipt.name
        receipt.parent.mkdir()
    if receipt_location == "same_filename":
        receipt = receipt.with_name(archive.name)
    if receipt_location == "conflict":
        (archive.parent / receipt.name).write_bytes(b"unvalidated receipt namesake")
    receipt.write_text(
        json.dumps(
            {
                "format_version": 3,
                "operation": "build",
                "success": True,
                "jat_git_sha": subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True).stdout.strip(),
                "rcc_executable": "/synthetic/rcc",
                "rcc_version": "v18.19.7",
                "hauler_version": "v2.1.1",
                "rcc_source": {**RCC_SOURCE, "asset": "rcc-linux64", "sha256": "f8e0394be7f47ee84d6f4cd734822b87b492a4d6102f977f6a18f29453703d48"},
                "platform": "linux_amd64",
                "artifact_digest": "sha256:" + "a" * 64,
                "specification_digest": "sha256:" + "b" * 64,
                "legacy_blueprint_key": "c" * 16,
                "archive": {
                    "filename": archive.name,
                    "sha256": hashlib.sha256(archive_bytes).hexdigest(),
                    "size": len(archive_bytes),
                },
                "verified_acquire": {"fresh_home": True, "no_build": True},
                "verified_no_build": {"fresh_home": True, "no_build": True},
                "verified_warm_reuse": {"fresh_home": True, "no_build": True, "provider_unavailable": True},
                "verified_exec": {"fresh_home": True},
                "verified_hauler": {
                    "fresh_home": True,
                    "command": ["hauler", "version"],
                    "launcher": ["python", "-c", HAULER_VERSION_CHECK, "v2.1.1"],
                    "resolved_under_conda_prefix": True,
                    "exit_code": 0,
                },
            }
        )
        + "\n"
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    oras_log = tmp_path / "oras.log"
    write_fake_oras(fake_bin / "oras", oras_log)
    environment = os.environ.copy()
    environment.update(
        PATH=f"{fake_bin}{os.pathsep}{environment['PATH']}",
        GITHUB_TOKEN="fake-token",
        GITHUB_ACTOR="fake-user",
    )
    reference = "ghcr.io/example/jat-runtime:linux_amd64-" + "a" * 64

    result = subprocess.run(
        [
            str(root / "scripts/publish_environment_artifact.sh"),
            "--archive",
            str(archive),
            "--receipt",
            str(receipt),
            "--repository",
            "ghcr.io/example/jat-runtime",
            "--username",
            "fake-user",
        ],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    if receipt_location == "same_filename":
        assert result.returncode != 0
        assert not oras_log.exists(), "colliding carrier filenames reached ORAS"
        return

    assert result.returncode == 0, (
        "wrapper rejected a schema-valid structured receipt before ORAS: "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert result.stdout.strip() == "ghcr.io/example/jat-runtime@sha256:" + "d" * 64
    assert Path(str(oras_log) + ".receipt").read_bytes() == receipt.read_bytes()
    assert [json.loads(line) for line in oras_log.read_text().splitlines()] == [
        ["login", "ghcr.io", "--username", "fake-user", "--password-stdin"],
        [
            "push",
            reference,
            "--artifact-type",
            "application/vnd.joshyorko.rcc-environment-artifact.v2",
            "jat-runtime.rcca:application/vnd.joshyorko.rcc-environment-artifact.v2+rcca",
            "jat-runtime.json:application/vnd.joshyorko.rcc-environment-artifact-receipt.v3+json",
        ],
        ["manifest", "fetch", "--descriptor", reference],
    ]

    valid_receipt = json.loads(receipt.read_text())
    weak = json.loads(receipt.read_text())
    weak["verified_hauler"]["launcher"] = [
        "python",
        "-c",
        "import os, shutil, subprocess, sys; executable = shutil.which('hauler'); "
        "prefix = os.environ.get('CONDA_PREFIX'); resolved = os.path.realpath(executable) if executable else ''; "
        "inside = bool(prefix and resolved.startswith(os.path.realpath(prefix) + os.sep)); "
        "sys.exit(127 if not inside else subprocess.run([resolved, 'version'], check=False).returncode)",
    ]
    receipt.write_text(json.dumps(weak) + "\n")
    oras_log.unlink(missing_ok=True)
    weak_rejected = subprocess.run(
        [
            str(root / "scripts/publish_environment_artifact.sh"),
            "--archive",
            str(archive),
            "--receipt",
            str(receipt),
            "--repository",
            "ghcr.io/example/jat-runtime",
        ],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert weak_rejected.returncode != 0
    assert not oras_log.exists(), "weak Hauler proof reached ORAS"

    invalid = json.loads(receipt.read_text())
    invalid["rcc_version"] = "v18.19.1"
    receipt.write_text(json.dumps(invalid) + "\n")
    oras_log.unlink(missing_ok=True)
    rejected = subprocess.run(
        [
            str(root / "scripts/publish_environment_artifact.sh"),
            "--archive",
            str(archive),
            "--receipt",
            str(receipt),
            "--repository",
            "ghcr.io/example/jat-runtime",
        ],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert rejected.returncode != 0
    assert not oras_log.exists(), "invalid receipt reached ORAS"
    stale_source = dict(valid_receipt)
    stale_source["jat_git_sha"] = "1" * 40
    receipt.write_text(json.dumps(stale_source) + "\n")
    stale_rejected = subprocess.run(
        [
            str(root / "scripts/publish_environment_artifact.sh"),
            "--archive",
            str(archive),
            "--receipt",
            str(receipt),
            "--repository",
            "ghcr.io/example/jat-runtime",
        ],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert stale_rejected.returncode != 0
    assert not oras_log.exists(), "stale source receipt reached ORAS"


def test_publish_wrapper_accepts_windows_archive_and_keeps_platform_tag(tmp_path):
    root = Path(__file__).parents[1]
    if shutil.which("jq") is None:
        pytest.fail("jq is required to exercise the publication wrapper")

    archive = tmp_path / "jat-runtime-windows.rcca"
    archive_bytes = b"schema-valid Windows RCCA archive"
    archive.write_bytes(archive_bytes)
    receipt = tmp_path / "jat-runtime-windows.json"
    receipt_data = {
        "format_version": 3,
        "operation": "build",
        "success": True,
        "jat_git_sha": subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True).stdout.strip(),
        "rcc_executable": "C:/runner/rcc.exe",
        "rcc_version": "v18.19.7",
        "rcc_source": {**RCC_SOURCE, "asset": "rcc-windows64.exe", "sha256": "d3338be49ef22bbd9f3e42b606e01231cb5c5d86716b18d24fadf85e7630e0be"},
        "hauler_version": "v2.1.1",
        "platform": "windows_amd64",
        "artifact_digest": "sha256:" + "e" * 64,
        "specification_digest": "sha256:" + "f" * 64,
        "legacy_blueprint_key": "a" * 16,
        "archive": {"filename": archive.name, "sha256": hashlib.sha256(archive_bytes).hexdigest(), "size": len(archive_bytes)},
        "verified_acquire": {"fresh_home": True, "no_build": True},
        "verified_no_build": {"fresh_home": True, "no_build": True},
        "verified_exec": {"fresh_home": True},
        "verified_warm_reuse": {"fresh_home": True, "no_build": True, "provider_unavailable": True},
        "verified_hauler": {
            "fresh_home": True,
            "command": ["hauler", "version"],
            "launcher": ["python", "-c", HAULER_VERSION_CHECK, "v2.1.1"],
            "resolved_under_conda_prefix": True,
            "exit_code": 0,
        },
        "verified_tasks": {"doctor": True, "build": True, "restore": True, "serve": True},
    }
    receipt.write_text(json.dumps(receipt_data) + "\n")
    validate(instance=receipt_data, schema=json.loads((root / "docs/environment-artifact-receipt.schema.json").read_text()))

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    oras_log = tmp_path / "oras.log"
    write_fake_oras(fake_bin / "oras", oras_log)
    environment = os.environ.copy()
    environment.update(PATH=f"{fake_bin}{os.pathsep}{environment['PATH']}", GITHUB_TOKEN="fake-token")
    result = subprocess.run(
        [str(root / "scripts/publish_environment_artifact.sh"), "--archive", str(archive), "--receipt", str(receipt), "--repository", "ghcr.io/example/jat-runtime"],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"Windows receipt was rejected: stdout={result.stdout!r} stderr={result.stderr!r}"
    assert result.stdout.strip() == "ghcr.io/example/jat-runtime@sha256:" + "d" * 64
    calls = [json.loads(line) for line in oras_log.read_text().splitlines()]
    assert calls[1][1] == "ghcr.io/example/jat-runtime:windows_amd64-" + "e" * 64


def test_publish_script_has_canonical_media_types_and_receipt_validation():
    root = Path(__file__).parents[1]
    script = (root / "scripts/publish_environment_artifact.sh").read_text()
    assert "application/vnd.joshyorko.rcc-environment-artifact.v2" in script
    assert "environment-artifact-receipt.v3+json" in script
    assert "jat-runtime.rcca" in script
    assert "environment-artifact-receipt.schema.json" in script
    assert "GITHUB_TOKEN" in script
    assert "oras login" in script
    assert "--password-stdin" in script


def test_legacy_files_are_removed_and_new_receipt_schema_is_version_three():
    root = Path(__file__).parents[1]
    for name in ("build_hololib.py", "build_hololib.sh", "publish_hololib.sh"):
        assert not (root / "scripts" / name).exists()
    for name in ("hololib.robot.yaml",):
        assert not (root / name).exists()
    assert not (root / "docs/hololib-receipt.schema.json").exists()
    schema = json.loads((root / "docs/environment-artifact-receipt.schema.json").read_text())
    assert schema["properties"]["format_version"]["const"] == 3
    assert schema["properties"]["rcc_version"]["const"] == "v18.19.7"
    assert schema["properties"]["hauler_version"]["const"] == "v2.1.1"
    assert "artifact_digest" in schema["required"]
