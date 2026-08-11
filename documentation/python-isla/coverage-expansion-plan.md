# Coverage expansion plan — reconstructed from implementation

This started as a forward-looking plan written before the work. It has been rewritten
**backwards from the results**, because implementing it changed the shape of the problem
enough that the original ordering and sizing were actively misleading. The milestone IDs
(M0–M9) are kept for continuity with anything that references them, but what each one
*is* has been rewritten to match what it actually took.

Companions: [`findings.md`](findings.md) for the defects and divergences the sweep turned
up, [`extension-coverage-tracker.md`](extension-coverage-tracker.md) for per-extension
status, [`privileged-instruction-coverage.md`](privileged-instruction-coverage.md) for the
privileged subset in detail.

---

## What the original plan got wrong

Four things, each of which cost or would have cost real time. They matter more than the
milestone list, because they generalise to whatever gets planned next.

### 1. The parser was the actual foundation, and it wasn't a milestone at all

The plan opened at M0 (trap harness) and treated the instruction list as solved — the
sweep already "worked", so the question was only which extensions to point it at.

It reached roughly **60 instructions**. It now reaches **1280 across 23 extensions with
zero unparsed clauses.** The gap was not difficulty; it was silence. A `mapping clause
assembly` whose mnemonic table lived in a *different file* was dropped with no error, no
warning, and no effect on the exit status. Two instances of that one bug:

- `width_mnemonic` (b/h/w/d) lives in `core/types.sail`. Without it, **every load and
  store in base I** — and every FP load/store — parsed to nothing.
- `maybe_aqrl` is a *tuple-typed* mapping in `extensions/A/aext_types.sail`, which the
  table regex didn't match. Without it, **the entire A extension** produced zero
  instructions.

Both looked identical to "this extension is fine, nothing to report."

**The durable fix is not the parser changes, it's the reporting.** Unparsed clauses are
now printed per sweep. Everything else in this section was found by reading that line.

**Generalisation**: in a framework whose value proposition is "we test what the model
actually implements", a silent zero is the worst possible failure mode and the one to
engineer against first. Any future extension of the parser should add its skip path to
that report before adding its happy path.

### 2. Almost every "broken extension" was a disabled feature

This is the single biggest time sink the plan failed to anticipate, and it recurred in
*seven* independent places. In each, correctly-encoded instructions failed on both
simulators, and the cause was that the relevant unit was off:

| What failed | Why | Fix |
|---|---|---|
| All of F/D | `rv_enable_fdext = false` in the isla config | `--config-override` |
| All of F/D, again | `mstatus.FS` resets to Off — every FP instruction illegal regardless of `misa.F` | `--enable-fp` |
| All of V | `mstatus.VS` resets to Off | `--enable-vector` |
| All of V, again | `vtype.vill` resets set — vector ops trap until some `vset*` runs | `vsetvli` prelude |
| Most of V, a third time | SEW=8 is not a legal FP element width | `vsetvli ... e32` |
| `wfi` | stalls forever with no interrupt pending | `--pending-interrupt` (CLINT MSIP) |
| `mret`/`sret` | `MPP`/`SPP` reset to User; PMP then denies the next fetch | `--preload-xpp`, `--pmp-allow-all` |
| `mcycle`/`minstret` | counting during the test | `--inhibit-counters` |

**Generalisation**: before concluding that an extension is broken or unsupported, check
what has to be *enabled* for it. The reset state of a RISC-V hart is far more restrictive
than "everything on" — `mstatus` resets with **every field zero**, which means FP off,
vector off, and previous-privilege = User. This should be the first hypothesis for any
whole-extension failure, not the last.

### 3. Setup belongs in the harness preamble, not in isla's opcode sequence

