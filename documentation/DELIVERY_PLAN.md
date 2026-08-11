# Delivery Plan — automatic test generation from the Sail RISC-V Golden Model

## Context

This plan covers two horizons in one document, because they are not independent:
the proposal due **Monday 31 August 2026** must itself *contain* the post-award
timeline and cost, so the second horizon has to exist before the first can be
written honestly.

- **Horizon A — pre-award sprint (now → 31 Aug).** Goal is not to finish the
  framework. It is to make the proposal's claims demonstrable, and to build the
  evidence that scores against the RFP's own criteria.
- **Horizon B — post-award build.** The framework delivery the RFP contracts
  for. Deliverables 1–5 land here, over months.

Supersedes the schedule half of
[`EXECUTION_PLAN.md`](EXECUTION_PLAN.md), whose phase dates were built around
the original 31 July deadline. The strategy and RFP-compliance mapping there
still hold; the dates do not.

### How this is sized, and the honest caveat

**Staffing is: Claude implements, the user reviews and approves.** That is one
workstream, not a team, so nothing here assumes parallel execution.

Work is sized in **work units** — one focused implementation-plus-verification
cycle producing a reviewable artifact. It is deliberately *not* sized in
calendar days, because the mapping from work units to days depends on session
cadence, which I cannot verify and will not invent. At roughly one unit per
working session the pre-award list fits the 21 days with margin; at one unit
every three days it does not, and A3/A4 are the first things to cut.

**What every estimate below is grounded in** — measured today, not assumed:

| Fact | Evidence |
|---|---|
| `--all-paths-for` already exists in isla-testgen | `src/testgen.rs:191` |
| It yields 14 tests for one `lw`, vs 1 today | generated and counted |
| Those 14 reach **+76 more Sail spans** than the single test | replayed on the coverage build: 2036 → 2112 |
| 7 of the 14 fail only for a missing trap expectation | traced: `lw` with `x1=0x2000` → load-access-fault, mcause 5 |
| `expect_trap_cause` is a global CLI flag, not per-path | `src/testgen.rs:251`, applied at `generate_object_riscv.rs:995` |
| PMA is fully config-driven | `atomic_support`, `reservability`, `misaligned_exceptions`, `mem_type` per region in the model's own config JSON |
| PMA coverage today | `sys/pma.sail` 15/97 spans = 15.5% |
| Interrupt-register coverage today | `core/interrupt_regs.sail` 13/83 = 15.7% |
| Page-table-walker coverage today | `sys/vmem_ptw.sail` 3/60 = **5.0%** |
| The model's CI uses 16 configs; we run 2 | `build/config/*.json` |
| Privileged branch coverage | 490/699 = **70.1%** (provisional) |

## High-Level Approach

- **Lead with the per-path lever (A1).** It is the only item that improves
  every extension at once, it is derived from the model rather than hand-written,
  and it directly attacks the branch-heavy privileged files that are lowest.
- **Then close the two named-but-empty RFP requirements** (PMA, interrupt
  delivery), because they are explicitly in the *required* list and currently
  sit near zero.
- **Write the proposal against measured numbers**, not projected ones. Every
  figure quoted must come from a run that happened.
- **Post-award work is ordered by RFP deliverable**, so progress maps to the
  contract rather than to our own interests.

---

# Horizon A — Pre-award sprint (now → 31 Aug 2026)

## A1 — Per-path test generation

**Why first:** one test per *architectural path* instead of one per
*instruction*. Measured: 14× for `lw`, +76 spans from that instruction alone.
It is also the honest answer to "does the framework find scenarios by itself" —
for one whole class, it would.

| Unit | Task (tangible output) |
|---|---|
| isla-gen | Per-path trap expectation: the emitter records the trap the solved path actually produced, instead of taking one `--expect-trap-cause` for the whole run. Output: a generated `.s` for a faulting path that asserts its own mcause. |
| isla-gen | Negative control for the above: a path test whose expected mcause is altered must fail on Sail **and** Spike. |
| python-isla | `opcode_sweep.py` gains an all-paths mode (currently hard-codes `-n 1` at line 371), with a per-instruction path cap so one pathological instruction cannot blow up the corpus. |
| python-isla | Corpus-cost measurement: tests generated, wall-clock, and disk, for one extension with and without all-paths. Decides the cap. |
| measurement | Coverage delta on a named subset, before vs after, replayed on the coverage build. |

