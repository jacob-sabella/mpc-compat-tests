#!/usr/bin/env python3
"""jobs.py [--cap N] [--per-machine N] [--label BUILD] [-k NAME]... [--prepare] [--plan] INSTANCE...: one pool of emulators across all
the machines of a run (run.sh). Runs on the host.

Every machine's ready snapshot is made first, once (run.py --ready; a build seen before comes from the cache). Scenarios then start
longest first across all machines, from measured times, so slow Gen1 scenarios start early and nothing is left alone at the end. Each
runs in its own container (its own ports) on a reflink copy of the stage under state/.jobs/, and the results are merged into the usual
results/<instance>/<build>/summary.json.

--cap: emulators at once in all (default from this host's cores and memory, or PM_EMU_CAP). An emulator under load takes 2 to 3 cores
and up to 3 GB. --per-machine: at most this many on one machine (run.sh --jobs). --plan: print the order and the estimated time, and
boot nothing. Each emulator's log is results/<instance>-<build>.j<N>.log."""
import argparse, fcntl, glob, json, os, re, shutil, signal, subprocess, sys, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.environ.get("PM_EMU_STATE", os.path.join(HERE, "state"))
TIMINGS = os.environ.get("PM_EMU_TIMINGS", os.path.join(HERE, "..", "timings.json"))
DEFAULT_SECS = 120                  # a scenario nothing has timed yet
PREPARE_SECS = 340                  # making one machine's ready snapshot, measured
out_lock = threading.Lock()


def say(text):
    with out_lock:
        sys.stdout.write(text); sys.stdout.flush()