The original M0 assumed `mret` would be tested by giving isla a *sequence* — configure
`mstatus`, then `mret`, then observe. That approach was pursued at length and failed:
isla cannot place an opcode at an address reached through a **data-computed** control
transfer, which is exactly `mret`'s `mepc`-derived jump (`Placing opcode in memory
unsatisfiable`, `execution.rs`'s `setup_opcode`). It works for `jal`, whose target is
immediate-encoded, and not for `mret`.

The resolution was structural, not a workaround: **state setup goes in the preamble,
which runs before isla's test region and involves no solving at all.** Once that was
clear, `mret`/`sret` fell out in one pass, and so did every subsequent scenario —
`--pmp-allow-all`, `--pmp-deny`, `--run-in-supervisor`, `--sv39` are all the same move.

**Generalisation**: the opcode sequence handed to isla should contain the instruction
under test and the minimum needed to make its operands well-defined. Anything
architectural — privilege, CSRs, page tables, PMP — is preamble work.

### 4. A passing test proves nothing without a negative control

Two tests in this round passed **vacuously** and were caught only by deliberately
constructing the case that had to fail:

- **`mret`**: passes trivially if `MPP` is left at Machine, since no privilege change
  happens. Fixed by targeting Supervisor, and *verified* by dropping `--pmp-allow-all` —
  which must fail, and does, precisely because the privilege change is real.
- **PMP violation**: with `--trap-is-pass`, restricting a region the test never touches
  reported a **pass** — no trap occurred, so control fell through to the ordinary
  comparison, which succeeded because the access legitimately worked. Fixed by making
  `finish` unreachable-by-design in that mode.

Every scenario test added since carries its controls: wrong-cause must fail, and
no-violation must fail.

**Generalisation**: for any test whose pass condition is "something was prevented",
write the version where it isn't prevented first, and confirm it fails.

### 5. The triage rule that resolved everything else

**When Sail and Spike fail identically, our expectation is wrong — not the two
implementations.** This settled `mcycle`/`minstret`/`mip` (self-updating registers can't
match a static expected value), `satp` (WARL legalisation), and the vector SEW problem,
without a single wasted investigation into either simulator.

The converse is where the value is: when they **disagree**, that is a finding. It
happened three times — see [`findings.md`](findings.md) A1–A3, including a real PMP
divergence where Sail follows the spec and Spike does not.

---

## The dependency graph that actually emerged

The original ordering was `M0 → M1 → M2/M3 → M4 → M5 → M6 → M7`, with M0 foundational
and M3 "the largest, most novel milestone". What the work actually looked like:

```
  Parser completeness  ← the real foundation; unnumbered in the original plan
           │
           ├──────────────► Feature-enable flags ──┬──► F/D  (M6)
           │                (the recurring theme)  ├──► V    (M7)
           │                                       └──► counters (M1)
           │
           └──► Preamble state-setup ──┬──► xRET / wfi   (M0)
                                       ├──► PMP allow/deny (M2)
                                       └──► Supervisor + Sv39 (M3)
