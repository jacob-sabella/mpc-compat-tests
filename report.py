#!/usr/bin/env python3
"""report.py [--ledger DIR] [--out DIR] [--pm REF] [--mpc-vst REF]: the compatibility page, made from the ledger (ledger.py).

One static folder (index.html, results.json and the screen captures), to be published as it is. Rows are the scenarios, columns the
machines in machines.json. A cell is the newest result for that scenario on that machine, however long ago and from whichever run:
passed, failed, or not run, with how old it is, the last few results before it, and whether it ran the code under test (--pm and
--mpc-vst: a commit, or owner/repo@ref looked up with git ls-remote; by default the commits of the newest result that names them).
A result from other code is shown, marked as other code: it is still the best that is known about that cell.

The page is public: it shows what was checked, what failed and what was on screen, and none of the device's log files. Nothing here
needs the emulator or the firmware: only the ledger, machines.json and the harness's scenario lists (emu/)."""
import argparse, datetime, html, json, os, re, shutil, subprocess

import ledger as L

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.environ.get("HARNESS") or os.path.join(HERE, "emu")                     # the harness: scenarios.py, sample_scenarios.py, routes.py

DEVICES = {"ACVA": "MPC One", "ACVA2": "MPC One+", "ACVA3": "MPC One (Gen 2)", "ACV5": "MPC X", "ACV5S": "MPC X SE", "ACV8": "MPC Live",
           "ACVB": "MPC Live II", "ACVG": "MPC Live III", "ACVC": "MPC XL", "ACVM": "MPC Key 61", "ACVM2": "MPC Key 61 (Gen 2)",
           "ACVR": "MPC Key 37", "ACVR2": "MPC Key 37 (Gen 2)", "ADA2": "Force", "ADA2S": "Force (Gen 2)",
           "AC50": "MPC Sample"}

# What a pass does not mean. (scenario, test on the record) -> the note shown in the cell.
PARTIAL = [
    ("vst2_plugin_json", lambda r: not r.get("external_plugins"),
     "This firmware has no VST2 Plugins folder. Only what the Manager writes is checked, not MPC loading from it."),
]
HISTORY = 8                         # results shown in a cell's strip, newest last


def ver(v): return tuple(int(x) for x in re.split(r"[._]", v or "") if x.isdigit())


def os_name(code, firmware):
    """The firmware's own name: the MPC Sample runs MPC Sample 1.x, not MPC OS."""
    return ("MPC Sample %s" if code == "AC50" else "MPC OS %s") % firmware


def resolve(ref):
    """A commit as it is, or owner/repo@ref (a branch or tag) looked up on GitHub."""
    if not ref or re.fullmatch(r"[0-9a-f]{7,40}", ref): return ref
    repo, _, name = ref.partition("@")
    try: out = subprocess.run(["git", "ls-remote", "https://github.com/%s" % repo, name], capture_output=True, text=True, check=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError) as e: raise SystemExit("%s: %s" % (ref, e))
    shas = [l.split()[0] for l in out.splitlines() if l.split()[1] in (name, "refs/heads/" + name, "refs/tags/" + name, "refs/tags/%s^{}" % name)]
    if not shas: raise SystemExit("%s: no such branch or tag" % ref)
    return shas[-1]


def harness_scenarios():
    """The scenarios each kind of machine runs, in the order they are written; None when the harness is not here."""
    try:
        read = lambda f, deco: re.findall(r"^@%s\ndef (\w+)" % deco, open(os.path.join(HARNESS, f)).read(), re.M)
        routes = read("routes.py", "route")
        return {"manager": read("scenarios.py", "scenario") + routes, "sample": read("sample_scenarios.py", "sample_scenario") + routes}
    except OSError: return None


