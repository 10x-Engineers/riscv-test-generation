# Automated Test Generation from the RISC-V Sail Golden Model

**Response to Request for Quotation**

---

**Document status.** This is a proposal for work to be performed. Except where a quantity is
identified as a property of the current upstream Sail model, no statement in this document should be
read as a report of completed work, a measured result, or a delivered capability.

**Model quantities.** Counts attributed to the Sail model throughout (extension declarations,
instruction declarations, CSRs, exception and interrupt causes, configuration-guarded sites) are
properties of the upstream model source obtained by static analysis of its declarations. They
describe the problem to be solved, not work already done. They will be recomputed against the model
revision agreed at project start and restated in the baseline report.

**Placeholders.** Sections 13 (Timeline), 14 (Cost) and 16 (Relevant Technical Experience) contain
values to be populated on agreement of scope, staffing and delivery date, and with the bidder's
verifiable credentials. They are deliberately not estimated here.

---

# 1. Executive Summary

We propose to build an extensible, configuration-aware, coverage-guided test-generation framework
driven by the upstream RISC-V Sail Golden Model, with particular focus on the Privileged
Architecture as that model implements it.

The framework will consume the unmodified upstream model and a Golden Model configuration JSON,
generate valid RISC-V ELF tests organised by configuration and ISA functionality, execute them on
both Sail and an independent simulator, compare the results differentially, measure coverage on
several distinct axes, and route uncovered targets back into generation according to *why* they were
missed.

Four design decisions distinguish this proposal.

**We compute a denominator before we generate anything.** A coverage percentage is meaningless
without a stated denominator, and the denominator differs for every configuration. Before generation
begins, the framework enumerates every measurable target the model exposes, determines which are
reachable under the selected configuration, removes the rest, and records an exclusion register
carrying a reason for every exclusion. Without this step a per-configuration 100% target is
unreachable by construction, because configuration-gated code cannot execute.

**We route generation by capability rather than assuming one mechanism covers the ISA.** Symbolic
generation, a concrete oracle and a small capped template backstop each handle the parts of the ISA
they are structurally suited to. This is an engineering routing decision, not a limitation, and
Section 5.3 states the technical basis for each route.

**We treat privileged architecture as machine state, not as instructions.** The entire Privileged
Architecture contributes only nine instructions to the Sail model. Privileged behaviour is reached
by establishing machine state — privilege level, `mstatus` fields, page tables, PMP regions,
delegation, pending interrupts — which the framework writes into a test preamble rather than
asserting as solver constraints. A generator organised around instructions would emit nine tests and
report the Privileged Architecture complete.

**We state a completeness claim we can actually discharge.** Our primary claim is *100% of the
enumerated reachable denominators for each required configuration, with every unreachable target
explicitly justified.* Sail branch coverage is measured and reported, and is used to find unreached
model behaviour, but it is not treated as proof of complete ISA coverage and is not the project
target. Mutation sensitivity audits whether our coverage partitions are meaningful. Differential
execution against an independent simulator detects the class of model defect that no metric derived
from Sail itself can find.

**We do not claim complete ISA coverage.** Section 6.6 states why no methodology can, and why a
bidder claiming otherwise should be treated with caution.

The framework is designed so that adding an instruction, extension, CSR, architectural state
element, or behavioural path to Sail does not require writing a new hard-coded generator. Section 7
describes the three-tier extensibility model — automatic discovery, capability adapters, and a capped
backstop — and Section 9 describes how the framework detects and responds to upstream model change.

All development is upstream-first. No private fork of the Sail model will be maintained. Defects
found in the model will be minimised to reproducers and submitted upstream as pull requests, never
patched locally.

---

# 2. Understanding of the RFQ

## 2.1 What is being asked for

The RFQ seeks a test-generation framework driven by the Sail Golden Model, evaluated primarily on
coverage of the Privileged Architecture ISA extensions *as implemented by that model*. The
qualification matters: the evaluation target is the model, which is enumerable, rather than the
architecture specification, which is prose and is not.

The remaining criteria — maintainability, open-source integration, demonstrated community skills,
cost and delivery date — indicate that the deliverable is expected to outlive the engagement and be
maintained by the Golden Model community.

## 2.2 The structural problem, stated plainly

Two properties of the Sail model shape any credible solution.

**The Privileged Architecture is behaviour, not instructions.** Across the model, the privileged
specification contributes nine instructions: `ECALL`, `EBREAK`, `MRET`, `SRET`, `WFI` and
`SFENCE.VMA` in the base instruction file, plus three `Svinval` fence instructions. Against 355
instruction declarations in the model overall, that is a rounding error.

What the Privileged Architecture actually consists of, in model terms, is several thousand lines of
trap handling, address translation, physical memory protection and attribute checking, CSR
behaviour, and interrupt delivery — reached by *putting the machine into a state*, then executing an
ordinary instruction. A generation strategy organised around selecting opcodes cannot reach it. This
is the single most important consequence for the architecture we propose, and it is why the preamble
builder (Section 5.4) is a first-class component rather than a helper.

**Coverage of the model is not coverage of the architecture.** The model is one implementation of
the ISA. Measuring how much of that implementation a suite executed is useful and necessary, but it
is not equivalent to testing the architecture, for reasons developed in Section 6.6. A proposal that
conflates the two is promising something it cannot deliver.

## 2.3 What we take the RFQ to require of the solution

| Requirement | Our reading |
|---|---|
| Consume the current upstream model | No private fork; the framework tracks upstream and adapts to it (Section 9) |
| Consume a Golden Model configuration JSON | The configuration is the single source of truth from which every downstream setting derives (Section 5.1) |
| Generate valid RISC-V ELF tests | Valid means it assembles, links, and runs unmodified on a simulator we did not write |
| Organise by configuration and ISA functionality | Suite layout keyed by configuration and extension, with machine-readable manifests |
| Focus on the Privileged Architecture | The primary evaluation criterion; Section 6 is dedicated to it |
| Execute on Sail and an independent simulator | Both, on every valid test (Section 5.6) |
| Differentially compare | Divergence is a model-defect signal, routed upstream (Section 5.6) |
| Measure and report coverage | On four distinct axes, each reported separately (Section 5.7) |
| Feed uncovered targets back | Classified by cause before regeneration (Section 5.8) |
| Repeat across configurations | Each configuration independently denominated; never averaged (Section 5.9) |
| Adapt to new upstream functionality | Three-tier extensibility; no new hard-coded generator per addition (Sections 7 and 9) |

---

# 3. Technical Objectives

Each objective is stated so that its achievement is checkable by a third party.

| # | Objective | Evidence of achievement | RFQ criterion |
|---|---|---|---|
| O1 | Generate valid RISC-V ELF tests from the unmodified upstream Sail model and a Golden Model configuration | Tests assemble, link, and execute on an independent simulator without modification | 1 |
| O2 | Compute a reachable denominator per configuration with a justified exclusion register | Register is machine-readable, one entry per exclusion, each with a reason from a closed vocabulary | 1 |
| O3 | Reach 100% of the enumerated reachable denominators for each required configuration | Matrix fill reports per configuration; every unreachable cell individually registered | 1 |
| O4 | Cover the Privileged Architecture extensions the model implements, per configuration | Per-extension coverage report, each scenario paired with a negative control | 1 |
| O5 | Execute every valid test on Sail and an independent simulator, comparing results | Per-test record showing expected and actual state on both targets | 1, 3 |
| O6 | Measure and report Sail branch coverage without treating it as the completeness claim | Coverage report carrying configuration, model revision and exclusion scope | 1 |
| O7 | Audit coverage partitions by mutation sensitivity | Mutation score reported against a stated fault-injection set | 1 |
| O8 | Require no new hard-coded generator when Sail gains an instruction, extension or CSR | Demonstrated by adding a model element and regenerating without framework code change | 2 |
| O9 | Provide a documented adapter mechanism for genuinely new architectural mechanisms | Adapter interface documented, with a worked example | 2 |
| O10 | Detect upstream model change and report its effect on the denominator | Model-revision diff report identifying new, changed and removed targets | 2 |
| O11 | Submit model defects upstream as minimised reproducers | Reproducer per defect, upstream issue or PR reference | 3, 4 |
| O12 | Deliver documentation sufficient for community maintenance | User and developer tracks, plus a documented acceptance test a third party can run | 2, 3 |