```

Two corrections to the original sizing:

- **M3 was called "large — new harness shape, not an extension of the existing one".** It
  was neither. Once `--run-in-supervisor` existed, Sv39 was one page table and six
  instructions of preamble. It came in *after* M2 on the same afternoon, both built on
  the same primitive.
- **M2 and M3 were gated on M0 in the plan, and that was right — but for the wrong
  reason.** They needed M0's *preamble machinery*, not its trap handler. The trap handler
  turned out to be reusable for them (`--trap-is-pass` extends `--expect-trap-cause`),
  but the load-bearing part was the architectural decision in lesson 3.

---

## Status

| Milestone | Status |
|---|---|
| **Parser completeness** *(unplanned)* | **done** — 1280 instructions, 23 extensions, zero unparsed clauses |
| M0 — Trap/privilege-change harness | **done** — 15/15 privileged instructions on Sail and Spike, both XLENs |
| M1 — Widen CSR sweep | **done** — 29 CSRs swept; 18 pass 6/6 on both, the rest diagnosed in `findings.md` |
| M2 — PMP-violation scenario | **done** — violation traps with the right cause; both negative controls fail as required |
| M3 — VM/page-table-walk scenario | **core done** — Sv39 identity map + unmapped-page fault, with controls proving translation is genuinely active |
| M4 — Pointer masking | not started |
| M5 — I, M, A + small extensions | **done** — 14 extensions, both XLENs |
| M6 — F/D, C, B, K | **done** — F/D 118/118 RV64; C's isla defect worked around by a template (`findings.md` C11) |
| M7 — V, vector crypto | **done** — 1264/1348 passing on both simulators |
| M8 — Config-awareness | **mechanism done** — `--config-override`; full matrix not enumerated |
| M9 — CI integration | **reporting done** — `regression.py` (exit 1 on regression) and `status_report.py`; not yet wired to a runner |

**No framework failures remain in the current deliverable's scope.** Every in-scope
failure is now on the model's side: 12 × `ssamoswap` (A4) and 2 × PMP entry 0 (A2). The
last two framework gaps were closed by routing the solver-timeout instructions to the
oracle (C12) and the compressed jumps to a template (C11). One failure, `c.srli`, is
unexplained — it failed once in a recorded sweep and passed on retry, and has not been
reproduced since; it is counted as a framework failure until it is understood.

---

## M0 — Trap/privilege-change harness — **done**

**15/15 privileged instructions pass on Sail and Spike, RV32 and RV64**, up from 10/15.

- **`ecall`/`ebreak`** — `--expect-trap-cause <mcause>`. The trap vector checks `mcause`,
  advances `mepc` past the trapping instruction (2 or 4 bytes depending on whether it was
  compressed — ported from `act4/trap-handler-findings.md` rather than re-derived), and
  returns. Verified on the negative path: a wrong expected cause still fails.

  *Note on a plan assumption that was wrong*: the original text said to route these
  "through ACT4's existing pipeline". ACT4's real trap handler loads only via
  `#include "riscv_arch_test.h"`, which this generator never emits — earlier
  "ACT4-compatible" tests matched its *format* closely enough to build in its tree, but
  never linked its environment code. A standalone handler was built instead.
- **`wfi`** — `--pending-interrupt`. Raises a machine software interrupt through the
  CLINT's MSIP register (`mip.MSIP` is read-only to CSR writes, so the memory-mapped
  CLINT is the only architectural route) and enables it in `mie`, while deliberately
  leaving `mstatus.MIE` clear. The spec has WFI resume on a pending enabled interrupt
  regardless of the global enable, but only *take* the trap when MIE is set — so this
  tests WFI rather than interrupt delivery, and the harness's checks still run after.
- **`mret`/`sret`** — `--preload-xepc` (their next-PC is a CSR isla leaves unconstrained
  and solves to its own exit trampoline), `--preload-xpp` (MPP/SPP reset to User), and
  `--pmp-allow-all` (nothing outside M-mode is accessible without a PMP entry). Both
  target **Supervisor**, and the negative control is described in lesson 4 above.

**Still open from this milestone**: real *interrupt delivery* — the trap actually being
entered, `mcause` carrying the interrupt bit, a handler running. All the machinery now
exists (CLINT access, `mie`, a trap-aware vector); the test isn't written.

## M1 — CSR sweep across real CSRs — **done**

[`csr_sweep.py`](../../python-isla/csr_sweep.py) drives all six Zicsr instructions against
each of 29 CSRs. **18 pass 6/6 on both simulators**, covering Smstateen, Ssstateen, Sstc,
Sscounterenw, Smcntrpmf, Sscofpmf and Ssqosid — seven tracker rows that were "not started".

Read-only CSRs are run with `--expect-trap-cause 2`, so they pass only if the model
actually *rejects* the write. Running them the other way round would have reported correct
behaviour as failure.

Three things this milestone taught that the plan didn't anticipate:

