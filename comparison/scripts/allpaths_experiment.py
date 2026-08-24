#!/usr/bin/env python3
"""Falsification test for the methodology claim.

Claim: enumerating feasible paths through the Sail model reaches architectural
corner cases that one-test-per-instruction generation reaches only by chance.

Test: generate I+M for RV32 both ways, measure unique Sail branches reached,
compare against the ACT4 hand-written baseline measured earlier
(1181 branches from 47 tests) and our current baseline (1055 from 53).

The claim fails if path enumeration does not materially shrink the 194-branch
deficit. That outcome is reportable, not something to explain away.

Deliberately conservative in one respect: a test that exits nonzero writes no
coverage file at all (finding C13), and roughly half of all-paths tests trap
without an expectation and therefore fail. So the measured gain here is a
LOWER BOUND on what the method yields once per-path trap expectations exist.
"""
import glob
import os
import shutil
import subprocess
import sys

sys.path.insert(0, "/home/jk/Documents/riscv-test-generation/python-isla")
import coverage_report as cr
import model_opcodes
import opcode_sweep
import paths

SP = "/tmp/claude-1000/-home-jk-Documents/6ccf63f4-7ac2-4873-9424-978374156338/scratchpad"
OUT = os.path.join(SP, "allpaths")
XLEN = 32
SAIL_FILES = ["extensions/I/base_insts.sail", "extensions/M/mext_insts.sail"]
CFG = paths.sail_config(XLEN, build="build-coverage")

# `width_mnemonic` (lw/lh/lb/sw/...) is declared in core/types.sail, not in
# base_insts.sail. Without it as a table path, LOAD and STORE come back as
# "unparsed" and the corpus contains no memory instruction at all -- which is
# exactly the class the hypothesis is about. The first run of this experiment
# made that mistake and measured a confident zero on a corpus of pure ALU ops.
TABLE_FILES = ["core/types.sail"]
# Likewise: without `m` in -march, GNU as rejects every M instruction, so
# div/rem -- the other multi-path class -- silently vanished.
MARCH_EXT = "m_zicsr"


def gen(name, opcodes, mode, idx):
    """Generate for one instruction. mode 'one' = -n 1; 'all' = --all-paths-for."""
    prefix = os.path.join(OUT, mode, f"{idx:03d}_{name.replace('.', '_')}")
    os.makedirs(os.path.dirname(prefix), exist_ok=True)
    strs = [f"0x{op:08x}" if (op & 3) == 3 else f"0x{op:04x}" for op in opcodes]
    cmd = [paths.ISLA_BIN, "-A", f"riscv-ir/riscv{XLEN}.ir",
           "-C", f"riscv-ir/riscv{XLEN}.toml", "-a", f"riscv{XLEN}",
           "--memory-region", "0x80020000-0x80030000", "-o", prefix, "-n", "1"]
    if mode == "all":
        # index of the instruction under test within the opcode sequence
        cmd += ["--all-paths-for", str(len(opcodes) - 1)]
    cmd += strs
    env = dict(os.environ, LD_LIBRARY_PATH=paths.Z3_LIB)
    try:
        subprocess.run(cmd, cwd=paths.ISLA_DIR, env=env,
                       capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        pass
    return sorted(glob.glob(prefix + "*.elf"))


def measure(elfs):
    """Absolute per-ELF span sets, each ELF replayed alone."""
    union, ran, passed = set(), 0, 0
    for e in elfs:
        if os.path.exists(cr.COVERAGE_FILE):
            os.remove(cr.COVERAGE_FILE)
        try:
            r = subprocess.run([cr.COVERAGE_SIM, "--config", CFG, e],
                               cwd=paths.SAIL_RISCV, capture_output=True,
                               text=True, timeout=60)
            passed += "SUCCESS" in r.stdout
        except subprocess.TimeoutExpired:
            pass
        ran += 1
        if os.path.exists(cr.COVERAGE_FILE):
            union |= cr.parse_spans(cr.COVERAGE_FILE)
    return union, ran, passed


def main():
    shutil.rmtree(OUT, ignore_errors=True)
    insns, unparsed = model_opcodes.parse_instructions(
        [os.path.join(paths.SAIL_RISCV, "model", f) for f in SAIL_FILES],
        table_paths=[os.path.join(paths.SAIL_RISCV, "model", f) for f in TABLE_FILES])
    print(f"parsed {len(insns)} instructions, {len(unparsed)} unparsed: {unparsed}",
          flush=True)
    mem = sorted(n for n in insns if n[0] in "ls" and len(n) <= 3)
    print(f"memory instructions in corpus: {mem}", flush=True)

    results = {}
    for mode in ("one", "all"):
        elfs = []
        cfg = opcode_sweep.XLEN_CONFIG[XLEN]
        skipped = 0
        # parse_instructions returns a dict keyed by mnemonic. Iterating it
        # directly yields keys, not Insn objects -- and a broad `except` around
        # this loop turns that mistake into a silent zero, which it did once.
        for i, (name, insn) in enumerate(sorted(insns.items())):
            ops, _err = model_opcodes.opcodes_for(
                insn, XLEN, cfg["as_bin"], cfg["objdump_bin"], march_ext=MARCH_EXT)
            if not ops:
                skipped += 1
                continue
            elfs += gen(name, ops, mode, i)
        print(f"  {mode}: {skipped} instructions produced no opcodes, "
              f"{len(elfs)} ELFs generated", flush=True)
        union, ran, passed = measure(elfs)
        total = cr.parse_spans(cr.BRANCH_INFO)
        br = {s for s in (union & total) if s[0] == "B"}
        results[mode] = (br, ran, passed)
        print(f"{mode:4s}: {ran:4d} ELFs, {passed:4d} passed, "
              f"{len(br):5d} unique Sail branches", flush=True)

    one, allp = results["one"][0], results["all"][0]
    print(f"\npath enumeration adds {len(allp - one)} branches, loses {len(one - allp)}")
    print(f"one-per-instruction : {len(one)}")
    print(f"all-paths           : {len(allp)}")
    print(f"union               : {len(one | allp)}")
    import json
    json.dump({m: sorted("|".join(map(str, s)) for s in v[0])
               for m, v in results.items()}, open(os.path.join(SP, "allpaths_spans.json"), "w"))


if __name__ == "__main__":
    main()
