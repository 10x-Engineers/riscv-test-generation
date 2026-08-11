# Automated RISC-V Test Generation from the Sail Golden Model
## Technical Architecture and 0→100 Execution Roadmap

**Status of this document.** It is the forward execution plan for building and
validating the complete system. Where exploratory work already satisfies a
planned activity, that work is cited as **evidence** so the task is de-risked
rather than re-derived — but the plan is written as the sequence of activities
required to reach the finished system, not as a progress report.

**Evidence discipline.** Every claim below is tagged:

- **[V]** verified — inspected in the repository, or reproduced by running it
- **[I]** inference — strongly implied by evidence, not directly observed
- **[U]** **UNKNOWN — REQUIRES INVESTIGATION**

Nothing is asserted from the name of a file or a component.

---

# Deliverable A — Technical Architecture

## A.1 The pipeline as it must exist

```
  RISC-V spec                    Sail Golden Model (riscv/sail-riscv)
       │                                    │
       │                    ┌───────────────┴───────────────┐
       │                    ▼                               ▼
       │            (a) isla-sail → IR              (b) model sources
       │                    │                               │
       │                    ▼                               ▼
       │         ┌──────────────────────┐        ┌────────────────────────┐
       │         │ SYMBOLIC ENGINE      │        │ MODEL ANALYSIS         │
       │         │ isla-testgen + Z3    │        │ assembly-mapping parse │
       │         │ solves for a path    │        │ → instruction set      │
       │         └──────────┬───────────┘        └───────────┬────────────┘
       │                    │                                │
       │                    │        ┌───────────────────────┘
       │                    │        ▼
       │                    │   ┌────────────────────┐   ┌──────────────┐
       │                    │   │ CONCRETE ENGINE    │   │ TEMPLATE     │
       │                    │   │ probe→model→expect │   │ hand-written │
       │                    │   └─────────┬──────────┘   └──────┬───────┘
       │                    │             │                     │
       │                    └─────────────┴──────────┬──────────┘
       │                                             ▼
       │                              ┌───────────────────────────┐
       │                              │ TEST REPRESENTATION       │
       │                              │ .S + expected-state tables│
       │                              └─────────────┬─────────────┘
       │                                            ▼
       │                              ┌───────────────────────────┐
       │                              │ COMPILATION               │
       │                              │ real toolchain + Model    │
       │                              │ crt0/link.ld/runtime      │
       │                              └─────────────┬─────────────┘
       │                                            ▼
       │                              ┌───────────────────────────┐
       │                              │ EXECUTION                 │
       │                              │ sail_riscv_sim │ Spike    │
       │                              │ QEMU │ CVA6 RTL           │
       │                              └─────────────┬─────────────┘
       │                                            ▼
       ▼                              ┌───────────────────────────┐
  DIFFERENTIAL ◄────────────────────  │ VALIDATION                │
  (Spike is the                       │ self-check + differential │
   independent axis)                  │ + vacuity + mutation      │
                                      └─────────────┬─────────────┘
                                                    ▼
                                      ┌───────────────────────────┐
                                      │ COVERAGE (Sail branches)  │
                                      │ per-ELF and per-suite     │
                                      └─────────────┬─────────────┘
                                                    ▼
                                      ┌───────────────────────────┐
                                      │ FEEDBACK / REFINEMENT     │
                                      │ uncovered → generation    │
                                      └─────────────┬─────────────┘
                                                    ▼
                                       REGRESSION + CI + EVALUATION
```

## A.2 Stage contracts

Each stage below states purpose, inputs, outputs, owning component, interface,
failure modes, and what "done" means. This is the interface specification the
implementation tasks in Deliverable B are written against.

### Stage 1 — Model enablement (Sail → IR)

- **Purpose**: produce an isla IR the symbolic engine can execute, from the
  *unmodified* upstream model.