- **Spike's ISA string is part of the test.** A CSR from an extension Spike wasn't told
  about reads as an illegal instruction, externally indistinguishable from a model bug.
  `stimecmp` failed all six on Spike until `sstc` was added — and then *aborted* until
  `zicntr` was added too (`findings.md` A3).
- **Some CSRs are structurally untestable by this harness**, not merely failing — see
  lesson 5 and `findings.md` C1/C2.
- **`pmpcfg0` is far slower to solve than its neighbours** (struct-typed), which read as a
  failure under the original timeout.

## M2 — PMP-violation scenario — **done**

`--pmp-deny <addr>` configures PMP entry 0 to deny all access to the 4KiB region
containing `addr`, and entry 1 to permit everything else. PMP is first-match, so the
denying entry has to come first — reversed, nothing is ever denied and the test passes
while proving the opposite.

Entry 0 is **locked** (L=1), which is what makes a PMP entry apply to Machine mode at all
— that avoids needing a privilege drop for the basic case, though it composes with
`--run-in-supervisor` when one is wanted.

`--trap-is-pass` was added here and is the reusable part: for a violation test the trap
*is* the result, and resuming into the ordinary final-state comparison fails by
construction, since the faulting instruction never wrote its destination register.

Verified with a full control set — positive (no restriction, access succeeds), the
violation itself (cause 5, load access fault), wrong-cause (must fail), and
no-violation (must fail). All four behave correctly on both simulators.

## M3 — Virtual memory / page-table walk — **core done**

Two new primitives, both preamble work:

- `--run-in-supervisor` — enters the test in S-mode by setting `mepc`/`MPP` and executing
  `mret` in place of the usual jump. Machine mode bypasses everything this milestone
  tests: PMP permits unmatched accesses there and `satp` doesn't apply to it at all.
- `--sv39` — builds an Sv39 root page table that identity-maps memory with **gigapages**
  (one leaf PTE per 1GiB, no second-level tables) and installs it in `satp`.

Identity mapping is the point rather than a simplification: it makes translation *active*
while leaving every address the test already uses valid, so the walk is genuinely
exercised without the test having to know about it.

**The proof that translation is really on** is the control set, since an identity-mapped
test that silently skipped translation would also pass:

| Case | Expected | Result |
|---|---|---|
| Sv39 on, unmapped VA, expect cause 13 (load *page* fault) | pass | pass, both simulators |
| Sv39 **off**, same VA, same expectation | must fail | fails — it's an access fault, not a page fault |
| Sv39 on, **mapped** VA, same expectation | must fail | fails — no fault occurs at all |

Cause 13 is reachable only through a real page-table walk, and it appears exactly when
the walk is enabled and the address is outside the map.

**Remaining in this milestone** (all now incremental, not new harness work): Sv32/Sv48/Sv57
as depth variants, `sfence.vma`'s actual effect (modify a PTE, fence, confirm the stale
translation is gone — plus the negative control that it *isn't* gone without the fence),
A/D-bit behaviour (Svadu/Svade), and the PTE-encoding variants (Svnapot, Svpbmt,
Svrsw60t59b). Each is a different page-table content against the same harness.

## M4 — Pointer masking — not started

Unchanged from the original scoping and still small: re-run existing load/store tests
under a pointer-masking CSR configuration and confirm the effective address is masked.
`--config-override` and the preamble CSR machinery are both in place.

## M5 — I, M, A and the small unprivileged extensions — **done**

14 extensions, both XLENs. Base I re-swept under the model-sourced pipeline, replacing the
stale `riscv-opcodes`-driven result: **45/45 RV32, 57/57 RV64**. A: **44/44 and 88/88**,
zero failures — an extension that had been producing *zero* instructions before the parser
work. M, Zifencei, Zicond, Zawrs, Zicbom/Zicbop/Zicboz, Zihintntl, Zihintpause, Zimop,
Zcmop all clean; `cfi`'s `ssamoswap.*` family is the one outstanding group. It was
originally read as "needs shadow-stack state enabled — lesson 2 again", and that turned
out to be wrong in an interesting way: the state *is* set up correctly, and the model
itself asserts on the resulting page-table entry. See `findings.md` **A4**. The 12
failures are the model's, not ours.

