# Technical Plan
## Automated RISC-V test generation from the Sail Golden Model

**What this document is.** The end-to-end technical plan: how a test gets
generated, which extensions we target, how each one is tackled, and what is hard
about each. It is written to be read by an engineer who will have to implement
or maintain it, not only by a reviewer scoring a proposal.

**Companion documents.** `METHODOLOGY.md` argues *why* this approach was chosen,
from measurements. This document says *what we do*. Where a number appears here
it is tagged **[V]** verified by running it, **[I]** inference, **[U]** unknown —
and `comparison/data/` holds the raw span data behind the coverage figures.

---

# Part 1 — The generation flow, end to end

Eight stages. Every one of them has failed in a way that produced a silent wrong
answer at some point in this project, so each is described together with how it
fails, not only how it works.

```
   Sail model sources                Golden Model config (JSON)
           │                                    │
           ▼                                    ▼
  ┌─────────────────┐                 ┌──────────────────┐
  │ 1. INGEST       │                 │  the same config │
  │  parse clauses  │                 │  the emulator    │
  │  → 1280 insns   │                 │  itself takes    │
  └────────┬────────┘                 └────────┬─────────┘
           ▼                                   │
  ┌─────────────────┐                          │
  │ 2. ENCODE       │  render asm → assemble → │
  │  → real opcodes │  objdump → bytes         │
  └────────┬────────┘                          │
           ▼                                   │
  ┌─────────────────┐                          │
  │ 3. STATE        │  preamble  ── or ──  solver
  │  make the machine ready for the instruction │
  └────────┬────────┘                          │
           ▼                                   │
  ┌─────────────────┐                          │
  │ 4. GENERATE     │  symbolic backend  or  concrete oracle  or template
  └────────┬────────┘                          │
           ▼                                   │
  ┌─────────────────┐                          │
  │ 5. EXPECT       │  capture the architectural state that must result
  └────────┬────────┘                          │
           ▼                                   │
  ┌─────────────────┐                          │
  │ 6. EMIT         │  self-checking ELF, HTIF exit, ACT4-compatible macros
  └────────┬────────┘                          │
           ▼                                   ▼
  ┌──────────────────────────────────────────────┐
  │ 7. EXECUTE   Sail  ──and──  Spike (independent)│
  └────────┬─────────────────────────────────────┘
           ▼
  ┌─────────────────┐
  │ 8. MEASURE      │  Sail branch coverage → uncovered list → back to stage 3
  └─────────────────┘
```

## Stage 1 — Ingest the model

We parse the model's own `mapping clause assembly` declarations to get, for every
instruction: its mnemonic, its operand kinds, and any XLEN guard. This is the
step that makes the framework track the model instead of drifting from it.

**Current state: 1280 instructions across 23 extensions, zero unparsed clauses.**
**[V]**

**How it fails.** A clause whose mnemonic table lives in a *different file* was
silently dropped — no error, no warning, just absent. That single bug cost the
base ISA every load and store, and the A extension in its entirety, while the
sweep reported a confident pass rate on what remained. It reached ~60
instructions where it should have reached 1280.

**The rule that came from it:** unparsed clauses are *returned and reported*, per
sweep. A parser that silently skips is worse than one that crashes. This same
failure recurred during the methodology experiments — `LOAD` and `STORE` came
back unparsed because `core/types.sail` wasn't in the table set, and the
experiment reported a confident zero.

## Stage 2 — Encode

The model's assembly syntax and the assembler's are not the same language. We
render each instruction to text, run it through the cross-assembler with the
right `-march`, then `objdump` it back to bytes. If the assembler rejects it, we
learn that immediately rather than emitting a test containing an invalid encoding.

**How it fails.** Two ways, both seen. `-march` missing an extension letter makes
every instruction in that extension "unrecognized opcode" — that is how all of M
vanished from a methodology experiment. And opcode *width* was once inferred from
string padding rather than the encoding, which mis-sized every compressed
instruction.

## Stage 3 — Construct machine state

**This is the stage that matters most, and it is the one the methodology work
identified as carrying ~95% of the coverage gap against hand-written tests.**
**[V]**

Reaching a branch in the model is two separable problems: *what state must hold*
(privilege mode, CSR values, page tables, PMP entries, `mstatus.FS`/`VS`), and
*what instruction runs under it*. Most of the model is not instruction semantics
— it is privilege checks, WARL legalisation, width variants and error paths, all
of which are only reachable from the right state.

