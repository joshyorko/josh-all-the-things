import json
import hashlib
import os
import subprocess
from pathlib import Path

from scripts.build_environment_artifact import main


def write_fake_rcc(path: Path, log: Path) -> None:
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, pathlib, sys\n"
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
        "    print(json.dumps({'artifactDigest': 'sha256:' + 'a' * 64, 'verification': {'valid': True}}))\n"
        "elif args[:2] == ['env', 'exec']:\n"
        "    print(json.dumps({'artifactDigest': 'sha256:' + 'a' * 64, 'exitCode': 0}))\n"
        "elif args == ['version']:\n"
        "    print('v18.19.1')\n"
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
    assert calls[0][:3] == ["env", "publish", "--robot"]
    assert calls[0][3:7] == [str(robot), "--provider", "local", "--json"]
    assert calls[1][:2] == ["env", "export"]
    assert "ht" not in calls[1]
    assert calls[2][:2] == ["env", "acquire"]
    assert calls[2][calls[2].index("--archive") + 1] == str(output)
    assert "--permissive-local" in calls[2] and "--json" in calls[2]
    assert calls[3][:3] == ["--no-build", "ht", "vars"]
    assert calls[4][:2] == ["env", "exec"]
    assert "--artifact" in calls[4]

    result = json.loads(receipt.read_text())
    assert result == {
        "artifact_digest": "sha256:" + "a" * 64,
        "archive": {"filename": "jat-runtime.rcca", "sha256": hashlib.sha256(b"RCCA").hexdigest(), "size": 4},
        "format_version": 2,
        "jat_git_sha": "d" * 40,
        "legacy_blueprint_key": "c" * 16,
        "platform": result["platform"],
        "rcc_version": "v18.19.1",
        "specification_digest": "sha256:" + "b" * 64,
        "verified_acquire": True,
        "verified_exec": True,
        "verified_no_build": True,
    }


def test_builder_rejects_existing_outputs(tmp_path):
    output = tmp_path / "jat-runtime.rcca"
    output.write_bytes(b"existing")
    try:
        main(["--output", str(output), "--receipt", str(tmp_path / "receipt.json")])
    except FileExistsError:
        pass
    else:
        raise AssertionError("existing output was accepted")


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
    assert "artifact_digest" in schema["required"]