**Sized "small" in the original plan, and that was right** — but only because the parser
work happened first. Against the original parser this milestone would have silently
"passed" while testing a fraction of what it claimed.

## M6 — F/D, C, B, K — **done**

- **F/D**: 118/118 RV64, 106/106 RV32. Needed the isla config override *and* `--enable-fp`,
  and a third fix: the model's assembly clauses emit a rounding mode uniformly, but GNU as
  rejects it on conversions where rounding can't occur. Resolved by having `opcodes_for`
  retry once without the trailing literal operand rather than hand-maintaining a list —
  the assembler is this framework's encoding source of truth, so taking its answer is the
  consistent move.
- **B**: 28/29 RV32, 38/40 RV64. **K**: 23/25, 21/25. The residual failures were generation
  timeouts and OOMs on genuinely SMT-hard instructions (population count, AES rounds,
  crossbar permutations). Raising the timeout 60s → 300s did not help, because several are
  SIGKILLed rather than timed out. **Now closed by routing all ten to the oracle**
  (`model-scalar` backend) — see `findings.md` C12. A solver-performance property was
  being counted as a test result.
- **C**: 25/27 RV32, 31/33 RV64. Required two real fixes — opcode width must come from the
  encoding (`bits[1:0] == 0b11`) rather than string padding, and preludes must be
  assembled `.option norvc` to work around the isla compressed-instruction defect.
  `c.jr`/`c.jalr` stayed blocked by that defect longest, and are **now covered by a
  hand-written template** with a poison instruction and a verified negative control
  (`findings.md` C11). The isla defect is still there; only the gap is closed.

**The plan predicted the FP register file would be "a real, scoped parser extension".**
Correct, and it generalised: the same shape covered compressed registers (`creg`/`cfreg`)
and vector registers, so M7's operand work was nearly free once M6's was done.

## M7 — V and vector crypto — **done**

627 V instructions, 41 vector-crypto, 6 bfloat16 — all parse with zero unparsed clauses.

| target | pass | fail |
|---|---|---|
| V RV32 | 571 / 627 | 56 |
| V RV64 | 604 / 627 | 23 |
| vector_crypto RV32 / RV64 | 39 / 38 of 41 | 2 / 3 |
| bfloat16 RV32 / RV64 | 6 of 6 each | 0 |
| **total** | **1264 / 1348** | **84** |

Full-project total, all 23 extensions × both XLENs: **1973 passing, 111 failing**
(`results/full-sweep-2026-08-06.log`).

The plan called this "large, and genuinely uncertain until M6 is done", and flagged
vector operands and `vtype` configuration as new shapes. **The operand work was small
(one more register kind); the state and operand *legality* were the whole difficulty**,
and it took four separate discoveries:

1. `mstatus.VS` resets to Off — every vector instruction illegal.
2. `vtype.vill` resets set — vector instructions still trap until a `vset*` runs.
3. That prelude needs **SEW=32**, not 8: 8 is not a legal FP element width, so every
   vector-FP instruction was illegal.
4. Vector operands must be allocated at **`v8`/`v16`/`v24`**, not `v1`/`v2`/`v3` —
   register *groups* must be aligned to their own size (`findings.md` C4). Worth 290
   instructions on its own.
5. SEW must match the mnemonic's own element width, or segment instructions break
   `nf * EMUL <= 8` (`vlseg5e64.v` needs 10 registers at SEW=32, 5 at SEW=64).
6. Strided accesses must have their **stride register** initialised — it decides which
   addresses are touched, and unconstrained it walks out of the declared region.
