#!/usr/bin/env python3
"""Run the emulator scenarios on one instance (inside the pm-emu-host container; emu/run.sh is the one command on the host).

  run.py INSTANCE [--label BUILD] [-k NAME]... [--prepare] [--list]
  run.py INSTANCE --ready | --worker ROOT K --only NAME | --merge ROOT       the steps of a run across several emulators (jobs.py)

Every scenario starts from the same snapshot ("ready": prepare.py made it once per instance and build) on a fresh QEMU, so none sees
what another did. Results go to emu/results/<instance>/<build>/<scenario>/ (what was on screen, the device's logs) and a line
per scenario to results/<instance>/<build>/summary.json."""
import argparse, glob, hashlib, json, os, shutil, subprocess, sys, time, traceback
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import emu, machine, prepare
from emu import Fail, log

HERE = os.path.dirname(os.path.abspath(__file__))
PM = "/tmp/openmpc-pm"


class T:
    """What a scenario works with: the machine, the fixture, and the steps a person at the device would take."""
    def __init__(self, vm, fx, info, out):
        self.vm, self.fx, self.out = vm, fx, out
        self.arch, self.synths = info["arch"], info["synths"]      # synths: where plugins are installed (VST2 Plugins on MPC OS 3.10.1+)
        self.external = bool(info.get("external_plugins"))         # this firmware loads plugins from "<drive>/VST2 Plugins"
        self.sample = info.get("kind") == "sample"                  # an MPC Sample (sample.py)
        self.gen = 2 if self.arch == "aarch64" else 1
        self.settings = prepare.SETTINGS
        self.problems = []
        self.findings = []          # what the device does that a user should know of, recorded as found and not failed
        self.nshot = 0

    # -- checks: a failed one is recorded and the scenario goes on, so one run shows everything that is wrong ------------------------
    def check(self, ok, what):
        if not ok:
            self.problems.append(what); log("    FAIL: " + what)
        return bool(ok)

    def finding(self, what):
        self.findings.append(what); log("    finding: " + what)

    def eq(self, got, want, what):
        return self.check(got == want, "%s: got %r, want %r" % (what, got, want))

    def has(self, hay, needle, what):
        return self.check(needle in hay, "%s: %r not in %r" % (what, needle, hay[-600:] if len(hay) > 600 else hay))

    def hasnt(self, hay, needle, what):
        return self.check(needle not in hay, "%s: %r is in it" % (what, needle))

    def shot(self, name):
        self.nshot += 1
        p = os.path.join(self.out, "%02d-%s.png" % (self.nshot, name))
        self.vm.shot(p)
        return p

    def screen_shows(self, *texts, name="screen"):
        """The screen shows each text (OCR of a capture that is kept)."""
        import ocr
        self.vm.settle()
        ws = ocr.words(self.shot(name))
        for t in texts:
            self.check(ocr.find(None, t, ws), "the screen shows %r (%s)" % (t, name))

    # -- the Manager -------------------------------------------------------------------------------------------------------------
    def state(self, **kw):
        return self.vm.state(**kw)

    def plugin(self, pid, st=None):
        st = st or self.state()
        for p in st.get("plugins", []):
            if p["id"] == pid: return p
        return {}

    def refresh(self):
        """Fetch the catalog again and wait for it."""
        self.vm.api("refresh", post=True)
        time.sleep(1)
        return self.state(secs=60, until=lambda s: s.get("loaded") == 1 and not s.get("busy"), why="catalog reloaded")

    def tap_state(self, pid, x, y, want, hold=0.08):
        """Tap at (x, y) on a still screen, then the plugin's state once the guest has dealt with the tap: the screen has settled
        and the state has held for two reads in a row (a press counted twice shows as one state and then the other)."""
        self.vm.settle()
        self.vm.tap(x, y, hold=hold, after=0.3)
        self.vm.settle()
        end = time.time() + emu.slow(10); last = None
        while True:
            got = self.plugin(pid).get("state")
            if got == last and (got == want or time.time() > end) or time.time() > end + emu.slow(10): return got
            last = got; time.sleep(1)

    def queue(self, pid, do="install"):
        self.vm.api("queue?id=%s&do=%s" % (pid, do), post=True)

    def prepare(self, secs=240):
        """The first APPLY: download and unpack. Returns the state once it is ready (ready=1) or has failed (failed=1). MPC does not
        restart during this step: if its process goes, the scenario fails there and says so, instead of polling a dead port."""
        pid = self.vm.out("pidof MPC || true").split()[:1]
        def gone():
            now = self.vm.out("pidof MPC || true").split()
            if pid and pid[0] not in now:
                self.save_journal("mpc-went-down")
                raise Fail("MPC went down during the first APPLY (pid %s, now %s): see journal-mpc-went-down.txt; manager.log ends %r"
                           % (pid[0], now or "none", self.vm.read(PM + "/manager.log")[-200:]))
        self.vm.api("apply", post=True)
        time.sleep(1)
        return self.state(secs=secs, until=lambda s: not s.get("busy") and (s.get("ready") or s.get("failed")), why="APPLY prepared", gone=gone)

    def launch(self):
        """The second APPLY (the restart prompt's GO): MPC stops, the changes are made, MPC starts again."""
        self.vm.sh("rm -f %s/result" % PM)
        self.log_before = self.vm.read(PM + "/apply.log")   # apply.log is appended to: wait_done looks only at what this apply adds
        pid = self.vm.out("pidof MPC || true").strip()
        before = self.vm.read(PM + "/manager.log").count("launching apply.sh")
        end = time.time() + emu.slow(20)
        while True:                         # 409: the worker is busy and the Manager is still ready, so the press can be repeated
            rc, out = self.vm.post("apply")
            if rc == 0 or "409" not in out or time.time() > end: break
            time.sleep(0.5)
        end = time.time() + emu.slow(30)
        while time.time() < end:            # the Manager logs this before it starts apply.sh, and the log outlives MPC
            if self.vm.read(PM + "/manager.log").count("launching apply.sh") > before: return
            time.sleep(0.5)
        now = self.vm.out("pidof MPC || true").strip()
        self.save_journal("no-launch")
        raise Fail("APPLY was never launched: POST /api/apply exit %d (%s); MPC pid %r before, %r now%s; manager.log: %r"
                   % (rc, out.strip()[-120:], pid, now, " (MPC restarted: it crashed or was killed)" if pid != now else "",
                      self.vm.read(PM + "/manager.log")[-300:]))

    def wait_done(self, secs=420):
        """Wait for the apply script to finish and MPC to be running again; returns (result lines, apply.log)."""
        end = time.time() + emu.slow(secs)
        while time.time() < end:
            lg = self.vm.read(PM + "/apply.log")
            old = getattr(self, "log_before", "")
            if "== done" in (lg[len(old):] if lg.startswith(old) else lg) and self.vm.sh("pidof MPC", check=False)[0] == 0: break
            time.sleep(1)
        else:
            self.check(False, "the apply never finished: apply.log ends %r" % self.vm.read(PM + "/apply.log")[-400:])
        self.keep("apply.log", "result")
        return self.results(), self.vm.read(PM + "/apply.log")

    def apply(self, secs=420):
        """Both APPLYs. Returns (result lines as {id: (status, detail)}, apply.log); a failed prepare returns ({}, the status line)."""
        st = self.prepare()
        if not st.get("ready"):
            return {}, st.get("status", "")
        self.launch()
        return self.wait_done(secs)

    def results(self):
        out = {}
        for line in self.vm.read(PM + "/result").splitlines():
            f = line.split("|", 2)
            if len(f) == 3: out[f[0]] = (f[1], f[2])
        return out

    def reopen(self):
        """After MPC restarted: a new project with the Manager on a track again."""
        prepare.open_manager(self.vm, keep=self.out)
        return self.state(secs=90, until=lambda s: s.get("loaded") == 1 and not s.get("busy"), why="the Manager loaded its catalog")

    def keep(self, *names):
        """Copy the device's log files into the results."""
        for n in names:
            txt = self.vm.read("%s/%s" % (PM, n))
            if txt:
                k = len([f for f in os.listdir(self.out) if f.endswith(n)])
                open(os.path.join(self.out, "%d-%s" % (k + 1, n)), "w").write(txt)

    def save_journal(self, name):
        """What the guest logged about MPC and the apply, and any crash report, for a failure that needs explaining."""
        cmd = ("journalctl --no-pager -b -o short-monotonic -n 600 -u acvs -u ac50 -u 'openmpcpm-apply-*' 2>&1; echo '--- kernel'; "
               "journalctl --no-pager -b -k -o short-monotonic -n 60 2>&1; echo '--- crash reports'; "
               "find /tmp /data /var/volatile /root /media/az01-internal -xdev -maxdepth 6 \\( -name '*.dmp' -o -iname '*crashpad*' \\) 2>/dev/null | head -n 40")
        try: txt = self.vm.sh(cmd, check=False, timeout=int(emu.slow(40)))[1]
        except Exception as e: txt = "could not read the journal: %s" % e
        open(os.path.join(self.out, "journal-%s.txt" % name), "w").write(txt)

    def settings_text(self):
        return self.vm.read(self.settings)

    def settings_when(self, ok, secs=90):
        """MPC.settings once ok(text) holds, or as it is after `secs`: MPC rewrites it as it starts, later on a slow host."""
        end = time.time() + emu.slow(secs)
        while True:
            txt = self.settings_text()
            if ok(txt) or time.time() > end: return txt
            time.sleep(1)

    def settings_ok(self, what="MPC.settings"):
        """MPC.settings is whole: well-formed XML with its PROPERTIES root."""
        rc, out = self.vm.sh("tail -c 14 %s; echo; wc -c < %s" % (self.settings, self.settings), check=False)
        txt = self.settings_text()
        ok = txt.lstrip().startswith("<?xml") and txt.rstrip().endswith("</PROPERTIES>")
        if ok:
            import xml.etree.ElementTree as ET
            try: ET.fromstring(txt.encode())
            except ET.ParseError as e: ok = False; out = str(e)
        return self.check(ok, "%s is whole (%s)" % (what, out.strip()[-80:]))


