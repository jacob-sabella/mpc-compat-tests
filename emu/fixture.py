#!/usr/bin/env python3
"""The emulator tests' catalog: gathers the zips emu/build.sh made into build/fx/<label>/ and writes catalog.full.json and
patches.json there. Each test serves a view of that folder (emu.Fixture) to the guest as http://10.0.2.2:8731/, the only address the
guest can reach, with a catalog.json cut down to what the test wants on offer.

  fixture.py OUT LABEL VERSION_A VERSION_B      (MPC_VST: the mpc-vst-plugins checkout, for its catalog tools and helper scripts)

Entries: emu-probe (a plugin, 1.0.0 and 1.0.1), emu-addin (an addin), plugin-manager-g2 (VERSION_B: an update for the installed VERSION_A),
and a few packages that must be refused or rolled back.

For the installers that are not the Manager (routes.py): catalog.store.json, the same entries plus emu-probe-out (needs emu-probe) and
emu-fx (only for the MPC Sample, "devices": ["mpc-sample"]), with the dependency picks made by mpc-vst-plugins' catalog_build.py;
catalog.tsv made from it by its catalog_site.py, as the site publishes it for mpc-store.sh; and the helper files catalog.tsv lists
(mpc-store.sh, sync.sh, plugin_list.awk) next to it."""
import hashlib, json, os, shutil, sys, zipfile

out, label, va, vb = sys.argv[1:5]
MV = os.environ["MPC_VST"]
sys.path.insert(0, os.path.join(MV, "tools"))
import catalog_build, catalog_site, deps   # noqa: E402
fx = os.path.join(out, "fx", label)
os.makedirs(fx, exist_ok=True)
BASE = "http://10.0.2.2:8731/"
ARCHES = ("armv7", "aarch64")


def asset(path):
    d = open(path, "rb").read()
    return {"url": BASE + os.path.basename(path), "sha256": hashlib.sha256(d).hexdigest(), "size": len(d)}


def manifest(path):
    with zipfile.ZipFile(path) as z:
        n = [x for x in z.namelist() if x.count("/") == 1 and x.endswith("/mpc-plugin.json")]
        return json.loads(z.read(n[0])) if n else {}


def take(src):
    dst = os.path.join(fx, os.path.basename(src))
    shutil.copyfile(src, dst)
    return dst


def version(ver, files, date, **more):
    """One catalog version from its per-CPU zips ({arch: path}); the top-level url/sha256/size are the armv7 zip's, as in the live catalog
    (the aarch64 zip's for a version with no armv7 one, gen2_only). The manifest's depends, resolved as catalog_build.py does."""
    a = {arch: asset(p) for arch, p in files.items()}
    first = a.get("armv7") or next(iter(a.values()))
    m = manifest(files.get("armv7") or next(iter(files.values())))
    v = {"version": ver, "size": first["size"], "sha256": first["sha256"], "url": first["url"], "assets": a, "defer": True,
         "channel": "stable", "date": date, "yanked": False, "manifest": m, "max_glibc": m.get("max_glibc", ""), "os_compat": m.get("os_compat", []),
         "gen2": "aarch64" in a, "notes": "A fixture release."}
    if "armv7" not in a: v["gen2_only"] = True
    if m.get("depends"):
        v["depends"] = [dict(zip(("id", "rules"), deps.resolve(d, ver))) for d in m["depends"]]
        v["conflicts"] = m.get("conflicts", [])
    v.update(more)
    return v


def entry(pid, name, kind, versions, **more):
    e = {"id": pid, "name": name, "author": "acme", "repo": "acme/" + pid, "kind": kind, "license": "MIT", "style": "utility", "tags": ["test"],
         "summary": "A stand-in for the emulator tests.", "distribution": "release", "source_available": True, "latest": versions[0]["version"],
         "versions": versions}
    e.update(more)
    return e


