#!/usr/bin/env python3
"""Generate and verify the privileged *scenario* tests — the ones that set up
architectural state rather than exercising a single instruction in isolation.

These are milestones M0, M2 and M3 of coverage-expansion-plan.md: privilege
transitions, PMP violations, and Sv39 page-table walks. They were built and
verified individually while being developed; this collects them into one
reproducible corpus so they can be re-run, counted, and — importantly — fed to
coverage_report.py. Measuring coverage without them reported `sys/vmem_ptw.sail`
at 0/60 while a working page-table-walk test existed, which is exactly the kind
of misleading number that makes a coverage figure worse than none.

Every scenario carries its **negative controls**, and they are run and checked
like any other case. That is not decoration: two of these tests passed
vacuously during development and were only caught by constructing the case that
had to fail (see the plan's lesson 4). A control that stops failing is a
regression, so `expect` is part of the test, not commentary.
"""
import argparse
import os
import subprocess
import sys

ISLA_DIR = "/home/jk/Documents/riscv-test-generation/isla-gen-extension"
ISLA_BIN = os.path.join(ISLA_DIR, "target/release/isla-testgen")
Z3_LIB = "/home/jk/.cache/udb/z3/z3-4.16.0/x64"
SAIL_SIM = "/home/jk/Documents/sail-riscv/sail_riscv_sim"
SPIKE = "/home/jk/Documents/riscv/bin/spike"

# Opcode sequences, assembled elsewhere and pinned here so a scenario is a
# fixed, reviewable artefact rather than something re-derived each run.
MRET = ["0x30200073"]
SRET = ["0x10200073"]
WFI = ["0x10500073"]
ECALL = ["0x00000073"]
EBREAK = ["0x00100073"]
# lui x2, 0x80020; slli x2,x2,32; srli x2,x2,32; lw x1, 0(x2)
LOAD_MAPPED = ["0x80020137", "0x02011113", "0x02015113", "0x00012083"]
# lui x2, 0x40000; lw x1, 0(x2)   -- an address outside the Sv39 identity map
LOAD_UNMAPPED = ["0x40000137", "0x00012083"]

# (name, opcodes, flags, expect) -- expect is True if the generated ELF must
# pass on both simulators, False if it must fail.
SCENARIOS = [
    # ---- M0: traps and privilege transitions -----------------------------
    ("m0_ecall", ECALL, ["--expect-trap-cause", "11"], True),
    ("m0_ebreak", EBREAK, ["--expect-trap-cause", "3"], True),
    ("m0_wfi", WFI, ["--pending-interrupt"], True),
    ("m0_mret_to_machine", MRET, ["--preload-xepc", "mepc", "--preload-xpp", "mpp=m"], True),
    ("m0_mret_to_supervisor", MRET,
     ["--preload-xepc", "mepc", "--preload-xpp", "mpp=s", "--pmp-allow-all"], True),
    ("m0_mret_to_user", MRET,
     ["--preload-xepc", "mepc", "--preload-xpp", "mpp=u", "--pmp-allow-all"], True),
    ("m0_sret_to_supervisor", SRET,
     ["--preload-xepc", "sepc", "--preload-xpp", "spp=s", "--pmp-allow-all"], True),
    ("m0_sret_to_user", SRET,
     ["--preload-xepc", "sepc", "--preload-xpp", "spp=u", "--pmp-allow-all"], True),
    # Control: without a PMP entry the post-xRET fetch must fault, because the
    # privilege change really happened. If this ever passes, the privilege
    # transition stopped being real and every result above is worthless.
    ("m0_NEG_mret_to_user_no_pmp", MRET,
     ["--preload-xepc", "mepc", "--preload-xpp", "mpp=u"], False),

    # ---- M2: PMP violation -----------------------------------------------
    ("m2_load_permitted", LOAD_MAPPED, [], True),
    ("m2_load_denied", LOAD_MAPPED,
     ["--pmp-deny", "0x80020000", "--expect-trap-cause", "5", "--trap-is-pass"], True),
    ("m2_NEG_wrong_cause", LOAD_MAPPED,
     ["--pmp-deny", "0x80020000", "--expect-trap-cause", "7", "--trap-is-pass"], False),
    ("m2_NEG_no_violation", LOAD_MAPPED,
     ["--pmp-deny", "0x80028000", "--expect-trap-cause", "5", "--trap-is-pass"], False),

    # ---- M3: Sv39 page-table walk ----------------------------------------
    ("m3_supervisor_bare", LOAD_MAPPED, ["--run-in-supervisor", "--pmp-allow-all"], True),
    ("m3_sv39_mapped", LOAD_MAPPED, ["--run-in-supervisor", "--pmp-allow-all", "--sv39"], True),
    ("m3_sv39_page_fault", LOAD_UNMAPPED,
     ["--run-in-supervisor", "--pmp-allow-all", "--sv39",
      "--expect-trap-cause", "13", "--trap-is-pass"], True),
    # Controls: cause 13 is a *page* fault, reachable only through a real walk.
    # Off, the same access faults differently; on a mapped address it doesn't
    # fault at all. Both must fail, or the Sv39 result proves nothing.
    ("m3_NEG_no_translation", LOAD_UNMAPPED,
     ["--run-in-supervisor", "--pmp-allow-all",
      "--expect-trap-cause", "13", "--trap-is-pass"], False),
    ("m3_NEG_mapped_address", LOAD_MAPPED,
     ["--run-in-supervisor", "--pmp-allow-all", "--sv39",
      "--expect-trap-cause", "13", "--trap-is-pass"], False),
]