SCENARIOS = []
SAMPLE_SCENARIOS = []          # the MPC Sample's (sample_scenarios.py): it has no plugin browser, so none of the Manager's apply


def scenario(f):
    SCENARIOS.append(f)
    return f


def sample_scenario(f):
    SAMPLE_SCENARIOS.append(f)
    return f


def route(f):
    """A scenario for every machine, the MPC Sample's too (routes.py: the ways to install without the Manager)."""
    SCENARIOS.append(f); SAMPLE_SCENARIOS.append(f)
    return f


STALL_MARKS = ("killing thread by force", "exception not rethrown")      # MPC's watchdog killing its audio thread, then the abort


def audio_stall(out):
    """The guest's journal in this scenario's results shows MPC killing its audio thread."""
    for path in glob.glob(os.path.join(out, "journal-*.txt")):
        txt = open(path, errors="replace").read()
        if all(m in txt for m in STALL_MARKS): return True
    return False


def run_one(instance, label, sc, info, fx, snapshot="ready", tclass=T, logs=(PM + "/device.txt", PM + "/manager.log", PM + "/apply.log", PM + "/result")):
    """One scenario. On Gen1, whose emulation can starve MPC's audio thread, a failure with that crash gets one more try on a fresh
    guest. The first try's files are kept in first-attempt/, and a pass after a retry is reported as one."""
    r = run_attempt(instance, label, sc, info, fx, snapshot, tclass, logs)
    out = os.path.join(HERE, "results", instance, label, sc.__name__)
    if r["ok"] or info["arch"] == "aarch64" or not audio_stall(out): return r
    log("%s %s: MPC killed its audio thread, trying once more" % (instance, sc.__name__))
    first = out + ".first-attempt"
    shutil.rmtree(first, ignore_errors=True); os.rename(out, first)
    second = run_attempt(instance, label, sc, info, fx, snapshot, tclass, logs)
    os.rename(first, os.path.join(out, "first-attempt"))
    second["retried"] = r["problems"]
    if second["ok"]:
        second.setdefault("findings", []).insert(0, "emulator audio stall on the first attempt (MPC killed its audio thread); passed on retry")
    else:
        second["problems"] = second["problems"] + ["failed on retry; the first attempt ended with MPC killing its audio thread"]
    return second


