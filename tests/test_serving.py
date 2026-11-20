import json
import socket
import sys

import psutil
import pytest

from jat.hauler import HaulerAdapter
from jat.models import ServeEndpoints, ServeRequest
from jat.serving import serve_children, startup_receipt


class Ready(BaseException):
    pass


class Server:
    def require_loopback_fileserver(self):
        pass

    def serve_fileserver_command(self, store, temp, directory, port):
        return [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"]


def run(tmp_path, request, publish, hauler=None):
    return serve_children(
        hauler or Server(),
        tmp_path,
        tmp_path,
        tmp_path,
        tmp_path,
        tmp_path / "config",
        "files",
        request,
        lambda *_: "",
        publish,
        lambda *_: None,
    )


def test_receipt_is_atomic_owned_and_removed(tmp_path):
    path = tmp_path / "ready.json"
    endpoints = ServeEndpoints(
        mode="files", fileserver_url="http://127.0.0.1:1234", fileserver_bind="loopback"
    )
    with startup_receipt(path) as publish:
        assert not path.exists()
        publish(endpoints)
        record = json.loads(path.read_text())
        assert record["schema_version"] == 1
        assert record["state"] == "ready"
        assert record["serve"]["fileserver_url"] == endpoints.fileserver_url
    assert not path.exists()
    assert not list(tmp_path.iterdir())


def test_receipt_never_overwrites_or_removes_another_file(tmp_path):
    path = tmp_path / "ready.json"
    path.write_text("keep")
    with pytest.raises(ValueError, match="already exist"), startup_receipt(path):
        pass
    assert path.read_text() == "keep"
    path.unlink()
    with startup_receipt(path) as publish:
        publish(ServeEndpoints(mode="files"))
        path.unlink()
        path.write_text("replacement")
    assert path.read_text() == "replacement"


def test_automatic_conflict_retries_and_never_accepts_foreign_listener(
    tmp_path, monkeypatch
):
    before = {child.pid for child in psutil.Process().children()}
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        conflict = occupied.getsockname()[1]
        # A second candidate is chosen without probing availability.
        choices = iter([conflict, 0])
        original = __import__("jat.serving", fromlist=["_port"])._port
        monkeypatch.setattr(
            "jat.serving._port",
            lambda requested, used: next(choices) or original(0, used),
        )

        def publish(endpoints):
            assert endpoints.fileserver_url != f"http://127.0.0.1:{conflict}"
            assert endpoints.fileserver_bind == "loopback"
            raise Ready()

        with pytest.raises(Ready):
            run(tmp_path, ServeRequest(haul="synthetic", startup_timeout=5), publish)
    assert {child.pid for child in psutil.Process().children()} == before


def test_explicit_conflict_fails_without_ready_or_fallback(tmp_path):
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        port = occupied.getsockname()[1]
        with pytest.raises(RuntimeError, match=f"port {port} exited"):
            run(
                tmp_path,
                ServeRequest(haul="synthetic", fileserver_port=port, startup_timeout=5),
                lambda _: pytest.fail("foreign listener must not qualify"),
            )


def test_startup_timeout_stops_child(tmp_path):
    class Silent(Server):
        def serve_fileserver_command(self, *args):
            return [sys.executable, "-c", "import time; time.sleep(60)"]

    before = {child.pid for child in psutil.Process().children()}
    with pytest.raises(RuntimeError, match="startup timed out"):
        run(
            tmp_path,
            ServeRequest(haul="synthetic", startup_timeout=0.2),
            lambda _: pytest.fail("not listening"),
            Silent(),
        )
    assert {child.pid for child in psutil.Process().children()} == before


def test_unsupported_hauler_fails_closed():
    class Runner:
        def run(self, *args, **kwargs):
            from jat.process import ProcessResult

            return ProcessResult(argv=[], exit_status=0, stdout="--port int")

    with pytest.raises(RuntimeError, match="pinned v2.1.1 is unsupported"):
        HaulerAdapter(Runner()).require_loopback_fileserver()