Two mechanisms, chosen per target:

- **Preamble** — assembly that puts the machine into the required state before
  the instruction under test. Used where the state can simply be written:
  entering S-mode, installing a trap vector, configuring PMP entries, setting up
  a page table, enabling the FP or vector unit.
- **Solver** — the symbolic engine derives the initial state that makes a
  particular path feasible. Used where the state cannot simply be written but
  must be *deduced* from the path we want.

**The rule that came from it:** set privileged state in the preamble, not by
adding instructions to the solver's opcode sequence. The solver reasons about the
instruction under test; using it to also arrange the machine makes both jobs
harder and the failure harder to attribute.

**How it fails.** A preamble that doesn't actually change anything makes the test
pass for the wrong reason. Two scenario tests passed vacuously during development
and were caught only by deliberately constructing the case that had to fail.
Hence: **every scenario carries negative controls, and a control that stops
failing is a regression.**

## Stage 4 — Generate the test body

Three backends. Routing is a measured decision per instruction class, not a
preference — see Part 2.

## Stage 5 — Capture the expectation

The expected architectural state — register values, CSR values, trap cause — is
recorded so the test can check itself.

**How it fails, and this is the most important failure in the project's history:
47.7% of the corpus once passed while comparing empty expected-state tables.**
**[V]** Every test was green. Nothing was being checked. The defect was invisible
*precisely because* the suite was passing.

**The rule:** a test is only a test if it can fail. Two properties are required —
a non-empty expectation, and demonstrated mutation sensitivity. **Enforcing this
mechanically across the whole corpus is not yet done, and it is the largest open
risk in this plan.** It is listed as a work item in Part 5, not a footnote.

## Stage 6 — Emit

A self-checking ELF that terminates via HTIF, using `riscv-arch-test`-compatible
macros so the output can live alongside the existing suite rather than beside it.

**How it fails.** Jump targets move between the probe run and the final ELF, so a
concrete backend cannot express control flow. And a trap vector must be 4-byte
aligned or `mtvec`'s low bits silently reinterpret as the mode field — found by
tracing an `ecall` landing at the wrong label.

## Stage 7 — Execute on two simulators

The Golden Model, and **Spike, which we did not write**. The second one is the
whole test: everything else is checking our harness against itself.

**The circularity that must be stated:** for oracle-generated tests the expected
values *come from* Sail, so Sail agreeing with them proves nothing. Only the
Spike run is independent. **[V]**

## Stage 8 — Measure coverage, and feed it back

Build the model with `-DCOVERAGE=ON`, replay each ELF alone, compare executed
spans against the model's own `sail_riscv_model.branch_info` manifest — **15,157
spans [V]**. The uncovered list becomes the work queue for stage 3.

**How it fails.** **A test that exits nonzero writes no coverage file at all** —
the runtime flushes on clean exit only. **[V]** So every coverage figure in this
project is *coverage from passing tests*, and understates. It must always be
quoted with its scope; a number without its scope is decoration.

---

# Part 2 — Backend routing: which engine, and why

Three backends, and the routing between them is the framework's core engineering
decision. Each boundary below was found by running into it.

| Backend | What it does | Reaches | Cannot |
|---|---|---|---|
| **Symbolic** (`isla-testgen` + Z3) | executes the model symbolically, solves for initial state | solved-for machine state; PMP and page-table configurations; per-path enumeration | FP arithmetic, vector element paths, VLEN > 129, control flow |
| **Concrete oracle** | renders concrete operands, runs the model, records resulting state | everything the model can execute — including all FP and vector | constructing state by solving; independence from Sail |
| **Template** | hand-written test with a verified negative control | what neither engine can | scale — deliberately capped |

**The measured walls** — these are why routing exists at all:

| Wall | Evidence |
|---|---|
| Symbolic cannot execute FP arithmetic | the model calls SoftFloat (`riscv_f32Add`, `riscv_f64Lt_quiet`); **absent from the IR entirely** **[V]** |
| Symbolic cannot execute vector element paths | every path reaching `read_vreg`/`write_vreg` dies with "Symbolic (bit)vector length in zeros" **[V]** |
| Symbolic cannot represent VLEN > 129 | `B129` bitvector type — VLEN 256 and 512 are unrepresentable **[V]** |
| Symbolic exhausts memory on some instructions | `cpop`, `aes64im`, `aes64dsm`, `xperm4`, `xperm8` are **SIGKILLed, not timed out** **[V]** |
| Concrete cannot express control flow | jump targets differ between probe and final ELF **[V]** |
| Concrete is circular against Sail | expectations come from the model **[V]** |
| **Neither** can produce a compressed jump | symbolic drops compressed control-flow effects; concrete can't place the target **[V]** |

