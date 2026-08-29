# Master Technical Plan
## Automated Test Generation Framework for the RISC-V Sail Golden Model

**Status.** Authoritative technical source of truth. GitHub structure, milestones, issues and any
stakeholder-facing proposal are derived *from* this document, not the reverse.

**Evidence discipline.** Claims are tagged **[V]** verified by execution, **[I]** inference from
evidence, **[U]** unknown. Where the source material does not establish a fact, this document
writes **UNKNOWN — REQUIRES VERIFICATION** rather than estimating.

**Relationship to prior documents.** Consolidates and supersedes the earlier master plan, the
technical assessment, and the RFQ-alignment review. Technical content established there and still
correct is carried forward unchanged; re-deriving settled decisions would introduce inconsistency
for no gain.

---

# 1. Executive Summary

The framework generates, organises, executes, validates and measures RISC-V test suites derived
directly from the Sail Golden Model, and integrates that capability into the Golden Model's own
CI.

Four decisions shape the engineering strategy. Each is forced by evidence, not preference:

1. **Configuration is an architecture, not a set of constants.** One configuration object, loaded
   from the Golden Model's own JSON, derives every downstream artefact — model invocation,
   generator legality set, toolchain flags, simulator ISA string, test metadata and coverage
   scope. This is the foundation phase because configuration disagreement between components
   produces failures indistinguishable from model defects, the most expensive false signal a
   verification framework can emit.

2. **The model dependency is made upstreamable before anything is built on it.** The framework
   requires model entry points that do not exist upstream **[V]**. Goal 1 and Deliverable 5 are
   both gated on decomposing and merging those changes, and merge latency is outside our control —
   so this work starts early and runs continuously.

3. **Validation integrity is a deliverable, not a quality attribute.** The framework must make it
   structurally impossible to report a passing suite that checks nothing. Every coverage and
   pass-rate claim depends on it, so it gates all reporting work.

4. **Coverage is a reporting capability, not a control loop.** Goals 6 requires per-ELF and suite
   coverage. It does not require coverage-guided generation, and measured evidence does not
   support prioritising it above contractual deliverables. It is scoped as future work with a
   clean interface.

The plan comprises **twelve phases** across four tracks. The critical path runs configuration →
generation → validation → suite/results → configuration matrix → turn-key → CI integration, with
the upstream merge track running alongside and joining at the terminus. **Deliverables 4 and 5 are
merge-gated and therefore carry the programme's highest risk**, regardless of engineering effort
applied.

## 1.1 What RISC-V receives

| # | Deliverable | Objective acceptance evidence |
|---|---|---|
| 1 | Repository with framework, user documentation and developer documentation | A clean machine completes install → configure → generate → execute → coverage using documentation alone; an outside maintainer adds a backend and a privileged scenario from the developer documentation alone |
| 2 | Example script generating a suite from a given configuration | One documented command produces an extension-organised, manifested, provenance-stamped suite for **every configuration in the committed set (§6.6)**; provenance regenerates it byte-identically |
| 3 | Example script executing a suite, collecting results, summarising | One documented command produces per-test verdicts, a machine-readable result file and a generated summary, using a simulator the project did not write |
| 4 | Golden Model CI integration | **Merged** pull request |
| 5 | Required Golden Model changes | **Merged** pull requests, or the change eliminated and the framework working without it |

## 1.2 Scope

**In scope.** Privileged ISA test generation — M-mode CSR access, traps, privilege transitions,
PMP, PMA, interrupts and delegation (required); S-mode virtual memory and address translation
(highly desirable). Valid ELF generation, extension organisation, configuration support, per-ELF
and suite coverage, and the five deliverables above.

**Explicitly out of scope for this iteration.** The **Hypervisor extension**. Floating-point and
vector *capability expansion* beyond what already exists to support the configuration matrix.
**Coverage-guided generation** — the RFQ does not require it and measured evidence does not
support prioritising it above the deliverables. Additional simulators beyond those needed to
satisfy Deliverable 3.

These are excluded from implementation but **not** from architecture: section 14 defines the
extension points that make each of them addable later without redesign.

| Area | Current scope (this RFQ) | Future scope (architecture ready, not built) | Out of scope |
|---|---|---|---|
| Privileged M-mode | CSR access, traps, privilege transitions, PMP, PMA, interrupts, delegation — **required** | Deeper trap-cause and delegation combinations | — |
| Privileged S-mode | Sv32 and Sv39 address translation with controls — **highly desirable** | Sv48, Sv57, PTE content features, full fault taxonomy | — |
| **Hypervisor** | — | — | **Out of scope for this RFQ.** Not implemented, not costed, not scheduled |
| Unprivileged ISA | Only what privileged scenarios require | Systematic unprivileged generation (extension point exists) | — |
| Floating point / vector | Only what the committed configuration set requires | Capability expansion | — |
| Configurations | The committed set in §6.6 | Further Golden Model configurations via the same interface | Configurations the Golden Model itself excludes from its test matrix |
| Coverage | Per-ELF and suite, attributed and scoped | Coverage-guided generation | — |
| Simulators | Golden Model + one independent simulator (Deliverable 3) | Additional simulator backends via the execution interface | — |
| Pass/fail | Self-checking with differential execution | Trace-based or other schemes via the validation interface | — |

## 1.3 The three external dependencies

Stakeholders should be aware that three outcomes are **not within the project's unilateral
control**, and the plan is explicit about each rather than assuming success:

1. **Upstream acceptance of required Golden Model changes** (Deliverable 5) — see §3.4 for exactly
   what is required and how invasive it is, and §18.5 for the fallback position per change.
2. **Upstream acceptance of the CI integration** (Deliverable 4) — see §17.3.
3. **Review latency for both**, which sets the programme's critical-path duration and cannot be
   compressed by engineering effort.

---

# 2. Project Objectives and RFQ Requirements

## 2.1 Goals

| # | Goal | Owning phases |
|---|---|---|
| 1 | Use the current up-to-date Sail RISC-V Golden Model | P2 |
| 2 | Generate valid RISC-V ELF files | P4 |
| 3 | Organise generated tests by ISA extension where practical | P4, P9 |
| 4 | Support Golden Model configurations, particularly those used by Golden Model CI (RV32, RV64, F/D, relevant VLEN/ELEN) | P1, P10 |
| 5 | Focus on the privileged ISA — M-mode, PMP, PMA, traps, interrupts required; S-mode virtual memory and address translation highly desirable; Hypervisor **out of scope** | P6, P7 |
| 6 | Coverage for an individual ELF and for an entire generated suite | P8 |
| 7 | Open-source licence compatible with the Golden Model | P11 |

## 2.2 Deliverables

| # | Deliverable | Owning phases | Completion rule |
|---|---|---|---|
| 1 | Repository with framework, user documentation and developer documentation sufficient for long-term maintenance | P3, P11 | Engineering completion |
| 2 | Example scripts generating a suite from a given configuration | P11 | Engineering completion |
| 3 | Example scripts executing tests on a simulator, collecting results, summarising | P9, P11 | Engineering completion |
| 4 | Approved **and merged** PRs integrating the framework into Golden Model CI | P12 | **Merged only** |
| 5 | Required Golden Model extensions created, reviewed, approved **and merged** | P2 | **Merged only** |

For Deliverables 4 and 5: PR created ≠ complete; reviewed ≠ complete; approved ≠ complete;
**merged = complete**. These are tracked as distinct states throughout.

## 2.3 Considerations

| ID | Consideration | Owning phase | Section |
|---|---|---|---|
| A | Architectural Certification Test compatibility | P4 | 12 |
| B | Generated test readability | P4 | 13 |
| C | Future unprivileged ISA support | Architecture | 14 |
| D | Future extensibility | Architecture | 14 |
| E | Golden Model evolution | P2, P8, P12 | 11, 17 |
| F | Coverage methodology | P8 | 9 |
| G | Pass/fail methodology | P5 | 8 |

## 2.4 Evaluation criteria

| # | Criterion | How the plan addresses it |
|---|---|---|
| 1 | Coverage of privileged ISA extensions implemented by Sail | P6, P7 capability; P8 per-extension privileged coverage view and trend |
| 2 | Long-term maintainability and extensibility | Section 14 extension points; P3 self-verification; P11 developer documentation acceptance test |
| 3 | Integration with open-source communities | Section 19 engagement map with USE/INTEGRATION/CONTRIBUTION/UPSTREAMING classification |
| 4 | Demonstrated technical integration with targeted ecosystems | Emerges from merged PRs, CI integration, reproducible examples — **no artificial tasks created** |
| 5 | Cost | Phase structure, scope, dependencies and critical path make effort derivable. **No costs invented** |
| 6 | Delivery date | Dependency-aware phase structure and identified critical path make a schedule derivable. **No dates assigned** |

## 2.5 Project success metrics

Objective indicators derived from the acceptance criteria already stated in this plan. Each is
measurable by execution or by observable upstream state — none requires a judgement call. **No
target percentage is set for coverage, for the reasons in §9.4.**

| # | Metric | How measured | Success condition | Source |
|---|---|---|---|---|
| 1 | Clean-environment usability | Execute install → configure → generate → execute → coverage on a machine that has never built the project | Completes with no file editing and no undocumented prerequisite | §25 D1 |
| 2 | Configuration end-to-end support | Per-stage matrix executed in P10 | Every configuration in the committed set (§6.6) passes all nine stages, **or** is reported unsupported with the failing stage and an architectural reason | §6.6, P10 |
| 3 | Privileged capability | Scenario execution on the Golden Model and an independent simulator | Every required M-mode capability and Sv32+Sv39 pass, **with every negative control failing** | P6, P7 |
| 4 | Validation integrity | Mutation sampling and control checks | 100% of sampled tests fail under expectation mutation; 100% of controls fail; validated count reconciles with executed count | §8.2 |
| 5 | Per-ELF coverage correctness | Reconciliation invariant asserted every run | Union of per-ELF span sets equals the suite total, on every run | §9.2 |
| 6 | Coverage attribution completeness | Attempt to emit a figure without scope | Impossible — every figure carries configuration, model revision and exclusion scope | §9.2, §9.4 |
| 7 | Privileged coverage improvement | Baseline at a stated revision, then per-extension measurement | Measured increase over the recorded baseline, with residual enumerated and classified | §9.5 |
| 8 | Reproducibility | Regenerate a suite on a second machine from provenance alone | Byte-identical suite, or a named and documented source of nondeterminism | §11 |
| 9 | Framework reliability | Framework test suite in CI | Green, gating every change; a deliberately-introduced regression is caught | §16 |
| 10 | Maintainability | An engineer outside the project follows the developer documentation | Adds a backend and a privileged scenario without author assistance | §15.3 |
| 11 | Extensibility | Inspect the eight extension points | All documented; at least one exercised by a second implementation | §26 item 19 |
| 12 | Deliverable 5 | Upstream repository state | Every required model change **merged**, or eliminated with the framework working without it | §18 |
| 13 | Deliverable 4 | Upstream repository state | CI integration **merged** | §17.3 |

Metrics 12 and 13 depend on third-party review and are the only two the project cannot satisfy by
engineering effort alone.

---

---


---

# 3. Current Technical Baseline

This section states what the source assessment established, so the roadmap is built on evidence
rather than assumption. **It is not a claim of completion.** Everything below is prototype
evidence that informs production implementation.

## 3.1 Existing evidence

| Area | Evidence | Status |
|---|---|---|
| ELF generation | Generated files are valid ELF32/ELF64, type EXEC, machine RISC-V, correct entry point and sections, and execute on an independent simulator **[V]** | **Demonstrated** |
| Per-ELF and suite coverage | Isolated per-ELF replay with absolute and marginal contribution, and a reconciliation invariant asserting the suite union equals the union of per-ELF sets **[V]** | **Demonstrated** |
| Privileged scenarios | Memory-protection violation, trap entry/return, privilege transitions, interrupt delivery and delegation, and 64-bit address translation — passing on the Golden Model *and* an independent simulator, with negative controls failing as required **[V]** | **Demonstrated at one XLEN** |
| Backend routing | Floating point provably fails through the symbolic path and provably succeeds through the oracle **[V]** | **Demonstrated** |
| Model-sourced instruction extraction | Instruction set derived from the model's own assembly declarations with unparsed clauses reported **[V]** | **Prototype** |
| Configuration consumption | The oracle consumes Golden Model configuration JSON directly, proving the format is usable unchanged **[V]** | **Partial** |
| Circularity awareness | Tooling warns when only the Golden Model was run, because expectations derive from it **[V]** | **Demonstrated** |