- **Inputs**: `riscv/sail-riscv` sources; a Golden Model config JSON.
- **Output**: `riscv-ir/riscv{32,64}.ir` + `.toml`.
- **Component**: `isla-sail` (fork), `isla-lib` (fork).
- **Interface**: IR file consumed by `isla-testgen -A`.
- **Failure modes**: Sail language drift breaks `isla-sail`; missing primops;
  hardcoded widths. **[V]** all three occurred — closed milestones M1 (#1 Sail
  drift, #2 missing primops, #3 hardcoded 64-bit width in
  `check_concrete_overlap`) in `10x-Engineers/riscv-testgen-sail`.
- **Done when**: IR builds from an unmodified upstream checkout for both XLENs
  and the chosen config, with no local model patches required.

### Stage 2 — Model analysis (instruction extraction)

- **Purpose**: derive *what to generate* from the model rather than a hand list.
- **Inputs**: model `.sail` sources (`assembly` / `encdec` mapping clauses).
- **Output**: an instruction set with mnemonics, operand classes, widths.
- **Component**: `python-isla/model_opcodes.py`, `autotest/src/sailtest/model.py`. **[V]**
- **Interface**: in-memory instruction objects → generators.
- **Failure modes**: a clause whose mnemonic table lives in another file is
  silently dropped. **[V]** this cost base-`I` every load/store and the whole
  `A` extension; unparsed clauses are now reported per sweep.
- **Done when**: unparsed-clause count is zero and the count is asserted, not
  eyeballed. **[V]** currently 1280 instructions, 23 extensions, zero unparsed.

### Stage 3 — Generation engines

Three engines with a **routing rule decided by capability, not preference**.

| Engine | Mechanism | Handles | Cannot do |
|---|---|---|---|
| Symbolic (`isla-testgen`) | Z3 solves an initial state driving a chosen path | constructed privilege/memory state; branchy control flow | FP arithmetic (SoftFloat absent from IR) **[V]**; vector element paths ("Symbolic (bit)vector length in zeros") **[V]**; VLEN > 129 (`B129` bitvector) **[V]** |
| Concrete oracle (`sailtest`) | run a probe on the model, bake results into a final test | anything the model executes | control flow — a jump target's address differs between probe and final ELF **[V]**; expected values are model-derived, so the Sail run is circular **[V]** |
| Template | hand-written | the residue neither engine reaches | does not scale; must stay small **[V]** 4 tests |

- **Routing interface**: an instruction is routed to the oracle iff the oracle
  corpus actually contains it — decided per instruction, not per extension. **[V]**
  `coverage_matrix.oracle_mnemonics()`.
- **Done when**: every in-scope instruction has a route, and the route is
  justified by an executed failure of the other engine, not an assumption.

### Stage 4 — Test representation and compilation

- **Purpose**: turn generated content plus expected state into a real ELF.
- **Inputs**: instruction sequence, initial-state tables, expected-state tables.
- **Output**: `.S` → `.elf`, self-checking, terminating via HTIF `tohost`.
- **Component**: `generate_object_riscv.rs`; `autotest/toolchain.py`. **[V]**
- **Interface**: the Golden Model's own `crt0.S` / `runtime.c` / `link.ld`.
- **Failure modes**: opcode width inferred from string padding rather than the
  encoding **[V]** (finding B2); harness scratch registers colliding with the
  test **[V]** (`reserved_harness_gprs`); memory-map mismatch between simulators
  **[V]** (finding C9).
- **Done when**: every generated ELF is a valid RISC-V executable and runs
  unmodified on a simulator we did not write. **[V]** 1894/1894 valid; 812
  passing on Spike.

### Stage 5 — Execution

- **Component**: `opcode_sweep.py`, `sailtest/runner.py`. **[V]**
- **Supported**: `sail_riscv_sim` **[V]**, Spike **[V]**, QEMU **[I]** (code path
  exists at `opcode_sweep.py:495 run_qemu`, binary not installed here, so not
  demonstrated this session), CVA6 RTL **[I]** (same — `--boot-fixed-entry`
  exists; not exercised).