**The routing rule.** Symbolic where state must be *derived*. Oracle where the
model must *execute* something the solver cannot. Template only where both fail,
and every template test carries a negative control proving it isn't vacuous.

"The solver gave up" is a **routing decision, not a dead end** — `cpop` and the
crypto instructions that exhaust Z3 are generated by the oracle instead, and pass.

---

# Part 3 — Extensions: what we target and how we tackle each

Ordered by the RFP's priority: **privileged first**.

## Priority 1 — Privileged architecture

### 1.1 Zicsr and the CSR-only extensions

**Ten privileged extensions define no mnemonics at all.** Smstateen, Ssstateen,
Sstc, Sscofpmf, Zicntr, Zihpm, Zkr, Ssqosid, Sscounterenw, Smcntrpmf — every one
is reached *exclusively* through `csrrw`/`csrrs`/`csrrc` against one specific CSR
address.

**How we tackle it.** Not "cover Zicsr" — that needs six instructions once.
Instead run the six instructions **once per CSR**, driven by `csr_sweep.py`. The
CSR list is derived from the model's own `csr_name_map` clauses: **172 CSRs**
**[V]**, not a hand-curated list that goes stale.

**Difficulties, and how each is handled:**

- **Read-only CSRs.** Every rendered instance is a *write*. Testing them as
  successes would report correct behaviour as failure, so they run with
  `--expect-trap-cause 2` and pass only if the model genuinely rejects the write.
- **Self-updating CSRs.** `mcycle`, `minstret`, `time` change between capture and
  check, so a static expected value can never match. They fail *identically on
  both simulators* — diagnosed, not open.
- **WARL fields.** A CSR reads back a legalised value, not the value written. The
  expectation must be the legalised one, which means the test encodes model
  behaviour, deliberately.
- **Harness-hazard CSRs.** Writing `mtvec` replaces the trap vector the preamble
  installed; writing `misa` can disable extensions the harness needs, ending in a
  trap loop. These are excluded by name with the reason recorded, and need a
  restore-after-write scenario to test properly.

**Status and the lesson.** The sweep had only ever been run at XLEN=64. The RV32
high-half CSRs — `mstateen1–3h`, `mcyclecfgh`, `minstretcfgh`, `stimecmph`, the
counter high halves — are `xlen == 32` guarded and were therefore unreachable,
not because they were hard but because the sweep was pointed at one XLEN.
**Running it at RV32 closed 104 of a 194-branch gap against hand-written tests —
53.6% — with no new capability. [V]**

**Plan:** run the full 172-CSR sweep at *both* XLENs, fold the output into the
standard corpus and the regression run. It currently lives in a scratch
directory, which means the gap reopens silently the next time anyone measures.

### 1.2 Physical Memory Protection (PMP)

**How we tackle it.** Two distinct kinds of test, because PMP has two kinds of
behaviour:

1. **CSR access** — `pmpcfg*`/`pmpaddr*` through the CSR sweep above.
2. **Enforcement** — a scenario: configure a locked, restrictive entry, then
   attempt an access that must fault with a specific cause.

**Difficulties:**

- `pmpcfg0` is **struct-typed** and fails symbolic generation; it is far slower
  to solve than a plain CSR and a tight timeout reports that as a failure rather
  than as slowness.
- **The negative control was initially unfailable** — the stock configuration
  passed whether or not PMP was enforcing. Replaced with a *wrong-cause* control,
  which can actually fail.
- PMA attributes are per-region in the config and validated against declared
  extensions, so a scenario config was rejected twice by the model's own
  validator before it was right.

**Status:** violation scenario passes on both simulators with wrong-cause and
no-violation controls both failing as required **[V]**. One real divergence found
(A1: Sail denies non-Machine access by default, Spike permits) and one open
model-side difference (A2: `pmpaddr0` reset value).

### 1.3 Virtual memory and address translation

**This is the largest concentrated gap in the whole project: 102 branches — 30%
of the privileged residual — in `vmem_pte.sail`, `vmem.sail` and `vmem_tlb.sail`.
[V]**

