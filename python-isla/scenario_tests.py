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
import json
import os
import subprocess
import sys

import paths
import test_config

ISLA_DIR = paths.ISLA_DIR
ISLA_BIN = os.path.join(ISLA_DIR, "target/release/isla-testgen")
Z3_LIB = paths.Z3_LIB
SAIL_SIM = paths.SAIL_SIM
SPIKE = paths.SPIKE

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

# ---- M-mode PMA: attributes come from the *region*, not from an instruction --
# `lui x3, 0x80020` puts the address in main memory, whose PMA the config
# variants below alter. Encodings taken from the assembler, not hand-computed.
AMO_MAIN = ["0x800201b7", "0x0021b0af"]           # lui x3, 0x80020; amoadd.d x1, x2, (x3)
LR_MAIN = ["0x800201b7", "0x1001b0af"]            # lui x3, 0x80020; lr.d x1, (x3)
SC_MAIN = ["0x800201b7", "0x1001b0af", "0x1821b22f"]  # lr.d then sc.d x4, x2, (x3)

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

    # ---- M5: interrupt delivery ------------------------------------------
    # `--pending-interrupt` (m0_wfi above) deliberately leaves mstatus.MIE
    # clear, so it tests WFI's wake condition and *not* delivery. These take
    # the trap for real.
    ("m5_interrupt_delivered", WFI,
     ["--deliver-interrupt", "--expect-trap-cause", "3", "--trap-is-pass"], True),
    # Delegated: the same interrupt, but mideleg sends it to S-mode and the
    # handler reads scause instead of mcause. The pair is the test -- a single
    # "an interrupt happened" assertion would pass against an implementation
    # that always trapped to M.
    # Cause 1, not 3: this has to be a *supervisor* software interrupt, because
    # legalize_mideleg hardwires the machine-level delegation bits to 0. A
    # machine software interrupt simply cannot be delegated.
    ("m5_interrupt_delegated", WFI,
     ["--deliver-interrupt", "--delegate-interrupt", "--run-in-supervisor",
      "--pmp-allow-all", "--expect-trap-cause", "1", "--trap-is-pass"], True),
    # Control: right delivery, wrong cause. Must fail, or the test is only
    # detecting that *something* trapped.
    ("m5_NEG_interrupt_wrong_cause", WFI,
     ["--deliver-interrupt", "--expect-trap-cause", "7", "--trap-is-pass"], False),
    # An ebreak control was tried here and removed: with delivery enabled the
    # interrupt fires *before* the ebreak retires, so the test passes on the
    # interrupt and the ebreak is never reached. It tested nothing. The
    # sign-bit check that distinguishes an interrupt cause from an identically
    # numbered exception cause is exercised by m5_interrupt_delivered itself --
    # cause 3 as an exception is `ebreak`, and without the check the two are
    # indistinguishable.

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



# --- M4: physical memory attributes (PMA) ---------------------------------
#
# PMA is named in the RFP's *required* M-mode list alongside PMP, and sat at
# 15/97 spans before this. It needs no new generator capability: every PMA
# attribute is a per-region field in the Golden Model's own config JSON, so a
# test is "run this access under a config whose region says it is illegal".
#
# That is also what makes the negative control free and honest -- the *same*
# ELF under the unmodified config must pass. If it does not, the test is
# faulting for some unrelated reason and proves nothing about PMA.

SAIL_CONFIG_BASE = os.path.join(paths.SAIL_RISCV, "build/config/rv{}d_v128_e64.json")
MAIN_MEMORY_BASE = "0x80000000"


def _strip_jsonc(text):
    """Golden Model configs are JSONC; `json` will not parse the comments."""
    out, in_str, esc, i = [], False, False, 0
    while i < len(text):
        c = text[i]
        if in_str:
            out.append(c)
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
            out.append(c)
        elif c == "/" and i + 1 < len(text) and text[i + 1] == "/":
            while i < len(text) and text[i] != "\n":
                i += 1
            continue
        else:
            out.append(c)
        i += 1
    return "".join(out)


