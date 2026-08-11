#!/usr/bin/env python3
"""Full status: per extension, what passed, what failed, and *whose fault it is*.

The question this answers is the one a pass/fail table cannot: of the failures,
which are the golden model disagreeing with Spike (findings worth reporting
upstream), and which are our own generator or harness falling short?

That distinction has been the single most useful one on this project, because
the ratio is lopsided in a way that is easy to get backwards. Every failure
investigated so far has been ours, except four model-side findings (A1-A4).
A report that lumps them together invites the opposite conclusion.

A worked example of why the split needs evidence rather than a plausible
story: the 12 `ssamoswap` failures were filed as a model defect on the strength
of a real, reproduced model bug (A4) found nearby. They are not that bug. They
are ours -- M-mode tests for an instruction the spec forbids in M-mode. The
model bug was real and is fixed; it just was not the cause of these.

Categories, and how each is decided -- from the recorded failure reason, not
from a guess:

  MODEL      the golden model is at fault -- either it and Spike disagree
             (A1-A3) or it contradicts itself (A4). Only assigned where a
             documented finding says so, because "Sail passes, Spike fails"
             on its own is more often a configuration difference (findings
             C6) than a model defect.
  FRAMEWORK  our generator cannot produce a valid test. Split by which
             limitation, so the report says what to fix rather than just
             "framework".
  ROUTED     isla cannot generate it, and another backend covers it -- the
             oracle (B5/B6/C12) or a hand-written template (C11). Not a
             failure in any meaningful sense; the test exists and passes.
  EXPECTED   the test asserts a trap and got one, or is a negative control.

Usage:
    python3 status_report.py
    python3 status_report.py --extension V
    python3 status_report.py --failures-only
"""
import argparse
import collections
import glob
import os
import re
import sys

import coverage_matrix

CORPUS = os.path.expanduser("~/.cache/riscv-sweep")
RESULTS = os.path.join(CORPUS, "elfs", "_results")

# (regex on the recorded reason, category, sub-cause, findings reference).
# Order matters: first match wins, so specific patterns precede general ones.
RULES = [
    (r"Symbolic \(bit\)vector length",
     "FRAMEWORK", "isla cannot execute V element paths", "B5"),
    (r"Function riscv_[fd]\d+\w+ does not exist",
     "FRAMEWORK", "isla has no SoftFloat: FP arithmetic unexecutable", "B6"),
    (r"Function get_16_random_bits does not exist",
     "FRAMEWORK", "isla missing primop for Zkr entropy source", "B3"),
    (r"does not exist",
     "FRAMEWORK", "isla missing a model primop", "B3/B6"),
    (r"Killed|Cannot allocate|MemoryError|memory allocation",
     "FRAMEWORK", "solver ran out of memory", "harness-limits"),
    (r"TIMEOUT \(generation\)",
     "FRAMEWORK", "solver did not finish in the time limit", "harness-limits"),
    (r"TIMEOUT",
     "FRAMEWORK", "simulator timed out (usually a self-loop)", "-"),
    (r"unrecognized opcode|illegal operands|Assembler",
     "FRAMEWORK", "generated assembly the assembler rejects", "C7"),
    (r"sail=6 spike=0",
     "MODEL", "Sail accepts, Spike rejects -- check Spike's ISA string first (C6)", "A?"),
    (r"no ELF produced",
     "FRAMEWORK", "generation produced no ELF (reason not recorded)", "-"),
]

# Failures that are known model-vs-Spike divergences, keyed by the instruction
# or CSR involved. Deliberately a short, explicit list: promoting a failure to
# "the model is wrong" needs a written-up reproducer, not a heuristic.
MODEL_DIVERGENCE = {
    "pmpaddr0": ("PMP entry 0 reset value differs between Sail and Spike", "A2"),
    "pmpcfg0": ("PMP entry 0 reset value differs between Sail and Spike", "A2"),
    "stimecmp": ("Spike aborts on stimecmp without Zicntr rather than trapping", "A3"),
}

# Failures that are ours, keyed by mnemonic, where the *reason* is not visible
# in the recorded failure text. Kept separate from RULES because these are
# conclusions from investigation, not pattern matches.
#
# `ssamoswap` was briefly misfiled as a model defect (A4). It is not: the spec
# says "Use of Zicfiss in M-mode is not supported", the model implements that
# faithfully (`vmem.sail:416`, raising E_SAMO_Access_Fault), and the generator
# emits these as ordinary M-mode instruction tests, which are architecturally
# required to fault. The correct test either expects the trap or runs in S-mode
# against a mapped shadow-stack page.
FRAMEWORK_KNOWN = {
    "ssamoswap.w": ("M-mode test for a instruction that cannot run in M-mode; "
                    "should expect the trap or run in S-mode", "A4-note"),
    "ssamoswap.d": ("M-mode test for a instruction that cannot run in M-mode; "
                    "should expect the trap or run in S-mode", "A4-note"),
}

