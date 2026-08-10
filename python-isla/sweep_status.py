#!/usr/bin/env python3
"""Report how far a running sweep_all.py has got.

Useful for two different situations, which is why it reads the output
directory rather than only the log:

* A sweep started before sweep_all.py streamed its output (it used to capture
  each extension and print it in one go, so the log showed nothing for the
  ~45 minutes a V sweep takes).
* Any sweep where you want a one-line answer rather than scrolling a log.

Progress inside an extension comes from the *newest* generated file. The sweep
iterates instructions in sorted order, so that filename's rank in the sorted
list is how far through it is -- which also tells you whether it is moving or
wedged, since the file's age is the time since the last instruction finished.

Usage:
    python3 sweep_status.py [--out-dir /tmp/sweep-m7b] [--log <path>] [--watch]
"""
import argparse
import glob
import os
import re
import sys
import time

import model_opcodes
import opcode_sweep
import sweep_all

SAIL_MODEL = os.path.join(opcode_sweep.SAIL_RISCV_ROOT, "model")


def instruction_count(label):
    """How many instructions the sweep will attempt for this extension, from
    the same parse the sweep itself does -- so the denominator is real rather
    than a guess that drifts as the parser improves."""
    files = dict((e[0], e[1]) for g in sweep_all.GROUPS for e in sweep_all.GROUPS[g]).get(label)
    if not files:
        return None
    paths = [os.path.join(SAIL_MODEL, f) for f in files]
    insns, _ = model_opcodes.parse_instructions(
        paths, table_paths=opcode_sweep._shared_table_paths(SAIL_MODEL, paths))
    return sorted(insns)


def current_section(log_path):
    """(label, xlen) of the section the sweep is in, or None."""
    if not log_path or not os.path.exists(log_path):
        return None
    section = None
    for line in open(log_path):
        m = re.match(r"== (\S+) RV(\d+)", line)
        if m:
            section = (m.group(1), int(m.group(2)))
    return section


def report(out_dir, log_path):
    running = os.popen("ps -ef | grep '[s]weep_all.py' | wc -l").read().strip() != "0"
    section = current_section(log_path)
    if not section:
        print("no section started yet" if running else "not running, no log")
        return
    label, xlen = section
    files = glob.glob(os.path.join(out_dir, label, "*.elf"))
    if not files:
        print(f"{label} RV{xlen}: starting up ({'running' if running else 'STOPPED'})")
        return
    newest = max(files, key=os.path.getmtime)
    # The sweep writes `name.replace('.', '_')`, so map back to compare against
    # the parsed mnemonics.
    stem = os.path.basename(newest)[: -len(".elf")]
    names = instruction_count(label) or []
    rank = next((i + 1 for i, n in enumerate(names) if n.replace(".", "_") == stem), None)
    age = int(time.time() - os.path.getmtime(newest))
    pos = f"{rank}/{len(names)}" if rank else f"?/{len(names) or '?'}"
    print(f"{label} RV{xlen}: {pos}  last={stem} ({age}s ago)  "
          f"{'running' if running else 'STOPPED'}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-dir", default="/tmp/sweep-m7b")
    ap.add_argument("--log", default=None, help="sweep_all.py's log file")
    ap.add_argument("--watch", action="store_true", help="refresh every 30s")
    args = ap.parse_args()
    while True:
        report(args.out_dir, args.log)
        if not args.watch:
            return
        sys.stdout.flush()
        time.sleep(30)


if __name__ == "__main__":
    main()
