# Methodology Selection
## Automated RISC-V test generation from the Sail Golden Model

**Audience**: a reviewer deciding whether this approach is the right one to
fund. The argument is made from measurements taken on this repository, not from
what is conventional in the literature.

**Evidence discipline.** Claims are tagged **[V]** verified by running it,
**[I]** inference from evidence, **[U]** unknown. Where a number does not exist,
this document says **NO QUANTITATIVE EVIDENCE AVAILABLE** rather than
estimating one.

**A note on what this document is not.** It is not a defence of what the
repository currently does. The methodology in the repository accreted from
exploration; it was never derived. Section 6 selects a methodology that
**differs from the current implementation in its organising principle**, on the
basis of §4's evidence.

---

# 1. The problem, defined from this project

## 1.1 Input

- The **unmodified** Sail RISC-V Golden Model (`riscv/sail-riscv`). Not a fork:
  a methodology that requires patching the model cannot be maintained by the
  model's own community, and the RFP asks for the current upstream model. **[V]**
  the model is consumed as a submodule pinned to an upstream-tracking branch.
- A **Golden Model configuration** — the same JSON the emulator itself takes,
  so generation and execution cannot disagree about the machine. **[V]**

## 1.2 Output

Valid RISC-V ELF files, organised by ISA extension, that are self-checking and
terminate via HTIF.

## 1.3 What makes a generated test *valid*

It assembles, links, loads, and **runs unmodified on a simulator we did not
write**. That last clause is the whole test — anything else is checking our own
harness against itself. **[V]** 1894 of 1894 generated ELFs are valid RISC-V
executables; 812 pass on Spike.

## 1.4 What makes a generated test *useful*

Validity is cheap. Usefulness requires that the test **fails when the
implementation is wrong**, which needs two properties:

1. a **non-empty expected-state table** — otherwise the test asserts nothing;
2. demonstrated **sensitivity** — mutate an expected value and the test must fail.

This distinction is not academic here. **47.7% of this corpus once passed while
comparing empty expected-state tables.** **[V]** Every test was green. The defect
was invisible precisely because the suite was passing. Any methodology that
cannot rule this out by construction is disqualified.

## 1.5 What "coverage" means

**Branch coverage of the Sail model's own source**, measured by building the
model with `-DCOVERAGE=ON` and comparing executed spans against the model's
`sail_riscv_model.branch_info` manifest. The model exposes **15,157** spans.
**[V]**

This is the RFP's own metric ("in terms of the Sail code of the Golden Model"),
and it is the definition this document optimises against. Two consequences that
matter later:

- **Instruction count is not coverage.** A suite can execute every instruction
  once and still miss most of the model's behaviour, because most of the model
  is error paths, width variants and privilege checks.
- **Coverage figures are only meaningful with their scope.** Three scope files
  exist, each justifying its exclusions in-file. A number quoted without its
  scope is not a claim, it is a decoration.

**One honest limitation of the metric itself**: a test that exits nonzero writes
no coverage file at all — the Sail runtime flushes on clean exit only. **[V]**
So every coverage figure in this project is *coverage from passing tests*. It
understates rather than overstates, but it must be stated.

## 1.6 Architectural behaviours targeted

The RFP's priority order, which is also this document's: **the privileged
architecture first**. M-mode PMP/PMA, trap and interrupt handling are named as
**required**; S-mode virtual memory and address translation as *highly
desirable*; hypervisor explicitly out of scope for this iteration; floating
point and vector deferred to a future iteration but the framework must be
*extendable* to them.

## 1.7 Constraints

| Constraint | Source | Consequence for methodology |
|---|---|---|
| Apache-2.0 | RFP, IP section | rules out GPL-only tooling |
| Must not fork the model | maintainability; RFP asks for current upstream | rules out approaches needing model rewrites |
| Must extend to FP, vector, hypervisor | RFP Consideration D | an approach that *structurally* cannot reach these is disqualified as a sole method |
| Must support the model's own CI matrix | RFP Goal 4 | 16 configs: `rv{32,64}d` × VLEN {64,128,256,512} × ELEN {32,64} **[V]** |
| Community maintainability | RFP criterion 2 | Rust + SMT expertise is a real cost against a Sail/C/Python community |
| Tests should be ACT4-compatible | RFP Consideration A | favours reusing `riscv-arch-test` macros |

## 1.8 Success criteria for the project

