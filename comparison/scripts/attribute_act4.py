#!/usr/bin/env python3
"""Step 1: what kind of branch is the ACT4-only deficit made of?

The path-enumeration hypothesis failed (5 of 194 recovered). The replacement
claim is that the branches ACT4 reaches and we do not are reached by *machine
state setup*, not by executing instructions -- and that generating deeper paths
through an instruction therefore cannot reach them in principle.

That claim is testable without generating anything, because ACT4's tests share
a common preamble through the RVTEST_* macros: every test boots the same way,
installs the same trap handler, initialises the same CSRs, and only then runs
the instruction it is actually about.

So the discriminator is *how many different ACT4 tests reach a given branch*:

  reached by nearly every test   -> shared scaffolding: boot, trap handler,
                                    CSR init. Not attributable to the
                                    instruction under test.
  reached by one or two tests    -> specific to that test's instruction.

Pre-registered prediction: most of the 194 are in the first group. If instead
they are concentrated in the second, the state-setup reframe is wrong too, and
the honest report is that we do not know where the deficit comes from.

This run also saves the span sets, which the previous comparison did not --
the earlier numbers had to be re-derived because only the summary survived.
"""
import collections
import glob
import json
import os
import subprocess
import sys

sys.path.insert(0, "/home/jk/Documents/riscv-test-generation/python-isla")
import coverage_report as cr

SP = "/tmp/claude-1000/-home-jk-Documents/6ccf63f4-7ac2-4873-9424-978374156338/scratchpad"
SAIL = "/home/jk/Documents/sail-riscv"
SIM = os.path.join(SAIL, "build-coverage/c_emulator/sail_riscv_sim")
COV = os.path.join(SAIL, "sail_coverage")

ACT4_CFG = os.path.join(SP, "act4-sail.json")
OUR_CFG = os.path.join(SAIL, "build-coverage/config/rv32d_v128_e64.json")


def measure(elfs, config, label):
    """Per-ELF absolute span sets, each ELF replayed alone."""
    per, ok = {}, 0
    for e in elfs:
        if os.path.exists(COV):
            os.remove(COV)
        try:
            r = subprocess.run([SIM, "--config", config, e], cwd=SAIL,
                               capture_output=True, text=True, timeout=60)
            good = "SUCCESS" in r.stdout
        except subprocess.TimeoutExpired:
            good = False
        ok += good
        # A test that exits nonzero writes no coverage file at all (C13), so a
        # failure contributes nothing rather than partial data.
        per[e] = cr.parse_spans(COV) if os.path.exists(COV) else set()
    print(f"{label}: {len(elfs)} ELFs, {ok} SUCCESS, {len(elfs) - ok} not", flush=True)
    return per


def main():
    act4 = sorted(glob.glob("/home/jk/Documents/riscv-arch-test/work/sail-rv32-max/"
                            "elfs/rv32i/I/*.elf") +
                  glob.glob("/home/jk/Documents/riscv-arch-test/work/sail-rv32-max/"
                            "elfs/rv32i/M/*.elf"))
    ours = sorted(glob.glob(SP + "/ours-rv32/**/*.elf", recursive=True))

    a_per = measure(act4, ACT4_CFG, "ACT4 (hand-written, I+M)")
    o_per = measure(ours, OUR_CFG, "ours (generated, I+M)")

    total = {s for s in cr.parse_spans(cr.BRANCH_INFO) if s[0] == "B"}
    def br(s):
        return {x for x in s if x[0] == "B"} & total

    A = br(set().union(*a_per.values())) if a_per else set()
    O = br(set().union(*o_per.values())) if o_per else set()
    only_a = A - O

    print(f"\nACT4 {len(A)}   ours {len(O)}   ACT4-only {len(only_a)}   "
          f"ours-only {len(O - A)}", flush=True)

    # --- the attribution ---------------------------------------------------
    live = [v for v in a_per.values() if v]          # tests that ran to SUCCESS
    n = len(live)
    reach = {s: sum(1 for v in live if s in v) for s in only_a}

    shared = [s for s, c in reach.items() if c >= 0.8 * n]
    common = [s for s, c in reach.items() if 0.2 * n <= c < 0.8 * n]
    specific = [s for s, c in reach.items() if c < 0.2 * n]

    print(f"\n{'=' * 72}")
    print(f"the {len(only_a)} branches ACT4 reaches and we do not, by how many "
          f"of {n} ACT4 tests reach each")
    print("=" * 72)
    for label, group in (("shared scaffolding (>=80% of tests)", shared),
                         ("partly shared (20-80%)", common),
                         ("test-specific (<20%)", specific)):
        pct = 100.0 * len(group) / len(only_a) if only_a else 0
        print(f"  {label:38s} {len(group):5d}  ({pct:5.1f}%)")

    for label, group in (("SHARED SCAFFOLDING", shared),
                         ("TEST-SPECIFIC", specific)):
        c = collections.Counter(s[1] for s in group)
        print(f"\n--- {label}: top files ---")
        for f, k in c.most_common(10):
            print(f"  {k:5d}  {f}")

    json.dump({"act4": sorted("|".join(map(str, s)) for s in A),
               "ours": sorted("|".join(map(str, s)) for s in O),
               "act4_only_reach_counts": {"|".join(map(str, s)): c
                                          for s, c in reach.items()},
               "act4_tests_counted": n},
              open(os.path.join(SP, "act4_attribution.json"), "w"))
    print(f"\nsaved -> {SP}/act4_attribution.json", flush=True)


if __name__ == "__main__":
    main()