# PMA attributes are not independently settable: the model *validates* the
# config and rejects combinations that contradict a declared extension. Setting
# main memory to AMONone while `Ziccamoa` is supported is refused outright --
#
#   "Main memory region starting at 0x80000000 is coherent and cacheable with
#    AMONone atomicity support, but Ziccamoa is enabled which requires
#    AMOArithmetic support."
#
# which is the model doing its job. So a PMA variant must also retract the
# extension that guarantees the attribute. Mapping the attribute to the
# extensions it contradicts keeps that link explicit rather than buried in a
# hand-edited config.
# The coupling is transitive, and the validator finds it for you: making main
# memory RsrvNone also breaks Svadu, because hardware A/D updates write PTEs
# atomically and so need a reservable region --
#
#   "The Svadu extension is enabled but no memory region supports hardware
#    page-table writes."
#
# Each entry here was added because the model rejected the config without it,
# not because it seemed likely.
PMA_REQUIRES_DISABLING = {
    "atomic_support": ["Ziccamoa", "Ziccamoc"],
    "reservability": ["Ziccrse", "Svadu"],
}


def pma_config(xlen, out_dir, name, **attrs):
    """Write a config variant with main memory's PMA attributes overridden.

    Only the region at MAIN_MEMORY_BASE is touched, and only the named fields,
    so a variant differs from the shipped config in exactly the attribute under
    test -- which is what lets a failure be attributed to that attribute.
    """
    with open(SAIL_CONFIG_BASE.format(xlen)) as f:
        cfg = json.loads(_strip_jsonc(f.read()))
    hit = 0
    for region in cfg["memory"]["regions"]:
        if region["base"]["value"] == MAIN_MEMORY_BASE:
            for k, v in attrs.items():
                if k not in region["attributes"]:
                    sys.exit(f"PMA attribute {k!r} is not in the config schema -- "
                             f"the model's config format changed, fix this rather "
                             f"than silently testing nothing")
                region["attributes"][k] = v
            hit += 1
    if hit != 1:
        sys.exit(f"expected exactly one main-memory region at {MAIN_MEMORY_BASE}, "
                 f"found {hit}")
    for attr in attrs:
        for ext in PMA_REQUIRES_DISABLING.get(attr, []):
            if ext in cfg.get("extensions", {}):
                cfg["extensions"][ext]["supported"] = False
    path = os.path.join(out_dir, f"config_{name}.json")
    with open(path, "w") as f:
        json.dump(cfg, f, indent=2)
    return path


# (name, opcodes, isla flags, expect, pma-attribute override or None).
# A `None` override means the stock config, which is how the controls work.
PMA_SCENARIOS = [
    # An AMO to a region declaring no atomic support must raise a store/AMO
    # access fault (cause 7).
    ("m4_amo_unsupported", "AMO_MAIN",
     ["--expect-trap-cause", "7", "--trap-is-pass"], True,
     {"atomic_support": "AMONone"}),
    # Control: same config, wrong expected cause. Must fail, which proves the
    # handler compares the *cause* rather than merely noticing a trap.
    #
    # The obvious control -- the same ELF under the stock config -- was tried
    # first and does not discriminate: with no trap the harness falls through
    # to the ordinary final-state comparison, which isla solved for the
    # non-trapping path, so it passes either way. A control that cannot fail
    # is the thing this project keeps having to relearn.
    ("m4_NEG_amo_wrong_cause", "AMO_MAIN",
     ["--expect-trap-cause", "5", "--trap-is-pass"], False,
     {"atomic_support": "AMONone"}),

    # LR to a region declaring no reservability -- same fault class, different
    # attribute, so a single over-broad config change cannot satisfy both.
    ("m4_lr_unreservable", "LR_MAIN",
     ["--expect-trap-cause", "5", "--trap-is-pass"], True,
     {"reservability": "RsrvNone"}),
    ("m4_NEG_lr_wrong_cause", "LR_MAIN",
     ["--expect-trap-cause", "7", "--trap-is-pass"], False,
     {"reservability": "RsrvNone"}),
]


# Which architectural feature each scenario family exercises, so the output can
# be organised and selected the same way the opcode sweeps are. The milestone
# prefix already encoded this -- it just wasn't used for anything.
#
# `params` are the configuration facts a directory name cannot express: a PMP
# test is meaningless on a machine with no PMP entries, and selecting it there
# would report a failure that says nothing about the implementation.
SCENARIO_GROUP = {
    "m0": ("privilege", ["I", "Zicsr", "Sm", "S", "U"], {}),
    "m2": ("PMP", ["I", "Zicsr", "Sm"], {"NUM_PMP_ENTRIES": "'>0'"}),
    "m3": ("Sv", ["I", "Zicsr", "Sm", "S"], {}),
    "m4": ("PMA", ["I", "Zicsr", "A", "Sm"], {}),
    "m5": ("interrupts", ["I", "Zicsr", "Sm", "S"], {}),
}


