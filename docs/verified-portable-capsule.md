# Verified Portable JAT Capsule acceptance

This is an opt-in integration fixture for an exact RCC candidate, not a new JAT
trust subsystem or runtime release. Ordinary `jat` hauling remains independent
of RCC. `runtime/rcc.json` and all production release targets remain unchanged.

The fixture reuses the checksum-verified, already shipped JAT runtime `.rcca`.
Only the disposable publisher imports and runs that existing unsigned runtime
with an explicit local policy to prepare the synthetic content. It publishes the imported runtime through `--no-build env publish`,
issues a complete v2 snapshot with RCC's operator CLI, and exports selected signed
attachments with RCC's archive CLI. The environment specification and legacy blueprint must remain identical.
Publishing the consumer-derived catalog can change the Artifact digest, so both
input and candidate identities are recorded. Renewal and revocation must retain
the signed candidate's Artifact digest. No environment rebuild, public artifact
upload, or release is performed.

The synthetic transport contains the signed `.rcca`, a real Hauler-backed JAT
workspace haul, and tracked JAT service source. Deployment trust roots and retained
rollback journals are provisioned outside that transport. Disposable private keys
remain in the publisher fixture; neither capsule nor CI evidence uploads them.
A source/content manifest binds the tracked JAT files, source commit and Hauler
workspace haul to the reviewed provenance build identity. The consumer first
requires RCC authenticated v2 admission, checks the exact metadata bytes against
the RCC receipt, then checks the transported source/content hashes before task
execution. A digest without that v2 decision never authorizes source execution.
RCC evaluates all signatures, authority, freshness, revocation and rollback.
JAT supplies its existing Doctor, Build, Inspect and Restore service behavior.

Run on Linux with an exact source-built RCC candidate and the published archive:

```sh
python scripts/verify_portable_capsule.py \
  --rcc /absolute/path/to/candidate-rcc \
  --rcc-sha256 EXACT_BINARY_SHA256 --rcc-source FULL_RCC_COMMIT \
  --archive /absolute/path/to/jat-runtime-linux-amd64.rcca \
  --archive-sha256 PUBLISHED_ARCHIVE_SHA256 \
  --work /persistent/new-private-evidence-directory
```

The test-driver Python needs `cryptography` for disposable Ed25519 key generation;
it does not implement RCC signing or verification. Use the declared RCC test
runtime or an isolated CI test driver. Runtime execution always uses the acquired
JAT environment's Python and Hauler. The package observation compares Python's
installed distribution metadata with an independently invoked pip listing and
also reads installed Conda names/versions. Unknown licenses and package hashes
are not guessed. It verifies the Hauler release and records its actual binary hash.

Linux defaults to a Bubblewrap network namespace and fails if it cannot create
one. No provider or evidence endpoint is supplied to the consumer. Windows must
select `--network providerless` explicitly: that proves no provider/evidence fetch
is required but does **not** prove OS-level network isolation. No macOS JAT package
qualification is claimed. The optional CI uses exact RCC source and records its
source-stamped binary checksum separately from the unchanged runtime pin.

The fixture retains raw CLI logs and RCC receipts, renews sequence 1 to 2 for the
same digest/materialization, starts a real task under sequence 2, revokes the
Artifact at sequence 3, and requires new leases to fail while the prior task
finishes with its earlier receipt. It then attempts real canary tasks against
rollback, tampered, missing, expired, future and unauthorized snapshots. They must
produce neither the canary output file nor the forbidden stdout marker. Worker
state is never reset. Completed tasks must leave no lease files.

Inspect `receipt.json`, `logs/`, and `package-observation.json` in the retained work
directory. Only logs/receipts/package observations are CI evidence; the authority
and deployment directories are not uploaded. Artifact identity, archive checksum,
Hauler content digests, capsule transport checksum and source/binary identity are
distinct. The fixture performs no registry publication, so it has no registry OCI
manifest digest to report.

RCC's renamed Hololib object inventory is **not a package SBOM**, SPDX conformance,
or package CVE/license coverage. Package observation is separate evidence, not a
standards document. A genuine package SBOM and independent schema validation remain
a release-review limitation until the RCC package-SBOM follow-up is implemented.
No power-loss durability, global replica propagation or host-compromise resistance
is claimed by this acceptance test.