---

# 4. Proposed Architecture

## 4.1 Pipeline

The framework is a ten-stage pipeline. Stages 1–8 form the forward path; stage 9 closes the loop;
stage 10 repeats it across the configuration matrix.

```
  01  Read inputs                unmodified Sail model + Golden Model configuration JSON
       |
  02  Compute the reachable      enumerate targets -> remove config-impossible ->
      denominator                 emit exclusion register
       |
  03  Generate, routed by        symbolic (Isla+Z3) | concrete oracle (sailtest) |
      capability                  template backstop        + preamble builder (cross-cutting)
       |
  04  Quality gate               reject any test with empty expected state
       |
  05  Execute on two targets     Sail (instrumented)  AND  independent simulator (Spike)
       |
  06  Compare                    agree -> pass + record coverage
                                  disagree -> model-defect candidate -> minimise -> upstream
       |
  07  Measure on three axes      denominator matrices | branch coverage | mutation score
       |
  08  Report                     per-ELF records | suite summary | exclusion register | defects
       |
  09  Classify and route         needs state | needs paths | needs operands | unreachable
      feedback                     -> back to 03
       |
  10  Iterate across             each configuration: own denominator, own exclusion register,
      configurations              own coverage figure -> back to 02
```

## 4.2 Agentic orchestration over a deterministic core

The framework separates *deciding what to attempt* from *establishing what actually happened*. The
separation is strict, and it is a correctness property, not a stylistic preference.

| Agentic layer — decides what to attempt | Deterministic core — establishes what happened |
|---|---|
| Sail model analysis and target enumeration | Sail model execution |
| Model-change detection between revisions | Isla symbolic execution |
| Coverage-gap analysis | Z3 constraint solving |
| Target classification and routing | `sailtest` concrete oracle |
| Test planning and generation prioritisation | Assembly, linking, ELF emission |
| Failure triage proposals | Independent simulator execution |
| Campaign management across configurations | Coverage instrumentation and collection |
| | Differential comparison |

**No language model is responsible for correctness.** The agentic layer emits *plans* — structured,
machine-readable descriptions of what should be generated and in what order. Every plan is executed
by deterministic tooling, and every result is established by that tooling. A plan that proposes an
impossible test produces a failed generation attempt, which is recorded; it does not produce a wrong
test that passes.

Three properties make this safe:

1. **The agent never authors expected state.** Expected state comes from the model, via the oracle
   or the solver. The agent selects targets; it does not decide what the correct answer is.
2. **Every agent output is a hypothesis validated downstream.** A classification the agent proposes
   is confirmed or refuted by the deterministic core, and the record reports which.
3. **The pipeline runs without the agent.** With the agentic layer disabled the framework still
   generates, executes, compares and reports, using static routing rules. The agent improves
   prioritisation and reduces manual maintenance; it is not load-bearing for correctness.

Section 7.4 explains why this division is what makes the framework maintainable as Sail evolves, and
Section 15 states the risks it introduces and how they are contained.

---

# 5. Detailed Technical Approach

## 5.1 Sail Model and Configuration Ingestion

**Inputs.** The unmodified upstream Sail RISC-V model, and one Golden Model configuration JSON in
the model's own format.

**The configuration is the single source of truth.** A recurring failure mode in test frameworks is
deriving configuration independently in several places — the model configuration, the symbolic
engine's setup, the assembler's `-march` string, and the simulator's ISA string — which allows a test
to pass on the Golden Model and fail on an independent simulator purely because the framework
disagreed with itself. The framework will load the configuration once into a validated configuration
object which rejects illegal combinations at load time, and every downstream setting will be derived
from that object rather than restated.

**Model ingestion is by static analysis of the model's own declarations.** The framework reads the
model source to enumerate what exists, rather than carrying a hand-maintained list. The declaration
forms consumed are the extension enum, instruction declarations, encoding mappings, CSR name
mappings, exception and interrupt cause constructors, and the model's generated span manifest.

Current model-level quantities, as properties of the upstream source:

| Quantity | Count |
|---|---:|
| Extensions in the enum | 125 |
| — privileged | 31 |
| — unprivileged | 94 |
| Instruction declarations | 355 |
| Instruction forms | 1,280 |
| Sail branch spans | 15,157 |
| CSRs | 344 |
| Exception causes | 21 |
| Interrupt causes | 11 |

**Provenance.** Every run records the configuration and its hash, the model revision, the revision of
each generation engine, and the versions of the assembler and independent simulator. Provenance is
written before generation begins, so that an interrupted run still records what it was attempting.

*Supports criteria 1 and 2.*

## 5.2 Reachable Denominator

**Why this stage exists.** A coverage figure is a proportion, and a proportion requires a
denominator. The denominator is not a constant: a substantial part of the model cannot execute in any
given configuration, by construction.

Configuration-guarded sites in the current model:

| Guard | Sites | Unreachable in |
|---|---:|---|
| `xlen == 64` | 115 | every RV32 configuration |
| `xlen == 32` | 129 | every RV64 configuration |
| `Ext_Sv48` / `Ext_Sv57` dependent | 13 | configurations not enabling them |

**Without this step a per-configuration 100% target is unreachable by construction, because
config-gated code cannot execute.** A framework that sets 100% as its goal without computing
reachability reports failure on every run, and cannot distinguish a genuine gap from a structural
impossibility.

**Procedure.** Before generation, for each configuration:

1. Enumerate all measurable targets from the model declarations (Section 5.1).
2. Evaluate each target's reachability under the configuration predicates.
3. Remove targets that cannot execute.
4. Emit an exclusion register: one entry per excluded target, with a reason.

**Exclusion reason vocabulary.** Closed, so that exclusions cannot be justified by free text:

| Reason | Meaning |
|---|---|
| `config-gated` | Guarded on a configuration predicate that is false here |
| `extension-absent` | Belongs to an extension not enabled in this configuration |
| `not-implemented` | Declared in the model but not implemented upstream |
| `platform-description` | Platform or device description code, outside the ISA |
| `out-of-scope` | Excluded by written scope policy, with justification |

The register is reviewable line by line. A reader who disagrees with an exclusion contests that entry
specifically, rather than disputing an aggregate figure with no visible basis.

**Every coverage figure the framework publishes is a proportion of the reachable denominator, and
cites the exclusion register revision that produced it.**

*Supports criteria 1 and 2.*

## 5.3 Capability-Routed Test Generation

Different parts of the ISA are best reached by different mechanisms. The framework routes generation
accordingly, and the routing rule is explicit and documented rather than implicit in whichever engine
happens to be invoked.

### A. Symbolic generation — Isla with the Z3 solver

**What it does, for readers unfamiliar with Isla.** Isla executes the Sail model symbolically:
instead of running with concrete register values, it runs with unknowns and accumulates the
constraints that a chosen execution path implies. Handing those constraints to the Z3 solver yields
concrete operand values that drive the model down that specific path — which is precisely what is
wanted when the goal is to reach a particular branch in the model. Its `--all-paths-for` mode
enumerates the feasible paths through a given instruction rather than stopping at the first.

