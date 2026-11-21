"""The ways a user installs, updates and removes plugins and addins without the Plugin Manager, on every machine (the MPC Sample's
too): a release zip's own install.sh and uninstall.sh run by hand (zip_*), tools/mpc-store.sh on the device (store_*), and the desktop
installer app over SSH from a computer (desktop_*), all from mpc-vst-plugins (MPC_VST).

Each checks what a user needs: the files are where MPC on this machine loads plugins from (MPC OS 3.10.1: <drive>/VST2 Plugins with a
plugin.json; older MPC OS: a Synths folder with its entry in MPC.settings; the MPC Sample: the card's Synths folder), the entries
are right, and MPC really loads the plugin after the route's restart:
  * MPC OS: the fixture plugin Emu Probe (a real VST2 instrument) is put on a plugin track (prepare.open_plugin) and must say that MPC
    loaded it (/tmp/emu-probe.log) with the installed file mapped. A plugin MPC does not list cannot be picked.
  * MPC Sample: the fixture effect Emu FX is put into the project by the RACK addin's apply helper and the card's Synths folder is
    bound exec, as sample_scenarios.py does (the only way a plugin loads on a Sample).
A scenario without -t (the route's default) fails where the default does not put the plugin where MPC loads it; the -t variants
cover the path that works. A default that stops is followed by the same step with -t, so the later steps are still covered.

Where the default and -t name the same folder, the zip and store routes do one real restart and load per variant: without -t the
first install, with -t the update, so that folder is proved after both. Where they are different folders (two_homes), each variant
proves its own folder after the install and after the update. The other steps run with MPC stopped (mpc_stopped) and check the same
files and settings. They use the installers' -n, or the default path with a stand-in systemctl that records what the script asked
for: a script that forgets to restart MPC, or restarts it twice, still fails. The desktop app runs its commands over its own SSH, so
it keeps its real restarts, and loads the plugin after the install and after the update.

What the emulator does not have that a device has: a Gen1 unit before MPC OS 3.10.1 keeps its internal SD card at /sdcard (rw, exec;
docs/MPC_OS_REFERENCE.md); the emulator's /sdcard is a folder on the read-only root. The default-target scenarios mount a stand-in
drive there on those machines (sdcard_standin)."""
import json, os, shlex, socket, subprocess, tempfile, time, urllib.request, urllib.error
import emu, prepare, sample
import sample_scenarios as ss
from emu import Fail, log, slow, FIXTURE_ADDR, FIXTURE_PORT
from run import route

ADDIN = "/data/mpc-addins/emu-addin"
STORE = "/tmp/mpc-store.sh"
CATALOG_TSV = "http://%s/catalog.tsv" % FIXTURE_ADDR


class P:
    """The plugin a route installs: Emu Probe (an instrument, 1.0.0 and 1.0.1) on MPC OS, Emu FX (an effect, only for the MPC Sample in
    the catalog) on the Sample."""
    def __init__(self, t):
        if t.sample: self.id, self.name, self.zipname, self.so, self.log = "emu-fx", "Emu FX", "Emu-FX", "emu_fx.so", ss.FXLOG
        else: self.id, self.name, self.zipname, self.so, self.log = "emu-probe", "Emu Probe", "Emu-Probe", "emu_probe.so", "/tmp/emu-probe.log"
        self.folder = "acme - VST - " + self.name

    def zip(self, ver):
        return "%s-%s" % (self.zipname, ver)


# ---- the machine ---------------------------------------------------------------------------------------------------------------

def mpc_pid(t):
    if t.sample: return sample.mpc_pid(t.vm)
    return t.vm.out("pidof MPC || true").strip().split(" ")[0]


def wait_mpc(t, other_than="", secs=180):
    """MPC (MPC Sample) running, a new process when other_than is given; its pid, or "" (a failed check) when it is not."""
    end = time.time() + slow(secs)
    while time.time() < end:
        p = mpc_pid(t)
        if p and p != other_than: return p
        time.sleep(0.5)
    t.check(False, "MPC is running%s" % (" again" if other_than else ""))
    return ""


def preload(t):
    """LD_PRELOAD of the running MPC."""
    p = wait_mpc(t)
    return t.vm.out("tr '\\0' '\\n' < /proc/%s/environ | grep '^LD_PRELOAD=' || true" % p).strip() if p else ""


def guest(t, cmd, name, timeout=300):
    """Run a user's command in the guest; its output is kept in the results (<name>.txt). Returns (exit status, output)."""
    run = "export PATH=%s/bin:$PATH; %s" % (CTL_DIR, cmd) if getattr(t, "recording", False) else cmd
    rc, out = t.vm.sh(run + " 2>&1", check=False, timeout=int(slow(timeout)))
    with open(os.path.join(t.out, name + ".txt"), "a") as f: f.write("$ %s\n%s\n[exit %d]\n\n" % (cmd, out, rc))
    return rc, out


def tail(out, n=6):
    return " | ".join(out.strip().splitlines()[-n:])


# ---- MPC stopped, for the steps that only look at files --------------------------------------------------------------------------