- **Failure modes**: a target that boots from a fixed entry ignores the ELF
  entry point **[V]** (why `--boot-fixed-entry` exists).
- **Done when**: the same corpus runs on ≥2 independent simulators and results
  are collected per simulator, with "not installed" distinguished from "failed".

### Stage 6 — Validation

Four distinct checks. **Only the last two say anything about the model.**

1. **Self-check** — the test compares final state against expected and branches
   to `fail`. Necessary, not sufficient.
2. **Vacuity** — does the expected-state table contain anything? **[V]** 47.7% of
   the corpus once passed while comparing empty tables (finding B4).
3. **Mutation** — perturb each expected value; the test must fail. **[V]** 14/14
   caught on both simulators (finding C10).
4. **Differential** — Sail vs Spike. **[V]** the only independent axis for
   oracle-generated tests, whose expected values come from Sail itself.
- **Done when**: vacuity is a hard generation-time error and mutation is a
  standing gate, not a manual exercise.

### Stage 7 — Coverage

- **Purpose**: the RFP's own metric — coverage "in terms of the Sail code".
- **Inputs**: `-DCOVERAGE=ON` build; `sail_riscv_model.branch_info`; the corpus.
- **Output**: per-ELF and per-suite branch coverage, scoped.
- **Component**: `coverage_report.py`. **[V]**
- **Failure modes**: an ELF directory omitted from the default list contributes
  nothing, silently **[V]** (1003 CSR ELFs); **a test that exits nonzero writes
  no coverage file at all** **[V]** (finding C13), so all coverage figures are
  from passing tests only.
- **Done when**: per-ELF and per-suite both exist and reconcile (union of parts
  == whole), and the scope used for any quoted figure is written down.

### Stage 8 — Depth (path-directed generation)

- **Purpose**: one test per *architectural path*, not one per instruction. This
  is the corner-case axis.
- **Evidence for the gap**: **[V]** `--all-paths-for` yields 14 tests for one
  `lw` where the sweep takes 1; those 14 reach +76 more spans; `sys/vmem_ptw.sail`
  sits at 5.0% because the walker is almost entirely branches.
- **Blocker**: the trap expectation is a *global CLI flag*, so the 7 of 14 paths
  that trap are emitted with no expectation and scored as failures. **[V]**
- **Done when**: a path that traps asserts its own cause, and a mutated cause
  makes it fail.

### Stage 9 — Feedback

- **Purpose**: close the loop from measurement to generation.
- **Current state**: `coverage_report.py --uncovered` writes the list and **a
  human reads it** **[V]** — nothing consumes it.
- **Done when**: for at least one instruction family, uncovered branches drive
  generation without a human in the loop, and the coverage delta is measured.

### Stage 10 — Scale and configuration

- **Inputs**: the Golden Model's CI configuration matrix — **16 configs**,
  `rv{32,64}d` × VLEN {64,128,256,512} × ELEN {32,64}. **[V]**
- **Current**: 2 of 16 (`v128_e64`, both XLENs). **[V]**
- **Hard boundary**: the symbolic engine cannot exceed VLEN 129 (`B129`). The
  concrete engine has no such limit. **[V]**
- **Done when**: the concrete engine runs the full matrix, per-config results
  are reported, and the symbolic ceiling is documented as designed.

### Stage 11 — Regression and CI

- **Component**: `regression.py` (status-ranked, exits 1) **[V]**,
  `status_report.py` (MODEL vs FRAMEWORK vs ROUTED attribution) **[V]**.
- **Evidence it works**: **[V]** it refused to pass and caught six sweep targets
  silently destroyed by a mid-run `git checkout`.
- **Missing**: **[V]** no `.github/workflows/` exists in this repository.
- **Done when**: a CI job runs the suite and fails the build on regression.

### Stage 12 — Evaluation