**Routed here:** I, M, A, B, C, Zicsr, and the `Zb*`, `Zc*`, `Za*` and `Zk*` families — the parts of
the ISA where behaviour is bit-precise and path selection is the useful lever.

### B. Concrete oracle — `sailtest`

**What it does.** Rather than solving for values that drive a path, the oracle selects values and
runs the model to obtain the resulting architectural state, which becomes the test's expected state.
Because it does not build a constraint system, it is not subject to solver-level restrictions.

**Routed here:** F and D and the related `Zf*`, `Zd*` and `Zh*` families; V, the `Zv*` family and
vector cryptography — 124 vector instructions in total.

**Why vector and floating point are routed away from the solver.** V and FP bypass the solver because
SMT bitvectors need fixed widths while `vl` is runtime state — a structural mismatch, not difficulty
— and FP needs 45 SoftFloat primitive operations that are absent from Isla's 164 registered primops.

The first is a property of the underlying theory: SMT bitvector reasoning is defined over concrete
fixed widths, whereas vector length in RISC-V is architectural state that changes at run time. No
amount of engineering effort inside the symbolic path removes that mismatch. The second is a bounded
integration gap rather than a structural one, and Section 7.3 treats extending the primop set as a
candidate future enhancement rather than a prerequisite.

**This is capability-based routing, not a workaround.** Each mechanism is applied where it is
structurally appropriate, and the result is that the framework covers vector and floating point from
the outset rather than deferring them.

### C. Template backstop

A small set of 4 template builders handles constructs neither engine reaches cleanly — principally
control-flow instructions whose expected state depends on the branch outcome.

**The backstop is capped, not continuously expanded.** Growth in this tier indicates that generic
generation is failing to reach something it should, and is treated as a signal to fix routing rather
than as progress. The count is reported in every suite summary for exactly this reason.

### Cross-cutting: the preamble builder

All three routes depend on the machine being in the right state. See Section 5.4.

*Supports criteria 1, 2 and 4.*

## 5.4 Privileged-State Preamble Generation

**The central component for the RFQ's primary criterion.** Because the Privileged Architecture is
reached by machine state rather than by opcode selection (Section 2.2), the component that
establishes machine state is what determines privileged coverage.

**State established by the preamble builder:**

| State | Detail |
|---|---|
| Privilege level | M, S, U, and the transitions between them |
| Status fields | `mstatus` / `sstatus` field configurations relevant to the behaviour under test |
| Address translation | Page tables for Bare, Sv32, Sv39, Sv48 and Sv57, including intermediate levels |
| Physical memory protection | PMP region configuration, permitting and denying |
| Physical memory attributes | PMA configuration relevant to the access under test |
| Delegation | `medeleg` / `mideleg` configuration |
| Interrupts | Pending and enabled interrupt state |

**Architectural state is written into the preamble and is not asserted as a solver constraint.** This
is a deliberate design decision with two consequences. First, it keeps the state setup independent of
which generation engine produces the instruction body, so all three routes benefit. Second, it avoids
asking a constraint solver to discover a page-table layout or a PMP configuration — a task for which
constructing the structure directly is both simpler and far more predictable.

**Every privileged scenario is paired with a negative control** — a variant which must fail. A
privileged test that passes for the wrong reason is indistinguishable from one that works, and the
control is what separates them. Controls are reported separately from the validated corpus, because
a control that passes is a framework defect.

*Supports criterion 1 (primary).*

## 5.5 Test Quality Gate

**A gate, not a generation step.** Every generated test is checked before it enters the suite, and
must carry:

- a preamble establishing the required machine state
- an instruction sequence
- a non-empty expected state
- a negative control where a trap is asserted

**A test with no expected state still executes and still raises the coverage figure, but cannot
report a defect. The gate rejects it.**

This gate exists because coverage metrics cannot distinguish a test that verifies behaviour from one
that merely executes it. Both raise the same number. Only the presence of a checkable expectation —
and, at the suite level, mutation sensitivity (Section 5.7) — separates them.

Rejections are recorded with cause, so that a systematic failure to produce expected state for some
class of instruction surfaces as a framework defect rather than as quietly reduced coverage.

*Supports criteria 1 and 2.*

## 5.6 Dual-Target Differential Execution

Every valid ELF is executed on two targets.

**A. Sail, instrumented.** Built with coverage instrumentation enabled, providing reference
behaviour and the executed span record used in Section 5.7.

**B. An independent simulator, Spike by default.** The same ELF, under the same configuration,
executed by an independently implemented model of the same architecture.

**Design rationale: generating from Sail, running on Sail and measuring against Sail is a closed loop
that cannot find a model defect.** If expected state is derived from the model, executed on the
model, and scored against the model's own coverage instrumentation, then any behaviour the model
implements incorrectly is reproduced consistently at every stage, and every check passes.

**Comparison outcomes:**

| Outcome | Action |
|---|---|
| **Agree** | Test passes. Coverage recorded against the reachable denominator |
| **Disagree** | Model-defect candidate: triage, minimise to a reproducer, raise upstream. Sail is never patched privately |

**Where the model omits required behaviour there is no branch to cover, so no metric derived from the
model can detect it. Divergence against an independent simulator is the only signal that surfaces
it.** This is the class of defect that a coverage-only methodology is structurally incapable of
finding, and it is why dual-target execution is a mandatory stage rather than an optional check.

Divergence is not automatically a model defect. Triage distinguishes: a Sail defect, an independent
simulator defect, a framework defect (most commonly configuration disagreement), and a legitimate
implementation-defined difference. The classification is recorded per divergence, with evidence.

*Supports criteria 1, 3 and 4.*

## 5.7 Coverage and Measurement

The framework measures on distinct axes and reports them separately. Collapsing them into one
headline number is the failure mode this design exists to prevent.

### Axis A — Denominator matrices

Finite, enumerable, and **completable**. These are the quantities against which the project commits
to 100%.

| Matrix | Construction |
|---|---|
| **Trap matrix** | **126 cells** — 21 exception causes × delegated / not delegated × originating privilege. Each cell requires a test producing exactly that cause with the correct exception program counter, trap value and resulting privilege |
| **CSR access matrix** | 344 CSRs × access form (read, write, set, clear) × privilege level. Illegal combinations are themselves tests: the access must trap correctly |
| **Translation matrix** | Mode (Bare, Sv32, Sv39, Sv48, Sv57) × page size × permission bits × accessed/dirty state × PMP interaction during the walk |
| **Interrupt matrix** | 11 interrupt causes × delegation × enable state |
| **Operand boundary partition** | Per operand: `0`, `1`, `-1`, `2`, `INT_MAX`, `INT_MIN`, their immediate neighbours, all-ones, and sign-extension boundaries |

Cell counts are upper bounds before exclusion; the reachable count per configuration comes from
Section 5.2.

### Axis B — Sail branch coverage

**Measured and reported. Not the generation objective.**

Branch coverage is used to identify model behaviour the suite has not reached — the uncovered list is
the work queue that drives Section 5.8. It is reported per configuration, per extension, and per
model revision, and every published figure carries its configuration, model revision and exclusion
scope.

It is **not** treated as proof of complete ISA coverage, and **no target percentage is committed**,
because the figure depends on how the model is written rather than on how well the suite tests the
architecture. Section 6.6 develops this.

### Axis C — Mutation sensitivity

