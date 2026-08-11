#!/usr/bin/env python3
"""Per-instruction status across *both* generators, in one table.

The two halves of this project are measured separately, and neither number is
honest on its own:

  * The isla sweep reports 799 pass / 1285 fail. Read as a pass rate that is
    38%, which is wrong in a way that matters -- 1257 of those "failures" are
    instructions isla structurally cannot execute (SoftFloat has no
    implementation in its IR; the V element paths hit a symbolic vector
    length), and every one of them is generated and verified by the oracle
    instead. They are *routed*, not failed.

  * The oracle's own run reports only its own suites, so it cannot say what
    fraction of the instruction set is covered overall.

This reports the thing an RFP actually asks for: for every instruction the
model defines, is there a test that *verifies* it, and which generator produced
it.

A subtlety this deliberately encodes: an isla PASS is only counted as verified
if the generated test asserts something. A test whose expected-state tables are
empty passes unconditionally -- that was 47.7% of the corpus before
`--enable-fp`/`--enable-vector` were fixed to set mstatus inside isla, and a
report that counted those as coverage would repeat the original mistake.

Usage:
    python3 coverage_matrix.py
    python3 coverage_matrix.py --by-extension
"""
import argparse
import collections
import glob
import os
import re
import sys

CORPUS = os.path.expanduser("~/.cache/riscv-sweep")
ISLA_RESULTS = os.path.join(CORPUS, "elfs", "_results")
ISLA_ELFS = os.path.join(CORPUS, "elfs")
ORACLE = os.path.join(CORPUS, "oracle")

VERDICTS = ("PASS", "FAIL", "SKIP", "NOENC")

# Lines in a generated .s that mean "this test compares real architectural
# state". A test with none of these asserts nothing and passes no matter what
# the model does.
_STATE_TABLE = re.compile(r"^(final_gpr_values|final_fpr_values|final_vr_values):")
_DATA_ENTRY = re.compile(r"^\s*\.(word|dword|byte)\s")
# A trap test asserts its *cause* rather than a register file. `ecall` writing
# no GPR is the correct outcome; what makes the test real is that it compares
# mcause against the expected value and fails otherwise. Counting these as
# "checks nothing" understated coverage in the opposite direction from the
# original vacuity bug, and is just as wrong.
_TRAP_CHECK = re.compile(r"csrr\s+x\d+,\s*mcause")

# Instructions with no architecturally observable result, for which "executed
# and did not trap" genuinely is the whole test. Distinguished from vacuous so
# the report does not flag correct behaviour as a defect -- `fence` has nothing
# to compare, whereas `fadd.s` comparing nothing was a bug.
NO_OBSERVABLE_STATE = {
    "fence", "fence.i", "fence.tso", "pause", "wfi",
    "c.nop", "nop", "ntl.p1", "ntl.pall", "ntl.s1", "ntl.all",
    "cbo.clean", "cbo.flush", "cbo.inval", "prefetch.i", "prefetch.r", "prefetch.w",
    "mop.r", "mop.rr", "c.mop",
}


def isla_status():
    """{mnemonic: verdict} plus the set of mnemonics whose test checks nothing."""
    verdict, vacuous, ext_of = {}, set(), {}
    for path in sorted(glob.glob(os.path.join(ISLA_RESULTS, "*.txt"))):
        target = os.path.basename(path)[:-4]           # e.g. "V-rv64"
        ext = target.rsplit("-", 1)[0]
        for line in open(path, errors="ignore"):
            parts = line.split()
            if len(parts) < 2 or parts[0] not in VERDICTS:
                continue
            v, mnem = parts[0], parts[1]
            ext_of.setdefault(mnem, ext)
            # Best verdict across XLENs: an instruction that passes on RV64 and
            # is skipped on RV32 is covered, not half-covered.
            if mnem not in verdict or _rank(v) > _rank(verdict[mnem]):
                verdict[mnem] = v
    for s in glob.glob(os.path.join(ISLA_ELFS, "**", "*.s"), recursive=True):
        mnem = _mnemonic_of_test(s)
        if mnem and not _checks_state(s):
            vacuous.add(mnem)
    return verdict, vacuous, ext_of


def _rank(v):
    return {"PASS": 3, "FAIL": 2, "SKIP": 1, "NOENC": 0}.get(v, 0)


