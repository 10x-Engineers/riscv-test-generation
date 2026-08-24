# Automatic Test Generation using the Sail RISC-V Golden Model

## Proposal in response to the RISC-V International Request for Quotation

**Submitted by:** 10xEngineers

---

## A note on how this proposal is written

Every capability claim in this document is either **measured** — we ran it and recorded the result
— or explicitly marked as **not yet verified**. Where we do not know something, we say so rather
than estimate.

We have taken this approach deliberately. A verification framework whose own claims cannot be
checked is of limited value to the Golden Model community, and a proposal that overstates is
harder to hold to account than one that states its limits. Section 19 lists what we do **not**
claim.

---

# 1. Executive Summary

We propose to build a turn-key framework that automatically generates RISC-V test suites from the
Sail Golden Model, executes them, validates them against an independent simulator, measures the
coverage they achieve of the Golden Model's own source, and integrates that capability into the
Golden Model's CI.

**What RISC-V receives:**

| # | Deliverable | How you verify it |
|---|---|---|
| 1 | Repository with the framework, user documentation and developer documentation | A clean machine completes install → configure → generate → execute → coverage using the documentation alone. An engineer outside our team adds a new backend and a new privileged scenario using the developer documentation alone |
| 2 | Example scripts generating a test suite from a Golden Model configuration | One documented command produces an extension-organised, self-describing suite for every configuration in our committed set; its provenance regenerates it byte-identically |
| 3 | Example scripts executing a suite, collecting results and summarising | One documented command produces per-test verdicts, a machine-readable result file and a generated summary, using a simulator we did not write |
| 4 | Golden Model CI integration | **Merged** pull request |
| 5 | Any required Golden Model changes | **Merged** pull requests, or the change eliminated and the framework working without it |

**Our focus is the privileged architecture**, as the RFQ directs: M-mode CSR access, traps,
privilege transitions, PMP, PMA, interrupts and delegation as required capability; S-mode virtual
memory and address translation as highly desirable capability. **The Hypervisor extension is out
of scope for this iteration**, as the RFQ permits.

**We have already de-risked the hard parts by building and measuring them.** Generated ELFs run on
an independent simulator. Privileged scenarios — memory-protection violations, trap handling,
interrupt delivery and delegation, and page-table walks — pass on both the Golden Model and Spike,
each with negative controls proving the tests can actually fail. Per-ELF and suite coverage are
working against the model's own instrumentation. This is prototype evidence, not a finished
product, and Section 13 states plainly what it does and does not establish.

**Three outcomes are not within our unilateral control**, and we have planned for each rather than
assumed success: upstream acceptance of the Golden Model changes we need, upstream acceptance of
the CI integration, and the review latency of both.

---

# 2. Understanding the Requirement

The RFQ asks for automatic test generation *from* the Golden Model, with the tests organised,
executable, measurable and maintainable by the Golden Model community after the project ends.

We read four things as central:

1. **The privileged architecture is the priority**, and it is the hardest part to generate
   automatically — precisely because privileged behaviour is only reachable from specific machine
   state that a generator must construct rather than stumble upon.
2. **Coverage must be measurable per-ELF and per-suite**, expressed in terms of the Golden Model's
   own code. This is a stronger requirement than it appears: attributing coverage to *one* test
   requires isolating it, not inferring it from a batch run.
3. **The framework must outlive us.** Maintainability is an explicit evaluation criterion, and the
   deliverable is not the tests — it is the capability to keep generating them as the model
   evolves.
4. **Integration means merged, not submitted.** Deliverables 4 and 5 complete only when pull
   requests are merged upstream.

---

# 3. Technical Approach

## 3.1 Why this problem is not solved by the model alone

The Sail Golden Model is an authoritative executable definition of the architecture. It answers
*"given this machine state and this instruction, what happens?"* completely and precisely.

Automatic test construction asks the **opposite** question: *"what machine state and which
instruction would reach this behaviour, and what result should be checked?"* The model does not
answer that, because it is a definition, not a search procedure.

Three capabilities must be supplied around it:

- **Reaching interesting state.** Most of the model is privilege checks, value legalisation and
  error paths, reachable only from specific machine state — particular CSR values, protection
  entries, page tables. Something must *derive* that state.
