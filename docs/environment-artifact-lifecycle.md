# JAT RCC Environment Artifact lifecycle

## Current RCC contract

The newest stable compatible Josh fork release checked on 2026-09-23 is
`joshyorko/rcc` v18.19.5 at
[`d1aec7d0bb897a81274423c7a6bb747233f9c263`](https://github.com/joshyorko/rcc/tree/d1aec7d0bb897a81274423c7a6bb747233f9c263).
Its release assets publish Linux amd64 and Windows amd64; their release API
SHA-256 digests are pinned in [`runtime/rcc.json`](../runtime/rcc.json).
The published `index.json` identifies v18.19.5 as the newest tested release.

RCC owns environment specification, artifact construction/identity, local
publish/export/acquire, materialization, and `env exec`. The builder retains an
isolated producer and a separate fresh verifier: the producer home is made
unavailable before archive acquisition and no-build execution. JAT adds the
Hauler-in-the-acquired-environment proof, binds RCC source/binary and exact JAT
source identity in its receipt, and promotes an archive/receipt only after the
proofs pass. Local construction uses the local provider and requires no
registry credentials. The ORAS/GHCR publisher remains a separate distribution
boundary: the immutable OCI manifest reference is not the RCC Artifact digest.
The source candidate and artifact receipt also bind the current official Hauler
v2.1.1 pin from `runtime/hauler.json`; the Linux and Windows checksums come
from the release's `hauler_2.1.1_checksums.txt`. This is the engine pin only;
JAT's Hauler engine/manifest implementation remains owned by issue #6.

The receipt contract intentionally advances from format 2 to format 3 because
current promotion requires exact RCC provenance, the selected Hauler version,
and warm/unavailable-provider evidence. The publisher rejects v2 receipts and
emits a v3 receipt media type; RCC Artifact identity and the `.rcca` archive
format are unchanged. Josh Room's linked consumer issue owns the corresponding
receipt-validation migration.

## RCC lifecycle options evaluated

| RCC capability | JAT decision | Evidence and boundary |
| --- | --- | --- |
| `env publish`, `env export`, `env acquire`, `env exec` | Adopt | Josh RCC [`v18.19.5` changelog](https://github.com/joshyorko/rcc/blob/d1aec7d0bb897a81274423c7a6bb747233f9c263/docs/changelog.md) and [`v18 compatibility contract`](https://github.com/joshyorko/rcc/blob/d1aec7d0bb897a81274423c7a6bb747233f9c263/docs/v18-compatibility.md) define the v1 manifest/object lifecycle and stable CLI/JSON boundary. JAT delegates lifecycle and materialization rather than exporting Holotree state itself. |
| `cache serve` loopback provider | Do not adopt | RCC changelog documents a canonical local provider-root server. JAT's current CI build/export/acquire path needs no network service and consumers already use immutable GHCR references; starting a loopback server would add a process without simplifying that path. |
| Named provider profiles / auth-env references | Do not adopt for builds | RCC v18 compatibility says profiles are additive and credentials are named by environment-variable reference. Local build/acquire from the exported archive needs no remote profile; publisher credentials remain isolated in ORAS login stdin and never enter the RCC receipt. |
| `env coordinate claim|heartbeat|wait|release|prewarm` | Do not enable in JAT CI | RCC [`coordination contract`](https://github.com/joshyorko/rcc/blob/d1aec7d0bb897a81274423c7a6bb747233f9c263/docs/environment-build-coordination.md) makes coordination an optional optimization and requires a shared CAS-like coordinator. JAT currently has one artifact build per platform job and no shared coordinator. Introducing one would add operational state absent from the problem; a concurrent-contender test is therefore not applicable to the enabled runtime path. |
| Completion receipts | Preserve the independent verifier proof | RCC requires artifact-backed waits to bind the authoritative completion receipt. JAT does not use coordinated waits; its fresh acquire receipt must match the exact locally published artifact digest and `verification.valid`, with separate producer/verifier homes. |
| Artifact trust, provenance, SBOM, signing, revocation | Defer strict remote trust | RCC [`artifact trust contract`](https://github.com/joshyorko/rcc/blob/d1aec7d0bb897a81274423c7a6bb747233f9c263/docs/environment-artifact-trust.md) defines detached attestations and strict-remote policy. Current JAT builds are local, unsigned artifacts, so the fresh consumer explicitly uses `--permissive-local`; this is not represented as a signed remote trust decision. The JAT receipt binds the release source and binary and the archive digest/size. |
| RCC GC | Do not run during build, acquisition, or startup | v18.19.5 changelog notes content-root validation and transactional GC hardening, but collection remains explicit maintenance and is not needed for a bounded producer/verifier home. |
| Remote artifact distribution | Retain GHCR/ORAS | RCC v18.19.5 trust documentation identifies filesystem/archive/HTTP carriers and explicitly places OCI carrier support outside its implementation. GHCR/ORAS therefore remains the current immutable consumer carrier; receipt archive hash/size and RCC artifact digest remain distinct from the OCI manifest digest. |

The warm proof runs a second `--no-build env exec` in the fresh verifier after
acquisition with `--provider http://127.0.0.1:1` explicitly pointing to an
unreachable loopback endpoint. The command succeeds with the same artifact
digest, proving the acquired materialization remains usable; that endpoint is
not saved in the receipt.

## Official RCC 21.3.0 comparison

| Capability | Josh RCC v18.19.5 | Official RCC v21.3.0 |
| --- | --- | --- |
| Environment Artifact publish | Documented `rcc env publish` v1 lifecycle | No equivalent contract established by the official release notes; those notes state there are no functional changes. |
| Acquire/export/exec | Documented artifact lifecycle with immutable artifact identity and local/archive provider paths | No equivalent artifact identity/receipt contract established by the official release notes. |
| Provider profiles | Documented named profiles and auth-env references, with provider-free local operation | Not established by the official v21.3.0 release notes. |
| Build coordination | Documented versioned claim/heartbeat/wait/release/prewarm machine contract | Not established by the official v21.3.0 release notes. |
| Trust/provenance | Documented artifact-bound provenance/SBOM/signature/revocation contracts | v21.3.0 release notes describe dependency and Go toolchain security updates only; no matching artifact trust contract is stated. |
| JAT receipt fields | JAT schema binds exact source SHA, RCC repo/tag/commit/asset/checksum, current Hauler version, artifact/specification/legacy identity, archive SHA/size and fresh/warm/Hauler proof fields | No compatible JAT receipt contract established. |
| Platform support | v18.19 artifact matrix documents Linux amd64, Windows amd64, macOS amd64, macOS arm64; JAT pins Linux and Windows only | Official v21.3.0 release notes publish Linux amd64, Windows amd64 and macOS arm64 executables; no Intel macOS executable. |
| License/distribution boundary | Josh RCC [`LICENSE`](https://github.com/joshyorko/rcc/blob/d1aec7d0bb897a81274423c7a6bb747233f9c263/LICENSE) is Apache-2.0; JAT distributes only its `.rcca`/receipt via GHCR/ORAS, not the RCC executable | The official RCC [`LICENSE`](https://github.com/robocorp/rcc/blob/master/LICENSE) is a Sema4 EULA for downloadable software (with third-party components separately licensed); its binaries are not a drop-in redistributable replacement. |

Primary comparison sources: official [RCC v21.3.0 release notes](https://sema4.ai/docs/automation/release-notes/2026-09-04-rcc-v21-3-0), Josh RCC [`v18.19.5 changelog`](https://github.com/joshyorko/rcc/blob/d1aec7d0bb897a81274423c7a6bb747233f9c263/docs/changelog.md), [`v18 compatibility contract`](https://github.com/joshyorko/rcc/blob/d1aec7d0bb897a81274423c7a6bb747233f9c263/docs/v18-compatibility.md), [`coordination contract`](https://github.com/joshyorko/rcc/blob/d1aec7d0bb897a81274423c7a6bb747233f9c263/docs/environment-build-coordination.md), and [`artifact trust contract`](https://github.com/joshyorko/rcc/blob/d1aec7d0bb897a81274423c7a6bb747233f9c263/docs/environment-artifact-trust.md). A larger official version number is not evidence of contract parity; JAT remains on the compatible Josh fork.

## Consumer handoff

Josh Room consumer work is tracked separately in
[joshyorko/josh-room#83](https://github.com/joshyorko/josh-room/issues/83).
JAT produces and proves its own runtime archive; Josh Room consumes its
immutable identity and independently owns its controller Environment Artifact.