## 3.2 Known limitations

| Limitation | Evidence | Consequence |
|---|---|---|
| Configuration re-derived independently in four places | Model config, symbolic IR, assembler flags, simulator ISA string **[V]** | A test can pass on the Golden Model and fail on the independent simulator purely from framework disagreement |
| Framework requires a modified Golden Model | Required entry points verified absent from upstream; the IR build requires them by name **[V]** | Goal 1 and Deliverable 5 both at risk |
| Model change set is a single large multi-file commit | **[V]** | Not reviewable upstream as submitted |
| No CI in any repository | **[V]** | Silent regression; Deliverable 4 at stage zero |
| No dependency manifest for the orchestration layer | **[V]** | Clean-environment install impossible |
| Compiled symbolic artefact committed and divergent from its source | **[V]** | Reproducibility broken; licensing question |
| No mechanical anti-vacuity enforcement | A prior corpus passed at scale while comparing empty expected-state tables **[V]** | Pass rates not trustworthy without it |
| Failing tests write no coverage record | **[V]** | Negative controls are structurally invisible to coverage |
| Address translation exists only for the 64-bit scheme | **[V]** | At RV32, translation scenarios *skip* — indistinguishable from passing in a summary |
| Framework's own test suite failing | **[V]** | Framework reliability unverified |
| Symbolic engine cannot represent vector lengths above its bitvector bound | **[V]** | Structural boundary, not a defect |
| Symbolic engine cannot execute floating-point arithmetic or vector element paths | **[V]** | Forces the hybrid methodology |

## 3.3 Unknowns requiring verification

The following are **UNKNOWN — REQUIRES VERIFICATION** and are not assumed anywhere in this plan.
Each is scoped as an explicit verification task rather than as capability:

- **Floating point at the 32-bit XLEN through the oracle backend.** Scoped into P10.
- **QEMU and Whisper execution.** Code paths exist; execution never demonstrated. Scoped into
  P9 and P10, and relevant to Deliverable 3's choice of independent simulator.
- **The complete model-derived CSR sweep.** A wide sweep was interrupted and its result never
  recorded. Scoped into P6.
- **Individual files within the model change set.** Not analysed one by one. Scoped into P2.

## 3.4 Required Golden Model changes — concrete statement

This is the question a Golden Model reviewer will ask first, so it is answered here rather than
left to the upstream section.

**The framework's symbolic generation path currently cannot run against unmodified upstream.**
Verified: the two entry-point functions it requires are absent from upstream, and the IR build
step requires them by name **[V]**.

| Change | What it is | Why required | Invasiveness | Upstreamable? | Fallback if rejected |
|---|---|---|---|---|---|
| **Symbolic entry points** | Two functions added to the model's top-level module, providing an initialisation and a step entry point for external symbolic tooling | The IR compilation step preserves them by name; without them the symbolic backend cannot be built at all | **Low** — additive, no change to existing behaviour | **[I] Likely** — additive entry points for external tooling | Symbolic backend unavailable; framework operates oracle-only, losing solver-derived machine state. Privileged scenarios relying on solved state would move to preamble construction. **Capability reduction, not project failure** |
| **CSR accessor restructuring** | Consolidation of scattered CSR accessor definitions | Required for the symbolic path to resolve CSR access | **High** — large, touches a widely-used area | **[U] UNKNOWN — REQUIRES VERIFICATION.** The most likely change to be rejected | If rejected, CSR-driven symbolic generation is unavailable; CSR coverage moves entirely to the oracle path. **P2 must first test whether this change is eliminable** |
| **Reset-behaviour fix** | Correction in memory-protection register reset | Independent defect fix | **Low** — narrow | **[I] Likely** — carries its own justification independent of this project | Not required by the framework; would be submitted as a standalone contribution regardless |
| **Remaining files in the change set** | Not individually analysed | — | **[U] UNKNOWN** | **[U] UNKNOWN** | Per-file triage is the first task of P2 |

**Stakeholder position.** The project does **not** assume any of these will be accepted. P2's first
task is to determine which are eliminable and remove them; the remainder are decomposed into
single-purpose reviewable PRs ordered least-contentious first. **The high-invasiveness change is
the one genuine threat to the symbolic backend, and it is stated as such rather than presented as
a formality.**

---

# 4. Target Architecture

## 4.1 End-to-end flow

```
  Golden Model configuration (JSON — the model's own format, canonical)
                    │
                    ▼
        ┌───────────────────────┐
        │  Configuration Object │  ◄── single source of truth
        │  validated at load    │      rejects illegal combinations
        └───────────┬───────────┘
                    │ derives, never re-derives
    ┌───────────┬───┴────┬──────────┬───────────┬──────────┐
    ▼           ▼        ▼          ▼           ▼          ▼
  Sail      generator  toolchain  simulator   ELF        coverage
  --config  legality   march/mabi ISA string  metadata   scope
    │           │        │          │           │          │
    └───────────┴────────┴──────────┴───────────┴──────────┘
                    │
                    ▼
   ┌────────────────────────────────────────────┐
   │ GENERATION                                  │
   │  scenario / constraint construction         │
   │  ├─ machine state: preamble  or  solver     │
   │  └─ body: symbolic │ oracle │ template      │
   └────────────────────┬───────────────────────┘
                        ▼
   assembly (formatted, optionally annotated)
                        ▼
   implementation-dependent operations layer (boot / terminate / interrupt)
                        ▼
        assembler → linker → ELF + configuration metadata
                        ▼
        organised suite: <extension>/ + manifest + provenance
                        ▼
   ┌────────────────────────────────────────────┐
   │ EXECUTION: Golden Model  AND  independent   │
   │            simulator                        │
   └────────────────────┬───────────────────────┘
                        ▼
   VALIDATION: expected vs actual, differential, failure classification
                        ▼
   COVERAGE: per-ELF (isolated replay) → suite aggregation → attribution
                        ▼
   RESULTS: machine-readable records + generated human summary
                        ▼
   CI: framework repository CI  →  Golden Model CI (merged)
```

## 4.2 Component responsibilities

| Component | Responsibility | Classification |
|---|---|---|
| **Sail Golden Model** | instruction source, execution oracle, coverage target | **Core** — all three roles |
| **ISLA + Z3 (SMT)** | derive initial machine state making a chosen path feasible | **Core, narrowed** |
| **isla-gen / isla-testgen** | symbolic generation front-end; emits assembly and linker script | **Core** |
| **Oracle generation** | model executes concrete operands; resulting state becomes the expectation | **Core** — only backend reaching FP and vector |
| **Template / scenario generation** | hand-written tests with mandatory negative controls | **Core, capped** |
| **Python orchestration** | configuration, drivers, suite management, coverage, results | **Core** |
| **Rust components** | the symbolic engine | **Core** |
| **OCaml (isla-sail)** | compiles the model to IR for symbolic execution | **Build-time dependency** |
| **Toolchain** | encoding authority and ELF construction | **Core** |
| **Independent simulator (Spike)** | the only non-circular check | **Core** |
| **QEMU / Whisper** | additional independent simulators | **Optional** — RFQ names them as alternatives |
| **Coverage infrastructure** | Sail source-span measurement | **Core** |
| **Coverage-guided generation** | gap → targeted generation loop | **Future, out of core scope** |

**Rationale for narrowing the symbolic engine.** It cannot execute floating-point arithmetic or
vector element paths, cannot represent vector lengths above its bitvector bound, and exhausts
memory on certain instructions **[V]**. These are structural properties. Assigning it the job it
uniquely performs — solving for machine state — and routing everything else to the oracle produces
a system whose gaps are the *intersection* of its backends' gaps rather than the union.

## 4.3 Architectural principles

1. **Configuration is derived once and propagated.** No component independently re-derives a
   configuration fact.
2. **The model is the source of truth for what exists.** Instructions, CSRs and coverage targets
   come from the model, never a parallel list that can drift.
3. **A test that cannot fail must not ship.** Invariant across pass/fail mechanisms; enforced at
   emission, not by review.
4. **Nothing is dropped silently.** Anything not generated is reported with a reason.
5. **Every published number carries its scope** — configuration, model revision, exclusions.
6. **Independence is required for a pass.** A result checked only against the model that produced
   the expectation is reported as inconclusive.
7. **Extension happens by adding data, not by restructuring code.**
8. **Reuse existing community work where it fits — decided by evaluation, never assumed.**
9. **Provenance makes results reproducible.**
10. **Model changes are expected, not exceptional.**

---

# 5. Test Generation Methodology

## 5.1 Selected methodology

**A configuration-driven hybrid, with machine-state construction as the primary lever.**

## 5.2 Why a hybrid is necessary — and why the model alone is not enough

The Sail Golden Model is an authoritative, executable definition of the architecture. It answers
*"given this machine state and this instruction, what happens?"* — completely and precisely.

**Automatic test construction asks the opposite question:** *"what machine state and which
instruction would reach this behaviour, and what result should be checked?"* The model does not
answer that, because it is a definition rather than a search procedure. Three things must be
supplied around it:

1. **Reaching interesting state.** Most of the model is privilege checks, legalisation and error
   paths, reachable only from specific machine state — particular CSR values, protection entries,
   page tables. Something must *derive* that state. This is what symbolic execution with an SMT
   solver provides: given a target path, it solves for an initial state that reaches it.
2. **Knowing the expected result.** A test must assert something. Running the model on concrete
   operands and capturing the resulting architectural state provides that — the oracle approach.
3. **Covering what neither can reach.** Some behaviour defeats both: the solver cannot execute
   floating-point arithmetic or vector element paths **[V]**, and the oracle cannot construct
   state by solving or express control flow **[V]**. A bounded set of hand-written scenarios with
   mandatory negative controls covers the remainder.

**No single one of these three suffices**, and their limitations are complementary rather than
overlapping — which is precisely what makes combining them worthwhile. A hybrid whose backends had
the *same* weaknesses would add complexity for nothing. Here, the gaps of the combined system are
the **intersection** of the backends' gaps rather than their union.

The cost is real: three backends and routing between them is more to maintain than one. That cost
buys the RFQ's most heavily weighted criterion — privileged coverage — because privileged
behaviour is exactly the part requiring solver-derived state.

## 5.3 Rationale

The methodology is retained from the assessment rather than replaced, because the assessment found
it defensible and found the *engineering around it* deficient. The evidence for the hybrid is
capability-based and measured:

- The symbolic engine cannot execute floating-point arithmetic (the model calls software
  floating-point routines absent from its IR) or vector element paths **[V]**.
- The oracle cannot construct machine state by solving, and is circular against the Golden Model
  because its expectations derive from it **[V]**.
- Neither engine can produce compressed control-flow instructions, for unrelated reasons **[V]**.

A single-backend design is therefore disqualified by capability evidence, not preference.

## 5.4 What each component provides

| Component | Provides |
|---|---|
| **Sail** | The authoritative definition of instructions, CSRs and architectural behaviour; the execution oracle; the coverage target |
| **ISLA** | Symbolic execution of the model, yielding feasible paths and the constraints that reach them |
| **SMT / Z3** | Satisfying assignments for those constraints — the initial machine state that makes a target path reachable |
| **Oracle generation** | Concrete execution of the model to capture resulting architectural state as the expectation; the only route to floating point and vector |
| **Template / scenario generation** | Bounded coverage of what neither engine can reach, always with negative controls |

## 5.5 Backend routing

Routing is **data, not control flow** — a declarative table keyed on instruction class and
configuration, with automatic fallback. Resource exhaustion and timeout are distinguished: the
former reroutes to another backend, the latter retries with a longer budget. Conflating them leads
to the wrong fix.

## 5.6 Generation sequence

1. **Constraint construction.** Architectural intent is expressed as scenario descriptors —
   privilege mode, protection entry state, translation mode, trap expectation — compiled into
   solver constraints or preamble code.