plugins = []
w = lambda arch, name: os.path.join(out, "fixture-work", arch, "dist", name)
probe = [version(v, {a: take(w(a, "Emu-Probe-%s-mpc-%s.zip" % (v, a))) for a in ARCHES}, d) for v, d in (("1.0.1", "2026-02-01"), ("1.0.0", "2026-01-01"))]
plugins.append(entry("emu-probe", "Emu Probe", "instrument", probe, components=[{"uid": "EmPr", "name": "Emu Probe"}]))
plugins.append(entry("emu-addin", "Emu addin", "addin", [version("1.0.0", {a: take(w(a, "Emu-addin-1.0.0-mpc-%s.zip" % a)) for a in ARCHES}, "2026-01-02")]))
pm = lambda arch, v: os.path.join(out, "pm", label, arch, v, "dist", "Plugin-Manager-G2-%s-mpc-%s.zip" % (v, arch))
plugins.append(entry("plugin-manager-g2", "Open MPC Plugin Manager", "instrument",
                     [version(vb, {a: take(pm(a, vb)) for a in ARCHES}, "2026-03-01"), version(va, {a: take(pm(a, va)) for a in ARCHES}, "2026-02-15")],
                     author="sd88me", repo="open-mpc/plugin-manager", components=[{"uid": "OMG2", "name": "Plugin Manager"}]))


def handmade(pid, name, files, date, kind="effect"):
    """A package written by hand (the same for both CPUs): one that fails, or that must be refused."""
    zp = os.path.join(fx, "%s-1.0.0.zip" % name)
    with zipfile.ZipFile(zp, "w") as z:
        for fn, body in files:
            z.writestr("%s-1.0.0/%s" % (name, fn), body)
    plugins.append(entry(pid, name, kind, [version("1.0.0", {a: zp for a in ARCHES}, date)]))


# its installer writes part of its folder and then fails: the folder and MPC.settings must be as before
FAIL_SH = ('#!/bin/sh\nwhile [ $# -gt 0 ]; do [ "$1" = -t ] && T=$2; shift; done\n'
           'mkdir -p "$T/acme - VST - Faily" && echo partial > "$T/acme - VST - Faily/partial" && echo "disk full" && exit 3\n')
handmade("faily", "Faily", [("install.sh", FAIL_SH), ("uninstall.sh", "#!/bin/sh\nexit 0\n"),
                            ("mpc-plugin.json", '{"schema": 1, "id": "faily", "folder": "portable/acme - VST - Faily"}\n')], "2025-03-01")
# the RACK addin for the MPC Sample (sample_scenarios.py): served, not in a catalog
for n in os.listdir(os.path.dirname(w("aarch64", "x"))):   # the RACK addin, whichever version build.sh put there
    if n.startswith("MPC-Sample-rack-addin-") and n.endswith("-mpc-aarch64.zip"): take(w("aarch64", n))
json.dump({"schema": 1, "generated": "2026-03-01T00:00:00Z", "plugins": plugins, "collections": []}, open(os.path.join(fx, "catalog.full.json"), "w"), indent=1)

# the other installers' catalog: the Manager's entries and two more
store = json.loads(json.dumps(plugins))
store.append(entry("emu-probe-out", "Emu Probe Out", "effect", [version("1.0.0", {a: take(w(a, "Emu-Probe-Out-1.0.0-mpc-%s.zip" % a)) for a in ARCHES}, "2026-02-10")]))
store.append(entry("emu-fx", "Emu FX", "effect", [version(v, {"aarch64": take(w("aarch64", "Emu-FX-%s-mpc-aarch64.zip" % v))}, d)
                                                  for v, d in (("1.0.1", "2026-02-20"), ("1.0.0", "2026-02-05"))], devices=["mpc-sample"]))
problems = []
catalog_build.apply_dependencies(store, problems)
if problems: raise SystemExit("fixture: the store catalog hides versions: %s" % problems)
store_doc = {"schema": 1, "generated": "2026-03-01T00:00:00Z", "plugins": store, "collections": []}
json.dump(store_doc, open(os.path.join(fx, "catalog.store.json"), "w"), indent=1)
helpers = [("mpc-store.sh", os.path.join(MV, "tools", "mpc-store.sh")), ("sync.sh", os.path.join(MV, "tools", "release", "sync.sh")),
           ("plugin_list.awk", os.path.join(MV, "tools", "release", "plugin_list.awk"))]       # as catalog_site.py main() lists them
for name, path in helpers: shutil.copyfile(path, os.path.join(fx, name))
open(os.path.join(fx, "catalog.tsv"), "w", newline="\n").write(catalog_site.tsv(store_doc, helpers))
json.dump({"schema": 1, "patches": []}, open(os.path.join(fx, "patches.json"), "w"), indent=1)
print("fixture: %d entries, %d files in %s" % (len(plugins), len(os.listdir(fx)), fx))