- **Knowing the expected result.** A test must assert something, so the expected architectural
  outcome has to come from somewhere.
- **Covering what neither can reach.** Some behaviour defeats both approaches and needs a small
  set of deliberately written scenarios.

## 3.2 The methodology we propose

A **configuration-driven hybrid**, with machine-state construction as the primary lever.

| Component | What it contributes | What it cannot do |
|---|---|---|
| **Symbolic execution with an SMT solver** (Isla + Z3) | Given a target path through the model, solves for the initial machine state that reaches it — this is what makes privileged scenarios generable rather than hand-written | Cannot execute floating-point arithmetic or vector element operations; cannot represent vector registers above a fixed width — **both measured** |
| **Model-as-oracle** | Runs the model on concrete operands and captures the resulting architectural state as the expected result — the only route to floating point and vector | Cannot construct machine state by solving; its expectations come from the model, so it is circular against the model — **measured** |
| **Scenario templates** | A bounded set of deliberately written tests, each with a mandatory control that must fail | Does not scale; capped by design |

**Why a hybrid rather than one approach:** we measured the boundaries above rather than assuming
them, and they are **complementary rather than overlapping**. Combining approaches whose weaknesses
coincided would add complexity for nothing. Here, the gaps of the combined system are the
*intersection* of the individual gaps rather than their union.

The cost is honest: three generation paths are more to maintain than one. That cost buys the
capability the RFQ weights most heavily — privileged coverage — because privileged behaviour is
exactly the part that requires solver-derived machine state.

**Backend selection is data, not code.** A declarative routing table maps instruction classes to
backends, with automatic fallback. This is what allows a future extension to be added by adding an
entry rather than by restructuring the framework.

## 3.3 Architecture

```
  Golden Model configuration (the model's own JSON — adopted unchanged)
                    │
                    ▼
        ┌───────────────────────┐
        │  Configuration object │  ◄── single source of truth
        │  validated on load    │
        └───────────┬───────────┘
                    │ derives — never re-derived downstream
    ┌───────────┬───┴────┬──────────┬───────────┬──────────┐
    ▼           ▼        ▼          ▼           ▼          ▼
  Golden     generator  toolchain  simulator   ELF        coverage
  Model      legality   flags      ISA string  metadata   scope
                    │
                    ▼
        GENERATION  (state: preamble or solver · body: symbolic/oracle/template)
                    ▼
        assembly → assembler → linker → ELF + metadata
                    ▼
        suite organised by extension, with manifest and provenance
                    ▼
        EXECUTION on the Golden Model AND an independent simulator
                    ▼
        VALIDATION → COVERAGE → RESULTS → CI
```

**The single most important architectural decision** is the configuration object. When independent
components each compute the same configuration fact — the ISA string, the assembler flags, the
vector width — they can disagree, and a test then passes on the Golden Model and fails on the
independent simulator for reasons that have nothing to do with either. That failure looks exactly
like a Golden Model defect. We have seen it, which is why configuration is the foundation of our
plan rather than a detail within it.

---

# 4. Deliverables and Acceptance

We propose that each deliverable is accepted against objective evidence, not review opinion.

| Deliverable | What we deliver | Acceptance evidence | Outside our control? |
|---|---|---|---|
| **1** Repository and documentation | Framework, user documentation, developer documentation sufficient for community maintenance | Clean-machine walkthrough completes with no file editing and no undocumented prerequisite; an external engineer extends the framework from documentation alone | No |
| **2** Generation scripts | One command per configuration, producing an organised, self-describing, reproducible suite | Suite regenerated byte-identically from its recorded provenance | No |
| **3** Execution and summary scripts | One command producing per-test verdicts, machine-readable results and a summary | Executed on a simulator we did not write; reports *inconclusive* rather than *pass* if only the Golden Model was available | No |
| **4** Golden Model CI integration | CI workflow consuming pinned, provenance-stamped suites within a bounded runtime | **Merged** pull request | **Yes — review and merge** |
| **5** Golden Model changes | The minimum necessary change set, decomposed into single-purpose reviewable pull requests | **Merged**, or eliminated with the framework working without it | **Yes — review and merge** |

