#!/usr/bin/env python3
"""Sweep Zicsr's six CSR instructions across real named CSRs, one CSR at a
time. This is milestone M1 of coverage-expansion-plan.md.

Why this exists as its own driver: roughly ten of the tracker's privileged
extensions -- PMP, Smstateen/Ssstateen, Sstc, Sscofpmf, Zicntr/Zihpm, Zkr,
Ssqosid, Sscounterenw, Smcntrpmf -- define no mnemonics at all. They are
reached exclusively through `csrrw`/`csrrs`/`csrrc` (and their immediate
forms) against one specific CSR address. So "cover Zicsr" and "cover those ten
extensions" are different jobs: the first needs the six instructions once, the
second needs them once per CSR. opcode_sweep.py does the first; this does the
second by driving it repeatedly through its --csr flag.

Two kinds of expectation, both real tests:

* read/write CSRs -- the access should simply succeed.
* read-only CSRs (every counter in Zicntr/Zihpm) -- a *write* to them must
  raise illegal-instruction, and since this framework always renders a nonzero
  rs1/uimm, all six instructions are writes. Those are run with
  --expect-trap-cause 2, so the test passes only if the model actually rejects
  the write. Running them the other way would report "fail" for correct
  behaviour, which is worse than not testing them.
"""
import argparse
import re
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))

ILLEGAL_INSTRUCTION = 2  # mcause for a write to a read-only CSR

# CSRs that advance on their own as instructions retire. The harness compares
# final state against the one value isla solved, which such a register can
# never match however correct the implementation -- so these are run with
# mcycle/minstret frozen (isla-testgen's --inhibit-counters). Diagnosed rather
# than assumed: they failed all six instructions on Sail *and* Spike
# identically, and two independent implementations agreeing is the tell that
# the expectation was wrong, not them.
SELF_UPDATING = {"mcycle", "minstret", "cycle", "instret", "time", "hpmcounter3",
                 "mhpmcounter3"}

# (extension, csr name, address, expected mcause or None, spike isa suffix).
#
# The last column matters more than it looks: Spike does not enable every
# extension by default, and a CSR belonging to one it wasn't told about reads
# as an illegal instruction -- indistinguishable, from the outside, from the
# model getting it wrong. Confirmed on `stimecmp`, which passed all six
# instructions on Sail and failed all six on Spike until `--isa=...sstc` was
# added. Each suffix here was checked against the real Spike binary before
# being used ("" where Spike has no such name, e.g. Ssstateen, which it folds
# into Smstateen).
#
# Addresses are the
# privileged spec's, and each is a CSR the Sail model actually implements --
# checked against the model's own `csr_name_map` clauses rather than taken
# from the spec alone, since an address the model doesn't implement would
# trap for that reason instead and prove nothing about the extension.
def model_csrs(model_dir="/home/jk/Documents/sail-riscv/model"):
    """Every CSR the model names, from its own `csr_name_map` clauses.

    The hand-written CSRS list below covers 37 registers. The model names
    **343**, and the omissions were not obscure -- `mstatus`, `misa`, `mtvec`,
    `medeleg`, `mideleg`, `sstatus`, `sie`, `sip`, `sepc`, `scause` were all
    missing. That is most of the privileged architecture, and it is why
    `core/sys_regs.sail` sat at 64% with its uncovered branches being `misa`
    write legalisation, a CSR the sweep never touched.

    Deriving the list the way model_opcodes.py derives instructions keeps it
    honest: a CSR added to the model is swept without anyone remembering to
    add it here.

    The counter banks (`hpmcounter3..31`, `mhpmevent3..31` and their high
    halves) are 180 of the 343 and are structurally identical to one another,
    so `--counter-samples` keeps a couple rather than all of them; sweeping
    every one triples the runtime to re-test the same code path.
    """
    import glob
    addrs = {}
    for f in glob.glob(os.path.join(model_dir, "**", "*.sail"), recursive=True):
        try:
            text = open(f, errors="ignore").read()
        except OSError:
            continue
        for m in re.finditer(
                r'mapping clause csr_name_map\s*=\s*0x([0-9A-Fa-f]{3})\s*<->\s*"([^"]+)"',
                text):
            addrs["0x" + m.group(1).lower()] = m.group(2)
    return addrs