2. **Machine-state construction.** Preamble where state can be written; solver where state must be
   deduced. Preamble is the default because it is inspectable, debuggable and configuration-portable.
3. **Legality.** Two gates: the configuration's legality set filters instructions before
   generation; the assembler round-trip confirms encodability. The first gate is required because
   an assembler will encode an instruction that is illegal on the selected machine.
4. **Assembly.** Preamble + body + verdict epilogue, emitted through the implementation-dependent
   operations layer (section 12), consistently formatted and optionally annotated (section 13).
5. **ELF.** Real cross-assembler and linker with configuration-derived flags. The assembler is the
   encoding authority; nothing is hand-encoded.
6. **Validation.** Section 8.
7. **Coverage.** Section 9.

---

# 6. Configuration Architecture

## 6.1 Principle

**Configuration is derived once and propagated. It is never re-derived.**

The failure mode eliminated: when independent components compute the same configuration fact
differently, a test passes on the Golden Model and fails on the independent simulator — presenting
as a model defect when it is a framework disagreement.

## 6.2 Single source of truth

The Golden Model's own configuration JSON is adopted unchanged. **No parallel configuration format
will be invented** — the oracle already demonstrates the format is directly consumable **[V]**.
It is loaded into a Configuration object that is validated at load, immutable for the lifetime of
a run, and hashed with the hash recorded in every artefact it influences.

## 6.3 Derivation map

| Consumer | Derived value |
|---|---|
| Sail model | configuration path passed to the emulator |
| Symbolic IR | vector length, XLEN — compile-time properties of the IR |
| Generator | legal instruction set, register-file availability, enabled units |
| Assembler / linker | architecture and ABI flags |
| Simulator | ISA string including vector-length extension, memory layout |
| ELF metadata | required extensions, configuration hash, parameters |
| Coverage | scope selection, configuration attribution |
| CI | matrix dimension |

**Design rule:** architecture-string, ABI-string and simulator-ISA construction exist in exactly
one module consumed by every component. Duplication is structurally prevented, not patched.

## 6.4 Hard boundaries

- The symbolic engine's bitvector representation imposes a **hard upper bound on vector length**
  **[V]**. Configurations above it are routed to the oracle by policy; the boundary is documented
  as architectural.
- Vector length is a **compile-time property of the symbolic IR** **[V]**. Either the IR is built
  per configuration, or symbolic vector generation is scoped to the buildable set. **This decision
  is a P1 deliverable, not an assumption.**

## 6.5 Target configuration validation matrix

Every configuration is assessed per stage. **A single "supported" flag is not used** — collapsing
nine stages into one hides exactly the failures that matter. Statuses: **SUPPORTED** ·
**PARTIALLY SUPPORTED** · **UNSUPPORTED** · **UNKNOWN — REQUIRES VERIFICATION**.

Stages assessed per configuration: configuration selection · generation · compilation · ELF
creation · Golden Model execution · independent simulator execution · validation · coverage ·
end-to-end.

Target set: the Golden Model's own exercised configuration set, spanning RV32 and RV64, floating
point, and the vector-length and element-width combinations the model's own test matrix builds.
**Configurations the Golden Model itself excludes from its test matrix are not treated as
mandatory**; any decision to include one is justified explicitly.

## 6.6 Committed configuration scope

A stakeholder must be able to answer *"which configurations will this support?"* without waiting
for P10. That question has two different answers and **they must not be conflated**:

- **Generation support** — the framework can produce tests for the configuration.
- **End-to-end support** — generation, compilation, ELF, Golden Model execution, independent
  simulator execution, validation and coverage all succeed.

**Only end-to-end support is claimed as "supported."**

| Configuration | Commitment | Basis |
|---|---|---|
| **RV32** | End-to-end **committed** | Generation, execution on two simulators and coverage already demonstrated at this XLEN **[V]** |
| **RV64** | End-to-end **committed** | Same **[V]** |
| **F/D at RV64** | End-to-end **committed** | Generation and independent-simulator execution demonstrated via the oracle backend **[V]** |
| **F/D at RV32** | **Target, not committed** | **UNKNOWN — REQUIRES VERIFICATION.** Resolved in P10; if it fails, recorded as unsupported with the failing stage named |
| **Vector-length / element-width combinations in the model's own test matrix** | **Target, with a stated architectural bound** | Oracle path demonstrated generating and executing at a non-default vector length **[V]**. The symbolic path has a hard representational upper bound on vector length **[V]**, so configurations above it are oracle-only by design |
| **Combinations the Golden Model itself excludes from its test matrix** | **Not committed** | Excluded upstream for runtime reasons; included only if separately justified |

**What the project commits to for every configuration in the target set**, whether or not it ends
up supported: a per-stage status backed by an execution record, and a documented architectural
reason for every stage that does not work. **An unsupported configuration will be reported as
unsupported, not omitted.**

The populated matrix is **generated by execution in P10, not asserted here.** Populating it before
execution would be exactly the unsupported claim this plan forbids.

---

# 7. Privileged ISA Generation Strategy

Ordered by technical dependency and RFQ value.

## 7.1 M-mode — **REQUIRED** by the RFQ

| Order | Capability | Rationale for position | Depends on |
|---|---|---|---|
| 1 | CSR access across the model-derived register set, both XLENs | Several privileged extensions define no instructions at all and are reachable only through specific CSR addresses. Nothing else in M-mode is complete without it | Configuration architecture |
| 2 | Trap entry, trap return, privilege transitions | Infrastructure for everything below — subsequent scenarios need a trap handler and a way to change privilege | 1 |
| 3 | PMP enforcement | Required by the RFQ; observable only from a lower privilege mode | 2 |
| 4 | PMA scenarios, both XLENs | Region attributes are configuration-driven; requires the configuration architecture to vary them legitimately | 2, configuration |
| 5 | Interrupt delivery and delegation | Requires trap infrastructure; delegation additionally requires supervisor entry | 2 |
| 6 | Trap-cause enumeration | Breadth pass once the mechanisms exist | 2–5 |

**Architectural constraint to document rather than solve:** machine-level interrupt delegation is
prevented by the model's own legalisation of the delegation register **[V]**. Delegation scenarios
target supervisor-level interrupts. Recorded so it is not re-investigated.

## 7.2 S-mode — **HIGHLY DESIRABLE** per the RFQ

| Order | Capability | Rationale for position |
|---|---|---|
| 1 | **Sv32** | Unblocks the entire RV32 privileged path. Without it, RV32 translation scenarios *skip*, which is indistinguishable from success in a summary. **Highest-value single item in the privileged track** |
| 2 | Sv39 | RV64 baseline translation scheme |
| 3 | Sv48, Sv57 | Depth variants against the same harness; low marginal cost once 1–2 exist |
| 4 | PTE content features — hardware A/D update, trap-on-improper-A/D, NAPOT contiguity, page-based memory types, reserved-for-software bits | Content variants requiring a working walk harness |
| 5 | Fault taxonomy — page faults, access faults, permission violations, U/S access rules | Breadth pass across 1–4 |

Every S-mode item depends on M-mode items 1 and 2.

**Commitment level within S-mode.** Items 1–2 (Sv32, Sv39) are **committed** — they appear in the
Definition of Done and in success metric 3. Items 3–5 are **FUTURE within this RFQ**: delivered if
the schedule permits, and otherwise **explicitly deferred with a recorded justification** rather
than silently omitted. This distinction exists so a stakeholder is not led to expect Sv48/Sv57 and
the full PTE feature set as contractual content.

## 7.3 Privileged coverage by extension and configuration

The Golden Model declares **28 named privileged extensions** — 4 `Sm*`, 12 `Ss*` and 12 `Sv*`
**[V]**. With the three privilege-mode extensions `S`, `U` and `H`, the privileged portion of the
model's extension enum totals 31, which is the figure §9.6.1 uses when partitioning all 125 declared
extensions. The two counts describe the same set at different boundaries; this table enumerates the
28 named extensions, since `S`, `U` and `H` are covered by the mode-transition scenarios of §7.1 and
§7.2 rather than as extensions in their own right.

This table states our commitment for each, against each XLEN. It reconciles to 28 so that a reader
can see the whole set rather than infer the residual.

**Key.** ✅ committed this RFQ · ○ future within this RFQ (delivered if schedule permits, otherwise
deferred with recorded justification) · **—** not applicable, enforced by the model · ✗ out of scope.

**XLEN applicability is taken from the model, not from convention:** `core/extensions.sail` gates
`Sv32` on `xlen == 32`, and `Sv39`, `Sv48`, `Sv57`, `Svnapot` and `Svpbmt` on `xlen == 64`.

### M-mode — required

| Extension / capability | RV32 | RV64 | Under F/D configs | Under V configs | How it is reached |
|---|:--:|:--:|:--:|:--:|---|
| **PMP** *(base architecture, not an extension)* | ✅ | ✅ | ✅ | ○ | Enforcement scenario + controls |
| **PMA** *(base architecture)* | ✅ | ✅ | ✅ | ○ | Region-attribute configs |
| **Traps / exceptions** *(base)* | ✅ | ✅ | ✅ | ○ | Expected-cause scenarios |
| **Interrupts + delegation** *(base)* | ✅ | ✅ | ✅ | ○ | Delivery and delegation scenarios |
| **Privilege transitions** *(base)* | ✅ | ✅ | ✅ | ○ | mret/sret into M, S, U |
| S (supervisor mode) | ✅ | ✅ | ✅ | ○ | Privilege transitions + S-mode entry |
| U (user mode) | ✅ | ✅ | ✅ | ○ | Privilege transitions |
| Zicsr | ✅ | ✅ | ✅ | ○ | Instruction sweep (6 mnemonics) |
| Zicntr | ✅ | ✅ | ✅ | ○ | CSR sweep — RV32 adds the high-half registers |
| Zihpm | ✅ | ✅ | ✅ | ○ | CSR sweep |
| Stateen (Sm/Ss) | ✅ | ✅ | ✅ | ○ | CSR sweep — RV32 adds high-half registers |
| Smcntrpmf | ✅ | ✅ | ✅ | ○ | CSR sweep — RV32 adds high-half registers |
| Sscofpmf | ✅ | ✅ | ✅ | ○ | CSR sweep |
| Sscounterenw | ✅ | ✅ | ✅ | ○ | CSR sweep |
| Ssqosid | ✅ | ✅ | ✅ | ○ | CSR sweep |
| Sstc | ✅ | ✅ | ✅ | ○ | CSR sweep — RV32 adds the high-half register |
| Sstvala | ○ | ○ | ○ | ○ | CSR-field guarantee; needs trap-scenario inspection |
| **Pointer masking** — Smmpm, Smnpm, Ssnpm | ○ | ○ | ○ | ○ | **No new instructions.** Re-runs existing instruction tests under a masking configuration |

### S-mode — highly desirable

| Extension | RV32 | RV64 | Under F/D configs | Under V configs | Notes |
|---|:--:|:--:|:--:|:--:|---|
| **Sv32** | ✅ | **—** | ✅ | ○ | RV32-only by model gating. **Unblocks the entire RV32 privileged path** |
| **Sv39** | **—** | ✅ | ✅ | ○ | RV64 baseline translation scheme |
| Sv48 | **—** | ○ | ○ | ○ | Depth variant on the same harness |
| Sv57 | **—** | ○ | ○ | ○ | Depth variant on the same harness |
| Svbare | ○ | ○ | ○ | ○ | No-translation mode — implicitly the state of every test today, never deliberately verified |
| Svinval | ✅ | ✅ | ✅ | ○ | Has 3 instructions; covered by the instruction sweep |
| Svade | ○ | ○ | ○ | ○ | PTE content: trap on improper A/D |
| Svadu | ○ | ○ | ○ | ○ | PTE content: hardware A/D update |
| Svnapot | **—** | ○ | ○ | ○ | PTE content: NAPOT contiguity. RV64-only by model gating |
| Svpbmt | **—** | ○ | ○ | ○ | PTE content: page-based memory types. RV64-only by model gating |
| Svrsw60t59b | ○ | ○ | ○ | ○ | PTE content: reserved-for-software bits |
| Svvptc | ○ | ○ | ○ | ○ | Obviating re-fence after PTE update |
| Ssccptr | ○ | ○ | ○ | ○ | Hardware PTE-read guarantee; a memory property, not an opcode set |

