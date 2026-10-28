# JAT v0.1.15 runtime release

The release workflow targets a new immutable prerelease built from the exact merged main commit. It retains RCC v18.19.7 and Hauler v2.1.1; the upstream Hauler selected-endpoint correction is tracked separately and is not consumed here.

This source includes capsule, archive, lifecycle and validation corrections from the dogfood exercise, full producer-receipt checks against each release's original contract, and OCI publication references bound to the push result. Runtime configuration now binds the Hauler pin into the RCC environment identity.

Publication requires native Linux and Windows build/acquire/no-build/Hauler/warm-provider qualification. The workflow attaches both runtime archives and receipts, freezes, provenance and SHA256SUMS, then verifies the published immutable release. The successful run and immutable release identify the actual publication evidence; this document alone is not proof of publication.

Josh Room consumes published JAT runtime prereleases through its existing candidate/qualification workflow. Its controller and packaged consumer pins are promoted and verified separately. Previous releases are retained unchanged.
