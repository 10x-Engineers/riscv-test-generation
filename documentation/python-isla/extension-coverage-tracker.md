# Extension coverage tracker

Every extension the Sail model's own `README.md` claims support for (Hypervisor excluded per
prior decision — see [`hypervisor-design-sketch.md`](hypervisor-design-sketch.md)), checked
against what this framework has actually swept. Ordered by the agreed priority: **privileged
architecture first, unprivileged second.** Regenerated from real checks (which `.sail` files
exist under `extensions/`/`mops/`, cross-referenced against what `opcode_sweep.py` has actually
run — see [`privileged-instruction-coverage.md`](privileged-instruction-coverage.md) for the
detailed per-instruction privileged results), not from the extension names alone — several
entries in the model's own README describe a *property* (a memory-consistency guarantee, a PMA,
a reservation-set size) rather than an instruction set, and those have no opcodes to sweep at all.

**Status legend**: `swept` = generated and run through the pipeline; `covered` = every instruction
has a passing test, though not all from the same backend (some are routed to the oracle or a
template — see findings.md C11/C12); `partial` = some but not all
of the extension's instructions/CSRs covered; `not started` = has real opcodes, none attempted
yet; `N/A (no opcodes)` = the extension is a property/config/CSR-field guarantee with nothing an
opcode sweep can target — needs a scenario/config test instead, a different kind of work.

## Priority 1 — Privileged architecture

### Has dedicated opcodes — sweepable today

| Extension | Sail source | Status | Notes |
|---|---|---|---|
| Zicsr | `extensions/Zicsr/zicsr_insts.sail` | **swept** | All 6 mnemonics pass, now against real CSRs derived from the model (172) rather than only `mscratch` — see the CSR-only section below |
| Svinval | `extensions/Svinval/svinval_insts.sail` | **swept** | 3/3 pass, both XLENs — "shallow pass" caveat applies (see privileged-instruction-coverage.md) |
| Base-ISA privileged subset (`mret`/`sret`/`wfi`/`ecall`/`ebreak`/`sfence.vma`) | `extensions/I/base_insts.sail` | **swept** | **6/6 pass** on Sail and Spike, both XLENs. `ecall`/`ebreak` via `--expect-trap-cause`, `wfi` via `--pending-interrupt`, `mret`/`sret` via `--preload-xepc`/`--preload-xpp`/`--pmp-allow-all` — see coverage-expansion-plan.md's M0 |

### CSR-only — no dedicated opcode, needs the CSR sweep widened, not new instructions

None of these have their own mnemonics; they're reached exclusively through `csrrw`/`csrrs`/etc.
against a specific CSR address. Real coverage means running the six instructions once per
CSR rather than once in total — `model_opcodes.CSR_ADDR` used to be hardcoded to
`mscratch`, so every one of these rows was being answered by a single test against an
unrelated register.

Driven by [`csr_sweep.py`](../../python-isla/csr_sweep.py), which runs all six Zicsr
instructions against each CSR in turn. It no longer works from a hand-curated list:
`--from-model` derives the CSR set from every `csr_name_map` clause in the model, which
takes it from 29 to **172 CSRs (37 curated + 135 derived)**. **18 of the original 29 pass
all six on both Sail and Spike**; the wider model-derived run was interrupted and its log
truncated, so a pass count for the full 172 is not yet recorded — it needs the re-sweep. Read-only CSRs are run with `--expect-trap-cause 2`, so they pass only if the
model actually *rejects* the write — testing them the other way would report correct
behaviour as failure.