**Milestone complete when:** a named extension generates N>1 tests per
instruction, the coverage delta is *measured and recorded*, **and** a mutated
expected-mcause makes a path test fail. The last clause is the point — without
it, "more tests" is satisfied by more tests that check nothing.

**Risk:** the Rust change is the largest unknown in this plan. I have read the
relevant code but not written it. If per-path trap detection turns out to need
information isla discards before emit, this becomes a much larger job — fallback
is to ship all-paths for *non-faulting* paths only, which still captures a real
share of the gain.

## A2 — A defensible privileged coverage number

**Why:** the RFP's **#1** evaluation criterion is privileged coverage
specifically. We have been quoting a whole-scope number, which answers a
different question.

| Unit | Task (tangible output) |
|---|---|
| python-isla | `rfp-scope-privileged.txt` — **done**, written with its exclusions justified in-file |
| measurement | Recompute after the full sweep lands, with a fresh replay rather than stale coverage data |
| measurement | Recompute again after A1, and report both, so the lever's effect is visible |
| docs | Write-up: the number, the scope rule, and the two decisions that deliberately understate it |

**Milestone complete when:** the number is computed from a clean full sweep, and
the write-up states how it could be wrong.

**Note:** the scope file deliberately *includes* `sys/pma.sail` (which we barely
cover) and deliberately *excludes* the privileged instructions living inside
`base_insts.sail` (which we do cover). Both choices lower the number.

## A3 — PMA scenario harness

**Why:** named in the RFP's required M-mode list, and at 15.5%. Far more
tractable than it looked — PMA attributes are set per region in the config JSON,
and the config-override machinery already exists.

| Unit | Task (tangible output) |
|---|---|
| python-isla | Region-attribute violation tests: AMO to a region with `atomic_support: AMONone`; LR/SC to `reservability: RsrvNone`; misaligned access to a region whose `misaligned_exceptions` say `AccessFault` |
| python-isla | A negative control per test — same access against a permissive region must pass |
| measurement | `sys/pma.sail` coverage before and after |

**Milestone complete when:** each test has a passing negative control, and the
`pma.sail` delta is recorded.

## A4 — Interrupt delivery

**Why:** the other named-but-empty required item, at 15.7%. Machinery exists
(`--pending-interrupt`, the trap handler, CSR preloading); what is missing is
delivery and delegation.

| Unit | Task (tangible output) |
|---|---|
| python-isla | Deliver an interrupt with `mie`/`mip` set and assert it lands with the right cause in the right mode |
| python-isla | Delegated variant via `mideleg`: assert it lands in S-mode, not M-mode |
| python-isla | Negative control: interrupt pending but disabled in `mie` must **not** trap |

**Milestone complete when:** the delegated and non-delegated cases land in
*different* modes, and the disabled-interrupt control does not fire.

## A5 — Per-ELF coverage

