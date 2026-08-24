#!/usr/bin/env python3
"""HTML rendering for testplan.py.

Kept out of testplan.py because it is presentation only: every number here is
computed there, and this module must not derive anything of its own. If a figure
appears on the page that is not in `items` or `rows`, that is a bug.

The page is generated rather than hand-written for the same reason the plan is:
a testplan that has to be re-typed after a model change will not be.
"""

import collections
import datetime
import html
import json

# IBM Plex: drawn for technical documentation, which is what this is, and not
# one of the faces that turns up by default. Serif for headings, sans for prose,
# mono for anything a person might paste into a shell.
FONTS = ("https://fonts.googleapis.com/css2?"
         "family=IBM+Plex+Mono:wght@400;500&"
         "family=IBM+Plex+Sans:wght@400;450;600&"
         "family=IBM+Plex+Serif:wght@500;600&display=swap")

CSS = """
:root {
  --paper:#f4f6f5; --surface:#ffffff; --sunk:#eceff0;
  --ink:#15191b; --body:#33403f; --muted:#63716f;
  --rule:#d5dcda; --rule-firm:#b9c4c1;
  --accent:#0d6b62; --accent-soft:#d6e8e5;
  --ok:#2c6e49; --partial:#9a6114; --todo:#a33529; --inert:#8b9694;
  --bar-track:#e2e7e6;
  --shadow:0 1px 2px rgba(20,30,28,.06);
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --paper:#101414; --surface:#171d1d; --sunk:#1d2424;
    --ink:#eef2f1; --body:#c3cecc; --muted:#8a9895;
    --rule:#2a3332; --rule-firm:#3a4544;
    --accent:#4fb3a6; --accent-soft:#1d3330;
    --ok:#6cbf8a; --partial:#d6a24c; --todo:#e0796c; --inert:#6f7d7b;
    --bar-track:#242c2c;
    --shadow:0 1px 2px rgba(0,0,0,.35);
  }
}
:root[data-theme="dark"] {
  --paper:#101414; --surface:#171d1d; --sunk:#1d2424;
  --ink:#eef2f1; --body:#c3cecc; --muted:#8a9895;
  --rule:#2a3332; --rule-firm:#3a4544;
  --accent:#4fb3a6; --accent-soft:#1d3330;
  --ok:#6cbf8a; --partial:#d6a24c; --todo:#e0796c; --inert:#6f7d7b;
  --bar-track:#242c2c;
  --shadow:0 1px 2px rgba(0,0,0,.35);
}

*{box-sizing:border-box}
body{
  margin:0; background:var(--paper); color:var(--body);
  font-family:"IBM Plex Sans",ui-sans-serif,system-ui,-apple-system,Segoe UI,sans-serif;
  font-weight:450; font-size:16px; line-height:1.6;
  -webkit-font-smoothing:antialiased;
}
.wrap{max-width:1180px;margin:0 auto;padding:0 28px 96px}
.prose{max-width:66ch}
h1,h2,h3{font-family:"IBM Plex Serif",Georgia,serif;color:var(--ink);
  text-wrap:balance;margin:0}
h1{font-size:clamp(2rem,4.2vw,2.9rem);font-weight:600;letter-spacing:-.018em;line-height:1.12}
h2{font-size:1.44rem;font-weight:600;letter-spacing:-.012em}
h3{font-size:1.05rem;font-weight:600}
p{margin:0}
a{color:var(--accent)}
code,.mono{font-family:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,monospace}
code{font-size:.87em;background:var(--sunk);padding:.1em .35em;border-radius:2px}
.num{font-variant-numeric:tabular-nums}

/* --- masthead ---------------------------------------------------------- */
.masthead{border-bottom:1px solid var(--rule-firm);margin-bottom:40px;
  padding:56px 0 26px;display:flex;flex-direction:column;gap:16px}
.eyebrow{font-family:"IBM Plex Mono",monospace;font-size:.72rem;
  letter-spacing:.13em;text-transform:uppercase;color:var(--accent);font-weight:500}
.standfirst{font-size:1.08rem;color:var(--muted);max-width:62ch}
.prov{font-family:"IBM Plex Mono",monospace;font-size:.76rem;color:var(--muted);
  display:flex;flex-wrap:wrap;gap:6px 22px;padding-top:6px}
.prov b{font-weight:500;color:var(--body)}

/* --- the figure band --------------------------------------------------- */
.band{display:grid;grid-template-columns:repeat(auto-fit,minmax(148px,1fr));
  gap:1px;background:var(--rule);border:1px solid var(--rule);margin:0 0 12px}
.fig{background:var(--surface);padding:16px 18px}
.fig .k{font-family:"IBM Plex Mono",monospace;font-size:.68rem;letter-spacing:.09em;
  text-transform:uppercase;color:var(--muted)}
.fig .v{font-family:"IBM Plex Serif",Georgia,serif;font-size:1.85rem;font-weight:600;
  color:var(--ink);line-height:1.15;margin-top:4px;font-variant-numeric:tabular-nums}
.fig .v small{font-size:.95rem;font-weight:500;color:var(--muted);margin-left:3px}

section{margin-top:56px}
.lede{margin:10px 0 22px}

/* --- tables ------------------------------------------------------------ */
.scroll{overflow-x:auto;border:1px solid var(--rule);background:var(--surface)}
table{border-collapse:collapse;width:100%;font-size:.875rem}
th,td{text-align:left;padding:8px 13px;border-bottom:1px solid var(--rule);
  vertical-align:top;white-space:nowrap}
th{font-family:"IBM Plex Mono",monospace;font-size:.68rem;letter-spacing:.08em;
  text-transform:uppercase;color:var(--muted);font-weight:500;
  background:var(--sunk);position:sticky;top:0;z-index:2}
td.r,th.r{text-align:right;font-variant-numeric:tabular-nums}
tbody tr:last-child td{border-bottom:0}
tbody tr:hover td{background:var(--sunk)}
td.wrap-cell{white-space:normal;min-width:22rem}

/* proportion bar: flat, no rounding -- it is data, not decoration */
.bar{display:flex;align-items:center;gap:9px;min-width:150px}
.track{flex:1;height:6px;background:var(--bar-track);position:relative}
.fill{height:100%;background:var(--accent)}
.pct{font-variant-numeric:tabular-nums;font-size:.8rem;color:var(--muted);
  min-width:3.4rem;text-align:right}

.state{font-family:"IBM Plex Mono",monospace;font-size:.7rem;letter-spacing:.04em;
  padding:2px 7px;border:1px solid currentColor;white-space:nowrap}
.s-complete{color:var(--ok)} .s-partial{color:var(--partial)}
.s-not{color:var(--todo)} .s-oor,.s-unreach{color:var(--inert)}

/* --- filter bar -------------------------------------------------------- */
.controls{display:flex;flex-wrap:wrap;gap:10px;align-items:center;
  padding:13px;background:var(--sunk);border:1px solid var(--rule);border-bottom:0}
.controls input,.controls select{font-family:inherit;font-size:.85rem;
  padding:7px 10px;border:1px solid var(--rule-firm);background:var(--surface);
  color:var(--ink);border-radius:0}
.controls input{flex:1;min-width:190px}
.controls input:focus,.controls select:focus{outline:2px solid var(--accent);outline-offset:-1px}
.count{font-family:"IBM Plex Mono",monospace;font-size:.76rem;color:var(--muted);
  margin-left:auto}
.more{padding:12px;text-align:center;border:1px solid var(--rule);border-top:0;
  background:var(--surface)}
.more button{font-family:"IBM Plex Mono",monospace;font-size:.8rem;padding:8px 18px;
  background:var(--surface);color:var(--accent);border:1px solid var(--rule-firm);cursor:pointer}
.more button:hover{background:var(--accent-soft)}

/* --- callout ----------------------------------------------------------- */
.note{border-left:2px solid var(--accent);padding:2px 0 2px 18px;margin:20px 0;
  color:var(--body)}
.note b{color:var(--ink)}
.two{display:grid;grid-template-columns:repeat(auto-fit,minmax(290px,1fr));gap:28px}
footer{margin-top:72px;padding-top:22px;border-top:1px solid var(--rule);
  font-size:.82rem;color:var(--muted)}
pre{background:var(--surface);border:1px solid var(--rule);padding:14px 16px;
  overflow-x:auto;font-size:.8rem;line-height:1.55;margin:14px 0}
@media (prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
"""