7. `bf16` is a 16-bit format, so every vector bf16 instruction needs SEW=16.
8. Zvbc's `vclmul*` is 64-bit-element only; and the EGW-256 crypto family (SM3, SM4,
   AES, SHA-2, GHASH) needs **LMUL>=2** — see the VLEN note in `findings.md`.
9. `.vf8` extends from SEW/8 bits, so it needs SEW=64 (4 bits is not an element width).

Every one of the four is lesson 2 again: not a model defect, a feature not enabled or an
operand not legal. None of M7's failures to date has been a bug in the model.

**Remaining 142**, in three groups: indexed/strided segment addressing (~29 of RV64's 49,
one more targeted fix — see `findings.md` C4); a long tail of ~20 singletons, likely
several independent small causes; and the crypto/bfloat16 residue, where `vclmul*`,
`vaesdm`, `vsm3*` look like generation timeouts on SMT-hard operations, matching B/K's
`cpop`/`aes64*`.

## M8 — Config-awareness — **mechanism done**

`--config-override KEY=VALUE` writes a per-sweep isla config, and the F/D-on axis is
already used in anger by M6. The remaining work is enumeration rather than mechanism: the
RV32/RV64 × F/D on-off × VLEN/ELEN matrix, RV32E/RV64E, and Zfinx/Zdinx as a register-file
variant of M6's F/D sweep.

## M9 — CI integration — **reporting done**, wiring not started

The half that needed thought is built. Two scripts answer the two questions CI has to
answer, and both deliberately avoid a raw pass/fail count, which on this project is
misleading in both directions (most failures are expected; and before B4 was fixed, 47.7%
of *passes* checked nothing):

- **`regression.py`** — compares each instruction's *status* (verified-by-isla,
  verified-by-oracle, stateless, vacuous, uncovered) against a stored baseline, ranked so
  that "passes but checks nothing" counts as a regression from "verified". Exits 1 if
  anything got worse, so it drops straight into a CI step. `--update` accepts the current
  state as the new baseline; `--json` for machine consumption.
- **`status_report.py`** — per-extension pass/fail, with every failure attributed to
  MODEL, FRAMEWORK, or ROUTED from its *recorded reason*, not a guess. This is the
  distinction that matters to a reader: which failures are findings worth reporting
  upstream, and which are ours to fix.

Both run from `python-isla/`. What remains is process: make it a standing target, then
open the PR against `sail-riscv`'s CI. Gated on maintainer review cadence, not on us.

---

## What to do next, re-ordered from evidence

1. **Report A4 upstream** (`findings.md`) — a defect that makes a documented feature
   unreachable in the Golden Model, with a one-file reproducer. Highest value per unit of
   effort of anything on this list, and it is not work we can do ourselves.
2. **Re-run the full sweep and re-baseline.** Every fix since the last recorded sweep is
   verified individually but not reflected in the results on disk, so any number quoted
   from `_results/` currently understates where things stand. Also folds the `csr-model`
   corpus into the coverage measurement for the first time.
3. **Reproduce or retire `c.srli`.** One unexplained failure is worth more attention than
   its count suggests: an intermittent failure is either a real race in the harness or a
   stale artefact, and both matter.
4. **Interrupt delivery** — the last real gap in M0's "trap and interrupt handling",
   which the RFP lists as required. All machinery exists.
5. **RV32 for the oracle backends**, which are RV64-only. The RFP wants both XLENs in CI,
   and the isla flow already does both.
6. **M3's remaining variants** — now incremental against a working harness.
7. **M4**, which is genuinely small now that the CSR and config machinery exist.
8. **M8's matrix**, then **M9's wiring**.

**Report the isla compressed-instruction defect** (B1) is no longer on this list as a
blocker: C11 closed the coverage gap it caused. It is still worth reporting, but it no
longer caps what the C extension can claim.