CTL_DIR = "/tmp/pm-emu-ctl"             # the guest's /tmp, written during a scenario: no snapshot is saved after this, so none holds it
CTL_LOG = CTL_DIR + "/calls"
CTL_SCRIPT = """#!/bin/sh
# The harness's systemctl for steps run with MPC stopped: a stop, start or restart of MPC's service is written down and not done.
case "$1" in
    stop|start|restart)
        case "${2%.service}" in acvs|ac50|inmusic-mpc) echo "$1" >> "@LOG@"; exit 0 ;; esac ;;
esac
exec "@REAL@" "$@"
"""


def mpc_stopped(t):
    """Stop MPC for real, so the guest stops burning CPU, and from here on run the user's commands (guest()) with the recording
    systemctl first in PATH. Starts with an empty record."""
    if not getattr(t, "ctl_ready", False):
        real = t.vm.out("command -v systemctl").strip()
        if not real.startswith("/"): raise Fail("no systemctl in the guest: %r" % real)
        t.vm.write(CTL_DIR + "/bin/systemctl", CTL_SCRIPT.replace("@LOG@", CTL_LOG).replace("@REAL@", real))
        t.vm.sh("chmod 755 %s/bin/systemctl && PATH=%s/bin:$PATH systemctl --version >/dev/null" % (CTL_DIR, CTL_DIR))   # fails on a noexec /tmp
        t.ctl_ready = True
    t.recording = False
    t.vm.sh("systemctl stop %s" % ("ac50" if t.sample else "acvs"), timeout=int(slow(120)))
    end = time.time() + slow(60)
    while mpc_pid(t) and time.time() < end: time.sleep(0.5)
    if mpc_pid(t): raise Fail("MPC did not stop")
    t.vm.sh(": > %s" % CTL_LOG)
    t.recording = True


def real_mpc(t):
    """The next command stops and starts the real MPC."""
    t.recording = False


def ctl_calls(t):
    """What was asked of MPC's service since the last look."""
    calls = t.vm.read(CTL_LOG).split()
    t.vm.sh(": > %s" % CTL_LOG)
    return calls


def asked_restart(t, what):
    """The script stopped MPC once and started it once, and nothing really started it."""
    t.eq(ctl_calls(t), ["stop", "start"], "%s: MPC is stopped once and started once" % what)
    t.eq(mpc_pid(t), "", "%s: MPC stayed down under the stand-in systemctl" % what)


def sdcard_standin(t):
    """A Gen1 unit before MPC OS 3.10.1 has its internal SD card at /sdcard (ext4, rw, exec), the installers' default; the emulator has
    a folder on the read-only root there. A drive is mounted over it, with a Synths folder."""
    if t.sample or t.external or t.gen != 1: return
    t.vm.sh("dd if=/dev/zero of=/data/emu-sdcard.img bs=1M count=0 seek=400 2>/dev/null && mke2fs -q -t ext4 -F /data/emu-sdcard.img "
            "&& mount -o loop /data/emu-sdcard.img /sdcard && mkdir -p /sdcard/Synths")


def homes(t):
    """Where MPC on this machine loads plugins from."""
    if t.external or t.sample: return [t.synths]
    return [t.synths] + (["/sdcard/Synths"] if t.gen == 1 else [])


def two_homes(t):
    """The route's default folder and the -t folder (t.synths) are different places on this machine's layout."""
    return any(h != t.synths for h in homes(t))


def places(t):
    """Every folder an installer might have put a plugin in."""
    out = []
    for d in homes(t) + ["/sdcard/Synths", "/nvme/Synths", "/storage/Synths", "/media/az01-internal/Synths", sample.SYNTHS]:
        for x in (d, d.rsplit("/", 1)[0] + "/VST2 Plugins"):
            if x not in out: out.append(x)
    return out


def found(t, folder):
    """The places that have the plugin folder `folder`."""
    test = "; ".join("[ -d %s ] && echo %s" % (shlex.quote("%s/%s" % (d, folder)), shlex.quote(d)) for d in places(t))
    return [l for l in t.vm.out(test + "; true").splitlines() if l]


# ---- what must be true after a route ---------------------------------------------------------------------------------------------

def check_installed(t, p, ver, what, old_pid, restarted=True):
    """The plugin at `ver` where MPC loads plugins from, once, with its entry; MPC restarted (restarted=False: a step with MPC stopped,
    whose restart is checked from the record). Returns the Synths (or VST2 Plugins) folder it is in, or None."""
    at = found(t, p.folder)
    good = [d for d in at if d in homes(t)]
    t.check(good, "%s: %s is where MPC loads plugins from on this machine (%s); it is in: %s" % (what, p.name, " or ".join(homes(t)), at or "none of " + ", ".join(places(t))))
    t.check(len(at) <= 1, "%s: one copy of %s (%s)" % (what, p.name, at))
    d = (good or at or [None])[0]
    if d is None: return None
    folder = "%s/%s" % (d, p.folder)
    t.check(t.vm.exists("%s/%s" % (folder, p.so)), "%s: %s is in %s" % (what, p.so, folder))
    t.has(t.vm.read(folder + "/mpc-plugin.json"), '"version": "%s"' % ver, "%s: the folder's manifest" % what)
    if t.external:
        t.has(t.vm.read(folder + "/plugin.json"), '"%s": "%s"' % ("file64" if t.arch == "aarch64" else "file32", p.so), "%s: plugin.json names the binary for this CPU" % what)
    else:
        f = 'file="%s/%s"' % (folder, p.so)
        txt = t.settings_when(lambda x: f in x)
        t.eq(txt.count(f), 1, "%s: %s lists %s/%s" % (what, os.path.basename(t.settings), folder, p.so))
        t.has(txt, 'name="%s"' % ("pluginList-arm-64bit" if t.arch == "aarch64" else "pluginList-arm"), "%s: the plugin list for this CPU" % what)
    t.settings_ok(os.path.basename(t.settings))
    if restarted:
        new = wait_mpc(t)
        t.check(new and new != old_pid, "%s: MPC was restarted (pid %s, before %s)" % (what, new, old_pid))
    return d


