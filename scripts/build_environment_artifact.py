#!/usr/bin/env python3

import argparse
import hashlib
import json
import os
import platform
import subprocess
import tempfile
from pathlib import Path

DEFAULT_RCC = "rcc"


def _environment(source: dict[str, str], home: Path, rcc_home: Path) -> dict[str, str]:
    result = {key: value for key, value in source.items() if key not in {"HOME", "ROBOCORP_HOME"}}
    result.update(HOME=str(home), ROBOCORP_HOME=str(rcc_home))
    return result


def _run(argv: list[str], *, cwd: Path, env: dict[str, str], timeout: int) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(argv)}\n{result.stderr[-4000:]}")
    return result


def _json(result: subprocess.CompletedProcess[str]) -> dict:
    return json.loads(result.stdout)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _platform_name() -> str:
    machine = {"x86_64": "amd64", "aarch64": "arm64"}.get(platform.machine(), platform.machine())
    return f"{platform.system().lower()}_{machine}"


def _git_sha(root: Path, env: dict[str, str], timeout: int) -> str:
    value = env.get("JAT_GIT_SHA")
    if value:
        return value
    return _run(["git", "rev-parse", "HEAD"], cwd=root, env=env, timeout=30).stdout.strip()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build and verify a JAT RCC environment artifact.")
    parser.add_argument("--robot", type=Path, default=Path("robot.yaml"))
    parser.add_argument("--output", type=Path, default=Path("dist/jat-runtime.rcca"))
    parser.add_argument("--receipt", type=Path, default=Path("dist/jat-runtime.json"))
    parser.add_argument("--rcc", default=DEFAULT_RCC)
    parser.add_argument("--jat-git-sha")
    parser.add_argument("--timeout", type=int, default=1800)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    robot = args.robot.resolve()
    output = args.output.resolve()
    receipt_path = args.receipt.resolve()
    if output.exists():
        raise FileExistsError(f"output already exists: {output}")
    if receipt_path.exists():
        raise FileExistsError(f"receipt already exists: {receipt_path}")
    if not robot.is_file():
        raise FileNotFoundError(f"robot configuration is unavailable: {robot}")
    root = robot.parent
    with tempfile.TemporaryDirectory(prefix="jat-environment-artifact-") as directory:
        work = Path(directory)
        producer_home, producer_rcc = work / "producer-home", work / "producer-rcc"
        verifier_home, verifier_rcc = work / "verifier-home", work / "verifier-rcc"
        producer_home.mkdir()
        verifier_home.mkdir()
        producer_env = _environment(dict(os.environ), producer_home, producer_rcc)
        verifier_env = _environment(dict(os.environ), verifier_home, verifier_rcc)
        publish = _json(_run([args.rcc, "env", "publish", "--robot", str(robot), "--provider", "local", "--json"], cwd=root, env=producer_env, timeout=args.timeout))
        artifact = publish["artifactDigest"]
        specification = publish["specificationDigest"]
        legacy = publish["legacyBlueprintKey"]
        archive = work / "jat-runtime.rcca"
        _run([args.rcc, "env", "export", "--artifact", artifact, "--provider", "local", "--output", str(archive)], cwd=root, env=producer_env, timeout=args.timeout)
        producer_home.rename(work / "producer-home-unavailable")
        if producer_rcc.exists():
            producer_rcc.rename(work / "producer-rcc-unavailable")
        acquired = _json(_run([args.rcc, "env", "acquire", "--archive", str(archive), "--permissive-local", "--json"], cwd=root, env=verifier_env, timeout=args.timeout))
        if acquired.get("artifactDigest") != artifact or acquired.get("verification", {}).get("valid") is not True:
            raise RuntimeError("fresh verifier did not validate the exported artifact")
        variables = _json(_run([args.rcc, "--no-build", "ht", "vars", "--robot", str(robot), "--json"], cwd=root, env=verifier_env, timeout=args.timeout))
        if not isinstance(variables, list):
            raise TypeError("no-build verification did not return RCC variables")
        execution = _json(_run([args.rcc, "env", "exec", "--artifact", artifact, "--permissive-local", "--json", "--", "python", "-c", "print('jat-runtime-proof')"], cwd=root, env=verifier_env, timeout=args.timeout))
        if execution.get("artifactDigest") != artifact or execution.get("exitCode") != 0:
            raise RuntimeError("environment execution proof failed")
        version = _run([args.rcc, "version"], cwd=root, env=producer_env, timeout=30).stdout.splitlines()[0]
        jat_sha = args.jat_git_sha or _git_sha(root, producer_env, args.timeout)
        if len(jat_sha) != 40 or any(character not in "0123456789abcdef" for character in jat_sha):
            raise ValueError("JAT git SHA must be a full lowercase commit digest")
        archive_info = {"filename": output.name, "sha256": _sha256(archive), "size": archive.stat().st_size}
        receipt = {
            "format_version": 2,
            "operation": "build",
            "success": True,
            "jat_git_sha": jat_sha,
            "rcc_executable": args.rcc,
            "rcc_version": version,
            "platform": _platform_name(),
            "artifact_digest": artifact,
            "specification_digest": specification,
            "legacy_blueprint_key": legacy,
            "archive": archive_info,
            "verified_acquire": {"fresh_home": True, "no_build": True},
            "verified_no_build": {"fresh_home": True, "no_build": True},
            "verified_exec": {"fresh_home": True},
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        os.link(archive, output)
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        staged_receipt = work / "jat-runtime.json"
        staged_receipt.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
        os.link(staged_receipt, receipt_path)
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