1. Privileged-architecture coverage of the Sail model — **criterion 1**.
2. A framework the Golden Model community can maintain — **criterion 2**.
3. Integration with existing open-source communities — **criterion 3**.
4. Demonstrated technical skill in those communities — **criterion 4**.
5. Cost and delivery date — **criteria 5 and 6**.

---

# 2. Candidate methodologies

Only methodologies that could actually be run against this input are listed. A
methodology that cannot consume the Sail model and a Golden Model config is not
a candidate for this RFP, however well it works elsewhere.

**Excluded, with reasons:**

- **Pure grammar-based generation.** The model already yields the instruction
  grammar — `parse_instructions` extracts 1280 mnemonics with zero unparsed
  clauses **[V]**. That makes grammar extraction a *stage* of the pipeline, not
  a methodology in its own right.
- **Pure mutation-based generation.** Needs a seed corpus whose tests already
  have non-empty expected-state tables. We did not have one at the start, and
  building one is the problem itself.
- **Formal equivalence checking.** Answers "are two models equivalent", not
  "produce tests that run on hardware". The RFP asks for ELFs.

**Candidates:**

**A. Concrete oracle (model-as-oracle).** Render an instruction with concrete
operands, run it on the model, record the resulting architectural state as the
expectation. The CHERIoT lineage the RFP cites.

**B. Symbolic path enumeration.** Execute the model symbolically over an
instruction, let an SMT solver find feasible paths, emit one test per path with
solved-for initial state. The isla-testgen lineage the RFP cites.

**C. Constrained-random.** Generate random instruction streams under
architectural constraints, compare against a reference. The riscv-dv lineage.

**D. Coverage-directed hybrid.** Take the unit of work to be *an uncovered Sail
branch*. Choose, per target, whichever engine can reach it, and construct the
machine state it requires. Uses A and B as backends rather than competing with
them.

**E. Hand-written testplans.** Included because it is the **baseline being
measured against**, not because it is a candidate for automation.

---

# 3. Comparison

Every cell is either a measurement from this repository or an inference labelled
as such. No scores are given without a stated basis.

| Criterion | A. Concrete oracle | B. Symbolic | C. Constrained-random | D. Coverage-directed hybrid | E. Hand-written |
|---|---|---|---|---|---|
| Reaches FP/vector | **yes** — model executes them **[V]** | **no** — SoftFloat absent from IR; symbolic vector length fails **[V]** | yes **[I]** | yes, via A **[V]** | yes **[V]** |
| Reaches solver-constructed state | no — needs hand-written preambles **[V]** | **yes** — PMP and Sv39 states solved for **[V]** | no **[I]** | yes, via B **[V]** | yes, by hand **[V]** |
| Independent of the model | **no** — expectations come from Sail; only Spike is an independent check **[V]** | partly — state is solved, outcome still model-derived **[I]** | depends on reference **[I]** | inherits both **[I]** | **yes** **[V]** |
| Expected-state table guaranteed non-empty | not by construction — 47.7% of the corpus once compared empty tables **[V]** | not by construction **[V]** | no **[I]** | **must be enforced explicitly** **[V]** | yes, by author intent **[V]** |
| Control flow / compressed jumps | no — targets move between probe and final ELF **[V]** | no — control-flow effects unmodelled **[V]** | yes **[I]** | **no** — 47 branches still unreachable **[V]** | yes **[V]** |
| Cost per branch reached | low **[I]** | high — some instructions SIGKILL on memory **[V]** | low, undirected **[I]** | low where a backend exists **[V]** | **highest** — human authorship |
| Maintainable by the Sail community | yes — Python/C **[I]** | costly — Rust + SMT expertise **[I]** | yes **[I]** | mixed **[I]** | yes **[V]** |
| Config-matrix support (16 configs) | yes **[I]** | **no** — VLEN > 129 unrepresentable **[V]** | yes **[I]** | partial **[V]** | yes **[V]** |
| Licence | Apache-2.0 ✅ | Apache-2.0 ✅ | ✅ | ✅ | ✅ |

The decisive rows are **row 1** and **row 2**: neither A nor B covers the space
alone, and their gaps are complementary rather than overlapping. That is the
argument for a hybrid, and it is an argument from measurement, not preference.

---

# 4. Evidence from our own experiments

*(Presented before the selection, because the selection depends on it.)*

## 4.1 A pre-registered prediction, and what actually happened

