# ACT4 trap-handler findings — ecall (T-SBI) and ebreak (compressed EPC advance)

Two real, reproducible bugs found in `riscv-arch-test`'s (ACT4) shared trap-handler
infrastructure (`tests/env/rvtest_trap_handler.h`), while checking whether `ecall` and
`ebreak` — the two instructions the [rv_i opcode sweep](../isla-gen/rv_i-opcode-sweep-findings.md)
correctly identified as needing trap-aware testing — actually work end-to-end against the
Sail Golden Model. **Both are now fixed and submitted upstream.**

Both were found by compiling minimal, targeted `.S` tests directly against ACT4's real
framework (`riscv_arch_test.h` → `rvtest_trap_handler.h` → `rvtest_setup.h`, driven by the
UDB-generated `rvtest_config.h` for the `sail-rv32-max` config) and running the resulting
ELF on `sail_riscv_sim`. Getting to that point required standing up the framework's Ruby/UDB
toolchain (Ruby 3.2 via `rbenv`, since the system Ruby was 3.0.2; an IPv4-forcing
`LD_PRELOAD` shim to work around a slow/hanging IPv6 path in Ruby's HTTP client) — that
toolchain is now working and reusable for any future ACT4-based test generation or
compatibility work.

## Finding 1 — `ebreak`: EPC-advance logic doesn't account for compressed instructions

**Status: fixed, PR submitted to `10x-Engineers/riscv-arch-test`.**

### Symptom

A minimal test executing a single `ebreak` in M-mode, relying on ACT4's normal (non-T-SBI)
trap path — record `mode+cause+epc+tval` into the trap-signature region, bump `mepc` past
the instruction, `mret` to resume — failed with a **second, cascading trap**:

```
MCAUSE:  0x00000002   (illegal instruction — not the expected 0x00000003 breakpoint)
MEPC:    0x80000048
Instruction that trapped: 0x0000
```

### Root cause

`tests/env/rvtest_trap_handler.h`, the generic exception handler's EPC-advance code
(`adj_<mode>epc_rtn:`), unconditionally did:

```asm
andi    T3, T3, ~WDBYTMSK    // align EPC down to a 4-byte boundary
addi    T3, T3,  2*WDBYTSZ   // advance by a fixed 8 bytes
csrw    CSR_XEPC, T3
```

This assumes every trapping instruction occupies a full, aligned 4-byte word slot. With the
C extension enabled (true for `sail-rv32-max`), `ebreak` compiles to the 2-byte `c.ebreak`
(`9002`). At an already-4-byte-aligned address (`0x80000040` here), the align-down is a
no-op and `+8` lands at `0x80000048` — six bytes past the correct resume address
(`0x80000042`, i.e. `EPC+2`). `0x80000048` happened to be zero-filled memory, so the CPU
immediately re-trapped on an illegal (all-zero) instruction.

This is generic handler code, not `ebreak`-specific — any exception on **any** compressed
instruction (e.g. a compressed illegal-instruction encoding) would hit the identical bug.

### Fix

Read the trapping instruction's low halfword and use RISC-V's own length-encoding rule
(low 2 bits `== 0b11` ⇒ ≥32-bit instruction, else 16-bit compressed) to advance by exactly
4 or 2 bytes:

```asm
adj_\__MODE__\()epc_rtn:
        lhu     T2, 0(T3)                             // T2 = low 16 bits of trapping instruction
        andi    T2, T2, 3                              // T2 = low 2 bits (RVC length indicator)
        addi    T4, T3, 4                              // T4 = EPC+4 (>=32-bit instruction resume addr)
        li      T6, 3
        beq     T2, T6, 1f                             // low bits == 3 -> >=32-bit instruction, use EPC+4
        addi    T4, T3, 2                              // else: 16-bit compressed, resume at EPC+2
1:      csrw    CSR_XEPC, T4                            // write adjusted EPC
```

Safe to read the instruction at this point: this code only runs for causes where the
instruction was already successfully fetched (illegal instruction, breakpoint, etc.) —
fetch-fault causes take a separate `SKIP_MEPC`-guarded path that doesn't reach this code.

`T1` (the persistent trap-signature write pointer) isn't touched; `T2`/`T4`/`T6` are dead
at this point in both the `SKIP_MEPC` and non-`SKIP_MEPC` builds (confirmed by reading
`TRAP_SIGUPD`'s own register usage — its temp register argument is consumed within each
call, not carried across).

**Verified:** the `ebreak` test now passes (`SUCCESS`, `sail_riscv_sim` exit 0), and the
existing 195/195 passing `rv32i` suite (`act ... -e I`) still passes unchanged after the
fix — no regression.

## Finding 2 — `ecall` via T-SBI: register-restore clobbers the promised return value

**Status: fixed, PR submitted to `10x-Engineers/riscv-arch-test`.**

### Symptom

`RVTEST_TSBI_ECALL_TEST`'s own doc comment promises: *"After this macro, a0 contains the
address of the ecall instruction itself."* A minimal test invoking it and comparing `a0`
against the known `ecall` address failed the comparison.

### Root cause

`T5` (a handler-internal scratch register used to compute the EPC-derived return value)
is aliased to `x10`, which is also `a0` — the header's own comment flags this
(`#define T5 x10 // NOTE: same as a0!`). Tracing register-by-register:

1. Mid-handler, `T5`/`a0` is correctly set to the `ecall` instruction's address (`0x80000044`
   in the trace) — the promised behavior, briefly true.
