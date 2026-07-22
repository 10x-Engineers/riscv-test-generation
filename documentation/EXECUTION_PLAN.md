# Automated RISC-V Test Generation from the Sail Golden Model
## Master Plan & RFP Response Strategy

> Consolidates: the recommended technical approach, its mapping to the RFP's evaluation
> criteria/deliverables, the 21–31 Jul proposal-preparation sprint, and the post-award
> execution roadmap that goes into the proposal.

---

## 0. The framing that changes everything

**31 July 2026 is the hard external deadline to submit the *proposal* — a technical plan +
estimated timeline + cost — to `tech-proposals@riscv.org`. It is NOT the deadline to deliver
the framework.** (RFP, "Responding to this RFQ".) **Internal target: 29 July** — proposal
complete and review-ready by the 29th, leaving 30–31 as a review/polish buffer before
submission.

So the 21–31 Jul work is **not** building the tool. It is: decide what to propose, prove the
load-bearing claims with small spikes, and write a proposal that wins on the RFP's own
evaluation criteria. The framework itself is built *after award*, over months — and the
proposal must contain that post-award timeline + cost (§5).

**The RFP's evaluation criteria, in priority order, are what the whole strategy optimizes
for:**
1. **Coverage of the RISC-V *Privileged* Architecture** as implemented by the Sail model.
2. **Long-term maintainability & extensibility.**
3. Integration with existing open-source communities.
4. Demonstrated technical skill in those communities.
5. Cost.
6. Date of delivery.

---

## 1. Recommended approach — the thesis

> **A Python-first, coverage-directed *hybrid* generator: a config-driven concrete-oracle
> backbone for breadth across the whole privileged ISA, with an ISLA symbolic-execution
> specialist for the hard corners random stimulus can't reach — both steered by, and
> measured against, Sail-code coverage.**

Five pillars, each chosen against a specific evaluation criterion:

| Pillar | What it is | RFP criterion it wins |
|---|---|---|
| **Python framework** | The orchestration, config, target interface, ELF emit — all Python. | #2 maintainability (community is Sail/C/Python, not Rust); #3/#4 |
| **Concrete-oracle backbone** | Random/templated instruction sequences → run the Golden Model → bake expected state into a self-checking test. Covers *every* privileged instruction by construction (model runs it ⇒ we can test it). | #1 breadth coverage; #6 fast delivery |
| **Sail-code coverage — metric *and* steering** | Build the model with `COVERAGE`/`--c-coverage`; run each ELF; report per-ELF + suite branch coverage **in Sail terms**, organized by extension; feed *uncovered privileged branches* back to steer generation. | #1 (coverage, exactly "in terms of the Sail code" per consideration F); this is a *required deliverable*, grounded in real `sail-riscv` tooling |
| **ISLA symbolic specialist** | For deep privileged corners random can't reach (specific PMP violations, precise trap-delegation, chosen page-fault causes), *solve* for inputs via `isla`. Reuses the `isla-testgen` lineage we already enabled for RV32 incl. PMP. | #1 depth; #3/#4 (isla community + demonstrated skill) |
| **ACT4-compatible, Apache-2.0** | Reuse `riscv-arch-test` (ACT4) `rvmodel` macros for portable ELFs; Apache-2.0 license. | Consideration A; IP requirement |

**Why hybrid, not one technique:** the RFP's #1 criterion (privileged coverage in Sail
terms) is coverage-directed generation's home turf — but ISLA's symbolic engine is where we
hit the worst blockers on exactly the privileged features in scope (the PMP struct/vector
bug; VM/page-table-walk path explosion). The oracle backbone covers those classes *by
construction*; ISLA supplies surgical depth where it's tractable. The RFP itself cites **both**
a randomised generator (CHERIoT `sail-riscv-test-generation`) **and** a symbolic one
(`isla-testgen`) as prior art — the hybrid synthesises the RFP's own two reference points.

**Why not pure ISLA (Rust):** it loses hard on criterion #2 — the `isla-lib`/`isla-sail`
stack needs Rust + symbolic-execution + SMT expertise the Golden Model community largely
doesn't have, and we lived its drift/maintenance cost this cycle. It stays in the design, but
as a scoped specialist, not the backbone.

**Why not pure oracle:** it can hit every instruction but random stimulus reaches deep
privileged corners only by luck — insufficient on its own for a *coverage-led* privileged
proposal. Coverage-directed steering + the ISLA specialist close that gap.

---

## 2. How the approach satisfies the RFP, requirement by requirement