def probe_loaded(t):
    """Has the running MPC created the probe (a 'main' line of its pid in the probe's log)?"""
    pid = mpc_pid(t)
    return bool(pid) and any(l.split()[:1] == ["main"] and l.split()[-1] == pid for l in t.vm.read("/tmp/emu-probe.log").splitlines())


def check_loads(t, p, d, what):
    """MPC loads the plugin installed in `d` (check_installed), after the route's restart."""
    if d is None: return
    so = "%s/%s/%s" % (d, p.folder, p.so)
    if t.sample:
        if d != sample.SYNTHS: return
        if not getattr(t, "rack", False): ss.install_rack(t); t.rack = True
        if ss.in_project(t) == 0: ss.put_in_project(t)
        if not ss.card_line(t, sample.SYNTHS): ss.bind_exec(t)
        pid = sample.restart_mpc(t.vm, settle=0)
        t.eq(ss.loads(t, pid), (True, True, True), "%s: MPC Sample loads %s from the project (its log, the effect's own line, mapped)" % (what, p.name))
        return
    try:
        prepare.open_plugin(t.vm, p.name, "acme", done=lambda: probe_loaded(t), keep=t.out)
    except Fail as e:
        t.check(False, "%s: MPC loads %s on a plugin track: %s" % (what, p.name, e)); return
    end, maps = time.time() + slow(30), ""    # the probe's first line comes before MPC has finished with the library: give it a moment
    while so not in maps and time.time() < end:
        maps = t.vm.out("cat /proc/%s/maps 2>/dev/null || true" % mpc_pid(t))
        if so not in maps: time.sleep(1)
    t.check(so in maps, "%s: MPC has %s mapped" % (what, so))
    t.shot("loaded-" + what.split(":")[0].replace(" ", "-").replace("/", "")[:40])


def check_removed(t, p, what):
    """Nothing of the plugin is left where MPC would find it, and its entry is gone."""
    t.eq(found(t, p.folder), [], "%s: folders of %s left" % (what, p.name))
    txt = t.settings_when(lambda x: p.so not in x)
    t.hasnt(txt, p.so, "%s: %s" % (what, os.path.basename(t.settings)))
    t.settings_ok(os.path.basename(t.settings))


def addin_installed(t, what, before):
    t.check(t.vm.exists(ADDIN + "/emu_addin.so"), "%s: the addin's .so is in %s" % (what, ADDIN))
    env = preload(t)
    t.has(env, ADDIN + "/emu_addin.so", "%s: MPC's LD_PRELOAD" % what)
    for lib in [x for x in before.replace("LD_PRELOAD=", "").split(":") if x]:
        t.has(env, lib, "%s: MPC's LD_PRELOAD keeps what it had" % what)
    end = time.time() + slow(30)
    while "MPC" not in t.vm.read("/tmp/emu-addin.loaded") and time.time() < end: time.sleep(0.5)
    t.has(t.vm.read("/tmp/emu-addin.loaded"), "MPC", "%s: the addin ran inside MPC" % what)


def addin_removed(t, what, before):
    t.check(not t.vm.exists(ADDIN), "%s: the addin's folder is gone" % what)
    t.eq(preload(t), before, "%s: MPC's LD_PRELOAD is what it was" % what)
    time.sleep(slow(5))
    # The marker names each process that loaded the addin. The old MPC's crashpad_handler inherits its LD_PRELOAD and can add a line
    # while it shuts down, so what counts is MPC itself: no MPC line, and the running MPC does not have the library mapped.
    who = t.vm.out("cat /tmp/emu-addin.loaded 2>/dev/null || true")
    if who:
        with open(os.path.join(t.out, "addin-marker.txt"), "a") as f: f.write("%s: %r\n" % (what, who))
    t.check("MPC" not in who.split("\n"), "%s: MPC no longer loads the addin (marker: %r)" % (what, who))
    p = wait_mpc(t)
    t.hasnt(t.vm.out("cat /proc/%s/maps 2>/dev/null || true" % p) if p else "", "emu_addin.so", "%s: the running MPC's memory" % what)


# ---- route 1: a release zip's own install.sh and uninstall.sh, by hand -----------------------------------------------------------