**How we tackle it.** A page-table-walk scenario, not an opcode sweep. Nothing
about translation is observable from an instruction encoding: you must configure
`satp`, build a page table, enter S-mode, and *walk it*. `--run-in-supervisor`
enters the test in S-mode and `--sv39` installs an identity map.

**Why the gap exists, precisely:** the harness works — Sv39 identity-map and
unmapped-page-fault tests pass on both simulators with both negative controls
holding **[V]** — but **it exists only for Sv39, which is RV64-only.** At RV32
every one of those scenarios *skips*. There is no Sv32 version.

**Plan, in order:**

1. **Sv32** — the depth variant that closes the RV32 gap. Highest value per unit
   of work in the entire plan.
2. **Sv48, Sv57** — further depth variants against the same harness.
3. **Page-table content variants** — Svadu (hardware A/D update), Svade
   (trap-on-improper-A/D), Svnapot (NAPOT contiguity), Svpbmt (page-based memory
   types), Svrsw60t59b (reserved-for-software bits), Svvptc.
4. **Svbare** — no-translation mode. Implicitly the state of every test so far
   and never deliberately verified as its own case.

**Difficulties.** The pre-registered risk assessment called virtual memory the
"hardest, path explosion, probe earliest" case. **That prediction was wrong in an
instructive way**: it works, and the real risk was never feasibility but
*depth* — the walker's error paths are not reached by one test per instruction.
Coverage of `vmem_ptw.sail` sat at 5.0% while a working page-table-walk test
existed **[V]**.

### 1.4 Traps, exceptions and interrupts

**How we tackle it.** Synchronous exceptions come from `--expect-trap-cause` on
ordinary instruction tests. Interrupts need delivery and delegation support added
to the generator: `--deliver-interrupt` and `--delegate-interrupt`.

**Difficulties:**

- **Delegation of machine interrupts is impossible by construction** —
  `legalize_mideleg` hardwires MEI/MTI/MSI to 0. The first delegation test was
  written against a machine software interrupt and could never have worked. It
  was moved to a *supervisor* software interrupt (cause 1).
- The trap handler must test the interrupt bit as a **sign bit**, so the same
  handler works at both XLENs rather than hardcoding bit 31 or 63.
- **One negative control here was unfailable and was removed**: an `ebreak`
  control could not fail because the interrupt fires before the `ebreak`
  executes. Removing it was correct; keeping a control that cannot fail is worse
  than having none, because it reports safety that isn't there.

**Status:** delivery and delegation both pass, traced into different privilege
modes **[V]**. Pre-registered as "hard (nondeterminism)" — it turned out better
than predicted.

### 1.5 Pointer masking (Smmpm, Smnpm, Ssnpm, Sspm, Supm)

**No new opcodes.** These change how *existing* load/store/jump instructions
compute addresses under a CSR-configured mask. Coverage therefore means re-running
existing instruction tests under a masking configuration — a config-driven
variant of an existing sweep, not new mnemonics.

**Status:** not started. Listed because it is a named privileged extension whose
work shape is different from everything above.

## Priority 2 — Unprivileged architecture

Every extension with real opcodes has been swept at both XLENs. Roughly **1800
instruction instances pass on both Sail and Spike [V]**. What follows is what is
*hard* about each, since the routine cases need no plan.

### 2.1 Base ISA (I), M, A — solved

I: 45/45 RV32, 57/57 RV64. M: 8/8 RV32, 12/13 RV64. A (with Zalrsc, Zaamo,
Zacas): 44/44 RV32, 88/88 RV64, zero failures. **[V]**

**Worth recording:** A was previously reported as fully covered while *the entire
extension was being silently dropped* by the parser, because it needed the bare
`(reg)` address form and the `.aq`/`.rl` suffix table. The number was confident
and wrong.

**Open item:** RV32 atomic forms account for **24 branches of the privileged
residual** — our scenarios use RV64 `.d` forms.

### 2.2 B (bit-manipulation) and K (crypto) — solved by routing

B: 28/29 RV32, 38/40 RV64. K: 23/25 RV32, 20/25 RV64. **[V]**

**Difficulty:** `cpop`, `cpopw`, `aes64im`, `aes64dsm`, `xperm4`, `xperm8`
**exhaust the SMT solver — OOM, killed, not timed out.** The distinction matters:
a timeout suggests a longer limit would help; an OOM kill says the approach is
wrong for that instruction. They are routed to the oracle's `model-scalar`
backend and pass there.

### 2.3 F, D, Zfh, Zfa — oracle-only by necessity

