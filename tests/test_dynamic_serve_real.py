"""Opt-in synthetic qualification; never substitutes a patched binary for the pin silently."""

import json
import os
import signal
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

import psutil
import pytest

HAULER = os.environ.get("JAT_TEST_SERVE_HAULER")
pytestmark = pytest.mark.skipif(
    not HAULER, reason="explicit Hauler qualification binary required"
)
OPENER = build_opener(ProxyHandler({}))


@pytest.fixture
def capsule(tmp_path):
    content = tmp_path / "synthetic.txt"
    content.write_text("synthetic portable content\n")
    store = tmp_path / "store"
    for arguments in (
        ["store", "add", "file", str(content), "--name", "synthetic.txt"],
        ["store", "save", "--filename", str(tmp_path / "synthetic.haul")],
    ):
        subprocess.run(
            [HAULER, "--store", str(store), *arguments],
            check=True,
            capture_output=True,
            timeout=30,
        )
    return tmp_path / "synthetic.haul"


@contextmanager
def serving(tmp_path, capsule, name, mode, *options):
    startup = tmp_path / f"{name}.json"
    environment = os.environ.copy()
    environment["PATH"] = str(Path(HAULER).parent) + os.pathsep + environment["PATH"]
    environment["JAT_RUN_DIR"] = str(tmp_path / f"{name}-runtime")
    with (tmp_path / f"{name}.log").open("wb") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "jat.cli",
                "serve",
                "--haul",
                str(capsule),
                "--mode",
                mode,
                "--startup-file",
                str(startup),
                "--startup-timeout",
                "10",
                "--json",
                *options,
            ],
            env=environment,
            stdout=log,
            stderr=log,
            start_new_session=os.name != "nt",
        )
        try:
            yield process, startup
        finally:
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
            process.wait(timeout=15)
    assert not startup.exists()
    assert not list((tmp_path / f"{name}-runtime").glob(".jat-*"))


def ready(process, path):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        assert process.poll() is None, path.with_suffix(".log").read_text()
        if path.exists():
            return json.loads(path.read_text())["serve"]
        time.sleep(0.05)
    pytest.fail("no startup receipt")


@pytest.mark.parametrize("mode", ["files", "registry", "both", "auto"])
def test_real_occupied_defaults_concurrent_sessions_and_shutdown(
    tmp_path, capsule, mode
):
    before = {child.pid for child in psutil.Process().children(recursive=True)}
    occupied = []
    try:
        for port in (8080, 5000):
            sock = socket.socket()
            try:
                sock.bind(("127.0.0.1", port))
                sock.listen()
                occupied.append(sock)
            except OSError:
                sock.close()  # Already occupied is also the condition under test.
        with serving(tmp_path, capsule, "first", mode) as (first, first_path):
            one = ready(first, first_path)
            with serving(tmp_path, capsule, "second", mode) as (second, second_path):
                two = ready(second, second_path)
                for key in ("fileserver_url", "registry_url"):
                    if one[key] is None:
                        assert two[key] is None
                        continue
                    assert one[key] != two[key]
                    expected_bind = (
                        "all-interfaces" if key == "fileserver_url" else "loopback"
                    )
                    assert one[key.replace("_url", "_bind")] == expected_bind
                    assert two[key.replace("_url", "_bind")] == expected_bind
                    for endpoints in (one, two):
                        url = endpoints[key]
                        assert not url.endswith((":8080", ":5000"))
                        path = (
                            "/synthetic.txt"
                            if key == "fileserver_url"
                            else "/v2/_catalog"
                        )
                        with OPENER.open(url + path, timeout=2) as response:
                            content = response.read()
                        if key == "fileserver_url":
                            assert content == b"synthetic portable content\n"
                        else:
                            assert "repositories" in json.loads(content)
                children = (
                    psutil.Process(first.pid).children()
                    + psutil.Process(second.pid).children()
                )
                for child in children:
                    assert all(
                        connection.laddr.ip
                        in (
                            {"127.0.0.1"}
                            if "registry" in child.cmdline()
                            else {"0.0.0.0", "::"}
                        )
                        for connection in child.net_connections()
                        if connection.status == psutil.CONN_LISTEN
                    )
    finally:
        for sock in occupied:
            sock.close()
    assert {child.pid for child in psutil.Process().children(recursive=True)} == before


@pytest.mark.parametrize(
    "mode,flag", [("files", "--fileserver-port"), ("registry", "--registry-port")]
)
def test_real_explicit_conflict_fails_promptly(tmp_path, capsule, mode, flag):
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        port = occupied.getsockname()[1]
        start = time.monotonic()
        with serving(tmp_path, capsule, "conflict", mode, flag, str(port)) as (
            process,
            receipt,
        ):
            assert process.wait(timeout=8) != 0
            assert not receipt.exists()
        assert time.monotonic() - start < 8
        assert (
            "address already in use" in (tmp_path / "conflict.log").read_text().lower()
        )


@pytest.mark.parametrize("mode", ["files", "registry", "both"])
def test_real_explicit_port_and_child_exit(tmp_path, capsule, mode):
    # An explicitly chosen candidate may lose a bind race; JAT must fail, never change it.
    from jat.serving import _port

    port = _port(0, set())
    with serving(
        tmp_path,
        capsule,
        "explicit",
        mode,
        "--registry-port" if mode == "registry" else "--fileserver-port",
        str(port),
    ) as (process, path):
        endpoints = ready(process, path)
        assert (
            endpoints["registry_url" if mode == "registry" else "fileserver_url"]
            == f"http://127.0.0.1:{port}"
        )
        children = psutil.Process(process.pid).children()
        assert len(children) == (2 if mode == "both" else 1)
        children[0].terminate()
        assert process.wait(timeout=5) != 0
        assert not path.exists()
        assert all(not child.is_running() for child in children)
