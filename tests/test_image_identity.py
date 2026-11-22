import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

from jat.image_identity import image_mapping, record_capture_inputs, verify_pull_references

DIGEST = "sha256:" + "a" * 64


def test_provenance_preserves_requested_aliases_and_duplicate_labels(tmp_path):
    aliases = [
        ("api:dev", "docker.io/library/api:dev"),
        ("backend/api:dev", "docker.io/backend/api:dev"),
        ("docker.io/nginx", "docker.io/library/nginx:latest"),
        ("localhost:61000/api:dev", "localhost:61000/api:dev"),
        ("ghcr.io/team/api@" + DIGEST, "ghcr.io/team/api@" + DIGEST),
    ]
    inventory, descriptors = [], []
    for source, ref in aliases:
        inventory.append({"Reference": ref, "Type": "image", "Digest": DIGEST})
        descriptors.append({"digest": DIGEST, "annotations": {
            "kind": "dev.hauler/image", "io.containerd.image.name": ref,
            "hauler.dev/original-ref": ref,
        }})
    inventory.append({"Reference": "hauler/file:latest", "Type": "file"})
    unknown = image_mapping(tmp_path, inventory)
    assert all(i.source_reference is None and i.pull_reference is None for i in unknown.images)
    (tmp_path / "index.json").write_text(json.dumps({"manifests": descriptors}))
    record_capture_inputs(tmp_path, [source for source, _ in aliases])
    images = image_mapping(tmp_path, inventory).images
    assert [(i.requested_reference, i.stored_reference) for i in images] == aliases
    assert [i.display_label for i in images].count("api:dev") == 3


def test_active_registry_requires_matching_digest_without_following_redirects(tmp_path):
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def do_HEAD(self):
            seen.append(self.path)
            self.send_response(302 if "redirect" in self.path else 200)
            self.send_header("Docker-Content-Digest", DIGEST if "wrong" not in self.path else "sha256:" + "b"*64)
            self.send_header("Location", "http://example.invalid")
            self.end_headers()
    names = ("ok", "wrong", "redirect", "../unsafe")
    refs = [{"Reference": "ghcr.io/" + repo + ":v1", "Type": "image", "Digest": DIGEST} for repo in names]
    mapping = image_mapping(tmp_path, refs)
    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        authority = "127.0.0.1:" + str(server.server_port)
        thread = Thread(target=server.serve_forever)
        thread.start()
        try:
            verify_pull_references(mapping, "http://" + authority)
        finally:
            server.shutdown()
            thread.join()
    assert [row.pull_reference for row in mapping.images] == [authority + "/ok@" + DIGEST, None, None, None]
    assert seen == [f"/v2/{repo}/manifests/{DIGEST}" for repo in names[:3]]