100/106 RV32 **[V]**.

**The hard boundary:** the symbolic engine **cannot execute FP arithmetic at
all.** The model calls SoftFloat and those functions are absent from the IR. This
was pre-registered as "moderate (fp primops)" and is the **worst prediction miss
in the project** — predicted moderate, actually impossible.

**Two non-obvious prerequisites**, both of which caused every FP test to fail
before they were found: `rv_enable_fdext` in the isla config (off by default),
and `--enable-fp` to set `mstatus.FS`, which **resets to Off and makes every FP
instruction illegal regardless of `misa.F`**.

**Open:** Zfbfmin not started; Zcf/Zcd (compressed FP) not started.

### 2.4 C (compressed) — the genuine wall

23/27 RV32, 29/33 RV64 **[V]**, with `c.jr`/`c.jalr` covered by template.

**This is the one capability wall no part of this plan closes: 47 branches across
`zca`, `zcb`, `zcf`, `zcd` remain unreachable. [V]** Compressed jumps defeat
**both** engines, for unrelated reasons — the symbolic engine drops the
control-flow effects of compressed instructions in a sequence, and the concrete
oracle cannot place a jump target that moves between probe and final ELF.

It was pre-registered as **"easy for both approaches."** It required inventing a
third backend. This is stated plainly rather than buried: **a methodology that
assumes one or two techniques will cover the space is wrong about the space.**

### 2.5 V (vector) — half the instruction set

555/627 RV32, 578/627 RV64 **[V]**. V alone contributes 1133 of the ~1800 passing
instances.

**Symbolic cannot touch it** — every path reaching `read_vreg`/`write_vreg` fails
outright. Vector is oracle-only, permanently.

**Emitting a *legal* vector instruction needs eight things right simultaneously**
— `mstatus.VS` enabled, a `vsetvli` prelude, SEW=32, 8-aligned vector register
groups among them. All eight initial failures were ours, not the model's. And
**VLEN=128 is the only workable value**, with both bounds hard: below it the
configuration is rejected, above it the symbolic type cannot represent it.

**Scope note:** the RFP defers vector to a future iteration. It accounts for 22
branches of the privileged residual and is not proposed as in-scope work now.

### 2.6 Vector crypto and bfloat16

Vector crypto: 35/41 RV32, 34/41 RV64 **[V]**. Zvfbfmin/Zvfbfwma: 2/6 — the four
`*bf16*` instructions fail for a cause **not yet investigated**, recorded as open
rather than explained away.

### 2.7 Zicfilp / Zicfiss (control-flow integrity)

4/8 RV32. The `ssamoswap.*` family fails — 12 tests.

