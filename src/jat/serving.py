"""Supervise Hauler-owned listeners and publish their readiness, never serve HTTP."""

import json
import os
import secrets
import subprocess
import threading
import time
from collections import deque
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

import psutil

from .hauler import hauler_subprocess_environment
from .models import ServeEndpoints
from .process import ProcessRunner


@contextmanager
def startup_receipt(path: Path | None):
    """Never overwrite a caller's file, including an existing symlink."""
    identity = None
    payload = None
    if path is not None:
        path = path.absolute()
        if os.path.lexists(path):
            raise ValueError("startup file must not already exist")
        if not path.parent.is_dir():
            raise ValueError("startup file parent must exist")

    def publish(endpoints):
        nonlocal identity, payload
        if path is None:
            return
        record = {
            "schema_version": 1,
            "state": "ready",
            "operation": "serve",
            "pid": os.getpid(),
            "session_id": secrets.token_hex(16),
            "serve": endpoints.model_dump(mode="json"),
        }
        payload = json.dumps(record).encode("utf-8")
        temporary = None
        try:
            with NamedTemporaryFile(mode="wb", dir=path.parent, delete=False) as output:
                temporary = Path(output.name)
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            identity = temporary.stat()
            os.link(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink()

    try:
        yield publish
    finally:
        if path is not None and identity is not None:
            try:
                current = path.lstat()
                if (current.st_dev, current.st_ino) == (
                    identity.st_dev,
                    identity.st_ino,
                ):
                    with path.open("rb") as receipt:
                        matches = receipt.read(4096) == payload
                    if matches:
                        path.unlink()
            except FileNotFoundError:
                pass


def _port(requested: int, used: set[int]) -> int:
    if requested:
        return requested
    while True:
        candidate = 49152 + secrets.randbelow(16384)
        if candidate not in used:
            return candidate


def _owned_listener(process, port, binding):
    try:
        connections = psutil.Process(process.pid).net_connections(kind="tcp")
    except psutil.NoSuchProcess:
        return False
    except psutil.Error as error:
        raise RuntimeError("cannot verify Hauler listener ownership") from error
    listeners = [
        connection
        for connection in connections
        if connection.status == psutil.CONN_LISTEN and connection.laddr.port == port
    ]
    expected = {"127.0.0.1"} if binding == "loopback" else {"0.0.0.0", "::"}
    if any(connection.laddr.ip not in expected for connection in listeners):
        raise RuntimeError(
            f"Hauler listener does not match {binding} binding; refusing readiness"
        )
    return bool(listeners)


def _drain(pipe, tail):
    try:
        while chunk := pipe.read(1024):
            tail.append(chunk)
    finally:
        pipe.close()


def serve_children(
    hauler,
    store,
    temp,
    registry,
    files_directory,
    config,
    mode,
    request,
    registry_config,
    publish,
    announce,
):
    """Bind by launching Hauler; retry collisions without a free-port probe.

    Readiness requires a listener owned by each exact child PID plus HTTP
    success. An unrelated process accepting the candidate port cannot qualify.
    """
    files = mode in ("files", "both")
    images = mode in ("registry", "both")
    deadline = time.monotonic() + request.startup_timeout
    opener = build_opener(ProxyHandler({}))
    environment = hauler_subprocess_environment()
    # Distribution parses REGISTRY_* after YAML; inherited overrides must not
    # turn a loopback/read-only registry into a different server.
    environment = {
        key: value
        for key, value in environment.items()
        if not key.upper().startswith("REGISTRY_")
    }
    for attempt in range(8):
        file_port = (
            _port(request.fileserver_port, {request.registry_port}) if files else None
        )
        registry_port = _port(request.registry_port, {file_port}) if images else None
        if files and images and file_port == registry_port:
            raise ValueError("fileserver and registry require distinct ports")
        config.write_text(registry_config(registry, registry_port or 5000))
        endpoints = ServeEndpoints(
            mode=mode,
            fileserver_url=f"http://127.0.0.1:{file_port}" if files else None,
            registry_url=f"http://127.0.0.1:{registry_port}" if images else None,
            fileserver_bind="all-interfaces" if files else None,
            registry_bind="loopback" if images else None,
        )
        specs = []
        if files:
            specs.append(
                (
                    hauler.serve_fileserver_command(
                        store, temp, files_directory, file_port
                    ),
                    file_port,
                    endpoints.fileserver_url + "/",
                    request.fileserver_port == 0,
                    endpoints.fileserver_bind,
                )
            )
        if images:
            specs.append(
                (
                    hauler.serve_registry_command(store, temp, registry, config),
                    registry_port,
                    endpoints.registry_url + "/v2/",
                    request.registry_port == 0,
                    endpoints.registry_bind,
                )
            )
        processes, readers, tails = [], [], []
        retry = False
        try:
            for argv, *_ in specs:
                child = subprocess.Popen(
                    argv,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    start_new_session=os.name != "nt",
                    env=environment,
                )
                processes.append(child)
                tail = deque(maxlen=8)
                tails.append(tail)
                reader = threading.Thread(
                    target=_drain, args=(child.stdout, tail), daemon=True
                )
                readers.append(reader)
                reader.start()
            ready = False
            while True:
                statuses = [child.poll() for child in processes]
                exited = next(
                    (
                        index
                        for index, status in enumerate(statuses)
                        if status is not None
                    ),
                    None,
                )
                if exited is not None:
                    readers[exited].join(timeout=1)
                    diagnostic = b"".join(tails[exited]).decode(
                        "utf-8", errors="replace"
                    )[-2048:]
                    conflict = (
                        "address already in use" in diagnostic.lower()
                        or "only one usage" in diagnostic.lower()
                    )
                    if (
                        not ready
                        and conflict
                        and specs[exited][3]
                        and attempt < 7
                        and time.monotonic() < deadline
                    ):
                        retry = True
                        break
                    reason = (
                        "address already in use" if conflict else diagnostic[-1500:]
                    )
                    raise RuntimeError(
                        f"Hauler endpoint on port {specs[exited][1]} exited "
                        f"with status {statuses[exited]}: {reason}"
                    )
                if not ready:
                    if time.monotonic() >= deadline:
                        raise RuntimeError(
                            "Hauler startup timed out before owned endpoints became ready"
                        )
                    available = True
                    for child, (_, port, url, _, binding) in zip(processes, specs):
                        if not _owned_listener(child, port, binding):
                            available = False
                            break
                        try:
                            with opener.open(
                                url,
                                timeout=min(
                                    0.5, max(0.01, deadline - time.monotonic())
                                ),
                            ) as response:
                                available = 200 <= response.status < 300
                        except (OSError, URLError):
                            available = False
                        if not available:
                            break
                    if available and all(child.poll() is None for child in processes):
                        publish(endpoints)
                        announce("serving capsule: " + endpoints.model_dump_json())
                        ready = True
                time.sleep(0.05 if not ready else 0.2)
        finally:
            ProcessRunner()._stop_all(processes)
            for reader in readers:
                reader.join(timeout=2)
        if not retry:
            break
    raise RuntimeError("Hauler automatic port allocation exhausted")
