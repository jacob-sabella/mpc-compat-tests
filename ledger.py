#!/usr/bin/env python3
"""ledger.py: every scenario result kept, one record per (run, machine, scenario), so the page can be made from many small runs.

  ledger.py add RESULTS [--sources sources.txt] [--run ID] [--url URL] ...
      Records from RESULTS/<instance>/<label>/summary.json (a harness results tree, or merge.py's output).
  ledger.py add-art ART [--url URL] ...
      Records from a compat run's downloaded artifacts, ART/results-<instance>-<n>/, each shard with its own sources.txt.
  ledger.py show [--instance I] [--scenario S]
      The records, newest last, one line each.

The ledger is a folder (LEDGER, default ./ledger): records/<yyyy>/<mm>/<run>.jsonl, one file per run, never rewritten, so two runs
adding at once never touch the same file; and shots/<run>/<instance>/<scenario>/*.webp, the screen captures of each
record, shrunk (SHOT_WIDTH, SHOT_QUALITY; needs ImageMagick, else they are kept as they are).
records/ is what is kept in git; shots/ can be pruned (prune-shots keeps the captures of the latest record of each cell only).

A record says what ran: the Manager's and the installers' commits (pm, mpc_vst), QEngine's, the machine id (firmware, product code,
QEngine, QEMU: emu/machine.py) and the build id (the Manager's zips and the catalog: run.py build_id). A commit that is not known is
null: results made before the ledger, from a fixture that has since been rebuilt, say nothing about which code they ran.

Adding is idempotent: a result already in the ledger (same instance, build, scenario, outcome, time and problems) is not added again.
run.py's summary.json keeps the scenarios of earlier runs beside the new ones, so re-adding a results tree adds only what is new."""
import argparse, datetime, glob, hashlib, json, os, shutil, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.environ.get("LEDGER") or os.path.join(HERE, "ledger")
SHOT_WIDTH, SHOT_QUALITY = 640, 50     # the captures as kept: half the MPC's 1280x800, WebP; ~8 KB each, the screen's text still readable
NAMES = {"plugin-manager": "pm", "mpc-vst-plugins": "mpc_vst", "QEngine": "qengine"}       # sources.txt's names (compat.yml)


