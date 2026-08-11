#!/usr/bin/env python3
"""Regression status: what got worse, what got better, since the last baseline.

A raw pass/fail count cannot answer "did anything regress", and on this project
it is actively misleading in both directions:

  * Most failures are *expected*. 1257 of the isla sweep's 1285 failures are
    instructions routed to the oracle because isla cannot execute them, and a
    handful more are known findings (self-updating CSRs, PMP reset mismatch,
    the harness-hazard registers). A run that reports those as failures every
    time trains everyone to ignore the number.

  * A PASS is not automatically good. Before `--enable-fp`/`--enable-vector`
    were fixed, 47.7% of the corpus passed while checking nothing. An
    instruction moving from *verified* to *passes-but-checks-nothing* is a
    serious regression that a pass/fail count records as no change at all.

So the unit of comparison is the per-instruction *status* from
coverage_matrix.py -- verified-by-isla, verified-by-oracle, stateless,
vacuous, uncovered -- plus the coverage percentages. A regression is a status
getting worse, and the ranking below is what "worse" means.

Usage:
    python3 regression.py                 # compare against the baseline
    python3 regression.py --update        # accept current state as the baseline
    python3 regression.py --json out.json # machine-readable, for CI

Exit code is 1 if anything regressed, so this drops straight into CI.
"""
import argparse
import datetime
import json
import os
import subprocess
import sys

import coverage_matrix

HERE = os.path.dirname(os.path.abspath(__file__))
# In the repo, not the cache: a baseline is only useful if it is reviewable and
# travels with the code. A change to it should show up in a diff, so that
# "we accepted this regression" is a visible decision rather than a silent one.
BASELINE = os.path.join(HERE, "..", "documentation", "python-isla", "results",
                        "baseline.json")

# Higher is better. Comparing these is what distinguishes a regression from a
# rearrangement: an instruction moving isla -> oracle is not a regression (it
# is still verified, just by the other generator), but verified -> vacuous is.
RANK = {
    "isla": 4,
    "oracle": 4,
    "stateless": 3,     # no architecturally observable result; correct as-is
    "skip": 2,          # not applicable at this XLEN
    "vacuous": 1,       # passes while checking nothing
    "uncovered": 0,
}


def current_state():
    """Per-instruction status + coverage percentages, right now."""
    verdict, vacuous, ext_of = coverage_matrix.isla_status()
    oracle = coverage_matrix.oracle_mnemonics()
    status = {}
    for mnem, v in verdict.items():
        if v == "NOENC":
            continue
        if v == "PASS" and (mnem not in vacuous
                            or mnem in coverage_matrix.NO_OBSERVABLE_STATE):
            status[mnem] = "isla"
        elif mnem in oracle:
            status[mnem] = "oracle"
        elif v == "PASS":
            status[mnem] = "vacuous"
        elif v == "SKIP":
            status[mnem] = "skip"
        else:
            status[mnem] = "uncovered"
    for mnem in oracle:
        status.setdefault(mnem, "oracle")
    return {
        "captured": datetime.datetime.now().isoformat(timespec="seconds"),
        "status": status,
        "coverage": _coverage(),
    }