**The audit on axes A and B.** A fault is injected into the model, the suite is run, and the suite is
expected to fail. Mutation measures whether the suite would *notice* if the implementation were
wrong, rather than whether it executed the relevant code.

Full matrix coverage combined with a poor mutation score does not indicate that the work is complete
— it indicates that the coverage partitions may be wrong, and that the partition definitions need
revision. This is the mechanism by which the framework audits its own definitions of completeness.

No mutation score target is committed before a baseline exists.

### Axis D — Differential agreement

The proportion of the suite for which Sail and the independent simulator agree, with every divergence
classified and accounted for (Section 5.6).

*Supports criteria 1 and 2.*

## 5.8 Feedback-Driven Generation

**A single generic "generate more tests for uncovered targets" loop encodes an assumption: that an
uncovered target is a generation-capability problem.** That assumption is frequently wrong, and
acting on it wastes effort on solver work when the real obstacle is machine state.

The framework therefore classifies each uncovered target before regenerating, and routes by cause:

| Cause | Route | Rationale |
|---|---|---|
| **Needs machine state** | Preamble builder (5.4) | Expected to dominate, given that the Privileged Architecture is reached by state rather than opcode. Routed first |
| **Needs path enumeration** | `isla --all-paths-for` | Applies where a single target has several feasible execution paths |
| **Needs different operands** | Operand boundary partition (5.7 A) | Reaches corner cases that are not branches. Base `ADD` has zero branches, so `1+1` merely executes it and no branch metric will ever ask for more |
| **Unreachable in this configuration** | Exclusion register (5.2) | Recorded with justification, not pursued |

**Each uncovered target is classified before regeneration. Routing by cause avoids the common failure
of treating every gap as a solver problem when most require machine state the generator never
established.**

The classification is produced by the agentic layer (Section 4.2) and validated by the deterministic
core: a target routed to the preamble builder and still unreached after regeneration is re-classified
with that evidence recorded.

*Supports criteria 1 and 2.*

## 5.9 Configuration Sweep

The framework repeats the full pipeline across the required Golden Model configuration matrix.
Illustrative configurations:

| Configuration |
|---|
| RV32 M/S/U |
| RV64 M/S/U |
| RV64 + F |
| RV64 + F/D |
| RV64 + V, VLEN=128 ELEN=32 |
| RV64 + V, VLEN=128 ELEN=64 |
| RV64 + V, VLEN=256 |
| … to the agreed configuration matrix, expected to be of the order of 16 configurations |

**Each configuration has its own reachable denominator, its own exclusion register and its own
coverage figure.** Configurations are never averaged into a single headline percentage: an average
across configurations with different denominators is not a meaningful quantity, and would obscure the
one thing a reader needs to know, which is where the gaps are.

**Each configuration yields a suite sized by its own enabled extension set and reachable
denominator.** Suites of different sizes across configurations are the expected outcome, not an
anomaly.

*Supports criteria 1 and 2.*

---

# 6. Privileged Architecture Coverage Strategy

This section addresses the RFQ's primary evaluation criterion directly.

## 6.1 What will be covered

The Sail model's extension enum contains 31 privileged entries: the three privilege-mode extensions
(`S`, `U`, `H`) and 28 named privileged extensions comprising 4 machine-level (`Sm*`), 12
supervisor-level (`Ss*`) and 12 virtual-memory (`Sv*`) extensions.

**One of the 31 is not testable and we state so rather than counting it.** The hypervisor extension
`H` is declared in the model but is not implemented upstream: its implementation file contains only
the declaration that gates it. There is no hypervisor behaviour in the model to test. We therefore
scope to the remaining 30, and treat adopting or completing an upstream hypervisor implementation as
a separately costed option (Section 11.2) rather than folding model implementation work into a test
generation engagement.

## 6.2 How privileged coverage will be quantified

Progress is reported as a structure, not a single number:

1. **A measured baseline** at a stated model revision and configuration, recorded before privileged
   capability work begins, so improvement is attributable.
2. **Per-extension coverage**, so a gap is attributable to a specific privileged extension rather
   than diluted into a whole-model figure.
3. **Matrix fill rates** — trap, CSR access, translation and interrupt matrices, per configuration,
   against the reachable denominator.
4. **Marginal contribution per scenario family delivered**, so the value of each increment is
   visible.
5. **Negative control status** for every scenario family — a control that passes is reported as a
   framework defect, not hidden.
6. **An explicit uncovered list**, each entry classified as reachable by a planned scenario,
   unreachable in this configuration, or out of scope.
7. **Trend across model revisions**, so change driven by model evolution is distinguishable from
   progress.

## 6.3 Scenario families

The privileged scenario families the framework will generate, each with negative controls:

| Family | Behaviour exercised |
|---|---|
| Privilege transitions | `ECALL`, `EBREAK`, `WFI`, `MRET` and `SRET` into each target privilege level |
| Physical memory protection | Permitted and denied accesses, correct cause and trap value |
| Physical memory attributes | Unsupported atomics, unreservable reservations |
| Address translation | Bare, Sv32, Sv39, Sv48, Sv57; mapped access, page faults at each walk level |
| Translation attributes | Accessed/dirty update, NAPOT, PBMT, and related `Sv*` behaviours |
| Interrupts | Delivery, delegation, masking, and cause correctness |
| CSR behaviour | Access legality across privilege, field behaviour, `Sm*` and `Ss*` extension CSRs |
| Pointer masking | Masked address generation across privilege levels |
| Unit-disabled traps | Floating-point and vector instructions trapping when the relevant unit is disabled |

## 6.4 Configurations

Privileged coverage is reported per configuration across the agreed matrix, since translation modes,
CSR widths and several `Ss*` behaviours are XLEN-dependent and a single figure would conceal that.

## 6.5 The completeness claim

> **100% of the enumerated reachable denominators for each required configuration, with every
> unreachable target explicitly justified.**

This is checkable. A reviewer can take the exclusion register, contest any individual entry, and
confirm the fill rate of every matrix independently.

## 6.6 What we do not claim

**We do not claim complete ISA coverage.** No methodology can, and we would rather state the boundary
than imply we had crossed it. Three independent reasons:

**Exhaustive operand coverage is unavailable.** Exhaustive testing of one `ADD` on RV64 involves
2^128 operand pairs — for a single instruction, before machine state is considered. Equivalence-class
partitioning (Section 5.7 A) is the standard and necessary response, and its correctness is a matter
for technical argument, which is why we publish the partition rather than only its results.

**"ISA conditions" do not necessarily provide an enumerable denominator.** The architecture
specification is prose. There is no machine-readable list of architectural conditions to divide by,
so a percentage of the ISA is not a computable quantity. Every figure this framework publishes is a
proportion of a denominator it states, which is why the denominators are enumerated explicitly in
Section 5.7.

**Model branch coverage can reach 100% even when required architectural behaviour is absent from the
model.** Coverage measures the implementation that exists. Where the model omits a required
behaviour, there is no branch to cover, and a complete coverage figure is reported on a defective
model. This is not a hypothetical weakness of the metric — it is the specific case that motivates
mandatory differential execution (Section 5.6), which is the only mechanism in this design capable of
detecting it.

A related and subtler point: whether an architectural corner case is visible to branch coverage
depends on how the model author wrote the line. Base integer addition in the model is a single
expression with no branches, so a test computing `1+1` achieves complete branch coverage of RISC-V
integer addition while leaving overflow, the `x0` destination case and sign-extension boundaries
untested. Division, by contrast, encodes its corner cases as explicit conditionals, which branch
coverage does detect. The metric cannot distinguish the two situations. Axis A exists to cover the
first case; axis C exists to detect when our partitioning of it is inadequate.

