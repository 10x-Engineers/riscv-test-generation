#!/usr/bin/env python3
"""Systematic per-Sail-file opcode sweep for isla-testgen.

Instruction list + encodings come straight from the Sail model itself (see
model_opcodes.py) -- not from riscv-opcodes, and not the abandoned REMS OCaml
random generator (last commit 2019, pinned to a 7-year-stale Sail AST shape).
This means the sweep only ever exercises what this model actually implements:
if a mnemonic isn't in the parsed `.sail` files, it's never generated, so
there's no risk of testing (or silently skipping) an instruction the model
doesn't have.

For each instruction found in the given Sail file(s): render one legal,
*reachable* instance as real assembly text, assemble it with the real RISC-V
assembler to get its actual opcode word(s) (same "assembler is the encoding
source of truth" principle sailtest/model.py uses), then (1) invoke
isla-testgen, and (2) actually run the resulting ELF on sail_riscv_sim to
confirm it passes -- generation not erroring is not enough proof (e.g. a
self-targeting jal "succeeds" at generation time but would hang forever if
actually run).

See model_opcodes.py's module docstring for why register/immediate/branch-
target values are chosen the way they are.
"""
import argparse
import os
import shutil
import subprocess
import sys

import model_opcodes

import paths
import test_config

SAIL_RISCV_ROOT = paths.SAIL_RISCV
ISLA_TESTGEN_DIR = paths.ISLA_DIR
ISLA_TESTGEN_BIN = os.path.join(ISLA_TESTGEN_DIR, "target/release/isla-testgen")
Z3_LIB_DIR = paths.Z3_LIB
# Hard address-space cap on each isla generation, in GiB.
#
# Some instructions make the solver allocate without bound -- `aes64im`,
# `aes64dsm`, `cpop` -- and an unbounded allocation on a 16 GiB laptop does not
# fail politely: it drives the whole machine into swap, and the sweep dies
# somewhere in the fallout along with whatever else was running. A full sweep
# has been lost to this twice, once taking the desktop session with it.
#
# With RLIMIT_AS set, the same instruction gets an allocation failure inside
# isla, that one test is recorded as a failure, and the sweep continues. A
# generation that needs more than this is one for the oracle anyway (see
# findings B6 / the routing memory) -- the cap turns a machine-wide outage into
# a routing signal.
# 4, not 6: 6 still let `aes64im` reach 5.5 GiB resident, which on a 16 GiB
# laptop running an editor is enough to make the desktop stutter. No
# instruction that generates successfully has ever needed more than ~1 GiB, so
# the headroom above 4 buys nothing except a slower path to the same failure.
ISLA_MEM_LIMIT_GIB = int(os.environ.get("ISLA_MEM_LIMIT_GIB", "4"))


def _limit_memory():
    """preexec_fn: cap the child's address space. Child-only -- never the sweep's."""
    import resource
    nbytes = ISLA_MEM_LIMIT_GIB * 1024 ** 3
    resource.setrlimit(resource.RLIMIT_AS, (nbytes, nbytes))


SAIL_RISCV_DIR = paths.SAIL_RISCV
# Ask paths.py rather than assuming a layout. CMake puts the binary at
# build/c_emulator/sail_riscv_sim; a top-level `sail_riscv_sim` next to it is a
# convenience symlink some checkouts have and the submodule does not, so
# hardcoding that name worked only where someone had made the link by hand.
SAIL_RISCV_SIM = paths.SAIL_SIM
RISCV_TOOLCHAIN_DIR = paths.RISCV_TOOLCHAIN_DIR
SPIKE_BIN = paths.SPIKE

# Everything that differs by XLEN, in one place: isla-testgen's arch name +
# compiled IR/config (see riscv-ir/riscv64.toml's own comment for how it was
# derived from the RV32 one), the cross assembler/objdump prefix, and each
# simulator's own way of being told "run in 32/64-bit mode."
XLEN_CONFIG = {
    32: dict(
        isla_arch="riscv32", isla_ir="riscv-ir/riscv32.ir", isla_toml="riscv-ir/riscv32.toml",
        as_bin=f"{RISCV_TOOLCHAIN_DIR}/riscv32-unknown-elf-as",
        objdump_bin=f"{RISCV_TOOLCHAIN_DIR}/riscv32-unknown-elf-objdump",
        sail_sim_flag="--rv32", qemu_bin="qemu-system-riscv32",
    ),
    64: dict(
        isla_arch="riscv64", isla_ir="riscv-ir/riscv64.ir", isla_toml="riscv-ir/riscv64.toml",
        as_bin=f"{RISCV_TOOLCHAIN_DIR}/riscv64-unknown-elf-as",
        objdump_bin=f"{RISCV_TOOLCHAIN_DIR}/riscv64-unknown-elf-objdump",
        # sail_riscv_sim has no --rv64 flag -- RV64 is its default config, and
        # --rv32 is the only XLEN shortcut it defines (--help confirms this).
        sail_sim_flag=None, qemu_bin="qemu-system-riscv64",
    ),
}

# The RISC-V spec's canonical single-letter ISA-string order, which Spike
# enforces strictly rather than tolerantly: `--isa=rv32imac_m_zicsr` is a hard
# error ("Extension 'm' appears too late in ISA string"), not a redundant
# no-op. So Spike's ISA string can't be built by pasting an extension's march
# suffix onto a fixed base -- the letters have to be merged and re-ordered.
_ISA_LETTER_ORDER = "imafdqlcbjtpv"