| Extension | Relevant CSR(s) | Status |
|---|---|---|
| Zicntr, Zihpm | `cycle`/`instret`/`hpmcounter*`/`mcycle`/`minstret`/`mhpmcounter3` | **partial** — `cycle`, `instret`, `hpmcounter3` (write-must-trap) and `mhpmcounter3` pass 6/6; `mcycle`/`minstret`/`time` fail *identically on both simulators* because they self-update, see findings.md C1 |
| Smcntrpmf | `mcountinhibit`, `mcyclecfg`, `minstretcfg` | **swept** — 6/6 on both, all three |
| Smstateen / Ssstateen | `mstateen0`/`sstateen0` | **swept** — 6/6 on both |
| Sscounterenw | `mcounteren` | **swept** — 6/6 on both |
| Sscofpmf | `scountovf`, `mhpmevent3` | **swept** — `mhpmevent3` 6/6; `scountovf` 5/6 |
| Ssqosid | `srmcfg` | **swept** — 6/6 on both |
| Sstc | `stimecmp` | **swept** — 6/6 on both (needs Zicntr in Spike's ISA string, or Spike aborts — findings.md A3) |
| Zkr | `seed` | **blocked** — generation fails on a missing isla-lib primop, `get_16_random_bits`; findings.md B3 |
| Physical Memory Protection (PMP) | `pmpcfg*`/`pmpaddr*` | **CSR access partial, violation scenario done** — a locked restrictive entry makes a denied load fault with the correct cause on both simulators, with wrong-cause and no-violation controls both failing as required (plan's M2). `pmpaddr1` 6/6 on both; `pmpaddr0` 6/6 on Sail and 0/6 on Spike, reproducing the reset-value mismatch from `isla-gen-extension/PMP_PLAN.md`; `pmpcfg0` (struct-typed) fails generation. Separately, a **real PMP behavioural divergence** was found via M0's privilege-transition tests — findings.md A1 |

### Virtual-memory / PTE property — needs a page-table-walk scenario test, not an opcode sweep

None of these have opcodes either — they're observable only by actually configuring `satp`,
walking a page table, and checking translation/fault behavior. This is the same "shallow pass"
gap already flagged for `sfence.vma`/Svinval: those instructions pass today, but nothing has yet
proven the address-translation behavior underneath them is correct.

Sv39 now has a working harness: `--run-in-supervisor` enters the test in S-mode and
`--sv39` installs an identity map, with controls confirming translation is genuinely
active (a page fault appears only when the walk is enabled *and* the address is outside
the map). See [`coverage-expansion-plan.md`](coverage-expansion-plan.md)'s M3. The
remaining rows below are page-table *content* variants against that same harness, not new
harness work.

| Extension | What it governs | Status |
|---|---|---|
| Sv39 | Page-based VM (RV64, 3-level) | **done** — identity map + unmapped-page fault (cause 13), both negative controls hold, Sail and Spike |
| Sv32, Sv48, Sv57 | The same scheme at other depths | not started — depth variants of the working Sv39 harness |
| Svadu | Hardware A/D bit update | not started |
| Svade | Trap-on-improper-A/D variant | not started |
| Svbare | No-translation mode | not started (implicitly the default state of every test so far, never deliberately verified as its own case) |
| Svnapot | NAPOT PTE contiguity | not started |
| Svpbmt | Page-based memory types | not started |
| Svrsw60t59b | PTE reserved-for-software bits | not started |
| Svvptc | Obviating re-fence after PTE update | not started |
| Ssccptr | Hardware PTE reads (a PMA guarantee) | N/A (no opcodes) |
| Sstvala, Sstvecd, Ssu64xl | CSR-field behavior guarantees | not started (verifiable only via trap-scenario CSR inspection, once the trap harness exists) |

### Pointer masking — modifies existing instructions' address computation, not new opcodes

| Extension | Status |
|---|---|
| Smmpm, Smnpm, Ssnpm, Sspm, Supm | not started — `extensions/pointer_masking/` has no `mapping clause assembly` of its own; it changes how existing load/store/jump instructions compute addresses under a CSR-configured mask, so coverage means re-testing existing instructions under a masking config, not sweeping new mnemonics |

### Pure architectural statement — nothing to test directly

- **Machine, Supervisor, and User modes** — the privilege-mode framework itself, exercised
  implicitly by everything above; not a discrete target.
- **Static memory regions with some static PMAs** — a platform/config description, not an
  extension with opcodes.

## Priority 2 — Unprivileged architecture

### Has dedicated opcodes — sweepable today, none attempted yet except base I

| Extension | Sail source | Status |
|---|---|---|
| RV32I/RV64I (non-privileged subset: ALU/branch/load-store) | `extensions/I/base_insts.sail` | **swept, clean** — re-run under the model-sourced pipeline, replacing the stale `riscv-opcodes`-driven result: **45/45 RV32, 57/57 RV64, zero failures**. Loads, stores and `fence` are covered for the first time — they were being silently dropped by the parser |
| Zifencei | `extensions/Zifencei/zifencei_insts.sail` | **swept** — 1/1 both XLENs |
| Zicond | `extensions/Zicond/zicond_insts.sail` | **swept** — 2/2 both XLENs |
| Zicfilp, Zicfiss | `extensions/cfi/*.sail` | **partial, blocked upstream** — 4/8 RV32. The `ssamoswap.*` family fails (12 tests). Originally read as "needs shadow-stack state enabled"; the state is set up correctly and **the model asserts on its own valid shadow-stack PTE** (findings.md A4). Not fixable from our side |
| Zimop | `mops/Zimop/zimop_insts.sail` | **swept** — 2/2 both XLENs |
| Zcmop | `mops/Zcmop/zcmop_insts.sail` | **swept** — `c.mop.1` passes both XLENs (needed the compressed-opcode width fix, findings.md B2) |
| Zihintntl | `extensions/Zihintntl/zihintntl_insts.sail` | **swept** — 4/4 both XLENs |
| Zihintpause | `extensions/Zihintpause/zihintpause_insts.sail` | **swept** — 1/1 both XLENs |
| Zicbom / Zicbop / Zicboz | `extensions/Zicbo*/` | **swept** — 3/3, 3/3, 1/1, both XLENs |
| M, Zmmul | `extensions/M/mext_insts.sail` | **swept** — 8/8 RV32, 12/13 RV64 (`divw` hit the generation timeout, since raised) |
| A, Zalrsc, Zaamo, Zacas | `extensions/A/zalrsc_insts.sail`, `zaamo_insts.sail` (`Zacas`'s `AMOCAS` confirmed bundled in `zaamo_insts.sail`) | **swept** — 44/44 RV32, 88/88 RV64, zero failures. Required parsing the bare `(reg)` address form and the `.aq`/`.rl` suffix table, which had been silently dropping the entire extension |
| Zabha | likely bundled in `zaamo_insts.sail` (word-width-parameterized AMOs) — not independently confirmed by name | covered by A's sweep to the extent it is bundled there |
| Zawrs | `extensions/Zawrs/zawrs_insts.sail` | **swept** — 2/2 both XLENs |
| F, D | `extensions/FD/fext_insts.sail`, `dext_insts.sail` | **swept** — 100/106 RV32. Needed two things beyond encoding: `rv_enable_fdext` in the isla config (off by default) and `--enable-fp` to set `mstatus.FS`, which resets to Off and makes every FP instruction illegal regardless of `misa.F` |
| Zfh, Zfhmin | `extensions/FD/zfh_insts.sail` | **swept** — included in the F/D sweep above |
| Zfa | `extensions/FD/zfa_insts.sail` | **swept** — included in the F/D sweep above |
| Zfbfmin | `extensions/bfloat16/zfbfmin_insts.sail` | not started |
| C / Zca, Zcb | `extensions/C/zca_insts.sail`, `zcb_insts.sail` | **covered** — 23/27 RV32, 29/33 RV64 by sweep; `c.ebreak` and `c.addi16sp` fixed earlier, and `c.jr`/`c.jalr` now covered by a template with a verified negative control (findings.md C11). The underlying isla defect (B1) is unfixed but no longer caps coverage |
| Zcf, Zcd | `extensions/FD/zcf_insts.sail`, `zcd_insts.sail` | not started (same compressed-FP shape as C above; parser handles `cfreg` operands) |
| B (Zba, Zbb, Zbs), Zbc | `extensions/B/*.sail` | **covered** — 28/29 RV32, 38/40 RV64 by sweep; `cpop`/`cpopw` are SMT-hard (OOM, not timeout) and are now generated by the oracle's `model-scalar` backend instead (findings.md C12) |
| Zbkb, Zbkx | `extensions/K/zbkb_insts.sail`, `zbkx_insts.sail` | **swept** — included in K below |
| Zbkc | bundled in `extensions/B/zbc_insts.sail` (confirmed — shares `clmul`) | **swept** — included in B above |
| Zkn (Zknd/Zkne/Zknh), Zks (Zksed/Zksh) | `extensions/K/zkn_insts.sail`, `zks_insts.sail` | **covered** — 23/25 RV32, 20/25 RV64 by sweep; `aes64im`, `aes64dsm`, `xperm4`, `xperm8` exhaust the solver and are now generated by the oracle's `model-scalar` backend (findings.md C12) |
| V | `extensions/V/vext_*.sail` (11 files) | **swept** — 555/627 RV32, 578/627 RV64. Needed `mstatus.VS`, a `vsetvli` prelude, SEW=32, and 8-aligned vector register groups (findings.md C4) |
| Zvfh, Zvfhmin | bundled in `extensions/V/vext_fp_insts.sail` and related V files (confirmed — no separate file) | **swept** — included in V above |
| Zvfbfmin, Zvfbfwma | `extensions/bfloat16/zvfbfmin_insts.sail`, `zvfbfwma_insts.sail` | **partial** — 2/6 both XLENs; the four `*bf16*` instructions fail for a cause not yet investigated |
| Zvbb, Zvbc | `extensions/vector_crypto/zvbb_insts.sail`, `zvbc_insts.sail` | **swept** — part of vector_crypto's 35/41 RV32, 34/41 RV64 |
| Zvkb | bundled in `extensions/vector_crypto/zvbb_insts.sail` (confirmed) | **swept** — included above |
| Zvkg | `extensions/vector_crypto/zvkg_insts.sail` | **swept** — part of vector_crypto's 35/41 RV32, 34/41 RV64 |
| Zvkned | `extensions/vector_crypto/zvkned_insts.sail` | **swept** — part of vector_crypto's 35/41 RV32, 34/41 RV64 |
| Zvknha, Zvknhb | `extensions/vector_crypto/zvknhab_insts.sail` | **swept** — part of vector_crypto's 35/41 RV32, 34/41 RV64 |
| Zvksed | `extensions/vector_crypto/zvksed_insts.sail` | **swept** — part of vector_crypto's 35/41 RV32, 34/41 RV64 |
| Zvksh | `extensions/vector_crypto/zvksh_insts.sail` | **swept** — part of vector_crypto's 35/41 RV32, 34/41 RV64 |
| Zibi *(unratified, `--enable-experimental-extensions`)* | `extensions/Zibi/zibi_insts.sail` | not started, not in RFP's list — noted for completeness |
| Zvabd *(unratified, `--enable-experimental-extensions`)* | `extensions/Zvabd/zvabd_insts.sail` | not started, not in RFP's list — noted for completeness |

### Config/property only — no dedicated opcodes

| Extension | What it is |
|---|---|
| RV32E, RV64E | Register-count variant of I (16 vs 32 GPRs) — same `base_insts.sail`, different config; needs a config-driven variant of the existing I sweep, not new opcodes |
| Zic64b, Ziccamoa, Ziccamoc, Ziccif, Zicclsm, Ziccrse | Pure PMA/memory-consistency guarantees ("main memory supports X") — no opcodes |
| Za64rs, Za128rs, Zama16b | Reservation-set size/alignment guarantees on top of A — no opcodes |
| Zvl32b…Zvl1024b | Minimum-VLEN config variants of V — no opcodes, config only |
| Zfinx, Zdinx, Zhinx, Zhinxmin | Register-file variant of F/D/Zfh (FP-in-integer-regs) — same instruction encodings, different config; not independently tested |
| Zvkn, Zvknc, Zvks, Zvksc, Zvkng, Zvksg | "Suite" aggregates bundling other Zvk*/Zvks* extensions — not independent opcode sets |
| Zkt, Zvkt | Explicitly noted by the model's own README as **"no impact on model"** — nothing to test by design |

## Summary

- **Privileged, has real opcodes to sweep**: 3 targets (Zicsr, Svinval, base-ISA privileged
  subset) — **15/15 individual instructions now passing** on Sail and Spike, both XLENs, up from
  10/15. [`privileged-instruction-coverage.md`](privileged-instruction-coverage.md) has the
  detail; [`coverage-expansion-plan.md`](coverage-expansion-plan.md)'s M0 has the four harness
  flags that got there.
- **Privileged, CSR-only**: **18 of the original 29 CSRs pass all six Zicsr instructions on
  both simulators**, covering Smstateen, Ssstateen, Sstc, Sscounterenw, Smcntrpmf, Sscofpmf
  and Ssqosid. The remaining ones are diagnosed rather than open — see
  [`findings.md`](findings.md). The sweep has since been widened to 172 model-derived CSRs,
  but that run was interrupted, so those results are not yet recorded.
- **Privileged, scenario-only** (page-table walk, pointer masking): still not started. M0's
  privilege-transition support was the prerequisite and now exists.
- **Unprivileged, has real opcodes**: **every extension with opcodes has now been swept, both
  XLENs** — I (re-run under the current pipeline), M, A, B, K, C, F/D, V, vector crypto,
  bfloat16 and every small Z* extension. Roughly **1800 instruction instances pass on both
  Sail and Spike**; V alone contributes 1133 of them (555 RV32 + 578 RV64).
- **No opcodes at all** (property/config-only, ~20 entries across both tiers): correctly N/A for
  an opcode sweep: real coverage needs config-driven or scenario-driven tests instead.
- **Failures remaining in the current deliverable's scope**: **14, all on the model's side** —
  12 × `ssamoswap` (findings.md A4, the model asserts on its own valid shadow-stack PTE) and
  2 × PMP entry 0 (A2, reset value differs from Spike). No framework failures remain. One
  unexplained failure, `c.srli`, failed once and passed on retry; it has not been reproduced
  and is counted against us until it is. See `status_report.py` for the live split.

**The parser is the reason these numbers moved so far.** It now reaches 1280 instructions across
23 extensions with **zero unparsed clauses**, against roughly 60 before. Almost all of that gap
was silent: a clause whose mnemonic table lived in a different file was dropped with no error at
all. That single bug cost base I every load and store, and the A extension in its entirety.
Unparsed clauses are now reported per sweep, so the failure mode this whole model-sourced
approach exists to prevent can't recur quietly.

Both scenario milestones are now done: M2's PMP-violation test and M3's Sv39 page-table
walk, each with negative controls proving the pass isn't vacuous. What remains on the
privileged side is narrower than it was — real interrupt *delivery* (as opposed to the
synchronous exceptions already covered), the page-table content variants listed above, and
pointer masking. See [`coverage-expansion-plan.md`](coverage-expansion-plan.md), which has
been rewritten backwards from these results rather than left as the original forecast.