- **Purpose**: demonstrate the suite is useful, against an external reference.
- **Method**: like-for-like Sail-coverage comparison against `riscv-arch-test`
  (ACT4), same extensions, same XLEN, same simulator. **[V]** performed for I+M
  RV32.
- **Done when**: the comparison is reproducible by script and covers more than
  one extension pair.

---

# Deliverable C — Current-state and gap analysis

Classification: *Not started · Prototype · Partially implemented · Functional ·
Validated · Production-ready*. "Validated" requires a negative control or a
mutation test, not a passing run.

| Area | Required capability | Evidence available | Missing work | Class | Risk |
|---|---|---|---|---|---|
| Model → IR | IR builds from unmodified upstream | Closed M1–M3 (`riscv-testgen-sail` #1–#12) **[V]** | Re-verify against current upstream; no CI guard | Functional | Sail drift silently breaks it again |
| Instruction extraction | Every instruction, no silent drops | 1280 instructions / 23 extensions / 0 unparsed **[V]** | Assert the count in CI | **Validated** | Low |
| Symbolic engine | Constructed privileged state | 15/15 privileged instructions; PMP, Sv39, shadow-stack scenarios **[V]** | Per-path generation (Stage 8 / Phase 9) | Functional | Depth gap is the main weakness |
| Concrete engine | Anything the model executes | FP/V/scalar-crypto backends pass on Sail+Spike **[V]** | RV32 for all backends is proven for `model`; other backends unverified **[U]** | Functional | Circular on Sail — must always be stated |
| Template engine | Residue | 4 tests, negative control verified **[V]** | Keep small | **Validated** | Growth here = a generator regression |
| ELF emission | Valid RISC-V ELFs | 1894/1894 valid; 812 pass on Spike **[V]** | — | **Validated** | Low |
| Execution: Sail, Spike | Differential | Both exercised throughout **[V]** | — | **Validated** | Low |
| Execution: QEMU, CVA6 | Portability | Code paths exist **[I]**; not run this session | Install and demonstrate | Prototype | Claiming support without a run |
| Self-checking | Test fails on mismatch | Mutation 14/14 **[V]** | Make it a standing gate | Functional | Manual audits do not recur |
| Vacuity control | No empty expected state | Detected and fixed once **[V]** | **No generation-time guard exists** | Partially implemented | This defect already cost 47.7% once |
| Coverage: suite | Sail branch coverage | 70.7% privileged / 72.1% current / 80.2% RFP scope **[V]** | — | **Validated** | Figures are passing-tests-only (C13) |
| Coverage: per-ELF | Individual ELF | Implemented, reconciles 2335==2335 **[V]** | Merge PR #66 | Functional | Unmerged |
| PMA | Region-attribute tests | 4/4 with controls **[V]** | Replay under per-ELF config, or coverage is not counted **[V]** | Functional | Coverage credit currently lost |
| Interrupts | Delivery + delegation | 3/3, traced `[S]` vs `[M]` **[V]** | RV32 variant **[U]** | **Validated** | Low |
| Path-directed depth | One test per path | `--all-paths-for` exists; 14 vs 1; +76 spans **[V]** | Per-path trap expectation; emitter work | Prototype | **Highest-value gap** |
| Coverage feedback | Uncovered drives generation | `--uncovered` writes a list **[V]**; nothing reads it **[V]** | The whole loop | Not started | Differentiator if built |
| Config matrix | 16 CI configs | 2 of 16 **[V]** | Parameterise the concrete engine | Partially implemented | Explicit RFP Goal |
| Reproducibility | Same seed → same tests | `.S` byte-identical across runs **[V]** | ELFs embed absolute paths | Functional | Cheap fix (`-ffile-prefix-map`) |
| Regression detection | Catch silent loss | `regression.py` caught a real corruption **[V]** | Wire to CI | Functional | Depends on a human running it |
| CI | Automated | **No `.github/workflows/`** **[V]** | Everything | Not started | RFP Deliverable 4 |
| External evaluation | vs existing suites | ACT4 I+M RV32 measured **[V]** | Script it; widen | Prototype | ACT4 leads on depth 194 vs 68 |
| Upstream contribution | Merged PRs | 1 model-fix PR open on fork **[V]** | Merge; upstream decision is the user's | Prototype | Outside our control |

## C.1 The three findings a reviewer will test

1. **Depth.** ACT4 reaches **194** branches we do not; we reach **68** they do
   not (I+M, RV32, like-for-like). **[V]** Stage 8 is the answer.
2. **Circularity.** Oracle expected values come from Sail, so the Sail column
   proves self-consistency, not correctness. **[V]**
3. **Coverage is from passing tests only.** Finding C13. Understates rather than
   overstates, but must be said. **[V]**

---

# Deliverable B — The 0→100 roadmap

Fourteen phases (0–13). Each phase is a **capability**; each task has an objectively
checkable acceptance criterion. Where prior work already satisfies a task, it is
cited as evidence — the task remains in the plan because the plan must be
executable end-to-end by someone starting from the repository.

## Phase 0 — Requirements, baseline and reproducibility contract
**Capability**: anyone can rebuild the baseline and get the same numbers.
- Pin the scope definitions and their justifications (`rfp-scope*.txt`).
- Make generation byte-reproducible including ELFs (`-ffile-prefix-map`).
- Record a baseline snapshot that `regression.py` compares against.
- **Acceptance**: two clean runs from the same seed produce identical `.S` *and*
  identical ELFs; `regression.py` reports clean against the recorded baseline.

## Phase 1 — Toolchain and model enablement
**Capability**: IR builds from an unmodified upstream model.
**Evidence**: closed M1–M3 in `riscv-testgen-sail` (#1–#12).
- Re-verify IR build against current upstream for both XLENs.
- **Acceptance**: `isla-sail` produces IR with no local model patches; the
  version pinned and recorded.

## Phase 2 — Model analysis and instruction extraction
**Capability**: the instruction set is derived, never hand-listed.
- Assert zero unparsed clauses as a build-time check, not a report line.
- **Acceptance**: adding an instruction to the model makes it appear in the
  corpus with no code change; a deliberately malformed clause fails the build.

## Phase 3 — Generation engines and the routing rule
**Capability**: every instruction has a justified route.
**Evidence**: closed M9, M10; current `model-*` backends.
- Record, per routed instruction, the executed failure that justifies the route.
- **Acceptance**: routing table is generated from evidence; no instruction is
  routed by extension label alone.

## Phase 4 — Test representation and compilation
**Capability**: valid, self-checking, portable ELFs.
**Evidence**: closed M4; 1894/1894 valid ELFs.
- **Acceptance**: 100% of generated ELFs are valid RISC-V executables and run
  unmodified on a simulator we did not write.

## Phase 5 — Execution environments
**Capability**: ≥2 independent simulators, plus portability targets.
- Demonstrate QEMU and CVA6 runs (currently code-only). **[U]**
- **Acceptance**: a corpus run reports per-simulator results with "not
  installed" distinguished from "failed"; QEMU and CVA6 each have one recorded
  passing run.

## Phase 6 — Privileged scenario construction
**Capability**: tests that construct architectural *state*, not just execute an
instruction. This is the RFP's #1 evaluation criterion and it is a distinct
capability from the generation engines of Phase 3 — an engine that can solve for
a path still needs someone to say *which* privileged state to construct.

**Evidence**: PMP violation, Sv39 walk, shadow-stack, PMA region-attribute and
interrupt delivery/delegation scenarios all exist and carry negative controls. **[V]**
- Each scenario ships a negative control that must fail.
- Cover the named-but-required M-mode features: PMP **[V]**, PMA **[V]**, trap
  handling **[V]**, interrupt delivery **[V]**; S-mode translation **[V]**.
- **Acceptance**: for every scenario, the control fails and the positive case
  passes on both simulators; a scenario whose control stops failing is treated
  as a regression, not a pass.

## Phase 7 — Validation and correctness
**Capability**: a test that checks nothing cannot be generated.
- **Vacuity gate**: empty expected-state table becomes a generation-time error.
- **Mutation gate**: sampled mutation testing runs in CI.
- **Negative-control policy**: every scenario ships one, and a control that
  stops failing is a regression.
- **Acceptance**: injecting an empty expectation fails generation; a mutated
  expected value fails the suite on both simulators.

## Phase 8 — Coverage measurement
**Capability**: per-ELF and per-suite Sail coverage, honestly scoped.
**Evidence**: PR #66.
- Replay ELFs under **their own** config, so config-dependent tests (PMA) count.
- **Acceptance**: per-ELF union reconciles with the suite total; `pma.sail`
  coverage rises when the PMA tests are included.

## Phase 9 — Depth: path-directed generation
**Capability**: one test per architectural path.
- Emit `mcause` per solved path (route de-risked: `regs()` +
  `GVAccessor::Field("bits")` works **[V]**; emitter must carry it).
- All-paths mode with a measured per-instruction cap.
- **Acceptance**: a trapping path asserts its own cause; a mutated cause fails;
  the coverage delta on a named subset is recorded; corpus growth is measured
  before the cap is chosen.

## Phase 10 — Coverage-guided refinement
**Capability**: uncovered branches drive generation.
- Close the loop for one family (PTE permission variants — the walker is at 5%).
- **Acceptance**: a documented uncovered branch is reached by a test the loop
  produced without human selection, and the delta is measured.

## Phase 11 — Configuration matrix and scalability
**Capability**: the model's own CI matrix.
- Parameterise the concrete engine over 16 configs; per-config reporting.
- Document the symbolic VLEN ceiling as designed, with the reason.
- **Acceptance**: a run produces per-config results for all 16; the ceiling is
  stated in user-facing docs.

## Phase 12 — Regression and CI automation
**Capability**: the build fails when quality drops.
**Evidence**: `regression.py` caught a real corruption. **[V]**
- Add `.github/workflows/` — none exists. **[V]**
- **Acceptance**: a deliberately introduced vacuous test fails CI.

## Phase 13 — Evaluation, comparison and productionisation
**Capability**: demonstrated usefulness against an external reference.
- Script the ACT4 comparison; widen beyond I+M.
- Developer-facing documentation for community maintenance.
- **Acceptance**: the comparison is one command; a maintainer who has not seen
  the code can add an extension using only the docs.

## B.1 Dependency graph and critical path

```
P0 baseline/reproducibility
 └─> P1 model enablement ─┬─> P2 extraction ──> P3 engines ──> P4 ELF ──> P5 execution
                          │                                                    │
                          └────────────────────────────────────────────────────┤
                                                                               ▼
                                                      P6 privileged scenarios
                                                                               │
                                                                               ▼
                                                                       P7 validation
                                                                               │
                                                                               ▼
                                                                       P8 coverage
                                                                        │       │
                                                     ┌──────────────────┘       └──────────┐
                                                     ▼                                     ▼
                                            P9 depth (paths)                      P11 config matrix
                                                     │                                     │
                                                     ▼                                     │
                                            P10 coverage feedback                          │
                                                     └──────────────┬────────────────────  ┘
                                                                    ▼
                                                            P12 regression/CI
                                                                    ▼
                                                            P13 evaluation
```

- **Critical path**: P1 → P2 → P3 → P4 → P5 → P6 → P7 → P8 → **P9** → P10 → P13.
- **Parallel**: P11 (config matrix) runs beside P9/P10 — it needs only P8.
  P12 (CI) needs only P7+P8. Documentation runs throughout.
- **P9 is the critical-path bottleneck for quality**, because P10 depends on it
  and the external comparison (P13) is currently lost on depth (194 vs 68).

---

# Deliverable D — Validation strategy

Five properties, each with a method that can fail.

| Property | Method | Fails when |
|---|---|---|
| Generation correctness | Differential Sail vs Spike | Two independent implementations disagree |
| Test is non-vacuous | Expected-state table non-empty, enforced at generation | A test asserts nothing |
| Test is sensitive | Mutation: perturb each expected value | A mutated value still passes |
| Scenario is real | Negative control per scenario | The control stops failing |
| Reproducibility | Same seed twice, byte-compare | Sources or ELFs differ |
| Coverage is honest | Scope file with written justifications; report both scoped and unscoped | A figure is quoted without its scope |

**The standing rule** — a completion criterion that an empty implementation
would also satisfy is not a criterion. "All tests pass" is satisfied by a suite
that checks nothing; this project has already paid that price once at 47.7%.

## D.1 Metrics — definition, measurement, baseline, target

Only metrics that are meaningful here and measurable with tooling that exists.

| Metric | Definition | Measured by | Baseline **[V]** | Target |
|---|---|---|---|---|
| Extraction completeness | unparsed model clauses | sweep report | 0 of 1280 | 0, asserted in CI |
| Sail branch coverage — privileged | covered/total `B` spans, privileged scope | `coverage_report.py --scope rfp-scope-privileged.txt` | **70.7%** (494/699) | ≥85% |
| Sail branch coverage — current scope | as above, current-deliverable scope | `--scope rfp-scope-current.txt` | **72.1%** (1098/1523) | ≥85% |
| Vacuity rate | tests with empty expected state | generation-time check (to build) | 0% now, was 47.7% | 0%, enforced |
| Differential agreement | in-scope failures that are model-side | `status_report.py` | 2 model / 0 framework in scope | model-side only |
| Reproducibility | identical `.S` for identical seed | byte compare | **holds** for `.S` | holds for ELFs too |
| Per-test yield | mean branches per test | `--per-elf` | 912 (I+M RV32) | ≥ ACT4's 1155 |
| Redundancy | tests adding nothing to the union | `--per-elf` | 13/53 (I+M RV32) | report, do not optimise blindly |
| Defect detection | findings with executed reproducers | `findings.md` | **4** (A1–A4) | continued yield |
| Config coverage | configs exercised of the model's CI matrix | sweep | **2 of 16** | 16 of 16 (concrete engine) |
| Regression stability | `regression.py` exit code | CI | passes on clean runs | gate the build |

**Deliberately excluded**: raw pass/fail counts as a headline (misleading in both
directions here), and generation throughput (not a stated RFP concern; record
it, do not target it).

---

# Deliverable E — GitHub mapping

- **Home**: `10x-Engineers/riscv-test-generation`, alongside the code.
- **Phases 1, 3, 4** cite the closed milestones in
  `10x-Engineers/riscv-testgen-sail` (M1–M11, 40 issues, all closed **[V]**) as
  evidence. Those are **not** re-created.
- **Hierarchy**: Phase milestone issue → task issues (native sub-issues +
  checklist), one native GitHub Milestone per phase.
- **Existing A1–A6 / B1–B6 task issues** are re-milestoned into the phases; the
  A/B milestone descriptor issues are retired with a pointer.
- **The 26 open P1–P5 sprint issues are left untouched**, with a triage list
  reported separately for a human decision.

## Unresolved — **UNKNOWN — REQUIRES INVESTIGATION**

1. QEMU and CVA6 execution — code paths exist; neither demonstrated here.
2. RV32 support for the `model-fp` / `model-v` / `model-vk` backends — only the
   base `model` backend was verified on RV32.
3. Corpus growth and wall-clock cost of all-paths generation across the full
   instruction set — measured only for one instruction.
4. Whether the ACT4 depth gap (194 vs 68) closes with path-directed generation,
   and by how much. Hypothesis, not a result.
5. Cost/rate inputs for any commercial timeline — not visible from the
   repository and not inferable.