# Single-letter extensions every generated test needs regardless of what's
# under test: `i` obviously, and `mac` because the harness preamble and the
# ELF's own startup are assembled/linked expecting them.
_SPIKE_BASE_LETTERS = "imac"


def spike_isa(xlen, march_ext):
    """Spike `--isa=` string for a sweep of an extension whose assembler march
    suffix is `march_ext` -- base letters and the suffix's own letters merged
    into canonical order, multi-letter (`Z*`/`S*`) extensions appended after.
    See _ISA_LETTER_ORDER for why plain concatenation doesn't work."""
    letters = set(_SPIKE_BASE_LETTERS)
    multi = []
    for part in march_ext.split("_"):
        if not part:
            continue
        (multi.append(part) if len(part) > 1 else letters.add(part))
    # Pin VLEN whenever the vector unit is in play, for the same reason
    # SAIL_CONFIG exists: Spike's own default VLEN is not the IR's, and the
    # difference reads as a wrong answer rather than as a wrong setting.
    if "v" in letters and not any(m.startswith("zvl") for m in multi):
        multi.append(SPIKE_VLEN_EXT)
    ordered = "".join(c for c in _ISA_LETTER_ORDER if c in letters)
    return f"rv{xlen}{ordered}" + "".join("_" + m for m in multi)

# Per-extension assembler/spike suffix, keyed by the Sail model's own
# extensions/<NAME>/ directory name (used as both the sweep's --extension
# label and, by default, its output subdirectory). Needed because the
# assembler and Spike both reject an instruction from an extension they
# weren't told is enabled -- confirmed the hard way: `csrrw` needs
# `-march=...zicsr` or the assembler calls it an unrecognized opcode, and
# `sinval.vma` passes generation and Sail, then fails on Spike specifically
# (exit 1, "*** FAILED ***") unless `--isa=...zicsr_svinval` is given --
# Spike enables Zicsr by default but not Svinval.
#
# Every entry gets "zicsr" whether it needs it standalone or not: the
# harness preamble's own `csrw mtvec` needs it regardless of what
# instruction is under test (see isla-gen-extension's ACT4 compatibility
# notes), and Spike accepts a redundant explicit "zicsr" harmlessly.
EXTENSION_MARCH = {
    "I": "zicsr",                 # base ISA -- includes the privileged subset
                                   # (mret/sret/wfi/ecall/ebreak/sfence.vma)
    "Zicsr": "zicsr",
    "Svinval": "zicsr_svinval",
    # Unprivileged (M5/M6). Each string was checked against the real
    # assembler before being added here -- GNU as rejects an unknown or
    # misspelled extension name outright, so a wrong entry here shows up as
    # "unrecognized opcode" on every instruction in the sweep rather than as
    # a silent misencoding.
    "M": "m_zicsr",
    "A": "a_zicsr",
    "Zifencei": "zifencei_zicsr",
    "Zicond": "zicond_zicsr",
    "Zawrs": "zawrs_zicsr",
    "Zicbom": "zicbom_zicsr",
    "Zicbop": "zicbop_zicsr",
    "Zicboz": "zicboz_zicsr",
    "Zihintntl": "zihintntl_zicsr",
    "Zihintpause": "zihintpause_zicsr",
    "Zimop": "zimop_zicsr",
    "Zcmop": "zca_zcmop_zicsr",   # Zcmop's c.mop.N encodings live in the C space
    "cfi": "zicfilp_zicfiss_zicsr",
    "B": "b_zicsr",
    "K": "zbkb_zbkx_zknd_zkne_zknh_zksed_zksh_zicsr",
    "FD": "f_d_zfh_zfa_zicsr",
    "C": "c_zicsr",
    "V": "v_zicsr",
    "vector_crypto": "v_zvbb_zvbc_zvkg_zvkned_zvknhb_zvksed_zvksh_zicsr",
    "bfloat16": "f_d_v_zfbfmin_zvfbfmin_zvfbfwma_zicsr",
    # "H": "zicsr_h",              # reserved, not usable yet -- see
                                   # documentation/python-isla/
                                   # privileged-instruction-coverage.md's
                                   # "Hypervisor readiness" section. The model
                                   # currently only stubs Ext_H's
                                   # currentlyEnabled guard (no instructions
                                   # implemented), so there is nothing to
                                   # sweep and no march string to verify yet;
                                   # uncomment and confirm against the real
                                   # toolchain once that changes.
}


# Per-extension overrides for the isla config's `[registers.defaults]` block
# (riscv-ir/riscv{32,64}.toml). These are the model's own feature-enable
# registers, and the defaults have several *off*: `rv_enable_fdext = false`
# means every F/D instruction, however correctly encoded, executes as an
# illegal instruction -- so an F/D sweep against the stock config measures the
# config, not the instructions.
#
# This is also the mechanism the plan's M8 (config-awareness) needs: the same
# override path drives "F/D on vs off" as a deliberate config axis, via
# --config-override, rather than being a hardcoded per-extension fixup.
EXTENSION_CONFIG = {
    "FD": {"rv_enable_fdext": "true"},
    "C": {"rv_enable_rvc": "true"},   # already the default; stated explicitly
                                       # so the sweep doesn't silently depend
                                       # on it staying that way.
}