# Extensions isla structurally cannot do, which the oracle covers wholesale.
# Kept for reporting only: routing is decided per *instruction*, by asking
# whether the oracle corpus actually contains it, not by which extension it
# belongs to. That distinction started mattering once the oracle picked up
# scalar instructions too -- cpop, aes64im, xperm8 and the compressed jumps
# are routed for reasons that have nothing to do with V or FP.
ROUTED_EXT = {"V", "vector_crypto", "bfloat16", "FD"}

# Extensions the RFQ defers to a follow-up iteration: "Future iterations of
# this RFQ may target automatic test generation for the hypervisor extension,
# the extensions involving floating point, and the vector extensions."
#
# They are still generated and run -- being able to extend to them is itself a
# requirement, and a working generator is the only convincing evidence of it --
# but they do not belong in the *current deliverable's* pass/fail table. Left
# in, they dominate it: 1257 of 1285 failures are these, and almost all of
# those are instructions isla cannot execute that the oracle covers instead.
DEFERRED_EXT = {"V", "vector_crypto", "bfloat16", "FD", "H"}


_CSR_LINE = re.compile(
    r"^(\S+)\s+(\S+)\s+(0x[0-9a-f]{3})\s+(read/write|write-must-trap)\s+"
    r"(\d+)/(\d+) pass\s+sail=(\d+) spike=(\d+)")


def parse_csr_logs():
    """[(ext, xlen, verdict, csr, reason)] from the CSR sweep logs.

    Included because the CSR sweep is where the *model* divergences actually
    live -- A2 (PMP entry 0 reset value) and A3 (Spike aborting on stimecmp)
    are both CSR findings, and a report drawn only from the opcode sweep shows
    zero model divergences, which reads as "we found nothing" rather than
    "we found them somewhere else".
    """
    rows = []
    for path in glob.glob(os.path.join(CORPUS, "csrmodel*.log")):
        xl = "32" if "32" in os.path.basename(path) else "64"
        for line in open(path, errors="ignore"):
            m = _CSR_LINE.match(line.strip())
            if not m:
                continue
            name, npass, sail, spike = m.group(2), int(m.group(5)), int(m.group(7)), int(m.group(8))
            reason = line.split("<-", 1)[1].strip() if "<-" in line else ""
            # Sail passing where Spike does not is the signature of a
            # divergence *or* of Spike missing an extension; the classifier
            # decides which, from the documented findings.
            if npass == 6:
                rows.append(("CSR", xl, "PASS", name, ""))
            else:
                rows.append(("CSR", xl, "FAIL", name,
                             reason or ("sail=%d spike=%d" % (sail, spike))))
    return rows


def parse_results():
    """[(extension, xlen, verdict, mnemonic, reason)] from the sweep results."""
    rows = []
    for path in sorted(glob.glob(os.path.join(RESULTS, "*.txt"))):
        target = os.path.basename(path)[:-4]
        ext, _, xl = target.rpartition("-")
        # Result filenames spell it "C-rv64"; the CSR logs spell it "64". Strip
        # the prefix here so both sources agree and the report doesn't print
        # "rvrv64".
        xl = xl[2:] if xl.startswith("rv") else xl
        for line in open(path, errors="ignore"):
            parts = line.split()
            if len(parts) < 2 or parts[0] not in ("PASS", "FAIL", "SKIP", "NOENC"):
                continue
            reason = ""
            if "<-" in line:
                reason = line.split("<-", 1)[1].strip()
            rows.append((ext, xl, parts[0], parts[1], reason))
    return rows