**On Deliverables 4 and 5:** a pull request created is not complete. Reviewed is not complete.
Approved is not complete. **Merged is complete.** We will report these states separately from
engineering progress so that our status is never ambiguous.

---

# 5. Scope

| Area | This RFQ | Architecture ready, not built | Out of scope |
|---|---|---|---|
| M-mode privileged | CSR access, traps, privilege transitions, PMP, PMA, interrupts, delegation — **required** | Deeper trap-cause and delegation combinations | — |
| S-mode privileged | Sv32 and Sv39 address translation with controls — **highly desirable, committed** | Sv48, Sv57, PTE content features, full fault taxonomy | — |
| **Hypervisor** | — | — | **Out of scope.** Not implemented, not costed, not scheduled |
| Unprivileged ISA | Only what privileged scenarios require | Systematic unprivileged generation | — |
| Floating point / vector | Only what the committed configuration set requires | Capability expansion | — |
| Coverage | Per-ELF and suite, attributed and scoped | Coverage-guided generation | — |
| Simulators | Golden Model plus one independent simulator | Further simulator backends | — |
| Pass/fail scheme | Self-checking with differential execution | Trace-based or other schemes | — |

Everything in the middle column is **excluded from implementation but not from architecture**. We
define the extension points now so that a future iteration adds capability without a redesign —
which is the RFQ's extensibility consideration met structurally rather than promised.

---

# 6. Privileged ISA Approach

*(Evaluation Criterion 1 — coverage of the privileged ISA extensions implemented by Sail)*

Our ordering is driven by technical dependency, not by feature list.

## M-mode — required

| Order | Capability | Why here |
|---|---|---|
| 1 | CSR access across the model-derived register set, both XLENs | Several privileged extensions define **no instructions at all** and are reachable only through specific CSR addresses. Nothing else in M-mode is complete without this |
| 2 | Trap entry, trap return, privilege transitions | Infrastructure: every scenario below needs a trap handler and a way to change privilege mode |
| 3 | PMP enforcement | Observable only from a lower privilege mode, so it depends on (2) |
| 4 | PMA scenarios, both XLENs | Region attributes are configuration-driven, so this depends on the configuration architecture |
| 5 | Interrupt delivery and delegation | Requires trap infrastructure; delegation additionally requires supervisor entry |
| 6 | Trap-cause enumeration | Breadth pass once the mechanisms exist |

## S-mode — highly desirable

**Sv32 first, then Sv39** — both committed. Sv32 leads because without it, RV32 translation
scenarios do not fail; they *skip*, and a skipped scenario is indistinguishable from a passing one
in a summary. We regard that as a reporting hazard as much as a coverage gap.

Sv48, Sv57 and the PTE content features are **future within this RFQ**: delivered if the schedule
permits, and otherwise explicitly deferred with a recorded justification rather than quietly
omitted.

## Which privileged extensions, under which configurations

The Golden Model declares **28 named privileged extensions** — 4 `Sm*`, 12 `Ss*` and 12 `Sv*`. With
the privilege modes `S`, `U` and `H`, the privileged portion of the model's extension enum totals 31
of 125 declared extensions. The table below enumerates the 28 named extensions and reconciles to all
28, so what we are *not* committing to is as visible as what we are.

**Key.** ✅ committed · ○ future within this RFQ (delivered if schedule permits, otherwise deferred
with recorded justification) · **—** not applicable, enforced by the model.

XLEN applicability is taken from the model itself, not from convention: it gates Sv32 on RV32, and
Sv39, Sv48, Sv57, Svnapot and Svpbmt on RV64.

