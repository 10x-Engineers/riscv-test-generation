# Coverage-Risk Matrix — de-risk by probing hard canaries, not by counting instructions

## The method

There are hundreds of RISC-V instructions but only **~12 classes by *generation
difficulty***. Don't enumerate instructions — for each class, pick the **hardest
representative** (its "canary") and probe *that* first, for each approach, during the sprint.

A **"blocked — here's why"** result is a *successful* de-risking outcome: it maps the boundary
while you still have decision latitude, before committing the approach in the proposal. This is
what converts the open worry *"can this approach cover all the privileged instructions?"* into
a tracked, evidence-based checklist.

## The key asymmetry (why this favours the hybrid)

- **Oracle (concrete):** coverage reduces to *"can the Golden Model execute it?"* — and by
  construction it can. So the oracle has a near **a-priori coverage guarantee**: model-runs-it
  ⇒ we can generate a conformance test. **No class is fundamentally blocked** — its worst rows
  are *"needs precondition-setup machinery"* (VM, PMP) or *"needs special handling for
  nondeterminism"* (interrupts, timers). That's engineering effort, not a capability wall.
- **ISLA (symbolic):** coverage is **empirical** — *"can isla symbolically execute this Sail
  path and solve the constraints without blowing up or hitting an engine gap?"* — and it has
  **three genuinely risky rows: PMP, Virtual memory / PTW, and Vector.** A post-commitment
  blocker, if one comes, comes from these.

The classes ISLA *can't* guarantee are exactly the ones the oracle covers *by construction* —
which is the core argument for the recommended hybrid (oracle backbone + ISLA specialist).

## The matrix (pre-populated with what this cycle already proved)

| Instruction class | Oracle (concrete) | ISLA (symbolic) | Canary to probe first | Status |
|---|---|---|---|---|
| Base ALU (add/shift/imm) | trivial | **proven** (`addi`) | — | ✅ proven (ISLA) |
| Branch / jump | easy | easy | — | ◻ untested |
| Load/store (aligned) | easy | moderate (symbolic addr) | load w/ symbolic base | ◻ untested |
| CSR access (Zicsr) | easy | **proven** after de-scatter (`csrrw`) | — | ✅ proven (ISLA) |
| **PMP** | moderate (config setup) | **hard** — hit struct/vector engine bug; fixed for read, Spike pending | pmpcfg write + protected access | 🟠 partial (ISLA read; Spike gap) |
| Traps / exceptions | moderate (check via handler) | moderate (path branching) | illegal instr, misaligned load | ◻ untested |
| **Virtual memory / PTW** | heavy (page-table setup) | **hardest** — memory-dependent loops → path explosion | **probe earliest on ISLA** | ◻ untested — highest ISLA risk |
| Atomics (A / LR-SC) | moderate | moderate (reservation stubbed) | LR/SC pair | ◻ untested (reservation primops stubbed) |
| Float / Double (F/D) | easy (mechanical) | moderate (fp primops) | — | ◻ untested |
| **Vector (V)** | moderate (state size) | **hard** (vector + struct symbolic) | any vector op | ◻ untested — high ISLA risk |
| Interrupts / timers | special (nondeterministic → mask) | hard (nondeterminism) | timer interrupt | ◻ untested |
| Fence / WFI / system | trivial (mostly no-ops) | easy | — | ◻ untested |

**Legend:** ✅ proven · 🟠 partial · ◻ untested · ⛔ blocked (record the reason if reached).

## Actionable directives for the sprint

1. **On the ISLA track, probe VM/PTW and Vector on day one of touching them** — these are the
   walls. Fail fast where the boundary is, while there's still latitude to scope ISLA as a
   specialist rather than the backbone.
2. **On the oracle track, there is nothing to "prove can it"** — instead *measure the
   setup-machinery cost* for each privileged class (VM page tables, PMP config, privilege
   transitions) and the *nondeterminism handling* (interrupts, timers, entropy). That is the
   oracle's real risk axis.
3. **Every probe result updates the Status column** with concrete evidence (a verified ELF, a
   coverage number, or a "blocked because X"). The filled-in matrix is a proposal artifact:
   it demonstrates coverage feasibility across the privileged ISA, per RFP evaluation
   criterion #1.

## The honest asterisk on "covers all instructions"

"Can run each instruction once with a self-check" (the oracle bar) ≈ **yes**. But
"meaningfully exercises the *interesting behaviour*" — a specific PMP violation, a particular
page-fault cause, a precise trap-delegation path — still needs *directed* input construction,
which is where the ISLA specialist earns its place. So even for privileged coverage the split
holds: **the oracle guarantees you hit every instruction; ISLA is what makes the hard
privileged corners deep rather than incidental.**