### Reconciliation and what the columns mean

| | Count |
|---|---:|
| Committed this RFQ (✅ at one or both XLENs) | **14** |
| Future within this RFQ (○ only) | **14** |
| **Total privileged extensions** | **28** |

**On the F/D column.** Privileged behaviour is largely orthogonal to floating point, but not
entirely: floating-point state (`mstatus.FS`) affects illegal-instruction trapping, so privileged
scenarios are exercised under configurations that enable F/D as part of the configuration matrix
(P10) rather than as separate work.

**On the V column.** Every entry is ○ because vector is deferred for this iteration. Privileged
scenarios are not blocked by vector, and the configuration matrix will record per-stage status for
vector-enabled configurations, but we do not commit to privileged × vector combinations here.

**On pointer masking.** It requires no new instructions — it changes how existing load, store and
jump instructions compute addresses under a CSR-configured mask. Testing it means re-running
existing instruction tests under a masking configuration, which is a configuration-driven variant
of work already in scope rather than a new generation capability.

## 7.4 Hypervisor — **OUT OF SCOPE** for this RFQ

**Explicitly out of scope for this iteration.** The RFQ permits this. If later required, an
orphaned upstream draft implementation exists and must be evaluated before any from-scratch
proposal.

---

# 8. Validation Strategy

**Principle: a test that cannot fail is not a test, and the framework must make shipping one
structurally impossible.**

## 8.1 Definition — a test is *validated* only when all six hold

1. It assembles and links with configuration-derived flags.
2. It produces a well-formed ELF for the selected XLEN.
3. It carries a **non-empty expected-state table**.
4. It **fails when its expected values are mutated** — demonstrated sensitivity.
5. It executes to a definite verdict on the Golden Model **and** at least one independent simulator.
6. Its verdict is classified, not merely recorded.

Anything satisfying fewer than six is reported as *unvalidated* and **excluded from pass-rate
claims**.

## 8.2 Anti-vacuity mechanisms

| Mechanism | Purpose |
|---|---|
| Empty-expectation rejection at emission | A test with nothing to compare never enters a suite |
| Mutation sampling across the corpus | Proof that expectations are load-bearing |
| Mandatory negative controls per scenario family | A control that stops failing is a regression |
| Suite-level invariant | The suite fails if validated-test count diverges from executed-test count |

This is an emission-time gate plus a suite-level invariant — **not a review practice**. It exists
because a prior corpus passed at scale while comparing empty expected-state tables, undetected
precisely because the suite was green **[V]**.

## 8.3 Result handling

- **Independent execution is mandatory.** Expectations sourced from the Golden Model are circular
  with respect to it; a run without an independent simulator reports **inconclusive**, never pass.
- **Failure classification:** model defect · framework defect · configuration mismatch ·
  nondeterminism · infrastructure. Unclassified failures are themselves a defect.
- **Timeouts** are distinguished from failures and from resource exhaustion.
- **Missing and malformed results** are explicit states, never coerced to failure or dropped.
- **Nondeterminism** is recorded rather than retried away.

## 8.4 What happens when things fail

Three failure modes a stakeholder will ask about, each with a defined policy rather than an
implicit behaviour:

**When generation fails.** The instruction or scenario is **reported with a reason** — routed to
another backend, filtered as illegal in this configuration, unreachable by any backend, or
unparsed by the model reader. It is never silently absent. Resource exhaustion reroutes; timeout
retries with a longer budget; the two are distinguished because they demand different responses.

**When validation fails.** The verdict is classified — model defect, framework defect,
configuration mismatch, nondeterminism, or infrastructure — and the classification is part of the
result record. An unclassified failure is itself treated as a defect in the framework.

**When the Golden Model and the independent simulator disagree.** This is the framework's primary
purpose, so the response is a defined protocol, not an incident:

1. **Configuration mismatch is eliminated first.** The single-derivation architecture (§6) exists
   so that both simulators are provably configured identically. A disagreement is only meaningful
   once that is established.
2. **The disagreement is classified**, not adjudicated by the framework. The framework reports
   *that* two implementations differ and on what; it does not assert which is correct.
3. **A reproducer is produced** — a minimal test, its configuration, and both observed results.
4. **Attribution follows evidence.** A finding is attributed to the model only when a reproducer
   demonstrates it. Prior experience on this project produced a wrong attribution of a set of
   failures to a model defect that turned out to be a framework fault, so the rule is explicit:
   **a finding earns only the failures its reproducer actually covers.**
5. **Independent simulators can also be wrong.** A simulator not configured for an extension is
   indistinguishable, from outside, from the model being incorrect. That case is classified as
   configuration mismatch, not as a model defect.

## 8.5 Pass/fail mechanism abstraction (Consideration G)

The RFQ permits self-checking, trace-based, or other schemes. A **validation mechanism** is
therefore an interface: given a test and an execution result, produce a classified verdict.
**Self-checking with differential execution is the mechanism implemented in this project**, chosen
because it requires no trace infrastructure and yields an independent result directly. Trace-based
or alternative schemes can be added against the same interface.

**The anti-vacuity guarantee is a property of the framework, not of the mechanism.** Any future
mechanism must satisfy the same invariant to be accepted.

---

# 9. Coverage Strategy

## 9.1 Coverage unit and definition

A **span in the Golden Model's own source**, taken from the model's generated span manifest.
Coverage points comprise function, branch and expression spans. This is **source coverage of the
model** — the metric the RFQ expects, and the only one permitting comparison between generated and
hand-written suites.

## 9.2 Required capabilities

| Capability | Method |
|---|---|
| **Per-ELF coverage** | Each ELF replayed in isolation with the span record cleared beforehand, so attribution means *this test*, not *replay order*. Reported as absolute coverage and marginal contribution |
| **Suite coverage** | Union across the suite with a **reconciliation invariant**: union of per-ELF span sets must equal the suite total, asserted on every run |
| **Configuration attribution** | Spans tagged with the configuration hash. Without this, cross-configuration comparison is invalid |
| **Extension attribution** | Spans mapped to owning extension, enabling per-extension privileged coverage |
| **Model-revision attribution** | Every figure qualified by the model revision that produced it — the span manifest changes as the model evolves |
| **Exclusions** | Written scope policy with per-exclusion justification |
| **Collection/reporting separation** | Reporting decoupled from collection so an alternative coverage source can be added later. No alternative source implemented now |

## 9.3 The failing-test problem

The model's coverage record is written only on clean exit, so failing tests contribute nothing
**[V]**. Because negative controls **must** fail, the tests proving the suite is non-vacuous are
structurally invisible to coverage.

**Resolution:** coverage accounting separates the *validated corpus* from the *control corpus*.
Every coverage figure is published with its scope and with the statement that it reflects passing
tests only. Controls are accounted for in the validation report.

## 9.4 What coverage means, and what it does not

**This is the most misreadable number the project produces, so the disclaimer is normative, not
editorial.**

A coverage figure from this framework means: *the proportion of instrumented spans in the Sail
Golden Model's source that were executed by the measured tests, under a stated configuration, at a
stated model revision, within a stated exclusion scope.*

**It does not mean any of the following, and no report may imply them:**

| A figure of "80%" does **not** mean | Because |
|---|---|
| 80% of the RISC-V ISA is covered | The metric counts source spans in one implementation of the ISA, not architectural features |
| 80% of the privileged architecture is verified | Executing a line is not verifying its behaviour; verification requires the expectation to be checked and able to fail (§8) |
| 80% of possible behaviours were tested | Spans are not behaviours; one span may hide many input-dependent outcomes |
| The remaining 20% is untested work in progress | Some spans are unreachable in the selected configuration, out of scope (Hypervisor), or platform description code |
| The figure is comparable to another suite's figure | Comparison requires the same model revision, configuration and exclusion scope |

**Every published coverage figure must carry its configuration, model revision and exclusion
scope.** A figure without them is not producible by the tooling — this is enforced, not requested
(§9.2).

Total coverage is neither achievable nor meaningful. **The objective is measurable improvement in
privileged coverage with stated scope, not a percentage target.**

## 9.5 How privileged coverage progress is reported (Evaluation Criterion 1)

The RFQ evaluates coverage of the privileged ISA extensions *as implemented by the model*. Because
a single percentage is misleading (§9.4), progress is reported as a structure rather than a number:

1. **A measured starting baseline**, recorded at a stated model revision and configuration, before
   privileged capability work begins.
2. **Per-extension privileged coverage**, so a gap is attributable to a specific privileged
   extension rather than diluted into a whole-model figure.
3. **Marginal contribution per capability delivered**, so the value of each privileged scenario
   family is visible.
4. **Trend across model revisions**, so evolution-driven change is distinguishable from progress.
5. **An explicit uncovered list** — what remains unreached, and for each, whether it is reachable
   by a planned scenario, unreachable in the selected configuration, or out of scope.

**No target percentage is committed**, because a percentage of a source-span metric is not a
meaningful commitment and would invite exactly the misreading §9.4 forbids. The commitment is to
**measured, attributed and reproducible improvement, with the residual honestly enumerated.**

## 9.6 The reachable denominator

**Principle: a coverage figure is meaningless without a stated denominator, and the denominator is
different for every configuration.** §9.4 establishes what a coverage figure does not mean. This
section establishes what it is a proportion *of*, and makes that quantity a computed artefact rather
than an assumption.

**This step runs before generation, not after measurement.** Its output is an input to the
generator, not a footnote on the report.

### 9.6.1 Enumerable target sets

The model exposes several target sets that are finite, machine-readable and countable. Each is a
candidate denominator. Counts below are measured at model revision `44fc6ccb`:

| Target set | Count | Derivation |
|---|---:|---|
| Extensions declared | 125 | `enum clause extension` in `model/core/extensions.sail` **[V]** |
| — privileged | 31 | 3 privilege modes (`S`, `U`, `H`) + 4 `Sm*` + 12 `Ss*` + 12 `Sv*` **[V]** |
| — unprivileged | 94 | remainder **[V]** |
| Instruction declarations | 355 | `union clause instruction` **[V]** |
| Encoding clauses | 397 | `mapping clause encdec` **[V]** |
| Instruction forms resolved | 1,280 | model parser output **[V]** |
| Source spans | 15,157 | model span manifest **[V]** |
| CSRs | 344 | `mapping clause csr_name_map` **[V]** |
| Exception causes | 21 | `E_*` constructors **[V]** |
| Interrupt causes | 11 | `I_*` constructors **[V]** |
| Operand kinds | 8 | `reg freg vreg cfreg imm csr mem lit` **[V]** |

**Note the asymmetry that shapes §7.** The entire privileged specification contributes **9
instructions** to the model — `ECALL`, `EBREAK`, `MRET`, `SRET`, `WFI`, `SFENCE.VMA` in
`extensions/I/base_insts.sail`, and three `Svinval` fences **[V]**. Privileged behaviour is reached
by establishing machine state, not by selecting opcodes. A generator organised around instructions
will emit nine tests and report the privileged specification complete.

### 9.6.2 Configuration-gated exclusion

A substantial part of the model cannot execute in any given configuration, by construction:

| Guard | Sites | Unreachable in |
|---|---:|---|
| `xlen == 64` | 115 | every RV32 configuration **[V]** |
| `xlen == 32` | 129 | every RV64 configuration **[V]** |
| `Ext_Sv48` / `Ext_Sv57` | 13 | configurations not enabling them **[V]** |

**Consequence, stated normatively: a goal of "100% coverage per configuration" fails on every run
by construction, and any plan committing to it is committing to a defect.** The denominator must be
computed per configuration before it can be a denominator at all.

### 9.6.3 The exclusion register

The output of this stage is a machine-readable register, one entry per excluded target, each
carrying its reason from a closed vocabulary:

| Reason | Meaning |
|---|---|
| `config-gated` | Guarded on a configuration predicate false in this configuration |
| `extension-absent` | Belongs to an extension not enabled here |
| `not-implemented` | Declared but unimplemented upstream — currently `H` alone (§7.4) |
| `platform-description` | Platform or device description code, outside the ISA |
| `out-of-scope` | Excluded by written scope policy, with justification |

