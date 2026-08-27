# JAT Hauler Runtime Design

## Goal

Make the JAT Linux amd64 runtime self-contained before RCC freezes it. The
runtime owns Hauler, while the optional `--brew` input remains only a payload
that can be captured or restored; Homebrew is not an installation mechanism.

## Boundaries

- JAT owns the Hauler installer, its exact upstream release manifest, and the
  capture/restore/serve Python service.
- RCC v18.19.2 owns dependency construction, `rccPostInstall`, freeze files,
  artifact publish/export/acquire, and Holotree materialization.
- Josh Room consumes the resulting immutable JAT environment artifact and the
  separately versioned JAT source contract; it does not absorb JAT code into
  its controller environment.

## Runtime contract

The canonical manifest pins Hauler `v2.0.3`, the official asset
`hauler_2.0.3_linux_amd64.tar.gz`, its GitHub Release URL, and SHA256
`6685eb1ba86291566f3694d69a8b7e80c928e5a589853691cccf51b26bc61617`.
`scripts/install_hauler.sh` reads that manifest, downloads into a private
temporary file, verifies the complete archive before extraction, promotes one
regular executable to `$CONDA_PREFIX/bin/hauler` atomically, and verifies
`hauler version`. Existing matching installations are verified and reused;
partial or mismatched installations fail closed without root or sudo.

The selected Linux freeze file must retain the exact `rccPostInstall` command.
`robot.yaml` must not contain the old Homebrew `preRunScripts` hook. JAT's
normal runtime resolves `hauler`, GNU tar, curl, coreutils, and zstd from the
RCC environment. The archive adapter fails closed when a capable environment
tar is unavailable and never probes Homebrew.

## Artifact proof

The artifact builder uses isolated producer and verifier `ROBOCORP_HOME`
directories. It runs RCC v18.19.2 publish/export, removes producer access
before fresh acquire, proves `--no-build ht vars`, and adds a no-build
`env exec --artifact <digest>` check. Because RCC v18.19.2 resolves a relative
command before applying the materialized child environment, that check invokes
the artifact's Python, locates `hauler` with the materialized PATH, and runs
`hauler version`. Only after all proofs pass are the `.rcca` and receipt
promoted as create-only outputs. The final receipt records the logical Hauler
operation and its artifact-Python launcher alongside the JAT commit, RCC
identity, platform, artifact/specification digests, archive hash/size, and
fresh acquire/no-build/exec assertions.

## Verification

Tests cover manifest validation, checksum and extraction safety, idempotence,
failure before promotion, no Homebrew command lookup, freeze-hook presence,
and the RCC call order. A real Linux amd64 run proves JAT Doctor, Build,
Restore, and Serve from a fresh acquired artifact. Publication is recorded
separately from local build and runtime acceptance.