**Why:** closes RFP Goal 6 outright ("a measure of coverage of an individual ELF
*and* for all the ELFs"). Currently only the suite-level half exists —
`coverage_report.py` clears the coverage file once and replays everything into it.

| Unit | Task (tangible output) |
|---|---|
| python-isla | Per-ELF span capture, and a report of the highest-value ELFs by unique spans contributed |

**Milestone complete when:** a named ELF's individual coverage can be printed,
and the per-ELF sum reconciles with the suite total.

## A6 — The proposal

**This is the actual 31 August deliverable.** Everything above exists to make it
credible.

| Unit | Task (tangible output) |
|---|---|
| proposal | Technical plan, written against Horizon B below |
| proposal | Estimated timeline and cost — **cost needs a real rate from the user; I will not invent one** |
| proposal | Evidence section: measured coverage, the findings with reproducers, the merged/open model fixes |
| proposal | RFP compliance mapping — every Goal, Deliverable and Consideration to where it is addressed |

**Milestone complete when:** every number in it traces to a recorded run.

---

# Horizon B — Post-award build

Ordered by RFP deliverable, so progress maps to the contract.

## B1 — Configuration matrix (Goal 4)

The model's CI builds 16 configs; we run 2. isla cannot follow — its `B129`
bitvector cannot represent VLEN 256 or 512 — but the oracle runs the real model
and has no such limit.

Tasks: parameterise the oracle over the matrix; per-config reporting; document
the isla ceiling as a known, principled boundary rather than an omission.

## B2 — Coverage-guided generation (Goal 6 / Consideration F)

Today `coverage_report.py --uncovered` writes a list and **a human reads it**.
Closing that loop for one well-understood family — PTE permission variants, given
the walker sits at 5% — turns scenario discovery from manual to automatic for
that class.

## B3 — CI integration (Deliverable 4)

Approved and merged PRs into the Golden Model's CI. `regression.py` already
exits 1 on regression, so the machinery exists; the wiring and the PR do not.
**Gated on maintainer review cadence, which is outside our control** — start the
conversation early rather than treating it as a final step.

## B4 — Upstream model fixes (Deliverable 5)

A4 (shadow-stack) is fixed and open on our fork. A1 (PMP default-deny
divergence) is the next candidate. **The RFP wants these merged upstream**;
whether to push beyond the fork is the user's decision, not mine.

## B5 — Developer documentation (Deliverable 1)

The RFP asks specifically for developer-facing docs "to enable long-term
maintenance by the Golden Model community". User-facing docs are strong; this
half is thinner.

## B6 — Extendability follow-ups (Consideration D)

Hypervisor, FP and vector are future iterations. Evidence that we can extend
already exists (FP and V generators run today). For hypervisor, check upstream
PR #612 — an orphaned draft whose maintainer invited pickup — before proposing
anything from scratch.

---

## Completion Tracker

Split by who owns the blocker, because mixing them invites the wrong conclusion.

### Ours to finish

| Milestone | RFP unit | Horizon |
|---|---|---|
| A1 Per-path generation | Criterion 1, Goal 6 | Pre-award |
| A2 Privileged coverage number | Criterion 1 | Pre-award |
| A3 PMA harness | Goal 5 (required) | Pre-award |
| A4 Interrupt delivery | Goal 5 (required) | Pre-award |
| A5 Per-ELF coverage | Goal 6 | Pre-award |
| A6 Proposal | "Responding to this RFQ" | Pre-award |
| B1 Config matrix | Goal 4 | Post-award |
| B2 Coverage-guided generation | Goal 6 | Post-award |
| B5 Developer docs | Deliverable 1 | Post-award |
| B6 Extendability | Consideration D | Post-award |

### Blocked on someone else

| Item | Owner | Note |
|---|---|---|
| B3 CI integration merged | sail-riscv maintainers | We can open the PR; we cannot merge it |
| B4 Model fixes merged upstream | sail-riscv maintainers | Fork PR exists; upstreaming is the user's call |
| Proposal cost figure | User | Needs a real rate — will not be invented |

## Deliverables → Where They Land

| # | RFP Deliverable | Delivered |
|---|---|---|
| 1 | Repo + user and developer docs | Exists; developer half in **B5** |
| 2 | Example scripts to generate | Exists |
| 3 | Example scripts to run + summarise | Exists (Sail, Spike, QEMU, CVA6) |
| 4 | Merged CI PRs | **B3** |
| 5 | Model-fix PRs | **B4** (A4 already open on the fork) |

## Known Risk, Stated Plainly

**A1's Rust change is the biggest unknown.** Read but not written. Fallback:
all-paths for non-faulting paths only.

**Corpus growth.** 14× on one instruction. Across ~1280 instructions this has
real runtime and disk cost, and a full sweep already takes hours. The
path-cap task exists to bound it, but the right cap is not yet known — it is
measured in A1, not guessed here.

**The 31 August deadline is external and hard.** If the sprint slips, A3 and A4
are the first cuts: they are narrow features, whereas A1 and A2 change what the
proposal can claim.

**Session cadence is unverified.** See the sizing caveat above. This is the
single largest source of schedule uncertainty and it is not a technical one.

**Not covered by this plan:** cost and rates; whether to upstream beyond the
fork; anything requiring maintainer response times.

## What unused time does

If the sprint runs ahead, the time goes to **B2 (coverage-guided generation)**,
pulled forward from post-award. It is the item that most strengthens the
proposal's central claim — that the framework derives tests from the model
rather than from us — and it is the natural continuation of A1.
