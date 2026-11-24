# Emulator tests

The Plugin Manager running inside real MPC firmware, in QEMU, driven the way a person drives it: taps on the screen, then a look at
what is on the device. [QEngine](https://github.com/Peyton-C/QEngine) boots the unmodified firmware; this folder adds the harness.

    emu/build.sh          # the Manager for armv7 and aarch64, pointed at the fixture catalog, plus the fixture packages
    emu/run.sh            # every scenario on a Gen1 and a Gen2 machine, side by side

`run.sh -k self_update` runs one scenario, `-i mpc-g2-391` picks an instance, `--list` names the scenarios, `--label` picks a build.
Results: `results/<instance>/<build>/summary.json`, and per scenario the screen captures and the device's own logs.

Three tiers, from a quick look to everything:

    emu/run.sh --tier quick                  # four scenarios on one Gen1 and one Gen2
    emu/run.sh --tier changed [--since REF]  # what the files changed since REF (default origin/main) need
    emu/run.sh --tier full                   # every scenario on all seven machines

`changed` (`tiers.py`) skips docs and the offline tests, runs the Manager's scenarios for a change in `src/`, `vst/` or `daemon/`, and
only the scenarios edited in a scenario file. Anything else, the harness included, means the full matrix. It does not see a change
in the mpc-vst-plugins installers: after one, use `full`.

All machines of a run share one pool of emulators (`jobs.py`). `--cap N` is how many run at once (default from the host's cores
and memory, about 10 on 32 cores; or `PM_EMU_CAP`), `--jobs N` limits how many of them work on one machine, and `--plan` prints the
order and an estimate without booting anything. Scenarios start longest first across all machines, from the last results and
`timings.json`. Each runs in its own container on a reflink copy of the machine's stage. A machine's ready snapshot is made once and
kept by the build's content (`state/<instance>/.ready/`), so a build seen before skips it. CI has shards and runs one at a time. A loaded
host is slower: raise `PM_EMU_TIMESCALE` if waits start to run out.

## What it needs

* Docker, with arm and arm64 user emulation registered (binfmt) for the cross builds. No root, no KVM.
* A QEngine checkout with instances built from Akai's update images (not in this repository, see below):
  `QENGINE=<path>`, default `../QEngine` beside this repository.
* An mpc-vst-plugins checkout for the wrapper and release tools: `MPC_VST=<path>`. The fixture packages are built with its
  installer and uninstaller, so a fix in those scripts is tested by pointing `MPC_VST` at the checkout that has it.
* `docker build -t pm-emu-host emu` once: QEMU, a VNC client, ssh and tesseract in one image.

Building the instances (once; about ten minutes each, needs the firmware images in `FIRMWARE_DIR`):

    emu/qe scripts/build_scripts/new_instance.sh --name mpc-g1-391  --product-code ACVA --firmware $FIRMWARE_DIR/MPC-3.9.1-Gen1-update.img
    emu/qe scripts/build_scripts/new_instance.sh --name mpc-g2-3100 --product-code ACVG --firmware $FIRMWARE_DIR/MPC-3.10.0-Gen2-update.img

Any other firmware or model is another instance (`--name`, `--product-code`), run with `./run.sh -i <name>`; `mpc-g1-3101` and
`mpc-g2-3101` are MPC OS 3.10.1. The scenarios do not name a firmware: the preparation asks the MPC binary whether it loads plugins
from `<drive>/VST2 Plugins`, sets the machine up accordingly, and the few checks that differ by design read `t.external`.

## How it works

* `qe` runs a command in the `pm-emu-host` container; `in-container` gives each machine its own container, so every VM uses the same
  ports and several run at once.
* `shim/qemu-system-aarch64` sits in front of QEMU: each instance disk gets a qcow2 overlay in `state/<instance>/<build>/`, so the
  instance is never written, and the guest's network is restricted to one address, the fixture server.
* `emu.py` is the machine: boot, ssh, the QEMU monitor (snapshots, power cut), the screen and touches over VNC, OCR of the screen,
  the Manager's web API from inside the guest, and `Fixture`, the catalog and zips the guest downloads from.
* `machine.py` boots an instance and sets it up as the model has it (the internal drive, on MPC OS 3.10.1+ the Enhancements
  switches), with no Manager, and snapshots that as "machine" in `state/<instance>/machine/`. Done once per instance, and again only
  when the firmware, QEngine, QEMU, `machine.py` or the shim change (`machine.py --id`); CI keeps it as a prebuilt image.
  `run.sh -i INSTANCE --machine` makes just that.
* `prepare.py` starts from a copy of the machine snapshot, installs the build's Manager, opens it on a track and snapshots that as
  "ready". That is done once per instance and build (three to five minutes); every scenario then resumes it in about three seconds.
* `scenarios.py` holds the scenarios; `run.py` runs them and writes the results.

A failed scenario also keeps `journal-end.txt`: the guest's log of MPC and the apply, the kernel's, and any crash reports. An APPLY that never
started fails at once and says whether MPC restarted (`journal-no-launch.txt`).

A scenario queues changes with a tap or through the Manager's web API (the same code path as the phone page), runs APPLY, and then
asserts on the device: `/tmp/openmpc-pm/{device.txt,apply.log,result}`, the plugin folders, `MPC.settings`, the running MPC's
environment, and what the screen shows.

## The scenarios

| Scenario | What it does |
|---|---|
| `fresh_install` | the Manager as first installed: listed, skin drawn, catalog read, install location found |
| `plugin_install_update_remove` | a plugin installed with a tap, updated, removed |
| `addin_install_remove` | an addin installed and removed; MPC really preloads it in between |
| `self_update` | the Manager updates itself |
| `theme_survives_update` | the dark theme is still there after the Manager's own update |
| `long_press` | slow presses on a button count once each |
| `power_cut_mid_apply` | QEMU is killed while an update keeps its safety copy; after the reboot the update goes through |
| `full_disk` | the install drive fills up: the message says so, a removal still works, MPC.settings stays whole |
| `drive_name_with_space` | an exFAT card called "EMU CARD": registered, installed to, updated, removed |
| `exfat_noexec_drive` | a noexec drive is refused with its reason, installs go to the internal drive |
| `readonly_addins` | `/data/mpc-addins` read-only: an addin is refused before MPC stops, a plugin still installs |
| `catalog_retry` | the catalog cannot be fetched at first; the Manager tries again on its own |
| `vst2_plugin_json` | the MPC OS 3.10.1 install path: `VST2 Plugins` and a correct plugin.json (on older firmware the switch is written into MPC.settings) |

The MPC Sample (`mpc-sample-130`, AC50, built with `--product-code AC50 --firmware $FIRMWARE_DIR/MPC_SAMPLE_1.3.0_UPDATE.exe`, and
`mpc-sample-140` from `MPC_Sample_1.4.0_Updater.exe`) has no plugin browser, so it runs its own scenarios instead (`sample_scenarios.py`, machine in `sample.py`): `sample_install`, `sample_plugin_loads`,
`sample_reboot` and `sample_uninstall`, with the install scripts, the RACK addin's apply helper and the card's exec bind mount. See
FINDINGS.md. A scenario can record a finding (what the device does that a user should know of) without failing.

### The routes outside the Manager (`routes.py`, on every machine, the Sample included)

| Scenario | What it does |
|---|---|
| `zip_install_by_hand`, `zip_install_by_hand_t` | a release zip's `install.sh` and `uninstall.sh` by hand, without and with `-t`: install, update over the older version, remove |
| `zip_addin_by_hand` | an addin's `install.sh` and `uninstall.sh` by hand |
| `store_install_update_remove`, `store_install_update_remove_t` | `mpc-store.sh` on the device, without and with `-t` |
| `store_list`, `store_dependency`, `store_device_limited` | its list, a plugin that brings another, entries limited to a device and the reverse |
| `store_addin_install_remove`, `store_sync_prune` | an addin through it; `sync` and `prune` |
| `desktop_install_update_remove`, `desktop_addin_install_remove` | the desktop app (`tools/desktop`), driven over its local web API |

Each checks that the files land where MPC on that device loads them, that MPC.settings and plugin.json are right, and that MPC loads
the plugin or addin after the route's restart. `run.sh -k zip_ -k store_ -k desktop_` runs them alone. `run.sh -i a -i b` runs machines at once.

The internal drive is a disk the harness adds to the VM (`shim/qemu-system-aarch64`). Attached drives are loop images mounted under `/media/<label>` in the guest, with the mount options a device was seen to use. The power cut
is QEMU's `quit` after a guest `sync`: what was written is on the disk, nothing after it is.

## Testing a fix

`build.sh --ref <commit> --label before` builds the Manager as it was at a commit; `run.sh --label before -k <scenario>` runs a
scenario against it. A scenario written for a bug fails there and passes on the working tree.

## Firmware

The update images are Akai's and are not redistributable: they are never committed, and neither are the instances built from them.
See `SPIKE.md` for what was tried on which firmware and what the emulator cannot show, and `CI.md` for running this in CI.

## By hand

    emu/vm up mpc-g2-3100 work ready     # resume the ready snapshot and keep it running
    emu/vm sh 'cat /tmp/openmpc-pm/device.txt'
    emu/vm tap /tmp/s.png 640,400 s2      # tap, wait two seconds, capture
    emu/vm down
