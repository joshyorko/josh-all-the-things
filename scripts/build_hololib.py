#!/usr/bin/env python3

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

STRIPPED_PREFIXES = ("CONDA_", "RCC_", "ROBOCORP_", "PYTHON")


def clean_environment(source: dict[str, str], robocorp_home: Path, home: Path) -> dict[str, str]:
    environment = {
        key: value
        for key, value in source.items()
        if not key.startswith(STRIPPED_PREFIXES)
    }
    environment["HOME"] = str(home)
    environment["ROBOCORP_HOME"] = str(robocorp_home)
    environment["PATH"] = os.pathsep.join(
        item for item in source.get("PATH", "").split(os.pathsep)
        if item and ".robocorp/holotree" not in item
    )
    return environment


def _run(argv: list[str], *, cwd: Path, environment: dict[str, str], timeout: int) -> subprocess.CompletedProcess:
    completed = subprocess.run(
        argv,
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode:
        diagnostic = (completed.stderr or completed.stdout)[-4000:]
        raise RuntimeError(f"command failed ({completed.returncode}): {' '.join(argv[:4])}\n{diagnostic}")
    return completed


def _variables(completed: subprocess.CompletedProcess) -> dict[str, str]:
    values = json.loads(completed.stdout)
    return {item["key"]: item["value"] for item in values}


def _digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def _publish(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with source.open("rb") as input_file, destination.open("xb") as output_file:
            shutil.copyfileobj(input_file, output_file, 1024 * 1024)
            output_file.flush()
            os.fsync(output_file.fileno())
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def _platform_name() -> str:
    machine = {"x86_64": "amd64", "aarch64": "arm64"}.get(platform.machine(), platform.machine())
    return f"{platform.system().lower()}_{machine}"


def reset_ephemeral_shared_root(path: Path) -> None:
    if os.environ.get("JAT_HOLOLIB_DAGGER") != "1":
        raise RuntimeError("shared holotree reset is allowed only inside the JAT Dagger builder")
    if path != Path("/opt/robocorp"):
        raise ValueError("ephemeral shared root must be exactly /opt/robocorp")
    if path.exists():
        shutil.rmtree(path)


def _enable_shared(rcc: str, *, cwd: Path, environment: dict[str, str], timeout: int) -> None:
    _run([rcc, "ht", "shared", "--enable", "--once"], cwd=cwd, environment=environment, timeout=timeout)
    _run([rcc, "ht", "init"], cwd=cwd, environment=environment, timeout=timeout)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build and verify a relocatable JAT RCC hololib archive.")
    parser.add_argument("--robot", type=Path, default=Path("robot.yaml"))
    parser.add_argument("--output", type=Path, default=Path("dist/hololib.zip"))
    parser.add_argument("--receipt", type=Path, default=Path("dist/hololib.json"))
    parser.add_argument("--rcc", default=shutil.which("rcc") or "rcc")
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--ephemeral-shared-root", action="store_true")
    parser.add_argument("--jat-git-sha")
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
    source_root = robot.parent
    conda = source_root / "conda.yaml"
    if not conda.is_file():
        raise FileNotFoundError(f"environment configuration is unavailable: {conda}")

    with tempfile.TemporaryDirectory(prefix="jat-hololib-") as temporary:
        work = Path(temporary)
        robot_root = work / "robot"
        robot_root.mkdir()
        shutil.copy2(robot, robot_root / "robot.yaml")
        shutil.copy2(conda, robot_root / "conda.yaml")

        builder_home = work / "builder-home"
        builder_rcc = work / "builder-rcc"
        builder_home.mkdir()
        builder_environment = clean_environment(dict(os.environ), builder_rcc, builder_home)
        expected_builder_root = builder_rcc
        if args.ephemeral_shared_root:
            reset_ephemeral_shared_root(Path("/opt/robocorp"))
            _enable_shared(args.rcc, cwd=robot_root, environment=builder_environment, timeout=args.timeout)
            expected_builder_root = Path("/opt/robocorp")
        resolved = _variables(_run(
            [args.rcc, "ht", "vars", "--robot", str(robot_root / "robot.yaml"), "--json"],
            cwd=robot_root,
            environment=builder_environment,
            timeout=args.timeout,
        ))
        environment_hash = resolved["RCC_ENVIRONMENT_HASH"]
        if not Path(resolved["RCC_HOLOTREE_SPACE_ROOT"]).is_relative_to(expected_builder_root):
            raise RuntimeError("fresh builder materialized holotree outside its ROBOCORP_HOME")

        archive = work / "hololib.zip"
        _run(
            [args.rcc, "ht", "export", environment_hash, "--robot", str(robot_root / "robot.yaml"), "--zipfile", str(archive), "--json"],
            cwd=robot_root,
            environment=builder_environment,
            timeout=args.timeout,
        )
        with zipfile.ZipFile(archive) as bundle:
            if bundle.testzip() is not None:
                raise RuntimeError("exported hololib zip failed integrity verification")

        verifier_home = work / "verifier-home"
        verifier_rcc = work / "verifier-rcc"
        verifier_home.mkdir()
        verifier_environment = clean_environment(dict(os.environ), verifier_rcc, verifier_home)
        expected_verifier_root = verifier_rcc
        if args.ephemeral_shared_root:
            reset_ephemeral_shared_root(Path("/opt/robocorp"))
            _enable_shared(args.rcc, cwd=robot_root, environment=verifier_environment, timeout=args.timeout)
            expected_verifier_root = Path("/opt/robocorp")
        _run(
            [args.rcc, "ht", "import", str(archive)],
            cwd=robot_root,
            environment=verifier_environment,
            timeout=args.timeout,
        )
        verified = _variables(_run(
            [args.rcc, "--no-build", "ht", "vars", "--robot", str(robot_root / "robot.yaml"), "--json"],
            cwd=robot_root,
            environment=verifier_environment,
            timeout=args.timeout,
        ))
        if verified["RCC_ENVIRONMENT_HASH"] != environment_hash:
            raise RuntimeError("imported environment hash does not match exported catalog")
        if not Path(verified["RCC_HOLOTREE_SPACE_ROOT"]).is_relative_to(expected_verifier_root):
            raise RuntimeError("verification materialized holotree outside its ROBOCORP_HOME")

        version = _run([args.rcc, "version"], cwd=source_root, environment=builder_environment, timeout=30).stdout.splitlines()[0]
        commit = args.jat_git_sha or _run(
            ["git", "rev-parse", "HEAD"], cwd=source_root, environment=builder_environment, timeout=30
        ).stdout.strip()
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise ValueError("JAT git SHA must be a full lowercase commit digest")
        receipt = {
            "environment_hash": environment_hash,
            "format_version": 1,
            "jat_git_sha": commit,
            "operation": "build-hololib",
            "platform": _platform_name(),
            "rcc_version": version,
            "success": True,
            "verified_no_build": True,
            "zip": {
                "filename": output.name,
                "sha256": _digest(archive),
                "size": archive.stat().st_size,
            },
        }
        receipt_source = work / "hololib.json"
        receipt_source.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
        _publish(archive, output)
        try:
            _publish(receipt_source, receipt_path)
        except BaseException:
            output.unlink(missing_ok=True)
            raise
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