def generate(name, opcodes, flags, out_dir, xlen):
    env = dict(os.environ, LD_LIBRARY_PATH=Z3_LIB)
    prefix = os.path.join(out_dir, name)
    cmd = [ISLA_BIN, "-A", f"riscv-ir/riscv{xlen}.ir", "-C", f"riscv-ir/riscv{xlen}.toml",
           "-a", f"riscv{xlen}", "--memory-region", "0x80020000-0x80030000",
           "-o", prefix, "-n", "1", *opcodes, *flags]
    r = subprocess.run(cmd, cwd=ISLA_DIR, env=env, capture_output=True, text=True, timeout=300)
    return prefix + ".elf", r.returncode == 0


def run(elf, xlen):
    if not os.path.exists(elf):
        return None, None
    sail_args = ["--rv32"] if xlen == 32 else []
    s = subprocess.run([SAIL_SIM] + sail_args + [elf], cwd=os.path.dirname(SAIL_SIM),
                       capture_output=True, text=True, timeout=30)
    k = subprocess.run([SPIKE, f"--isa=rv{xlen}imac_zicsr", elf],
                       capture_output=True, text=True, timeout=30)
    return "SUCCESS" in s.stdout, k.returncode == 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-dir", default="/tmp/scenario-tests")
    ap.add_argument("--xlen", type=int, default=64, choices=[32, 64])
    ap.add_argument("--only", help="Comma-separated scenario names.")
    args = ap.parse_args()

    out_dir = os.path.join(args.out_dir, f"rv{args.xlen}")
    os.makedirs(out_dir, exist_ok=True)
    wanted = set(args.only.split(",")) if args.only else None

    good = bad = 0
    for name, opcodes, flags, expect in SCENARIOS:
        if wanted and name not in wanted:
            continue
        # Sv39 is RV64-only (it is a 39-bit virtual address scheme).
        if args.xlen == 32 and "--sv39" in flags:
            print(f"SKIP  {name:32s} Sv39 is RV64-only")
            continue
        elf, gen_ok = generate(name, opcodes, flags, out_dir, args.xlen)
        sail, spike = run(elf, args.xlen)
        if sail is None:
            verdict, detail = "ERROR", "generation produced no ELF"
        else:
            passed = bool(sail and spike)
            verdict = "ok" if passed == expect else "REGRESSION"
            detail = (f"sail={'ok' if sail else 'FAIL'} spike={'ok' if spike else 'FAIL'}"
                      f"  (expected {'pass' if expect else 'FAIL'})")
        good += verdict == "ok"
        bad += verdict != "ok"
        print(f"{verdict:11s} {name:32s} {detail}")

    print(f"\n{good} as expected, {bad} not, RV{args.xlen}")
    print(f"ELFs in {out_dir} -- pass this to coverage_report.py --elf-dir so the "
          f"scenario tests count toward model coverage")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