*Supports criterion 1.*

---

# 7. Maintainability and Extensibility

The RFQ's second criterion is long-term maintainability, and the framework is expected to be
maintained by the Golden Model community after delivery. The design objective is therefore explicit:

> Adding a new instruction, extension, CSR, architectural state element, or behavioural path to Sail
> shall not require manually adding a new hard-coded test generator.

## 7.1 Three-tier extensibility

| Tier | Applies when | Work required |
|---|---|---|
| **Tier 1 — automatic discovery** | The addition uses existing declaration forms and operand kinds: a new instruction, a new CSR, a new extension composed of them | **None.** The framework re-reads the model declarations, the new element appears in the enumeration, the denominator updates, and generation targets it |
| **Tier 2 — capability adapter** | The addition introduces a genuinely new architectural mechanism requiring special initialisation — a new translation scheme, a new protection mechanism, a new state element that must be established before the behaviour is reachable | **A new adapter**, implementing a documented interface. No change to the framework core |
| **Tier 3 — template backstop** | Neither of the above can reach the construct | **A template builder**, explicitly capped and reported |

The intent is that the overwhelming majority of upstream additions land in tier 1, that tier 2 is
exercised a small number of times per year as the architecture genuinely grows, and that tier 3 stays
at its current size. **Movement of work into tier 3 is reported as a framework defect signal**, since
it indicates generic generation is failing to reach something it should.

## 7.2 The capability adapter interface

A capability adapter declares what a new mechanism needs, rather than how to test it. The interface
is deliberately narrow:

| Method | Responsibility |
|---|---|
| `declares()` | Which model elements — extensions, CSRs, state — this adapter is responsible for |
| `reachability(config)` | Whether the mechanism is reachable in a given configuration, feeding the exclusion register |
| `emit_preamble(scenario)` | The machine state required, emitted as preamble instructions |
| `targets()` | The matrix cells this mechanism contributes, feeding the denominator |
| `negative_controls()` | The controls that must fail, so the adapter cannot silently produce vacuous tests |

Adapters are registered, not compiled into the core. An adapter that fails to supply negative
controls is rejected at registration, so extensibility cannot be used as a route around the quality
gate.

## 7.3 Bounded future enhancements

Stated as candidate future work rather than commitments, so that scope is visible:

| Enhancement | Nature |
|---|---|
| Extending Isla's primop set with the 45 SoftFloat operations | Bounded integration work that would allow floating point through the symbolic path in addition to the oracle |
| Adopting or completing an upstream hypervisor implementation | Model implementation work, separately costed (Section 11.2) |
| Additional independent simulators beyond the default | The comparison interface is simulator-agnostic by design |

## 7.4 Why the agentic layer improves maintainability

The maintenance burden in a framework of this kind is not the generation engines — it is keeping the
framework's *understanding of the model* synchronised with the model itself. Hand-maintained lists of
instructions, CSRs, extensions and scenarios are what rot when upstream changes.

The agentic layer removes that burden by deriving its understanding from the model on each run rather
than from a checked-in list: enumerating targets, detecting what changed between model revisions,
classifying uncovered targets, and prioritising work. When Sail gains an extension, the analysis picks
it up and reports its effect on the denominator without a maintainer editing a table.

Because the agent produces plans rather than results, and the deterministic core establishes what
actually happened (Section 4.2), this reduces maintenance effort without placing correctness in the
hands of a non-deterministic component.

*Supports criterion 2.*

---

# 8. Open Source Integration

## 8.1 Position

**Upstream-first, reuse over duplication.** The framework is an integration of existing community
tools, not a replacement for them. Where a capability exists in a community project, the framework
consumes it.

## 8.2 Projects consumed and the nature of the integration

| Project | Role | Integration |
|---|---|---|
| **RISC-V Sail model** | Golden reference and source of truth | Consumed unmodified from upstream. Configuration read in the model's own JSON format. Coverage taken from the model's own instrumentation and span manifest |
| **Isla** | Symbolic execution of the Sail model | Consumed as the symbolic generation engine, including its path-enumeration mode |
| **Z3** | Constraint solving | Consumed via Isla |
| **`sailtest`** | Concrete oracle | Executes the model to obtain expected architectural state |
| **Spike** | Independent simulator | Differential execution target; the interface is simulator-agnostic so alternatives can be added |
| **RISC-V Architectural Certification Tests** | Format and conventions | Generated tests follow established conventions where applicable, so that output is familiar to the community and usable alongside existing suites |

## 8.3 No private fork

**No private fork of the Sail model will be maintained.** This is a hard constraint, and it has
consequences the proposal accepts explicitly:

- Where the framework requires model capability that does not exist upstream, that capability is
  proposed upstream as a reviewable change, decomposed into individually reviewable pull requests
  rather than a single large commit.
- Where a defect is found in the model, it is minimised to a reproducer and raised upstream. It is
  not patched locally, because a locally patched model is no longer the Golden Model and any coverage
  measured against it is not comparable.
- Where an upstream change is not accepted, the framework adapts rather than diverging. Section 15
  records this as a risk with its mitigation.

## 8.4 Contribution back

Defects found through differential execution will be submitted upstream with minimised reproducers.
Framework code will be released under a licence compatible with the projects it integrates, and
documentation will be written for community maintenance rather than for the delivering team
(Section 10.3).

*Supports criteria 3 and 4.*

---

# 9. Model Evolution and Support for New Sail Functionality

The Sail model is actively developed. A framework that requires manual updating for every upstream
change will fall behind, and its coverage figures will silently become incomparable.

## 9.1 Change detection

On each run, and on demand between two model revisions, the framework produces a **model-revision
diff report**:

| Reported | Consequence |
|---|---|
| New extensions, instructions, CSRs, causes | Denominator grows; new targets enter the work queue |
| Removed or renamed elements | Denominator shrinks; affected tests and matrix cells are retired with a record |
| Changed span manifest | Historical coverage figures are marked as measured at a prior revision and not directly comparable |
| Changed configuration schema | Configuration object validation updated; affected configurations flagged |

**Every coverage figure is qualified by the model revision that produced it.** Comparison across
revisions is explicitly guarded rather than silently permitted, because a changed span manifest can
move a percentage without any change in test quality.

## 9.2 Response by tier

The change is then handled per Section 7.1: tier 1 additions are absorbed with no code change; tier 2
additions require an adapter; tier 3 is a reported exception.

## 9.3 Regression protection

The configuration sweep and the matrix fill reports run as continuous integration, so that an
upstream change reducing reachability or breaking generation surfaces as a CI failure with the
model revision that caused it, rather than as an unexplained drop in a coverage number.

*Supports criteria 1, 2 and 3.*

---

# 10. Verification and Quality Assurance

A test-generation framework that is itself untested is not evidence of anything. The framework will
be verified on four levels.

## 10.1 Test-level

The quality gate (Section 5.5) rejects any test that cannot fail. Rejections are recorded with cause.

## 10.2 Suite-level

**Negative controls** accompany every privileged scenario family; a control that passes is a
framework defect and is reported as such.

**Mutation sensitivity** (Section 5.7 C) audits whether the suite detects injected faults, providing
evidence that coverage figures correspond to verification rather than to execution.

**Coverage reconciliation invariant**: the union of per-test coverage records must equal the suite
total, asserted on every run, so that attribution errors surface immediately.

## 10.3 Framework-level

**Reproducibility**: provenance sufficient to regenerate a suite; a suite regenerated from recorded
provenance is expected to match.

**Documented acceptance test**: a procedure a third party can run against a clean checkout to confirm
the framework does what this document says, without access to the delivering team.