| Area | Extension / capability | RV32 | RV64 | With F/D |
|---|---|:--:|:--:|:--:|
| **M-mode base** | PMP · PMA · traps · interrupts and delegation · privilege transitions | ✅ | ✅ | ✅ |
| M-mode | S, U, Zicsr, Zicntr, Zihpm, Stateen, Smcntrpmf, Sscofpmf, Sscounterenw, Ssqosid, Sstc | ✅ | ✅ | ✅ |
| M-mode | Sstvala | ○ | ○ | ○ |
| M-mode | Pointer masking — Smmpm, Smnpm, Ssnpm | ○ | ○ | ○ |
| **S-mode** | **Sv32** | ✅ | — | ✅ |
| S-mode | **Sv39** | — | ✅ | ✅ |
| S-mode | Svinval | ✅ | ✅ | ✅ |
| S-mode | Sv48, Sv57 | — | ○ | ○ |
| S-mode | Svnapot, Svpbmt | — | ○ | ○ |
| S-mode | Svade, Svadu, Svbare, Svrsw60t59b, Svvptc, Ssccptr | ○ | ○ | ○ |
| **Hypervisor** | — | ✗ | ✗ | ✗ |

**14 of the 28 are committed** for this RFQ; the remaining 14 are future-within-RFQ. Note that
several of the required M-mode capabilities — PMP, PMA, traps, interrupts, privilege transitions —
are *base privileged architecture* rather than optional extensions, so they do not appear in the
model's extension list at all. An extension count alone understates privileged coverage.

**On floating point:** privileged behaviour is largely orthogonal to it, but not entirely —
floating-point state affects illegal-instruction trapping — so privileged scenarios are exercised
under F/D-enabled configurations as part of the configuration matrix rather than as separate work.

**On vector:** deferred for this iteration. We do not commit to privileged × vector combinations,
though the configuration matrix will record per-stage status for vector-enabled configurations.

## How privileged progress is reported

We do not propose a target coverage percentage, for the reasons in Section 7. We propose instead:

- a **measured starting baseline** at a stated model revision and configuration;
- **per-extension privileged coverage**, so a gap is attributable to a specific extension;
- **marginal contribution** per capability delivered;
- **trend across model revisions**, so model evolution is distinguishable from progress;
- an **explicit list of what remains uncovered**, each item classified as reachable by a planned
  scenario, unreachable in the selected configuration, or out of scope.

---

# 7. Coverage

*(RFQ Goal 6 — coverage for an individual ELF and for a complete suite)*

**What we measure.** Executed spans in the Sail Golden Model's own source — functions, branches
and expressions — against the model's own instrumentation manifest.

**Per-ELF coverage** is produced by replaying each ELF in isolation with the coverage record
cleared beforehand, so "what this test covers" means that test and not an artefact of batch
ordering. Both absolute coverage and marginal contribution are reported.

**Suite coverage** is the union across the suite, protected by an invariant asserted on every run:
the union of the per-ELF sets must equal the suite total. If attribution is wrong, the run fails.

## What a coverage number does not mean

This is the most misreadable number the project will produce, so we state its limits as a
requirement on our own reporting rather than a footnote:

| A figure of "80%" does **not** mean | Because |
|---|---|
| 80% of the RISC-V ISA is covered | The metric counts source spans in one implementation of the architecture, not architectural features |
| 80% of the privileged architecture is verified | Executing a line is not verifying its behaviour |
| 80% of possible behaviours were tested | One span can hide many input-dependent outcomes |
| The remaining 20% is unfinished work | Some spans are unreachable in the selected configuration, or out of scope |
| The figure is comparable to another suite's figure | Comparison requires the same model revision, configuration and exclusion scope |

**Every figure we publish will carry its configuration, its model revision and its exclusion
scope.** Our tooling will not produce a figure without them.

**Coverage-guided generation** — using coverage gaps to steer the generator — is **not** part of
this proposal. The RFQ does not require it, and our own measurements did not support prioritising
it above the contractual deliverables. We define the interface so it can be added later.

---

# 8. Validation and Trust

The RFQ permits self-checking tests, trace-based validation or other schemes. We propose
**self-checking tests with differential execution**, and we design the pass/fail mechanism as an
interface so that a trace-based scheme could be added later without redesign.

**A test only counts as validated when all six of these hold:**

1. it assembles and links using configuration-derived flags;
2. it produces a well-formed ELF for the selected XLEN;
3. it carries a non-empty expected-state table;
4. **it fails when its expected values are deliberately mutated**;
5. it reaches a definite verdict on the Golden Model **and** on an independent simulator;
6. its verdict is classified.

Anything meeting fewer than six is reported as *unvalidated* and **excluded from pass-rate
claims**.

