#!/usr/bin/env python3
"""Derive the spans no generated test can reach, with a reason for each.

A raw Sail source-coverage percentage understates delivered coverage, because
the denominator contains code that instruction execution cannot reach at all.
The largest category by far is Sail's *bidirectional mappings*:

    mapping rtype_mnemonic : rop <-> string = { ADD <-> "add", ... }

The `<->` declares two directions. Executing `add x1, x2, x3` runs the
instruction's `execute` clause; it never consults the mnemonic table, because
nothing needs the string "add" to compute a result. The backward direction --
string to instruction -- is assembly *parsing*, and the forward one is
*printing*. Both are real code, both are instrumented, and neither is reachable
by running a program. In `extensions/I/base_insts.sail` they account for 87 of
162 uncovered spans: over half of the apparent gap is disassembly plumbing.

Excluding them is only honest if the exclusion is derived and reasoned rather
than hand-listed, which is what this script is for. Line numbers move whenever
the model is edited, so a checked-in list of line numbers silently rots into
excluding the wrong code. Here the ranges are recomputed from the Sail sources
on every run, and each exclusion carries the construct it came from and why it
is unreachable, so a reviewer can audit any entry back to a declaration.

Deliberately *not* excluded: paths that are merely hard to reach. A privileged
branch guarded by `mstatus.TSR`, or an RV64-only path, is uncovered because no
test has set that state up yet -- that is a work item, not an exclusion. Only
constructs that instruction execution can never reach are listed here.

Usage:
    derive_span_exclusions.py [--model-dir DIR] [-o FILE] [--json FILE]
    coverage_report.py --exclude-spans FILE ...
"""

import argparse
import json
import os
import re
import sys

import paths

# A mapping is unreachable-by-execution when one of its directions exists only
# to render or parse assembly text. Detected structurally, by the declaration
# form, rather than by matching known names -- a new mnemonic table added to the
# model is then covered automatically.
#
#   mapping clause assembly = RTYPE(...) <-> rtype_mnemonic(op) ^ ...
#   mapping rtype_mnemonic : rop <-> string = { ... }
#   mapping encdec_uop : uop <-> bits(7) = { ... }
#
# `encdec` is included on the same grounds as `assembly`: the backward direction
# is decoding, which the C emulator performs through generated decode functions
# rather than by running the mapping as instrumented model code.
_ASSEMBLY_CLAUSE = re.compile(r"^mapping clause assembly\b")
_STRING_MAPPING = re.compile(r"^mapping\s+(\w+)\s*:\s*[^<]*<->\s*string\b")
_ENCDEC_MAPPING = re.compile(r"^mapping\s+(encdec\w*)\s*:")
_ENCDEC_CLAUSE = re.compile(r"^mapping clause (encdec\w*)\b")
# A naming clause: `mapping clause csr_name_map = 0x3A0 <-> "pmpcfg0"`. Same
# argument as `mapping clause assembly` and the same shape, but the rule above
# only matched the mapping's *declaration* (`mapping X : ... <-> string`), and
# these tables are declared in one file and extended by clauses in twenty
# others. In pmp/pmp_regs.sail alone that is 76 of 99 uncovered spans -- three
# quarters of the apparent PMP gap is the CSR-name table.
_STRING_CLAUSE = re.compile(r'^mapping clause (\w+)\s*=.*<->\s*"')

REASONS = {
    "assembly_clause": "bidirectional `mapping clause assembly`; renders/parses "
                       "assembly text and is not reached by instruction execution",
    "string_mapping": "bidirectional mnemonic table (`<-> string`); the enum-to-text "
                      "direction is disassembly, not execution",
    "encdec_mapping": "bidirectional encode/decode mapping; decoding is performed by "
                      "generated decode functions, not by executing this mapping",
    "string_clause": "bidirectional naming clause (`<-> \"text\"`); renders a CSR or "
                     "enum as text and is never consulted by instruction execution",
}


def _block_end(lines, start):
    """Last line of the declaration beginning at `start` (0-indexed).

    Sail declarations here end either at a closing brace at column 0 (block
    form) or at the first blank line (single-clause form). Both appear in the
    model, so both terminate the scan.
    """
    depth = 0
    seen_brace = False
    for i in range(start, len(lines)):
        s = lines[i].split("//")[0]
        depth += s.count("{") - s.count("}")
        if "{" in s:
            seen_brace = True
        if seen_brace and depth <= 0:
            return i
        if not seen_brace and i > start and not s.strip():
            return i - 1
    return len(lines) - 1


def derive(model_dir):
    """[{file, first_line, last_line, construct, kind, reason}] for the model."""
    out = []
    for root, _d, files in os.walk(model_dir):
        for fn in sorted(files):
            if not fn.endswith(".sail"):
                continue
            rel = os.path.relpath(os.path.join(root, fn), model_dir)
            lines = open(os.path.join(root, fn), errors="ignore").read().splitlines()
            for i, raw in enumerate(lines):
                line = raw.strip()
                kind = name = None
                if _ASSEMBLY_CLAUSE.match(line):
                    kind, name = "assembly_clause", "assembly"
                elif _STRING_MAPPING.match(line):
                    kind = "string_mapping"
                    name = _STRING_MAPPING.match(line).group(1)
                elif _STRING_CLAUSE.match(line):
                    kind = "string_clause"
                    name = _STRING_CLAUSE.match(line).group(1)
                elif _ENCDEC_MAPPING.match(line):
                    kind = "encdec_mapping"
                    name = _ENCDEC_MAPPING.match(line).group(1)
                elif _ENCDEC_CLAUSE.match(line):
                    kind = "encdec_mapping"
                    name = _ENCDEC_CLAUSE.match(line).group(1)
                if not kind:
                    continue
                out.append({
                    "file": rel,
                    "first_line": i + 1,
                    "last_line": _block_end(lines, i) + 1,
                    "construct": name,
                    "kind": kind,
                    "reason": REASONS[kind],
                })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir",
                    default=os.path.join(paths.SAIL_RISCV, "model"))
    ap.add_argument("-o", "--out", default="span-exclusions.txt",
                    help="text form consumed by coverage_report.py --exclude-spans")
    ap.add_argument("--json", help="also write the full record, with reasons")
    args = ap.parse_args()

    recs = derive(os.path.abspath(args.model_dir))
    if not recs:
        sys.exit("no exclusion constructs found -- check --model-dir")

    with open(args.out, "w") as f:
        f.write("# Spans unreachable by instruction execution, derived from the\n"
                "# Sail sources by derive_span_exclusions.py. Regenerate after any\n"
                "# model change; line numbers move.\n"
                "# format: <file>:<first_line>-<last_line>  # reason\n")
        for r in recs:
            f.write(f"{r['file']}:{r['first_line']}-{r['last_line']}"
                    f"  # {r['construct']}: {r['reason']}\n")
    if args.json:
        with open(args.json, "w") as f:
            json.dump(recs, f, indent=1)

    by_kind = {}
    for r in recs:
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
    print(f"wrote {args.out}: {len(recs)} excluded ranges")
    for k, v in sorted(by_kind.items(), key=lambda kv: -kv[1]):
        print(f"  {k:<18} {v}")


if __name__ == "__main__":
    main()