def write_config(base_toml, overrides, dest):
    """Copy `base_toml` to `dest` with `[registers.defaults]` entries replaced
    by `overrides`. Line rewriting rather than a TOML round-trip on purpose:
    the file carries comments explaining several of its values (see
    riscv64.toml's note on how it was derived from the RV32 one), and a
    round-trip would drop every one of them."""
    if not overrides:
        return base_toml
    remaining = dict(overrides)
    out = []
    for line in open(base_toml):
        key = line.split("=", 1)[0].strip()
        if key in remaining:
            out.append(f"{key} = {remaining.pop(key)}\n")
        else:
            out.append(line)
    if remaining:
        raise SystemExit(f"config override(s) not present in {base_toml}: {', '.join(remaining)}")
    with open(dest, "w") as f:
        f.writelines(out)
    return dest


def _shared_table_paths(model_dir, sail_paths):
    """Extra `.sail` files to parse for mnemonic string tables (not for
    instructions). A clause's mnemonic table need not live in the same file as
    the clause: M's `div`/`divu`/`rem`/`remu` resolve `maybe_u` against a table
    in `extensions/I/base_insts.sail`, and several extensions keep theirs in a
    sibling `*_types.sail`. Missing those doesn't error -- it silently drops
    every clause that referenced them (this cost M 4 of its 6 mnemonics before
    it was caught), so the base file and each swept file's own directory
    siblings are always included."""
    # Every `.sail` file in the model, not a curated list. A clause's mnemonic
    # tables can live anywhere: `width_mnemonic` (b/h/w/d) is in
    # `core/types.sail`, `maybe_u` in `extensions/I/base_insts.sail`,
    # `maybe_aqrl` in `extensions/A/aext_types.sail` -- and that last one is
    # needed to parse a clause in `extensions/cfi/`, which no
    # same-directory-plus-core rule would ever have reached.
    #
    # Missing one is not a small loss and not a loud one: it silently drops
    # every clause that referenced it. `width_mnemonic` alone accounted for
    # every load and store in base I. Over-collecting has no failure mode to
    # trade against -- only `<-> string` mapping blocks are read out of these,
    # and Sail's own namespace already makes those names unique model-wide, so
    # there is nothing to shadow.
    paths = []
    for root, _, files in os.walk(model_dir):
        paths += sorted(os.path.join(root, f) for f in files if f.endswith(".sail"))
    return [p for p in paths if p not in sail_paths]


def _default_extension(sail_files):
    """Infer an extension label from the first Sail file's own parent
    directory name (e.g. "extensions/Zicsr/zicsr_insts.sail" -> "Zicsr") --
    matches the Sail model's own directory-per-extension layout, so this
    needs no maintenance as new extension files are swept."""
    return os.path.basename(os.path.dirname(sail_files[0]))


# union_name -> mcause the instruction is expected to trap with, always
# starting from Machine mode (isla-testgen's init state) -- see
# execute ECALL/EBREAK in extensions/I/base_insts.sail. Anything not listed
# here uses the harness's default "any trap fails" behaviour.
TRAP_EXPECT = {
    "ECALL": 11,     # E_M_EnvCall -- we always start in Machine mode
    "EBREAK": 3,     # E_Breakpoint
    "C_EBREAK": 3,   # the compressed form is a separate union, and keying this
                     # table on union names means it needs its own entry
    # E_SAMO_Access_Fault. Zicfiss is *architecturally unusable* in M-mode --
    # "Use of Zicfiss in M-mode is not supported", implemented at
    # `sys/vmem.sail:416` as `effPriv == Machine -> E_SAMO_Access_Fault`. This
    # sweep runs in Machine mode, so an ssamoswap here must fault, and a test
    # expecting it to succeed is a wrong test. All eight mnemonic forms
    # (.w/.d x aq/rl orderings) share this one union, so one entry covers them.
    #
    # This is the *negative* half of the instruction. The positive half -- an
    # S-mode ssamoswap against a mapped shadow-stack page -- is a scenario
    # test, not an opcode sweep, and lives in scenario_tests.py.
    "SSAMOSWAP": 7,
}

# union_name -> needs isla-testgen's --pending-interrupt. WFI stalls until an
# enabled interrupt is pending, so without one raised up front it never
# retires: Spike hangs until this script's own wall clock kills it, which
# reports as a timeout and proves nothing about WFI. See
# Target::pending_interrupt in isla-gen-extension/src/target.rs.
PENDING_INTERRUPT = {"WFI"}

# union_name -> extra isla-testgen flags needed to make that instruction
# testable at all. Both entries here deliberately target *Supervisor*, so the
# test proves a real privilege transition rather than an xRET that stayed put:
#
#  --preload-xepc  MRET/SRET jump to mepc/sepc, which isla leaves unconstrained
#                  and freely solves to its own exit trampoline. The preamble
#                  has to write that value into the real CSR or the instruction
#                  jumps to the CSR's reset value at runtime.
#  --preload-xpp   mstatus resets all-zero, so MPP/SPP start at User. Set the
#                  target privilege explicitly rather than inheriting that.
#  --pmp-allow-all Once outside Machine mode, pmpCheck denies every access that
#                  matches no PMP entry -- and the default config has PMP
#                  enabled with none configured, so the very next fetch faults.
#
# That last one is also the negative control proving these aren't vacuous
# passes: drop it, and both tests fail on Sail exactly as they should, because
# the privilege change really did happen. (Spike passes them either way -- a
# real divergence, see privileged-instruction-coverage.md.)
XRET_SETUP = {
    "MRET": ["--preload-xepc", "mepc", "--preload-xpp", "mpp=s", "--pmp-allow-all"],
    "SRET": ["--preload-xepc", "sepc", "--preload-xpp", "spp=s", "--pmp-allow-all"],
}