# Counter/event banks: structurally identical repeats of one code path.
_COUNTER_BANK = re.compile(r'^(m|s)?hpm(counter|event)\d+h?$|^(cycle|time|instret)h?$')

# CSRs that live behind mstatus.FS / mstatus.VS. Both reset to Off, and while
# they are Off *accessing* these registers is an illegal instruction -- so a
# sweep that does not enable the unit tests nothing except that the model
# correctly refuses. They failed 0/6 identically on Sail and Spike, which is
# the same "two implementations agreeing means the expectation is wrong"
# signal as findings C1.
# Architecturally read-only, so every instruction this framework renders (all
# six are writes, since rs1/uimm is always nonzero) must raise illegal-
# instruction. Expecting success reports correct behaviour as a failure -- the
# same mistake the unprivileged counters needed fixing for.
#
# The 0xf1x machine-information block is read-only by definition, not by
# configuration, so this is safe to assert unconditionally.
READ_ONLY_CSRS = {"mvendorid", "marchid", "mimpid", "mhartid", "mconfigptr"}

FP_CSRS = {"fcsr", "fflags", "frm"}
VECTOR_CSRS = {"vcsr", "vstart", "vxrm", "vxsat", "vl", "vtype", "vlenb"}

# ...and Spike has to be told the unit exists too. Enabling mstatus.FS fixed
# Sail (0/6 -> 6/6) while Spike stayed at 0/6, because its default ISA string
# here is rv64imac -- no `f`, so `fcsr` is an unimplemented CSR to it. Exactly
# the `stimecmp` lesson: an extension Spike wasn't told about is
# indistinguishable, from outside, from the model getting it wrong.
_UNIT_ISA = {"fp": "f_d", "vector": "v"}

# High-half CSRs: on RV64 each of these is a single 64-bit register and the
# `h` address is *not implemented*, so an access must raise illegal-instruction.
# Expecting success there reports correct behaviour as a failure -- the same
# mistake as sweeping a read-only counter as though it were writable.
_HIGH_HALF = re.compile(r"h$")


def _is_high_half(name, addrs):
    """True if `name` is the RV32 high half of a CSR the model also names."""
    return bool(_HIGH_HALF.search(name)) and name[:-1] in addrs.values()


# CSRs whose value the *harness itself* depends on. Writing an arbitrary value
# to one of these breaks the test around the instruction under test, so the
# resulting failure says nothing about the model -- it is the same class of
# wrong expectation as the self-updating counters (findings C1), and reporting
# it as a failure would be worse than not testing it.
#
# Observed, not assumed:
#   mtvec  writing it replaces the trap vector the preamble installed, so the
#          harness's own trap handling goes somewhere else. 0/6, both sims.
#   misa   writing it can *disable extensions the harness needs* -- the run
#          ends with "possible trap loop detected with MEPC=0x80010038",
#          which is the model correctly refusing instructions that are no
#          longer enabled.
#
# Testing these properly needs a scenario that restores the register before the
# harness relies on it again, in the style of scenario_tests.py. Listed here so
# the gap is explicit rather than showing up as a mystery failure.
HARNESS_HAZARD = {
    "mtvec": "writing it replaces the harness's own trap vector",
    "stvec": "same, for the supervisor trap vector",
    "misa": "writing it can disable extensions the harness needs to keep running",
}