`comparison/coverage-risk-matrix.md` was written **before** the implementation
work and recorded, per instruction class, how hard each approach was expected to
be. That makes it unusually good evidence: it can be wrong, and it was.

| Class | Predicted | What happened | Verdict |
|---|---|---|---|
| Base ALU | trivial / proven | works | ✅ as predicted |
| CSR access | proven after de-scatter | works, 172 CSRs model-derived | ✅ as predicted |
| PMP | symbolic **"hard"**, highest-risk | **works** — violation scenarios pass with controls | ⬆ better than predicted |
| VM / PTW | symbolic **"hardest"**, path explosion, "probe earliest" | **works** — Sv39 walks pass. But only **5.0%** of `vmem_ptw.sail` is covered | ↔ right about risk, wrong about *which* risk: depth, not feasibility |
| Vector | symbolic "hard", high risk | **hard wall** — every path reaching `read_vreg`/`write_vreg` dies with "Symbolic (bit)vector length in zeros" | ✅ predicted, and worse |
| Float / Double | symbolic **"moderate (fp primops)"** | **impossible** — the model calls SoftFloat, which is absent from the IR entirely | ❌ **badly underestimated** |
| Branch / jump | **"easy"** for both approaches | **blocked in both** — compressed jumps defeat the symbolic engine (control-flow effects unmodelled) *and* the concrete one (jump targets move between probe and final ELF) | ❌ **wrong**, and it required inventing a third backend |
| Atomics | moderate both | works | ✅ as predicted |
| Interrupts | "hard (nondeterminism)" | works — delivery and delegation, traced to different privilege modes | ⬆ better than predicted |

**[V]** every row.

## 4.2 What the wrong predictions teach

Three lessons, and they drive §6.

**1. Capability walls are discovered by running, not by reasoning.** F/D was
predicted "moderate" and is impossible; PMP was predicted "hard" and works. The
prediction errors run in *both* directions, which means neither optimism nor
pessimism was systematic — the information simply was not available without
executing.

**2. Some work defeats every generator.** Nobody predicted needing a third
backend. Compressed jumps are blocked in both engines for *unrelated* reasons.
A methodology that assumes one or two techniques will cover the space is wrong
about the space.

**3. Feasibility and depth are different risks, and depth is the bigger one.**
The matrix treated VM/PTW as the thing that might not *work*. It works. What it
does not do is go deep: 5.0% of the walker. The real risk was never "can we
generate a page-table-walk test" but "will one test per instruction reach the
walker's error paths" — and it does not.

## 4.3 The measured capability boundaries

| Boundary | Evidence |
|---|---|
| Symbolic cannot execute FP arithmetic | model calls SoftFloat (`riscv_f32Add`, `riscv_f64Lt_quiet`); absent from IR **[V]** |
| Symbolic cannot execute vector element paths | "Symbolic (bit)vector length in zeros" **[V]** |
| Symbolic cannot represent VLEN > 129 | `B129` bitvector type — 256 and 512 unrepresentable **[V]** |
| Symbolic exhausts memory on some instructions | `cpop`, `aes64im`, `xperm*` SIGKILLed, not timed out **[V]** |
| Concrete cannot express control flow | jump target addresses differ between probe and final ELF **[V]** |
| Concrete is circular on Sail | expected values *come from* the model; only Spike is independent **[V]** |
| Concrete cannot construct state by solving | privileged scenarios need hand-written preambles **[V]** |

## 4.4 What the experiments produced

| Result | Value |
|---|---|
| Instructions parsed from the model | 1280, 23 extensions, **zero unparsed clauses** **[V]** |
| Model-side defects found, with executed reproducers | **4** (A1–A4) **[V]** |
| Privileged branch coverage | **70.7%** (494/699) **[V]** |
| Current-scope branch coverage | **72.1%** (1098/1523) **[V]** |
| Reproducibility | identical seed ⇒ byte-identical `.S` **[V]** |
| Config matrix exercised | **2 of 16** **[V]** |

**NO QUANTITATIVE EVIDENCE AVAILABLE** for: QEMU and CVA6 execution (code paths
exist, never demonstrated); RV32 on the `model-fp`/`model-v`/`model-vk`
backends; corpus cost of full path enumeration across the instruction set.

## 4.5 The finding that reframes the problem

Measured like-for-like — same extensions (I+M), same XLEN (RV32), same
simulator, same coverage build:

| | Sail branches | tests | branches/test |
|---|---:|---:|---:|
| **ACT4, hand-written from testplans** | **1181** | 47 | 1155 |
| **Ours, one test per instruction** | 1055 | 53 | 912 |