# 60s was too short for the SMT-heavy instructions, and the failure looked
# like a real generation failure rather than slowness until generation errors
# started being reported verbatim (they now read "TIMEOUT (generation)"). The
# expensive ones are exactly the ones you'd expect: population count (B's
# `cpop`), the AES round functions and crossbar permutations (K's `aes64*`,
# `xperm*`), and integer division.
def run_isla_testgen(opcodes, out_prefix, xlen, timeout=300, boot_fixed_entry=False,
                     expect_trap_cause=None, isla_toml=None, enable_fp=False,
                     enable_vector=False, pending_interrupt=False, extra_args=()):
    cfg = XLEN_CONFIG[xlen]
    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = Z3_LIB_DIR
    # isla-testgen's opcode parser treats the hex string's digit count as the
    # instruction width (e.g. 5 hex digits => tries to build a 20-bit value),
    # not just the numeric value -- Python's hex() strips leading zeros, so it
    # must be re-padded explicitly.
    #
    # Which width, though, is per-instruction, not fixed at 8: a *compressed*
    # instruction is 16 bits and must be given as 4 digits. Padding one to 8
    # hands isla a 32-bit opcode whose upper half is zero, which it duly
    # places -- and the extra zero half-word decodes as an illegal instruction
    # at runtime. RISC-V marks the difference in the encoding itself: bits
    # [1:0] == 0b11 means a 32-bit instruction, anything else means 16-bit, so
    # no extra bookkeeping is needed to tell them apart.
    opcode_strs = [f"0x{op:08x}" if (op & 3) == 3 else f"0x{op:04x}" for op in opcodes]
    cmd = [
        ISLA_TESTGEN_BIN,
        "-A", cfg["isla_ir"],
        "-C", isla_toml or cfg["isla_toml"],
        "-a", cfg["isla_arch"],
        "--memory-region", "0x80020000-0x80030000",
        "-o", out_prefix,
    ]
    if boot_fixed_entry:
        # QEMU's "spike" machine and CVA6's own bootrom both always hand off
        # to a fixed address (0x80000000) after reset, ignoring the ELF's
        # actual entry point -- confirmed by tracing QEMU's own boot stub
        # (`-d in_asm`) and by CVA6's ariane_testharness.sv wiring
        # `boot_addr_i` to a fixed ROM base. Sail/Spike/ACT4 don't have this
        # constraint (they honor ENTRY() properly), which is what makes the
        # *default* layout (test bytes conceptually "at" 0x80000000, matching
        # ACT4's own fixed `.text.init` placement) work for them.
        #
        # These two layouts are mutually exclusive -- something has to be
        # physically at 0x80000000, and ACT4 wants the test bytes there while
        # QEMU/CVA6 want the preamble there -- so this is a separate,
        # opt-in generation mode, not a change to the default one. In this
        # mode our own generated .ld places things (not ACT4's fixed one),
        # so only the address values need to change: preamble at the
        # required 0x80000000, test bytes moved to an explicit --code-region
        # comfortably clear of the harness and tohost (which stays at its
        # usual harness_code+0x1000 -- see generate_object_riscv.rs), and
        # both well clear of the 0x80020000+ scratch data region below.
        cmd += ["--harness-code", "0x80000000", "--code-region", "0x80004000-0x80014000"]
    if expect_trap_cause is not None:
        cmd += ["--expect-trap-cause", str(expect_trap_cause)]
    if enable_fp:
        cmd += ["--enable-fp"]
    if enable_vector:
        cmd += ["--enable-vector"]
    if pending_interrupt:
        cmd += ["--pending-interrupt"]
    cmd += list(extra_args)
    cmd += ["-n", "1", *opcode_strs]
    try:
        result = subprocess.run(
            cmd, cwd=ISLA_TESTGEN_DIR, env=env,
            capture_output=True, text=True, timeout=timeout,
            preexec_fn=_limit_memory,
        )
    except subprocess.TimeoutExpired:
        return False, "", "TIMEOUT (generation)"
    # "successful execution" appearing anywhere is not enough: isla prints it
    # per *opcode*, so a two-instruction test whose `vsetvli` prelude succeeds
    # and whose instruction-under-test dies still matched -- reporting `gen=ok`
    # for something that produced no ELF, and throwing away the one line that
    # said why ("Failed path Error Symbolic (bit)vector length in zeros").
    # That reason is the whole basis for telling a framework limitation apart
    # from a model defect, so it is kept.
    combined = result.stdout + "\n" + (result.stderr or "")
    ok = (result.returncode == 0
          and "successful execution" in result.stdout
          and "No successful executions" not in result.stdout)
    if ok:
        return True, result.stdout, ""
    # Prefer the model-level diagnosis over the last line, which is usually a
    # generic "Generation attempt failed".
    reason = ""
    for line in combined.splitlines():
        line = line.strip()
        if line.startswith("Failed path Error"):
            reason = line[len("Failed path "):]
            break
    if not reason:
        lines = [l for l in combined.strip().splitlines() if l.strip()]
        reason = lines[-1] if lines else "generation failed"
    return False, result.stdout, reason