**Why we are strict about this.** A test suite that checks nothing still reports green. During our
prototype work we found a large portion of a generated corpus passing while comparing empty
expected-state tables — every test green, nothing actually checked. It was invisible precisely
*because* it was passing. The safeguards above exist so that cannot recur: an emission-time gate,
mutation sampling, mandatory negative controls, and a suite-level reconciliation between validated
and executed test counts.

**When the Golden Model and the independent simulator disagree** — which is the framework's
purpose — we follow a defined protocol: eliminate configuration mismatch first, classify the
disagreement without adjudicating which implementation is correct, produce a minimal reproducer,
and attribute a finding only to what its reproducer actually demonstrates. We apply that last rule
strictly, having previously mis-attributed a set of failures to a model defect that turned out to
be our own.

---

# 9. Configuration Support

*(RFQ Goal 4)*

We distinguish two things that are easily conflated, and we commit only to the stronger one:

- **Generation support** — the framework can produce tests for the configuration.
- **End-to-end support** — generation, compilation, ELF, Golden Model execution, independent
  simulator execution, validation **and** coverage all succeed.

**Only end-to-end support is claimed as "supported."**

| Configuration | Commitment | Basis |
|---|---|---|
| **RV32** | End-to-end committed | Generation, dual-simulator execution and coverage already demonstrated |
| **RV64** | End-to-end committed | Same |
| **F/D at RV64** | End-to-end committed | Demonstrated via the oracle backend |
| **F/D at RV32** | Target, **not yet verified** | Resolved during the project; if it fails, reported as unsupported with the failing stage named |
| **Vector-length / element-width combinations in the Golden Model's own test matrix** | Target, with a stated architectural bound | Demonstrated generating and executing at a non-default vector width. The symbolic path has a hard representational limit on vector width, so configurations above it are oracle-only by design |
| Combinations the Golden Model itself excludes from its test matrix | Not committed | Excluded upstream for runtime reasons |

**For every configuration in the target set, whether or not it ends up supported, we commit to: a
per-stage status backed by an execution record, and a documented architectural reason for every
stage that does not work.** An unsupported configuration will be reported as unsupported, not
omitted.

---

# 10. Golden Model Changes and Upstreaming

*(Deliverable 5)*

We expect this to be the first question the Golden Model maintainers ask, so we answer it directly.

**Our symbolic generation path currently cannot run against the unmodified upstream model.** The
two entry points it requires are absent upstream, and the intermediate-representation build step
requires them by name. This is verified, not assumed.

| Change | What it is | Invasiveness | Can it be upstreamed? | If rejected |
|---|---|---|---|---|
| **Symbolic entry points** | Two functions providing initialisation and step entry points for external tooling | **Low** — additive, no change to existing behaviour | Likely — additive hooks for external tools | Symbolic backend unavailable; framework runs oracle-only. Privileged scenarios move to preamble-constructed state. **Capability reduction, not project failure** |
| **CSR accessor restructuring** | Consolidation of scattered CSR accessor definitions | **High** — large, touches a widely used area | **Not yet established.** The most likely change to be rejected | CSR-driven symbolic generation unavailable; CSR coverage moves entirely to the oracle path |
| **Reset-behaviour fix** | Correction in a memory-protection register reset value | **Low** — narrow | Likely — an independent defect fix | Not required by the framework; we would offer it regardless |

**Our commitments to the maintainers:**

1. **We will try to eliminate changes before proposing them.** Every change we ask you to accept
   is a cost against acceptance of the ones that matter. Our first task is determining which are
   avoidable.
2. **We will not submit a large multi-file commit.** Changes are decomposed into single-purpose
   pull requests, each with its own justification, ordered least-contentious first so a
   contentious change cannot block the others.
3. **Each pull request will state** what breaks without it, why the approach is the least invasive
   available, its test evidence, and confirmation that it does not change behaviour for existing
   model users.
4. **Each required change has a documented fallback.** We do not assume acceptance.

**We would welcome an early technical conversation about the CSR accessor change specifically**,
before we commit engineering effort behind it. It is the one item that could materially reduce the
framework's capability, and establishing its acceptability early is cheaper for both sides than
discovering it at review time.

---

# 11. Open-Source Integration

