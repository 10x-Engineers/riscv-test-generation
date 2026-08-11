#!/usr/bin/env python3
"""How much of the 194-branch ACT4-only deficit does our existing CSR sweep close?

The attribution showed 184 of 194 come from ACT4's shared startup code, and the
largest block of those decodes to specific CSR addresses -- mostly RV32-only
high halves -- that `csr_sweep.py` already targets by name. The claim to test is
that the deficit is a corpus-scope gap (we never ran that sweep at RV32, and
never folded its output into the comparison) rather than a capability gap.

Measured under two configs, because config choice can silently decide the
answer: an extension that is off makes its CSR access trap, the test exits
nonzero, and a failing test writes no coverage file at all (C13). So both
numbers below are lower bounds, and the smaller one is the conservative claim.
"""
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

CONFIGS = {
    "rv32d_v128_e64 (what 'ours' used)":
        os.path.join(SAIL, "build-coverage/config/rv32d_v128_e64.json"),
    "act4-sail (what ACT4 used)": os.path.join(SP, "act4-sail.json"),
}


def measure(elfs, config):
    union, ok = set(), 0
    for e in elfs:
        if os.path.exists(COV):
            os.remove(COV)
        try:
            r = subprocess.run([SIM, "--config", config, e], cwd=SAIL,
                               capture_output=True, text=True, timeout=60)
            ok += "SUCCESS" in r.stdout
        except subprocess.TimeoutExpired:
            pass
        if os.path.exists(COV):
            union |= cr.parse_spans(COV)
    return union, ok


def main():
    d = json.load(open(os.path.join(SP, "act4_attribution.json")))
    deficit = {tuple(k.split("|")) for k in d["act4_only_reach_counts"]}
    deficit = {(a, b, int(c), int(x), int(y), int(z)) for a, b, c, x, y, z in deficit}

    elfs = sorted(glob.glob(SP + "/csr-rv32/**/*.elf", recursive=True))
    print(f"CSR corpus: {len(elfs)} ELFs, deficit to close: {len(deficit)} branches\n")

    total = {s for s in cr.parse_spans(cr.BRANCH_INFO) if s[0] == "B"}
    for label, cfg in CONFIGS.items():
        union, ok = measure(elfs, cfg)
        br = {s for s in union if s[0] == "B"} & total
        closed = br & deficit
        print(f"{label}")
        print(f"   {ok}/{len(elfs)} ELFs SUCCESS, {len(br)} branches reached")
        print(f"   closes {len(closed)} of {len(deficit)} "
              f"({100.0 * len(closed) / len(deficit):.1f}%)\n", flush=True)


if __name__ == "__main__":
    main()
