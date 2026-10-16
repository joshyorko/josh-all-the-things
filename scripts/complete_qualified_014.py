"""One-off approved publication driver; never rebuilds the qualified runtime."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time

REPO = "joshyorko/josh-all-the-things"
SOURCE = "fe43065a654d1649ba9ebe5a6f202eac612e4385"
TAG = "v0.1.14-jat-runtime"
RELEASE_ID = 415200660
API = f"repos/{REPO}"


def api(path, data=None):
    args = ["gh", "api", path]
    if data is not None:
        request = Path("publication-request.json")
        request.write_text(json.dumps(data))
        args += ["--method", "PATCH", "--input", str(request)]
    return json.loads(subprocess.check_output(args, text=True))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


assert api(API + "/commits/main")["sha"] == SOURCE
release = api(f"{API}/releases/{RELEASE_ID}")
assert release["tag_name"] == TAG and release["target_commitish"] == SOURCE
assert release["draft"] is True and release["prerelease"] is True
assets = Path("release-assets")
assets.mkdir()
platforms = {}
for platform, carrier in (
    ("linux", "c3253ce1d51f9faddceef611f25e679613161121ec7078d3cbfa123a0ce9a4d6"),
    ("windows", "46d44b7f44a16a7f9ee7cb676cf134a4432b3f2814fd7faba645356f5607c907"),
):
    original = Path("qualified") / platform
    receipt_path = original / "promotion-receipt.json"
    receipt = json.loads(receipt_path.read_text())
    archive = original / "jat-filtered-candidate.rcca"
    assert receipt["jat_git_sha"] == SOURCE and receipt["format_version"] == 3
    assert digest(archive) == receipt["archive"]["sha256"]
    assert archive.stat().st_size == receipt["archive"]["size"]
    manifest_path = Path("carrier-readback") / f"{platform}.json"
    assert digest(manifest_path) == carrier
    manifest = json.loads(manifest_path.read_text())
    layers = {layer["digest"]: layer for layer in manifest["layers"]}
    for path in (archive, receipt_path):
        assert layers["sha256:" + digest(path)]["size"] == path.stat().st_size
    receipt_name = f"jat-runtime-{platform}-amd64.json"
    archive_name = f"jat-runtime-{platform}-amd64.rcca"
    shutil.copyfile(archive, assets / archive_name)
    shutil.copyfile(receipt_path, assets / receipt_name)
    freeze_name = f"environment_{platform}_amd64_freeze.yaml"
    freeze = Path("docs/receipts/qualified-014") / freeze_name
    expected = {"linux": "9f261fa13e0c808afa4398f6f5f5db5da0f7e186e286230e02bb9e44fcdd82af", "windows": "a3e029f0417aa52ee76fd2dd7a75176ec1f6e0f2662bc6f709de6c8e08616d30"}
    assert digest(freeze) == expected[platform]
    shutil.copyfile(freeze, assets / freeze_name)
    platforms[f"{platform}_amd64"] = {
        "receipt": receipt_name,
        "artifact_digest": receipt["artifact_digest"],
        "specification_digest": receipt["specification_digest"],
        "archive": {"asset": archive_name, "sha256": receipt["archive"]["sha256"], "size": receipt["archive"]["size"]},
        "ghcr_carrier": "ghcr.io/joshyorko/josh-all-the-things-jat-runtime@sha256:" + carrier,
    }
provenance = {
    "format_version": 1, "release_tag": TAG, "jat_git_sha": SOURCE,
    "workflow_run": {"id": 38934211098, "url": f"https://github.com/{REPO}/actions/runs/38934211098"},
    "rcc_version": "v18.19.7", "hauler_version": "v2.1.1", "platforms": platforms,
}
(assets / "jat-runtime-provenance.json").write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n")
assert digest(assets / "jat-runtime-provenance.json") == "cceef528fe5e07bf85c2024972cbe4ae651a09e112d94dd3a6458312249e57dd"
(assets / "SHA256SUMS").write_text("".join(f"{digest(path)}  {path.name}\n" for path in sorted(assets.iterdir())))
files = sorted(assets.iterdir())
assert len(files) == 8
existing = {item["name"]: item for item in release["assets"]}
assert set(existing) <= {path.name for path in files}
for path in files:
    if path.name in existing:
        item = existing[path.name]
        assert item["digest"] == "sha256:" + digest(path) and item["size"] == path.stat().st_size
    else:
        subprocess.run(["gh", "release", "upload", TAG, str(path), "--repo", REPO], check=True)
release = api(f"{API}/releases/{RELEASE_ID}")
existing = {item["name"]: item for item in release["assets"]}
assert set(existing) == {path.name for path in files}
assert release["draft"] is True and release["target_commitish"] == SOURCE
for path in files:
    item = existing[path.name]
    assert item["digest"] == "sha256:" + digest(path) and item["size"] == path.stat().st_size
# Approved publication happens only after readback of every exact asset.
release = api(f"{API}/releases/{RELEASE_ID}", {"draft": False, "prerelease": True, "make_latest": "false"})
Path("publication-readback.json").write_text(json.dumps(release, indent=2) + "\n")
for attempt in range(5):
    release = api(f"{API}/releases/{RELEASE_ID}")
    if release.get("immutable") is True and release["draft"] is False:
        break
    time.sleep(3)
assert release["immutable"] is True and release["draft"] is False and release["prerelease"] is True
target = api(f"{API}/git/ref/tags/{TAG}")["object"]
if target["type"] == "tag":
    target = api(f'{API}/git/tags/{target["sha"]}')["object"]
assert target["type"] == "commit" and target["sha"] == SOURCE
Path("publication-readback.json").write_text(json.dumps(release, indent=2) + "\n")
print(f'PASS immutable release {release["html_url"]}; exact source and eight asset hashes verified')