def scenario_group(name, xlen, flags):
    """(directory, required extensions, params) for one scenario."""
    group, exts, params = SCENARIO_GROUP.get(
        name.split("_", 1)[0], ("other", ["I", "Zicsr"], {}))
    exts, params = list(exts), dict(params)
    # Recorded from the flag actually passed rather than from the group, since
    # the paging scheme is what a configuration selects on and Sv39 vs Sv32 is
    # exactly the distinction that makes these skip at RV32 today.
    if "--sv39" in flags:
        exts.append("Sv39")
    return group, exts, params


def generate(name, opcodes, flags, out_dir, xlen, manifests=None):
    env = dict(os.environ, LD_LIBRARY_PATH=Z3_LIB)
    group, exts, params = scenario_group(name, xlen, flags)
    group_dir = os.path.join(out_dir, group)
    os.makedirs(group_dir, exist_ok=True)
    prefix = os.path.join(group_dir, name)
    cmd = [ISLA_BIN, "-A", f"riscv-ir/riscv{xlen}.ir", "-C", f"riscv-ir/riscv{xlen}.toml",
           "-a", f"riscv{xlen}", "--memory-region", "0x80020000-0x80030000",
           "-o", prefix, "-n", "1", *opcodes, *flags]
    r = subprocess.run(cmd, cwd=ISLA_DIR, env=env, capture_output=True, text=True, timeout=300)
    elf = prefix + ".elf"
    if r.returncode == 0 and manifests is not None:
        march = test_config.march_string(xlen, "zicsr")
        test_config.annotate(prefix + ".s",
                             test_config.header(exts, march, xlen, params))
        m = manifests.setdefault(group, test_config.Manifest(group, xlen, march))
        # Negative controls are marked, because a runner that silently drops
        # them keeps the tests and loses the only thing proving they can fail.
        m.add(name, elf, exts, params,
              note="negative control: must FAIL" if "_NEG_" in name else None)
    return elf, r.returncode == 0


def run(elf, xlen, config=None):
    if not os.path.exists(elf):
        return None, None
    # A PMA test is only a PMA test if the simulator is told the region's
    # attributes; without --config it runs under the build default and the
    # override has no effect at all -- a pass that means nothing.
    sail_args = (["--config", config] if config
                 else (["--rv32"] if xlen == 32 else []))
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
    manifests = {}
    for name, opcodes, flags, expect in SCENARIOS:
        if wanted and name not in wanted:
            continue
        # Sv39 is RV64-only (it is a 39-bit virtual address scheme).
        if args.xlen == 32 and "--sv39" in flags:
            print(f"SKIP  {name:32s} Sv39 is RV64-only")
            continue
        elf, gen_ok = generate(name, opcodes, flags, out_dir, args.xlen, manifests)
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

    # --- PMA scenarios: same shape, but each carries a config variant ------
    for name, seq_name, flags, expect, override in PMA_SCENARIOS:
        if wanted and name not in wanted:
            continue
        if args.xlen == 32:
            # The sequences use .d (doubleword) AMO/LR forms, which do not
            # exist on RV32. Skipped rather than silently swapped for .w, so
            # the gap is visible in the output.
            print(f"SKIP  {name:32s} PMA sequences use RV64 .d forms")
            continue
        opcodes = globals()[seq_name]
        cfg = (pma_config(args.xlen, out_dir, name, **override) if override
               else SAIL_CONFIG_BASE.format(args.xlen))
        elf, gen_ok = generate(name, opcodes, flags, out_dir, args.xlen, manifests)
        sail, spike = run(elf, args.xlen, config=cfg)
        if sail is None:
            verdict, detail = "ERROR", "generation produced no ELF"
        else:
            # Spike has no way to be told these PMA attributes, so it cannot
            # agree or disagree -- judge on Sail alone and say so, rather than
            # counting a Spike mismatch as a divergence it is not.
            verdict = "ok" if bool(sail) == expect else "REGRESSION"
            detail = (f"sail={'ok' if sail else 'FAIL'}  (expected "
                      f"{'pass' if expect else 'FAIL'}; sail-only: PMA is not "
                      f"expressible in Spike's config)")
        good += verdict == "ok"
        bad += verdict != "ok"
        print(f"{verdict:11s} {name:32s} {detail}")

    print(f"\n{good} as expected, {bad} not, RV{args.xlen}")
    for g, m in sorted(manifests.items()):
        m.write(os.path.join(out_dir, g))
    print(f"ELFs in {out_dir} -- pass this to coverage_report.py --elf-dir so the "
          f"scenario tests count toward model coverage")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