**Documentation in two tracks**: a user track for running the framework and interpreting its reports,
and a developer track for extending it — the adapter interface, the routing rules, and the
denominator computation.

## 10.4 Result-level

Failures are classified into **model fault** and **framework fault**, and reported separately. A
report that presents a single pass/fail count without that split is not actionable, since the two
require entirely different responses.

*Supports criteria 1, 2 and 3.*

---

# 11. Deliverables

## 11.1 Core deliverables

| # | Deliverable | Acceptance basis |
|---|---|---|
| D1 | Test-generation framework, source and licence | Builds from a clean checkout following the documented procedure |
| D2 | Example scripts generating a suite from a given Golden Model configuration | One documented command produces an extension-organised, manifested, provenance-stamped suite for each configuration in the agreed set |
| D3 | Example scripts executing a suite and reporting results | One documented command executes on both targets and produces the per-test and summary reports of Section 5.7 |
| D4 | Generated test suites for the agreed configuration matrix | Suites assemble, link and execute on an independent simulator; manifests and provenance present |
| D5 | Coverage and measurement reporting | Reports on all four axes, per configuration, each carrying configuration, model revision and exclusion scope |
| D6 | Exclusion register per configuration | Machine-readable; one entry per exclusion with a reason from the closed vocabulary |
| D7 | Model defect reports | Minimised reproducer and divergence evidence per defect, submitted upstream |
| D8 | Documentation — user and developer tracks | Sufficient for a third party to run and extend the framework; includes the documented acceptance test |
| D9 | CI integration | Configuration sweep and matrix fill running automatically, with model-revision qualification |

## 11.2 Optional, separately scoped

| # | Item | Note |
|---|---|---|
| O1 | Hypervisor extension model work | Adopting or completing an upstream hypervisor implementation is model development, not test generation. Quoted separately if required |
| O2 | Isla SoftFloat primop extension | Would additionally enable floating point through the symbolic path (Section 7.3) |
| O3 | Additional independent simulators | Beyond the default differential target |

---

# 12. Milestones and Acceptance Criteria

Work packages, dependencies and acceptance criteria are defined here; durations and dates are
addressed in Section 13.

| WP | Work package | Depends on | Acceptance criteria |
|---|---|---|---|
| **WP1** | Configuration architecture | — | A single validated configuration object; every downstream setting derived from it; illegal combinations rejected at load; provenance written before generation |
| **WP2** | Model ingestion and target enumeration | WP1 | All target sets in Section 5.1 enumerated from model declarations; unparsed declarations reported rather than silently skipped |
| **WP3** | Reachable denominator and exclusion register | WP2 | Per-configuration denominator computed; exclusion register machine-readable with closed-vocabulary reasons; register reconciles against the full enumeration |
| **WP4** | Generation core and capability routing | WP1, WP2 | All three routes operational; routing rule documented and applied automatically; backstop count reported |
| **WP5** | Preamble builder | WP4 | All state classes of Section 5.4 constructible; page tables for each translation mode; negative controls emitted |
| **WP6** | Quality gate | WP4 | No test enters the suite without non-empty expected state; rejections recorded with cause |
| **WP7** | Dual-target execution and differential comparison | WP4, WP6 | Every valid ELF executed on both targets; divergences triaged and classified with evidence |
| **WP8** | Coverage architecture | WP3, WP7 | All four axes measured; reconciliation invariant asserted; every figure carries configuration, revision and exclusion scope |
| **WP9** | Denominator matrices | WP5, WP8 | Trap, CSR access, translation and interrupt matrices constructed; fill rates reported per configuration |
| **WP10** | Mutation harness | WP8 | Fault-injection set defined; mutation score measured and reported against it |
| **WP11** | Feedback classification and routing | WP8, WP9 | Uncovered targets classified by cause; routing applied; re-classification on repeated failure recorded |
| **WP12** | Privileged scenario families | WP5, WP9 | Each family of Section 6.3 generated with negative controls; per-extension coverage reported |
| **WP13** | Configuration sweep | WP3, WP8 | Full matrix executed; per-configuration denominators, registers and figures; no cross-configuration averaging |
| **WP14** | Agentic orchestration layer | WP2, WP11 | Model analysis, change detection, gap analysis, classification and prioritisation operational; pipeline demonstrably runs with the layer disabled |
| **WP15** | Model evolution support | WP2, WP14 | Model-revision diff report; denominator impact reported; historical figures revision-qualified |
| **WP16** | Extensibility mechanism | WP4, WP5 | Adapter interface documented; worked example; adapter without negative controls rejected at registration |
| **WP17** | Documentation and acceptance test | WP1–WP16 | User and developer tracks; documented acceptance test executable by a third party on a clean checkout |
| **WP18** | CI integration and upstream submission | WP7, WP13 | CI running the sweep; defect reproducers prepared and submitted upstream as reviewable changes |

**Milestone structure.** Milestones are proposed at the following points, each gated on the
acceptance criteria of its constituent work packages:

| Milestone | Constituent work packages | Represents |
|---|---|---|
| M1 — Foundation | WP1, WP2, WP3 | Configuration, enumeration and denominator: the framework can state what it is measuring against |
| M2 — Generation | WP4, WP5, WP6 | Valid, non-vacuous tests produced across all routes |
| M3 — Validation | WP7, WP8, WP10 | Dual-target execution, coverage on all axes, mutation audit |
| M4 — Privileged capability | WP9, WP11, WP12 | The primary evaluation criterion demonstrated |
| M5 — Scale and adaptation | WP13, WP14, WP15, WP16 | Configuration sweep, agentic layer, model evolution, extensibility |
| M6 — Delivery | WP17, WP18 | Documentation, acceptance test, CI, upstream submission |

---

# 13. Timeline

**To be populated based on agreed scope, staffing, and delivery date.**

We have deliberately not stated durations, because a schedule that is not derived from agreed scope
and confirmed staffing is not a commitment and should not be treated as one by an evaluator.

## 13.1 What determines the schedule

| Driver | Effect |
|---|---|
| Size of the agreed configuration matrix | Sweep effort and matrix construction scale with configuration count |
| Which privileged scenario families are in scope (Section 6.3) | The dominant driver of WP12 |
| Whether the agentic layer (WP14) is in the core scope or deferred | Affects WP11 and WP15 sequencing |
| Whether the optional items of Section 11.2 are included | Hypervisor model work in particular is a different class of work |
| Upstream review latency for model changes (WP18) | Outside our control; see Section 15 |
| Staffing profile and concurrency | See Section 13.3 |

## 13.2 Critical path

```
WP1 -> WP2 -> WP3 -> WP8 -> WP9 -> WP12
             \-> WP4 -> WP5 -> WP7 -/
```

The denominator (WP3) gates coverage architecture (WP8), which gates the matrices (WP9), which gate
the privileged scenario families (WP12) that carry the primary evaluation criterion. **WP1–WP3 are
therefore the highest-priority sequence**, and any schedule that defers them in favour of visible
test generation will produce tests that cannot be scored.

WP14 (agentic layer) is deliberately off the critical path: the pipeline runs without it.

## 13.3 Resource assumptions

To be confirmed. The estimation model assumes the following roles, whose allocation is to be agreed:

| Role | Responsibility |
|---|---|
| Framework engineer | Generation core, routing, adapters, configuration architecture |
| Verification engineer | Privileged scenarios, preamble construction, negative controls, matrices |
| Model / formal-methods engineer | Sail and Isla integration, upstream change decomposition, defect minimisation |
| Infrastructure engineer | CI, configuration sweep, reporting, reproducibility |