*(Evaluation Criterion 3)*

We distinguish using a project from integrating with it, and we describe each relationship at the
level our actual interaction supports.

| Project | Relationship | What that means concretely |
|---|---|---|
| `riscv/sail-riscv` | **Upstreaming** | Required framework changes and any model defects we find are submitted as pull requests. CI integration is a merged deliverable |
| `riscv-non-isa/riscv-arch-test` | **Integration**, contribution assessed | We will evaluate — per operation — whether their macros for boot, termination and interrupt delivery can be reused, adopt them where the evaluation supports it, and document our reasoning where it does not. Whether we contribute anything back follows from that evaluation |
| **Isla** | **Contribution assessed** | Our work extends the symbolic generator. We will assess whether those extensions are of general value and record the decision either way |
| **Spike** | **Use** | Independent execution reference. We anticipate no contribution, and we say so rather than overstate |

On the architectural test suite specifically: **we do not promise blanket "compatibility."** We
promise a per-operation evaluation with a recorded decision, and an abstraction layer that keeps
generated tests portable across implementations regardless of which way each decision goes.

---

# 12. Maintainability and Extensibility

*(Evaluation Criterion 2)*

The deliverable is not a set of tests. It is the ability to keep generating them after we leave.

**Our acceptance test for maintainability is deliberately uncomfortable:** an engineer from the
Golden Model community, unfamiliar with the project, adds a new generation backend and a new
privileged scenario using the developer documentation alone. If they cannot, the documentation is
not finished.

Supporting this:

- **Eight defined extension points** — instruction source, generation backend, scenario
  definition, machine-state descriptor, implementation-dependent operations, execution backend,
  validation mechanism, coverage source. At least one will be demonstrated by a second
  implementation, so the interfaces are proven rather than aspirational.
- **Privileged behaviour expressed as data**, not logic embedded in the core, so future
  unprivileged generation is a matter of adding inputs.
- **The framework's own test suite**, gated by CI — separate from the tests it generates.
- **Full provenance** on every suite, so results remain reproducible as the model evolves.
- **A model-evolution policy**: regeneration against a new Golden Model revision is a scripted
  operation, and the resulting differences are classified as expected evolution or regression
  rather than left ambiguous.

---

# 13. Demonstrated Capability

*(Evaluation Criterion 4)*

We have built and measured prototypes of the hardest parts. **This is evidence that the approach
works, not a claim that the work is done** — everything below still requires productionisation,
independent validation, configuration coverage, documentation and CI before it becomes a
deliverable.

| Capability | What we demonstrated | What remains |
|---|---|---|
| Valid ELF generation | Generated files are valid ELF32/ELF64 executables that run on Spike, a simulator we did not write | Configuration-driven emission; portability layer |
| Privileged scenarios | Memory-protection violations, trap entry and return, privilege transitions, interrupt delivery **and delegation**, and Sv39 page-table walks — passing on the Golden Model *and* Spike, each with negative controls that fail as required | Second XLEN; Sv32; declarative scenario format; breadth |
| Per-ELF and suite coverage | Isolated per-ELF measurement with a reconciliation invariant, against the model's own instrumentation | Configuration, extension and model-revision attribution |
| Backend routing | Floating point provably fails through the symbolic path and provably succeeds through the oracle — the measurement that justifies the hybrid | Declarative routing; fallback policy |
| Model-sourced instruction extraction | Instruction set derived from the model's own assembly declarations, with anything unparsed reported rather than silently dropped | Hardening; configuration legality filtering |

**What this evidence establishes:** the methodology is sound and the hard technical risks are
understood. **What it does not establish:** that the framework is production-ready, maintainable by
others, or supported across the configuration matrix. Those are the work we are proposing.

---

# 14. Execution Plan

Twelve phases, ordered by technical dependency. No dates are proposed at this stage; the structure
below is what a schedule would be derived from.

