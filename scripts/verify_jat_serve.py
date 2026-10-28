#!/usr/bin/env python3
"""Observe acquired-runtime serving, then stop only the owned foreground child."""

import argparse
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def verify_serving(haul: Path, output: Path, port: int = 8080) -> None:
    output.mkdir(parents=True, exist_ok=True)
    log_path = output / 'serve.log'
    downloaded = output / 'served-workspace.tar.zst'
    with log_path.open('wb') as log:
        process = subprocess.Popen(
            [sys.executable, '-m', 'jat.cli', 'serve', '--haul', str(haul), '--mode', 'files',
             '--fileserver-port', str(port), '--json'],
            stdout=log, stderr=log, start_new_session=os.name != 'nt',
        )
        try:
            for _ in range(30):
                if process.poll() is not None:
                    raise RuntimeError('JAT Serve exited before readiness')
                try:
                    with urllib.request.urlopen(f'http://127.0.0.1:{port}/joshs-all-the-things-workspace.tar.zst', timeout=2) as response:
                        downloaded.write_bytes(response.read())
                    if downloaded.stat().st_size:
                        break
                except (urllib.error.URLError, TimeoutError):
                    pass
                time.sleep(1)
            else:
                raise RuntimeError('JAT Serve did not serve the acquired workspace')
        finally:
            if process.poll() is None:
                if os.name == 'nt':
                    subprocess.run(['taskkill.exe', '/PID', str(process.pid), '/T', '/F'], check=True)
                else:
                    os.killpg(process.pid, signal.SIGINT)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                if os.name == 'nt':
                    process.kill()
                else:
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
                raise
            print(log_path.read_text(errors='replace'))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--haul', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8080)
    args = parser.parse_args(argv)
    verify_serving(args.haul, args.output, args.port)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