CSRS = [
    # Physical Memory Protection. Prior work (isla-gen-extension/PMP_PLAN.md)
    # unblocked PMP CSR access at the isla-lib level and documented a real
    # Sail-vs-Spike reset-value mismatch -- expect that to show up here as
    # Sail passing where Spike doesn't, rather than treating it as new.
    ("PMP", "pmpcfg0", "0x3a0", None, ""),
    ("PMP", "pmpaddr0", "0x3b0", None, ""),
    ("PMP", "pmpaddr1", "0x3b1", None, ""),
    # Smstateen / Ssstateen.
    ("Smstateen", "mstateen0", "0x30c", None, "smstateen"),
    ("Ssstateen", "sstateen0", "0x10c", None, "smstateen"),
    # Sstc -- supervisor timer compare.
    # `zicntr` as well as `sstc`: Spike's Sstc path reaches the `time` CSR, and
    # without Zicntr enabled it doesn't fail cleanly, it dumps core.
    ("Sstc", "stimecmp", "0x14d", None, "zicntr_sstc"),
    # Sscounterenw / counter enables.
    ("Sscounterenw", "mcounteren", "0x306", None, ""),
    ("Smcntrpmf", "mcountinhibit", "0x320", None, "smcntrpmf"),
    # Smcntrpmf -- counter mode filtering.
    ("Smcntrpmf", "mcyclecfg", "0x321", None, "smcntrpmf"),
    ("Smcntrpmf", "minstretcfg", "0x322", None, "smcntrpmf"),
    # Sscofpmf -- overflow/filtering.
    ("Sscofpmf", "scountovf", "0xda0", ILLEGAL_INSTRUCTION, "sscofpmf"),
    ("Sscofpmf", "mhpmevent3", "0x323", None, "sscofpmf"),
    # Zicntr / Zihpm. The unprivileged counter CSRs are read-only, so every
    # rendered instance is an illegal write -- see the module docstring.
    ("Zicntr", "cycle", "0xc00", ILLEGAL_INSTRUCTION, "zicntr"),
    ("Zicntr", "time", "0xc01", ILLEGAL_INSTRUCTION, "zicntr"),
    ("Zicntr", "instret", "0xc02", ILLEGAL_INSTRUCTION, "zicntr"),
    ("Zihpm", "hpmcounter3", "0xc03", ILLEGAL_INSTRUCTION, "zihpm"),
    # ...and their writable machine-level counterparts, which are not.
    ("Zihpm", "mhpmcounter3", "0xb03", None, "zihpm"),
    ("Zicntr", "mcycle", "0xb00", None, "zicntr"),
    ("Zicntr", "minstret", "0xb02", None, "zicntr"),
    # Zkr -- entropy source. Expected to fail generation, and the reason is
    # worth keeping visible rather than dropping the row: the model's `seed`
    # read calls `get_16_random_bits`, a foreign function isla-lib has no
    # implementation for ("Function get_16_random_bits does not exist"), so
    # symbolic execution can't get past it. That's a missing primop in the
    # generator, not a model or encoding problem -- and not something a
    # different CSR address or march string will fix.
    ("Zkr", "seed", "0x015", None, "zkr"),
    # Ssqosid -- resource management config.
    ("Ssqosid", "srmcfg", "0x181", None, "ssqosid"),
    # Machine trap-handling CSRs: not an "extension" row in the tracker, but
    # the ones every trap test depends on, and never swept as CSR *operands*.
    ("base", "mscratch", "0x340", None, ""),
    ("base", "mepc", "0x341", None, ""),
    ("base", "mcause", "0x342", None, ""),
    ("base", "mtval", "0x343", None, ""),
    ("base", "mie", "0x304", None, ""),
    ("base", "mip", "0x344", None, ""),
    ("base", "sscratch", "0x140", None, ""),
    ("base", "satp", "0x180", None, ""),

    # RV32-only high halves. On RV64 each of these is a *single* 64-bit CSR and
    # the high-half address is not implemented, so these only mean anything at
    # XLEN=32 -- and this sweep had only ever been run at XLEN=64, which is why
    # `postlude/csr_end.sail` was sitting at 69% with its uncovered branches
    # being almost entirely `xlen == 32` guards:
    #
    #   (0x31A, _, _) => currentlyEnabled(Ext_U) & (xlen == 32),   // menvcfgh
    #   (0x312, _, _) => currentlyEnabled(Ext_S) & xlen == 32,     // medelegh
    #   (0x721, _, _) => currentlyEnabled(Ext_Smcntrpmf) & xlen == 32,
    #
    # A whole class of model behaviour was unreachable not because it was hard
    # but because the sweep was only ever pointed at one XLEN.
    ("base", "menvcfgh", "0x31a", None, ""),
    ("base", "medelegh", "0x312", None, ""),
    ("base", "mstatush", "0x310", None, ""),
    ("base", "mseccfgh", "0x757", None, ""),
    ("Smcntrpmf", "mcyclecfgh", "0x721", None, "smcntrpmf"),
    ("Smcntrpmf", "minstretcfgh", "0x722", None, "smcntrpmf"),
    ("base", "mseccfg", "0x747", None, ""),
    ("base", "henvcfgh", "0x61a", None, ""),
]