| Phase | Objective |
|---|---|
| **1** Configuration architecture | One validated configuration object deriving every downstream artefact |
| **2** Model integration and upstreaming | Minimum change set, decomposed and submitted; ecosystem engagement; model-evolution policy |
| **3** Productization and self-verification | Installable, tested, CI-gated framework |
| **4** Generation core and routing | Configuration-aware generation, declarative routing, portable operations layer, readable output |
| **5** Validation integrity | Structural guarantee that a test that cannot fail cannot ship |
| **6** M-mode capability | Required privileged capability at both XLENs with controls |
| **7** S-mode translation | Sv32 and Sv39 with controls |
| **8** Coverage architecture | Per-ELF and suite coverage, attributed and scoped |
| **9** Suite, provenance and results | Reproducible suites; unified results and summary |
| **10** Configuration matrix validation | Per-stage evidence for every target configuration |
| **11** Turn-key packaging, examples, documentation | Deliverables 1, 2 and 3 |
| **12** Golden Model CI integration | Deliverable 4 |

**Dependencies.** Phase 1 gates Phase 4, which gates Phase 5, which gates Phases 6, 8 and 9.
Phase 6 gates Phase 7. Phases 9, 10 and 11 gate Phase 12. Phase 2 gates the symbolic path in
Phase 4 and otherwise runs in parallel throughout.

**Critical path:** Phase 1 → 4 → 5 → 9 → 10 → 11 → 12 → merged.

**The binding constraint is not engineering effort.** It is the upstream review chain for
Deliverables 4 and 5, whose duration is set by maintainer availability. We therefore begin the
upstream track in the first phase and run it continuously rather than leaving it to the end.

---

# 15. Risks and How We Manage Them

| Risk | Impact | Our management |
|---|---|---|
| **CSR restructuring rejected upstream** | High — reduces symbolic capability | Attempt elimination first; documented fallback to oracle-only CSR generation; early conversation with maintainers |
| **Upstream review latency** | High — sets programme duration | Upstream track starts in phase 1 and runs continuously; we never report submission as completion |
| **Validation enforcement reduces reported pass rates** | Medium | Expected and disclosed. If enforcing the safeguards reveals that previously-passing tests were vacuous, that is a correction we will report, not conceal |
| **F/D at RV32 proves unsupportable** | Medium | Verified during the project; reported per-stage rather than as a binary claim |
| **Coverage figure misread externally** | High — reputational | Scope enforced by tooling; the disclaimer in Section 7 applies to every figure we publish |
| **Framework complexity impedes handover** | Medium | The external-engineer documentation test is the check, and it is an acceptance criterion |
| **CI runtime exceeds the model's budget** | Medium | CI consumes pre-generated pinned suites rather than generating on every run, because generation cost varies by orders of magnitude across instruction classes |

---

# 16. Cost and Schedule Basis

*(Evaluation Criteria 5 and 6)*

**We have not invented costs or dates in this document.** The twelve phases, their dependency
relationships, their defined scope and the identified critical path are the structure from which
effort and schedule are derived, and we will provide both in the format RISC-V prefers.

Two things a stakeholder should factor into any schedule:

1. **Deliverables 4 and 5 depend on third-party review**, which no amount of engineering effort
   compresses. We recommend these be tracked against merge state rather than submission date.
2. **Phase 1 has high blast radius** — it touches every component — and is deliberately sequenced
   first, so early progress will look structural rather than feature-shaped.

---

# 17. RFQ Compliance

| RFQ Goal | How we satisfy it | Where |
|---|---|---|
| 1 · Current upstream Golden Model | Minimum change set, decomposed and upstreamed; model-evolution policy for tracking | §10 |
| 2 · Valid RISC-V ELF files | Generated ELFs verified by execution on an independent simulator | §4, §13 |
| 3 · Organised by ISA extension | Extension-based organisation plus machine-readable metadata for selective inclusion | §4, §11 |
| 4 · Golden Model configurations | Single configuration source of truth; per-stage support matrix | §9 |
| 5 · Privileged ISA focus | M-mode required capability; Sv32/Sv39 committed; Hypervisor out of scope | §6 |
| 6 · Per-ELF and suite coverage | Isolated per-ELF measurement with reconciliation invariant; attributed suite coverage | §7 |
| 7 · Compatible open-source licence | Apache-2.0 framework, compatible with the Golden Model's BSD-2-Clause licence; third-party attributions completed and any items needing legal review identified | §4 |

