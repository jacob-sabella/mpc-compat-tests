# MPC compatibility tests

The OpenMPC Plugin Manager run inside Akai's own MPC firmware under emulation ([QEngine](https://github.com/Peyton-C/QEngine)),
on each machine and firmware in `machines.json`. The result is one page: scenarios down the side, machines across the top.

The tests and the emulator driver are in `emu/`. The rest of this repository picks the machines, fetches the firmware, runs the
tests on each and makes the page (`report.py`). Locally, with `plugin-manager`, `QEngine` and `mpc-vst-plugins` checked out
beside this repository (or named by `PM`, `QENGINE` and `MPC_VST`): `emu/build.sh`, then `emu/run.sh`. See `emu/README.md`.

## Credits

The emulation is [QEngine](https://github.com/Peyton-C/QEngine), by [Peyton Cashin (Peyton-C)](https://github.com/Peyton-C) and [contributors](https://github.com/Peyton-C/QEngine/graphs/contributors). These tests would not exist without it.

## Latest results

**[Open the full report](https://open-mpc.github.io/mpc-compat-tests/)**: 141 of 141 checks passed on 2026-11-21.

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

Every four hours the **compat** workflow checks the code under test (`target.json`: the Manager's and the installers' branches)
and runs the cells with no result on its current commits (`plan.py rolling`), at most six jobs of 45 to 60 minutes, one machine
each; the rest wait for the next run. When neither branch has moved since every cell ran, it runs nothing: quiet branches, quiet
schedule. A new scenario in the harness, or a machine added to `machines.json`, is a cell with no result, so it runs too.

Actions > **compat** > Run workflow runs every scenario instead (or the machines and scenarios named in the inputs; or, with
*rolling*, what the schedule would). Empty refs mean `target.json`'s. A full run is for new firmware, or before a release.

Either way, each machine's scenarios are split into jobs (`plan.py`, balanced by the desktop timings in `timings.json`), and the
report job adds their results to the ledger (`ledger/`), remakes the page from the whole ledger (`docs/`, served by Pages) and
commits both to `main`.

## The ledger

`ledger.py` keeps every scenario result as a record: the machine, the outcome, when, and the commits of the Manager, the installers
and QEngine it ran. `report.py` makes the page from the ledger: each cell is the newest result for that scenario on that machine,
with its age and the last few results before it, marked when it ran code other than the commit under test (`--pm`, `--mpc-vst`), when
its commit was not recorded, or when it both passed and failed on the same code (flaky). So the page can be made from many small
runs instead of one full one.

```
python3 ledger.py add emu/results --pm ../plugin-manager --mpc-vst ../mpc-vst-plugins --qengine ../QEngine   # a local run
python3 ledger.py add-art art --url <run url> --at <when>       # a compat run's downloaded results-* artifacts
python3 ledger.py show --scenario self_update
python3 report.py --pm open-mpc/plugin-manager@main             # out/site/index.html
```

Adding is idempotent: what is already in the ledger is not added again. The ledger is `ledger/` (records/ by month, one file per run;
shots/, the screen captures, which `ledger.py prune-shots` trims to the latest result of each cell).

## Prebuilt machines

A job starts from a prebuilt image of its machine when one matches: the QEngine instance and the harness's "machine" snapshot
(MPC OS booted, the internal drive set up, the Enhancements switches on; no Plugin Manager). Without one, the job builds the instance
(about 12 minutes on a hosted runner) and boots MPC itself, and the run's summary says so.

Actions > **images** > Run workflow builds the images that are missing. Run it after changing the firmware, QEngine, QEMU (the
harness's Dockerfile) or the harness's `machine.py` or QEMU shim: the image tag is `<instance>-<machine id>`, and the id covers all of
those (`emu/machine.py`), so a stale image is never used, just not found.

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
2. In this repository: Settings > Environments > `firmware` (limited to the default branch) > Add secret `FIRMWARE_TOKEN`, with
   that token.

## Adding a machine or a firmware

Upload the image to the firmware repository's `images` release (and its line to its `SHA256SUMS`), then add a line to
`machines.json`: an instance name, the product code QEngine boots it as, the image's file name and its sha256.

## Cost

On a private repository, Actions minutes count against the account's free allowance (2,000 a month on GitHub Free).
Measured on a desktop, the full suite takes about 45 minutes on a Gen1 machine (32-bit, slower to emulate) and 26 on a Gen2;
hosted runners are slower, so expect a full run of every machine to use several hundred minutes. Pick machines and
scenarios in the run inputs for a smaller run. Public repositories do not use the allowance.