def zip_route(t, target):
    p = P(t)
    if target is None: sdcard_standin(t)
    tf = "" if target is None else " -t %s" % shlex.quote(target)
    how = "install.sh%s" % (" -t" if target else " (no -t)")
    live = ("1.0.0", "1.0.1") if two_homes(t) else ("1.0.1",) if target else ("1.0.0",)     # real restart and load; the rest: -n, MPC stopped
    d = None; last = ""
    for ver in ("1.0.0", "1.0.1"):
        real = ver in live
        if real: real_mpc(t)
        else: mpc_stopped(t)
        pid = mpc_pid(t)
        n = "" if real else " -n"
        last = ss.unpack(t, p.zip(ver), t.arch)
        rc, out = guest(t, "cd %s && sh install.sh -y%s%s" % (last, n, tf), "install-" + ver)
        if not t.check(rc == 0 and "Done." in out, "%s of %s %s: exit %d: %s" % (how, p.name, ver, rc, tail(out))):
            if target is not None: return
            log("    installing with -t %s, to go on" % t.synths)
            rc, out = guest(t, "cd %s && sh install.sh -y%s -t %s" % (last, n, shlex.quote(t.synths)), "install-%s-t" % ver)
            if not t.check(rc == 0, "install.sh -t %s of %s %s: %s" % (t.synths, p.name, ver, tail(out))): return
        d = check_installed(t, p, ver, "%s %s" % (how, ver), pid, restarted=real)
        if real: check_loads(t, p, d, "%s %s" % (how, ver))
        else:
            t.eq(ctl_calls(t), [], "%s %s -n: asks nothing of MPC's service" % (how, ver))
            t.eq(mpc_pid(t), "", "%s %s -n: MPC stays stopped" % (how, ver))
    mpc_stopped(t)
    rc, out = guest(t, "cd %s && sh uninstall.sh -y%s" % (last, tf), "uninstall")
    t.check(rc == 0 and "Removed" in out, "uninstall.sh%s: exit %d: %s" % (tf, rc, tail(out)))
    asked_restart(t, "uninstall.sh%s" % (" -t" if target else " (no -t)"))
    check_removed(t, p, "uninstall.sh%s" % (" -t" if target else " (no -t)"))


@route
def zip_install_by_hand(t):
    """A release zip's own install.sh and uninstall.sh run by hand with no -t (their default folder): install, update over the older
    version, remove. The plugin must end up where MPC loads it from, and MPC must load it."""
    zip_route(t, None)


@route
def zip_install_by_hand_t(t):
    """The same with -t naming the folder MPC loads plugins from on this machine (VST2 Plugins on MPC OS 3.10.1, the card's Synths on a
    Sample)."""
    zip_route(t, t.synths)


@route
def zip_addin_by_hand(t):
    """An addin's release zip installed with its install.sh (default folder, then -t) and removed with the uninstall.sh in its folder:
    MPC preloads it after the restart, and no longer does after the removal."""
    before = preload(t)
    d = ss.unpack(t, "Emu-addin-1.0.0", t.arch)
    for tf in ("", " -t " + ADDIN):
        what = "addin install.sh" + (" -t" if tf else "")
        t.vm.sh("rm -f /tmp/emu-addin.loaded")
        rc, out = guest(t, "cd %s && sh install.sh -y%s" % (d, tf), "addin-install")
        if not t.check(rc == 0 and "Done." in out, "%s: exit %d: %s" % (what, rc, tail(out))): return
        addin_installed(t, what, before)
        pid = mpc_pid(t)
        t.vm.sh("rm -f /tmp/emu-addin.loaded")
        rc, out = guest(t, "sh %s/uninstall.sh -y%s" % (ADDIN, tf), "addin-uninstall")
        t.check(rc == 0 and "Removed" in out, "%s/uninstall.sh%s: exit %d: %s" % (ADDIN, tf, rc, tail(out)))
        wait_mpc(t, other_than=pid)
        addin_removed(t, "addin uninstall.sh" + (" -t" if tf else ""), before)


# ---- route 2: tools/mpc-store.sh on the device -------------------------------------------------------------------------------------

def store(t, args, target=None, name="store", by_option=False, timeout=600):
    """sh mpc-store.sh -y [-t target] <args>, pointed at the fixture's catalog.tsv by MPC_STORE_URL (or --url). (exit status, output)"""
    if not t.vm.exists(STORE): t.vm.fetch("mpc-store.sh", STORE)
    cmd = "%ssh %s -y%s%s %s" % ("" if by_option else "MPC_STORE_URL=%s " % CATALOG_TSV, STORE, " -t %s" % shlex.quote(target) if target else "",
                                 " --url %s" % CATALOG_TSV if by_option else "", args)
    return guest(t, cmd, name, timeout)


