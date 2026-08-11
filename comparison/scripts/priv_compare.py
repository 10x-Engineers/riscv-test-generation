#!/usr/bin/env python3
"""The comparison that matters: privileged architecture, ours vs hand-written.

Today's earlier comparison was 47 base-integer tests. The RFP's first criterion
is privileged coverage, and the hand-written suite has 450 privileged tests --
171 for PMP, 134 for virtual memory. That head-to-head has never been run.

This measures the hand-written side first, because those ELFs are already built
and the measurement does not depend on regenerating our own corpus. Each ELF is
replayed alone so "what this test reaches" means that test, not replay order.

Same caveat as every coverage figure here: a test that exits nonzero writes no
coverage file at all, so this is coverage from passing tests and is a lower
bound. The pass count is printed so that is visible rather than assumed.
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
    # elfs/priv is the built output tree; build/priv holds intermediates and
    # would double-count. An empty result here means the build did not run --
    # assert rather than measure nothing and report a confident zero, which is
    # exactly what the first version of this script did.
    priv = sorted(glob.glob("/home/jk/Documents/riscv-arch-test/work/"
                            "sail-rv32-max/elfs/priv/**/*.elf", recursive=True))
    assert priv, "no privileged ELFs built -- run the make elfs step first"
    print(f"{len(priv)} privileged ELFs", flush=True)

    per, ok = {}, 0
    for i, e in enumerate(priv):
        if os.path.exists(COV):
            os.remove(COV)
        try:
            r = subprocess.run([SIM, "--config", CFG, e], cwd=SAIL,
                               capture_output=True, text=True, timeout=120)
            good = "SUCCESS" in r.stdout
        except subprocess.TimeoutExpired:
            good = False
        ok += good
        per[e] = cr.parse_spans(COV) if os.path.exists(COV) else set()
        if (i + 1) % 25 == 0:
            print(f"  {i + 1}/{len(priv)} replayed, {ok} passed", flush=True)

    total = {s for s in cr.parse_spans(cr.BRANCH_INFO) if s[0] == "B"}
    A = ({s for s in set().union(*per.values()) if s[0] == "B"} & total) if per else set()
    print(f"\nhand-written privileged: {len(priv)} ELFs, {ok} SUCCESS, "
          f"{len(A)} unique Sail branches", flush=True)

    print("\ntop files they reach:")
    for f, n in collections.Counter(s[1] for s in A).most_common(15):
        print(f"  {n:5d}  {f}")

    json.dump({"act4_priv": sorted("|".join(map(str, s)) for s in A),
               "elfs": len(priv), "passed": ok},
              open(os.path.join(SP, "priv_act4.json"), "w"))
    print(f"\nsaved -> {SP}/priv_act4.json", flush=True)


if __name__ == "__main__":
    main()