def _coverage():
    """Branch coverage, current scope and combined. Empty if not measurable."""
    out = {}
    for label, scope in (("current", "rfp-scope-current.txt"),
                         ("combined", "rfp-scope.txt")):
        path = os.path.join(HERE, scope)
        if not os.path.exists(path):
            continue
        try:
            r = subprocess.run(
                [sys.executable, os.path.join(HERE, "coverage_report.py"),
                 "--no-replay", "--scope", path],
                capture_output=True, text=True, timeout=300, cwd=HERE)
        except (subprocess.SubprocessError, OSError):
            continue
        for line in r.stdout.splitlines():
            parts = line.split()
            if len(parts) == 4 and parts[0] == "branches":
                out[label] = {"covered": int(parts[1]), "total": int(parts[2]),
                              "pct": float(parts[3].rstrip("%"))}
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--update", action="store_true",
                    help="Write the current state as the new baseline.")
    ap.add_argument("--json", metavar="FILE", help="Also write the report as JSON.")
    ap.add_argument("--baseline", default=BASELINE)
    ap.add_argument("--quiet", action="store_true",
                    help="Only print regressions, not improvements.")
    args = ap.parse_args()

    now = current_state()

    if args.update:
        os.makedirs(os.path.dirname(args.baseline), exist_ok=True)
        with open(args.baseline, "w") as f:
            json.dump(now, f, indent=1, sort_keys=True)
        n = len(now["status"])
        print(f"baseline updated: {n} instructions, captured {now['captured']}")
        for k, v in now["coverage"].items():
            print(f"  {k:9s} branches {v['covered']}/{v['total']}  {v['pct']:.1f}%")
        print(f"\nwritten to {os.path.relpath(args.baseline, HERE)}")
        return 0

    if not os.path.exists(args.baseline):
        sys.exit("no baseline at %s\nrun `regression.py --update` to create one"
                 % args.baseline)
    with open(args.baseline) as f:
        base = json.load(f)

    regressed, improved, added, removed = [], [], [], []
    for mnem, st in sorted(now["status"].items()):
        old = base["status"].get(mnem)
        if old is None:
            added.append((mnem, st))
        elif RANK[st] < RANK[old]:
            regressed.append((mnem, old, st))
        elif RANK[st] > RANK[old]:
            improved.append((mnem, old, st))
    for mnem in sorted(base["status"]):
        if mnem not in now["status"]:
            removed.append((mnem, base["status"][mnem]))

    print("=" * 68)
    print(f"Regression status vs baseline captured {base['captured']}")
    print("=" * 68)

    if regressed:
        print(f"\nREGRESSED ({len(regressed)}):")
        for mnem, old, new in regressed:
            print(f"  {mnem:22s} {old:10s} -> {new}")
    else:
        print("\nno regressions")

    if removed:
        # An instruction vanishing is a regression in coverage even though no
        # single status got worse -- it usually means a sweep did not run.
        print(f"\nDISAPPEARED ({len(removed)}) -- did every sweep run?:")
        for mnem, old in removed[:12]:
            print(f"  {mnem:22s} was {old}")

    if not args.quiet:
        if improved:
            print(f"\nimproved ({len(improved)}):")
            for mnem, old, new in improved[:20]:
                print(f"  {mnem:22s} {old:10s} -> {new}")
            if len(improved) > 20:
                print(f"  ... and {len(improved) - 20} more")
        if added:
            print(f"\nnew instructions ({len(added)}):")
            for mnem, st in added[:12]:
                print(f"  {mnem:22s} {st}")
            if len(added) > 12:
                print(f"  ... and {len(added) - 12} more")

    print("\n" + "-" * 68)
    print(f"{'scope':10s} {'baseline':>18s} {'now':>18s}   delta")
    for k in ("current", "combined"):
        b, n = base["coverage"].get(k), now["coverage"].get(k)
        if not b or not n:
            continue
        d = n["pct"] - b["pct"]
        flag = "  <-- DOWN" if d < -0.05 else ""
        print(f"{k:10s} {b['covered']:6d}/{b['total']:<6d}{b['pct']:5.1f}% "
              f"{n['covered']:6d}/{n['total']:<6d}{n['pct']:5.1f}%   {d:+5.1f}pt{flag}")

    cov_down = any(now["coverage"].get(k, {}).get("pct", 0)
                   < base["coverage"].get(k, {}).get("pct", 0) - 0.05
                   for k in base["coverage"])

    if args.json:
        with open(args.json, "w") as f:
            json.dump({"regressed": regressed, "improved": improved,
                       "added": added, "removed": removed,
                       "coverage_baseline": base["coverage"],
                       "coverage_now": now["coverage"]}, f, indent=1)
        print(f"\nwrote {args.json}")

    failed = bool(regressed) or bool(removed) or cov_down
    print("\nRESULT:", "REGRESSION" if failed else "clean")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
