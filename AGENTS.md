# Agent Instructions

This repository intentionally tracks only the self-contained RCC bundle. Do not
commit its unpacked contents.

Before inspecting or changing the automation, unpack the current bundle with
RCC into a temporary directory:

```sh
unpack_dir=$(mktemp -d)
rcc robot unpack --bundle ./josh-all-the-things-bundle.py --output "$unpack_dir" --force
```

Make source changes in the unpacked directory, then rebuild the bundle before
committing the updated `josh-all-the-things-bundle.py`.


  rcc robot bundle \
    --robot ./robot.yaml \
    --output josh-all-the-things-bundle.py

  To replace the bundle in the repo afterward:

  mv ../josh-all-the-things-bundle.py ./josh-all-the-things-bundle.py