def store_route(t, target):
    p = P(t)
    if target is None: sdcard_standin(t)
    how = "mpc-store.sh%s" % (" -t" if target else " (no -t)")
    live = ("install", "update") if two_homes(t) else ("update",) if target else ("install",)     # real restart and load; the rest record theirs
    if "install" in live: real_mpc(t)
    else: mpc_stopped(t)
    pid = mpc_pid(t)
    rc, out = store(t, "install %s@1.0.0" % p.id, target)
    if not t.check(rc == 0 and "Installed 1 of 1." in out, "%s install %s@1.0.0: exit %d: %s" % (how, p.id, rc, tail(out))): return
    d = check_installed(t, p, "1.0.0", how + " install", pid, restarted="install" in live)
    if "install" in live: check_loads(t, p, d, how + " install")
    else: asked_restart(t, how + " install")
    rc, out = store(t, "list", target, by_option=True)
    row = [l for l in out.splitlines() if l.startswith(p.id + " ")]
    t.check(row and "1.0.0" in row[0] and "update available" in row[0], "%s list: %s installed at 1.0.0 with its update: %s" % (how, p.id, row))
    if "update" in live: real_mpc(t)
    else: mpc_stopped(t)
    pid = mpc_pid(t)
    rc, out = store(t, "update", target)
    t.has(out, "%s: 1.0.0 -> 1.0.1" % p.id, how + " update")
    if t.check(rc == 0 and "Installed 1 of 1." in out, "%s update: exit %d: %s" % (how, rc, tail(out))):
        d = check_installed(t, p, "1.0.1", how + " update", pid, restarted="update" in live)
        if "update" in live: check_loads(t, p, d, how + " update")
        else: asked_restart(t, how + " update")
    mpc_stopped(t)
    rc, out = store(t, "remove %s" % p.id, target)
    t.check(rc == 0 and "removed %s" % p.id in out, "%s remove: exit %d: %s" % (how, rc, tail(out)))
    asked_restart(t, how + " remove")
    check_removed(t, p, how + " remove")


@route
def store_install_update_remove(t):
    """mpc-store.sh with no -t (its default folder): install a plugin at its older version, update it, remove it. The plugin must end up
    where MPC loads it from, and MPC must load it."""
    store_route(t, None)


@route
def store_install_update_remove_t(t):
    """The same with -t naming the folder MPC loads plugins from on this machine."""
    store_route(t, t.synths)


@route
def store_list(t):
    """mpc-store.sh list, with and without -t: the catalog's newest versions, what is installed, a folder put there by hand as "manual",
    and an entry limited to the MPC Sample marked as such everywhere else."""
    p = P(t)
    mpc_stopped(t)
    rc, out = store(t, "list", by_option=True, name="list")
    t.check(rc == 0 and out.count("\n") > 3, "mpc-store.sh list: exit %d: %s" % (rc, tail(out)))
    rows = {l.split()[0]: l for l in out.splitlines() if l and l.split()[0] in ("emu-probe", "emu-fx", "emu-addin", "emu-probe-out")}
    t.eq(sorted(rows), ["emu-addin", "emu-fx", "emu-probe", "emu-probe-out"], "mpc-store.sh list: the fixture's entries")
    t.check("1.0.1" in rows.get("emu-probe", ""), "list: emu-probe's newest is 1.0.1: %r" % rows.get("emu-probe"))
    t.check("(addin)" in rows.get("emu-addin", ""), "list: emu-addin is an addin: %r" % rows.get("emu-addin"))
    marked = "[only for: mpc-sample]" in rows.get("emu-fx", "")
    t.eq(marked, not t.sample, "list: emu-fx (only for the MPC Sample) is marked as such %s" % ("nowhere on a Sample" if t.sample else "on this MPC"))
    d = ss.unpack(t, p.zip("1.0.0"), t.arch)
    t.vm.sh("mkdir -p %s && cp -a %s %s/" % (shlex.quote(t.synths), shlex.quote("%s/portable/%s" % (d, p.folder)), shlex.quote(t.synths)))
    rc, out = store(t, "list", t.synths, name="list")
    row = [l for l in out.splitlines() if l.startswith(p.id + " ")]
    t.check(row and " manual " in row[0], "list -t %s: a folder copied there by hand shows as manual: %s" % (t.synths, row))
    t.eq(ctl_calls(t), [], "mpc-store.sh list asks nothing of MPC's service")


@route
def store_dependency(t):
    """mpc-store.sh -t: a plugin that needs another brings it at the catalog's pick; removing what it needs is refused with nothing
    changed; removing both works."""
    mpc_stopped(t)
    rc, out = store(t, "install emu-probe-out", t.synths)
    t.check(rc == 0 and "Installed 2 of 2." in out, "install emu-probe-out: exit %d: %s" % (rc, tail(out)))
    asked_restart(t, "install emu-probe-out")
    t.has(out, "emu-probe 1.0.1: needed by Emu Probe Out", "install emu-probe-out says why emu-probe comes too")
    for f, ver in (("acme - VST - Emu Probe Out", "1.0.0"), ("acme - VST - Emu Probe", "1.0.1")):
        t.has(t.vm.read("%s/%s/mpc-plugin.json" % (t.synths, f)), '"version": "%s"' % ver, "%s in %s" % (f, t.synths))
    state = t.vm.read(t.synths + "/.mpc-store")
    t.check("emu-probe\t1.0.1" in state and "emu-probe-out\t1.0.0" in state, "the store's record of both: %r" % state)
    rc, out = store(t, "remove emu-probe", t.synths)
    t.check(rc != 0 and "needs emu-probe" in out, "removing what emu-probe-out needs is refused: exit %d: %s" % (rc, tail(out)))
    t.check(t.vm.exists("%s/acme - VST - Emu Probe/emu_probe.so" % t.synths), "the refused removal changed nothing")
    t.eq(ctl_calls(t), [], "the refused removal did not restart MPC")
    rc, out = store(t, "remove emu-probe-out emu-probe", t.synths)
    t.check(rc == 0 and "removed emu-probe-out" in out and "removed emu-probe" in out, "remove both: exit %d: %s" % (rc, tail(out)))
    asked_restart(t, "remove both")
    t.eq(found(t, "acme - VST - Emu Probe") + found(t, "acme - VST - Emu Probe Out"), [], "folders left after removing both")
    t.settings_ok(os.path.basename(t.settings))


