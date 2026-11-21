#!/usr/bin/env python3
"""merge.py ART OUT: the shards' results (one artifact folder each, art/results-<instance>-<n>/) into one results tree, OUT/<instance>/<build>/,
with one summary.json per machine holding every shard's scenarios. Scenario folders are copied as they are (each shard ran different ones)."""
import glob, json, os, shutil, sys

art, out = sys.argv[1], sys.argv[2]
merged = {}
for root in sorted(glob.glob(os.path.join(art, "results-*", "compat", "emu", "results"))):
    for f in glob.glob(os.path.join(root, "*", "*", "summary.json")):
        inst, build = f.split(os.sep)[-3:-1]
        d = json.load(open(f))
        m = merged.setdefault((inst, build), dict(d, scenarios=[]))
        have = {s["scenario"] for s in m["scenarios"]}
        m["scenarios"] += [s for s in d["scenarios"] if s["scenario"] not in have]
        src = os.path.dirname(f)
        dst = os.path.join(out, inst, build)
        for e in os.listdir(src):
            if os.path.isdir(os.path.join(src, e)): shutil.copytree(os.path.join(src, e), os.path.join(dst, e), dirs_exist_ok=True)
    for f in glob.glob(os.path.join(root, "*.log")):
        os.makedirs(out, exist_ok=True); shutil.copy(f, os.path.join(out, os.path.basename(f)))
for (inst, build), d in merged.items():
    os.makedirs(os.path.join(out, inst, build), exist_ok=True)
    json.dump(d, open(os.path.join(out, inst, build, "summary.json"), "w"), indent=1)
    print("%s: %d scenarios from the shards" % (inst, len(d["scenarios"])))