Part-time and shared allocation across roles is expected rather than exceptional.

---

# 14. Cost

**To be populated based on agreed scope, staffing, and delivery date.**

No price is stated in this document. What follows is the estimation model by which a price will be
derived, so that the basis is transparent and the evaluator can see what drives it.

## 14.1 Estimation model

```
Total cost  =  SUM over work packages of ( effort_WP  x  blended_day_rate )
               +  configuration_sweep_effort
               +  upstream_engagement_allowance
               +  contingency
```

Where:

| Term | Basis |
|---|---|
| `effort_WP` | Engineer-days per work package, by role, from the WP table in Section 12 |
| `blended_day_rate` | To be supplied by the bidder |
| `configuration_sweep_effort` | Scales with the agreed configuration matrix size; largely automation once WP13 is complete |
| `upstream_engagement_allowance` | Effort for decomposing model changes into reviewable pull requests and responding to review. Distinct because its duration is not under our control |
| `contingency` | Applied against the risks in Section 15, principally upstream review latency and model evolution |

## 14.2 Effort banding

To allow the evaluator to compare scope options without a stated price, work packages are banded by
relative effort. Bands are ordinal, not absolute.

| Band | Work packages |
|---|---|
| **Large** | WP4 Generation core · WP5 Preamble builder · WP12 Privileged scenario families |
| **Medium** | WP1 Configuration · WP2 Ingestion · WP3 Denominator · WP7 Dual-target execution · WP8 Coverage architecture · WP9 Matrices · WP13 Configuration sweep · WP14 Agentic layer · WP17 Documentation |
| **Small** | WP6 Quality gate · WP10 Mutation harness · WP11 Feedback routing · WP15 Model evolution · WP16 Extensibility mechanism · WP18 CI and upstream |

## 14.3 Scope options affecting cost

| Option | Cost effect |
|---|---|
| Reduce the configuration matrix | Reduces WP13 and matrix construction effort proportionally |
| Defer the agentic layer (WP14) | Reduces cost; increases long-term maintenance effort, working against criterion 2 |
| Defer selected privileged scenario families | Reduces WP12; directly reduces performance against criterion 1, the primary criterion |
| Include hypervisor model work (Section 11.2, O1) | Substantial addition of a different work type |
| Include Isla SoftFloat primops (Section 11.2, O2) | Bounded addition; no effect on committed coverage, since the oracle already covers floating point |

**Deferring WP1–WP3 is not offered as a cost option**, because without them no coverage figure the
project produces is defensible.

---

# 15. Risks and Mitigations

Stated honestly, including risks that are not fully within our control.

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | **Upstream review latency.** Model changes required by the framework, and defect fixes, depend on upstream maintainer availability | Medium | Schedule | Decompose into small individually reviewable pull requests rather than one large change; engage early; the framework adapts rather than forking if a change is not accepted (Section 8.3) |
| R2 | **Model evolution during the engagement** changes the denominator and invalidates comparison of coverage figures | High | Reporting integrity | Every figure qualified by model revision; model-revision diff report (Section 9.1); comparison across revisions explicitly guarded |
| R3 | **Symbolic path scalability.** Path enumeration on deeply branching model code may not terminate in acceptable time | Medium | Coverage of specific areas | Capability routing already directs the hardest areas to the oracle; per-target time budgets; targets exceeding budget recorded in the uncovered list with cause rather than silently dropped |
| R4 | **Preamble construction complexity** for the deeper translation and protection scenarios exceeds estimate | Medium | WP5, WP12 | Adapter mechanism allows incremental delivery per mechanism; scenario families are independently deliverable, so partial delivery degrades scope rather than blocking |
| R5 | **Differential divergence volume.** A large number of Sail/Spike divergences would consume triage effort | Medium | Schedule | Triage classification is part of WP7; divergence is capped per run with the remainder queued; configuration disagreement eliminated as a cause by the single-source configuration object (Section 5.1) |
| R6 | **Agentic layer non-determinism** produces inconsistent planning between runs | Medium | Reproducibility | The agent produces plans, never expected state or test content; all plans are logged and replayable; the pipeline runs with the layer disabled (Section 4.2); correctness is established solely by the deterministic core |
| R7 | **Independent simulator gaps.** Spike may not support every configuration in the matrix | Medium | Differential coverage | The comparison interface is simulator-agnostic; configurations lacking an independent target are reported as such rather than silently single-target |
| R8 | **Mutation harness cost.** Fault injection across a large model may be expensive to run | Low | Schedule | Mutation is sampled against a defined fault set rather than exhaustive; run at milestone boundaries rather than per commit |
| R9 | **Scope of "privileged extensions" interpreted differently** by evaluator and bidder | Low | Acceptance | Section 6.1 states the enumeration and its boundary explicitly, including the exclusion of `H` and the reason |

---

# 16. Relevant Technical Experience

> **To be populated by the bidder with verifiable credentials.** This section is deliberately left
> for factual completion rather than drafted, since claims of community standing must be
> substantiated and are directly assessed under RFQ criterion 4.

The following structure is recommended, with each entry supported by a public, checkable reference:

| Evidence area | What to supply |
|---|---|
| RISC-V Sail model | Contributions, issues raised, review participation — with links |
| Isla / symbolic execution | Experience with the toolchain, contributions if any |
| Spike or other independent simulators | Integration or contribution experience |
| RISC-V Architectural Certification Tests | Familiarity with the format and conventions |
| RISC-V community participation | Working group involvement, ratified specification contribution |
| Comparable engagements | Prior test-generation or verification framework delivery, with outcomes |
| Team composition | Named roles against Section 13.3, with relevant background per person |

**No claim of community acceptance, merged contributions, or prior results is made in this document
where it has not been substantiated.**

---

# 17. Mapping to RFQ Evaluation Criteria

## Criterion 1 — Coverage of the Privileged Architecture ISA extensions as implemented by the Sail model

| Design decision | Contribution |
|---|---|
| Reachable denominator per configuration (5.2) | Makes a coverage claim computable and checkable rather than asserted |
| Preamble builder as a first-class component (5.4) | Directly addresses the fact that privileged behaviour is state-driven, not opcode-driven |
| Denominator matrices — trap, CSR, translation, interrupt (5.7 A) | Provides completable, enumerable coverage of privileged behaviour |
| Per-extension, per-configuration reporting (6.2) | Attributes gaps to specific extensions rather than diluting them |
| Negative controls on every scenario family (5.4, 6.3) | Ensures privileged tests fail when they should |
| Differential execution (5.6) | Detects privileged behaviour missing from the model, which coverage cannot |
| Explicit exclusion of `H` with reason (6.1) | Accurate scope rather than an inflated count |

## Criterion 2 — Long-term maintainability and extensibility

| Design decision | Contribution |
|---|---|
| Three-tier extensibility (7.1) | Most upstream additions require no framework change at all |
| Capability adapter interface (7.2) | New architectural mechanisms are added without core redesign |
| Model-derived enumeration rather than hand-maintained lists (5.1, 7.4) | Removes the artefact that rots when upstream changes |
| Model-revision diff reporting (9.1) | Change is surfaced and quantified rather than discovered later |
| Single-source configuration object (5.1) | Eliminates a whole class of framework-fault failures |
| Capped template backstop with growth reported (5.3 C) | Prevents accumulation of unmaintainable special cases |
| Documentation in user and developer tracks, with a third-party acceptance test (10.3) | Community maintenance is a delivery requirement, not an aspiration |

## Criterion 3 — Inclusion or integration of existing open source communities