**A correction worth preserving:** those 12 failures were originally attributed
to a real model defect (A4, an assertion tripped by the model's own valid
shadow-stack PTE, since fixed and PR'd on our fork). **That attribution was
wrong.** The spec forbids Zicfiss in M-mode, and the tests run in M-mode. They
are *our* framework's failures, not the model's.

**The rule that came from it:** a finding earns only the failures its reproducer
actually covers.

### 2.8 The small Z* extensions — done

Zifencei, Zicond, Zimop, Zcmop, Zihintntl, Zihintpause, Zicbom/Zicbop/Zicboz,
Zawrs — all swept, both XLENs **[V]**.

### 2.9 Config-only extensions — a different kind of work

Roughly 20 entries have **no opcodes at all**: RV32E/RV64E (register-count
variant), the Zic64b/Ziccamoa/Ziccrse family (PMA guarantees), Za64rs/Za128rs
(reservation-set guarantees), Zvl32b–Zvl1024b (VLEN variants), Zfinx/Zdinx
(FP-in-integer-registers). Zkt and Zvkt are noted by the model's own README as
having **no impact on the model** — nothing to test by design.

These need **config-driven** tests: the same instructions under a different
machine configuration. This is the same lever that closed 53.6% of the
hand-written gap, applied deliberately instead of accidentally.

---

# Part 4 — The difficulties, stated plainly

## 4.1 Path enumeration does not do what it looks like it should

The obvious idea — generate one test per feasible path instead of one per
instruction — **was tested and rejected: 19% more tests, 5 more branches, 2.6% of
the gap. [V]**

Why: no load or store produced more than one path, because `--memory-region`
bounds the address to a mapped region and exception paths are off by default, so
fault paths are infeasible for the solver to find. Path enumeration digs deeper
into instruction semantics, and instruction semantics is ~5% of the gap.

**It remains available as a flag. It is not the organising principle.**

## 4.2 Symbolic cost is unbounded, and fails in two different ways

Some instructions time out; some are **SIGKILLed on memory**. Treating those the
same leads to the wrong fix. OOM means route to another backend; timeout means
raise the limit. Both were misdiagnosed once as "the machine was busy", which was
recorded as fact and was wrong.

## 4.3 A test that cannot fail reports success forever

The defining failure of this project: **47.7% of the corpus passing while
comparing empty expected-state tables.** The suite was green. That is why it took
so long to find.

Every mitigation in this plan traces back to it — negative controls on scenarios,
mutation sensitivity as a requirement, controls that must fail being treated as
regressions when they stop failing. **Mechanical corpus-wide enforcement is still
not implemented.** It is the top work item in Part 5.

## 4.4 Coverage measurement has a blind spot in the same direction as our bias

**A failing test writes no coverage file at all. [V]** So coverage is measured
only from passing tests. Every figure understates — which is the safe direction,
but it must be stated every time, and it means "improve coverage" and "make tests
pass" are not independent goals.

## 4.5 The oracle is circular against the model it is testing

Oracle expectations come from Sail. Sail agreeing with them proves nothing.
**Only Spike is an independent check [V]**, and Spike does not implement
everything the model does — an extension Spike wasn't told about is
indistinguishable, from outside, from the model getting it wrong.

## 4.6 Configuration hides coverage more effectively than technique finds it

The single largest measured coverage win in this project came from running an
**existing** tool at an **XLEN it had never been pointed at** — 104 branches,
53.6% of a measured gap, zero new capability **[V]**. Meanwhile **only 2 of the
model's own 16 CI configurations have ever been exercised [V]**.

The honest conclusion: **we should expect more coverage to be hiding in
configuration than in cleverness**, and the plan is ordered accordingly.

---

# Part 5 — Order of work, and why this order

| # | Work | Why here | Size |
|---|---|---|---|
| **1** | **Mechanical vacuity + mutation enforcement across the corpus** | Everything else's numbers are worthless without it. This is the failure that already happened once at 47.7% | medium |
| **2** | **Fold the RV32 CSR sweep into the standard corpus and regression** | 104 branches already won, currently sitting in a scratch directory and will silently reopen | small |
| **3** | **Sv32 page-table-walk scenarios** | 102 branches — the largest single concentrated gap. The harness exists; only the RV32 depth variant is missing | medium |
| **4** | **RV32 atomic forms in the PMA/scenario tests** | 24 branches, same shape as #3 | small |
| **5** | **Run the config matrix — the model's own 16 CI configurations** | §4.6: this is where coverage hides. Currently 2 of 16 | medium |
| **6** | **Triage the six CSR simulator discrepancies** | 3 fail on both simulators, 3 pass on Sail and fail on Spike. Untriaged; these are exactly what the framework exists to find | small |
| **7** | **Fix the 3 RV32 privileged scenario regressions** | Real failures in our own corpus, not the model's | small |
| **8** | **Page-table content variants** (Svadu, Svade, Svnapot, Svpbmt, Svvptc) | Depth on the working harness once #3 lands | large |
| **9** | **Pointer masking under config** | Named privileged extension, config-driven | medium |
| **10** | **Zcf/Zcd, Zfbfmin, bfloat16 investigation** | Unprivileged completeness | medium |

**Deliberately not in this list:** compressed control flow (a real wall, §2.4),
vector expansion (deferred by the RFP), and hypervisor (out of scope this
iteration — note that an orphaned upstream draft implementation exists, PR #612
on sail-riscv, and should be checked before anyone proposes a from-scratch
design).

---

# Part 6 — What we will not claim

- **Not** that generated tests replace hand-written ones. We reach **77.2%** of
  what 145 hand-written privileged tests reach, plus **53 branches they do not**
  **[V]**. The value is complementary coverage at a fraction of the authoring
  cost, which is what the RFP asks for.
- **Not** that the framework covers compressed control flow. 47 branches, both
  engines, no path forward in this plan.
- **Not** that vector or hypervisor are in scope this iteration.
- **Not** any figure without its scope and its measurement. Where evidence does
  not exist this project writes **NO QUANTITATIVE EVIDENCE AVAILABLE** — currently
  true for QEMU and CVA6 execution (code paths exist, never demonstrated) and for
  RV32 on three of the oracle backends.