def _mnemonic_of_test(path):
    """The instruction a generated .s is testing, from its filename."""
    base = os.path.basename(path)[:-2]
    return base or None


def _checks_state(path):
    """True if the test compares at least one real architectural value."""
    try:
        lines = open(path, errors="ignore").read().split("\n")
    except OSError:
        return False
    for line in lines:
        if _TRAP_CHECK.search(line):
            return True
    for i, line in enumerate(lines):
        if _STATE_TABLE.match(line):
            for follow in lines[i + 1:]:
                if _DATA_ENTRY.match(follow):
                    return True
                if follow and not follow.startswith(("\t/*", "    /*", "\t.byte")):
                    break
    return False


def oracle_mnemonics():
    """Every instruction the oracle corpus actually contains."""
    seen = collections.Counter()
    for s in glob.glob(os.path.join(ORACLE, "**", "*.S"), recursive=True):
        for line in open(s, errors="ignore"):
            line = line.split("//")[0].strip()
            if not line or line.startswith((".", "#", "/*")) or line.endswith(":"):
                continue
            seen[line.split()[0]] += 1
    return seen


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--by-extension", action="store_true")
    ap.add_argument("--list", metavar="STATUS",
                    choices=["uncovered", "vacuous", "oracle", "isla"],
                    help="Print the mnemonics in one status class.")
    args = ap.parse_args()

    if not os.path.isdir(ISLA_RESULTS):
        sys.exit("no isla sweep results at %s -- run sweep_all.py first" % ISLA_RESULTS)

    verdict, vacuous, ext_of = isla_status()
    oracle = oracle_mnemonics()

    status = {}
    for mnem, v in verdict.items():
        if v == "NOENC":
            continue                       # not a real instruction: a cross-product artefact
        if v == "PASS" and (mnem not in vacuous or mnem in NO_OBSERVABLE_STATE):
            status[mnem] = "isla"
        elif mnem in oracle:
            status[mnem] = "oracle"
        elif v == "PASS":
            status[mnem] = "vacuous"       # passes, checks nothing, no oracle test either
        elif v == "SKIP":
            status[mnem] = "skip"
        else:
            status[mnem] = "uncovered"
    # Instructions the oracle generates that the isla sweep never listed.
    for mnem in oracle:
        status.setdefault(mnem, "oracle")

    counts = collections.Counter(status.values())
    real = sum(v for k, v in counts.items() if k != "skip")
    verified = counts["isla"] + counts["oracle"]

    print("=" * 66)
    print("Per-instruction verification status")
    print("=" * 66)
    for k, label in (("isla", "verified by isla (symbolic)"),
                     ("oracle", "verified by oracle (concrete)"),
                     ("vacuous", "PASSES BUT CHECKS NOTHING"),
                     ("stateless", "no observable state (correct)"),
                     ("uncovered", "not covered by either"),
                     ("skip", "n/a at this XLEN")):
        if counts[k]:
            pct = 100 * counts[k] / real if k != "skip" else 0
            print(f"  {label:32s} {counts[k]:5d}" + (f"  {pct:5.1f}%" if k != "skip" else ""))
    print(f"\n  {'VERIFIED':32s} {verified:5d}  {100*verified/real:5.1f}%  of {real} instructions")

    if args.by_extension:
        print("\n" + "-" * 66)
        print(f"{'extension':16s} {'isla':>6s} {'oracle':>7s} {'vacuous':>8s} {'uncov':>6s}")
        print("-" * 66)
        per = collections.defaultdict(collections.Counter)
        for mnem, st in status.items():
            per[ext_of.get(mnem, "(oracle-only)")][st] += 1
        for ext in sorted(per):
            c = per[ext]
            if c["isla"] + c["oracle"] + c["vacuous"] + c["uncovered"] == 0:
                continue
            print(f"{ext:16s} {c['isla']:6d} {c['oracle']:7d} {c['vacuous']:8d} {c['uncovered']:6d}")

    if args.list:
        picked = sorted(m for m, st in status.items() if st == args.list)
        print(f"\n{len(picked)} instruction(s) with status '{args.list}':")
        for i in range(0, len(picked), 6):
            print("   " + "  ".join(f"{m:18s}" for m in picked[i:i + 6]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