**ACT4 reaches 194 branches we do not; we reach 68 they do not.** **[V]**

More tests, less coverage. And the files ACT4 reaches alone are diagnostic —
`csr_end.sail` (76 spans), `Stateen`, `vext_vset`, `interrupt_regs` — all
**corner-case files**: WARL fields, illegal encodings, privilege gating,
delegation combinations. Precisely what a testplan enumerates deliberately and
what one-test-per-instruction hits only by luck.

This is not a tooling deficiency. It is the arithmetic consequence of optimising
instruction count while the metric counts branches.

## 4.6 A hypothesis this document proposed, tested, and abandoned

The first explanation offered for §4.5 was **depth**: we write one test per
instruction, which follows one route through it, so we miss the corner cases a
testplan enumerates. The proposed fix was `--all-paths-for`, isla-testgen's
existing flag that emits one test per feasible architectural path.

It was tested rather than assumed. Same 53 instructions of I+M at RV32, same
simulator, same coverage build, one variable changed:

| | ELFs | passed | unique Sail branches |
|---|---:|---:|---:|
| one test per instruction | 53 | 49 | **958** |
| `--all-paths-for` | 63 | 59 | **963** |

**19% more tests; 5 more branches — 2.6% of the 194-branch deficit.** **[V]**

Worse for the hypothesis, the 5 are all in `extensions/C/zca_insts.sail`, the
compressed-instruction decoder — an artefact of the extra tests having different
code layout, not an architectural corner case. And **no load or store produced
more than one path**: `--memory-region` bounds the address to a mapped region
and exception paths are off by default, so the fault paths are infeasible for
the solver to find. The single observation that motivated the proposal — one
`lw` yielding 14 tests and +76 spans — did not reproduce.

**The hypothesis is rejected.** It is recorded here, rather than deleted,
because the rejection is what produced §4.7.

*(One methodological note, since this document's numbers are only worth what its
discipline is worth: the first run of this experiment reported **zero**, on a
corpus that had silently excluded every memory and M instruction — `LOAD`/`STORE`
were unparsed because their mnemonic table lives in `core/types.sail`, and
`-march` omitted `m`. The number above comes from the corrected run. A confident
zero on the wrong corpus is the same failure mode as a passing test with an
empty expectation table.)*

## 4.7 Where the deficit actually comes from

If depth is not the explanation, what is? The hand-written tests share a common
preamble through the `RVTEST_*` macros — every test boots the same way, installs
the same trap handler, initialises the same CSRs, then runs the instruction it
is about. So *how many different tests reach a given branch* discriminates
between the two explanations: a branch hit by nearly every test cannot be
attributable to the instruction under test, because those differ.

Of the 194 branches ACT4 reaches and we did not:

| | branches | share |
|---|---:|---:|
| reached by ≥80% of ACT4's tests — **shared setup** | **184** | 94.8% |
| reached by 20–80% | 1 | 0.5% |
| reached by <20% — **specific to one test's instruction** | 9 | 4.6% |

**[V]** The deficit is 95% machine-state setup and 5% instruction behaviour.
Path enumeration was digging deeper into the 5%.

Decoding the largest block confirms it: the 76 branches in
`postlude/csr_end.sail` are CSR-access match arms for specific addresses —
`mstateen1–3h`, `hstateen*`, the RV32 counter high halves, `mcyclecfgh`,
`minstretcfgh`, `stimecmph`, indexed `pmpcfg`. Not instruction behaviour at all.

## 4.8 What closed it — and what that says about the framework

Those CSRs are exactly what `csr_sweep.py` already targets by name, and the
file already carried a note that `csr_end.sail` sat at 69% because the sweep had
only ever been run at XLEN=64 — these arms are `xlen == 32` guarded.

Running the **existing** sweep at RV32 and folding its 215 ELFs into the corpus:

| | deficit closed | of 194 |
|---|---:|---:|
| path enumeration (new capability, rejected) | 5 | 2.6% |
| existing CSR sweep, run at RV32 | **104** | **53.6%** |

**[V]**, and identical under two different machine configurations, so config
choice is not deciding the result. `csr_end.sail` went from 76 uncovered to 3;
`Stateen` and the Zihpm counters went to zero.

**No new capability was involved.** The lever was corpus scope and
configuration, not technique. That is the central empirical result of this
document.