def utc(ts): return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def git_head(path):
    try: return subprocess.run(["git", "-C", path, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip() or None
    except (OSError, subprocess.CalledProcessError): return None


def read_sources(path):
    """sources.txt (compat.yml): '<repo folder> <sha>' per line."""
    got = {}
    try:
        for line in open(path):
            name, _, sha = line.strip().partition(" ")
            if name in NAMES and sha: got[NAMES[name]] = sha.strip()
    except OSError: pass
    return got


def fixture_commit(fx, label, made):
    """The Manager's commit a local result ran, when it can be told: emu/build/fx/<label>/commit, if that fixture was built before the
    result was written and not since (a later build.sh overwrites it)."""
    d = os.path.join(fx, label)
    try:
        if os.path.getmtime(d) <= made: return open(os.path.join(d, "commit")).read().strip() or None
    except OSError: pass
    return None


def store_shot(src, dst_dir):
    """A capture, shrunk to WebP with ImageMagick (magick, or convert); copied as it is where there is neither. Returns its name."""
    base = os.path.splitext(os.path.basename(src))[0]
    for tool in (["magick"], ["convert"]):
        if shutil.which(tool[0]):
            name = base + ".webp"
            r = subprocess.run(tool + [src, "-resize", "%dx>" % SHOT_WIDTH, "-quality", str(SHOT_QUALITY), os.path.join(dst_dir, name)],
                               capture_output=True)
            if r.returncode == 0: return name
    shutil.copyfile(src, os.path.join(dst_dir, os.path.basename(src)))
    return os.path.basename(src)


def key(r):
    """What makes two records the same result."""
    return json.dumps([r["instance"], r.get("build"), r["scenario"], r["ok"], r["seconds"], r["problems"]])


def load(ledger=None):
    """Every record, oldest first."""
    out = []
    for f in sorted(glob.glob(os.path.join(ledger or LEDGER, "records", "*", "*", "*.jsonl"))):
        for line in open(f):
            line = line.strip()
            if line: out.append(json.loads(line))
    out.sort(key=lambda r: (r["at"], r["run"]))
    return out


def records_from(results, src, run, extra, fx, at=None):
    """The records of one results tree; src: {pm, mpc_vst, qengine}, or {} to work them out from the fixture."""
    out = []
    for f in sorted(glob.glob(os.path.join(results, "*", "*", "summary.json"))):
        d = json.load(open(f)); p = d.get("prepare", {}); made = os.path.getmtime(f)
        inst, label = d["instance"], d.get("build") or os.path.basename(os.path.dirname(f))
        s = dict(src)
        if "pm" not in s and fx: s["pm"] = fixture_commit(fx, label, made)
        for r in d["scenarios"]:
            shots = sorted(glob.glob(os.path.join(os.path.dirname(f), r["scenario"], "*.png")))
            rec = {"run": run, "at": r.get("at") or at or utc(made), "instance": inst, "scenario": r["scenario"], "ok": r["ok"],
                   "seconds": r["seconds"], "problems": r.get("problems", []), "findings": r.get("findings", []), "retried": r.get("retried", []), "doc": r.get("doc", ""),
                   "pm": s.get("pm"), "mpc_vst": s.get("mpc_vst"), "qengine": s.get("qengine"),
                   "machine": p.get("machine"), "build": p.get("build"), "label": label, "firmware": p.get("firmware"),
                   "arch": p.get("arch"), "external_plugins": p.get("external_plugins", False)}
            rec.update({k: v for k, v in extra.items() if v})
            rec["_shots"] = shots
            out.append(rec)
    return out


def write(recs, ledger, run):
    """Add the records that are not in the ledger yet, as records/<yyyy>/<mm>/<run>.jsonl, and their captures. Returns how many."""
    have = {key(r) for r in load(ledger)}
    new, seen = [], set()
    for r in recs:
        k = key(r)
        if k in have or k in seen: continue
        seen.add(k); new.append(r)
    if not new: return 0
    when = min(r["at"] for r in new)
    path = os.path.join(ledger, "records", when[:4], when[5:7], run + ".jsonl")
    if os.path.exists(path): raise SystemExit("%s exists: a run id is used once" % path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w") as fo:
        for r in new:
            shots = r.pop("_shots", [])
            dst = os.path.join(ledger, "shots", run, r["instance"], r["scenario"])
            if shots: os.makedirs(dst, exist_ok=True)
            r["shots"] = [store_shot(s, dst) for s in shots]
            fo.write(json.dumps(r, sort_keys=True) + "\n")
    os.rename(path + ".tmp", path)
    return len(new)


def run_id(given, *parts):
    if given: return given
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return "%s-%s" % (stamp, hashlib.sha256("\0".join(parts).encode()).hexdigest()[:6])


def cmd_add(a):
    src = read_sources(a.sources) if a.sources else {}
    for k, path in (("pm", a.pm), ("mpc_vst", a.mpc_vst), ("qengine", a.qengine)):
        if path: src[k] = git_head(path) if os.path.isdir(path) else path
    extra = {"url": a.url, "runner": a.runner, "pm_repo": a.pm_repo, "mpc_vst_repo": a.mpc_vst_repo}
    run = run_id(a.run, a.results)
    n = write(records_from(a.results, src, run, extra, None if src.get("pm") else a.fixture, a.at), a.ledger, run)
    print("%s: %d new records" % (run, n))


def cmd_add_art(a):
    extra = {"url": a.url, "runner": a.runner, "pm_repo": a.pm_repo, "mpc_vst_repo": a.mpc_vst_repo}
    run = run_id(a.run, a.art)
    recs = []
    for d in sorted(glob.glob(os.path.join(a.art, "results-*"))):
        recs += records_from(os.path.join(d, "compat", "emu", "results"), read_sources(os.path.join(d, "sources.txt")), run, extra, None, a.at)
    n = write(recs, a.ledger, run)
    print("%s: %d new records" % (run, n))


def cmd_show(a):
    for r in load(a.ledger):
        if a.instance and r["instance"] != a.instance or a.scenario and r["scenario"] != a.scenario: continue
        print("%s %-16s %-32s %-6s %4ds pm=%s %s" % (r["at"], r["instance"], r["scenario"], "ok" if r["ok"] else "FAILED", r["seconds"],
                                                     (r.get("pm") or "?")[:7], r["run"]))


def cmd_prune_shots(a):
    """Remove the captures of every record that is no longer the latest of its cell."""
    latest = {}
    for r in load(a.ledger): latest[(r["instance"], r["scenario"])] = r["run"]
    keep = {(run, i, s) for (i, s), run in latest.items()}
    gone = 0
    for d in glob.glob(os.path.join(a.ledger, "shots", "*", "*", "*")):
        run, i, s = d.split(os.sep)[-3:]
        if (run, i, s) not in keep: shutil.rmtree(d); gone += 1
    for d in glob.glob(os.path.join(a.ledger, "shots", "*")):
        for sub in glob.glob(os.path.join(d, "*")):
            if not os.listdir(sub): os.rmdir(sub)
        if not os.listdir(d): os.rmdir(d)
    print("%d capture folders removed" % gone)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ledger", default=LEDGER)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("add", "add-art"):
        p = sub.add_parser(name)
        p.add_argument("art" if name == "add-art" else "results")
        p.add_argument("--run", help="the run's id (default: the time and a hash); used once")
        p.add_argument("--url", help="where the run's logs and artifacts are (the Actions run)")
        p.add_argument("--runner", help="what ran it (hosted, desktop, ...)")
        p.add_argument("--at", help="when it ran (UTC, 2026-11-24T23:12:41Z), where the files' times are not it (downloaded artifacts)")
        p.add_argument("--pm-repo", help="owner/repo of the Plugin Manager tested")
        p.add_argument("--mpc-vst-repo", help="owner/repo of the installers tested")
        if name == "add":
            p.add_argument("--sources", help="sources.txt naming the commits")
            p.add_argument("--pm", help="the Plugin Manager's checkout (its HEAD) or commit")
            p.add_argument("--mpc-vst", help="mpc-vst-plugins' checkout or commit")
            p.add_argument("--qengine", help="QEngine's checkout or commit")
            p.add_argument("--fixture", default=os.path.join(HERE, "emu", "build", "fx"),
                           help="emu/build/fx: the Manager's commit of a local result, when no commit is given")
    p = sub.add_parser("show"); p.add_argument("--instance"); p.add_argument("--scenario")
    sub.add_parser("prune-shots")
    a = ap.parse_args()
    {"add": cmd_add, "add-art": cmd_add_art, "show": cmd_show, "prune-shots": cmd_prune_shots}[a.cmd](a)
