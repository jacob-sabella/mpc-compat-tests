#!/usr/bin/env python3
"""report.py [--label BUILD] [--out DIR]: the compatibility page, made from what the last runs left in results/.

One static folder (index.html and the screen captures), to be published as it is. Rows are the scenarios, columns the machines that
were run (a device identity and a firmware). A cell is passed, failed, or not run; a note marks a scenario that the emulator covers
only in part. The page is public: it shows what was checked, what failed and what was on screen, and none of the device's log files.
Nothing here needs the emulator or the firmware: only results/, and the instances' instance.env for the device name and the image."""
import argparse, datetime, glob, hashlib, html, json, os, shutil, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.environ.get("HARNESS") or os.path.join(HERE, "emu")                     # the harness: scenarios.py, results/
PM = os.environ.get("PM") or os.path.join(os.path.dirname(HERE), "plugin-manager")
QENGINE = os.environ.get("QENGINE") or os.path.join(os.path.dirname(PM), "QEngine")
MPC_VST = os.environ.get("MPC_VST") or os.path.join(os.path.dirname(os.path.dirname(PM)), "open-mpc")

DEVICES = {"ACVA": "MPC One", "ACVA2": "MPC One+", "ACVA3": "MPC One (Gen 2)", "ACV5": "MPC X", "ACV5S": "MPC X SE", "ACV8": "MPC Live",
           "ACVB": "MPC Live II", "ACVG": "MPC Live III", "ACVC": "MPC XL", "ACVM": "MPC Key 61", "ACVM2": "MPC Key 61 (Gen 2)",
           "ACVR": "MPC Key 37", "ACVR2": "MPC Key 37 (Gen 2)", "ADA2": "Force", "ADA2S": "Force (Gen 2)",
           "AC50": "MPC Sample"}


def os_name(m):
    """The firmware's own name: the MPC Sample runs MPC Sample 1.x, not MPC OS."""
    return ("MPC Sample %s" if m.get("code") == "AC50" else "MPC OS %s") % m["firmware"]

# What a pass does not mean. (scenario, test on the machine's facts) -> the note shown in the cell.
PARTIAL = [
    ("vst2_plugin_json", lambda m: not m.get("external_plugins"),
     "This firmware has no VST2 Plugins folder. Only what the Manager writes is checked, not MPC loading from it."),
]


def ver(v): return tuple(int(x) for x in v.split(".") if x.isdigit())


def git(path, *a):
    try: return subprocess.run(["git", "-C", path, *a], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError): return ""


MACHINES = {}                           # --machines: instance -> {"code", "image", "sha256"}, for results made elsewhere (CI)


def instance_env(name):
    m = MACHINES.get(name)
    if m: return {"PRODUCT_CODE": m.get("code", ""), "FIRMWARE_IMG": m.get("image", ""), "SHA256": m.get("sha256", "")}
    env = {}
    try:
        for line in open(os.path.join(QENGINE, "build", "instances", name, "instance.env")):
            k, _, v = line.strip().partition("=")
            env[k] = v.strip("'\"")
    except OSError: pass
    return env


def sha256(path):
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for b in iter(lambda: f.read(1 << 20), b""): h.update(b)
        return h.hexdigest()
    except OSError: return ""


def load(label, results):
    machines = []
    for f in sorted(glob.glob(os.path.join(results, "*", label, "summary.json"))):
        d = json.load(open(f)); env = instance_env(d["instance"]); p = d.get("prepare", {})
        machines.append({"instance": d["instance"], "dir": os.path.dirname(f), "code": env.get("PRODUCT_CODE", ""),
                         "device": DEVICES.get(env.get("PRODUCT_CODE", ""), env.get("PRODUCT_CODE", "") or d["instance"]),
                         "firmware": p.get("firmware", "?"), "arch": p.get("arch", "?"), "external_plugins": p.get("external_plugins", False),
                         "image": os.path.basename(env.get("FIRMWARE_IMG", "")), "sha256": env.get("SHA256") or sha256(env.get("FIRMWARE_IMG", "")),
                         "when": datetime.datetime.fromtimestamp(os.path.getmtime(f), datetime.timezone.utc),
                         "results": {r["scenario"]: r for r in d["scenarios"]}})
    machines.sort(key=lambda m: (ver(m["firmware"]), m["arch"] != "armv7", m["device"]))
    return machines


def scenarios_in_order(machines):
    names, docs = [], {}
    try:                                    # the order they are written in, when the harness is importable
        import re
        names = re.findall(r"^@scenario\ndef (\w+)", open(os.path.join(HARNESS, "scenarios.py")).read(), re.M)
        names += re.findall(r"^@sample_scenario\ndef (\w+)", open(os.path.join(HARNESS, "sample_scenarios.py")).read(), re.M)
        names += re.findall(r"^@route\ndef (\w+)", open(os.path.join(HARNESS, "routes.py")).read(), re.M)
    except OSError: pass
    for m in machines:
        for n, r in m["results"].items():
            if n not in names: names.append(n)
            docs.setdefault(n, r.get("doc", ""))
    return [n for n in names if any(n in m["results"] for m in machines)], docs