@route
def store_addin_install_remove(t):
    """mpc-store.sh: an addin installed (MPC preloads it), reported up to date, and removed."""
    before = preload(t)
    t.vm.sh("rm -f /tmp/emu-addin.loaded")
    rc, out = store(t, "install emu-addin")
    if not t.check(rc == 0 and "Installed 1 of 1." in out, "install emu-addin: exit %d: %s" % (rc, tail(out))): return
    addin_installed(t, "mpc-store.sh install emu-addin", before)
    rc, out = store(t, "update emu-addin")
    t.has(out, "emu-addin: up to date (1.0.0)", "mpc-store.sh update emu-addin")
    pid = mpc_pid(t)
    t.vm.sh("rm -f /tmp/emu-addin.loaded")
    rc, out = store(t, "remove emu-addin")
    t.check(rc == 0 and "removed emu-addin" in out, "remove emu-addin: exit %d: %s" % (rc, tail(out)))
    wait_mpc(t, other_than=pid)
    addin_removed(t, "mpc-store.sh remove emu-addin", before)


@route
def store_sync_prune(t):
    """mpc-store.sh sync registers a plugin folder copied by hand and drops the entry of one deleted by hand; prune keeps the newest
    settings backups."""
    p = P(t)
    folder = "%s/%s" % (t.synths, p.folder)
    mpc_stopped(t)
    d = ss.unpack(t, p.zip("1.0.0"), t.arch)
    t.vm.sh("mkdir -p %s && cp -a %s %s/" % (shlex.quote(t.synths), shlex.quote("%s/portable/%s" % (d, p.folder)), shlex.quote(t.synths)))
    rc, out = store(t, "sync", t.synths, name="sync")
    t.check(rc == 0, "sync: exit %d: %s" % (rc, tail(out)))
    f = 'file="%s/%s"' % (folder, p.so)
    t.eq(t.settings_when(lambda x: f in x).count(f), 1, "sync registered the folder copied by hand (%s)" % f)
    t.settings_ok(os.path.basename(t.settings))
    asked_restart(t, "sync of a folder copied by hand")
    t.vm.sh("rm -rf %s" % shlex.quote(folder))
    rc, out = store(t, "sync", t.synths, name="sync")
    t.check(rc == 0, "sync after a removal by hand: exit %d: %s" % (rc, tail(out)))
    t.hasnt(t.settings_when(lambda x: p.so not in x), p.so, "sync dropped the entry of the folder deleted by hand")
    t.settings_ok(os.path.basename(t.settings))
    asked_restart(t, "sync after a removal by hand")
    s = shlex.quote(t.settings)
    t.vm.sh("for i in 1 2 3 4; do cp %s %s.bak-emu-2026010$i; touch -d 2026-01-0$i %s.bak-emu-2026010$i; done" % (s, s, s))
    n = int(t.vm.out("ls %s.bak-* | wc -l" % s).strip())
    rc, out = store(t, "prune --keep 2", name="prune")
    t.check(rc == 0 and "Deleted %d." % (n - 2) in out, "prune --keep 2 of %d backups: exit %d: %s" % (n, rc, tail(out)))
    left = t.vm.out("ls -t %s.bak-* 2>/dev/null || true" % s).split("\n")
    left = [x for x in left if x]
    t.eq(len(left), 2, "backups left after prune --keep 2")
    t.check(not any(x.endswith(("-20260101", "-20260102", "-20260103", "-20260104")) for x in left), "prune kept the newest backups: %s" % left)
    t.settings_ok(os.path.basename(t.settings))
    t.eq(ctl_calls(t), [], "prune asks nothing of MPC's service")