This register discharges the §9.2 *Exclusions* capability and supplies the §9.5 *explicit uncovered
list*. It is reviewable line by line: a reader who disagrees with an exclusion can contest that
entry specifically rather than the aggregate figure.

**Every published coverage figure is a proportion of the reachable denominator, and cites the
exclusion register revision that produced it.**

## 9.7 Completable coverage — the enumerated matrices

§9.4 and §9.5 correctly refuse a span-coverage percentage target. That refusal creates an obligation:
if the project commits to no completable number, a reviewer cannot tell finished work from
abandoned work. This section supplies the completable numbers.

### 9.7.1 Why span coverage cannot carry this alone

Span coverage is coverage of *one implementation* of the ISA, and two classes of architectural
corner case are invisible to it.

**Corner cases that are not branches.** Base integer addition in the model is:

```sail
function clause execute RTYPE(rs2, rs1, rd, op) = {
  X(rd) = match op {
    ADD  => X(rs1) + X(rs2),
```

Zero branches **[V]**. A test computing `1 + 1` achieves complete span coverage of RISC-V integer
addition, leaving signed overflow, the `x0` destination case, and the RV64 32-bit sign-extension
boundary untested. By contrast `DIV` encodes both of its corner cases — division by zero and signed
overflow — as explicit conditionals **[V]**, so span coverage does detect them.

**Whether a corner case is visible therefore depends on how the model author wrote that line.** The
metric cannot distinguish the two situations, and silently rewards arithmetic.

**Behaviour the model omits.** Where the model is missing a case the architecture requires, no span
exists to cover, and complete span coverage is reported on a defective model. This is not
hypothetical: it is the shape of a model defect this framework has already found and raised upstream
(§18). Only differential execution against an independent simulator detects this class (§8.3).

### 9.7.2 The matrices

Each matrix below is finite, enumerable from §9.6.1, and **completable**. These are the quantities
against which the project commits to 100%.

| Matrix | Cells | Construction |
|---|---:|---|
| **Trap matrix** | ~126 | 21 exception causes × delegated / not delegated × originating privilege **[I]** from **[V]** counts. Each cell requires a test producing exactly that cause with the correct `xepc`, `xtval` and resulting privilege |
| **CSR access matrix** | 344 × access form × privilege | Each CSR under read, write, set and clear, from each privilege level. Illegal combinations are themselves tests: the access must trap correctly |
| **Translation matrix** | mode × page size × permission × A/D × PMP | Bare, Sv32, Sv39, Sv48, Sv57 against page size, permission bits, accessed/dirty state, and PMP interaction during the walk |
| **Interrupt matrix** | 11 causes × delegation × enable state | Delivery, delegation, and masking for each interrupt cause |
| **Operand boundary partition** | ~51,000 **[I]** | See §9.7.3 |

Cell counts are upper bounds before exclusion; the reachable count per configuration comes from
§9.6.

### 9.7.3 The operand boundary partition

Exhaustive operand coverage is not available. Two 64-bit operands give 2^128 pairs for a single
instruction **[I]** — beyond enumeration by any margin that matters, before machine state is
considered at all.

The standard resolution is equivalence-class partitioning: assert that behaviour is uniform within a
class, then cover every class. A defensible partition per integer operand is `0`, `1`, `-1`, `2`,
`INT_MAX`, `INT_MIN`, `INT_MAX-1`, `INT_MIN+1`, all-ones, and the 32-bit sign-extension boundaries —
twelve classes, giving 144 pairs for a two-operand instruction and roughly 51,000 across 355
instruction declarations **[I]**.

**The partition is published as part of the deliverable.** Its correctness is a matter for technical
argument, and a reviewer must be able to contest it directly. A partition that cannot be inspected
is not evidence.

### 9.7.4 Mutation as the audit

Every quantity above measures what the suite *reached*. Only mutation measures whether reaching it
detected anything: a fault is injected into the model, the suite is run, and the suite must fail.

**Mutation is therefore the audit on §9.6 and §9.7.** Full matrices combined with a poor mutation
score demonstrates that the partitions were wrong, not that the work is complete. The framework has
already measured the failure mode this guards against — a substantial fraction of an earlier corpus
executed spans, raised the coverage figure, and could not have failed (§8.2).

### 9.7.5 What is and is not committed

| Committed | Refused |
|---|---|
| 100% of the reachable denominator (§9.6), each exclusion individually justified | Any span-coverage percentage target (§9.4, §9.5) |
| 100% of the enumerated matrices (§9.7.2), with unreachable cells registered | Any claim of complete ISA coverage |
| A published operand partition | A claim that the partition is exhaustive |
| A mutation score, measured and reported | A mutation score target before a baseline exists |

**Complete ISA coverage is not claimed and is not achievable by any method.** "ISA conditions" is
not an enumerable set — the architecture specification is prose — so no percentage of it can be
computed. Every number this project publishes is a proportion of a denominator it states.

## 9.8 Coverage-directed regeneration

The uncovered list from §9.5 is a work queue. How it is consumed determines whether coverage
measurement is a report or a control loop.

**Routing by cause is in the core deliverable. Speculative path enumeration is not.** These were
previously conflated under "coverage-guided generation" and deprioritised together; measurement
separates them.

Each uncovered target is classified before regeneration:

| Cause | Route | Basis |
|---|---|---|
| **Requires machine state** | Preamble builder (§7) | Expected to dominate. The preamble work is contractual regardless, so routing here consumes no additional scope |
| **Requires operand values** | Boundary partition (§9.7.3) | Reaches the class of corner case span coverage cannot see |
| **Requires path enumeration** | `isla --all-paths-for` | Real but narrow; applies where one span has several feasible paths |
| **Unreachable in this configuration** | Exclusion register (§9.6.3) | Recorded with justification, not pursued |

**Evidence for the split.** Measured against a hand-written baseline at I+M RV32, the framework was
short by 194 spans. Path enumeration was predicted to close the gap and recovered 5; re-running an
existing generator with corrected machine state recovered 104 **[V]**. The bottleneck was machine
state the generator never established, not solver capability.

**Consequence for the design.** A single "generate tests for uncovered spans" feedback path encodes
the assumption that uncovered spans are a solver problem. That assumption was tested and did not
hold, which is why the classification above exists and why the dominant route is the preamble
builder rather than the solver.

---

# 10. Test Suite and Result Architecture

## 10.1 Suite layout

```
suite-<config-hash>/
  suite.json                 provenance + configuration + index
  <extension>/
    tests.json               per-directory manifest
    <test>.elf
    <test>.s                 with embedded config header
```

Naming is **deterministic**, derived from extension, scenario and configuration, so identical
inputs always yield identical names.

## 10.2 Test metadata

Carried both in an in-source header (for source-level runners) and in the manifest (for tooling
consuming ELFs): required extensions · configuration parameters · architecture string · XLEN ·
scenario family · seed · model revision · toolchain revision · simulator information · and for
controls, an explicit marker that the test **must fail**.

A runner that silently discards controls keeps the tests and destroys the evidence that they can
fail. The marker prevents that.

## 10.3 Unified result record

Minimum fields: test identifier · configuration · extension · simulator · verdict · failure class
and reason · runtime · coverage reference · provenance reference.

## 10.4 Summary

Generated from the records, never hand-maintained: totals by verdict, breakdown by extension and
configuration, failure counts by class, controls confirmed failing, validated-vs-executed
reconciliation, and coverage totals with scope.

---

# 11. Reproducibility Strategy

**Requirement: a second developer regenerates a byte-identical suite from recorded provenance
alone.**

Captured per suite: framework repository revision · Golden Model revision and configuration hash ·
symbolic engine revision · IR build identity · toolchain versions · simulator versions · random
seeds · dependency versions · relevant environment.

## 11.1 The generated-IR problem

The symbolic engine consumes a large compiled artefact derived from the model. Committing it as a
binary creates three problems: it diverges silently from its source **[V]**, it is a derived work
of the model with attribution implications, and it is incomplete across configurations.

**Resolution: the artefact is built, not committed.** The build is scripted, versioned, and its
inputs recorded in provenance. Reproducibility is established by *rebuilding to the same identity*.

## 11.2 Model evolution (Consideration E)

- Every suite and coverage figure is **qualified by model revision**.
- Regeneration against a new revision is a **supported, scripted operation**.
- Differences arising from regeneration are classified as **expected model evolution** or
  **regression**, using the same taxonomy as failure classification.
- Coverage totals are tracked across revisions so direction of travel is visible.
- **CI behaviour on model change is defined explicitly**: what triggers regeneration, what
  constitutes failure, and what constitutes an expected difference requiring baseline update.

---

# 12. RISC-V Architectural Certification Test Compatibility (Consideration A)

The RFQ prefers generated ELFs to be somewhat compatible with other RISC-V test suites,
particularly the Architectural Certification Tests, and asks whether their implementation-specific
macros — boot code, test termination, interrupt delivery — can be reused.

**Reuse is not mandated. It is decided by evaluation.** The process:

1. **Identify** the implementation-dependent operations generated tests actually require.
2. **Determine technical compatibility** of the existing macros against each operation.
3. **Determine whether reuse improves portability** for that operation specifically.
4. **Reuse where appropriate.**
5. **Document deviations** where reuse is not appropriate, with the technical reason.

**Architectural consequence:** an **implementation-dependent operations layer** so the emitter
depends on the *operation*, not on a particular convention for realising it. This makes generated
tests portable across implementations and allows the reuse decision to change later without
rewriting the emitter.

**Contribution assessment.** Where the evaluation identifies something worth contributing back,
that is recorded and owned under section 19 — not assumed.

Owner: **P4**. Acceptance: a recorded adopt-or-decline decision with technical rationale **per
operation**.

---

# 13. Readability and Generated-Test Quality (Consideration B)

Generated assembly is difficult to read. The RFQ asks for consistent formatting, with comments and
provenance encouraged but not mandatory. These are maintainability requirements, not cosmetics: a
test nobody can read is a test nobody can debug when it fails.

**Requirements:**

- **One consistent format across all backends.** Formatting is a property of the emission layer,
  not of each backend.
- **Consistent naming** derived deterministically from extension, scenario and configuration.
- **Readable structure**: preamble, body and verdict epilogue visually distinguishable.
- **Optional provenance annotation**, including a pointer to the originating Sail source location
  where the generator can determine it.
- **Annotation must be suppressible and must never affect the encoded test.**

Owner: **P4**. Acceptance: formatting consistency verified across backends; enabling or disabling
annotation leaves the encoded test **byte-identical**.

---

# 14. Extensibility and Future Scope (Considerations C and D)

Future iterations may target the unprivileged ISA, floating point, vector and the Hypervisor
extension. **None is implemented now.** The architecture must make them possible without redesign.

## 14.1 Separation principle (Consideration C)

**ISA-independent generation infrastructure** — instruction sourcing, encoding, emission,
execution, validation, coverage — is separated from **privileged-specific scenario definitions**.
Privileged behaviour is expressed as scenario *data* consumed by the core, not as logic embedded
in it.

This is what makes later unprivileged-ISA generation a matter of adding inputs rather than
restructuring the framework. **No privileged assumption is hard-coded into the core.**

## 14.2 Extension points (Consideration D)

| Extension point | What can be added later | Why it is an interface now |
|---|---|---|
| Instruction source | new ISA extensions | instruction set derived from the model, so a new extension appears without framework change |
| Generation backend | new generation strategies | backends selected by a declarative routing table |
| Scenario definition | new privileged scenarios; Hypervisor scenarios | scenarios are declarative state descriptors, not fixed encodings |
| Machine-state descriptor | floating-point state, vector state, Hypervisor state | state requirements are configuration-derived fields |
| Implementation-dependent operations | alternative boot / termination / interrupt conventions | isolates portability from generation |
| Execution backend | additional simulators | required anyway by Deliverable 3 |
| Validation mechanism | trace-based or other pass/fail schemes | RFQ permits schemes other than self-checking |
| Coverage source | alternative coverage mechanisms | reporting decoupled from collection |

**Scope discipline:** these are interfaces, not implementations. No Hypervisor, floating-point,
vector or additional-simulator capability is built in this project beyond what the RFQ requires.

