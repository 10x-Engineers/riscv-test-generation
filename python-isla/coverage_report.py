#!/usr/bin/env python3
"""Measure how much of the Sail model the generated test corpus actually
exercises, by replaying every generated ELF on a coverage-instrumented
sail_riscv_sim.

This answers a different question from the sweep's pass/fail counts, and the
more important one for a test-*generation* project: "1800 instructions pass" says
the tests run and agree with Spike, but says nothing about how much of the golden
model's behaviour they reach. A sweep that exercises one legal instance of every
instruction can still miss most of the model's branches -- every error path, every
width/rounding-mode/privilege variant.

Mechanics: the coverage build (`cmake -DCOVERAGE=ON`) makes the emulator append a
line per executed span to `sail_coverage` in its working directory, and emits the
full set of instrumented spans once at build time as
`<build>/sail_riscv_model.branch_info`. Coverage is the ratio of the first to the
second. The two formats differ by one field -- branch_info carries a span index
that sail_coverage doesn't -- so the index is dropped before comparing.

Spans are of three kinds, kept separate because they mean different things:
  F  function entered
  B  branch taken
  T  ("taken") the finer-grained per-expression spans Sail emits

Usage:
    python3 coverage_report.py                      # replay everything, report
    python3 coverage_report.py --no-replay          # report from existing sail_coverage
    python3 coverage_report.py --elf-dir /tmp/sweep-m7b
"""
import argparse
import collections
import glob
import os
import re
import subprocess
import sys
import time

SAIL_RISCV = "/home/jk/Documents/sail-riscv"
COVERAGE_SIM = os.path.join(SAIL_RISCV, "build-coverage/c_emulator/sail_riscv_sim")
BRANCH_INFO = os.path.join(SAIL_RISCV, "build-coverage/sail_riscv_model.branch_info")
COVERAGE_FILE = os.path.join(SAIL_RISCV, "sail_coverage")

# Must be the same configs opcode_sweep.py generates against -- see its
# SAIL_CONFIG and findings C8 on why VLEN is pinned to 128.
SAIL_CONFIG = {
    32: os.path.join(SAIL_RISCV, "build/config/rv32d_v128_e64.json"),
    64: os.path.join(SAIL_RISCV, "build/config/rv64d_v128_e64.json"),
}

# The scenario tests belong here as much as the opcode sweeps do, and leaving
# them out is actively misleading: without them this reported
# `sys/vmem_ptw.sail` at 0/60 while a verified page-table-walk test existed.
# Under ~/.cache, not /tmp: a full sweep takes hours, and losing the corpus to
# a reboot or a tmp cleaner means the coverage number cannot be reproduced
# without re-running the whole thing. That happened.
_CORPUS = os.path.expanduser("~/.cache/riscv-sweep")
DEFAULT_ELF_DIRS = [os.path.join(_CORPUS, "elfs"),
                    os.path.join(_CORPUS, "csr-sweep"),
                    # The model-derived CSR sweep writes here. Omitting it meant
                    # 169 CSRs x 6 instructions x 2 XLENs of privileged tests
                    # contributed nothing to the measured coverage -- the
                    # `sys/vmem_ptw.sail` mistake again, one directory over.
                    os.path.join(_CORPUS, "csr-model"),
                    os.path.join(_CORPUS, "scenario-tests"),
                    os.path.join(_CORPUS, "oracle")]

# The two files spell the same span differently, and the three kinds differ
# again, so the leading fields are all optional and repeatable:
#   branch_info    F 0, "neq_int", "flow.sail", 78, 26, 78, 48   (index + name)
#                  B 1, "hex_bits.sail", 69, 44, 69, 58          (index)
#                  T 1, 2, "vext.sail", 754, 84, 754, 99         (*two* indices)
#   sail_coverage  T "vext.sail", 754, 84, 754, 99               (no index)
# Allowing exactly one leading index silently dropped every T span, which is
# 16103 of the manifest's located spans -- the largest kind by some margin.
_SPAN = re.compile(r'^([FBT])\s+(?:\d+,\s*)*(?:"[^"]*\.sail",\s*)?(?:"[^"]*",\s*)?'
                   r'"?([^",]*)"?,\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+)')


