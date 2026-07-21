# riscv-test-generation

Automatic RISC-V test generation from the **Sail RISC-V Golden Model** — the working repo for
our response to the RISC-V International RFP *"Automatic Test Generation using the Sail RISC-V
Golden Model"* (v1.0). Proposal due **31 July 2026** (internal target **29 July**).

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

## Layout

| Path | What |
|---|---|
| `isla-gen-extension/` | submodule → our `isla-testgen` fork (symbolic-specialist evidence; already enabled for RV32 incl. PMP) |
| `python-isla/` | the Python framework prototype (oracle backbone + coverage) |
| `autotest/` | submodule → `sail-riscv-autotest` (Apache-2.0 oracle baseline) |
| `documentation/` | the master plan, RFP compliance, per-approach docs + diagrams |
| `comparison/` | the three-approach evaluation feeding the recommendation |
| `experiments/` | the coverage + privileged-canary spikes (proposal evidence) |
| `PROPOSAL/` | the RFP response drafted here, submitted by 31 Jul |

## Clone

```
git clone --recurse-submodules https://github.com/10x-Engineers/riscv-test-generation.git
```

## License

Apache-2.0 (RFP-preferred for new repositories).
