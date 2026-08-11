#!/usr/bin/env python3
"""Everything we currently generate at RV32, against the hand-written privileged set.

Comparing our 18 privileged scenarios alone against their 145 privileged tests
understates what the framework reaches, for the same reason the base-integer
comparison did: their privileged tests boot through shared startup code that
touches CSRs and base instructions, and *our* coverage of that lives in other
corpora -- the instruction sweep and the CSR sweep -- which were not in the
comparison.

So this measures the union of every RV32 corpus we have:
  - instruction tests (I+M)          53 ELFs
  - CSR sweep                       215 ELFs
  - privileged scenarios             18 ELFs

and reports what remains of their privileged coverage after all of it. That
residual is the honest statement of the gap: what we cannot currently reach
with anything we have, rather than what one corpus misses.
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

CORPORA = {
    "instruction tests (I+M)": SP + "/ours-rv32/**/*.elf",
    "CSR sweep": SP + "/csr-rv32/**/*.elf",
    "privileged scenarios": SP + "/ours-priv-rv32/**/*.elf",
}


def measure(elfs):
    union, ok = set(), 0
    for e in elfs:
        if os.path.exists(COV):
            os.remove(COV)
        try:
            r = subprocess.run([SIM, "--config", CFG, e], cwd=SAIL,
                               capture_output=True, text=True, timeout=120)
            ok += "SUCCESS" in r.stdout
        except subprocess.TimeoutExpired:
            pass
        if os.path.exists(COV):
            union |= cr.parse_spans(COV)
    return union, ok


def main():
    total = {s for s in cr.parse_spans(cr.BRANCH_INFO) if s[0] == "B"}
    ours, n = set(), 0
    for label, pat in CORPORA.items():
        elfs = sorted(glob.glob(pat, recursive=True))
        assert elfs, f"no ELFs for {label} ({pat})"
        assert all(os.path.isabs(e) for e in elfs), "simulator runs elsewhere: need absolute paths"
        u, ok = measure(elfs)
        b = {s for s in u if s[0] == "B"} & total
        ours |= b
        n += len(elfs)
        print(f"{label:26s} {len(elfs):4d} ELFs, {ok:4d} pass, {len(b):5d} branches", flush=True)

    a = json.load(open(os.path.join(SP, "priv_act4.json")))
    A = {tuple(k.split("|")) for k in a["act4_priv"]}
    A = {(x[0], x[1], int(x[2]), int(x[3]), int(x[4]), int(x[5])) for x in A}

    print(f"\n{'=' * 64}")
    print(f"{'hand-written privileged':30s} {a['elfs']:5d} tests {len(A):6d} branches")
    print(f"{'everything we generate (RV32)':30s} {n:5d} tests {len(ours):6d} branches")
    print(f"\nof their {len(A)} privileged branches, we reach {len(A & ours)} "
          f"({100.0 * len(A & ours) / len(A):.1f}%)")
    print(f"still out of reach: {len(A - ours)}")
    print(f"we reach that they do not: {len(ours - A)}")

    print("\nwhere the residual lives:")
    for f, k in collections.Counter(s[1] for s in A - ours).most_common(14):
        print(f"  {k:5d}  {f}")

    json.dump({"ours_all": sorted("|".join(map(str, s)) for s in ours)},
              open(os.path.join(SP, "priv_all_ours.json"), "w"))


if __name__ == "__main__":
    main()