def parse_spans(path):
    """{(kind, file, l1, c1, l2, c2)} from a branch_info or sail_coverage file."""
    spans = set()
    with open(path) as f:
        for line in f:
            m = _SPAN.match(line)
            if not m:
                continue
            kind, fname = m.group(1), m.group(2)
            # Placeholder rows with no source location (`B 0, "", 0, 0, 0, 0`)
            # are not reachable code and would only deflate the denominator.
            if not fname:
                continue
            spans.add((kind, fname) + tuple(int(g) for g in m.groups()[2:]))
    return spans


def replay(elf_dirs, limit=None, timeout=20):
    elfs = sorted({p for d in elf_dirs for p in glob.glob(os.path.join(d, "**", "*.elf"),
                                                          recursive=True)})
    if limit:
        elfs = elfs[:limit]
    if os.path.exists(COVERAGE_FILE):
        os.remove(COVERAGE_FILE)
    ok = fail = 0
    t0 = time.time()
    for i, elf in enumerate(elfs, 1):
        # Replay under the *same platform config the corpus was generated for*,
        # not the emulator's build default. VLEN is the one that bites: the IR
        # and the sweep are pinned to 128 (findings C8), and replaying vector
        # tests at a different VLEN makes them fail -- which silently subtracts
        # their coverage rather than reporting an error.
        #
        # RV32 ELFs need the RV32 config; the default is RV64. Rather than track
        # which sweep produced which, try RV64 and fall back -- a mismatch fails
        # fast and costs nothing, and a wrong-XLEN run would contribute
        # misleading coverage.
        for args in ([["--config", c] for c in (SAIL_CONFIG[64], SAIL_CONFIG[32])
                      if os.path.exists(c)] or [[], ["--rv32"]]):
            try:
                r = subprocess.run([COVERAGE_SIM] + args + [elf], cwd=SAIL_RISCV,
                                   capture_output=True, text=True, timeout=timeout)
                if "SUCCESS" in r.stdout:
                    ok += 1
                    break
            except subprocess.TimeoutExpired:
                pass
        else:
            fail += 1
        if i % 200 == 0:
            print(f"  ... {i}/{len(elfs)} replayed ({round(time.time()-t0)}s)", flush=True)
    print(f"replayed {len(elfs)} ELFs: {ok} reached SUCCESS, {fail} did not "
          f"({round(time.time()-t0)}s)")
    return len(elfs)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--elf-dir", action="append", default=None)
    ap.add_argument("--no-replay", action="store_true",
                    help="Report from the existing sail_coverage rather than re-running.")
    ap.add_argument("--limit", type=int, help="Replay only the first N ELFs (for a quick check).")
    ap.add_argument("--top", type=int, default=18, help="How many per-file rows to show.")
    ap.add_argument("--uncovered", metavar="FILE",
                    help="Write every uncovered span as `kind file:line:col` -- the "
                         "actionable list for closing branch coverage, as opposed to "
                         "the percentage, which says how far there is to go but not "
                         "what to write next.")
    ap.add_argument("--scope", metavar="FILE",
                    help="Restrict the report to model files matching any newline- or "
                         "comma-separated prefix in this file (e.g. the RFP's "
                         "deliverable extension list). A model-wide percentage counts "
                         "the Hypervisor and other out-of-scope code we are not "
                         "contracted to cover, which understates delivered coverage.")
    args = ap.parse_args()

    for path, what in ((COVERAGE_SIM, "coverage-instrumented emulator"),
                       (BRANCH_INFO, "branch_info manifest")):
        if not os.path.exists(path):
            sys.exit(f"missing {what}: {path}\n"
                     f"build it with: cmake -B build-coverage -DCOVERAGE=ON && cmake --build build-coverage")

    if not args.no_replay:
        replay(args.elf_dir or DEFAULT_ELF_DIRS, args.limit)

    total = parse_spans(BRANCH_INFO)
    covered = parse_spans(COVERAGE_FILE) & total

    scope_label = "whole model"
    if args.scope:
        with open(args.scope) as f:
            entries = [p.strip() for line in f
                       for p in line.split("#", 1)[0].split(",") if p.strip()]
        # A leading `-` excludes. Needed because the includes are directory
        # prefixes: `sys/` legitimately covers the privileged core, but also
        # swept in `sys/simple_interrupt_generator.sail`, a model-only test
        # device the scope file's own comments said was excluded. The comments
        # and the actual filter disagreed, and the filter is what counts.
        prefixes = [p for p in entries if not p.startswith("-")]
        excludes = [p[1:] for p in entries if p.startswith("-")]
        if prefixes:
            in_scope = lambda s: (any(s[1].startswith(p) for p in prefixes)
                                  and not any(s[1].startswith(x) for x in excludes))
            total = {s for s in total if in_scope(s)}
            covered = {s for s in covered if in_scope(s)}
            scope_label = f"{len(prefixes)} scoped path prefixes from {os.path.basename(args.scope)}"
    if not total:
        sys.exit("no instrumented spans in scope -- check the --scope prefixes "
                 "against the paths in branch_info")

    if args.uncovered:
        # Sorted by file then line so the output reads as a work list, and
        # diffs cleanly between runs to show what a new test actually closed.
        uncovered = sorted(total - covered, key=lambda s: (s[1], s[2], s[3]))
        with open(args.uncovered, "w") as f:
            f.write(f"# {len(uncovered)} uncovered spans ({scope_label})\n")
            f.write("# kind file:line:col   F=function B=branch T=expression\n")
            for kind, fname, line, col, *_ in uncovered:
                f.write(f"{kind} {fname}:{line}:{col}\n")
        print(f"wrote {len(uncovered)} uncovered spans to {args.uncovered}")

    print(f"\n{'=' * 74}\nSail model coverage from the generated corpus\n{'=' * 74}")
    print(f"{'span kind':12s} {'covered':>9s} {'total':>9s} {'%':>7s}")
    for kind, label in (("F", "functions"), ("B", "branches"), ("T", "expressions")):
        c = sum(1 for s in covered if s[0] == kind)
        t = sum(1 for s in total if s[0] == kind)
        print(f"{label:12s} {c:9d} {t:9d} {100*c/t if t else 0:6.1f}%")
    print(f"{'ALL':12s} {len(covered):9d} {len(total):9d} {100*len(covered)/len(total):6.1f}%")

    # Per-file, model sources only. The Sail *library* (flow.sail, hex_bits.sail
    # and friends, which live under the opam share dir) is instrumented too, but
    # its coverage says nothing about how well the RISC-V model is tested.
    by_file = collections.Counter()
    tot_file = collections.Counter()
    for s in total:
        if not s[1].startswith("/"):
            tot_file[s[1]] += 1
    for s in covered:
        if not s[1].startswith("/"):
            by_file[s[1]] += 1
    print(f"\n{'-' * 74}\nleast-covered model files (of {len(tot_file)} with instrumented spans)\n{'-' * 74}")
    rows = sorted(tot_file.items(), key=lambda kv: by_file[kv[0]] / kv[1])
    for fname, t in rows[:args.top]:
        c = by_file[fname]
        print(f"  {100*c/t:5.1f}%  {c:5d}/{t:<5d}  {fname}")
    lib_t = sum(1 for s in total if s[1].startswith("/"))
    lib_c = sum(1 for s in covered if s[1].startswith("/"))
    print(f"\n(excluded from the per-file table: {lib_c}/{lib_t} spans in the Sail "
          f"standard library, which measures the library rather than the model)")


if __name__ == "__main__":
    main()
