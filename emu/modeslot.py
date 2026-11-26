"""The Plugin Manager app (open-mpc/plugin-manager, modeslot/) on the real MPC firmware.

The app's shim turns MPC's unused mode slot 0x2090 into a "Plugin Manager" tile in MENU whose page the Manager's daemon draws. It
loads into MPC and patches its code, so it only runs on the firmware builds it was profiled for (MPC OS 3.10.1: Gen2, Gen1 MPC and
Force) and must leave every other build alone. The scenario runs the published release zip on each machine:
  * on a 3.10.1 machine: the shim installs ("modeslot: installed ... named "Plugin Manager"" in MPC's log), MENU has the tile, the tile
    opens the page, and after the removal MPC runs without the shim;
  * on any other machine: the shim refuses ("modeslot: refused") and the app's web page still answers.
build.sh puts the zips beside the fixture's files; PM_MODESLOT_DIR names another folder (a build to check before it is published).
"""
import glob, os, re, time
import emu, ocr
from emu import slow
from run import scenario
from routes import guest, wait_mpc, preload, mpc_pid, tail

MENU_BUTTON = (60, 52)                 # the grid icon at the top left of the touch UI
NEW_PROJECT = (106, 770)
MENU_NEXT = (1240, 456)                # MENU's next-page arrow


def newest_zip(folder, prefix, arch):
    zips = sorted(glob.glob(os.path.join(folder, "%s-*-mpc-%s.zip" % (prefix, arch))), key=os.path.getmtime)
    return zips[-1] if zips else None


def mpc_log(t):
    """What the shim and the app's start script said inside MPC (the service's journal)."""
    return t.vm.sh("journalctl --no-pager -b -u acvs 2>&1 | grep -E 'modeslot|pm-app' || true", check=False, timeout=int(slow(60)))[1]


def install(t, prefix, what):
    """Put the release zip in the guest and run its install.sh; the restart is real. Returns (folder in the guest, the new MPC pid)."""
    folder = os.environ.get("PM_MODESLOT_DIR") or t.fx.src
    z = newest_zip(folder, prefix, t.arch)
    if not z: raise Fail("%s: no %s zip for %s in %s: run emu/build.sh" % (what, prefix, t.arch, folder))
    pid = mpc_pid(t)
    t.vm.put(z, "/tmp/%s.zip" % prefix)
    rc, out = guest(t, "rm -rf /tmp/ms && mkdir /tmp/ms && cd /tmp/ms && unzip -q /tmp/%s.zip && cd %s-* && sh install.sh -y" % (prefix, prefix), what + "-install", 600)
    t.check(rc == 0 and "Done." in out, "%s: install.sh exit %d: %s" % (what, rc, tail(out)))
    return os.path.basename(z), wait_mpc(t, other_than=pid)


def open_menu_tile(t, what):
    """MENU, then the Plugin Manager tile: what the screen shows after each step is kept in the results. A restarted MPC draws nothing for
    minutes on a slow guest, so MENU is tapped until its grid is up (PREFERENCES: only MENU has it, a track screen can also say
    "Plugin Manager"); the tile can be on the grid's second page."""
    t.vm.wait_stable(secs=300)
    end = time.time() + slow(180)
    while True:
        t.vm.settle()
        ws = ocr.words(t.shot(what + "-menu"))
        if ocr.find(None, "PREFERENCES", ws) or time.time() > end: break
        if ocr.find(None, "RECENT", ws): t.vm.tap(*NEW_PROJECT)     # "Select Project", what a restarted MPC opens on
        else: t.vm.tap(*MENU_BUTTON)
        time.sleep(slow(3))
    t.check(ocr.find(None, "PREFERENCES", ws), "%s: MENU opens" % what)
    if not ocr.find(None, "Manager", ws):
        t.vm.tap(*MENU_NEXT)
        t.vm.settle()
        ws = ocr.words(t.shot(what + "-menu2"))
    t.check(ocr.find(None, "Manager", ws), "%s: MENU shows a Plugin Manager tile" % what)
    t.vm.tap_text("MANAGER", fresh=True)
    t.vm.settle()


def expect_page(t, what, *texts):
    t.vm.wait_stable()
    ws = ocr.words(t.shot(what + "-page"))
    for text in texts:
        t.check(ocr.find(None, text, ws), "%s: the slot's page shows %r" % (what, text))


@scenario
def modeslot_app(t):
    """The Plugin Manager app from its zip: on a 3.10.1 machine the tile shows the Manager's own page and the web page answers; elsewhere
    the shim refuses but the app still starts, so the web page answers; a clean removal."""
    expect = t.external
    _, pid = install(t, "Plugin-Manager-App", "pmapp")
    log = mpc_log(t)
    end = time.time() + slow(90)
    while time.time() < end and not t.vm.out("pidof openmpc-pm-daemon || true").strip(): time.sleep(1)
    t.check(t.vm.out("pidof openmpc-pm-daemon || true").strip(), "pm-app: the app's daemon is running")
    exe = t.vm.out("readlink /proc/$(pidof openmpc-pm-daemon | cut -d' ' -f1)/exe || true").strip()
    t.eq(exe, "/data/mpc-addins/pm-app/openmpc-pm-daemon", "pm-app: the daemon that runs is the app's own")
    t.has(log, "modeslot: started /data/mpc-addins/pm-app/pm-app-start.sh", "pm-app: the shim ran the start script")
    state = ""
    end = time.time() + slow(60)
    while time.time() < end and '"version"' not in state:
        state = t.vm.out("wget -q -O - http://127.0.0.1:8790/api/state 2>/dev/null || true")
        time.sleep(2)
    t.has(state, '"version"', "pm-app: the web page's state answers on port 8790")
    if expect:
        t.has(log, "modeslot: installed:", "pm-app: the shim installed on this firmware")
        open_menu_tile(t, "pmapp")
        expect_page(t, "pmapp", "PLUGIN MANAGER")
    else:
        t.has(log, "modeslot: refused", "pm-app: the shim refuses this firmware")
        t.hasnt(log, "modeslot: installed:", "pm-app: nothing is patched on this firmware")
    pid = mpc_pid(t)
    rc, out = guest(t, "sh /data/mpc-addins/pm-app/uninstall.sh -y", "pmapp-uninstall")
    t.check(rc == 0 and "Removed" in out, "pm-app: uninstall.sh exit %d: %s" % (rc, tail(out)))
    wait_mpc(t, other_than=pid)
    t.hasnt(preload(t), "modeslot.so", "pm-app: MPC no longer preloads it")