def age(at, now):
    s = (now - datetime.datetime.strptime(at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)).total_seconds()
    return "<1h" if s < 3600 else "%dh" % (s // 3600) if s < 172800 else "%dd" % (s // 86400) if s < 1209600 else "%dw" % (s // 604800)


def short(sha): return (sha or "?")[:7]


def commit_link(repo, sha):
    e = html.escape
    if not sha: return "<code>unknown</code>"
    return '<a href="https://github.com/%s/commit/%s"><code>%s</code></a>' % (e(repo), e(sha), e(short(sha))) if repo else "<code>%s</code>" % e(short(sha))


CSS = """
:root{--bg:#f7f7f5;--panel:#fff;--ink:#1c1d1f;--mute:#62666d;--line:#dcdcd6;--pass:#1a7f4b;--pass-bg:#e3f4ea;--fail:#b3261e;--fail-bg:#fbe6e4;
--none:#8a8d93;--none-bg:#eeeeea;--part:#8a5a00;--part-bg:#fbf0d6;--link:#1f5fbf}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#141516;--panel:#1d1f21;--ink:#ececec;--mute:#a0a4ab;--line:#33363a;--pass:#5fd08f;
--pass-bg:#153324;--fail:#ff8a80;--fail-bg:#3b1a18;--none:#8d9197;--none-bg:#26282b;--part:#f0c060;--part-bg:#3a2e12;--link:#8ab4ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1180px;margin:0 auto;padding:28px 16px 56px}h1{font-size:26px;margin:0 0 6px;letter-spacing:-.01em}
h2{font-size:18px;margin:36px 0 10px}p{margin:0 0 10px}.lede{color:var(--mute);max-width:70ch}a{color:var(--link)}
.stats{display:flex;flex-wrap:wrap;gap:10px;margin:18px 0}.stat{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:8px 14px;flex:1 1 150px}
.stat b{display:block;font-size:22px;font-variant-numeric:tabular-nums}.stat span{color:var(--mute);font-size:13px}.stat small{display:block;color:var(--mute);font-size:12px}
.wrap{overflow-x:auto;background:var(--panel);border:1px solid var(--line);border-radius:10px}
table{border-collapse:collapse;width:100%;min-width:640px}th,td{padding:9px 12px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
tr:last-child td{border-bottom:0}thead th{font-weight:600;font-size:13px;white-space:nowrap;background:var(--panel);position:sticky;top:0}
thead small{display:block;font-weight:400;color:var(--mute)}td.s{min-width:240px}td.s b{font-weight:600}td.s span{display:block;color:var(--mute);font-size:13px}
.c{white-space:nowrap}.pill{display:inline-block;padding:2px 9px;border-radius:999px;font-size:13px;font-weight:600;text-decoration:none;border:1px solid transparent}
.pass{color:var(--pass);background:var(--pass-bg)}.fail{color:var(--fail);background:var(--fail-bg)}.none{color:var(--none);background:var(--none-bg)}
.part{color:var(--part);background:var(--part-bg)}.t{color:var(--mute);font-size:12px;margin-left:6px;font-variant-numeric:tabular-nums}
.old .pill{background:transparent;border-style:dashed;border-color:currentColor}.flag{display:block;font-size:12px;color:var(--mute);margin-top:3px}
.hist{display:flex;gap:2px;margin-top:5px}.hist i{display:block;width:7px;height:10px;border-radius:2px}.hist i.p{background:var(--pass)}.hist i.f{background:var(--fail)}
.hist i.o{opacity:.35}
.legend{display:flex;flex-wrap:wrap;gap:8px 18px;margin:12px 0 0;color:var(--mute);font-size:13px}.legend .old{display:inline}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(270px,1fr));gap:12px}.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.card h3{margin:0 0 6px;font-size:15px}.card dl{margin:0;display:grid;grid-template-columns:auto 1fr;gap:2px 10px;font-size:13px}.card dt{color:var(--mute)}
.card dd{margin:0;overflow-wrap:anywhere}code{font:12px ui-monospace,Menlo,Consolas,monospace}
details{background:var(--panel);border:1px solid var(--line);border-radius:10px;margin:0 0 8px;padding:0 14px}summary{cursor:pointer;padding:10px 0;font-weight:600}
details ul{margin:0 0 10px;padding-left:20px}.shots{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 14px}.shots a{display:block;width:230px;max-width:100%}
.shots img{width:100%;border:1px solid var(--line);border-radius:6px;display:block}.shots span{font-size:12px;color:var(--mute)}
table.runs{min-width:0;font-size:13px;margin:0 0 14px}table.runs td{padding:3px 10px 3px 0;border:0}
ul.plain{padding-left:20px;max-width:80ch}footer{margin-top:40px;color:var(--mute);font-size:13px}
"""

# Ages are written when the page is made, and brought up to date when it is read.
JS = """<script>(function(){var n=Date.now();document.querySelectorAll("time[datetime]").forEach(function(t){var s=(n-Date.parse(t.getAttribute("datetime")))/1e3;
if(!(s>=0))return;t.textContent=s<3600?"<1h":s<172800?Math.floor(s/3600)+"h":s<1209600?Math.floor(s/86400)+"d":Math.floor(s/604800)+"w";});})();</script>"""


def build(a):
    e = html.escape
    now = datetime.datetime.now(datetime.timezone.utc)
    recs = L.load(a.ledger)
    machines = json.load(open(a.machines))["machines"]
    known = harness_scenarios()

    # the code under test: given, or that of the newest result that names it
    target = {"pm": resolve(a.pm), "mpc_vst": resolve(a.mpc_vst)}
    for k in target:
        if not target[k]: target[k] = next((r[k] for r in reversed(recs) if r.get(k)), None)
    repo = {k: a.__dict__.get(k + "_repo") or next((r.get(k + "_repo") for r in reversed(recs) if r.get(k) == target[k] and r.get(k + "_repo")), None)
            for k in target}
    def current(r):
        """True: ran the code under test; False: other code; None: its commits are not known (results from before the ledger)."""
        want = [k for k, v in target.items() if v]
        if any(r.get(k) and r[k] != target[k] for k in want): return False
        return True if all(r.get(k) for k in want) else None

    cells = {}
    for r in recs: cells.setdefault((r["instance"], r["scenario"]), []).append(r)

    cols = []
    for m in machines:
        mine = [r for r in recs if r["instance"] == m["instance"]]
        last = mine[-1] if mine else {}
        fw = last.get("firmware") or (re.search(r"\d+\.\d+(?:\.\d+)?", m["image"]) or [None])[0] or "?"
        cols.append(dict(m, firmware=fw, arch=last.get("arch") or "?", device=DEVICES.get(m["code"], m["code"]), last=last,
                         kind=m.get("kind", "manager")))
    cols.sort(key=lambda m: (m["kind"] == "sample", ver(m["firmware"]), m["arch"] != "armv7", m["device"]))

    names = []
    if known:
        for kind in ("manager", "sample"):                  # the Manager's own first, then what only an MPC Sample runs
            if any(m["kind"] == kind for m in cols): names += [n for n in known[kind] if n not in names]
    else:
        names = sorted({s for _, s in cells})
    applies = lambda m, n: n in known[m["kind"]] if known else (m["instance"], n) in cells
    docs = {}
    for r in recs: docs[r["scenario"]] = r.get("doc") or docs.get(r["scenario"], "")

    shutil.rmtree(a.out, ignore_errors=True); os.makedirs(a.out)
    notes = lambda r: [t for s, test, t in PARTIAL if s == r["scenario"] and test(r)]
    word = lambda r: ("fail", "failed") if not r["ok"] else ("part", "passed*") if notes(r) or r.get("retried") else ("pass", "passed")

    stats = dict(cells=0, passed=0, failed=0, none=0, old=0, unknown=0, flaky=0)
    oldest = None
    head = "".join("<th>%s<small>%s · %s</small></th>" % (e(m["device"]), e(os_name(m["code"], m["firmware"])), e(m["arch"])) for m in cols)
    rows, details, out_cells = [], [], []
    for n in names:
        tds = []
        for m in cols:
            if not applies(m, n): tds.append('<td class="c"></td>'); continue
            stats["cells"] += 1
            hist = cells.get((m["instance"], n), [])
            if not hist:
                stats["none"] += 1; tds.append('<td class="c"><span class="pill none">not run</span></td>')
                out_cells.append({"instance": m["instance"], "scenario": n, "ok": None}); continue
            r = hist[-1]; cur = current(r); aid = "%s-%s" % (m["instance"], n)
            same = [h for h in hist if h.get("pm") and all(h.get(k) == r.get(k) for k in target)]
            flaky = len({h["ok"] for h in same}) > 1
            kind, w = word(r)
            stats["passed" if r["ok"] else "failed"] += 1; stats["old"] += cur is False; stats["unknown"] += cur is None; stats["flaky"] += flaky
            if cur and (oldest is None or r["at"] < oldest): oldest = r["at"]
            flags = (["other code"] if cur is False else ["commit unknown"] if cur is None else []) + (["flaky"] if flaky else [])
            strip = "".join('<i class="%s%s" title="%s · %s · Manager %s"></i>' % ("p" if h["ok"] else "f", "" if current(h) else " o", e(h["at"][:16].replace("T", " ")),
                            "passed" if h["ok"] else "failed", e(short(h.get("pm")))) for h in hist[-HISTORY:])
            tds.append('<td class="c%s"><a class="pill %s" href="#%s">%s</a><span class="t"><time datetime="%s">%s</time></span>%s<span class="hist">%s</span></td>' %
                       ("" if cur else " old", kind, aid, w, e(r["at"]), age(r["at"], now),
                        '<span class="flag">%s</span>' % " · ".join(flags) if flags else "", strip))
            out_cells.append({"instance": m["instance"], "scenario": n, "ok": r["ok"], "at": r["at"], "pm": r.get("pm"), "mpc_vst": r.get("mpc_vst"),
                              "current": cur, "flaky": flaky, "run": r["run"]})

            shots = []
            for s in r.get("shots", []):
                src = os.path.join(a.ledger, "shots", r["run"], m["instance"], n, s)
                if not os.path.exists(src): continue
                rel = os.path.join("shots", m["instance"], n, s)
                os.makedirs(os.path.dirname(os.path.join(a.out, rel)), exist_ok=True); shutil.copyfile(src, os.path.join(a.out, rel))
                shots.append('<a href="%s"><img loading="lazy" src="%s" alt="MPC screen: %s"><span>%s</span></a>' % (e(rel), e(rel), e(s), e(os.path.splitext(s)[0])))
            body = ""
            if r["problems"]: body += "<ul>%s</ul>" % "".join("<li>%s</li>" % e(p) for p in r["problems"])
            for t in notes(r): body += "<p>* %s</p>" % e(t)
            if r.get("findings"): body += "<p>Found (not failed):</p><ul>%s</ul>" % "".join("<li>%s</li>" % e(f) for f in r["findings"])
            if shots: body += '<div class="shots">%s</div>' % "".join(shots)
            body += '<table class="runs">%s</table>' % "".join(
                "<tr><td>%s UTC</td><td>%s</td><td>Manager %s, installers %s</td><td>%s</td></tr>" %
                (e(h["at"][:16].replace("T", " ")), "passed" if h["ok"] else "failed", commit_link(h.get("pm_repo"), h.get("pm")),
                 commit_link(h.get("mpc_vst_repo"), h.get("mpc_vst")), '<a href="%s">run</a>' % e(h["url"]) if h.get("url") else "")
                for h in reversed(hist[-HISTORY:]))
            details.append((not r["ok"], '<details id="%s"%s><summary><span class="pill %s">%s</span> %s · %s, %s%s</summary>%s</details>' %
                            (aid, "" if r["ok"] else " open", kind, w, e(n), e(m["device"]), e(os_name(m["code"], m["firmware"])),
                             " · other code" if cur is False else " · commit unknown" if cur is None else "", body)))
        rows.append('<tr><td class="s"><b>%s</b><span>%s</span></td>%s</tr>' %
                    (e(n.replace("_", " ")), e(docs.get(n, "").rstrip(":").split(". ")[0].rstrip(".") + "."), "".join(tds)))

    cards = []
    for m in cols:
        mine = [c for c in out_cells if c["instance"] == m["instance"]]
        last = m["last"]
        cards.append('<div class="card"><h3>%s · %s</h3><dl><dt>CPU</dt><dd>%s</dd><dt>Scenarios</dt><dd>%d of %d on the code under test, %d failed</dd>'
                     '<dt>Last result</dt><dd>%s</dd><dt>Image</dt><dd>%s</dd><dt>sha256</dt><dd><code>%s</code></dd></dl></div>' %
                     (e(m["device"]), e(os_name(m["code"], m["firmware"])), e(m["arch"]), sum(1 for c in mine if c.get("current") is True), len(mine),
                      sum(1 for c in mine if c["ok"] is False), e(last["at"][:16].replace("T", " ") + " UTC") if last else "none yet",
                      e(m["image"]), e(m["sha256"])))
    runs = sorted({r["run"] for r in recs})
    span = "%s to %s" % (recs[0]["at"][:10], recs[-1]["at"][:10]) if recs else "none yet"
    tested = "".join("<li>%s: %s</li>" % (name, commit_link(repo[k], target[k])) for name, k in
                     (("Plugin Manager", "pm"), ("mpc-vst-plugins (installers, wrapper)", "mpc_vst")))
    limits = ["MPC runs in an emulator (QEngine on QEMU), not on hardware: drives are disk images, there is no audio path, pads or Wi-Fi.",
              "A pass says the Manager did the right thing inside that firmware. It does not say an installed plugin makes sound.",
              "A cell is its newest result. One marked other code ran a commit other than the one under test, and commit unknown one from before commits were recorded; neither has been run again since.",
              "A device or firmware that is not a column here was not run, which is not the same as not working."]
    stat = lambda v, t, sub="": '<div class="stat"><b>%s</b><span>%s</span>%s</div>' % (e(str(v)), e(t), "<small>%s</small>" % e(sub) if sub else "")
    page = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Plugin Manager compatibility</title><style>%s</style></head><body><main>
<h1>Plugin Manager compatibility</h1>
<p class="lede">The OpenMPC Plugin Manager, run inside Akai's own MPC firmware under emulation. Each cell is the newest result for that scenario on that machine, from %d runs (%s); scenarios are run a few at a time, the ones without a result on the code under test first.</p>
<div class="stats">%s</div>
<div class="wrap"><table><thead><tr><th>Scenario</th>%s</tr></thead><tbody>%s</tbody></table></div>
<div class="legend"><span><span class="pill pass">passed</span> every check held</span><span><span class="pill part">passed*</span> passed, with a limit on what that covers</span>
<span><span class="pill fail">failed</span> see the details below</span><span class="old"><span class="pill pass">passed</span> other code, or a commit not recorded: not run on the code under test yet</span>
<span><span class="pill none">not run</span> no result for this machine</span><span>the time is how old the result is; the bars are the last results, newest on the right</span></div>
<h2>What was tested</h2><ul class="plain">%s</ul><div class="cards">%s</div>
<h2>What this does not cover</h2><ul class="plain">%s</ul>
<h2>Details</h2>%s
<footer>Made by report.py on %s UTC. Emulation by <a href="https://github.com/Peyton-C/QEngine">QEngine</a>, by <a href="https://github.com/Peyton-C">Peyton Cashin (Peyton-C)</a> and <a href="https://github.com/Peyton-C/QEngine/graphs/contributors">contributors</a>. The firmware is Akai's and is not distributed with these tests or this page.</footer>
</main>%s</body></html>""" % (
        CSS, len(runs), span,
        stat("%d / %d" % (stats["passed"], stats["cells"]), "passed", "%d not run" % stats["none"] if stats["none"] else "")
        + stat(stats["failed"], "failed") + stat(stats["flaky"], "flaky", "passed and failed on the same code")
        + stat("%d / %d" % (stats["cells"] - stats["none"] - stats["old"] - stats["unknown"], stats["cells"]), "on the code under test",
               ", ".join(t for t in ("%d other code" % stats["old"] if stats["old"] else "", "%d commit unknown" % stats["unknown"] if stats["unknown"] else "") if t))
        + stat(age(oldest, now) if oldest else "–", "oldest result", "on the code under test"),
        head, "".join(rows), tested, "".join(cards), "".join("<li>%s</li>" % e(t) for t in limits),
        "".join(d for _, d in sorted(details, key=lambda x: not x[0])) or "<p>No results yet.</p>", now.strftime("%Y-%m-%d %H:%M"), JS)
    open(os.path.join(a.out, "index.html"), "w").write(page)
    open(os.path.join(a.out, ".nojekyll"), "w").close()                  # Pages serves the folder as it is
    json.dump({"made": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "target": target, "cells": out_cells}, open(os.path.join(a.out, "results.json"), "w"), indent=1)
    print("%s: %d cells, %d passed, %d failed, %d not run, %d on other code, %d commit unknown, %d flaky" %
          (os.path.join(a.out, "index.html"), stats["cells"], stats["passed"], stats["failed"], stats["none"], stats["old"], stats["unknown"], stats["flaky"]))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ledger", default=L.LEDGER)
    ap.add_argument("--machines", default=os.path.join(HERE, "machines.json"))
    ap.add_argument("--out", default=os.path.join(HERE, "out", "site"))
    ap.add_argument("--pm", help="the Plugin Manager under test: a commit, or owner/repo@ref")
    ap.add_argument("--mpc-vst", help="the installers under test: a commit, or owner/repo@ref")
    ap.add_argument("--pm-repo", help="owner/repo, for commit links (default: from the records)")
    ap.add_argument("--mpc-vst-repo", help="owner/repo, for commit links (default: from the records)")
    build(ap.parse_args())