CSS = """
:root{--bg:#f7f7f5;--panel:#fff;--ink:#1c1d1f;--mute:#62666d;--line:#dcdcd6;--pass:#1a7f4b;--pass-bg:#e3f4ea;--fail:#b3261e;--fail-bg:#fbe6e4;
--none:#8a8d93;--none-bg:#eeeeea;--part:#8a5a00;--part-bg:#fbf0d6;--link:#1f5fbf}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#141516;--panel:#1d1f21;--ink:#ececec;--mute:#a0a4ab;--line:#33363a;--pass:#5fd08f;
--pass-bg:#153324;--fail:#ff8a80;--fail-bg:#3b1a18;--none:#8d9197;--none-bg:#26282b;--part:#f0c060;--part-bg:#3a2e12;--link:#8ab4ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1180px;margin:0 auto;padding:28px 16px 56px}h1{font-size:26px;margin:0 0 6px;letter-spacing:-.01em}
h2{font-size:18px;margin:36px 0 10px}p{margin:0 0 10px}.lede{color:var(--mute);max-width:70ch}a{color:var(--link)}
.wrap{overflow-x:auto;background:var(--panel);border:1px solid var(--line);border-radius:10px}
table{border-collapse:collapse;width:100%;min-width:640px}th,td{padding:9px 12px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
tr:last-child td{border-bottom:0}thead th{font-weight:600;font-size:13px;white-space:nowrap;background:var(--panel);position:sticky;top:0}
thead small{display:block;font-weight:400;color:var(--mute)}td.s{min-width:240px}td.s b{font-weight:600}td.s span{display:block;color:var(--mute);font-size:13px}
.c{white-space:nowrap}.pill{display:inline-block;padding:2px 9px;border-radius:999px;font-size:13px;font-weight:600;text-decoration:none}
.pass{color:var(--pass);background:var(--pass-bg)}.fail{color:var(--fail);background:var(--fail-bg)}.none{color:var(--none);background:var(--none-bg)}
.part{color:var(--part);background:var(--part-bg)}.t{color:var(--mute);font-size:12px;margin-left:6px;font-variant-numeric:tabular-nums}
.legend{display:flex;flex-wrap:wrap;gap:8px 18px;margin:12px 0 0;color:var(--mute);font-size:13px}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(270px,1fr));gap:12px}.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.card h3{margin:0 0 6px;font-size:15px}.card dl{margin:0;display:grid;grid-template-columns:auto 1fr;gap:2px 10px;font-size:13px}.card dt{color:var(--mute)}
.card dd{margin:0;overflow-wrap:anywhere}code{font:12px ui-monospace,Menlo,Consolas,monospace}
details{background:var(--panel);border:1px solid var(--line);border-radius:10px;margin:0 0 8px;padding:0 14px}summary{cursor:pointer;padding:10px 0;font-weight:600}
details ul{margin:0 0 10px;padding-left:20px}.shots{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 14px}.shots a{display:block;width:230px;max-width:100%}
.shots img{width:100%;border:1px solid var(--line);border-radius:6px;display:block}.shots span{font-size:12px;color:var(--mute)}
ul.plain{padding-left:20px;max-width:80ch}footer{margin-top:40px;color:var(--mute);font-size:13px}
"""


