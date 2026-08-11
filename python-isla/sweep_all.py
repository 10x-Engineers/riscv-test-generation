#!/usr/bin/env python3
"""Run opcode_sweep.py across a named set of extensions, both XLENs, and
summarise. This is the batch driver for the coverage-expansion plan's opcode
sweep milestones (M5/M6/M7) -- opcode_sweep.py itself stays a single-extension
tool, since that's what you want when debugging one instruction.

Each entry in EXTENSIONS is (label, [sail files relative to <sail-riscv>/model]).
The label picks opcode_sweep.py's own --extension (output subdirectory and
assembler/Spike march suffix, via its EXTENSION_MARCH), so it has to match a
key there for any extension outside bare I.

Usage:
    python3 sweep_all.py                 # every group
    python3 sweep_all.py M5              # one group
    python3 sweep_all.py M A Zicond      # named extensions
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))

# Grouped by the milestone that owns them (coverage-expansion-plan.md), so a
# group can be run and reported on as a unit.
GROUPS = {
    "M5": [
        ("I", ["extensions/I/base_insts.sail"]),
        ("M", ["extensions/M/mext_insts.sail"]),
        ("A", ["extensions/A/zalrsc_insts.sail", "extensions/A/zaamo_insts.sail"]),
        ("Zifencei", ["extensions/Zifencei/zifencei_insts.sail"]),
        ("Zicond", ["extensions/Zicond/zicond_insts.sail"]),
        ("Zawrs", ["extensions/Zawrs/zawrs_insts.sail"]),
        ("Zicbom", ["extensions/Zicbom/zicbom_insts.sail"]),
        ("Zicbop", ["extensions/Zicbop/zicbop_insts.sail"]),
        ("Zicboz", ["extensions/Zicboz/zicboz_insts.sail"]),
        ("Zihintntl", ["extensions/Zihintntl/zihintntl_insts.sail"]),
        ("Zihintpause", ["extensions/Zihintpause/zihintpause_insts.sail"]),
        ("Zimop", ["mops/Zimop/zimop_insts.sail"]),
        ("Zcmop", ["mops/Zcmop/zcmop_insts.sail"]),
        ("cfi", ["extensions/cfi/zicfilp_insts.sail", "extensions/cfi/zicfiss_insts.sail"]),
    ],
    "M6": [
        ("B", ["extensions/B/zba_insts.sail", "extensions/B/zbb_insts.sail",
               "extensions/B/zbc_insts.sail", "extensions/B/zbs_insts.sail"]),
        ("K", ["extensions/K/zbkb_insts.sail", "extensions/K/zbkx_insts.sail",
               "extensions/K/zkn_insts.sail", "extensions/K/zks_insts.sail"]),
        ("C", ["extensions/C/zca_insts.sail", "extensions/C/zcb_insts.sail"]),
        ("FD", ["extensions/FD/fext_insts.sail", "extensions/FD/dext_insts.sail",
                "extensions/FD/zfh_insts.sail", "extensions/FD/zfa_insts.sail"]),
    ],
    "M7": [
        ("V", ['extensions/V/vext_arith_insts.sail', 'extensions/V/vext_fp_insts.sail', 'extensions/V/vext_fp_red_insts.sail', 'extensions/V/vext_fp_utils_insts.sail', 'extensions/V/vext_fp_vm_insts.sail', 'extensions/V/vext_mask_insts.sail', 'extensions/V/vext_mem_insts.sail', 'extensions/V/vext_red_insts.sail', 'extensions/V/vext_utils_insts.sail', 'extensions/V/vext_vm_insts.sail', 'extensions/V/vext_vset_insts.sail']),
        ("vector_crypto", ['extensions/vector_crypto/zvbb_insts.sail', 'extensions/vector_crypto/zvbc_insts.sail', 'extensions/vector_crypto/zvkg_insts.sail', 'extensions/vector_crypto/zvkned_insts.sail', 'extensions/vector_crypto/zvknhab_insts.sail', 'extensions/vector_crypto/zvksed_insts.sail', 'extensions/vector_crypto/zvksh_insts.sail']),
        ("bfloat16", ['extensions/bfloat16/zfbfmin_insts.sail', 'extensions/bfloat16/zvfbfmin_insts.sail', 'extensions/bfloat16/zvfbfwma_insts.sail']),
    ],
    "privileged": [
        ("Zicsr", ["extensions/Zicsr/zicsr_insts.sail"]),
        ("Svinval", ["extensions/Svinval/svinval_insts.sail"]),
    ],
}

# The line opcode_sweep.py prints for each instruction starts with one of
# these; anything else (its `#` header lines, its summary) isn't a per-
# instruction verdict and isn't counted here.
VERDICTS = ("PASS", "FAIL", "SKIP", "NOENC")


def run_one(label, files, xlen, out_dir, timeout):
    """Run one extension/XLEN, streaming its output line by line as it happens.

    Streaming rather than capture_output, because a sweep of V takes the better
    part of an hour and capturing means nothing is visible until it ends -- so
    `tail -f` on the log shows a blank file for 45 minutes and there is no way
    to tell progress from a hang. Each line is echoed immediately *and*
    collected, since the summary still needs to count verdicts afterwards.
    `-u` on the child keeps its own stdout unbuffered; without it Python
    line-buffers into a pipe and the streaming is defeated at the far end."""
    cmd = [sys.executable, "-u", "opcode_sweep.py", *files,
           "--xlen", str(xlen), "--extension", label, "--out-dir", out_dir]
    t0 = time.time()
    lines = []
    try:
        proc = subprocess.Popen(cmd, cwd=HERE, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, bufsize=1)
        for line in proc.stdout:
            print(line, end="", flush=True)
            lines.append(line)
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        msg = f"!! sweep_all: hit the {timeout}s wall clock for this extension/XLEN\n"
        print(msg, end="", flush=True)
        lines.append(msg)
    return "".join(lines), time.time() - t0


def main():
    argv = sys.argv[1:]
    if not argv:
        wanted = [(g, e) for g in GROUPS for e in GROUPS[g]]
    elif all(a in GROUPS for a in argv):
        wanted = [(g, e) for g in argv for e in GROUPS[g]]
    else:
        by_label = {e[0]: (g, e) for g in GROUPS for e in GROUPS[g]}
        missing = [a for a in argv if a not in by_label]
        if missing:
            sys.exit(f"unknown group/extension: {', '.join(missing)}\n"
                     f"groups: {', '.join(GROUPS)}\n"
                     f"extensions: {', '.join(sorted(by_label))}")
        wanted = [by_label[a] for a in argv]

    out_dir = os.environ.get("SWEEP_OUT", "/tmp/sweep-all")
    timeout = int(os.environ.get("SWEEP_TIMEOUT", "1800"))
    # Per-target result files, so a sweep that dies part-way can be resumed
    # instead of restarted. A full sweep is hours of work and has now been lost
    # twice -- once to a solver allocating without bound and taking the machine
    # with it (opcode_sweep.ISLA_MEM_LIMIT_GIB now prevents that), once to a
    # hard reset. Losing an hour of finished results to an unrelated crash is
    # avoidable, so avoid it.
    results_dir = os.path.join(out_dir, "_results")
    os.makedirs(results_dir, exist_ok=True)
    resume = os.environ.get("SWEEP_RESUME", "1") != "0"
    totals = {}
    for group, (label, files) in wanted:
        for xlen in (32, 64):
            key = f"{label} RV{xlen}"
            done_path = os.path.join(results_dir, f"{label}-rv{xlen}.txt")
            if resume and os.path.exists(done_path):
                with open(done_path) as f:
                    out = f.read()
                lines = [l for l in out.splitlines() if l.startswith(VERDICTS)]
                print(f"\n{'=' * 72}\n== {key}  ({group})  [resumed: {len(lines)} results "
                      f"from a previous run]\n{'=' * 72}", flush=True)
                print(out, end="", flush=True)
                secs = 0
                totals[key] = dict(
                    group=group,
                    passed=sum(1 for l in lines if l.startswith("PASS")),
                    failed=sum(1 for l in lines if l.startswith("FAIL")),
                    skipped=sum(1 for l in lines if l.startswith("SKIP")),
                    noenc=sum(1 for l in lines if l.startswith("NOENC")),
                    secs=secs,
                )
                continue
            print(f"\n{'=' * 72}\n== {key}  ({group})\n{'=' * 72}", flush=True)
            out, secs = run_one(label, files, xlen, out_dir, timeout)
            lines = [l for l in out.splitlines() if l.startswith(VERDICTS)]
            # Written only after the target completes, so a target interrupted
            # half-way is re-run rather than resumed with partial results.
            with open(done_path, "w") as f:
                f.write(out)
            totals[key] = dict(
                group=group,
                passed=sum(1 for l in lines if l.startswith("PASS")),
                failed=sum(1 for l in lines if l.startswith("FAIL")),
                skipped=sum(1 for l in lines if l.startswith("SKIP")),
                noenc=sum(1 for l in lines if l.startswith("NOENC")),
                secs=round(secs),
            )

    print(f"\n\n{'=' * 72}\n== SUMMARY\n{'=' * 72}")
    print(f"{'target':22s} {'group':10s} {'pass':>5s} {'fail':>5s} {'skip':>5s} "
          f"{'noenc':>6s} {'secs':>6s}")
    for key, t in totals.items():
        print(f"{key:22s} {t['group']:10s} {t['passed']:5d} {t['failed']:5d} "
              f"{t['skipped']:5d} {t['noenc']:6d} {t['secs']:6d}")
    tp = sum(t["passed"] for t in totals.values())
    tf = sum(t["failed"] for t in totals.values())
    print(f"\n{tp} passing, {tf} failing across {len(totals)} extension/XLEN targets")


if __name__ == "__main__":
    main()