The residual 90 is dominated by vector code (~40, deferred by the RFP), platform
and interrupt registers (~20), and 8 genuine instruction-level misses — the only
part the rejected hypothesis was ever aimed at, and about the size §4.7 predicts.

## 4.9 The comparison that matters: privileged architecture

§4.5–§4.8 concern base integer and multiply. The RFP's first criterion is
privileged coverage. The hand-written suite has 450 privileged tests; 145 build
and run for RV32 on the Sail target.

| | tests | branches |
|---|---:|---:|
| hand-written privileged | 145 | **1493** |
| everything we generate at RV32 | 286 | 1206 |

**We reach 1153 of their 1493 — 77.2% — and 53 branches they do not reach.**
**[V]**

Two qualifications, both against us. Their 145 are privileged tests only, while
our 286 is every RV32 corpus we have — necessary, because their privileged tests
boot through startup code whose coverage lives in our instruction and CSR
sweeps; our 18 privileged scenarios alone reach 1044. And 8 of those 18 do not
pass (5 are negative controls that must fail; **3 are real RV32 regressions**),
and a failing test writes no coverage, so 77.2% is a floor.

**The 340 still out of reach are concentrated, not scattered:**

| Residual | branches | status |
|---|---:|---|
| virtual memory (`vmem_pte`, `vmem`, `vmem_tlb`) | **102** | technique exists — our page-walk scenarios are Sv39/RV64 only and skip at RV32 |
| compressed (`zca`, `zcb`, `zcf`, `zcd`) | 47 | **genuine capability wall** — defeats both engines |
| atomics (`zaamo`) | 24 | our PMA scenarios use RV64 `.d` forms |
| vector | 22 | out of scope this iteration per the RFP |
| `mem`, `platform`, `cfi`, `zicbom`, other | ~145 | mixed |

Roughly 150 of the 340 are "the method exists, it has not been pointed at RV32".
About 47 are a real wall. About 22 are out of scope by the RFP's own terms.

---

# 5. Prototype versus final

Per component, on the evidence above.

| Component | Verdict | Why |
|---|---|---|
| Model parser (`model_opcodes.py`) | **retain** | 1280 instructions, zero unparsed clauses **[V]**. But it must keep *reporting* skips — §4.6's zero came from ignoring two |
| Concrete oracle backend | **retain** | the only thing that reaches FP and vector **[V]** |
| Symbolic backend (isla-testgen) | **retain, narrow** | the only thing that solves for machine state **[V]**; not a general-purpose engine here |
| `--all-paths-for` | **do not adopt as the organising principle** | 5 of 194 **[V]**. Keep as an available flag; it costs nothing to leave in |
| Instruction sweep drivers | **modify** | must take an XLEN *matrix*, not one XLEN. §4.8 is entirely a consequence of this gap |
| CSR sweep | **promote** | closed 53.6% of the deficit unaided **[V]**. Belongs in the standard corpus and the regression run, not in a scratch directory |
| Scenario/preamble harness | **extend** | the mechanism that reaches privileged state; needs Sv32 and RV32 atomic forms — 126 of the 340 residual **[V]** |
| Coverage measurement | **retain** | already emits the uncovered-branch work queue; nothing consumes it yet |
| Regression reporter | **retain** | caught a corpus deletion mid-sweep **[V]** |
| Template backend | **cap explicitly** | "what neither engine can reach", currently 4 tests. Not a growth area |

Nothing is abandoned. The change is which component the control loop is built
around.

---

# 6. Selected methodology

**Coverage-directed hybrid, with machine-state construction — not path depth —
as the primary lever.**

Precisely:

1. The **unit of work is an uncovered Sail branch**, taken from the model's own
   `branch_info` manifest (15,157 spans). Not an instruction, not a test count.
2. Reaching a branch is treated as **two separable problems**: *what machine
   state must hold* (privilege, CSR values, page tables, PMP entries), and *what
   instruction executes under it*. §4.7 measured the first as ~95% of the gap.
3. State is constructed by whichever mechanism is cheapest and provable: a
   **preamble** where one suffices, the **symbolic solver** where the state must
   be derived rather than written.
4. The instruction is rendered by the **concrete oracle** by default, and by the
   **symbolic backend** where solved-for initial state is required.
5. **Corpus scope is a first-class parameter**, not an implementation detail.
   The XLEN matrix, the extension set, and which drivers contribute to the
   measured corpus are configuration, and §4.8 shows they dominate technique.