def run_attempt(instance, label, sc, info, fx, snapshot, tclass, logs):
    out = os.path.join(HERE, "results", instance, label, sc.__name__)
    shutil.rmtree(out, ignore_errors=True); os.makedirs(out)
    vm = emu.VM(instance, label)
    t0 = time.time(); t = None
    try:
        fx.catalog()
        vm.start(snapshot)
        t = tclass(vm, fx, info, out)
        sc(t)
        problems = t.problems
    except Fail as e:
        problems = (t.problems if t else []) + ["stopped: %s" % e]
    except Exception:
        problems = (t.problems if t else []) + ["harness error: %s" % traceback.format_exc()[-800:]]
    try:
        if vm.proc and vm.proc.poll() is None:
            if problems and t: t.save_journal("end")
            vm.shot(os.path.join(out, "99-end.png"))
            for path in logs:
                txt = vm.read(path)
                if txt: open(os.path.join(out, "end-" + os.path.basename(path)), "w").write(txt)
    except Exception:
        pass
    vm.kill()                               # no sync: the next scenario starts from the snapshot, and this guest's disk state is dropped
    secs = int(time.time() - t0)
    findings = t.findings if t else []
    log("%s %-28s %s (%ds)%s%s" % (instance, sc.__name__, "ok" if not problems else "FAILED", secs, "".join("\n      - " + p for p in problems),
                                  "".join("\n      * " + f for f in findings)))
    r = {"scenario": sc.__name__, "ok": not problems, "seconds": secs, "problems": problems, "doc": (sc.__doc__ or "").strip().split("\n")[0]}
    if findings: r["findings"] = findings
    return r