def run(csr_addr, xlen, out_dir, expect_trap, isa_suffix, inhibit, timeout,
        enable_fp=False, enable_vector=False):
    cmd = [sys.executable, "opcode_sweep.py", "extensions/Zicsr/zicsr_insts.sail",
           "--xlen", str(xlen), "--extension", "Zicsr", "--csr", csr_addr,
           "--out-dir", out_dir]
    if isa_suffix:
        cmd += ["--extra-march", isa_suffix]
    if inhibit:
        cmd += ["--inhibit-counters"]
    if enable_fp:
        cmd += ["--enable-fp"]
    if enable_vector:
        cmd += ["--enable-vector"]
    if expect_trap is not None:
        cmd += ["--expect-trap-cause", str(expect_trap)]
    try:
        p = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True, timeout=timeout)
        return p.stdout + p.stderr
    except subprocess.TimeoutExpired:
        return f"!! timed out after {timeout}s"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--xlen", type=int, default=64, choices=[32, 64])
    ap.add_argument("--out-dir", default="/tmp/csr-sweep")
    ap.add_argument("--timeout", type=int, default=900,
                    help="Per-CSR wall clock. Generous by default: some CSRs are far slower "
                         "to solve than others (pmpcfg0 is a struct-typed CSR and takes "
                         "minutes where mscratch takes seconds), and a too-tight limit "
                         "reports that as a failure rather than as slowness.")
    ap.add_argument("--only", help="Comma-separated CSR names, instead of all of them.")
    ap.add_argument("--no-resume", dest="resume", action="store_false",
                    help="Re-run every CSR instead of reusing cached per-CSR results.")
    ap.add_argument("--from-model", action="store_true",
                    help="Sweep every CSR the model names, not just the curated "
                         "list. The curated list is 37 of the model's 343 and was "
                         "missing mstatus/misa/mtvec/medeleg/sstatus among others.")
    ap.add_argument("--counter-samples", type=int, default=2,
                    help="With --from-model, how many of each structurally "
                         "identical counter/event bank register to keep.")
    args = ap.parse_args()

    csrs = list(CSRS)
    model = model_csrs()
    if args.from_model:
        known = {a for _e, _n, a, _t, _s in CSRS}
        kept_bank = 0
        for addr, name in sorted(model_csrs().items()):
            if addr in known:
                continue
            if _COUNTER_BANK.match(name):
                kept_bank += 1
                if kept_bank > args.counter_samples:
                    continue
            # Read-only by convention: the unprivileged counter shadows live in
            # 0xc00-0xc9f, and every instruction this framework renders is a
            # write, so they must trap rather than succeed.
            expect = ILLEGAL_INSTRUCTION if 0xc00 <= int(addr, 16) <= 0xc9f else None
            if name in READ_ONLY_CSRS:
                expect = ILLEGAL_INSTRUCTION
            # The RV32 high half of a 64-bit CSR does not exist at XLEN=64.
            if args.xlen == 64 and _is_high_half(name, model):
                expect = ILLEGAL_INSTRUCTION
            suffix = ("" if name not in FP_CSRS and name not in VECTOR_CSRS
                      else _UNIT_ISA["fp"] if name in FP_CSRS else _UNIT_ISA["vector"])
            csrs.append(("model", name, addr, expect, suffix))
        print(f"# swept {len(csrs)} CSRs ({len(CSRS)} curated + "
              f"{len(csrs) - len(CSRS)} derived from the model)")

    wanted = set(args.only.split(",")) if args.only else None
    rows, hazards, resumed = [], [], 0
    for ext, name, addr, expect_trap, isa_suffix in csrs:
        if wanted and name not in wanted:
            continue
        if name in HARNESS_HAZARD:
            hazards.append((name, HARNESS_HAZARD[name]))
            print(f"{ext:14s} {name:14s} {addr:6s} HAZARD -- not swept: "
                  f"{HARNESS_HAZARD[name]}", flush=True)
            continue
        t0 = time.time()
        if args.xlen == 64 and _is_high_half(name, model) and expect_trap is None:
            expect_trap = ILLEGAL_INSTRUCTION
        # Resume support. A full CSR sweep is over an hour per XLEN and has now
        # been lost three times to things unrelated to it -- an OOM that took
        # the desktop down, a hard reset, and a reboot ten minutes after
        # launch. Each CSR's raw output is cached the moment it completes, so
        # an interruption costs one CSR rather than the whole run.
        cache = os.path.join(args.out_dir, "_results", f"{name}-rv{args.xlen}.txt")
        if args.resume and os.path.exists(cache):
            out = open(cache, errors="ignore").read()
            resumed += 1
        else:
            out = run(addr, args.xlen, os.path.join(args.out_dir, name), expect_trap,
                      isa_suffix, name in SELF_UPDATING, args.timeout,
                      enable_fp=name in FP_CSRS, enable_vector=name in VECTOR_CSRS)
            os.makedirs(os.path.dirname(cache), exist_ok=True)
            with open(cache, "w") as f:
                f.write(out)
        lines = [l for l in out.splitlines() if l.startswith(("PASS", "FAIL"))]
        n_pass = sum(1 for l in lines if l.startswith("PASS"))
        # Per-simulator, so a Sail-vs-Spike divergence (which PMP is already
        # known to have) reads as a divergence rather than as a flat failure.
        per_sim = {s: sum(1 for l in lines if f"{s}= ok " in l) for s in ("sail", "spike")}
        reasons = sorted({l.split("<- ", 1)[1] for l in lines if "<- " in l})
        rows.append((ext, name, addr, expect_trap, n_pass, len(lines), per_sim,
                     reasons, round(time.time() - t0)))
        print(f"{ext:14s} {name:14s} {addr:6s} "
              f"{'write-must-trap' if expect_trap else 'read/write':16s} "
              f"{n_pass}/{len(lines) or 6} pass  "
              f"sail={per_sim['sail']} spike={per_sim['spike']}  {rows[-1][-1]}s"
              + (f"   <- {reasons[0][:100]}" if reasons else ""), flush=True)

    print(f"\n{'=' * 78}\nCSR sweep summary (RV{args.xlen}) -- 6 Zicsr instructions per CSR\n{'=' * 78}")
    full = [r for r in rows if r[4] == 6]
    none = [r for r in rows if r[4] == 0]
    print(f"{len(full)}/{len(rows)} CSRs pass all 6 instructions on every simulator")
    if none:
        print("  no instruction passed for: " + ", ".join(f"{r[1]}" for r in none))
    partial = [r for r in rows if 0 < r[4] < 6]
    if partial:
        print("  partial: " + ", ".join(f"{r[1]} ({r[4]}/6)" for r in partial))


if __name__ == "__main__":
    main()