def build(label, out, results):
    e = html.escape
    machines = load(label, results)
    if not machines: raise SystemExit("no results for build %r under %s" % (label, results))
    names, docs = scenarios_in_order(machines)
    shutil.rmtree(out, ignore_errors=True); os.makedirs(out)
    notes = lambda n, m: [t for s, test, t in PARTIAL if s == n and test(m)]
    cols = "".join("<th>%s<small>%s · %s</small></th>" % (e(m["device"]), e(os_name(m)), e(m["arch"])) for m in machines)
    rows, details = [], []
    for n in names:
        cells = []
        for m in machines:
            r = m["results"].get(n); aid = "%s-%s" % (m["instance"], n)
            if r is None: cells.append('<td class="c"><span class="pill none">not run</span></td>'); continue
            kind, word = ("fail", "failed") if not r["ok"] else ("part", "passed*") if notes(n, m) else ("pass", "passed")
            cells.append('<td class="c"><a class="pill %s" href="#%s">%s</a><span class="t">%ds</span></td>' % (kind, aid, word, r["seconds"]))
            shots = []
            for png in sorted(glob.glob(os.path.join(m["dir"], n, "*.png"))):
                rel = os.path.join("shots", m["instance"], n, os.path.basename(png))
                os.makedirs(os.path.dirname(os.path.join(out, rel)), exist_ok=True); shutil.copyfile(png, os.path.join(out, rel))
                shots.append('<a href="%s"><img loading="lazy" src="%s" alt="MPC screen: %s"><span>%s</span></a>' %
                             (e(rel), e(rel), e(os.path.basename(png)), e(os.path.splitext(os.path.basename(png))[0])))
            body = ""
            if r["problems"]: body += "<ul>%s</ul>" % "".join("<li>%s</li>" % e(p) for p in r["problems"])
            for t in notes(n, m): body += "<p>* %s</p>" % e(t)
            if r.get("findings"): body += "<p>Found (not failed):</p><ul>%s</ul>" % "".join("<li>%s</li>" % e(f) for f in r["findings"])
            if shots: body += '<div class="shots">%s</div>' % "".join(shots)
            details.append((not r["ok"], '<details id="%s"%s><summary><span class="pill %s">%s</span> %s · %s, %s</summary>%s</details>' %
                            (aid, "" if r["ok"] else " open", kind, word, e(n), e(m["device"]), e(os_name(m)), body or "<p>No captures.</p>")))
        rows.append('<tr><td class="s"><b>%s</b><span>%s</span></td>%s</tr>' % (e(n.replace("_", " ")), e(docs.get(n, "").rstrip(":").split(". ")[0].rstrip(".") + "."), "".join(cells)))
    total = sum(len(m["results"]) for m in machines); failed = sum(1 for m in machines for r in m["results"].values() if not r["ok"])
    cards = "".join(
        '<div class="card"><h3>%s · %s</h3><dl><dt>CPU</dt><dd>%s</dd><dt>Scenarios</dt><dd>%d run, %d failed</dd><dt>Run</dt><dd>%s UTC</dd>'
        '<dt>Image</dt><dd>%s</dd><dt>sha256</dt><dd><code>%s</code></dd></dl></div>' %
        (e(m["device"]), e(os_name(m)), e(m["arch"]), len(m["results"]), sum(1 for r in m["results"].values() if not r["ok"]),
         m["when"].strftime("%Y-%m-%d %H:%M"), e(m["image"] or "?"), e(m["sha256"] or "?")) for m in machines)
    src = [("Plugin Manager", PM), ("mpc-vst-plugins (installers, wrapper)", MPC_VST), ("QEngine", QENGINE)]
    tested = "".join("<li>%s: <code>%s</code>%s</li>" % (e(name), e(git(p, "rev-parse", "--short", "HEAD") or "?"),
                     " (with local changes)" if git(p, "status", "--porcelain", "--untracked-files=no") else "") for name, p in src)
    limits = ["MPC runs in an emulator (QEngine on QEMU), not on hardware: drives are disk images, there is no audio path, pads or Wi-Fi.",
              "A pass says the Manager did the right thing inside that firmware. It does not say an installed plugin makes sound.",
              "A device or firmware that is not a column here was not run, which is not the same as not working."]
    page = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Plugin Manager compatibility</title><style>%s</style></head><body><main>
<h1>Plugin Manager compatibility</h1>
<p class="lede">The OpenMPC Plugin Manager, run inside Akai's own MPC firmware under emulation. %d checks across %d machines, %s. Regenerated after each run; a run is started when the Manager or the installers change, or when new firmware comes out.</p>
<div class="wrap"><table><thead><tr><th>Scenario</th>%s</tr></thead><tbody>%s</tbody></table></div>
<div class="legend"><span><span class="pill pass">passed</span> every check held</span><span><span class="pill part">passed*</span> passed, with a limit on what that covers</span>
<span><span class="pill fail">failed</span> see the details below</span><span><span class="pill none">not run</span> no result for this machine</span><span>the time is how long the scenario took</span></div>
<h2>What was tested</h2><div class="cards">%s</div><ul class="plain" style="margin-top:12px">%s</ul>
<h2>What this does not cover</h2><ul class="plain">%s</ul>
<h2>Details</h2>%s
<footer>Made by report.py on %s UTC. Emulation by <a href="https://github.com/Peyton-C/QEngine">QEngine</a>, by <a href="https://github.com/Peyton-C">Peyton Cashin (Peyton-C)</a> and <a href="https://github.com/Peyton-C/QEngine/graphs/contributors">contributors</a>. The firmware is Akai's and is not distributed with these tests or this page.</footer>
</main></body></html>""" % (CSS, total, len(machines), "%d failed" % failed if failed else "all passed", cols, "".join(rows), cards, tested,
                           "".join("<li>%s</li>" % e(t) for t in limits), "".join(d for _, d in sorted(details, key=lambda x: not x[0])),
                           datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M"))
    open(os.path.join(out, "index.html"), "w").write(page)
    json.dump({"build": label, "machines": [{k: (v.isoformat() if k == "when" else v) for k, v in m.items() if k not in ("dir",)} for m in machines]},
              open(os.path.join(out, "results.json"), "w"), indent=1)
    print("%s: %d machines, %d checks, %d failed" % (os.path.join(out, "index.html"), len(machines), total, failed))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="work"); ap.add_argument("--out", default=os.path.join(HARNESS, "out", "site"))
    ap.add_argument("--results", default=os.path.join(HARNESS, "results"))
    ap.add_argument("--machines", help="machines.json: the device and image of each instance, when the instances are not on this host")
    a = ap.parse_args()
    if a.machines: MACHINES.update({m["instance"]: m for m in json.load(open(a.machines))["machines"]})
    build(a.label, a.out, a.results)