6. Every generated test must carry a **non-empty expected-state table** and be
   **mutation-sensitive**, enforced mechanically. This is not a quality goal; it
   is what separates a test from a decoration, and 47.7% of this corpus once
   failed it silently.

**Why this and not the alternatives.** A alone cannot construct privileged state
and cannot express control flow. B alone cannot execute FP or vector, cannot
represent VLEN > 129, and exhausts memory on some instructions. C is undirected
against a metric that counts specific branches. E is the baseline and does not
automate. D is the only option whose gaps are the *intersection* of its
backends' gaps rather than the union.

**Assumptions.** That branch coverage of the Sail sources is a reasonable proxy
for verification quality — the RFP's own choice of metric, and adopted here
rather than argued for. That the model remains unforked.

**Risks and limitations, stated plainly.**

- **Compressed instructions — 47 branches — defeat both engines** and no
  mechanism in this methodology addresses them **[V]**. This is a known,
  bounded hole, not an oversight.
- **Coverage is measured from passing tests only.** A test that exits nonzero
  writes no coverage file **[V]**. Every figure here understates.
- **The concrete oracle is circular against Sail**; only Spike provides
  independence **[V]**.
- **The config matrix is barely exercised — 2 of 16 [V]**, and §4.8 is direct
  evidence that this is where coverage silently hides.
- **Symbolic cost is unbounded on some instructions** — `cpop`, `aes64im`,
  `xperm*` were SIGKILLed on memory, not timed out **[V]**.

**Fallback.** If state construction proves infeasible for a target class, that
class ships with preamble-built state only and the limitation is recorded per
class — the approach already used for PMP and Sv39, which pass with negative
controls.

---

# 7. Research hypothesis

Derived from §4.7 and §4.8, replacing the one §4.6 rejected:

> **The branches an automated generator misses relative to hand-written
> testplans are predominantly reached by machine-state setup rather than by
> instruction behaviour; therefore directing generation by uncovered branch,
> constructing the required state, and treating corpus scope as a parameter
> closes materially more of the gap than deepening per-instruction path
> exploration.**

Status: **supported**. Path depth closed 2.6% of the measured gap; corpus scope
and state setup closed 53.6% with no new capability **[V]**. The hypothesis
remains falsifiable — the residual in §4.9 is where it would fail next, and
102 of those 340 branches are the immediate test of it.

---

# 8. Baselines

Three, all measured on this repository, all reproducible from saved span data.

| Baseline | Value | Role |
|---|---|---|
| **Hand-written, I+M RV32** | 1181 branches / 47 tests **[V]** | the quality bar |
| **Hand-written, privileged RV32** | 1493 branches / 145 tests **[V]** | the bar on the RFP's first criterion |
| **Ours, one test per instruction** | 1055 branches / 53 tests **[V]** | the starting point |
| **Ours, everything at RV32** | 1206 branches / 286 tests **[V]** | the current position |
| **Path enumeration** | +5 branches **[V]** | the rejected control |

The rejected control matters as much as the rest: it is the measurement that
separates "better directed" from "simply more tests".

---

# 9. Success criteria for the selection

A methodology is selected only if it can show **measurable improvement in unique
Sail branches reached**, not more tests.

1. **Beat the volume control.** More tests trivially reach more branches; a
   matched-test-count comparison must separate direction from volume. ✅ met —
   `--all-paths-for` added 19% more tests for 0.5% more coverage and was
   rejected on exactly this basis.
2. **Close a material share of the hand-written gap.** Bar set in advance at
   half. ✅ met — 53.6%.
3. **Every test must be able to fail.** Non-empty expectations plus demonstrated
   mutation sensitivity. ⚠️ enforced in the scenario harness via negative
   controls; **not yet enforced mechanically corpus-wide**. This is the largest
   open risk in the methodology and is tracked as such.
4. **No fork of the Golden Model.** ✅ met — consumed as a pinned submodule; the
   one model change made (A4) is a defect fix PR'd on a fork, not a requirement.
5. **Extendable to FP, vector, hypervisor.** ✅ structurally — the concrete
   oracle executes FP and vector today; not yet demonstrated at scale.
6. **Reproducible.** ✅ met — identical seed yields byte-identical `.S` **[V]**.

Criterion 3 is the one that is not met, and it is stated here rather than in a
footnote because it is the criterion whose failure is invisible: a suite that
cannot fail still reports green.
