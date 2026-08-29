#!/usr/bin/env python3
"""Turn uncovered Sail spans into a work queue that says what stimulus is missing.

`coverage_report.py --uncovered` answers *what* is not covered: a list of
file:line:col. That is the right output for a diff, and the wrong one for
deciding what to generate next -- a bare line number does not say whether the
span needs a privilege change, a CSR bit, a different configuration, or simply
an operand value nobody happened to pick.

This reads that list back against the Sail source, recovers the condition
guarding each uncovered span, and classifies the stimulus it requires. The
result is a table -- CSV, JSON, and a spreadsheet -- with one row per uncovered
span, sorted so the largest closable groups come first.

The classification is derived from the model text, not from a hand-maintained
mapping: each span's enclosing `match` arm or `if` guard is read out of the
source and matched against the forms the model actually uses to discriminate
behaviour. Where the guard does not fall into a known form the row says so
rather than guessing, because a wrong directive costs a generation cycle and an
honest "unclassified" costs a minute of reading.

Why a spreadsheet: the queue is worked through by a person deciding what to
build next, and 68 rows sorted by stimulus with the guard text alongside is the
form that supports that decision. The JSON is for the generator.

Usage:
    coverage_report.py ... --uncovered uncov.txt
    coverage_analysis.py uncov.txt --model-dir <sail>/model -o queue
"""

import argparse
import collections
import csv
import json
import os
import re
import sys

# How the model discriminates behaviour, and what a generator has to do about
# it. Order matters: the first match wins, so the more specific forms come
# first. `directive` is what to pass the generator; None means the fix is not a
# generator flag (a configuration rebuild, or nothing at all).
RULES = [
    ("hypervisor",
     re.compile(r"VirtualUser|VirtualSupervisor|Hypervisor"),
     "unreachable: Hypervisor is not implemented in this model", None),

    ("privilege",
     re.compile(r"^\s*(User|Supervisor|Machine)\s*=>"),
     "run the instruction at this privilege level",
     {"User": "--preload-xepc mepc --preload-xpp mpp=u --pmp-allow-all",
      "Supervisor": "--run-in-supervisor --pmp-allow-all",
      "Machine": "(default; already M-mode)"}),

    ("csr_bit",
     re.compile(r"(m|s|h)(status|envcfg|counteren|ideleg|edeleg)\s*\[\s*(\w+)\s*\]"),
     "preload this CSR field before executing", None),

    ("configuration",
     re.compile(r"\bconfig\s+([\w.]+)"),
     "rebuild/select a configuration where this key differs", None),

    ("extension_gate",
     re.compile(r"currentlyEnabled\s*\(\s*(Ext_\w+)\s*\)|hartSupports"),
     "select a configuration with this extension disabled/enabled", None),

    ("xlen",
     re.compile(r"\bxlen\s*==\s*(\d+)|sizeof\(xlen\)"),
     "run this sweep at the other XLEN", None),

    ("operand_value",
     re.compile(r"!=\s*zreg|==\s*zreg|rs[12]\s*(!=|==)|\brd\s*(!=|==)"),
     "vary the operand: the guard discriminates on a register being x0 or not",
     None),

    ("access_kind",
     re.compile(r"(Load|Store|Atomic|LoadReserved|StoreConditional|"
                r"InstructionFetch|CacheAccess)\s*\("),
     "issue this kind of memory access (AMO, cache-block op, shadow-stack, fetch)",
     None),

    ("fault_path",
     re.compile(r"Illegal_Instruction|internal_error|access_fault|"
                r"Addr_Align|_Page_Fault|accessFault|failure\s*=>"),
     "construct the faulting condition and assert the trap", None),
]

UNCLASSIFIED = ("unclassified", "read the guard and decide", None)


def enclosing_guard(lines, lineno, back=14):
    """The text most likely to be the condition guarding this span.

    Sail states a discriminator either on the span's own line (a `match` arm,
    `X => ...`) or on a nearby `if`/`match` header above it. Both are returned
    to the caller as one blob so the rules can match against whichever carries
    the condition, with the span's own line first so it wins ties.
    """
    own = lines[lineno - 1] if 0 < lineno <= len(lines) else ""
    start = max(0, lineno - 1 - back)
    above = [l for l in lines[start:lineno - 1]
             if re.search(r"\b(if|match|when)\b", l)]
    return own, "\n".join(above[-3:] + [own])