@route
def store_device_limited(t):
    """Catalog entries limited by device: emu-fx is only for the MPC Sample ("devices": ["mpc-sample"]), emu-probe-out for every MPC.
    Elsewhere mpc-store.sh skips emu-fx with nothing changed; on a Sample it installs, and so does the entry for every MPC."""
    mpc_stopped(t)
    target = sample.SYNTHS if t.sample else None
    rc, out = store(t, "install emu-fx", target)
    if t.sample:
        t.check(rc == 0 and "Installed 1 of 1." in out, "install emu-fx on a Sample: exit %d: %s" % (rc, tail(out)))
        t.check(t.vm.exists("%s/acme - VST - Emu FX/emu_fx.so" % sample.SYNTHS), "emu-fx is on the card")
        rc, out = store(t, "install emu-probe-out", target)
        t.check(rc == 0 and "Installed 2 of 2." in out, "install emu-probe-out (for every MPC) on a Sample: exit %d: %s" % (rc, tail(out)))
        t.check(t.vm.exists("%s/acme - VST - Emu Probe Out/emu_probe_out.so" % sample.SYNTHS), "emu-probe-out is on the card")
    else:
        t.has(out, "skipping emu-fx 1.0.1: only for mpc-sample, not this device", "install emu-fx here")
        t.check(rc == 0 and "Nothing to install" in out, "install emu-fx here installs nothing: exit %d: %s" % (rc, tail(out)))
        t.eq(found(t, "acme - VST - Emu FX"), [], "emu-fx folders")
        t.eq(ctl_calls(t), [], "MPC was not restarted")
        rc, out = store(t, "install emu-probe-out", t.synths)
        t.check(rc == 0 and "Installed 2 of 2." in out, "install emu-probe-out (for every MPC): exit %d: %s" % (rc, tail(out)))


# ---- route 3: the desktop installer app (tools/desktop), from the computer, over SSH ------------------------------------------------

DESKTOP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "build", "fx", "%s", "mpc-installer")   # beside the fixture: CI hands that folder to the shards
APP_PORT, TLS_PORT = 8795, 8743


def wait_port(port, secs=10):
    end = time.time() + secs
    while time.time() < end:
        try: socket.create_connection(("127.0.0.1", port), 1).close(); return True
        except OSError: time.sleep(0.2)
    return False


class Desktop:
    """The desktop app as a person's computer runs it, here in the harness container: its web API (the page's own calls, with the
    token it prints) instead of a browser. It needs https for the catalog and the zips (a TLS front for the fixture server, with a
    certificate made here and given to it by SSL_CERT_FILE) and SSH on port 22 (forwarded to the emulator's), and logs in with the
    default key in its HOME (the harness's guest key)."""
    def __init__(self, t, **versions):
        self.bin = DESKTOP % t.vm.stage
        if not os.path.exists(self.bin): raise Fail("no desktop app: run emu/build.sh")
        os.chmod(self.bin, 0o755)            # a CI artifact loses its mode
        self.t, self.app, self.n = t, None, 0
        self.d = tempfile.mkdtemp(prefix="pm-emu-desktop-")
        os.makedirs(self.d + "/home/.ssh")
        key = self.d + "/home/.ssh/id_ed25519"
        open(key, "wb").write(open(t.vm.key, "rb").read()); os.chmod(key, 0o600)
        subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes", "-keyout", self.d + "/key.pem",
                        "-out", self.d + "/cert.pem", "-days", "7", "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1"],
                       check=True, capture_output=True)
        open(self.d + "/both.pem", "w").write(open(self.d + "/key.pem").read() + open(self.d + "/cert.pem").read())
        self.procs = [subprocess.Popen(["socat", "OPENSSL-LISTEN:%d,bind=127.0.0.1,reuseaddr,fork,verify=0,cert=%s" % (TLS_PORT, self.d + "/both.pem"),
                                        "TCP:127.0.0.1:%d" % FIXTURE_PORT], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL),
                      subprocess.Popen(["socat", "TCP-LISTEN:22,bind=127.0.0.1,reuseaddr,fork", "TCP:127.0.0.1:%d" % t.vm.ssh_port],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)]
        if not (wait_port(TLS_PORT) and wait_port(22)): raise Fail("the desktop app's https and ssh forwards did not start")
        self.start(**versions)

    def catalog(self, **versions):
        """desktop.json: the store catalog (catalog.store.json) with https links, each entry with all its versions unless named."""
        c = json.load(open(os.path.join(self.t.fx.src, "catalog.store.json")))
        want = {k.replace("_", "-"): v for k, v in versions.items()}
        for e in c["plugins"]:
            if e["id"] in want:
                e["versions"] = [v for v in e["versions"] if v["version"] in want[e["id"]]]; e["latest"] = e["versions"][0]["version"]
        self.t.fx.put("desktop.json", json.dumps(c).replace("http://%s/" % FIXTURE_ADDR, "https://127.0.0.1:%d/" % TLS_PORT))

    def start(self, **versions):
        self.catalog(**versions)
        self.logf = self.d + "/app-%d.log" % len(os.listdir(self.d))
        self.app = subprocess.Popen([self.bin, "--no-browser", "--no-update-check", "--port", str(APP_PORT), "--catalog",
                                     "https://127.0.0.1:%d/desktop.json" % TLS_PORT], stdout=open(self.logf, "w"), stderr=subprocess.STDOUT,
                                    env=dict(os.environ, HOME=self.d + "/home", SSL_CERT_FILE=self.d + "/cert.pem"))
        end = time.time() + 20; self.token = ""
        while time.time() < end and not self.token:
            for l in open(self.logf).read().split():
                if "?t=" in l: self.token = l.split("?t=")[1]
            time.sleep(0.2)
        if not self.token: raise Fail("the desktop app did not start: %s" % open(self.logf).read()[-400:])
        code, r = self.api("connect", {"Host": "127.0.0.1"})
        self.keep("connect", r)
        if code != 200: raise Fail("the desktop app could not connect to the device: %s" % r)
        self.device = r["device"]
        code, c = self.api("catalog")           # the page asks for it once connected; the app reads the catalog then
        if code != 200: raise Fail("the desktop app could not read the catalog: %s" % c)
        return r

    def restart(self, **versions):
        self.app.terminate(); self.app.wait(10)
        return self.start(**versions)

    def api(self, path, body=None, secs=120):
        req = urllib.request.Request("http://127.0.0.1:%d/api/%s" % (APP_PORT, path), headers={"X-MPC-Token": self.token},
                                     data=None if body is None else json.dumps(body).encode(), method="GET" if body is None else "POST")
        try:
            with urllib.request.urlopen(req, timeout=secs) as r: return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            txt = e.read().decode("utf-8", "replace")
            try: return e.code, json.loads(txt)
            except ValueError: return e.code, {"error": txt}

    def keep(self, name, data):
        self.n += 1
        open(os.path.join(self.t.out, "desktop-%02d-%s.json" % (self.n, name)), "w").write(json.dumps(data, indent=1))

    def job(self, path, body, secs=900):
        """Start an install or a removal and wait for it: (ok, its error or result, its log lines)."""
        code, r = self.api(path, body)
        if code != 200:
            self.keep(path + "-refused", r); return False, r.get("error", str(r)), []
        end = time.time() + slow(secs)
        while True:
            code, r = self.api("job")
            if r.get("state") != "running" or time.time() > end: break
            time.sleep(1)
        self.keep(path, r)
        return r.get("state") == "done", r.get("result", ""), r.get("lines", [])

    def item(self, folder):
        """The /api/remove item for a folder the app lists on the device."""
        code, r = self.api("device")
        for kp in r.get("plugins", []):
            if kp.get("folder") == folder: return {"root": kp["root"], "folder": kp["folder"]}
        return None

    def stop(self):
        for p in ([self.app] if self.app else []) + self.procs:          # its own processes, by their handles
            try: p.terminate(); p.wait(10)
            except Exception: pass
        import shutil
        shutil.rmtree(self.d, ignore_errors=True)