---

# 15. Documentation Strategy

## 15.1 User track

Installation from a clean checkout, **verified by execution rather than review** · quick start ·
configuration guide including which combinations are valid and why · generation · execution ·
reading results · coverage interpretation with scope caveats · troubleshooting keyed to real
failure messages.

## 15.2 Developer track

Architecture and module boundaries · methodology and its evidence base · Golden Model integration
including the IR build and its upstream implications · symbolic engine integration · SMT usage and
its boundaries · generation backends and how to add one · privileged scenarios and how to add one,
including the negative-control requirement · configuration system · validation · coverage system
and scope policy · result architecture · framework test strategy · extension mechanisms ·
maintenance and model-tracking · upstreaming process.

## 15.3 Acceptance test

**A Golden Model maintainer unfamiliar with the project can add a new backend and a new privileged
scenario using the documentation alone.** This is the RFQ's long-term-maintenance requirement
expressed as something testable rather than asserted.

---

# 16. Framework Verification Strategy

**Tests that verify the framework are distinct from tests the framework generates.** Both are
required; conflating them is how a framework with a red test suite ships green results.

| Layer | Scope | Purpose |
|---|---|---|
| **Unit** | configuration parsing and validation, architecture/ABI derivation, manifest generation, coverage parsing, result records | the logic whose duplication caused configuration defects |
| **Integration** | model parsing → encoding → generation → ELF, per backend | catches silent drops between stages |
| **End-to-end** | one scenario per family: generated, executed, validated, measured | proves the pipeline, not its parts |
| **Configuration** | smoke test per supported configuration | prevents configuration regressions from being invisible |
| **Negative** | invalid configurations rejected; vacuous tests rejected; controls confirmed failing | proves the safety mechanisms engage |
| **Reproducibility** | same inputs → identical outputs | protects the provenance claim |
| **Regression** | baseline comparison with model-fault vs framework-fault split | prevents silent erosion |

**Non-negotiable:** the framework's own test suite must be green and CI-gated before any pass-rate
or coverage figure is published externally.

---

# 17. CI Strategy

## 17.1 Framework repository CI

Runs on every change: framework unit and integration tests · fast end-to-end smoke generation and
execution · configuration smoke matrix · negative tests · reproducibility check · licence and
attribution check.

## 17.2 Golden Model CI integration

**Recommended approach: CI consumes pre-generated, version-pinned suites; it does not generate on
every run.**

Justification: generation cost varies by orders of magnitude across instruction classes, from
sub-second to minutes, with some cases exhausting memory rather than timing out **[V]**. That
variance is incompatible with a CI runtime budget. Generation runs as a separate scheduled job
producing a released, provenance-stamped artefact; CI consumes that artefact.

Required elements: configuration matrix aligned to the Golden Model's exercised set · suite
acquisition and caching · execution · validation · result reporting · coverage summary · artefact
and log retention · failure policy distinguishing model defects from framework defects · bounded
runtime budget · defined behaviour when the model changes.

## 17.3 Contractual distinction

Local workflows do not satisfy the deliverable. States: **local implementation → PR created →
reviewed → approved → merged.** Only *merged* counts, tracked separately from engineering
completion throughout.

---

# 18. Upstream Golden Model Strategy

## 18.1 Principle

**The framework must be able to consume the upstream Golden Model.** Any change it requires is to
be upstreamed, eliminated, or replaced by a mechanism needing no model modification.

## 18.2 Triage

Each candidate change is classified: **eliminable** · **required for the framework** · **required
for CI** · **required for coverage** · **optional improvement** · **independent defect fix**.

**Eliminable changes are eliminated before they are proposed.** Every change we ask maintainers to
accept is a cost against acceptance of the ones that matter.

## 18.3 Decomposition

**A single large multi-file commit will not be submitted.** It is decomposed into independently
reviewable PRs, each with one purpose, its own justification and its own acceptance criteria.
Ordering runs least-contentious first: independent defect fixes (they carry their own
justification and build reviewer confidence), then additive integration points, then any
structural change last — because structural changes are likeliest to be rejected and must not
block the others.

## 18.4 Per-PR acceptance criteria

Each PR states: the exact change · why the framework requires it · what breaks without it · why
the approach is the least invasive available · test evidence · confirmation it does not alter
model behaviour for existing users.

## 18.5 Risk position

Upstream acceptance is **not within our control**. Every required change carries a **documented
fallback** describing how the framework degrades if it is not merged. A plan with no fallback for
a merge-gated dependency is not a plan.

---

# 19. Open Source Integration Strategy

Community integration is an explicit evaluation criterion. **Invoking a project is not integrating
with it**, and the plan states relationships at the level the actual interaction supports.

| Project | Relationship | Basis |
|---|---|---|
| `riscv/sail-riscv` | **UPSTREAMING** | required framework changes and any model defects found are submitted as PRs; CI integration is a merged deliverable |
| `riscv-non-isa/riscv-arch-test` | **INTEGRATION**, contribution assessed | generated tests made portable through the implementation-dependent operations layer; contribution decided by the section 12 evaluation, not assumed |
| **ISLA** | **CONTRIBUTION assessed** | the project extends the symbolic generator; extensions of general value are assessed for upstream contribution, with a recorded decision either way |
| **Spike** | **USE** | independent execution reference; no contribution anticipated |
| Golden Model community workflows | **INTEGRATION** | the framework fits existing conventions rather than introducing parallel ones |

Each relationship carries an owner and a recorded outcome. **A relationship classified USE is
stated as USE.**

Owner: **P2**.

---

# 20. 0→100 Execution Roadmap

Twelve phases across four tracks: **Foundation** · **Capability** · **Productization** ·
**Integration**. Each phase states its risks explicitly.

---

## Phase 1 — Configuration Architecture

**Objective.** A single validated configuration object deriving every downstream artefact.

**Technical rationale.** Configuration disagreement between components produces failures
indistinguishable from model defects. Every other phase produces untrustworthy results until this
holds.

**Scope.** Configuration model, validation, identity, propagation, CLI selection, configuration
tests. **Not in scope:** matrix execution (P10).

**Technical approach.** Adopt the model's JSON as canonical. Build a loader with architectural
validation. Extract all architecture/ABI/ISA-string construction into one shared module. Thread
the configuration through generation, toolchain, simulator, metadata and coverage scope.

**Inputs.** Golden Model configuration schema; the model's exercised configuration set.

**Outputs.** Configuration object and loader; validation rules; derivation module; CLI selection
across all drivers; configuration identity; configuration test suite; **a decision on per-config
IR build vs scoped symbolic vector support**.

**Dependencies.** None. **Entry point of the programme.**

**Verification.** Unit tests on derivation and validation; negative tests proving illegal
combinations are rejected with reasons; a differential test proving Golden Model and independent
simulator receive consistent configuration.

**Acceptance criteria.**

- Given a valid Golden Model configuration, the configuration object produces identical
  architecture, ABI and ISA information for the generator, toolchain and simulator.
- Given an invalid configuration, the CLI rejects it before generation and reports the
  architectural reason.
- No component derives a configuration value independently.
- A configuration whose vector width differs from the default reaches the simulator and the
  assembler with that width.

**RFQ.** Goal 4 · **Deliverables** 1, 2, 4.

**Risks.** Touches every component, giving high blast radius while the framework test suite is not
yet trustworthy — mitigated by sequencing P3's test layers alongside.

---

## Phase 2 — Model Integration and Upstream Decomposition

**Objective.** Consume an upstream-trackable Golden Model and open the upstream path.

**Technical rationale.** Goal 1 and Deliverable 5 both depend on it; it is checkable by a reviewer
in minutes; and merge latency is outside our control, so it must start early and run long.

**Scope.** Change triage and elimination, decomposition, rebase, fallbacks, PR shepherding,
ecosystem engagement map, model-evolution policy.

**Technical approach.** Triage each change against the elimination test; rework the framework to
remove eliminable changes; decompose the remainder into single-purpose PRs; rebase onto current
upstream; establish tracking.

**Inputs.** Current model dependency set; upstream contribution guidelines.

**Outputs.** Minimal justified change set; ordered PR series; fallback documentation per required
change; ecosystem engagement map; model-evolution policy and scripted regeneration.

**Dependencies.** **Soft** on P1 — configuration rework may eliminate some changes. PR shepherding
runs **in parallel with all later phases**.

**Verification.** The framework builds and generates against a current upstream checkout plus only
the proposed changes; a regeneration against a different model revision is performed and its
differences correctly classified.

**Acceptance criteria.**

- Every model change classified with recorded rationale; eliminable changes removed.
- Each PR is independently reviewable and self-justifying.
- Every required change has a documented fallback.
- Every ecosystem relationship has a classification, owner and recorded outcome.
- **Contractually complete only when merged.**

**RFQ.** Goal 1 · Consideration E · Evaluation Criterion 3 · **Deliverable** 5.

**Risks.** **Highest-risk item in the programme.** Structural model changes may be rejected,
stranding the symbolic backend; no mitigation beyond elimination and fallbacks. Merge duration
cannot be compressed by effort.

---

## Phase 3 — Framework Productization and Self-Verification

**Objective.** Make the framework installable, testable and continuously verified.

**Technical rationale.** No capability claim is durable without continuous verification; no user
reaches any capability without an install path.

**Scope.** Dependency manifests, packaging, environment doctor, path portability, the seven test
layers, framework CI, regression baseline.

**Technical approach.** Declare dependencies per component; package with entry points; remove
developer-local assumptions; build the framework's own test suite; stand up CI.

**Inputs.** P1 configuration module.

**Outputs.** Installable framework; environment doctor; framework test suite; framework CI;
regression baseline with fault classification.

**Dependencies.** **Strict** on P1 for configuration tests; otherwise parallelizable.

**Verification.** Install and run on a machine that has never built the project; introduce a
deliberate regression and confirm CI catches it.

**Acceptance criteria.**

- A clean machine installs and runs the framework with no file editing and no undocumented
  environment variable.
- The framework test suite is green and gates every change in CI.
- A deliberately-introduced regression is caught.

**RFQ.** **Deliverable** 1 · Evaluation Criterion 2.

**Risks.** Existing framework test failures may reveal deeper defects than expected — surfaced
early by sequencing this alongside P1.

---

## Phase 4 — Generation Core and Backend Routing

**Objective.** A configuration-aware generation core with declarative backend routing, portable
implementation-dependent operations, and readable output.

**Technical rationale.** Backend capability boundaries are structural. Encoding routing as data
rather than control flow is what makes the framework extensible to future extensions.

**Scope.** Instruction extraction, legality gate, routing, fallback, three backends, ELF emission,
implementation-dependent operations evaluation and abstraction, formatting and provenance.

**Technical approach.** Derive the instruction set from the model with mandatory unparsed-clause
reporting; filter by configuration-derived legality; route via a declarative table with fallback
distinguishing resource exhaustion from timeout; emit through the operations layer with consistent
formatting.

**Inputs.** P1 configuration; P2 model integration.

**Outputs.** Generation core; routing table; operations abstraction with per-operation reuse
decisions; formatted, optionally annotated assembly; ELFs with configuration metadata.

**Dependencies.** **Strict** on P1; **strict** on P2 for the symbolic path.

**Verification.** Per-backend integration tests; a test proving configuration-illegal instructions
are not emitted; a test proving fallback engages on resource exhaustion; formatting consistency
check; byte-comparison of encoded output with annotation on and off.

**Acceptance criteria.**

- Every instruction the model defines is generated, routed with a recorded reason, or reported
  unreachable with a stated cause. **No silent drops.**
- Each implementation-dependent operation carries a recorded adopt-or-decline decision with
  technical rationale.
- All backends emit one consistent assembly format.
- Enabling or disabling provenance annotation leaves the encoded test byte-identical.

**RFQ.** Goals 2, 3, 4 · Considerations A, B · **Deliverables** 1, 2.

**Risks.** The macro-reuse evaluation may conclude reuse is inappropriate for most operations,
reducing the portability benefit — acceptable, because the decision is evidence-based and the
abstraction retains the option.

---

## Phase 5 — Validation Integrity

**Objective.** Make shipping a test that cannot fail structurally impossible.

