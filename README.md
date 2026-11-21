# MPC compatibility tests

The OpenMPC Plugin Manager run inside Akai's own MPC firmware under emulation ([QEngine](https://github.com/Peyton-C/QEngine)),
on each machine and firmware in `machines.json`. The result is one page: scenarios down the side, machines across the top.

The harness itself (scenarios, the emulator driver) is in the Plugin Manager repository under `tests/emu`. This repository
picks the machines, fetches the firmware, runs the harness on each and makes the page (`report.py`).

## Latest results

**[Open the full report](https://jacob-sabella.github.io/mpc-compat-tests/)**: 141 of 141 checks passed on 2026-11-21.

| Machine | Passed |
|---|---|
| MPC Live III, MPC OS 3.10.1 | 25 of 25 |
| MPC Live III, MPC OS 3.10.0 | 25 of 25 |
| MPC One, MPC OS 3.10.1 | 25 of 25 |
| MPC X, MPC OS 3.9.1 | 25 of 25 |
| MPC One, MPC OS 3.9.1 | 25 of 25 |
| MPC Sample 1.3.0 | 16 of 16 |

These ran under emulation on a desktop, not on real hardware. They combine three runs: 95 checks from a full run, 28 addin checks
rerun after an installer fix, and 18 install checks rerun on the final rebased code.

## Running it

Actions > **compat** > Run workflow. The inputs say which Plugin Manager, installers (mpc-vst-plugins) and QEngine to test,
as `owner/repo@ref`, and optionally which machines and scenarios. A run is meant for when the Manager or the installers
change, or when new firmware comes out.

Each machine is split into shards, parallel jobs that each run part of its scenarios (`shards` in `machines.json`, balanced by the
desktop timings in `timings.json`; a small run gets fewer). The report job merges them (`merge.py`) and makes the page, the
`compat-page` artifact of the run; open `index.html`.

## Prebuilt machines

A job starts from a prebuilt image of its machine when one matches: the QEngine instance and the harness's "machine" snapshot
(MPC OS booted, the internal drive set up, the Enhancements switches on; no Plugin Manager). Without one, the job builds the instance
(about 12 minutes on a hosted runner) and boots MPC itself, and the run's summary says so.

Actions > **images** > Run workflow builds the images that are missing. Run it after changing the firmware, QEngine, QEMU (the
harness's Dockerfile) or the harness's `machine.py` or QEMU shim: the image tag is `<instance>-<machine id>`, and the id covers all of
those (`tests/emu/machine.py`), so a stale image is never used, just not found.

The images hold Akai's firmware files. They are **private** packages (`ghcr.io/<owner>/mpc-compat-machine`), read by this
repository's own token; never make the package public. The emulator host image (QEMU, no firmware) is in the same package, tagged
`host-<hash of the Dockerfile>`. Old tags are not removed by the workflow: delete them under the package's settings now and then.
Private package storage over GitHub Free's 500 MB is billed (about $0.25 per GB a month).

## Firmware

Akai's update images are not in this repository. Each job downloads one from the private release named in `machines.json`
(`firmware_repo`, tag `images`) with the `FIRMWARE_TOKEN` secret of the `firmware` environment, and checks it against the
sha256 in `machines.json`. Nothing built from an image is cached or uploaded.

Setup, once:

1. Create a fine-grained personal access token: resource owner your account, repository access *only* the firmware
   repository, permission *Contents: read-only*.
2. In this repository: Settings > Secrets and variables > Actions > New repository secret `FIRMWARE_TOKEN`, with that token.
   (Once this repository is public, move it to an environment `firmware` limited to the default branch; see the workflow.)

## Adding a machine or a firmware

Upload the image to the firmware repository's `images` release (and its line to its `SHA256SUMS`), then add a line to
`machines.json`: an instance name, the product code QEngine boots it as, the image's file name and its sha256.

## Cost

On a private repository, Actions minutes count against the account's free allowance (2,000 a month on GitHub Free).
Measured on a desktop, the full suite takes about 45 minutes on a Gen1 machine (32-bit, slower to emulate) and 26 on a Gen2;
hosted runners are slower, so expect a full run of every machine to use several hundred minutes. Pick machines and
scenarios in the run inputs for a smaller run. Public repositories do not use the allowance.