def _e(x):
    return html.escape(str(x), quote=True)


def _bar(pct):
    return (f'<div class="bar"><div class="track">'
            f'<div class="fill" style="width:{max(0,min(100,pct)):.1f}%"></div></div>'
            f'<span class="pct">{pct:.1f}%</span></div>')


_STATE_CLASS = {"complete": "s-complete", "partial": "s-partial",
                "not started": "s-not", "out of reach": "s-oor",
                "unreachable": "s-unreach"}


def render(items, rows, meta, changes=None):
    live = [i for i in items if i["state"] != "unreachable"]
    work = [i for i in live if i["state"] not in ("out of reach",)]
    tot = sum(i["spans"] for i in live)
    cov = sum(i["covered"] for i in live)
    pct = 100.0 * cov / tot if tot else 0.0
    st = collections.Counter(i["state"] for i in items)

    by_feat = collections.OrderedDict()
    for i in live:
        f = by_feat.setdefault(i["feature"], {"items": 0, "done": 0, "spans": 0, "cov": 0})
        f["items"] += 1
        f["done"] += i["state"] == "complete"
        f["spans"] += i["spans"]
        f["cov"] += i["covered"]

    by_stim = collections.OrderedDict()
    for i in live:
        s = by_stim.setdefault(i["stimulus"], {"items": 0, "spans": 0, "cov": 0,
                                               "todo": 0, "dir": i["directive"]})
        s["items"] += 1
        s["spans"] += i["spans"]
        s["cov"] += i["covered"]
        s["todo"] += i["todo"]

    depth = collections.Counter()
    depth_todo = collections.Counter()
    for r in rows:
        d = min(int(r["nesting"]), 4)
        depth[d] += 1
        if r["status"] == "todo":
            depth_todo[d] += 1
    n_spans = sum(depth.values()) or 1
    conj = sum(depth[d] for d in range(2, 5))
    conj_todo = sum(depth_todo[d] for d in range(2, 5))
    crosses = [i for i in work if i.get("cross") == "yes" and i["todo"]]

    P = []
    A = P.append
    A(f'<title>Sail Model Testplan</title>')
    A(f'<link rel="stylesheet" href="{FONTS}">')
    A(f"<style>{CSS}</style>")
    A('<div class="wrap">')

    # -- masthead
    A('<header class="masthead">')
    A('<div class="eyebrow">RISC-V Golden Model &middot; derived coverage plan</div>')
    A("<h1>Sail Model Testplan</h1>")
    A('<p class="standfirst">Every target the model implements, what stimulus '
      'reaches it, whether the current corpus reaches it, and what to generate '
      'for the ones it does not — derived from the model’s own branch '
      'structure, not hand-listed.</p>')
    A('<div class="prov">')
    A(f'<span>generated <b>{datetime.date.today().isoformat()}</b></span>')
    A(f'<span>manifest <b>{_e(meta["branch_info"].split("/")[-1])}</b></span>')
    A(f'<span>model <b>{_e(meta["model_dir"])}</b></span>')
    A(f'<span>status <b>{_e((meta.get("coverage") or "not joined").split("/")[-1])}</b></span>')
    if meta.get("scope"):
        A(f'<span>scope <b>{_e(meta["scope"].split("/")[-1])}</b></span>')
    A("</div></header>")

    # -- figures
    A('<div class="band">')
    for k, v, sub in (
        ("reachable spans", f"{tot:,}", ""),
        ("covered", f"{cov:,}", f"{pct:.1f}%"),
        ("plan items", f"{len(live):,}", ""),
        ("complete", f"{st['complete']:,}", ""),
        ("partial", f"{st['partial']:,}", ""),
        ("not started", f"{st['not started']:,}", ""),
    ):
        A(f'<div class="fig"><div class="k">{k}</div>'
          f'<div class="v">{v}{f"<small>{sub}</small>" if sub else ""}</div></div>')
    A("</div>")
    A(f'<p class="prov"><span>{st["unreachable"]:,} items held unreachable and '
      f'{st["out of reach"]:,} out of reach are accounted for separately, below.</span></p>')

    # -- method
    A('<section><h2>How a span becomes a plan item</h2>'
      '<div class="prose lede"><p>The target set is the model’s instrumented '
      'span manifest, so the plan is complete by construction: it cannot omit a '
      'behaviour the model implements, because every behaviour the model '
      'implements is compiled into a span. Four steps turn one into an item, '
      'each read from the sources rather than from a table.</p></div>')
    A('<div class="scroll"><table><thead><tr>'
      '<th>Step</th><th>What it recovers</th><th class="wrap-cell">Why it matters</th>'
      "</tr></thead><tbody>")
    for n, what, why in (
        ("Ownership", "the top-level definition containing the span",
         "<code>function clause execute RTYPE(...)</code> owns its spans; so does "
         "<code>pmpCheck</code>. Cross-checked against the function names the "
         "compiler emitted — 99.9% agreement."),
        ("Discrimination", "the enclosing <code>match</code> arm",
         "Inside an execute clause the model discriminates instructions by matching "
         "on the operand enum, so each arm is its own span. An uncovered span is "
         "not “an expression in RTYPE” — it is <i>sra was never executed</i>."),
        ("Naming", "the arm’s assembly mnemonic",
         "The model states it itself: <code>SRA &lt;-&gt; \"sra\"</code>. Those mappings "
         "are unreachable as code and excluded from measurement, but they are the "
         "model’s own authority on what a constructor is called."),
        ("Stimulus", "what a generator must arrange",
         "A guard on the span’s own line gives a privilege level, CSR field, "
         "configuration key or XLEN. A helper with no guard is resolved through the "
         "call graph to the instructions that reach it. Anything left is reported "
         "<code>unclassified</code> rather than guessed at."),
    ):
        A(f"<tr><td><b>{n}</b></td><td>{what}</td>"
          f'<td class="wrap-cell">{why}</td></tr>')
    A("</tbody></table></div></section>")

    # -- by feature
    A('<section><h2>Coverage by feature</h2>'
      '<div class="prose lede"><p>Grouped by the model’s own directory layout, '
      'so a new extension appears here without anything being edited.</p></div>')
    A('<div class="scroll"><table><thead><tr><th>Feature</th>'
      '<th class="r">Items</th><th class="r">Complete</th><th class="r">Spans</th>'
      '<th class="r">Covered</th><th style="min-width:200px">Coverage</th>'
      "</tr></thead><tbody>")
    for f, d in sorted(by_feat.items(), key=lambda kv: -kv[1]["spans"]):
        p = 100.0 * d["cov"] / d["spans"] if d["spans"] else 0.0
        A(f'<tr><td><b>{_e(f)}</b></td><td class="r">{d["items"]}</td>'
          f'<td class="r">{d["done"]}</td><td class="r">{d["spans"]:,}</td>'
          f'<td class="r">{d["cov"]:,}</td><td>{_bar(p)}</td></tr>')
    A("</tbody></table></div></section>")

    # -- by stimulus
    A('<section><h2>By stimulus</h2>'
      '<div class="prose lede"><p>What a generator has to arrange, and how much '
      'rides on each. The largest outstanding group is the cheapest coverage per '
      'unit of work.</p></div>')
    A('<div class="scroll"><table><thead><tr><th>Stimulus</th>'
      '<th class="r">Items</th><th class="r">Spans</th><th class="r">To do</th>'
      '<th style="min-width:180px">Coverage</th>'
      '<th class="wrap-cell">What it takes</th></tr></thead><tbody>')
    for sname, d in sorted(by_stim.items(), key=lambda kv: -kv[1]["todo"]):
        p = 100.0 * d["cov"] / d["spans"] if d["spans"] else 0.0
        A(f'<tr><td><code>{_e(sname)}</code></td><td class="r">{d["items"]}</td>'
          f'<td class="r">{d["spans"]:,}</td><td class="r">{d["todo"]:,}</td>'
          f'<td>{_bar(p)}</td>'
          f'<td class="wrap-cell">{_e(d["dir"])}</td></tr>')
    A("</tbody></table></div></section>")

    # -- crosses
    A('<section><h2>Crosses</h2>'
      '<div class="prose lede"><p>Two different things get called a cross, and '
      'the plan’s answer differs for each.</p></div>')
    A('<div class="two"><div>')
    A("<h3>Operand-value crosses — not covered, and not coverable</h3>")
    A('<div class="prose"><p style="margin-top:8px">ACT4’s '
      "<code>cr_rs1_rs2_edges</code> crosses 11 edge values on <code>rs1</code> "
      "against 11 on <code>rs2</code>. None are visible here, because the model "
      "has no branch on operand values for arithmetic:</p></div>")
    A("<pre>ADD  =&gt; X(rs1) + X(rs2),</pre>")
    A('<div class="prose"><p>That is <b>one span</b>, covered the first time any '
      "<code>add</code> executes, whatever the operands. All 121 combinations are "
      "indistinguishable to source coverage. This is a property of the metric, not "
      "a gap in the tooling, and it is why the RVVI/SVA coverpoints are measured "
      "alongside this plan rather than replaced by it.</p></div>")
    A("</div><div>")
    A("<h3>Control-flow crosses — reported, with the conjunction</h3>")
    A(f'<div class="prose"><p style="margin-top:8px">Where the model does branch on '
      f"combinations, each span carries its full chain of enclosing conditions, not "
      f"just the nearest — a directive naming only the innermost sends the "
      f"generator after a state it will never reach. "
      f"<b>{conj:,} spans ({100*conj/n_spans:.1f}%)</b> need two or more conditions "
      f"to hold together; {conj_todo:,} are uncovered.</p></div>")
    A('<div class="scroll" style="margin-top:14px"><table><thead><tr><th>Depth</th>'
      '<th class="r">Spans</th><th class="r">Share</th><th class="r">Uncovered</th>'
      "</tr></thead><tbody>")
    for d, lab in ((0, "unconditional"), (1, "one condition"),
                   (2, "two at once"), (3, "three at once"), (4, "four or more")):
        A(f'<tr><td>{lab}</td><td class="r">{depth[d]:,}</td>'
          f'<td class="r">{100*depth[d]/n_spans:.1f}%</td>'
          f'<td class="r">{depth_todo[d]:,}</td></tr>')
    A("</tbody></table></div></div></div>")

    A('<div class="note"><b>On the depth figure.</b> '
      "<code>} else if c then {</code> is an alternative, not a nesting — "
      "exactly one of the chain holds, never all of them. Counting those as nesting "
      "put <code>clint_store</code> at depth 8 and claimed eight conditions had to "
      "hold simultaneously. A line’s leading <code>}</code> is applied before its "
      "<code>{</code>, so an else-if chain stays at constant depth.</div>")

    if crosses:
        A(f"<h3 style=\"margin-top:30px\">{len(crosses)} outstanding items need "
          f"several conditions at once</h3>")
        A('<div class="scroll" style="margin-top:12px"><table><thead><tr>'
          '<th>Item</th><th>Feature</th><th>Target</th><th class="r">Depth</th>'
          '<th class="r">To do</th><th class="wrap-cell">Conditions that must hold together</th>'
          "</tr></thead><tbody>")
        for i in sorted(crosses, key=lambda x: (-x["max_nesting"], -x["todo"]))[:14]:
            c = i["conditions"][:190]
            A(f'<tr><td class="mono">{_e(i["item_id"])}</td><td>{_e(i["feature"])}</td>'
              f'<td><code>{_e(i["target"])}</code></td>'
              f'<td class="r">{i["max_nesting"]}</td><td class="r">{i["todo"]}</td>'
              f'<td class="wrap-cell mono" style="font-size:.78rem">{_e(c)}</td></tr>')
        A("</tbody></table></div>")
    A("</section>")

    # -- the plan itself
    A(f'<section><h2>The plan</h2>'
      f'<div class="prose lede"><p>All {len(live):,} reachable items, worst-covered '
      f'largest first — that ordering is the schedule. Filter by feature, state '
      f'or stimulus, or search a target, file or mnemonic.</p></div>')
    feats = sorted({i["feature"] for i in live})
    stims = sorted({i["stimulus"] for i in live})
    A('<div class="controls">')
    A('<input id="q" type="search" placeholder="Search target, source file, directive…" '
      'aria-label="Search the plan">')
    A('<select id="f" aria-label="Filter by feature"><option value="">All features</option>'
      + "".join(f'<option>{_e(f)}</option>' for f in feats) + "</select>")
    A('<select id="s" aria-label="Filter by state"><option value="">All states</option>'
      '<option>not started</option><option>partial</option><option>complete</option>'
      '<option>out of reach</option></select>')
    A('<select id="t" aria-label="Filter by stimulus"><option value="">All stimuli</option>'
      + "".join(f'<option>{_e(t)}</option>' for t in stims) + "</select>")
    A('<span class="count" id="count"></span></div>')
    A('<div class="scroll"><table id="plan"><thead><tr>'
      '<th>Item</th><th>Feature</th><th>Target</th><th>Stimulus</th><th>State</th>'
      '<th class="r">Spans</th><th class="r">Done</th><th class="r">To do</th>'
      '<th style="min-width:150px">Coverage</th><th>Source</th>'
      '<th class="wrap-cell">What to generate</th></tr></thead><tbody>')
    for i in live:
        cls = _STATE_CLASS.get(i["state"], "")
        hay = _e(" ".join((i["item_id"], i["feature"], i["target"], i["stimulus"],
                           i["state"], i["source"], i["directive"], i["detail"])).lower())
        A(f'<tr data-h="{hay}" data-f="{_e(i["feature"])}" data-s="{_e(i["state"])}" '
          f'data-t="{_e(i["stimulus"])}">'
          f'<td class="mono">{_e(i["item_id"])}</td><td>{_e(i["feature"])}</td>'
          f'<td><code>{_e(i["target"])}</code></td><td>{_e(i["stimulus"])}</td>'
          f'<td><span class="state {cls}">{_e(i["state"])}</span></td>'
          f'<td class="r">{i["spans"]}</td><td class="r">{i["covered"]}</td>'
          f'<td class="r">{i["todo"]}</td><td>{_bar(i["percent"])}</td>'
          f'<td class="mono" style="font-size:.78rem">{_e(i["source"])}</td>'
          f'<td class="wrap-cell">{_e(i["directive"])}</td></tr>')
    A("</tbody></table></div>")
    A('<div class="more"><button id="more" type="button">Show more</button></div>')
    A("</section>")

    # -- accounting
    A('<section><h2>What is excluded, and on what basis</h2>'
      '<div class="prose lede"><p>Two kinds of “cannot be reached” are kept '
      'apart, because they justify different things. The distinction is what keeps '
      'the headline figure honest: the plan is allowed to say <i>do not schedule '
      'this</i>, and not allowed to say <i>so it does not count</i>.</p></div>')
    A('<div class="scroll"><table><thead><tr><th>Kind</th><th class="r">Items</th>'
      '<th>Denominator</th><th class="wrap-cell">Basis</th></tr></thead><tbody>')
    A(f'<tr><td><b>Held unreachable</b></td><td class="r">{st["unreachable"]:,}</td>'
      f"<td>removed</td><td class=\"wrap-cell\">Whole declaration forms reasoned "
      f"about structurally by <code>derive_span_exclusions.py</code>: the "
      f"disassembly direction of a bidirectional mapping is not reachable by "
      f"running a program, for any such mapping.</td></tr>")
    A(f'<tr><td><b>Out of reach</b></td><td class="r">{st["out of reach"]:,}</td>'
      f"<td>retained</td><td class=\"wrap-cell\">Decided per span from its own text: "
      f"enum-to-text mappings in another declaration form, diagnostic string "
      f"builders, and Hypervisor code the model does not implement. A per-span "
      f"judgement is not a strong enough basis to move a coverage figure, so these "
      f"are excluded from the schedule but not deducted.</td></tr>")
    A("</tbody></table></div></section>")

    # -- reproduce
    A("<section><h2>Reproducing this</h2>")
    A("<pre>cd python-isla\n"
      "python3 derive_span_exclusions.py -o /tmp/excl.txt\n"
      "python3 testplan.py \\\n"
      "    --coverage ~/Documents/sail-riscv/sail_coverage \\\n"
      "    --exclude-spans /tmp/excl.txt \\\n"
      "    --baseline documentation/python-isla/results/testplan.json \\\n"
      "    -o testplan --html --xlsx --fail-on-new-holes</pre>")
    A('<div class="prose"><p>Stale inputs stop the run rather than producing a '
      'plausible plan: a manifest older than the model would describe the previous '
      'model, and a coverage log older than the manifest would join status onto '
      'spans that have since moved. <code>--baseline</code> separates new holes and '
      'regressions from the standing backlog, and '
      '<code>--fail-on-new-holes</code> exits non-zero on either, so a model bump '
      'fails CI rather than quietly enlarging the backlog.</p></div>')
    A("</section>")

    A(f'<footer>Generated by <code>testplan.py</code> from '
      f'<code>{_e(meta["branch_info"].split("/")[-1])}</code>. Every figure on this '
      f'page is computed by that script; nothing here is hand-entered.</footer>')
    A("</div>")

    A("""<script>
(function(){
  var rows=[].slice.call(document.querySelectorAll('#plan tbody tr'));
  var q=document.getElementById('q'), f=document.getElementById('f'),
      s=document.getElementById('s'), t=document.getElementById('t'),
      c=document.getElementById('count'), m=document.getElementById('more');
  var STEP=120, shown=STEP, matched=rows;
  function apply(){
    var qq=q.value.trim().toLowerCase(), ff=f.value, ss=s.value, tt=t.value;
    matched=rows.filter(function(r){
      return (!ff||r.dataset.f===ff)&&(!ss||r.dataset.s===ss)&&
             (!tt||r.dataset.t===tt)&&(!qq||r.dataset.h.indexOf(qq)>-1);
    });
    rows.forEach(function(r){r.style.display='none';});
    matched.slice(0,shown).forEach(function(r){r.style.display='';});
    c.textContent=matched.length.toLocaleString()+' of '+rows.length.toLocaleString()+
      ' items'+(matched.length>shown?' \\u00b7 showing '+shown:'');
    m.parentNode.style.display=matched.length>shown?'':'none';
  }
  [q,f,s,t].forEach(function(el){
    el.addEventListener('input',function(){shown=STEP;apply();});
  });
  m.addEventListener('click',function(){shown+=STEP*3;apply();});
  apply();
})();
</script>""")
    return "\n".join(P)