**Goals**
| RFP goal | How we meet it |
|---|---|
| Use current `riscv/sail-riscv` | Yes — already working against it (this cycle's enablement). |
| Tests as valid RISC-V ELFs | Yes — oracle backbone emits self-checking ELFs; ACT4-macro compatible. |
| Organize tests by ISA extension | Yes — `out/<config>/<extension>/` + `manifest.json` (AutoTest already does this). |
| Support model configs (RV32/RV64, F/D, VLEN/ELEN) | Config-driven off the Golden Model's own JSON; matches its CI configs. |
| **Focus on privileged ISA** (M-mode PMP/PMA, traps, interrupts; S-mode VM desirable) | The core of the roadmap (§5, P2–P3); coverage-directed at privileged Sail branches. |
| Coverage measure per-ELF and per-suite | Sail-code branch coverage via `--c-coverage`/`sail_coverage`, per-ELF + merged. |
| OSS license compatible with Golden Model | Apache-2.0. |

**Deliverables**
| # | RFP deliverable | Plan |
|---|---|---|
| 1 | Repo + user & developer docs | The `riscv-test-generation` repo; docs a first-class output (criterion #2). |
| 2 | Example scripts: generate a suite from a config | Config-driven CLI + example scripts (P1/P5). |
| 3 | Example scripts: run a simulator, collect + summarize results | Spike/QEMU/Whisper runners + summary (P1/P5). |
| 4 | **Merged PRs integrating into Golden Model CI** | Explicit roadmap phase (P5); we already have open model PRs from this cycle. |
| 5 | Merged PRs if the model needs extension | Precedent set — this cycle's `main.sail`/callback PRs are exactly this shape. |

**Considerations**
| Ref | Consideration | Plan |
|---|---|---|
| A | ACT4-macro-compatible ELFs | Reuse `riscv-arch-test` `rvmodel` macros. |
| B | Readable, consistently formatted tests; Sail-source pointers | Formatter + optional Sail-line annotations (the oracle path makes this natural). |
| C | Unprivileged ISA (no FP/V) should be easy | It is — oracle covers it trivially; base integer is the P1 baseline. |
| D | Extendable to hypervisor / FP / vector later | Python + config-driven design keeps extension additive (P5 does F/D + VLEN/ELEN). |
| E | Tests/coverage change as the model evolves | Coverage is recomputed from the live model each run; nothing hard-coded. |
| F | Coverage measure in terms of Sail code | Exactly what `sail_coverage` gives — Sail branch/line coverage. |
| G | Self-checking or trace-based | Self-checking ELFs (portable, run anywhere), matching ACT4. |

---

## 3. Repository structure (finalized)

```text
riscv-test-generation/            # new, private, Apache-2.0
├── isla-gen-extension/           # submodule → our isla-testgen fork (symbolic specialist evidence)
├── python-isla/                  # the Python framework prototype (oracle backbone + coverage)
├── autotest/                     # submodule → sail-riscv-autotest (Apache-2.0 oracle baseline)
├── documentation/                # this plan, RFP compliance, per-approach docs + diagrams
├── comparison/                   # the three-approach evaluation feeding the recommendation
├── experiments/                  # the coverage + privileged-canary spikes (proposal evidence)
└── PROPOSAL/                     # the actual RFP response drafted here, submitted 31 Jul
```

`isla-gen-extension` and `autotest` are brought in as submodules of the existing org forks so
all three lineages are co-located and comparable; `python-isla` is developed in-repo.

---

## 4. Execution workflow — understand → implement the table → finalize → propose

**Staffing: you + Claude.** **Internal completion 29 Jul; 30–31 review buffer; submit by 31 Jul.**

The sprint runs as five phases. The engine is **Phase 3**: walk the
[coverage-risk matrix](../comparison/coverage-risk-matrix.md) class by class, implementing
each in isla-gen, one class = one task, **merged only once reviewed and understood**. The
filled-in table is both the coverage guarantee ("almost every instruction is reachable") *and*
the evidence for the methodology — where isla-gen hits a wall (VM/PTW, Vector), that blocking
is exactly what justifies the oracle backbone in the hybrid.

| Phase | Days | Tangible output |
|---|---|---|
| **P1 · Document the existing isla-gen RISC-V work** | 22 Jul | A **flow diagram** of the isla-gen RISC-V pipeline (init → symbolic step → state extract → ELF) and **inline code comments** on every change made to extend isla-gen for `addi` and the PMP tests. In `documentation/isla-gen/`. |
| **P2 · Review & understand the changes** | 23 Jul | Using the diagram + comments, a written walkthrough confirming what each change does and why — the shared understanding Phase 3 builds on. |
| **P3 · Implement the instruction table, class by class** | 23–28 Jul | One task per class (below); each implemented, verified on `sail_riscv_sim`/Spike, **reviewed, understood, and merged** before the next; each result updates the coverage-risk matrix (proven / partial / blocked-with-reason). |
| **P4 · Finalize the methodology** | 28 Jul | With the table covered, lock the recommended methodology: isla-gen (symbolic) where it's proven tractable, oracle backbone where isla-gen blocks — justified by the concrete per-class evidence. |
| **P5 · Review the proposal & send** | 29 Jul | Proposal reviewed against the six RFP criteria; **buffer 30–31 Jul**; submit to `tech-proposals@riscv.org` by the 31st. The completed table is the proposal's coverage guarantee. |

### Phase 3 — instruction-class tasks (ordered RFP-privileged-first)

Each is a task that gets *merged once reviewed and understood*. Status carried from the
coverage-risk matrix:

| # | Class | Current status | Notes |
|---|---|---|---|
| C0 | Base ALU (`addi`) + CSR (`csrrw`) | ✅ proven | Documented in P1/P2; no new work, baseline. |
| C1 | **PMP** | 🟠 partial | Finish the Spike reset-value gap; first privileged class merged. |
| C2 | **Traps / exceptions** | ◻ to do | illegal-instr, misaligned load — RFP core. |
| C3 | **Interrupts / timers** | ◻ to do | timer interrupt; needs nondeterminism handling — RFP core. |
| C4 | Branch / jump | ◻ to do | quick (RFP says unprivileged should be easy). |
| C5 | Load / store (aligned) | ◻ to do | symbolic-address canary. |
| C6 | Fence / WFI / system | ◻ to do | quick. |
| C7 | Atomics (A / LR-SC) | ◻ to do | reservation primops currently stubbed. |
| C8 | **Virtual memory / PTW (S-mode)** | ◻ to do | RFP "highly desirable"; **the hardest for isla** — likely the wall → evidence for the hybrid. |
| C9 | Float / Double (F/D) | ◻ future | RFP consideration D — framework-extendable; likely post-award. |
| C10 | Vector (V) | ◻ future | RFP consideration D — hard for isla; likely post-award. |

Privileged classes (C1–C3, C8) are the RFP's #1 criterion and go first; the easy unprivileged
ones (C4–C6) are quick wins; F/D and Vector (C9–C10) are explicitly future per the RFP and are
candidates to defer to the post-award roadmap (§5) rather than force into this window.

---

## 5. Post-award execution roadmap (goes *into* the proposal)

High-level; the proposal details it. Effort in person-weeks, grounded partly in this cycle's
real enablement experience. **Cost = total effort × blended day-rate; the rate and team
availability are inputs you supply — not fabricated here** (see Risks).

| Phase | Scope | Rough effort |
|---|---|---|
| **P1 · Foundation** | Python framework; config-driven off the Golden Model JSON; ACT4-compatible self-checking ELF harness; **Sail-code coverage measurement + reporting**; simulator runners (Spike/QEMU/Whisper) + summary; CI scaffolding; RV32+RV64 base integer. | ~3–5 wk |
| **P2 · M-mode privileged** | PMP/PMA, trap & interrupt handling — coverage-directed oracle generation, steered by uncovered privileged Sail branches. | ~4–6 wk |
| **P3 · S-mode** | Virtual memory / address translation (RFP "highly desirable"). | ~3–5 wk |
| **P4 · Symbolic specialist** | Integrate `isla` to *solve* for the hard uncovered privileged corners; coverage closure on branches random can't reach. | ~3–5 wk |
| **P5 · Config breadth + delivery** | F/D + VLEN/ELEN configs; extensibility hooks (hypervisor/FP/V-ready per consideration D); hardening; user + developer docs; **merged CI-integration PRs** (deliverable #4). | ~3–4 wk |

Indicative total **~16–25 person-weeks**. Phases P2–P4 are the privileged core the RFP
weights highest; P1 and the coverage harness are the reusable spine under everything.

---

## 6. Known risks & what's out of scope

1. **Cost/rates/availability are not ours to invent.** The proposal needs a cost; we supply
   the *effort estimate* and the formula (effort × blended rate). The **rate and how many
   people/when are your inputs** — flagged, not guessed (RISC-V evaluates on cost + delivery
   date, so these must be real numbers before submission).
2. **ISLA's privileged blockers are real** — PMP struct/vector, VM/PTW path explosion. The
   architecture *contains* this risk by making ISLA the specialist, not the backbone; the
   oracle covers those classes by construction. The way we de-risk this concretely is the
   **[coverage-risk canary matrix](../comparison/coverage-risk-matrix.md)**: probe the hardest
   representative of each of the ~12 instruction classes first (VM/PTW and Vector earliest on
   ISLA, since those are the walls), and treat a "blocked — here's why" as a *successful*
   boundary-mapping result. Oracle has no fundamentally-blocked class; its risk is
   setup-machinery effort, measured the same way.
3. **"Understand every function" of `isla-lib` isn't in scope** for the sprint — S1 is
   depth-first on the RISC-V/privileged path (already traced for the PMP fix).
4. **Explicitly out of scope of the proposal itself:** hypervisor extension (RFP defers it);
   and the sprint does *not* deliver the framework — only the proposal + evidence spikes.
5. **Unused slack is reinvested** into a second privileged canary (e.g. VM alongside PMP) and
   a stronger coverage report — both directly strengthen the criterion-#1 story. It does not
   pull the 31 Jul submission earlier; that date is fixed.