@route
def desktop_install_update_remove(t):
    """The desktop installer app, driven over its local web API from the computer side, against the emulator's SSH: install a plugin
    from the catalog to the place it picks, update it over the older version, remove it. The plugin must end up where MPC loads it
    from, and MPC must load it. On a Sample it must refuse the noexec card and say what works, then install to the card once its Synths is bound exec."""
    p = P(t)
    sdcard_standin(t)
    dk = Desktop(t, **{p.id: ["1.0.0"]})
    try:
        for ver in ("1.0.0", "1.0.1"):
            if ver == "1.0.1": dk.restart()
            what = "desktop app %s %s" % ("install" if ver == "1.0.0" else "update", ver)
            if t.sample and ver == "1.0.0":  # the Sample's card is noexec: refused with what to do, then bound exec (sample_scenarios.py)
                ok, res, lines = dk.job("install", {"catalog": [p.id], "confirm": True})
                t.check(not ok and "noexec" in res and "mount --bind" in res, "%s on the noexec card is refused and says what works: %s" % (what, res))
                ss.bind_exec(t)
                dk.restart(**{p.id: ["1.0.0"]})
            pid = mpc_pid(t)
            ok, res, lines = dk.job("install", {"catalog": [p.id], "confirm": True})
            if not t.check(ok, "%s: %s %s" % (what, res, " | ".join(lines[-4:]))): return
            d = check_installed(t, p, ver, what, pid)
            check_loads(t, p, d, what)
        dk.restart()
        it = dk.item(p.folder)
        if not t.check(it, "the desktop app lists %s on the device" % p.folder): return
        pid = mpc_pid(t)
        ok, res, lines = dk.job("remove", {"items": [it], "confirm": True})
        t.check(ok, "desktop app remove: %s %s" % (res, " | ".join(lines[-4:])))
        wait_mpc(t, other_than=pid)
        check_removed(t, p, "desktop app remove")
    finally:
        dk.stop()


@route
def desktop_addin_install_remove(t):
    """The desktop app: an addin installed (MPC preloads it after the restart) and removed."""
    before = preload(t)
    dk = Desktop(t)
    try:
        t.vm.sh("rm -f /tmp/emu-addin.loaded")
        ok, res, lines = dk.job("install", {"catalog": ["emu-addin"], "confirm": True})
        if not t.check(ok, "desktop app addin install: %s %s" % (res, " | ".join(lines[-4:]))): return
        addin_installed(t, "desktop app addin install", before)
        dk.restart()
        it = dk.item("emu-addin")
        if not t.check(it, "the desktop app lists the addin on the device"): return
        pid = mpc_pid(t)
        t.vm.sh("rm -f /tmp/emu-addin.loaded")
        ok, res, lines = dk.job("remove", {"items": [it], "confirm": True})
        t.check(ok, "desktop app addin remove: %s %s" % (res, " | ".join(lines[-4:])))
        wait_mpc(t, other_than=pid)
        addin_removed(t, "desktop app addin remove", before)
    finally:
        dk.stop()
