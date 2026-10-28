import socket
import subprocess
import sys
from pathlib import Path

from scripts import verify_jat_serve


def test_serve_probe_downloads_content_and_stops_owned_child(tmp_path, monkeypatch):
    content = b'synthetic workspace archive'
    (tmp_path / 'joshs-all-the-things-workspace.tar.zst').write_bytes(content)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    original = subprocess.Popen
    children = []

    def start_server(_argv, **options):
        child = original([sys.executable, '-m', 'http.server', str(port), '--bind', '127.0.0.1'], cwd=tmp_path, **options)
        children.append(child)
        return child

    monkeypatch.setattr(verify_jat_serve.subprocess, 'Popen', start_server)
    verify_jat_serve.verify_serving(Path('synthetic.haul'), tmp_path / 'probe', port)
    assert (tmp_path / 'probe/served-workspace.tar.zst').read_bytes() == content
    assert len(children) == 1 and children[0].poll() is not None
