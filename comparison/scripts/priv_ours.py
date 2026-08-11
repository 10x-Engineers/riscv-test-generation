#!/usr/bin/env python3
"""Our privileged corpus, measured against the hand-written privileged set.

Run after priv_compare.py, which writes priv_act4.json -- both replay onto the
same shared coverage file, so they cannot run at the same time.

The same attribution question as the base-integer comparison: of the branches
the hand-written suite reaches and we do not, how many come from shared startup
code (reached by most of their tests) versus from what a specific test does?
That is what decided the earlier result, and it is what decides whether the
remaining work is corpus scope or genuine capability.
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
CFG = os.path.join(SP, "act4-sail.json")


def main():
    ours = sorted(glob.glob(SP + "/ours-priv-rv32/**/*.elf", recursive=True))
    assert ours, "no privileged ELFs of ours -- run scenario_tests.py first"

    per, ok = {}, 0
    for e in ours:
        if os.path.exists(COV):
            os.remove(COV)
        try:
            r = subprocess.run([SIM, "--config", CFG, e], cwd=SAIL,
                               capture_output=True, text=True, timeout=120)
            ok += "SUCCESS" in r.stdout
        except subprocess.TimeoutExpired:
            pass
        per[e] = cr.parse_spans(COV) if os.path.exists(COV) else set()

    total = {s for s in cr.parse_spans(cr.BRANCH_INFO) if s[0] == "B"}
    O = ({s for s in set().union(*per.values()) if s[0] == "B"} & total) if per else set()

    a = json.load(open(os.path.join(SP, "priv_act4.json")))
    A = {tuple(k.split("|")) for k in a["act4_priv"]}
    A = {(x[0], x[1], int(x[2]), int(x[3]), int(x[4]), int(x[5])) for x in A}

    print(f"\n{'':26s} {'tests':>6s} {'branches':>9s}")
    print(f"{'hand-written privileged':26s} {a['elfs']:6d} {len(A):9d}")
    print(f"{'ours privileged':26s} {len(ours):6d} {len(O):9d}")
    print(f"\nboth reach      {len(A & O):5d}")
    print(f"ONLY theirs     {len(A - O):5d}")
    print(f"ONLY ours       {len(O - A):5d}")

    print("\ntop files only they reach:")
    for f, n in collections.Counter(s[1] for s in A - O).most_common(12):
        print(f"  {n:5d}  {f}")
    print("\ntop files only we reach:")
    for f, n in collections.Counter(s[1] for s in O - A).most_common(8):
        print(f"  {n:5d}  {f}")

    json.dump({"ours_priv": sorted("|".join(map(str, s)) for s in O),
               "elfs": len(ours), "passed": ok},
              open(os.path.join(SP, "priv_ours.json"), "w"))


if __name__ == "__main__":
    main()
