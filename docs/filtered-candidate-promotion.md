# Qualify and promote the same filtered candidate bytes

The standard environment builder produces a dependency environment. It does not
embed the filtered JAT source. Do not replace a qualified self-contained candidate
with a standard rebuild and assume its evidence still applies.

The draft feature workflow builds a self-contained `.rcca`, verifies filtered
capture on Linux and Windows, then runs `qualify_candidate_for_promotion.py` on
those same bytes. This second, read-only step uses the existing RCC qualification
helpers and exact pinned Hauler launcher. It freshly acquires the archive, resolves
its descriptors with `--no-build`, compares embedded source hashes with canonical
Git blobs, repeats filtered capture/restore without a source overlay, and verifies
warm execution with an unreachable provider. It emits the existing publisher's
schema-v3 receipt only after all checks succeed. The candidate receipt and
promotion receipt identify the same archive hash, size, artifact and source SHA.
No build, export, tag, registry push or release action occurs during this step.

To repeat qualification on **each artifact's native platform**, check out the
exact qualified source and use its checksum-pinned RCC executable:

```sh
python scripts/qualify_candidate_for_promotion.py \
  --archive /downloaded/jat-filtered-candidate.rcca \
  --candidate-receipt /downloaded/candidate-receipt.json \
  --rcc /path/to/checksum-verified-rcc \
  --output /new/location/promotion-receipt.json
```

The output is exclusive-create. Source, platform, RCC pin, archive size/hash or
execution mismatches fail without producing a successful promotion receipt.
Keep the candidate's original archive basename; do not edit receipt identities.

## Publication boundary and exact commands

This campaign does not authorize publication. After review, a separately
**authorized publication** must start from the exact current main commit that
owns the qualified candidates. If merging the draft creates a different SHA,
qualify new candidates at that final SHA; earlier candidate evidence remains
historical. Do not edit an old receipt's source SHA to match a new commit.

The existing native publisher can consume the qualified bytes directly; it does
not rebuild. From the exact source checkout, after both native platform promotion
receipts have passed and publication has been authorized:

```sh
set -euo pipefail
: "${JAT_PROMOTION_SOURCE_SHA:?Set the reviewed final-main SHA from both receipts}"
test "$(git rev-parse HEAD)" = "$JAT_PROMOTION_SOURCE_SHA"
test "$(git ls-remote origin refs/heads/main | cut -f1)" = "$JAT_PROMOTION_SOURCE_SHA"
# GITHUB_TOKEN and GITHUB_ACTOR are supplied through the approved publishing environment.
bash scripts/publish_environment_artifacts.sh \
  --linux-archive /qualified/linux/jat-filtered-candidate.rcca \
  --linux-receipt /qualified/linux/promotion-receipt.json \
  --windows-archive /qualified/windows/jat-filtered-candidate.rcca \
  --windows-receipt /qualified/windows/promotion-receipt.json
```

The unchanged publisher validates the v3 receipt against that checkout, checks the
archive bytes again, and returns immutable platform-specific OCI references.
Record those returned references before any separately authorized Room runtime
pin adoption. This command does not create a GitHub release. Do not dispatch the
standard dependency-only rebuild as a substitute for promoting these bytes.

`v0.1.13-jat-runtime` is already an immutable maintenance release. A future feature
release requires a distinct approved version and exact-source verification; this
work does not change or republish that release. Current candidate run links,
archive hashes and remaining acceptance gates are recorded in draft PR #21.
