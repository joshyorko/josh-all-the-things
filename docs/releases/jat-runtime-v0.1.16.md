# JAT v0.1.16 runtime release

The release workflow targets a new immutable prerelease built from the exact merged main commit, using RCC v18.19.9 at `1eef8de8bd4b2b01c2f99a5fdfab95ec98f89674` and Hauler v2.1.1. Linux and Windows RCC binaries are SHA-256 pinned in `runtime/rcc.json`; the builder, adapter, receipt schema, and qualification fixtures use the same release identity.

RCC v18.19.9 includes acquired-environment command resolution, bundle task result preservation, embedded Artifact trust admission, and detached metadata authentication fixes. Native Linux and Windows qualification must pass before publication, followed by fresh verification of the published artifacts.

Josh Room consumes this release through its existing RCC controller and JAT maintenance workflow. Controller artifacts and consumer pins require their own qualification and promotion. Previous immutable releases remain unchanged.

Windows qualification invokes Hauler directly through RCC’s acquired-environment command resolution. Published-release verification selects the executable below the acquired `CONDA_PREFIX` explicitly, preserving verification of older releases without depending on a host shell.
