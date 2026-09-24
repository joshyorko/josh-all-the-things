# JAT runtime v0.1.11

Published as the immutable pre-release [v0.1.11-jat-runtime](https://github.com/joshyorko/josh-all-the-things/releases/tag/v0.1.11-jat-runtime).

- Runtime source: `5b59fcafc832be67eaca3b8f42ff9f7b06719b76` (merged #9 after #10)
- Release automation main: `28995fea4979d80843e19c4987b4ae9ed860f96d`
- RCC: `v18.19.5` from `joshyorko/rcc`, commit `d1aec7d0bb897a81274423c7a6bb747233f9c263`
- Hauler: `v2.1.1`
- Publication workflow: [run #72](https://github.com/joshyorko/josh-all-the-things/actions/runs/35943386526)

The release includes the Linux and Windows RCC archives, receipts, platform freezes, provenance, and `SHA256SUMS`. These identities match the attached receipts and the GHCR carriers recorded in provenance.

| Platform | RCC artifact digest | Specification digest | Archive SHA-256 | Archive size | GHCR carrier | OCI manifest digest |
| --- | --- | --- | --- | ---: | --- | --- |
| Linux amd64 | `sha256:ae6b5802103c780de49b42768885a8c66073747272caf759784e9eb23f44e4f1` | `sha256:899025175bc2bfceb1f41778e6feaa4341ca84e4dbe97a5ddd97384ee9fa586d` | `eb5a2f5634b6bc78a66bc070493f7d7df81a775cfe421160b8b1ac34c2589d5f` | 170822706 | `ghcr.io/joshyorko/josh-all-the-things-jat-runtime:linux_amd64-ae6b5802103c780de49b42768885a8c66073747272caf759784e9eb23f44e4f1` | `sha256:93aabb87eff422ff66268abc60f5beaa10348f6fe45c022d056b720d57648d39` |
| Windows amd64 | `sha256:9d35d565d53ede7e05aa1ded75758578c34a92a032b3623b7801ce84c3631827` | `sha256:e19095b0e079d03357a70e50a517d7e865ed1f7cb76fa57d1bd75f3ab7d04db2` | `894841785490e4643d12a08340af3da5e45eb29dec4eaf9ddc358406a284deaa` | 104191913 | `ghcr.io/joshyorko/josh-all-the-things-jat-runtime:windows_amd64-9d35d565d53ede7e05aa1ded75758578c34a92a032b3623b7801ce84c3631827` | `sha256:e8cd8a2b87ec2bc6f878320135f14b6b992786c5dfc55e7a98d64f0a1e71cfb1` |

The receipts record the exact JAT source SHA, RCC and Hauler versions, artifact and specification digests, archive hashes and sizes, and the internal fresh-acquire/no-build, Hauler, and JAT task proofs.
