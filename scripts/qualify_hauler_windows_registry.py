"""Compare official and backported Hauler binaries using one synthetic OCI image."""

import gzip
import hashlib
import io
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tarfile
import time
from urllib.request import ProxyHandler, build_opener

IMAGE = "library/jat-windows-ab"
MANIFEST = "application/vnd.oci.image.manifest.v1+json"


def digest(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def fixture(root):
    blobs = root / "blobs" / "sha256"
    blobs.mkdir(parents=True)

    def blob(data, media_type):
        (blobs / digest(data).split(":")[1]).write_bytes(data)
        return {"mediaType": media_type, "size": len(data), "digest": digest(data)}

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        payload = b"JATT Windows registry qualification\n"
        entry = tarfile.TarInfo("fixture.txt")
        entry.size = len(payload)
        archive.addfile(entry, io.BytesIO(payload))
    raw_layer = buffer.getvalue()
    layer = blob(gzip.compress(raw_layer, mtime=0), "application/vnd.oci.image.layer.v1.tar+gzip")
    config = blob(json.dumps({
        "architecture": "amd64", "os": "linux", "config": {},
        "rootfs": {"type": "layers", "diff_ids": [digest(raw_layer)]},
    }).encode(), "application/vnd.oci.image.config.v1+json")
    manifest = blob(json.dumps({
        "schemaVersion": 2, "mediaType": MANIFEST, "config": config, "layers": [layer],
    }).encode(), MANIFEST)
    manifest["annotations"] = {
        "kind": "dev.hauler/image",
        "org.opencontainers.image.ref.name": IMAGE + ":test",
        "io.containerd.image.name": "docker.io/" + IMAGE + ":test",
        "hauler.dev/original-ref": "docker.io/" + IMAGE + ":test",
    }
    (root / "oci-layout").write_text('{"imageLayoutVersion":"1.0.0"}', encoding="utf-8")
    (root / "index.json").write_text(json.dumps({"schemaVersion": 2, "manifests": [manifest]}), encoding="utf-8")
    return manifest["digest"]


def verify_pull(port, expected):
    from urllib.request import Request

    pull = build_opener(ProxyHandler({}))
    base = f"http://127.0.0.1:{port}/v2/{IMAGE}"
    with pull.open(Request(f"{base}/manifests/{expected}", headers={"Accept": MANIFEST}), timeout=5) as response:
        manifest = response.read()
        assert digest(manifest) == expected == response.headers["Docker-Content-Digest"]
    image = json.loads(manifest)
    for descriptor in [image["config"], *image["layers"]]:
        with pull.open(f"{base}/blobs/{descriptor['digest']}", timeout=5) as response:
            payload = response.read()
            assert digest(payload) == descriptor["digest"] and len(payload) == descriptor["size"]
    return image


def exercise(binary, work, seed, expected, evidence, lane):
    store = work / "store"
    shutil.copytree(seed, store)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    registry = work / "registry"
    config = work / "registry.json"
    config.write_text(json.dumps({
        "version": "0.1",
        "storage": {"filesystem": {"rootdirectory": str(registry)}},
        "http": {"addr": f"127.0.0.1:{port}"},
    }), encoding="utf-8")
    log = evidence / f"{lane}.log"
    with log.open("wb") as stream:
        child = subprocess.Popen([
            str(binary), "--store", str(store), "store", "serve", "registry",
            "--directory", str(registry), "--config", str(config), "--log-level", "debug",
        ], cwd=work, stdout=stream, stderr=subprocess.STDOUT)
        try:
            if lane == "official":
                status = child.wait(timeout=90)
                raw = log.read_text(encoding="utf-8", errors="replace")
                assert status != 0 and "/blobs/uploads/" in raw and "500 Internal Server Error" in raw
                return {"exit_code": status, "expected_upload_http_status": 500}
            deadline = time.monotonic() + 90
            while True:
                assert child.poll() is None, f"candidate exited: {child.returncode}"
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=1):
                        break
                except OSError:
                    if time.monotonic() > deadline:
                        raise TimeoutError("candidate registry never listened")
                    time.sleep(0.2)
            image = verify_pull(port, expected)
            return {"pull_reference": f"127.0.0.1:{port}/{IMAGE}@{expected}",
                    "manifest_digest": expected, "verified_manifest": image}
        finally:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=15)


def main(root):
    evidence = root / "evidence"
    seed = root / "fixture"
    expected = fixture(seed)
    (evidence / "fixture-digest.txt").write_text(expected + "\n", encoding="utf-8")
    for lane in ("official", "backport"):
        work = root / f"{lane}-run"
        work.mkdir()
        record = exercise(root / f"{lane}.exe", work, seed, expected, evidence, lane)
        (evidence / f"{lane}.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve())