def build_id(label):
    """What a ready snapshot holds: the Manager's zips, the catalog it has loaded, and how it was installed and opened. By content, not
    by label: a build that comes back (or the same code under another label) finds its snapshot in the cache below."""
    h = hashlib.sha256()
    d = os.path.join(HERE, "build", "fx", label)
    for f in sorted(os.listdir(d)):
        if f.startswith("Plugin-Manager-G2-" + prepare.VERSION_A) or f == "catalog.full.json": h.update(open(os.path.join(d, f), "rb").read())
    h.update(open(os.path.join(HERE, "prepare.py"), "rb").read())
    return h.hexdigest()[:16]


READY_KEEP = 3                      # ready snapshots kept per machine (reflinks where the filesystem has them)


def ready_cache(instance, bid):
    return os.path.join(emu.STATE, instance, ".ready", bid)


def copy_stage(src, dst, reflink="auto"):
    for f in os.listdir(src):
        if f.endswith(".qcow2") or f == "ready.json":
            subprocess.run(["cp", "--reflink=" + reflink] + ([] if reflink == "always" else ["--sparse=always"]) + [os.path.join(src, f), os.path.join(dst, f)],
                           check=True, stderr=subprocess.DEVNULL)


def save_ready(instance, label, bid):
    """Keep this stage's ready snapshot under its content id, and only the newest few. Only as a reflink: where a copy would take
    gigabytes (a CI runner's disk), there is no cache."""
    dst = ready_cache(instance, bid); tmp = dst + ".tmp"
    shutil.rmtree(tmp, ignore_errors=True); os.makedirs(tmp)
    try: copy_stage(os.path.join(emu.STATE, instance, label), tmp, "always")
    except subprocess.CalledProcessError:
        shutil.rmtree(tmp, ignore_errors=True); log("%s: no reflinks here, the ready snapshot is not cached" % instance); return
    shutil.rmtree(dst, ignore_errors=True); os.rename(tmp, dst)
    top = os.path.dirname(dst)
    old = sorted((d for d in os.listdir(top) if not d.endswith(".tmp")), key=lambda d: -os.path.getmtime(os.path.join(top, d)))
    for d in old[READY_KEEP:]: shutil.rmtree(os.path.join(top, d), ignore_errors=True)