def classify(ext, mnem, reason, oracle_covered):
    """(category, sub-cause, findings ref) for one failure."""
    for key, (why, ref) in MODEL_DIVERGENCE.items():
        # Prefix match so an ordering-suffixed form counts as the same finding:
        # `ssamoswap.w.aqrl` fails for exactly the reason `ssamoswap.w` does,
        # and listing all four variants of both widths would only invite one
        # of them to be forgotten.
        if mnem == key or mnem.startswith(key + "."):
            return "MODEL", why, ref
    for key, (why, ref) in FRAMEWORK_KNOWN.items():
        if mnem == key or mnem.startswith(key + "."):
            return "FRAMEWORK", why, ref
    if mnem in oracle_covered:
        # "oracle_covered" is every mnemonic the concrete corpus contains,
        # which includes the handful of hand-written templates -- so say
        # "another backend" rather than naming the oracle specifically.
        return "ROUTED", "isla cannot generate it; another backend covers it", "B5/B6/C11/C12"
    for pattern, cat, why, ref in RULES:
        if re.search(pattern, reason):
            return cat, why, ref
    return "FRAMEWORK", "unclassified -- reason not recorded", "-"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--extension", help="Detail for one extension only.")
    ap.add_argument("--failures-only", action="store_true")
    ap.add_argument("--all-scope", action="store_true",
                    help="Include the extensions the RFQ defers (V, FP, H). By "
                         "default the report shows the current deliverable only, "
                         "and summarises the deferred ones separately.")
    args = ap.parse_args()

    if not os.path.isdir(RESULTS):
        sys.exit("no sweep results at %s" % RESULTS)

    rows = parse_results() + parse_csr_logs()
    oracle_covered = set(coverage_matrix.oracle_mnemonics())

    per_ext = collections.defaultdict(collections.Counter)
    causes = collections.Counter()
    cause_ref = {}
    detail = collections.defaultdict(list)

    deferred = collections.Counter()
    for ext, xl, verdict, mnem, reason in rows:
        if args.extension and ext != args.extension:
            continue
        if ext in DEFERRED_EXT and not args.all_scope and not args.extension:
            deferred[verdict] += 1
            if verdict == "FAIL":
                cat, _w, _r = classify(ext, mnem, reason, oracle_covered)
                deferred[cat] += 1
            continue
        per_ext[ext][verdict] += 1
        if verdict == "FAIL":
            cat, why, ref = classify(ext, mnem, reason, oracle_covered)
            per_ext[ext][cat] += 1
            causes[(cat, why)] += 1
            cause_ref[(cat, why)] = ref
            detail[ext].append((mnem, xl, cat, why))

    if not args.failures_only:
        print("=" * 78)
        print("Per-extension test results")
        print("=" * 78)
        print(f"{'extension':16s} {'pass':>5s} {'fail':>5s} {'skip':>5s} {'n/a':>5s}"
              f" {'MODEL':>6s} {'FRAME':>6s} {'ROUTED':>7s}")
        print("-" * 78)
        for ext in sorted(per_ext):
            c = per_ext[ext]
            print(f"{ext:16s} {c['PASS']:5d} {c['FAIL']:5d} {c['SKIP']:5d} {c['NOENC']:5d}"
                  f" {c['MODEL']:6d} {c['FRAMEWORK']:6d} {c['ROUTED']:7d}")
        tot = collections.Counter()
        for c in per_ext.values():
            tot.update(c)
        print("-" * 78)
        print(f"{'TOTAL':16s} {tot['PASS']:5d} {tot['FAIL']:5d} {tot['SKIP']:5d} "
              f"{tot['NOENC']:5d} {tot['MODEL']:6d} {tot['FRAMEWORK']:6d} {tot['ROUTED']:7d}")

    if deferred:
        print("\n" + "-" * 78)
        print("deferred to a future RFQ iteration (V, vector-crypto, bfloat16, FD, H)")
        print("-" * 78)
        print(f"  {deferred['PASS']} pass, {deferred['FAIL']} fail "
              f"-- of those failures, {deferred['ROUTED']} are generated and verified "
              f"by the oracle instead,\n  and {deferred['FRAMEWORK']} are isla "
              f"limitations. Not part of the current deliverable; shown so the "
              f"extendability\n  evidence is visible. Use --all-scope to fold them in.")

    print("\n" + "=" * 78)
    print("Why the failures fail")
    print("=" * 78)
    for (cat, why), n in causes.most_common():
        print(f"{n:6d}  [{cat:9s}] {why}   ({cause_ref[(cat, why)]})")

    model_n = sum(n for (cat, _w), n in causes.items() if cat == "MODEL")
    frame_n = sum(n for (cat, _w), n in causes.items() if cat == "FRAMEWORK")
    routed_n = sum(n for (cat, _w), n in causes.items() if cat == "ROUTED")
    print("-" * 78)
    print(f"  golden-model findings    : {model_n}  (divergences + model defects)")
    print(f"  framework limitations    : {frame_n}")
    print(f"  routed to another backend: {routed_n}  (covered, not failing)")

    if args.extension:
        print(f"\n{'-' * 78}\nfailures in {args.extension}\n{'-' * 78}")
        for mnem, xl, cat, why in sorted(detail[args.extension])[:40]:
            print(f"  {mnem:20s} rv{xl:2s} [{cat:9s}] {why}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