# The platform config the isla IR was compiled against, and therefore the one
# every simulator has to be run with. VLEN in particular is baked into the IR
# at compile time (`type vlenbits = bits(vlen)`), so if sail_riscv_sim runs at
# its own default (VLEN=256) while the IR says 128, isla's expected `vl` is
# simply a different number from the one the simulator produces -- a pure
# configuration disagreement that reads exactly like a model bug. It showed up
# as `vsetvli` failing on Sail while passing Spike.
#
# 128 rather than Sail's default 256 because both ends constrain it: the V
# extension requires VLEN >= 128 (`vlen_exp >= 7`), and isla's bitvector type
# is B129, so a 256-bit vector register can't be represented at all. 128 is
# the only value that satisfies both.
SAIL_CONFIG = {
    32: f"{SAIL_RISCV_DIR}/build/config/rv32d_v128_e64.json",
    64: f"{SAIL_RISCV_DIR}/build/config/rv64d_v128_e64.json",
}
# Spike takes VLEN through the Zvl* ISA extensions (this build has no --varch).
SPIKE_VLEN_EXT = "zvl128b"


def run_sail_sim(elf_path, xlen, timeout=15):
    if not os.path.exists(elf_path):
        return False, "no ELF produced"
    cfg = SAIL_CONFIG.get(xlen)
    if cfg and os.path.exists(cfg):
        # --config supersedes --rv32: the config file carries XLEN itself, and
        # sail_riscv_sim rejects the two together ("--rv32 Excludes: --config").
        argv = [SAIL_RISCV_SIM, "--config", cfg, elf_path]
    else:
        flag = XLEN_CONFIG[xlen]["sail_sim_flag"]
        argv = [SAIL_RISCV_SIM] + ([flag] if flag else []) + [elf_path]
    try:
        result = subprocess.run(
            argv,
            # The model root, not the binary's directory -- the two are the same
            # only in a checkout with the top-level symlink, and the simulator
            # writes sail_coverage relative to cwd.
            cwd=SAIL_RISCV_DIR,
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, "TIMEOUT (sail_riscv_sim — likely a self-loop, e.g. branch/jump to own PC)"
    ok = result.returncode == 0 and "SUCCESS" in result.stdout
    return ok, ("" if ok else (result.stdout + result.stderr).strip().splitlines()[-1] if (result.stdout + result.stderr).strip() else "no SUCCESS")


def run_spike(elf_path, xlen, march_ext="", timeout=15):
    if not os.path.exists(elf_path):
        return False, "no ELF produced"
    if not shutil.which(SPIKE_BIN):
        return None, "spike not found on PATH"
    isa = spike_isa(xlen, march_ext)
    try:
        # Bare-metal `spike <elf>` (no proxy kernel): exit code is Spike's own
        # HTIF-derived status, 0 on the pass value our harness writes to
        # tohost -- see generate_object_riscv.rs's RVMODEL_HALT_PASS/FAIL note.
        # `march_ext` matters here specifically -- Spike enables Zicsr by
        # default but not every extension (Svinval, confirmed empirically:
        # `sinval.vma` generates fine and passes Sail, then fails on Spike
        # with exit 1 unless `--isa=...svinval` is given explicitly).
        result = subprocess.run(
            [SPIKE_BIN, f"--isa={isa}", elf_path],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, "TIMEOUT (spike — likely a self-loop)"
    ok = result.returncode == 0
    return ok, ("" if ok else (result.stdout + result.stderr).strip().splitlines()[-1]
                if (result.stdout + result.stderr).strip() else "nonzero exit")


def assembles_for_other_xlen(insn, xlen, march_ext, csr_addr):
    """True if `insn` won't assemble for `xlen` but does for the other one --
    i.e. it's an XLEN-specific instruction (RV64's `mulw`/`addw`/`ld`/`sd`,
    RV32's none-so-far), which the Sail model defines in the same file behind
    an `xlen == 64` guard. Those are correctly *not testable* at this XLEN, so
    reporting them as failures would be wrong; they're skipped instead, and
    picked up by the other XLEN's own sweep.

    Checked empirically against the real assembler rather than by parsing the
    model's own guards, for exactly the reason opcodes come from the assembler
    in the first place (see model_opcodes.py's docstring) -- the guard syntax
    varies per instruction, the assembler's answer doesn't."""
    other = 64 if xlen == 32 else 32
    cfg = XLEN_CONFIG[other]
    opcodes, _ = model_opcodes.opcodes_for(
        insn, other, cfg["as_bin"], cfg["objdump_bin"], march_ext=march_ext,
        csr_addr=csr_addr)
    return opcodes is not None


def run_qemu(elf_path, xlen, timeout=15):
    if not os.path.exists(elf_path):
        return False, "no ELF produced"
    qemu_bin = XLEN_CONFIG[xlen]["qemu_bin"]
    if not shutil.which(qemu_bin):
        return None, f"{qemu_bin} not found on PATH"
    try:
        # "spike" (not "virt"): QEMU's spike-board machine model is the one
        # that emulates HTIF, so our tohost/fromhost write becomes a real
        # process exit code the same way it does under Spike/Sail -- matches
        # sailtest/runner.py's own qemu invocation.
        result = subprocess.run(
            [qemu_bin, "-machine", "spike", "-nographic", "-bios", "none",
             "-kernel", elf_path],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, "TIMEOUT (qemu — likely a self-loop, or waiting for a console)"
    ok = result.returncode == 0
    return ok, ("" if ok else (result.stdout + result.stderr).strip().splitlines()[-1]
                if (result.stdout + result.stderr).strip() else "nonzero exit")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("sail_files", nargs="+",
                     help="Sail source files to parse, relative to <sail-riscv>/model/ "
                          "(e.g. extensions/I/base_insts.sail)")
    ap.add_argument("--sail-riscv", default=SAIL_RISCV_ROOT)
    ap.add_argument("--out-dir", default="/tmp/opcode-sweep",
                     help="Generated tests land under <out-dir>/<extension>/ -- see "
                          "--extension.")
    ap.add_argument("--extension",
                     help="Label for this sweep: picks the output subdirectory "
                          "(<out-dir>/<extension>/) and the assembler/Spike extension "
                          "suffix (EXTENSION_MARCH) needed to encode/run its "
                          "instructions. Defaults to the first Sail file's own parent "
                          "directory name, matching the model's own "
                          "extensions/<NAME>/ layout (e.g. 'Zicsr' for "
                          "extensions/Zicsr/zicsr_insts.sail). Required (pass "
                          "explicitly) for files outside that layout, or to give an "
                          "extension a march/isa suffix not yet in EXTENSION_MARCH.")
    ap.add_argument("--xlen", type=int, default=32, choices=[32, 64])
    ap.add_argument("--boot-fixed-entry", action="store_true",
                     help="Generate for a target that always boots to a fixed address "
                          "(0x80000000) regardless of the ELF's entry point -- QEMU's "
                          "'spike' machine and CVA6 both do this. Mutually exclusive in "
                          "effect with ACT4 compatibility (--signature): ACT4's own fixed "
                          "linker script expects test bytes at 0x80000000, this mode "
                          "needs the preamble there instead.")
    ap.add_argument("--only",
                     help="Comma-separated mnemonics to sweep, instead of everything "
                          "parsed from --sail-files -- e.g. to re-check one instruction, "
                          "or a small named subset of a larger file like base_insts.sail.")
    ap.add_argument("--csr", default=model_opcodes.CSR_ADDR,
                     help="Hex CSR address (e.g. 0x3a0 for pmpcfg0) for any swept instruction "
                          "with a csr operand kind (CSRRW/CSRRS/CSRRC/CSRRWI/CSRRSI/CSRRCI) -- "
                          "defaults to mscratch (0x340). Widening this across real CSRs, not "
                          "just mscratch, is most of what M1 (coverage-expansion-plan.md) is.")
    ap.add_argument("--enable-fp", action="store_true",
                    help="Set mstatus.FS before the tested instruction. Needed for "
                         "the FP CSRs (fcsr/fflags/frm): with FS at its reset value "
                         "of Off, *accessing* them is an illegal instruction, so the "
                         "test fails for a reason that has nothing to do with the CSR.")
    ap.add_argument("--enable-vector", action="store_true",
                    help="Set mstatus.VS, likewise, for vcsr/vstart/vxrm/vxsat.")
    ap.add_argument("--inhibit-counters", action="store_true",
                     help="Freeze mcycle/minstret via mcountinhibit before the test "
                          "(isla-testgen's --inhibit-counters). They advance on their own as "
                          "instructions retire, so their final value can never match the single "
                          "value isla solved -- without this they fail identically on Sail and "
                          "Spike, which is the tell that the expectation is what's wrong.")
    ap.add_argument("--isla-arg", action="append", default=[], metavar="FLAG",
                    help="Pass a flag straight through to isla-testgen for every "
                         "instruction in the sweep; repeatable. The privilege level a "
                         "test runs at is a sweep dimension, not a property of an "
                         "instruction: the model's ECALL, SRET, WFI and SFENCE.VMA all "
                         "branch on cur_privilege and on mstatus bits, so the same "
                         "opcode swept at M, S and U reaches arms that are dead by "
                         "construction in a single-privilege run. "
                         "E.g. --isla-arg --run-in-supervisor --isla-arg --pmp-allow-all "
                         "(the two go together: Supervisor is subject to PMP, which "
                         "M-mode's unmatched-access allowance hides).")
    ap.add_argument("--extra-march", default="",
                     help="Extra extension name(s) to append to both the assembler's -march and "
                          "Spike's --isa for this sweep, on top of whatever EXTENSION_MARCH "
                          "gives --extension. Needed when what's under test isn't an "
                          "instruction but a CSR: Spike doesn't enable every extension by "
                          "default, and a CSR from one it wasn't told about reads as an illegal "
                          "instruction -- externally indistinguishable from the model being "
                          "wrong. Confirmed on stimecmp, which needed `sstc` (see csr_sweep.py).")
    ap.add_argument("--expect-trap-cause", type=int,
                     help="Expect every swept instruction to trap with this exact mcause, and "
                          "pass only if it does (isla-testgen's own --expect-trap-cause). Needed "
                          "for the negative half of M1's CSR coverage: writing a read-only CSR "
                          "(any counter in Zicntr/Zihpm) must raise illegal-instruction (2), and "
                          "a test that merely doesn't crash proves nothing there. Overrides the "
                          "per-instruction TRAP_EXPECT table above.")
    ap.add_argument("--config-override", action="append", default=[], metavar="KEY=VALUE",
                     help="Override one isla config `[registers.defaults]` entry for this "
                          "sweep, e.g. rv_enable_fdext=true. Repeatable. Applied on top of "
                          "whatever EXTENSION_CONFIG already sets for --extension. This is "
                          "the config axis M8 (coverage-expansion-plan.md) calls for -- "
                          "running the same sweep under F/D on vs off, RVC on vs off, and so "
                          "on -- rather than one arbitrary config being taken as the answer.")
    args = ap.parse_args()

    extension = args.extension or _default_extension(args.sail_files)
    march_ext = EXTENSION_MARCH.get(extension, "")
    if args.extra_march:
        march_ext = f"{march_ext}_{args.extra_march}" if march_ext else args.extra_march
    out_dir = os.path.join(args.out_dir, extension)
    os.makedirs(out_dir, exist_ok=True)
    model_dir = os.path.join(args.sail_riscv, "model")
    sail_paths = [os.path.join(model_dir, f) for f in args.sail_files]
    insns, unparsed = model_opcodes.parse_instructions(
        sail_paths, table_paths=_shared_table_paths(model_dir, sail_paths))
    if args.only:
        wanted = set(args.only.split(","))
        insns = {k: v for k, v in insns.items() if k in wanted}
    if not insns:
        print("No instructions parsed from %s -- check the file list/paths." % sail_paths)
        return []
    overrides = dict(EXTENSION_CONFIG.get(extension, {}))
    overrides.update(kv.split("=", 1) for kv in args.config_override)
    isla_toml = write_config(
        os.path.join(ISLA_TESTGEN_DIR, cfg_toml := XLEN_CONFIG[args.xlen]["isla_toml"]),
        overrides, os.path.join(out_dir, os.path.basename(cfg_toml)))
    print(f"# extension={extension} march_ext={march_ext or '(none)'} out_dir={out_dir}"
          + (f" config={overrides}" if overrides else ""))
    if unparsed and not args.only:
        # Surfaced, never swallowed: a clause the parser can't model is a real
        # coverage hole, and the entire point of sourcing the instruction list
        # from the model is that holes are visible rather than assumed absent.
        print(f"# {len(unparsed)} assembly clause(s) not modeled by the parser, so not swept: "
              + ", ".join(sorted({u for u, _ in unparsed})))

    def status_cell(ok):
        return {True: " ok ", False: "FAIL", None: " -- "}[ok]

    cfg = XLEN_CONFIG[args.xlen]
    results = []
    skipped = []
    nonexistent = []
    march = test_config.march_string(args.xlen, march_ext)
    req_exts = test_config.required_extensions(march_ext, extra=[extension])
    manifest = test_config.Manifest(extension, args.xlen, march)
    for name, insn in sorted(insns.items()):
        out_prefix = os.path.join(out_dir, name.replace(".", "_"))

        # The model's own `when xlen == N` guard is the authoritative answer to
        # "does this mnemonic exist at this XLEN"; the assembler round-trip
        # below is the fallback for clauses that carry no guard but still
        # differ (checked in that order, cheapest-and-most-authoritative first).
        if insn.xlen_guard is not None and insn.xlen_guard != args.xlen:
            skipped.append((name, insn.xlen_guard))
            print(f"SKIP  {name:14s} {'':22s}  RV{insn.xlen_guard}-only per the model's own "
                  f"`when xlen == {insn.xlen_guard}` guard -- not a failure")
            continue

        opcodes, asm_err = model_opcodes.opcodes_for(
            insn, args.xlen, cfg["as_bin"], cfg["objdump_bin"], march_ext=march_ext,
            csr_addr=args.csr)
        if opcodes is None:
            if assembles_for_other_xlen(insn, args.xlen, march_ext, args.csr):
                other = 64 if args.xlen == 32 else 32
                skipped.append((name, other))
                print(f"SKIP  {name:14s} {'':22s}  RV{other}-only "
                      f"(assembles for RV{other}, not RV{args.xlen}) -- not a failure")
                continue
            if "unrecognized opcode" in asm_err:
                # No such mnemonic at *either* XLEN. This framework builds a
                # clause's mnemonics as the cross-product of its string tables
                # (LOAD's is `"l" ^ width_mnemonic ^ maybe_u`), and that
                # over-generates: it yields `ldu` alongside the real
                # lb/lbu/lh/lhu/lw/lwu/ld, because the model constrains which
                # combinations are legal in its *encdec* clause, not its
                # assembly one. Counting those as failures would be simply
                # wrong -- they aren't instructions. Counting them silently
                # would be worse, so they get their own listed category: if a
                # march string is ever wrong, every mnemonic in the sweep
                # lands here at once and that's obvious on sight.
                nonexistent.append(name)
                print(f"NOENC {name:14s} {'':22s}  no such mnemonic at either XLEN "
                      f"(mnemonic-table cross-product artifact) -- not a failure")
                continue
            results.append((name, None, False, {}, "assemble: " + asm_err))
            print(f"FAIL  {name:14s} {'':10s}  gen=FAIL sail=FAIL spike=FAIL qemu=FAIL   <- assemble: {asm_err}")
            continue

        gen_ok, stdout, gen_err = run_isla_testgen(
            opcodes, out_prefix, args.xlen, boot_fixed_entry=args.boot_fixed_entry,
            expect_trap_cause=(args.expect_trap_cause if args.expect_trap_cause is not None
                               else TRAP_EXPECT.get(insn.union_name)),
            isla_toml=isla_toml,
            pending_interrupt=insn.union_name in PENDING_INTERRUPT,
            extra_args=(list(XRET_SETUP.get(insn.union_name, ()))
                        + (["--inhibit-counters"] if args.inhibit_counters else [])
                        + list(args.isla_arg)),
            # An f-register operand is the reliable signal that the harness
            # needs mstatus.FS on -- more reliable than the extension label,
            # since C's `c.flw`/`c.fsd` are FP instructions living in the C
            # extension's own files.
            # Vector FP instructions (`vfadd.vv`, `vfclass.v`, ...) take only
            # *vector* register operands, so an f-register operand isn't a
            # sufficient signal for them -- and they need mstatus.FS on just
            # as much as scalar FP does. Enabled across the whole vector sweep
            # rather than guessed at per mnemonic.
            enable_fp=(args.enable_fp
                       or any(k[0] in ("freg", "cfreg") for k in insn.operand_kinds)
                       or extension in ("V", "vector_crypto", "bfloat16")),
            # Same signal one register file over. Both can be needed at once:
            # `vfadd.vv` is a vector instruction, but the FP unit has to be on
            # for it too, and bfloat16's `vfwmaccbf16.vf` takes operands from
            # both files in one instruction.
            enable_vector=args.enable_vector
                          or extension in ("V", "vector_crypto", "bfloat16")
                          or any(k[0] == "vreg" for k in insn.operand_kinds))
        elf_path = out_prefix + ".elf"
        if gen_ok:
            # Recorded only for tests that actually built. A manifest entry for
            # an ELF that does not exist would let a runner "select" a test it
            # cannot run, which is a worse failure than the test being absent.
            params = {}
            # `--csr` always has a value (it defaults to mscratch), so it is not
            # evidence that this test touches a CSR at all -- only the operand
            # kind is. Keyed off the wrong one, every test in every extension
            # claimed to require 0x340.
            if any(k[0] == "csr" for k in insn.operand_kinds):
                params["CSR"] = args.csr
            if args.enable_fp or extension in ("V", "vector_crypto", "bfloat16"):
                params["MSTATUS_FS"] = "enabled"
            if args.enable_vector or extension in ("V", "vector_crypto", "bfloat16"):
                params["MSTATUS_VS"] = "enabled"
                params["VLEN"] = 128  # the only value that works -- findings C8
            test_config.annotate(out_prefix + ".s",
                                 test_config.header(req_exts, march, args.xlen, params))
            manifest.add(name, elf_path, req_exts, params)
        if not gen_ok:
            # Carry isla-testgen's own last line through rather than flattening
            # every generation failure to the same three words: "TIMEOUT
            # (generation)" and a real unsat are very different findings, and
            # the difference was being thrown away here.
            why = f"generation failed: {gen_err}" if gen_err else "generation failed"
            sim = {k: (False, why) for k in ("sail", "spike", "qemu")}
        else:
            # Retried once on failure, and the retry is the verdict. Simulator
            # runs carry a wall-clock timeout, so a loaded machine makes a
            # perfectly good test "fail" -- and that is not hypothetical here:
            # re-running 18 apparent V failures on an idle machine turned 15 of
            # them green, and `vclmulh.vx` flipped between two consecutive runs
            # on an identical opcode. Reporting those as failures sent real
            # investigation after nothing. Generation is not retried, since it
            # has no timing dependence of that kind.
            def _retry(fn, *a, **kw):
                ok, why = fn(*a, **kw)
                return (ok, why) if ok is not False else fn(*a, **kw)

            sim = {"sail": _retry(run_sail_sim, elf_path, args.xlen),
                   "spike": _retry(run_spike, elf_path, args.xlen, march_ext=march_ext),
                   # QEMU's "spike" machine always boots to a fixed 0x80000000
                   # regardless of the ELF's entry point, so it can only ever
                   # run --boot-fixed-entry tests -- running it on a default
                   # -layout ELF doesn't test anything, it just hangs until the
                   # timeout and reports a "failure" that says nothing about
                   # the instruction. Reported as not-run rather than failed.
                   "qemu": run_qemu(elf_path, args.xlen) if args.boot_fixed_entry
                           else (None, "not run (needs --boot-fixed-entry)")}

        # None (simulator not installed) doesn't count against pass/fail --
        # only real failures on an available simulator do.
        overall_ok = gen_ok and all(ok is not False for ok, _ in sim.values())
        results.append((name, opcodes, gen_ok, {k: v[0] for k, v in sim.items()}, sim))
        status = "PASS" if overall_ok else "FAIL"
        opcode_str = "+".join(f"{op:#010x}" for op in opcodes)
        reasons = "; ".join(f"{k}: {v[1]}" for k, v in sim.items() if v[0] is False)
        print(f"{status}  {name:14s} {opcode_str:22s}  "
              f"gen={'ok' if gen_ok else 'FAIL'} "
              f"sail={status_cell(sim['sail'][0])} spike={status_cell(sim['spike'][0])} qemu={status_cell(sim['qemu'][0])}"
              + (f"   <- {reasons}" if reasons else ""))

    total = len(results)
    passed = sum(1 for r in results if r[2] and all(v is not False for v in r[3].values()))
    print(f"\n{passed}/{total} instructions: parsed from the model, generated, and passing on every available "
          f"simulator (ok/FAIL/-- = pass/fail/not run)")
    if skipped:
        print(f"  ({len(skipped)} skipped as wrong-XLEN, not counted above: "
              f"{', '.join(n for n, _ in skipped)})")
    if nonexistent:
        print(f"  ({len(nonexistent)} not real mnemonics, not counted above: "
              f"{', '.join(nonexistent)})")
    for sim_name in ("sail", "spike", "qemu"):
        oks = [r[3].get(sim_name) for r in results if r[3]]
        n_pass = sum(1 for v in oks if v is True)
        n_fail = sum(1 for v in oks if v is False)
        n_na = sum(1 for v in oks if v is None)
        print(f"  {sim_name:6s} {n_pass} pass, {n_fail} fail, {n_na} not installed")
    if (m := manifest.write(out_dir)):
        print(f"  manifest: {m} ({len(manifest.tests)} selectable tests)")
    return results


if __name__ == "__main__":
    main()
