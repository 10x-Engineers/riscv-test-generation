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

import paths

SAIL_RISCV = paths.SAIL_RISCV
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


def replay(elf_dirs, limit=None, timeout=20, per_elf=None):
    """Replay every ELF, appending into one `sail_coverage`.

    `per_elf`, when given a dict, additionally records each ELF's *own* span
    set. The RFP asks for "a measure of coverage of an individual ELF and for
    all the ELFs in a generated test-suite" (Goal 6), and the merged file
    answers only the second half -- once run N has appended, there is no way to
    ask what run N alone reached.

    Doing this needs the coverage file truncated between ELFs, which is why it
    is opt-in: it costs one extra parse per ELF, and on a corpus of ~1900 that
    is not free.
    """
    elfs = sorted({p for d in elf_dirs for p in glob.glob(os.path.join(d, "**", "*.elf"),
                                                          recursive=True)})
    if limit:
        elfs = elfs[:limit]
    if os.path.exists(COVERAGE_FILE):
        os.remove(COVERAGE_FILE)
    cumulative = set()
    ok = fail = 0
    t0 = time.time()
    for i, elf in enumerate(elfs, 1):
        if per_elf is not None and os.path.exists(COVERAGE_FILE):
            os.remove(COVERAGE_FILE)   # each ELF measured on its own, see below
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
        if per_elf is not None:
            # This ELF's own *absolute* coverage, which is what "coverage of an
            # individual ELF" has to mean. Diffing against a running cumulative
            # set instead would measure marginal contribution in replay order --
            # under which the first ELF is credited with the entire shared boot
            # prelude and an identical ELF later scores zero. Order-dependent,
            # and not the question the RFP asks.
            #
            # So truncate before each run. The merged total is not lost: it is
            # the union, accumulated here and returned.
            spans = parse_spans(COVERAGE_FILE) if os.path.exists(COVERAGE_FILE) else set()
            per_elf[elf] = spans
            cumulative |= spans
            if os.path.exists(COVERAGE_FILE):
                os.remove(COVERAGE_FILE)
        if i % 200 == 0:
            print(f"  ... {i}/{len(elfs)} replayed ({round(time.time()-t0)}s)", flush=True)
    print(f"replayed {len(elfs)} ELFs: {ok} reached SUCCESS, {fail} did not "
          f"({round(time.time()-t0)}s)")
    return cumulative if per_elf is not None else None
    return len(elfs)



def report_per_elf(per_elf, suite_covered, dest):
    """Per-ELF coverage, plus the check that it reconciles with the suite total.

    The reconciliation is part of the output, not a follow-up: a per-ELF
    capture that silently drops data would otherwise look exactly like a corpus
    with a lot of redundant tests. If the union of the parts does not equal the
    whole, the number is wrong and the report says so rather than printing a
    plausible table.
    """
    # Scope-filter each ELF's set the same way the suite total was filtered,
    # or a scoped run would compare unlike with unlike and always mismatch.
    per_elf = {e: v & suite_covered for e, v in per_elf.items()}
    union = set().union(*per_elf.values()) if per_elf else set()
    ranked = sorted(per_elf.items(), key=lambda kv: -len(kv[1]))

    print(f"\n{'=' * 74}\nper-ELF coverage (RFP Goal 6: individual ELF, not just the suite)\n{'=' * 74}")
    print(f"{len(per_elf)} ELFs replayed; {sum(1 for _e, v in ranked if v)} contributed at least one span")
    print(f"\n{'spans':>7s}  ELF (its own coverage, measured alone)")
    for elf, spans in ranked[:15]:
        print(f"{len(spans):7d}  {os.path.relpath(elf, os.path.expanduser('~/.cache/riscv-sweep'))}")

    # Marginal contribution, computed greedily: what each ELF adds once the
    # better ones are already in. This is the number a coverage-guided
    # generator wants -- absolute coverage is dominated by shared boot code and
    # ranks near-duplicates identically.
    seen, marginal = set(), []
    for elf, spans in ranked:
        marginal.append((elf, len(spans - seen)))
        seen |= spans
    redundant = [e for e, n in marginal if n == 0]
    top_marginal = [(e, n) for e, n in marginal if n][:8]
    if top_marginal:
        print(f"\n{'added':>7s}  greedy marginal contribution (what each adds after the ones above)")
        for elf, n in top_marginal:
            print(f"{n:7d}  {os.path.relpath(elf, os.path.expanduser('~/.cache/riscv-sweep'))}")
    if redundant:
        print(f"\n{len(redundant)} of {len(per_elf)} ELFs add nothing once the others are in. "
              f"Expected for\nnear-duplicate tests, and exactly the signal a "
              f"coverage-guided generator needs.")

    ok = union == suite_covered
    print(f"\nreconciliation: union of per-ELF spans {'==' if ok else '!='} suite total "
          f"({len(union)} vs {len(suite_covered)})")
    if not ok:
        print("  MISMATCH -- the per-ELF capture is dropping data; do not quote these numbers.")

    if dest and dest != "-":
        with open(dest, "w") as f:
            f.write("# spans\tELF -- each ELF's own contribution, not cumulative\n")
            for elf, spans in ranked:
                f.write(f"{len(spans)}\t{elf}\n")
        print(f"wrote per-ELF coverage to {dest}")


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
    ap.add_argument("--per-elf", metavar="FILE", nargs="?", const="-",
                    help="Also report each ELF's own coverage, not just the "
                         "suite total -- the other half of RFP Goal 6. Writes "
                         "`spans<TAB>path` sorted by contribution, or prints a "
                         "summary if no file is given. Requires a replay.")
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

    per_elf = {} if args.per_elf else None
    merged = None
    if not args.no_replay:
        merged = replay(args.elf_dir or DEFAULT_ELF_DIRS, args.limit, per_elf=per_elf)
    elif args.per_elf:
        sys.exit("--per-elf needs a real replay; it cannot be derived from an "
                 "already-merged sail_coverage file.")

    total = parse_spans(BRANCH_INFO)
    # In per-ELF mode the coverage file is truncated between runs, so the union
    # accumulated during replay is the merged total rather than the file.
    covered = (merged if merged is not None else parse_spans(COVERAGE_FILE)) & total

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

    if per_elf is not None:
        report_per_elf(per_elf, covered, args.per_elf)


if __name__ == "__main__":
    main()
