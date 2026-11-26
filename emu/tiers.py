#!/usr/bin/env python3
"""tiers.py [--since REF]: which machines and scenarios a change needs (run.sh --tier changed). Prints two lines, "instances ..." and
"scenarios ..." (none named: all of them), or "nothing"; the reasoning goes to stderr. Anything it cannot place means the full matrix."""
import argparse, ast, os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
COMPAT = os.path.dirname(HERE)
PM = os.environ.get("PM") or os.path.join(os.path.dirname(COMPAT), "plugin-manager")
FULL = ["mpc-g2-3101", "mpc-g2-3100", "mpc-g1-3101", "mpc-x-391", "mpc-g1-391", "mpc-sample-130", "mpc-sample-140"]
QUICK = ["mpc-g1-3101", "mpc-g2-3101"]            # one Gen1 and one Gen2, both on the firmware with the VST2 Plugins path
SAMPLES = ["mpc-sample-130", "mpc-sample-140"]
NO_EMU = re.compile(r"(\.md$|^docs/|^LICENSE$|^NOTICE|^tested\.json$|^tests/(run\.sh|addins\.sh|drive\.c|fixture\.py|snapshot_catalog\.py|catalog-snapshot\.json|devices/))")
MANAGER = re.compile(r"^(src/|vst/|daemon/)")


def git(*a, repo=None):
    return subprocess.run(["git", "-C", repo or PM] + list(a), capture_output=True, text=True)


def names(path, deco):
    return re.findall(r"^@%s\ndef (\w+)" % deco, open(os.path.join(COMPAT, path)).read(), re.M)


def changed_functions(path, ref):
    """The top-level functions of a Python file that differ from REF, or None when something else in it changed too."""
    old = git("show", "%s:%s" % (ref, path), repo=COMPAT)
    if old.returncode: return None
    try: a, b = ast.parse(old.stdout), ast.parse(open(os.path.join(COMPAT, path)).read())
    except (SyntaxError, OSError): return None
    def split(tree):
        fn = {n.name: ast.dump(n) for n in tree.body if isinstance(n, ast.FunctionDef)}
        return fn, [ast.dump(n) for n in tree.body if not isinstance(n, ast.FunctionDef)]
    (fa, oa), (fb, ob) = split(a), split(b)
    if oa != ob: return None
    return sorted(k for k in set(fa) | set(fb) if fa.get(k) != fb.get(k))


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--since", default="origin/main"); a = ap.parse_args()
    def full(why):
        print("full matrix: " + why, file=sys.stderr)
        print("instances " + " ".join(FULL)); print("scenarios"); return 0
    base = git("merge-base", a.since, "HEAD")
    if base.returncode: return full("no merge base with %s" % a.since)
    ref = base.stdout.strip()
    diff = git("diff", "--name-only", ref)                     # committed since REF, and what is not committed yet
    new = git("ls-files", "--others", "--exclude-standard")
    if diff.returncode or new.returncode: return full("git could not list the changes")
    files = sorted(set(diff.stdout.split("\n") + new.stdout.split("\n")) - {""})
    # the harness is in this repository: what changed in it since origin/main, committed or not (a path under emu/)
    cbase = git("merge-base", "origin/main", "HEAD", repo=COMPAT)
    if cbase.returncode: return full("no merge base for the harness")
    href = cbase.stdout.strip()
    hd, hn = git("diff", "--name-only", href, "--", "emu", repo=COMPAT), git("ls-files", "--others", "--exclude-standard", "--", "emu", repo=COMPAT)
    if hd.returncode or hn.returncode: return full("git could not list the harness's changes")
    harness = sorted(set(hd.stdout.split("\n") + hn.stdout.split("\n")) - {""})
    inst, scen = set(), set()
    mgr = names("emu/scenarios.py", "scenario")
    for f in files + harness:
        if f not in harness and NO_EMU.search(f): continue
        if MANAGER.match(f):
            print("%s: the Manager's scenarios" % f, file=sys.stderr); inst.update(QUICK); scen.update(mgr)
        elif f in ("emu/scenarios.py", "emu/modeslot.py", "emu/routes.py", "emu/sample_scenarios.py"):
            fns = changed_functions(f, href)
            deco = {"emu/scenarios.py": "scenario", "emu/modeslot.py": "scenario", "emu/routes.py": "route", "emu/sample_scenarios.py": "sample_scenario"}[f]
            own = names(f, deco)
            if fns is None or set(fns) - set(own): return full("%s: more than its scenarios changed" % f)
            print("%s: %s" % (f, " ".join(fns)), file=sys.stderr)
            scen.update(fns)
            if deco != "sample_scenario": inst.update(QUICK)
            if deco != "scenario": inst.update(SAMPLES)
        else:
            return full("%s: not something this tier can place" % f)
    if not scen:
        print("no scenario depends on what changed", file=sys.stderr); print("nothing"); return 0
    print("instances " + " ".join(i for i in FULL if i in inst)); print("scenarios " + " ".join(sorted(scen)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