**Technical rationale.** Every pass rate and coverage figure depends on this. Placed before
coverage deliberately: measuring an unvalidated corpus produces numbers that must later be
withdrawn.

**Scope.** Anti-vacuity mechanisms, validation-mechanism abstraction, execution-backend
abstraction, failure taxonomy, result-state handling.

**Technical approach.** Emission-time expectation gate; mutation sampling; mandatory negative
controls with regression semantics; suite-level invariant; interfaces for pass/fail mechanism and
execution backend.

**Inputs.** P4 generation core.

**Outputs.** Validated-corpus guarantee; validation and execution interfaces; failure taxonomy;
inconclusive verdict handling.

**Dependencies.** **Strict** on P4.

**Verification.** Negative tests confirming vacuous tests are rejected, mutation is detected, and a
disabled control is reported as a regression.

**Acceptance criteria.**

- A test with an empty expected-state table cannot be emitted.
- Mutating an expected value causes the affected test to fail.
- A negative control that stops failing is reported as a regression.
- A run without an independent simulator reports inconclusive, never pass.
- Every non-pass verdict carries a failure class.

**RFQ.** Goals 2, 6 · Consideration G · **Deliverables** 1, 3.

**Risks.** **Enforcement may invalidate existing pass numbers**, producing a large apparent
regression. This is the honest outcome and is expected; it is not a reason to weaken the gate.

---

## Phase 6 — M-mode Privileged Capability

**Objective.** Deliver the required M-mode capability across both XLENs.

**Technical rationale.** Privileged coverage is the RFQ's stated focus and its primary evaluation
criterion.

**Scope.** CSR capability, trap and privilege infrastructure, PMP, PMA, interrupts, trap-cause
enumeration. **Not in scope:** Hypervisor.

**Technical approach.** Declarative scenario definitions specifying required state, instruction
under test, expected outcome and mandatory controls. Preamble-constructed state by default;
solver-derived where state must be deduced.

**Inputs.** P1, P4, P5.

**Outputs.** M-mode scenario corpus at both XLENs with controls; declarative scenario format;
recorded architectural constraints.

**Dependencies.** **Strict** on P1, P4, P5. Internal ordering per section 7.1.

**Verification.** Every scenario passes on the Golden Model and an independent simulator with all
controls failing; configuration-dependent scenarios validated across configurations.

**Acceptance criteria.**

- CSR set derived from the model, both XLENs exercised, exclusions justified by name.
- Trap entry, return and privilege transitions pass on both simulators.
- A denied access faults with the specific expected cause; a wrong-cause control fails.
- Interrupt delivery asserts both cause and privilege mode entered; delegation targets
  supervisor-level interrupts.
- Every scenario family carries at least one control that fails.

**RFQ.** Goal 5 · Evaluation Criterion 1 · **Deliverable** 1.

**Risks.** The complete CSR sweep is currently **UNKNOWN — REQUIRES VERIFICATION**; its result may
reveal capability gaps not yet visible.

---

## Phase 7 — S-mode Address Translation

**Objective.** Deliver virtual memory and address translation testing.

**Technical rationale.** Highly desirable in the RFQ and the largest concentrated coverage
opportunity. **Sv32 is first because without it RV32 translation scenarios skip, and a skipped
scenario reads as success** — a reporting hazard as much as a coverage gap.

**Scope.** Parameterised translation harness, Sv32, Sv39, deeper schemes, PTE content features,
fault taxonomy.

**Technical approach.** One harness parameterised by paging scheme, driving page-table
construction, supervisor entry and fault observation. Scheme depth is a configuration input.

**Inputs.** P6 items 1–2.

**Outputs.** Parameterised harness; scheme instantiations with controls; fault taxonomy coverage.

**Dependencies.** **Strict** on P6 — supervisor entry requires privilege transitions and a trap
vector.

**Verification.** For each scheme, a fault appears only when translation is enabled *and* the
address is genuinely outside the mapping — both controls must fail.

**Acceptance criteria.**

- Translation scenarios execute at RV32 rather than skipping.
- A mapped access succeeds and an unmapped access faults with the specific expected cause, on both
  simulators.
- Both negative controls fail for every implemented scheme.
- Schemes not implemented are explicitly deferred with recorded justification.

**RFQ.** Goal 5 · **Deliverable** 1.

**Risks.** Deeper schemes may prove disproportionately costly; mitigated by explicit deferral with
justification rather than silent omission.

---

## Phase 8 — Coverage Architecture

**Objective.** Per-ELF and suite coverage with configuration, extension and model-revision
attribution.

**Technical rationale.** Two explicit RFQ goals, and the primary evidence of progress.

**Scope.** Instrumented build, per-ELF isolated measurement, aggregation, attribution, exclusion
policy, trend tracking, corpus separation. **Not in scope:** coverage-guided generation.

**Technical approach.** Instrumented model build; isolated replay per ELF with the record cleared
beforehand; suite union with reconciliation invariant; span tagging; written exclusion policy.

**Inputs.** P1, P5.

**Outputs.** Per-ELF and suite coverage; per-extension privileged view; coverage trend across
revisions; machine-readable uncovered-span report.

**Dependencies.** **Strict** on P1 for attribution and P5 — measuring an unvalidated corpus is
meaningless.

**Verification.** Reconciliation invariant asserted on every run; attribution verified by
construction; exclusions individually justified.

**Acceptance criteria.**

- Per-ELF coverage is generated after isolated replay and the suite union equals the union of
  individual span sets.
- Every coverage figure carries configuration, model revision and exclusion scope.
- A per-extension privileged coverage view is available.
- Every report states that coverage reflects passing tests only.

**RFQ.** Goals 6 · Considerations E, F · Evaluation Criterion 1 · **Deliverable** 1.

**Risks.** Attribution may reveal that headline coverage figures were configuration-blended;
correcting them may reduce reported numbers. This is a correction, not a regression.

---

## Phase 9 — Suite, Provenance and Result Architecture

**Objective.** Reproducible, self-describing suites and a unified result and summary system.

**Technical rationale.** Deliverable 3 depends on it, CI depends on it, and organisation without
persistence and provenance is not a deliverable.

**Scope.** Suite layout and naming, manifest convergence, provenance capture, IR build, result
records, summary generation, persisted artefacts.

**Technical approach.** Deterministic layout and naming; one manifest schema; provenance covering
every component version and configuration identity; result record per execution; summary derived
from records.

**Inputs.** P1, P4, P5, P8.

**Outputs.** Deterministic suites; unified manifest; full provenance; result records; generated
summary; versioned suite artefacts.

**Dependencies.** **Strict** on P1, P4, P5; **soft** on P8.

**Verification.** Regenerate a suite from provenance alone and compare byte-for-byte; summary
totals compared against underlying records.

**Acceptance criteria.**

- A suite regenerated from its recorded provenance is byte-identical to the original.
- One manifest schema is used across all generators, with controls identifiable.
- No large compiled artefact is committed.
- Every test execution produces a record with verdict, failure class, runtime and provenance
  reference; the summary is generated from those records.

**RFQ.** Goal 3 · **Deliverables** 2, 3.

**Risks.** Byte-identical regeneration may prove impossible if a toolchain introduces
nondeterminism — would be recorded as a bounded limitation with the specific source named, not
concealed.

---

## Phase 10 — Configuration Matrix Validation

**Objective.** Prove end-to-end operation across the Golden Model's exercised configuration set.

**Technical rationale.** Goal 4 is a claim about configurations working; "supported" without
per-stage evidence is not defensible under review.

**Scope.** Matrix definition, per-stage harness, execution, classification, boundary
documentation, resolution of carried-forward unknowns.

**Technical approach.** Validate each stage independently per configuration and record status per
stage, never as a single flag.

**Inputs.** P1, P4, P5, P8, P9.

**Outputs.** Executed configuration matrix with per-stage status; documented boundaries; resolved
unknowns.

**Dependencies.** **Strict** on P1, P4, P5, P9.

**Verification.** The matrix is **generated by execution**, not asserted.

**Acceptance criteria.**

- Every target configuration has a per-stage status backed by an execution record.
- Every non-working combination has a documented architectural reason.
- No combination remains **UNKNOWN** without a recorded verification attempt.
- Configurations the Golden Model itself excludes are justified rather than assumed mandatory.

**RFQ.** Goal 4 · **Deliverables** 1, 4.

**Risks.** May reveal configurations that cannot be supported — the response is documented
boundaries, not concealment.

---

## Phase 11 — Turn-Key Packaging, Examples and Documentation

**Objective.** Deliver the repository, example scripts and both documentation tracks.

**Technical rationale.** Deliverables 1, 2 and 3 are satisfied here; the developer track is how
the long-term-maintenance requirement is met.

**Scope.** Clean-environment install, the two example workflows, user and developer documentation,
troubleshooting, licence review and attributions.

**Technical approach.** Installation verified by execution, not review. Example scripts as thin,
documented wrappers over the framework's entry points — demonstrating the workflow, not hiding it.

**Inputs.** All prior phases.

**Outputs.** Verified install flow; `generate_suite` and `run_suite` examples; both documentation
tracks; troubleshooting guide; licence review with attributions and items needing legal review
identified.

**Dependencies.** **Strict** on P3, P9, P10.

**Verification.** An engineer outside the project completes the full workflow from documentation
alone; the developer-documentation acceptance test is executed.

**Acceptance criteria.**

- All turn-key steps succeed on a clean machine with no file editing.
- A single documented command generates a suite for every configuration in the committed set
  (§6.6), with provenance sufficient for byte-identical regeneration.
- A single documented command executes a suite on a named simulator and produces per-test verdicts
  and a summary.
- A maintainer unfamiliar with the project adds a backend and a privileged scenario from the
  developer documentation alone.
- Attributions complete; items requiring legal review identified.

**RFQ.** Goal 7 · **Deliverables** 1, 2, 3 · Evaluation Criteria 2, 4.

**Risks.** The developer-documentation acceptance test requires an external reviewer; scheduling
that is a dependency outside the engineering team.

---

## Phase 12 — Golden Model CI Integration

**Objective.** Achieve **merged** integration into the Golden Model CI.

**Technical rationale.** Deliverable 4 is contractually merge-gated and is the programme's terminal
dependency.

**Scope.** Suite release mechanism, CI workflow, matrix, reporting, retention, failure policy, PR
submission and shepherding.

**Technical approach.** Pre-generated, version-pinned suites consumed by CI, designed against the
model's own CI conventions and runtime constraints.

**Inputs.** P9, P10, P11; merged model changes from P2 where the workflow depends on them.

**Outputs.** Suite release mechanism; CI workflow; submitted, shepherded and merged PR.

**Dependencies.** **Strict** on P9, P10, P11. **Merge is outside our control.**

**Verification.** CI executes within budget against real configurations, producing results and
coverage; reviewer feedback addressed.

**Acceptance criteria.**

- CI consumes a pinned, provenance-stamped suite rather than generating on every run.
- The workflow completes within its stated runtime budget.
- The failure policy distinguishes model defects from framework defects.
- **The integration is merged into the Golden Model repository.**

**RFQ.** **Deliverable** 4.

**Risks.** Merge depends on third-party review latency and cannot be compressed by effort. If the
workflow depends on an unmerged model change, both are blocked together — mitigated by the
fallbacks from P2.

---

# 21. Dependency Graph