def load_ready(instance, label, bid):
    """The stage from the cache, if this content was prepared before; its info, or None."""
    src = ready_cache(instance, bid)
    try: info = json.load(open(os.path.join(src, "ready.json")))
    except (OSError, ValueError): return None
    if info.get("build") != bid: return None
    stage = os.path.join(emu.STATE, instance, label)
    shutil.rmtree(stage, ignore_errors=True); os.makedirs(stage)
    copy_stage(src, stage)
    os.utime(src)
    return info


def write_summary(instance, label, info, res):
    """Add these results to results/<instance>/<build>/summary.json, in the order of the scenarios."""
    outd = os.path.join(HERE, "results", instance, label)
    os.makedirs(outd, exist_ok=True)
    prev = {}
    try: prev = {r["scenario"]: r for r in json.load(open(os.path.join(outd, "summary.json")))["scenarios"]}
    except (OSError, ValueError, KeyError): pass
    for r in res: prev[r["scenario"]] = r
    json.dump({"instance": instance, "build": label, "prepare": info, "scenarios": [prev[s.__name__] for s in SCENARIOS if s.__name__ in prev]},
              open(os.path.join(outd, "summary.json"), "w"), indent=1)
    bad = [r for r in res if not r["ok"]]
    log("%s: %d scenarios, %d failed, %ds" % (instance, len(res), len(bad), sum(r["seconds"] for r in res)))
    return 1 if bad else 0


def worker(instance, label, root, k, kind, name):
    """One scenario on one emulator of a run across several (jobs.py): its own copy of the stage, chosen and made by the scheduler."""
    emu.STATE = os.path.join(root, "w%s" % k)
    info = json.load(open(os.path.join(emu.STATE, instance, label, "ready.json")))
    sc = {s.__name__: s for s in SCENARIOS}[name]
    fx = emu.Fixture(label)
    try:
        if kind == "sample": r = run_one(instance, label, sc, info, fx, "machine", sys.modules["sample_scenarios"].ST, sys.modules["sample_scenarios"].LOGS)
        else: r = run_one(instance, label, sc, info, fx)
        with open(os.path.join(root, "result-%s.jsonl" % k), "a") as f: f.write(json.dumps(r) + "\n")
        return 0 if r["ok"] else 1
    finally:
        fx.stop(); emu.shutdown()