def default_cap():
    """Emulators this host can run at once: three cores each, 3.5 GB each with 4 GB left for everything else."""
    cores = len(os.sched_getaffinity(0))
    avail = 0
    for line in open("/proc/meminfo"):
        if line.startswith("MemAvailable:"): avail = int(line.split()[1]) / (1024 * 1024)
    return max(1, min(cores // 3, int((avail - 4) / 3.5)))


def copy(src, dst):
    """A reflink when the filesystem can, else a plain sparse copy (which takes the disk space of every stage)."""
    if subprocess.run(["cp", "--reflink=always", src, dst], stderr=subprocess.DEVNULL).returncode:
        say("%s: no reflink here: a full copy of %s\n" % (os.path.basename(os.path.dirname(os.path.dirname(dst))), os.path.basename(src)))
        subprocess.run(["cp", "--reflink=never", "--sparse=always", src, dst], check=True)


def pump(proc, logpath, instance):
    """A container's output to its log; a scenario's result, with the lines under it, here in one piece."""
    head = re.compile(r"^[0-9:]+ %s " % re.escape(instance))
    block = []
    with open(logpath, "a") as lg:
        for line in proc.stdout:
            lg.write(line); lg.flush()
            if block and line.startswith("      "): block.append(line); continue
            if block: say("".join(block)); block = []
            if head.match(line): block = [line]
    if block: say("".join(block))


def scenarios(instance, label, keys):
    """The scenarios of this machine, from run.py --list (nothing is booted)."""
    k = [x for key in keys for x in ("-k", key)]
    r = subprocess.run([sys.executable, os.path.join(HERE, "run.py"), instance, "--label", label, "--list"] + k, capture_output=True, text=True)
    if r.returncode: raise SystemExit(r.stdout + r.stderr)
    return [l.split()[0] for l in r.stdout.splitlines() if l.strip() and not re.match(r"^[0-9:]+ ", l)]


def measured(instance, label):
    """Seconds per scenario: this build's last results where they exist, else the CI timings."""
    secs = {}
    try: secs.update(json.load(open(TIMINGS))["instances"].get(instance, {}))
    except (OSError, ValueError, KeyError): pass
    try:
        for r in json.load(open(os.path.join(HERE, "results", instance, label, "summary.json")))["scenarios"]:
            if r.get("seconds", 0) > 0: secs[r["scenario"]] = r["seconds"]
    except (OSError, ValueError, KeyError): pass
    return secs


def order(todo, label):
    """Every (seconds, instance, scenario), longest first across the machines."""
    out = []
    for inst, names in todo.items():
        secs = measured(inst, label)
        out += [(secs.get(n, DEFAULT_SECS), inst, n) for n in names]
    return sorted(out, key=lambda x: (-x[0], x[1], x[2]))


def simulate(tasks, instances, cap, per, prepare_secs=PREPARE_SECS):
    """How long the pool takes with these times: the same rules as run(), in simulated time."""
    now, running, ready = 0, [], set()          # running: (end, instance or None, instance to mark ready)
    preps, pending = list(instances), list(tasks)
    while preps or pending or running:
        started = True
        while started:
            started = False
            if preps and len(running) < cap:
                i = preps.pop(0); running.append((now + prepare_secs, None, i)); started = True; continue
            for k, (secs, inst, _) in enumerate(pending):
                if len(running) >= cap: break
                if inst in ready and sum(1 for r in running if r[1] == inst) < per:
                    running.append((now + secs, inst, None)); pending.pop(k); started = True; break
        if not running: break
        now = min(r[0] for r in running)
        for r in [r for r in running if r[0] == now]:
            running.remove(r)
            if r[2]: ready.add(r[2])
    return now


class Pool:
    def __init__(self, a, todo, tasks):
        self.a, self.todo, self.pending = a, todo, list(tasks)
        self.label = a.label
        self.cond = threading.Condition()
        self.preps = list(todo)
        self.running = 0
        self.per = {i: 0 for i in todo}
        self.ready, self.failed = set(), set()
        self.free = {i: [] for i in todo}       # emulator folders not in use
        self.made = {i: 0 for i in todo}
        self.names = set()                      # containers running now
        self.incont = os.path.join(HERE, "in-container")

    def root(self, inst):
        return os.path.join(STATE, ".jobs", "%s-%s" % (inst, self.label))

    def container(self, name, cmd, logpath, inst, env=None):
        with self.cond: self.names.add(name)
        p = subprocess.Popen([self.incont, name] + cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
        pump(p, logpath, inst)
        rc = p.wait()
        with self.cond: self.names.discard(name)
        return rc

    def prepare(self, inst):
        cmd = ["python3", "run.py", inst, "--label", self.label, "--ready"] + (["--prepare"] if self.a.prepare else [])
        log = os.path.join(HERE, "results", "%s-%s.log" % (inst, self.label))
        rc = self.container("pm-emu-%s-%s" % (inst, self.label), cmd, log, inst)
        if rc == 0:
            root = self.root(inst)
            shutil.rmtree(root, ignore_errors=True); os.makedirs(root)
            open(os.path.join(root, "todo"), "w").write("\n".join(self.todo[inst]))
        else: say("%s: no ready snapshot (exit %d), its scenarios are not run: see %s\n" % (inst, rc, log))
        with self.cond:
            self.running -= 1; self.per[inst] -= 1
            (self.ready if rc == 0 else self.failed).add(inst)
            if rc: self.pending = [t for t in self.pending if t[1] != inst]
            self.cond.notify_all()

    def emulator(self, inst):
        """A folder with its own copy of the stage: one a finished scenario left (the next start goes back to the snapshot) or a new one."""
        with self.cond:
            if self.free[inst]: return self.free[inst].pop()
            self.made[inst] += 1; k = self.made[inst]
        stage = os.path.join(STATE, inst, self.label)
        w = os.path.join(self.root(inst), "w%d" % k); d = os.path.join(w, inst, self.label); os.makedirs(d)
        for f in glob.glob(os.path.join(stage, "*.qcow2")): copy(f, os.path.join(d, os.path.basename(f)))
        shutil.copy(os.path.join(stage, "ready.json"), d)
        for f in ("guest_key", "guest_key.pub"): shutil.copy2(os.path.join(STATE, f), w)      # the key the snapshot's guest trusts
        open(os.path.join(self.root(inst), "w%d.lock" % k), "w").close()
        return k

    def scenario(self, inst, name):
        k = None
        try:
            k = self.emulator(inst)
            env = dict(os.environ, QE_RUNLOCK=os.path.join(self.root(inst), "w%d.lock" % k), QE_INSTANCE=inst)
            log = os.path.join(HERE, "results", "%s-%s.j%d.log" % (inst, self.label, k))
            self.container("pm-emu-%s-%s-j%d" % (inst, self.label, k),
                           ["python3", "run.py", inst, "--label", self.label, "--worker", self.root(inst), str(k), "--only", name], log, inst, env)
        except Exception as e:                  # no result line: merge() reports the scenario as not finished
            say("%s %s: could not be run: %s\n" % (inst, name, e))
        with self.cond:
            if k is not None: self.free[inst].append(k)
            self.running -= 1; self.per[inst] -= 1
            self.cond.notify_all()

    def start(self, target, *args):
        threading.Thread(target=target, args=args, daemon=True).start()

    def run(self):
        with self.cond:
            while self.preps or self.pending or self.running:
                while self.preps and self.running < self.a.cap:
                    i = self.preps.pop(0); self.running += 1; self.per[i] += 1; self.start(self.prepare, i)
                for t in list(self.pending):
                    if self.running >= self.a.cap: break
                    if t[1] in self.ready and self.per[t[1]] < self.a.per_machine:
                        self.pending.remove(t); self.running += 1; self.per[t[1]] += 1; self.start(self.scenario, t[1], t[2])
                self.cond.wait(1)

    def stop(self):
        with self.cond: names = list(self.names)
        for c in names: subprocess.run(["docker", "rm", "-f", c], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("instances", nargs="+")
    ap.add_argument("--label", default="work")
    ap.add_argument("-k", action="append", default=[])
    ap.add_argument("--prepare", action="store_true")
    ap.add_argument("--plan", action="store_true", help="print the order and the estimate; boot nothing")
    ap.add_argument("--cap", type=int, default=int(os.environ.get("PM_EMU_CAP") or 0), help="emulators at once in all")
    ap.add_argument("--per-machine", type=int, default=0, help="at most this many on one machine")
    a = ap.parse_args()
    a.cap = a.cap or default_cap()
    a.per_machine = a.per_machine or a.cap
    todo = {i: scenarios(i, a.label, a.k) for i in dict.fromkeys(a.instances)}
    todo = {i: n for i, n in todo.items() if n}
    tasks = order(todo, a.label)
    est = simulate(tasks, list(todo), a.cap, a.per_machine)
    say("%d scenarios on %d machines, at most %d emulators (%d per machine); about %d min from the timings on file, each machine prepared\n"
        % (len(tasks), len(todo), a.cap, a.per_machine, round(est / 60)))
    if a.plan:
        for secs, inst, name in tasks: print("%5d  %-16s %s" % (secs, inst, name))
        return 0
    if not tasks: return 0
    os.makedirs(os.path.join(HERE, "results"), exist_ok=True); os.makedirs(STATE, exist_ok=True)
    locks = []
    for i in sorted(todo):                      # one run per machine at a time; a second build's run waits for the first
        f = open(os.path.join(STATE, i + ".lock"), "w"); fcntl.flock(f, fcntl.LOCK_EX); locks.append(f)
        open(os.path.join(HERE, "results", "%s-%s.log" % (i, a.label)), "w").close()
        for old in glob.glob(os.path.join(HERE, "results", "%s-%s.j*.log" % (i, a.label))): os.remove(old)
    pool = Pool(a, todo, tasks)
    def term(*_): raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, term)
    t0 = time.time(); rc = 0
    try:
        pool.run()
        for i in todo:
            if i in pool.failed: rc = 1; continue
            if subprocess.run([sys.executable, os.path.join(HERE, "run.py"), i, "--label", a.label, "--merge", pool.root(i)]).returncode: rc = 1
        say("all machines: %d scenarios in %d min\n" % (len(tasks), round((time.time() - t0) / 60)))
        return rc
    except KeyboardInterrupt:
        pool.stop()
        return 130
    finally:
        pool.stop()
        for i in todo: shutil.rmtree(pool.root(i), ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
