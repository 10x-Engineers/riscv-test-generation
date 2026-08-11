# riscv-test-generation

Automatic RISC-V test generation from the **Sail RISC-V Golden Model** — the working repo for
our response to the RISC-V International RFP *"Automatic Test Generation using the Sail RISC-V
Golden Model"* (**v1.1**). Proposal deadline is **Monday, 31 August 2026** — extended from the
original 31 July, and confirmed against the v1.1 document. That date is for the *proposal*
(technical plan + estimated timeline + cost, to `tech-proposals@riscv.org`), not for the
framework; deliverables 1–5 are post-award.

## Recommended approach

A **Python-first, coverage-directed hybrid**:

- a config-driven **concrete-oracle backbone** (run the Golden Model, bake expected state into
  self-checking ELFs) for breadth across the whole **privileged** ISA;
- an **ISLA symbolic-execution specialist** for the hard privileged corners random stimulus
  can't reach (specific PMP violations, precise trap-delegation, chosen page-fault causes);
- both **steered by, and measured against, Sail-code coverage** (`sail-riscv`'s own
  `COVERAGE`/`--c-coverage`/`sail_coverage`) — exactly the "coverage in terms of the Sail code"
  the RFP's #1 evaluation criterion asks for.

Full reasoning, RFP compliance mapping, sprint, and post-award roadmap:
[`documentation/EXECUTION_PLAN.md`](documentation/EXECUTION_PLAN.md).

## Where it stands

All three backends are built and running on RV32 and RV64, against both
`sail_riscv_sim` and Spike:

| | |
|---|---|
| Instructions parsed from the model | 1280, across 23 extensions, zero unparsed clauses |
| Sail branch coverage | **79.6%** combined; **71.8%** within the current deliverable's declared scope |
| Failures in scope | **2** — PMP entry 0, on the model's side |
| Framework failures in scope | **0** |

The two are finding **A2** (PMP entry 0's reset value differs from Spike) in
[`documentation/python-isla/findings.md`](documentation/python-isla/findings.md).
That file is the deliverable's real output: defects found by generating and
*running* tests, each with a reproducer that was executed. It also carries **A4**,
a two-part defect that made the Golden Model's shadow-stack code unreachable —
found here, fixed, and open as a PR on our fork.

To see the current state yourself:

```
cd python-isla
python3 regression.py        # what got worse since the baseline (exit 1 if anything did)
python3 status_report.py     # per-extension pass/fail, split by model fault vs ours
```

Hypervisor, floating point and the vector extensions are deferred to a follow-up
RFQ iteration. They are still generated and run — being able to extend to them is
itself a requirement — but they are reported separately rather than folded into
the current deliverable's numbers.

## Quickstart

Want to just generate and run tests? See [`documentation/QUICKSTART.md`](documentation/QUICKSTART.md).
In short:

```
cd python-isla
python3 opcode_sweep.py extensions/I/base_insts.sail --xlen 32
```

parses every instruction the given Sail model file(s) actually implement, generates a
self-checking test per instruction via `isla-testgen`, and runs each one on every simulator found
on `PATH` (Sail, Spike, and — with `--boot-fixed-entry` — QEMU and CVA6 RTL), reporting a
pass/fail summary.

## Layout

| Path | What |
|---|---|
| `isla-gen-extension/` | submodule → our `isla-testgen` fork (symbolic-specialist evidence; RV32+RV64, incl. PMP, ACT4-compatible signature mode, and QEMU/CVA6-compatible boot mode) |
| `python-isla/` | the model-sourced opcode driver: parses the Sail model's own instruction definitions, generates tests, runs them across Sail/Spike/QEMU/CVA6 — see [`documentation/python-isla/model-sourced-generation.md`](documentation/python-isla/model-sourced-generation.md) |
| `autotest/` | submodule → `sail-riscv-autotest` (Apache-2.0 oracle baseline) |
| `documentation/` | the master plan, RFP compliance, per-approach docs + diagrams — start at [`QUICKSTART.md`](documentation/QUICKSTART.md) for usage, [`EXECUTION_PLAN.md`](documentation/EXECUTION_PLAN.md) for the plan |
| `comparison/` | the three-approach evaluation feeding the recommendation |
| `experiments/` | the coverage + privileged-canary spikes (proposal evidence) |
| `PROPOSAL/` | the RFP response drafted here (original deadline 31 Jul, since extended) |

## Clone

```
git clone --recurse-submodules https://github.com/10x-Engineers/riscv-test-generation.git
```

## License

Apache-2.0 (RFP-preferred for new repositories).

## Clone and set up

```bash
git clone --recurse-submodules https://github.com/10x-Engineers/riscv-test-generation.git
```

`--recurse-submodules` is required — without it the three submodules arrive empty.

| Submodule | Repo | Purpose |
|---|---|---|
| `sail-riscv/` | `10x-Engineers/sail-riscv-testgen` @ `riscv-testgen-support` | the Golden Model |
| `isla-gen-extension/` | `10x-Engineers/isla-testgen` | symbolic engine (carries a nested `isla` submodule) |
| `autotest/` | `10x-Engineers/sail-riscv-autotest` | concrete oracle |

The folder names differ from the repo names, which is worth knowing before you
go looking for them on GitHub.

### Where things are found

Paths are resolved by [`python-isla/paths.py`](python-isla/paths.py) rather than
hardcoded. To see what resolved to what on your machine:

```bash
python3 python-isla/paths.py
```

Every entry can be overridden with the environment variable of the same name
(`SAIL_RISCV`, `SPIKE_BIN`, `ISLA_TESTGEN_BIN`, `Z3_LIB_DIR`, …), so CI and
other machines need no source edits.

`SAIL_RISCV` prefers whichever checkout is actually *built* — the submodule for
a fresh clone, or an existing sibling checkout for a developer who already has
one. Neither case needs configuration.