```
                    ┌──────────────────────────────┐
                    │ P1 Configuration Architecture│  ◄── entry point
                    └──────────────┬───────────────┘
                 ┌─────────────────┼─────────────────┐
                 ▼                 ▼                 ▼
     ┌───────────────────┐  ┌─────────────┐  ┌──────────────────┐
     │ P2 Model / Upstream│  │ P3 Product- │  │ P4 Generation    │
     │    (soft on P1)    │──│  ization    │  │    Core          │
     └─────────┬──────────┘  └──────┬──────┘  └────────┬─────────┘
               │  (PR shepherding    │                  ▼
               │   runs in parallel  │        ┌──────────────────┐
               │   throughout)       │        │ P5 Validation    │
               │                     │        │    Integrity     │
               │                     │        └────────┬─────────┘
               │                     │      ┌──────────┼──────────┐
               │                     │      ▼          ▼          ▼
               │                     │  ┌────────┐ ┌────────┐ ┌────────┐
               │                     │  │P6 M-mode│ │P8 Cov- │ │P9 Suite│
               │                     │  └────┬────┘ │ erage  │ │+Results│
               │                     │       ▼      └───┬────┘ └───┬────┘
               │                     │  ┌─────────┐     │          │
               │                     │  │P7 S-mode│     │          │
               │                     │  └────┬────┘     │          │
               │                     │       └──────────┴──────────┘
               │                     │                  ▼
               │                     │        ┌──────────────────┐
               │                     └───────▶│ P10 Config Matrix│
               │                              └────────┬─────────┘
               │                                       ▼
               │                              ┌──────────────────┐
               │                              │ P11 Turn-Key +   │
               │                              │     Docs         │
               │                              └────────┬─────────┘
               │                                       ▼
               └──────────────────────────────▶┌──────────────────┐
                          (merged model PRs)   │ P12 Golden Model │
                                               │     CI (MERGED)  │
                                               └──────────────────┘
```

**Strict dependencies.** P1 → P4 → P5 → {P6, P8, P9}; P6 → P7; {P9, P10, P11} → P12; P2 → P4
(symbolic path).

**Soft dependencies.** P2 → P1 (elimination may be enabled by configuration rework); P8 → P9
(coverage references in results).

**Parallelizable.** P3 alongside P2 and P4. P8 and P9 alongside P6 and P7. P2's PR shepherding
runs continuously from its start.

---

# 22. Critical Path

```
P1 → P4 → P5 → P9 → P10 → P11 → P12(submit) → P12(MERGED)
```

**Two independent chains feed the terminus:**

- **Engineering chain:** P1 → P4 → P5 → P9 → P10 → P11 → P12.
- **Merge chain:** P2 (triage → decompose → submit) → review → approval → merge, feeding
  Deliverable 5 and unblocking any model-dependent element of P12.

**The binding constraint is the merge chain, not the engineering chain.** Its duration is set by
third-party review. Consequences for execution: the upstream track starts in the first phase and
runs continuously, and every required model change carries a documented fallback.

**Highest-risk critical-path items:** P5 (may invalidate existing measurements, forcing rework in
P6–P8) · P10 (may reveal unsupportable configurations) · P12 (merge outside our control).

---

# 23. Risks and Mitigations

| # | Risk | Impact | Mitigation |
|---|---|---|---|
| 1 | Upstream rejects structural model changes | Strands the symbolic backend | Eliminate what can be eliminated; decompose so rejection is isolated; documented fallback per change |
| 2 | Merge latency for Deliverables 4 and 5 | Cannot be compressed by effort | Start upstream track in phase 1; run continuously; track states separately from engineering |
| 3 | Configuration rework has high blast radius | Regressions during the most foundational phase | Sequence P3 test layers alongside P1; guard test preventing reintroduced duplication |
| 4 | Vacuity enforcement invalidates existing numbers | Apparent large regression | Expected and disclosed; pass rates computed only over validated tests from that point |
| 5 | Symbolic engine vector-length bound | Config matrix cannot be fully served symbolically | Documented architectural boundary; oracle routing above the bound |
| 6 | Sail's triple role (source, oracle, coverage target) | One model change moves three numbers at once | Model-revision attribution on every artefact; evolution-vs-regression classification |
| 7 | Fork divergence accelerates | Rebase cost grows | Upstream-tracking check reporting divergence automatically |
| 8 | Generation cost unbounded (memory exhaustion, not timeout) | Threatens CI runtime budget | CI consumes pre-generated pinned suites; generation on a schedule |
| 9 | Configurations that cannot be supported | Goal 4 partially unmet | Per-stage matrix with documented architectural reasons rather than a single flag |
| 10 | Carried-forward unknowns resolve negatively | Capability narrower than hoped | Each scoped as an explicit verification task; result recorded either way |

---

# 24. RFQ Traceability Matrix

| RFQ Goal | Consideration | Deliverable | Evaluation Criterion | Plan Phase | Acceptance Evidence |
|---|---|---|---|---|---|
| 1 Current model | E | 5 | 3, 4 | P2 | PRs **merged**; ecosystem map recorded; regeneration exercised |
| 2 Valid ELFs | A, B | 1, 2 | 2 | P4 | ELF validity verified by execution on an independent simulator; per-operation reuse decisions recorded |
| 3 Extension organisation | A | 1, 2 | 2 | P4, P9 | Tests selectable by extension and configuration from metadata alone |
| 4 Configuration support | D | 1, 4 | 1, 2 | P1, P10 | Per-stage matrix generated by execution; single-point derivation proven |
| 5 Privileged focus | — | 1 | **1** | P6, P7 | Scenarios pass on two simulators with controls failing; per-extension privileged coverage |
| 6 Per-ELF coverage | F | 1 | 1 | P8 | Isolated replay with reconciliation invariant |
| 6 Suite coverage | E, F | 1 | 1 | P8 | Configuration-, extension- and revision-attributed |
| 7 Licensing | — | 1 | 3 | P11 | Attributions complete; legal-review items identified |
| — | B readability | 1 | 2 | P4 | One format across backends; annotation leaves encoding byte-identical |
| — | C unprivileged future | — | 2 | Architecture (§14) | ISA-independent core separated from privileged scenario data |
| — | D extensibility | — | 2 | Architecture (§14) | Interface defined per anticipated extension point |
| — | E model evolution | 1, 4 | 2 | P2, P8, P12 | Regeneration exercised; differences classified; coverage revision-qualified |
| — | G pass/fail schemes | 1, 3 | 2 | P5 | Validation mechanism selectable through interface |
| — | — | 1 docs | 2, 4 | P11 | Outside maintainer adds a backend and a scenario from docs alone |
| — | — | 2 examples | 4 | P11 | One command per configuration, provenance sufficient to regenerate |
| — | — | 3 execution | 4 | P9, P11 | Result file plus generated human summary |
| — | — | 4 CI | 3, 4 | P12 | **PR merged** |

**Evaluation Criterion 5 (cost)** is derivable from phase structure, scope, dependencies and
critical path. **Criterion 6 (delivery)** is derivable from the same structure plus the critical
path. **No costs or dates are invented, and no artificial technical tasks are created for either.**

# 25. Deliverable Acceptance Criteria

**Deliverable 1 — Repository, user and developer documentation.**
Clean machine → clone → documented install → build → configure → generate → execute → coverage,
with no file editing and no undocumented prerequisite. Framework test suite green in CI. Developer
documentation passes its acceptance test. Licence, attributions and notices complete, with items
requiring legal review identified.

**Deliverable 2 — Example generation scripts.**
A single documented command generates an extension-organised, manifested, provenance-stamped suite
from **any** valid Golden Model configuration. Provenance sufficient to regenerate byte-identically.
Verified across the configuration matrix, not on one example.

**Deliverable 3 — Simulator execution, result collection and summary.**
A single documented command executes a suite on a named simulator and produces per-test verdicts
with runtime and failure classification, a machine-readable result file, and a human-readable
summary. Must operate on an independent simulator, and must report inconclusive rather than pass
when only the Golden Model is available.

**Deliverable 4 — Golden Model CI integration.**
States: local implementation → PR created → reviewed → approved → **merged**. Only *merged*
satisfies this deliverable.

**Deliverable 5 — Golden Model modifications.**
Per change: not required / identified / implemented locally / PR created / reviewed / approved /
**merged**. Only *merged* satisfies this deliverable. Each required change carries a documented
fallback for non-acceptance.

---

# 26. Final Definition of Done

The project is complete when **all** of the following hold simultaneously. **Code completion alone
satisfies none of them.**

1. A clean environment can install, build and run the framework using documentation alone.
2. Every target configuration has a per-stage status backed by an execution record, and every
   unsupported combination has a documented architectural reason.
3. Required M-mode capability is implemented at both XLENs with negative controls and coverage
   evidence.
4. S-mode address translation is implemented for at least Sv32 and Sv39 with controls proving
   translation is genuinely active.
5. Generated suites are valid RISC-V ELF files, verified by execution on a simulator the project
   did not write.
6. Tests are organised by extension and selectable by configuration through machine-readable
   metadata.
7. Generated tests are consistently formatted and readable.
8. Suites execute end-to-end with results collected and summarised.
9. Validation is trustworthy: every shipped test provably able to fail, controls confirmed
   failing, and pass rates computed only over validated tests.
10. Per-ELF coverage is produced with correct attribution.
11. Suite-level coverage is produced with configuration, extension and model-revision attribution,
    a reconciliation invariant, and stated scope.
12. Suites are reproducible from recorded provenance.
13. User and developer documentation are complete, and the developer-documentation acceptance test
    has been executed.
14. Example generation and execution scripts work as specified across the configuration matrix.
15. Framework CI runs and gates the framework's own test suite.
16. **Golden Model CI integration is merged.**
17. **All required Golden Model changes are merged**, or eliminated with the framework working
    without them.
18. Licensing obligations are satisfied, attributions complete, and legal-review items resolved.
19. The architecture remains extensible for future scope, demonstrated objectively rather than
    asserted: **each of the eight extension points in section 14 has a documented interface, and at
    least one of them has been exercised by adding a second implementation** — for example a second
    execution backend or a second scenario family — proving the interface is real and not
    aspirational. No future capability is implemented as current scope.

**Items 16 and 17 depend on third-party review and cannot be satisfied by engineering effort
alone.** They are tracked separately throughout, and no claim of completion is made on the basis of
submission or approval.

---

# Appendix A — Internal Consistency Check

| # | Check | Result |
|---|---|---|
| 1 | Every RFQ goal addressed | ✅ §2.1, §24 — all seven owned |
| 2 | Every deliverable addressed | ✅ §2.2, §25 — all five owned |
| 3 | Every Consideration A–G addressed | ✅ A §12 · B §13 · C, D §14 · E §11.2, §17 · F §9 · G §8.4 |
| 4 | Evaluation criteria addressed where technically applicable | ✅ 1–3 via phases; 4 emerges from merged PRs and CI; **5 and 6 structural, no artificial tasks** |
| 5 | No unsupported capability claimed | ✅ All capability claims tagged **[V]**; §3.1 separates demonstrated from prototype |
| 6 | Unknowns explicitly marked | ✅ §3.3 — four items, each scoped to a verification task |
| 7 | Configuration architecture internally consistent | ✅ §6 principle → §6.3 derivation map → P1 acceptance criteria |
| 8 | Validation methodology internally consistent | ✅ §8.1 six conditions → §8.2 mechanisms → P5 acceptance criteria |
| 9 | Coverage methodology internally consistent | ✅ §9.1 unit → §9.2 capabilities → §9.3 failing-test resolution → P8 acceptance |
| 10 | Dependency graph matches roadmap | ✅ §21 edges match each phase's stated dependencies |
| 11 | Critical path matches dependency graph | ✅ §22 path traverses only strict edges in §21 |
| 12 | Future scope not in current implementation scope | ✅ §14 defines interfaces only; §9.5 defers coverage-guided generation |
| 13 | Hypervisor out of scope | ✅ §2.1, §7.3, §14 — stated three times, no phase implements it |
| 14 | Experimental work not shown as completion | ✅ §3 titled *Baseline*, explicitly "not a claim of completion"; roadmap is forward-only |
| 15 | Convertible to GitHub milestones/issues | ✅ Twelve phases with objective, scope, dependencies, verification and acceptance criteria per phase |
| 16 | Convertible to a stakeholder proposal without changing technical strategy | ✅ §1, §2, §20, §24–26 are proposal-ready; no restatement of strategy required |

---

# Appendix B — Items Explicitly Not Assumed

Carried forward as **UNKNOWN — REQUIRES VERIFICATION** and not relied upon anywhere in this plan:

- **Floating point at the 32-bit XLEN through the oracle backend** — verification task in P10.
- **QEMU and Whisper execution** — verification task in P9/P10; relevant to Deliverable 3's choice
  of independent simulator. The RFQ names these as examples; **the plan does not assume all must
  be supported.**
- **The complete model-derived CSR sweep result** — verification task in P6.
- **Per-file analysis of the model change set** — verification task in P2.