def classify(own, context):
    for name, rx, directive, flags in RULES:
        m = rx.search(own) or rx.search(context)
        if not m:
            continue
        detail = ""
        if name == "privilege":
            mode = re.match(r"^\s*(\w+)", own.strip()).group(1)
            detail = flags.get(mode, "") if flags else ""
        elif m.groups():
            detail = next((g for g in m.groups() if g), "")
        return name, directive, detail
    return UNCLASSIFIED[0], UNCLASSIFIED[1], ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("uncovered", help="output of coverage_report.py --uncovered")
    ap.add_argument("--model-dir", required=True, help="path to <sail-riscv>/model")
    ap.add_argument("-o", "--out-prefix", default="coverage-queue")
    ap.add_argument("--xlsx", action="store_true",
                    help="also write .xlsx (requires openpyxl)")
    args = ap.parse_args()

    cache, rows = {}, []
    for line in open(args.uncovered):
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        kind, loc = parts[0], parts[1]
        fname, _, rest = loc.partition(":")
        lineno = int(rest.split(":")[0])
        path = os.path.join(args.model_dir, fname)
        if fname not in cache:
            try:
                cache[fname] = open(path, errors="ignore").read().splitlines()
            except OSError:
                cache[fname] = []
        own, ctx = enclosing_guard(cache[fname], lineno)
        stim, directive, detail = classify(own, ctx)
        rows.append(collections.OrderedDict(
            file=fname, line=lineno, span_kind=kind,
            stimulus=stim, directive=directive, detail=detail,
            source=own.strip()[:120],
        ))

    if not rows:
        sys.exit("no uncovered spans parsed -- is that a --uncovered file?")

    # Largest groups first: the queue is worked top-down, and the biggest
    # single-stimulus group is the cheapest coverage per unit of work.
    counts = collections.Counter(r["stimulus"] for r in rows)
    rows.sort(key=lambda r: (-counts[r["stimulus"]], r["stimulus"],
                             r["file"], r["line"]))

    with open(args.out_prefix + ".csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    with open(args.out_prefix + ".json", "w") as f:
        json.dump({"total_uncovered": len(rows),
                   "by_stimulus": dict(counts.most_common()),
                   "rows": rows}, f, indent=1)

    if args.xlsx:
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font, PatternFill
            from openpyxl.utils import get_column_letter
            wb = Workbook()
            ws = wb.active
            ws.title = "work queue"
            ws.append(list(rows[0].keys()))
            for r in rows:
                ws.append(list(r.values()))
            for c in ws[1]:
                c.fill = PatternFill("solid", fgColor="1B3A6B")
                c.font = Font(color="FFFFFF", bold=True)
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions
            for i, col in enumerate(zip(*([list(rows[0].keys())] +
                                          [list(map(str, r.values())) for r in rows])), 1):
                ws.column_dimensions[get_column_letter(i)].width = \
                    min(max(len(v) for v in col) + 2, 60)
            s = wb.create_sheet("summary")
            s.append(["stimulus", "spans", "share"])
            for k, v in counts.most_common():
                s.append([k, v, round(100 * v / len(rows), 1)])
            for c in s[1]:
                c.fill = PatternFill("solid", fgColor="1B3A6B")
                c.font = Font(color="FFFFFF", bold=True)
            wb.save(args.out_prefix + ".xlsx")
        except ImportError:
            print("openpyxl not available; skipped .xlsx", file=sys.stderr)

    print(f"{len(rows)} uncovered spans -> {args.out_prefix}.{{csv,json}}"
          + (f",xlsx" if args.xlsx else ""))
    print(f"\n{'stimulus':<18}{'spans':>6}  {'share':>6}   what to do")
    for k, v in counts.most_common():
        d = next(r["directive"] for r in rows if r["stimulus"] == k)
        print(f"{k:<18}{v:>6}  {100*v/len(rows):>5.1f}%   {d[:52]}")


if __name__ == "__main__":
    main()
