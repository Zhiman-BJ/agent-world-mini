# Task 1 Report: upstream preparation

## Result

- Corrected root: `/data1/home/tianfang/agent-world-mini-zhiman/.worktrees/kimi/artifacts/semiconductor_delivery_20260918`
- Reproduction: `python scripts/prepare_semiconductor_delivery.py /data1/agent_world/toolgen_semiconductor_0917_rich/delivery artifacts/semiconductor_delivery_20260918`
- Manifest: `artifacts/semiconductor_delivery_20260918/delivery_corrections.json`
- Source was not modified. Capacity preflight recorded 21,414,490,133 source bytes and 1,543,990,128,640 free bytes. Copy used `cp -a --reflink=auto`, never hardlinks.

The script derived launchers from each generation source's `tool_generation/software_environment.json`, verified those launchers in the copied Profile, and corrected the first five mappings from `interpreters/.../pythonX.Y` to `python-X.Y/bin/python`. It also rewrote the first five copied `pyvenv.cfg:home` values to their verified bundled interpreters. The final three mappings were already correct and were unchanged.

## Evidence

- Before preparation, invoking each of the first five mapped interpreters with `-I` failed immediately with `ModuleNotFoundError: No module named 'jsonschema'`.
- `python -m unittest tests.test_prepare_semiconductor_delivery -v`: 3 tests passed. They cover metadata-derived correction, copied payload preservation, distinct source/destination inodes, manifest output, existing-destination rejection, canonical symlink containment, exact environment-set validation, and isolated import-probe settings.
- Preparation output: `"import_failures": {}`. Isolated probes imported `jsonschema` plus the selected business modules in all eight Profiles. No package was installed or repaired.
- `load_delivery()` loaded all eight copied bindings and 139 tools: counts were 13, 12, 27, 9, 11, 32, 20, and 15.
- `diff -qr --exclude=profile.json <source>/environments <corrected>/environments` returned no differences. Thus bindings, environment definitions, tools/code, receipts, resources, and initial state match the source byte-for-byte.
- An inode comparison over every `environment/state` and `tools` file reported `shared_mutable_inodes=0`.

## Portability concerns

- The corrected first five `pyvenv.cfg` files contain destination-absolute paths to bundled bases. They are isolated from the original generation tree, but moving this prepared root requires rerunning the script for the new destination.
- Three Profiles still have declared host-external bases: eigenmode and generic FEM use `/data1/agent_world/toolgen_semiconductor_0917_rich/runtime/interpreters/cpython-3.11.16-linux-x86_64-gnu/bin`; EME uses `/usr/bin`. Imports pass on this host, not on an arbitrary host.
- Import success and `load_delivery()` prove packaging/runtime prerequisites, not numerical correctness of all 139 tools. Real tool and state-transition checks remain part of pipeline acceptance.

## Real-task candidates

1. `semiconductor_eigenmode_1`: strongest overall breadth, with 32 tools, 12 Record Sets, real meshes/workspace files, saved scans, mode solving, sweeps, convergence, overlap, and artifact export.
2. `semiconductor_cloud_fdtd_1`: 27 tools and 12 Record Sets spanning GDS, YAML, HDF5, notebooks, monitors, process layers, experiment lineage, cost/performance, and candidate ranking. Prefer local analysis tasks before any cloud/API-dependent action.
3. `semiconductor_generic_fem_pde_1`: no Record Set but a coherent file-backed workflow across seven source/mesh assets and 15 tools for mesh generation, steady/transient solves, convergence, thermal-optic sweeps, and rendering.

These are recommendations only; task generation should still require successful representative real calls and verifier acceptance.

## Review hardening

- The destination is now canonicalized after the nonexistence check and before containment/copy, so a symlinked parent cannot place the copy inside its own source.
- Preparation now requires the discovered package IDs to equal the eight explicit semiconductor IDs; unknown or incomplete deliveries fail before metadata reads or copying. Import checks therefore cannot silently accept an empty module list.
- Import probes now use `-B -I` and temporary `HOME`, `XDG_CACHE_HOME`, `XDG_CONFIG_HOME`, `TMPDIR`, Matplotlib, and Tidy3D paths. This prevents probe bytecode and user-cache writes through both bundled and external-base interpreters.
- A post-hardening run planned all eight real source environments, imported every declared probe module under the new isolation policy, and loaded 139 tools from the corrected root.
- The existing corrected artifact was not recopied: its manifest already records all eight expected environments with no import failures. These guards affect preparation safety and future reproductions, not its payload.