| RFQ Consideration | How we address it |
|---|---|
| **A** Architectural certification test compatibility | Per-operation evaluation with recorded decisions; portability abstraction either way (§11) |
| **B** Generated test readability | One consistent format across backends; optional provenance annotation that never alters the encoded test |
| **C** Future unprivileged ISA | Privileged behaviour is data, not core logic — later unprivileged generation adds inputs, not redesign (§12) |
| **D** Future extensibility | Eight defined extension points, one demonstrated by a second implementation (§12) |
| **E** Golden Model evolution | Revision-qualified suites and coverage; scripted regeneration; evolution-vs-regression classification (§12) |
| **F** Coverage methodology | Sail source coverage, per-ELF and suite, with explicit limits on interpretation (§7) |
| **G** Pass/fail methodology | Self-checking with differential execution, behind an interface permitting other schemes (§8) |

---

# 18. How You Will Know It Worked

Objective completion indicators. Each is measurable by execution or by observable upstream state.

| # | Indicator | Success condition |
|---|---|---|
| 1 | Clean-environment usability | Install through coverage completes with no file editing and no undocumented prerequisite |
| 2 | Configuration support | Every committed configuration passes all nine stages, or is reported unsupported with the failing stage and an architectural reason |
| 3 | Privileged capability | Every required M-mode capability plus Sv32 and Sv39 pass on two simulators, **with every negative control failing** |
| 4 | Validation integrity | Every sampled test fails under expectation mutation; every control fails; validated count reconciles with executed count |
| 5 | Per-ELF coverage | Union of per-ELF span sets equals the suite total, asserted on every run |
| 6 | Coverage honesty | A coverage figure without configuration, model revision and exclusion scope cannot be produced |
| 7 | Privileged coverage improvement | Measured increase over a recorded baseline, with the residual enumerated and classified |
| 8 | Reproducibility | A second machine regenerates a byte-identical suite from provenance alone |
| 9 | Framework reliability | Framework test suite green and gating every change in CI |
| 10 | Maintainability | An external engineer adds a backend and a privileged scenario from documentation alone |
| 11 | Extensibility | All eight extension points documented; at least one exercised by a second implementation |
| 12 | **Deliverable 5** | Required model changes **merged**, or eliminated with the framework working without them |
| 13 | **Deliverable 4** | CI integration **merged** |

Indicators 12 and 13 are the only two we cannot satisfy by engineering effort alone.

---

# 19. What We Do Not Claim

We think a proposal is more useful when it states its limits, so that RISC-V can hold us to
something precise.

- **We do not claim Hypervisor support.** It is out of scope for this iteration.
- **We do not claim a target coverage percentage.** A percentage of a source-span metric would
  invite exactly the misreading Section 7 warns against. We commit to measured, attributed,
  reproducible improvement with the residual honestly enumerated.
- **We do not claim support for every Golden Model configuration.** We commit to a defined set,
  and to reporting per-stage evidence — including negative evidence — for every configuration in
  the target set.
- **We do not claim floating point at RV32 works.** It is not yet verified. We will verify it and
  report the result either way.
- **We do not claim support for QEMU or Whisper.** The RFQ names them as examples; we commit to
  the Golden Model plus one independent simulator, and will verify others rather than assume them.
- **We do not claim coverage-guided generation.** It is not required by the RFQ and our own
  measurements did not justify prioritising it.
- **We do not claim our Golden Model changes will be accepted.** We commit to minimising them,
  decomposing them, justifying them individually, and providing a documented fallback for each.
- **We do not claim the existing prototype work is a delivered framework.** It is evidence that
  the approach works. The productionisation, validation, configuration coverage, documentation and
  CI are the work we are proposing.

---

# 20. Summary

We propose a framework that generates privileged-architecture tests from the Sail Golden Model,
proves they can actually fail, measures what they cover in the model's own terms, and hands the
Golden Model community something they can maintain and extend after we are gone.

The technical risks are understood because we measured them rather than estimated them. The
limits are stated rather than concealed. The acceptance criteria are objective, so RISC-V can
determine whether this was delivered without relying on our assessment of our own work.

We would welcome a technical conversation with the Golden Model maintainers about the upstream
changes described in Section 10 at the earliest opportunity.
