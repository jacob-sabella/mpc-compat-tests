#!/usr/bin/env python3
"""plan.py: which jobs a compat run starts, as a GitHub Actions matrix ({"include": [...]}, one entry per job).

  plan.py full [--suite smoke|full] [--machines "a b"] [--scenarios "x y"]
      Each machine's smoke list in machines.json ("all": every scenario it has; none: left out), or with --suite full every scenario of
      every machine; naming machines or scenarios overrides both. Each machine split into shards (machines.json's shards, balanced by
      timings.json).
  plan.py rolling --pm SHA --mpc-vst SHA [--jobs N] [--budget SECONDS] [--again]
      Only the cells with no result on the code under test (never run, other code, commit unknown; the longest without first). When
      neither the Manager nor the installers changed since every cell ran, there is nothing to run: a quiet repository is a quiet
      schedule. --again adds the cells whose newest result failed or that are flaky, then the oldest (a run by hand, to confirm).
      Packed into at most N jobs, one machine each (so a job pulls one prebuilt image), each about SECONDS of scenarios as timed on a
      desktop (timings.json).

A job entry is the machine's line from machines.json plus shard, of, ks (its scenarios) and est (their desktop seconds)."""
import argparse, json, os, re

import ledger as L

HERE = os.path.dirname(os.path.abspath(__file__))


def scenarios():
    """The scenarios each kind of machine runs, in the order they are written."""
    read = lambda f, deco: re.findall(r"^@%s\ndef (\w+)" % deco, open(os.path.join(HERE, "emu", f)).read(), re.M)
    routes = read("routes.py", "route")
    return {"manager": read("scenarios.py", "scenario") + routes, "sample": read("sample_scenarios.py", "sample_scenario") + routes}


def cost(times, inst, s):
    t = times.get(inst) or {}
    return t.get(s, sum(t.values()) / len(t) if t else 200)


def full(ms, names, times, pick, ks, suite="smoke"):
    known = set(sum(names.values(), []))
    for what, got, have in (("machines.json", pick, {m["instance"] for m in ms}), ("the harness's scenarios", ks, known)):
        if set(got) - have: raise SystemExit("not in %s: %s" % (what, " ".join(sorted(set(got) - have))))
    smoke = suite != "full" and not pick and not ks
    for m in ms:
        if m.get("smoke") != "all" and set(m.get("smoke", [])) - set(names[m.get("kind", "manager")]):
            raise SystemExit("%s: smoke names a scenario it does not have" % m["instance"])
    inc = []
    for m in ms:
        if pick and m["instance"] not in pick: continue
        own = names[m.get("kind", "manager")]
        todo = (own if m.get("smoke") == "all" else m.get("smoke", [])) if smoke else [s for s in ks if s in own] if ks else own
        if not todo: continue
        total = sum(cost(times, m["instance"], s) for s in todo)        # a shard costs its setup too: at least ~400 s of scenarios each
        n = max(1, min(int(m.get("shards", 1)), len(todo), int(total // 400) or 1))
        shards = [[] for _ in range(n)]; load = [0] * n
        for s in sorted(todo, key=lambda s: -cost(times, m["instance"], s)):     # longest first, onto the least loaded shard
            k = load.index(min(load)); shards[k].append(s); load[k] += cost(times, m["instance"], s)
        for k, sc in enumerate(shards):
            inc.append(dict(m, shard=k + 1, of=n, ks=" ".join(sorted(sc, key=todo.index)), est=int(load[k])))
    return inc


def rolling(ms, names, times, recs, target, jobs, budget, again=False):
    last, flaky = {}, set()
    for r in recs: last[(r["instance"], r["scenario"])] = r
    seen = {}
    for r in recs:
        if all(r.get(k) == v for k, v in target.items()): seen.setdefault((r["instance"], r["scenario"]), set()).add(r["ok"])
    flaky = {c for c, oks in seen.items() if len(oks) > 1}
    cells = []
    for m in ms:
        for s in names[m.get("kind", "manager")]:
            r = last.get((m["instance"], s)); c = (m["instance"], s)
            on_target = r is not None and all(r.get(k) == v for k, v in target.items())
            rank = 0 if not on_target else 1 if (not r["ok"] or c in flaky) else 2
            if rank and not again: continue
            cells.append((rank, r["at"] if r else "", m["instance"], s))
    cells.sort()
    byname = {m["instance"]: m for m in ms}
    open_jobs = []                                                     # [instance, [scenarios], load]
    for rank, at, inst, s in cells:
        c = cost(times, inst, s)
        j = next((j for j in open_jobs if j[0] == inst and j[2] + c <= budget), None)
        if j is None and len(open_jobs) >= jobs or j is None and any(j[0] == inst for j in open_jobs):
            # rather a job a little over its budget than a new one for a short scenario (a job's setup costs more)
            j = next((j for j in open_jobs if j[0] == inst and j[2] + c <= budget * 1.25), None)
        if j is None:
            if len(open_jobs) >= jobs: continue                        # no room this time: a later run takes it
            j = [inst, [], 0]; open_jobs.append(j)
        j[1].append(s); j[2] += c
    inc = []
    for inst in dict.fromkeys(j[0] for j in open_jobs):
        mine = [j for j in open_jobs if j[0] == inst]
        order = names[byname[inst].get("kind", "manager")]
        for k, j in enumerate(mine):
            inc.append(dict(byname[inst], shard=k + 1, of=len(mine), ks=" ".join(sorted(j[1], key=order.index)), est=int(j[2])))
    return inc, cells


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("mode", choices=("full", "rolling"))
    ap.add_argument("--machines", default=""); ap.add_argument("--scenarios", default="")
    ap.add_argument("--suite", default="smoke", choices=("smoke", "full"), help="full: the smoke lists or every scenario")
    ap.add_argument("--pm"); ap.add_argument("--mpc-vst")
    ap.add_argument("--jobs", type=int, default=6, help="rolling: jobs at most")
    ap.add_argument("--budget", type=int, default=700, help="rolling: desktop seconds of scenarios per job (a hosted runner is ~4x slower)")
    ap.add_argument("--ledger", default=L.LEDGER)
    ap.add_argument("--explain", action="store_true", help="rolling: also print why, to stderr")
    ap.add_argument("--again", action="store_true", help="rolling: also the failed, flaky and oldest cells when nothing is new")
    a = ap.parse_args()
    ms = json.load(open(os.path.join(HERE, "machines.json")))["machines"]
    times = json.load(open(os.path.join(HERE, "timings.json")))["instances"]
    names = scenarios()
    if a.mode == "full":
        inc = full(ms, names, times, a.machines.split(), a.scenarios.split(), a.suite)
    else:
        pick = a.machines.split()
        inc, cells = rolling([m for m in ms if not pick or m["instance"] in pick], names, times, L.load(a.ledger),
                             {"pm": a.pm, "mpc_vst": a.mpc_vst}, a.jobs, a.budget, a.again)
        if a.explain:
            import sys
            why = ("no result on the code under test", "failed or flaky", "oldest")
            for rank, at, inst, s in cells[:40]: print("%-24s %-16s %-32s %s" % (why[rank], inst, s, at or "never"), file=sys.stderr)
    print(json.dumps({"include": inc}))