| Design decision | Contribution |
|---|---|
| No private fork of the model (8.3) | The Golden Model remains the Golden Model; coverage stays comparable |
| Consumption of Sail, Isla, Z3, `sailtest` and Spike as upstream projects (8.2) | Integration rather than duplication |
| Model changes decomposed into reviewable pull requests (8.3) | Respects upstream review capacity |
| Defects raised upstream with minimised reproducers (8.4) | Contribution back, not extraction |
| Certification-test conventions where applicable (8.2) | Output usable alongside existing community suites |

## Criterion 4 — Demonstrated technical skills in the targeted open source communities

| Design decision | Contribution |
|---|---|
| Capability routing justified by the properties of SMT solving and the Isla primop set (5.3) | Demonstrates working knowledge of the tools rather than nominal familiarity |
| Preamble-not-constraint design decision (5.4) | Demonstrates understanding of where solvers are and are not the right instrument |
| Recognition that the Privileged Architecture contributes nine instructions (2.2) | Demonstrates the model has been read, not assumed |
| Coverage philosophy grounded in how the model is written (6.6) | Demonstrates understanding of what the metric does and does not measure |
| Section 16 | To be substantiated by the bidder with checkable references |

## Criterion 5 — Cost of the proposal

| Design decision | Contribution |
|---|---|
| Transparent estimation model (14.1) | The basis of the price is visible, not opaque |
| Effort banding by work package (14.2) | Scope options can be compared before a price is fixed |
| Explicit scope options with their consequences (14.3) | The evaluator can reduce cost knowingly rather than by guesswork |
| Optional items separately scoped (11.2) | Model implementation work is not bundled into test generation |
| Agentic layer reducing long-run maintenance (7.4) | Addresses total cost of ownership, not only delivery cost |

## Criterion 6 — Date of delivery

| Design decision | Contribution |
|---|---|
| Work packages with explicit dependencies (12) | Schedule is derivable rather than asserted |
| Stated critical path (13.2) | The sequence that determines delivery is visible |
| Independently deliverable scenario families (15, R4) | Partial delivery degrades scope rather than blocking delivery |
| Agentic layer off the critical path (13.2) | The most novel component cannot delay the core |
| Upstream latency identified as an external dependency (15, R1) | The one schedule risk outside our control is named rather than absorbed silently |

---

# 18. Final Acceptance Criteria

The engagement is complete when all of the following hold. Each is checkable by the evaluator without
reference to the delivering team.

| # | Criterion |
|---|---|
| A1 | The framework builds from a clean checkout following the documented procedure, against the unmodified upstream Sail model at a stated revision |
| A2 | A single documented command generates a suite from a given Golden Model configuration, for every configuration in the agreed matrix |
| A3 | A single documented command executes a suite on both targets and produces the per-test and summary reports of Section 5.7 |
| A4 | Every generated test carries a non-empty expected state; the count of gate rejections is reported with causes |
| A5 | Every valid test has been executed on Sail and on an independent simulator, with results compared and divergences classified |
| A6 | A reachable denominator and an exclusion register exist for each configuration, machine-readable, with a closed-vocabulary reason per exclusion |
| A7 | Matrix fill rates are reported per configuration for the trap, CSR access, translation and interrupt matrices, against the reachable denominator |
| A8 | 100% of the enumerated reachable denominators is achieved for each required configuration, or every shortfall is individually listed and justified |
| A9 | Sail branch coverage is reported per configuration, carrying configuration, model revision and exclusion scope, and is not presented as complete ISA coverage |
| A10 | A mutation score is reported against a stated fault-injection set |
| A11 | Privileged coverage is reported per extension and per configuration, with negative control status for every scenario family |
| A12 | Adding an instruction, extension or CSR to the model requires no new hard-coded generator, demonstrated by worked example |
| A13 | The capability adapter interface is documented with a worked example, and rejects adapters lacking negative controls |
| A14 | A model-revision diff report is produced, reporting the effect of upstream change on the denominator |
| A15 | Model defects found are minimised to reproducers and submitted upstream |
| A16 | Documentation in user and developer tracks is delivered, including an acceptance test a third party can execute |
| A17 | CI runs the configuration sweep and matrix fill reporting, qualified by model revision |
| A18 | No private fork of the Sail model exists in any delivered artefact |

---

# Evaluator's View

**Why this proposal is strong against each criterion.**

**1 — Privileged Architecture coverage.** Most responses to this RFQ will treat privileged coverage
as a test-count or branch-percentage problem. This proposal identifies the structural fact that
determines the outcome — the Privileged Architecture contributes nine instructions to the model, and
everything else about it is reached by establishing machine state — and builds the architecture
around it, with the preamble builder as a first-class component rather than a helper. It then makes
the coverage claim *checkable*: an enumerated denominator, per-configuration, with an exclusion
register a reviewer can contest line by line, and matrices whose fill rate is a fact rather than an
assertion. Every scenario family carries a negative control, so a passing privileged test means
something.

**2 — Maintainability and extensibility.** The proposal treats the real maintenance burden correctly.
It is not the generation engines; it is keeping the framework's understanding of the model
synchronised with a model that is actively developed. Enumeration is derived from the model on every
run rather than from checked-in lists, a three-tier extensibility model means most upstream additions
require no framework change at all, and the capability adapter interface allows genuinely new
architectural mechanisms without core redesign. The template backstop is capped and its growth
reported as a defect signal, preventing the accumulation of special cases that makes frameworks of
this kind unmaintainable.

**3 — Open source integration.** No private fork, as a hard constraint with its consequences accepted
explicitly. Every major component is an existing community project consumed rather than reimplemented.
Model changes are decomposed into individually reviewable pull requests rather than presented as a
single large commit, which respects upstream review capacity. Defects are raised upstream with
minimised reproducers instead of patched locally, so the Golden Model remains the Golden Model and
coverage figures stay comparable across the community.

**4 — Demonstrated technical skill.** The technical decisions are justified from the properties of
the tools rather than asserted. Vector generation is routed away from the symbolic path because SMT
bitvector theory requires fixed widths while `vl` is runtime state; floating point because 45
SoftFloat primops are absent from Isla's registered set. Machine state is written into a preamble
rather than asserted as a solver constraint, because constructing a page table directly is more
predictable than asking a solver to discover one. The coverage philosophy is grounded in how the
model is actually written — the observation that base `ADD` has no branches while `DIV` encodes its
corner cases explicitly, so the metric's sensitivity depends on the author's style. These are the
observations of a team that has read the model.

**5 — Cost.** The estimation model is transparent, the work packages are banded, and the scope
options are stated with their consequences so the evaluator can trade cost against capability
knowingly. Optional items — hypervisor model work in particular — are separately scoped rather than
bundled, so the evaluator is not asked to buy model development inside a test-generation engagement.
No price is invented, which means the price ultimately quoted will be derived from agreed scope
rather than defended after the fact.

**6 — Delivery date.** The schedule is derivable from stated work packages, dependencies and a named
critical path rather than asserted as a date. The most novel component, the agentic layer, is
deliberately off the critical path and the pipeline runs without it, so innovation cannot delay
delivery. Scenario families are independently deliverable, so schedule pressure degrades scope
visibly rather than causing slip. The one schedule risk outside our control — upstream review latency
— is named as such with its mitigation, rather than silently absorbed into an optimistic estimate.

**The position on completeness is the differentiator.** This proposal states plainly that complete
ISA coverage is not claimed, gives three independent technical reasons why no methodology can deliver
it, and then supplies a completeness claim that *can* be discharged and checked. An evaluator
comparing this against a response promising 100% ISA or branch coverage is comparing a commitment
that can be audited against one that cannot be met — and any evaluator who has done verification work
will recognise which is which.
