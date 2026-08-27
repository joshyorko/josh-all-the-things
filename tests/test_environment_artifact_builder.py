import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.build_environment_artifact import HAULER_VERSION_COMMAND, _stage_copy, build_parser, main


HAULER_VERSION_CHECK = (
    "import os, shutil, subprocess, sys; executable = shutil.which('hauler'); "
    "prefix = os.environ.get('CONDA_PREFIX'); prefix_root = os.path.realpath(prefix) if prefix else ''; "
    "resolved = os.path.realpath(executable) if executable else ''; "
    "python_resolved = os.path.realpath(sys.executable); "
    "inside = bool(prefix_root and resolved.startswith(prefix_root + os.sep)); "
    "python_inside = bool(prefix_root and python_resolved.startswith(prefix_root + os.sep)); "
    "sys.exit(127 if not (inside and python_inside) else subprocess.run([resolved, 'version'], check=False).returncode)"
)


def write_fake_rcc(path: Path, log: Path) -> None:
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        f"log = pathlib.Path({str(log)!r})\n"
        "log.open('a').write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "args = sys.argv[1:]\n"
        "if args[:2] == ['env', 'publish']:\n"
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
        "    print(json.dumps({'artifactDigest': 'sha256:' + 'a' * 64, 'exitCode': exit_code}))\n"
        "elif args == ['version']:\n"
        "    print(os.environ.get('JAT_FAKE_RCC_VERSION', 'v18.19.2'))\n"
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
    )
    path.chmod(0o755)


def test_build_uses_official_publish_export_acquire_and_exec_flow(tmp_path, monkeypatch):
    robot = tmp_path / "robot.yaml"
    robot.write_text("tasks: {}\n")
    rcc = tmp_path / "rcc"
    log = tmp_path / "rcc.log"
    write_fake_rcc(rcc, log)
    monkeypatch.setenv("JAT_GIT_SHA", "d" * 40)
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
    ]

    result = json.loads(receipt.read_text())
    assert result["operation"] == "build"
    assert result["success"] is True
    assert result["rcc_executable"] == str(rcc)
    assert result["verified_acquire"]["fresh_home"] is True
    assert result["verified_acquire"]["no_build"] is True
    assert result == {
        "artifact_digest": "sha256:" + "a" * 64,
        "archive": {"filename": "jat-runtime.rcca", "sha256": hashlib.sha256(b"RCCA").hexdigest(), "size": 4},
        "format_version": 2,
        "jat_git_sha": "d" * 40,
        "legacy_blueprint_key": "c" * 16,
        "platform": result["platform"],
        "rcc_version": "v18.19.2",
        "specification_digest": "sha256:" + "b" * 64,
        "operation": "build",
        "success": True,
        "rcc_executable": str(rcc),
        "verified_acquire": {"fresh_home": True, "no_build": True},
        "verified_exec": {"fresh_home": True},
        "verified_hauler": {
            "fresh_home": True,
            "command": ["hauler", "version"],
            "launcher": ["python", "-c", HAULER_VERSION_CHECK],
            "resolved_under_conda_prefix": True,
            "exit_code": 0,
        },
        "verified_no_build": {"fresh_home": True, "no_build": True},
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


def test_builder_rejects_unsupported_rcc_before_publish(tmp_path, monkeypatch):
    robot = tmp_path / "robot.yaml"
    robot.write_text("tasks: {}\n")
    rcc = tmp_path / "rcc"
    log = tmp_path / "rcc.log"
    write_fake_rcc(rcc, log)
    monkeypatch.setenv("JAT_FAKE_RCC_VERSION", "v18.19.1")

    with pytest.raises(RuntimeError, match="v18.19.2"):
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
    monkeypatch.setenv("JAT_GIT_SHA", "d" * 40)
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


def test_publish_wrapper_accepts_schema_valid_structured_verification_receipt(tmp_path):
    root = Path(__file__).parents[1]
    if shutil.which("jq") is None:
        pytest.fail("jq is required to exercise the publication wrapper")

    archive = tmp_path / "jat-runtime.rcca"
    archive_bytes = b"schema-valid RCCA archive"
    archive.write_bytes(archive_bytes)
    receipt = tmp_path / "jat-runtime.json"
    receipt.write_text(
        json.dumps(
            {
                "format_version": 2,
                "operation": "build",
                "success": True,
                "jat_git_sha": "0" * 40,
                "rcc_executable": "/synthetic/rcc",
                "rcc_version": "v18.19.2",
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
                "verified_exec": {"fresh_home": True},
                "verified_hauler": {
                    "fresh_home": True,
                    "command": ["hauler", "version"],
                    "launcher": ["python", "-c", HAULER_VERSION_CHECK],
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

    assert result.returncode == 0, (
        "wrapper rejected a schema-valid structured receipt before ORAS: "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert result.stdout.strip() == "ghcr.io/example/jat-runtime@sha256:" + "d" * 64
    assert [json.loads(line) for line in oras_log.read_text().splitlines()] == [
        ["login", "ghcr.io", "--username", "fake-user", "--password-stdin"],
        [
            "push",
            reference,
            "--artifact-type",
            "application/vnd.joshyorko.rcc-environment-artifact.v2",
            "jat-runtime.rcca:application/vnd.joshyorko.rcc-environment-artifact.v2+rcca",
            "jat-runtime.json:application/vnd.joshyorko.rcc-environment-artifact-receipt.v2+json",
        ],
        ["manifest", "fetch", "--descriptor", reference],
    ]

    invalid = json.loads(receipt.read_text())
    invalid["rcc_version"] = "v18.19.1"
    receipt.write_text(json.dumps(invalid) + "\n")
    oras_log.unlink()
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


def test_publish_script_has_canonical_media_types_and_receipt_validation():
    root = Path(__file__).parents[1]
    script = (root / "scripts/publish_environment_artifact.sh").read_text()
    assert "application/vnd.joshyorko.rcc-environment-artifact.v2" in script
    assert "jat-runtime.rcca" in script
    assert "environment-artifact-receipt.schema.json" in script
    assert "GITHUB_TOKEN" in script
    assert "oras login" in script
    assert "--password-stdin" in script


def test_legacy_files_are_removed_and_new_receipt_schema_is_version_two():
    root = Path(__file__).parents[1]
    for name in ("build_hololib.py", "build_hololib.sh", "publish_hololib.sh"):
        assert not (root / "scripts" / name).exists()
    for name in ("hololib.robot.yaml",):
        assert not (root / name).exists()
    assert not (root / "docs/hololib-receipt.schema.json").exists()
    schema = json.loads((root / "docs/environment-artifact-receipt.schema.json").read_text())
    assert schema["properties"]["format_version"]["const"] == 2
    assert schema["properties"]["rcc_version"]["const"] == "v18.19.2"
    assert "artifact_digest" in schema["required"]
