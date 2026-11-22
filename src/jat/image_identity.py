"""OCI inventory provenance and digest-verified pull references for a live JAT."""
import http.client
import json
import re
import time
from pathlib import Path

from .models import ImageIdentity, ImageMapping

_DIGEST = re.compile(r"sha256:[a-f0-9]{64}")
_COMPONENT = r"[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*"
_REPOSITORY = re.compile(rf"{_COMPONENT}(?:/{_COMPONENT})*")
_MEDIA_TYPES = ", ".join((
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
))


def _index(store):
    # Hauler preserves its original-ref annotation alongside the containerd
    # name in the OCI index. Never conflate two names sharing one digest.
    index = Path(store) / "index.json"
    if index.is_symlink() or not index.is_file() or index.stat().st_size > 16 * 1024 * 1024:
        return {}
    try:
        obj = json.loads(index.read_bytes())
        if isinstance(obj, dict) and isinstance(obj.get("manifests"), list):
            return obj
    except (KeyError, TypeError, ValueError):
        pass
    return {}


def _provenance(store):
    sources = {}
    for descriptor in _index(store).get("manifests", []):
        if not isinstance(descriptor, dict):
            continue
        annotations = descriptor.get("annotations")
        if not isinstance(annotations, dict):
            continue
        stored = annotations.get("io.containerd.image.name")
        original = annotations.get("hauler.dev/original-ref")
        digest = descriptor.get("digest")
        if all(isinstance(value, str) for value in (stored, original, digest)):
            requested = annotations.get("dev.jat.requested-reference")
            if not isinstance(requested, str) or _docker_name(requested) != _docker_name(stored):
                requested = None
            sources.setdefault((stored, digest), set()).add((original, requested))
    return {key: next(iter(names)) for key, names in sources.items() if len(names) == 1}


def _docker_name(reference):
    # Only for joining caller inputs to acquired inventory. Never used for pulls.
    path, sep, digest = reference.partition("@")
    first, slash, rest = path.partition("/")
    if not slash or (first != "localhost" and "." not in first and ":" not in first):
        host, path = "docker.io", path
    else:
        host, path = first, rest
    if host in {"index.docker.io", "docker.io"}:
        host = "docker.io"
        if "/" not in path:
            path = "library/" + path
    if not sep and ":" not in path.rsplit("/", 1)[-1]:
        path += ":latest"
    return host + "/" + path + sep + digest


def record_capture_inputs(store, requested):
    # The new build store is private staging. Annotations live on index
    # descriptors and leave manifest, layer and configuration bytes untouched.
    if not requested:
        return
    index = _index(store)
    inputs = {}
    for ref in requested:
        inputs.setdefault(_docker_name(ref), set()).add(ref)
    changed = False
    for descriptor in index.get("manifests", []):
        annotations = descriptor.get("annotations", {}) if isinstance(descriptor, dict) else {}
        if not isinstance(annotations, dict) or annotations.get("kind") != "dev.hauler/image":
            continue
        name = annotations.get("io.containerd.image.name")
        matches = inputs.get(_docker_name(name), set()) if isinstance(name, str) else set()
        if len(matches) == 1:
            annotations["dev.jat.requested-reference"] = next(iter(matches))
            changed = True
    if changed:
        (Path(store) / "index.json").write_text(json.dumps(index), encoding="utf-8")


def image_mapping(store, inventory):
    images = [item for item in inventory if str(item.get("Type")).lower() == "image"]
    sources = _provenance(store) if images else {}
    records = []
    for item in images:
        stored = item["Reference"]
        digest = item.get("Digest")
        origin = sources.get((stored, digest)) if isinstance(digest, str) else None
        records.append(ImageIdentity(
            stored_reference=stored,
            source_reference=origin[0] if origin else None,
            requested_reference=origin[1] if origin else None,
            display_label=stored.rsplit("/", 1)[-1],
            digest=digest if isinstance(digest, str) and _DIGEST.fullmatch(digest) else None,
            platform=item.get("Platform") if isinstance(item.get("Platform"), str) else None,
        ))
    return ImageMapping(images=records)


def verify_pull_references(mapping, registry_url):
    # This endpoint comes from the live, owned registry, never haul metadata.
    match = re.fullmatch(r"http://127\.0\.0\.1:([1-9][0-9]{0,4})", registry_url or "")
    if not match or int(match[1]) > 65535:
        return
    authority = registry_url.removeprefix("http://")
    deadline = time.monotonic() + 5
    for image in mapping.images:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        path = image.stored_reference.partition("/")[2].split("@", 1)[0]
        repo = path.rsplit(":", 1)[0]
        if not image.digest or not _REPOSITORY.fullmatch(repo) or len(repo) > 255:
            continue
        connection = http.client.HTTPConnection("127.0.0.1", int(match[1]), timeout=min(1, remaining))
        try:
            connection.request("HEAD", f"/v2/{repo}/manifests/{image.digest}", headers={"Accept": _MEDIA_TYPES})
            response = connection.getresponse()
            if response.status == 200 and response.getheader("Docker-Content-Digest") == image.digest:
                image.pull_reference = f"{authority}/{repo}@{image.digest}"
        except (OSError, http.client.HTTPException):
            pass
        finally:
            connection.close()
