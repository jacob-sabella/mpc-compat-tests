# MPC compatibility tests

The OpenMPC Plugin Manager run inside Akai's own MPC firmware under emulation ([QEngine](https://github.com/Peyton-C/QEngine)),
on each machine and firmware in `machines.json`. The result is one page: scenarios down the side, machines across the top.

The harness itself (scenarios, the emulator driver) is in the Plugin Manager repository under `tests/emu`. This repository
picks the machines, fetches the firmware, runs the harness on each and makes the page (`report.py`).

## Running it

Actions > **compat** > Run workflow. The inputs say which Plugin Manager, installers (mpc-vst-plugins) and QEngine to test,
as `owner/repo@ref`, and optionally which machines and scenarios. A run is meant for when the Manager or the installers
change, or when new firmware comes out.

Each machine is its own job. The page is the `compat-page` artifact of the run; open `index.html`.

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