2. The common return path's generic "restore all saved temp registers" step then reloads
   `x10` from the pre-trap save area — which holds the *original* pre-trap value
   (`0x73`, the T-SBI opcode itself) — clobbering the freshly-computed return value before
   `mret`.

Confirmed with **both** a hand-stubbed minimal config and the real UDB-generated
`rvtest_config.h` for `sail-rv32-max` — same failure either way, ruling out a config/stub
artifact.

### Why this went unnoticed

`grep -rl "TSBI_ECALL_TEST" tests/` returns **zero** real test files — the macro is defined
but exercised by no test in the suite. Meanwhile `ecall` itself is proven sound: every one
of the 195 passing `rv32i` tests boots through `RVTEST_BOOT_TO_MMODE`, which uses `ecall`
internally via the *other* mechanism (`RVTEST_GOTO_MMODE`, the `x3==0` convention, which
doesn't rely on a0 surviving the restore). So this is not "ecall is broken" — it's an
unused convenience macro whose one distinguishing promise (return value in a0) doesn't
survive the shared restore path.

### Fix

The one register-restore instruction that matters (`T5`/`a0`) needed to be skippable
without disturbing the rest of the shared restore-and-return path, since ordinary
exceptions and `GOTO_xMODE` still need `T5` restored to the interrupted program's original
`a0`/discard value normally.

Split `resto_<mode>rtn` into two entry points: the original label (unchanged, still
restores `T5`), and a new `resto_<mode>rtn_keep_a0` immediately after the `T5` restore line
— i.e. it restores everything *except* `T5`/`a0`. `ECALL_TEST`, `CSR_ACCESS`, and the
`RESERVED`/unrecognized-operation fallback (all three of which set `a0` to a value the
caller is meant to receive, per the file's own T-SBI contract table) now jump to the
`_keep_a0` entry point, in both the M-mode and S-mode copies of the handler (8 call sites
total). Every other path — ordinary exceptions, interrupts, `GOTO_xMODE` (which explicitly
documents `a0` as undefined afterward, so restoring the original value there is harmless)
— is untouched, still jumping to the original `resto_<mode>rtn`.

```asm
 resto_\__MODE__\()rtn:
        ...
        LREG    T5, trap_sv_off+5*REGWIDTH(sp)    // restore T5 (x10/a0)

 resto_\__MODE__\()rtn_keep_a0:
        // T-SBI operations that return a value in a0 (ECALL_TEST, CSR_ACCESS, and
        // the RESERVED/error fallback) jump directly here, skipping the T5/a0
        // restore above, so their freshly-computed return value survives to
        // mret/sret.
        LREG    T6, trap_sv_off+6*REGWIDTH(sp)    // restore T6 (x11/a1)
        LREG    sp, trap_sv_off+7*REGWIDTH(sp)    // restore original sp
        mret / sret
```

**Verified:** a minimal M-mode test using `RVTEST_TSBI_ECALL_TEST` and comparing the
returned `a0` against the known `ecall` address now passes (`SUCCESS`) against
`sail_riscv_sim`. The existing 195/195 `rv32i` suite still passes unchanged with both this
fix and Finding 1's fix applied together.

## Net read

- The rv_i sweep's original judgment call — `ecall`/`ebreak` need dedicated trap-aware
  testing, not the plain harness — is now backed by two concrete upstream findings, not
  just a prediction.
- Both bugs are in **shared, generic** trap-handler code, not instruction-specific: the
  EPC-advance bug affects any compressed-instruction trap; the T-SBI bug affects any
  future test that starts using `ECALL_TEST`/`CSR_ACCESS` and expects the documented
  a0/a1 return-value behavior.
- Both are now fixed, verified individually and together (no regression on the existing
  195-test suite), and pushed as separate branches on `10x-Engineers/riscv-arch-test`,
  ready to PR against `riscv-non-isa/riscv-arch-test`'s `act4` branch:
  - `fix-compressed-instr-epc-advance` (Finding 1)
  - `fix-tsbi-ecall-a0-restore` (Finding 2)