def merge(instance, label, root):
    """The workers' results into the one summary.json. A scenario no worker finished (its container died) is a failure, not a gap."""
    res = []
    for f in sorted(glob.glob(os.path.join(root, "result-*.jsonl"))):
        res += [json.loads(l) for l in open(f) if l.strip()]
    got = {r["scenario"] for r in res}
    for name in open(os.path.join(root, "todo")).read().split():
        if name not in got:
            res.append({"scenario": name, "ok": False, "seconds": 0, "problems": ["no worker finished this scenario (see the run's .j<N>.log files)"],
                        "doc": next(((s.__doc__ or "").strip().split("\n")[0] for s in SCENARIOS if s.__name__ == name), "")})
    info = json.load(open(os.path.join(emu.STATE, instance, label, "ready.json")))
    return write_summary(instance, label, info, res)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("instance")
    ap.add_argument("--label", default="work", help="which build of the Manager (emu/build.sh --label); default the working tree's")
    ap.add_argument("-k", action="append", default=[], help="run only scenarios whose name contains this (may repeat)")
    ap.add_argument("--prepare", action="store_true", help="make the ready snapshot again even if it is current")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--machine", action="store_true", help="make the machine snapshot (no Manager) if it is not current, and stop")
    ap.add_argument("--ready", action="store_true", help="make the ready snapshot if it is not current, and stop")
    ap.add_argument("--worker", nargs=2, metavar=("ROOT", "K"), help="run the scenario --only on emulator K of the run in ROOT (jobs.py)")
    ap.add_argument("--only", metavar="NAME", help="with --worker: the scenario")
    ap.add_argument("--merge", metavar="ROOT", help="write summary.json from the workers' results in ROOT (jobs.py)")
    a = ap.parse_args()
    import run, scenarios, sample, sample_scenarios, routes   # noqa: they register with the module named run, which is not this one when run as a script
    kind = "sample" if sample.is_sample(a.instance) else "manager"
    SCENARIOS[:] = run.SAMPLE_SCENARIOS if kind == "sample" else run.SCENARIOS
    todo = [s for s in SCENARIOS if not a.k or any(k in s.__name__ for k in a.k)]
    other = [s.__name__ for s in (run.SCENARIOS if kind == "sample" else run.SAMPLE_SCENARIOS) if a.k and any(k in s.__name__ for k in a.k)]
    if other: log("%s: not run here (%s): %s" % (a.instance, "MPC Sample, no plugin browser" if kind == "sample" else "the MPC Sample's", " ".join(other)))
    if a.list:
        for s in todo: print("%-28s %s" % (s.__name__, (s.__doc__ or "").strip().split("\n")[0]))
        return 0
    if a.merge: return merge(a.instance, a.label, a.merge)
    if a.worker: return worker(a.instance, a.label, a.worker[0], a.worker[1], kind, a.only)
    if a.machine:                           # the machine snapshot alone: no Manager build, no fixture
        try: log("%s: machine %s" % (a.instance, machine.ensure(a.instance, force=a.prepare)["id"])); return 0
        finally: emu.shutdown()
    fx = emu.Fixture(a.label)
    rc = 1
    try:
        stage = os.path.join(emu.STATE, a.instance, a.label)
        bid = build_id(a.label)
        info = {}
        try: info = json.load(open(os.path.join(stage, "ready.json")))
        except (OSError, ValueError): pass
        minfo = machine.ensure(a.instance, force=a.prepare)        # the machine snapshot, shared by every build
        bid = "%s-%s" % (bid, minfo["id"])
        if kind == "sample":                # no Manager: the scenarios resume a copy of the machine snapshot
            if a.prepare or info.get("build") != bid:
                info = sample.prepare(a.instance, a.label, minfo); info["build"] = bid
                json.dump(info, open(os.path.join(stage, "ready.json"), "w"))
        elif a.prepare or info.get("build") != bid:
            cached = None if a.prepare else load_ready(a.instance, a.label, bid)
            if cached:
                info = cached
                log("%s: ready snapshot of this build reused (%s)" % (a.instance, bid))
            else:
                log("%s: preparing (build %s)" % (a.instance, a.label))
                info = prepare.prepare(a.instance, a.label, fx, minfo)
                info["build"] = bid
                with open(os.path.join(stage, "ready.json"), "w") as f: json.dump(info, f)
                save_ready(a.instance, a.label, bid)
        if a.ready: return 0
        if kind == "sample":
            res = [run_one(a.instance, a.label, s, info, fx, "machine", sample_scenarios.ST, sample_scenarios.LOGS) for s in todo]
        else:
            res = [run_one(a.instance, a.label, s, info, fx) for s in todo]
        rc = write_summary(a.instance, a.label, info, res)
    finally:
        fx.stop(); emu.shutdown()
    return rc


if __name__ == "__main__":
    sys.exit(main())
