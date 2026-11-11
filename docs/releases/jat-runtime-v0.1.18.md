# JAT v0.1.18 runtime release preparation

The next release targets immutable prerelease `v0.1.18-jat-runtime` from the exact merged main commit. The runtime uses published RCC v18.19.11 at `953eda18d3d1f4a286c17709f282315a2ed16a16` and Hauler v2.1.1. `runtime/rcc.json` pins the downloaded Linux and Windows release assets by SHA-256. The adapter, builder, receipt schema, and tests use that same RCC identity.

RCC v18.19.11 upgrades embedded micromamba to 2.10.0, coordinates compiler dependency pins, adds ready-environment GC dry-run coverage, and adds opt-in read-only release readiness receipts. The release source and both asset hashes were independently checked against the official GitHub release on 2026-11-11.

Linux and Windows runtime qualification must pass before the official manual publication workflow runs from exact current main. The signed capsule workflow qualifies the exact RCC release source, strict consumer Doctor, Build, Inspect, and Restore, renewal, revocation, denial canaries, package hash observations, and lease cleanup. Both v18.19.7 metadata N-1 and v18.19.9 inventory migration tests remain required. Their checksum pins are independent of the production RCC pin. Linux uses an offline network namespace; Windows proves providerless operation without claiming OS network isolation.

Runtime release artifacts remain unsigned and use the existing explicit local admission policy. Disposable capsule test keys do not sign the published runtime. The format-version 3 receipt and `.rcca` archive format remain unchanged. Fresh postpublication verification must pass on both platforms. Previous immutable releases, including `v0.1.17-jat-runtime`, remain unchanged. This preparation does not establish publication or qualification success.

Josh Room owns its separate controller rebuild and maintenance promotion. Its handoff requires the actual published JATT tag, source commit, publication run, archive checksums, receipt identities, and immutable carrier references. No speculative downstream pin is authorized by this preparation.
