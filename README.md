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


---

# Getting started

## 1. Clone

```bash
git clone --recurse-submodules https://github.com/10x-Engineers/riscv-test-generation.git
cd riscv-test-generation
```

`--recurse-submodules` is required. Without it the submodules arrive empty and
nothing will run.

| Folder | Repo | Purpose |
|---|---|---|
| `sail-riscv/` | `sail-riscv-testgen` @ `riscv-testgen-support` | the Golden Model |
| `isla-gen-extension/` | `isla-testgen` | symbolic engine (carries a nested `isla` submodule) |
| `autotest/` | `sail-riscv-autotest` | concrete oracle |

The folder names deliberately differ from the repo names — worth knowing before
you go looking for them on GitHub.

Already cloned without submodules?

```bash
git submodule update --init --recursive
```

## 2. Prerequisites

| Tool | Needed for | Notes |
|---|---|---|
| RISC-V cross-compiler | everything | `riscv64-unknown-elf-gcc`, or Clang with a RISC-V target |
| Z3 | the symbolic engine | loaded at runtime via `LD_LIBRARY_PATH` |
| Rust toolchain | building `isla-testgen` | |
| CMake | building the model | |
| Spike | **differential testing** | the independent check — see "Why Spike matters" below |
| QEMU, Whisper | optional extra targets | |

## 3. Build the three pieces

**The Golden Model simulator**

```bash
cmake -B sail-riscv/build -S sail-riscv
cmake --build sail-riscv/build -j$(nproc)
```

**A coverage-instrumented model**, kept separate so a coverage run can never
silently use an uninstrumented emulator:

```bash
cmake -B sail-riscv/build-coverage -S sail-riscv -DCOVERAGE=ON
cmake --build sail-riscv/build-coverage -j$(nproc)
```

**The symbolic engine**

```bash
cd isla-gen-extension
LIBRARY_PATH=/path/to/z3/lib cargo build --release --bin isla-testgen
cd ..
```

## 4. Check that everything was found

```bash
python3 python-isla/paths.py
```

Prints where each tool resolved to and flags anything missing. Every entry can
be overridden with the environment variable of the same name:

```bash
export SAIL_RISCV=/path/to/sail-riscv
export SPIKE_BIN=/path/to/spike
export Z3_LIB_DIR=/path/to/z3/lib
```

`SAIL_RISCV` prefers whichever checkout is actually **built**, so a fresh clone
uses the submodule and an existing sibling checkout keeps working — neither
needs configuration.

---

# Running things

All commands below are run from the repository root unless stated otherwise.

## Generate and run tests for one extension

The instruction list comes from the model's own `.sail` sources, so you name a
model file, not a list of instructions:

```bash
cd python-isla
python3 opcode_sweep.py extensions/I/base_insts.sail --xlen 64
```

This parses every instruction that file implements, generates a self-checking
test for each, and runs them on every simulator it can find, printing a
per-instruction verdict.

Useful flags:

```bash
--xlen {32,64}          which XLEN to generate for
--only add,sub,xor      restrict to specific mnemonics
--extension Zicsr       label the run and pick the right -march/--isa string
--out-dir DIR           where the ELFs land
--boot-fixed-entry      for QEMU / CVA6, which ignore the ELF entry point
```

## Sweep everything

```bash
cd python-isla
python3 sweep_all.py                 # every extension, both XLENs
python3 sweep_all.py I M A           # just these
python3 sweep_all.py privileged      # a named group
```

Groups: `M5`, `M6`, `M7`, `privileged`.

A full sweep takes hours and writes one result file per target, so an
interrupted run resumes rather than restarting:

```bash
SWEEP_OUT=~/.cache/riscv-sweep/elfs SWEEP_RESUME=1 python3 sweep_all.py
```

## Privileged scenario tests

These construct architectural *state* rather than exercising one instruction —
PMP violations, Sv39 page-table walks, PMA region attributes, interrupt
delivery and delegation:

```bash
cd python-isla
python3 scenario_tests.py --xlen 64
python3 scenario_tests.py --xlen 64 --only m4_amo_unsupported
```

**Every scenario carries a negative control that must fail.** The runner checks
both directions, so a control that stops failing is reported as a regression
rather than passing quietly.

## The concrete oracle (FP, vector, scalar crypto)

Used where the symbolic engine structurally cannot go. Run from `autotest/`:

```bash
cd autotest
PYTHONPATH=src python3 -m sailtest.cli backends      # list what is available

PYTHONPATH=src python3 -m sailtest.cli generate \
    --config configs/rv64.json --sail-riscv ../sail-riscv \
    --backend model-fp --count 8 --out out

PYTHONPATH=src python3 -m sailtest.cli run \
    --manifest out/rv64/manifest.json --config configs/rv64.json
```

Backends: `model`, `model-fp`, `model-scalar`, `model-v`, `model-vk`,
`model-vk64`, `template`.

Generation is reproducible — the same `--seed` produces byte-identical `.S`
sources.

## CSR sweep

Runs all six Zicsr instructions against each CSR in turn, rather than once
against one register:

```bash
cd python-isla
python3 csr_sweep.py --xlen 64 --from-model    # all 172 CSRs the model defines
python3 csr_sweep.py --xlen 64 --only mscratch,mepc
```

---

# Measuring and reporting

## Coverage of the Sail model

The RFP's own metric — how much of the Golden Model's source the tests actually
reach. Needs the coverage build from step 3.

```bash
cd python-isla
python3 coverage_report.py                                  # replay + report
python3 coverage_report.py --no-replay                      # reuse existing data
python3 coverage_report.py --scope rfp-scope-privileged.txt # privileged only
python3 coverage_report.py --per-elf out.txt                # per individual ELF
python3 coverage_report.py --uncovered todo.txt             # what is not reached
```

Three scope files, because a coverage number means nothing without saying what
it was measured over:

| File | Covers |
|---|---|
| `rfp-scope-privileged.txt` | the privileged architecture only — the RFP's #1 criterion |
| `rfp-scope-current.txt` | the current deliverable (excludes deferred FP/vector) |
| `rfp-scope.txt` | the full RFP scope |

Each file justifies its exclusions in comments. **Always report the scope with
the number.**

## Did anything get worse?

```bash
cd python-isla
python3 regression.py            # exits 1 if anything regressed
python3 regression.py --update   # accept current state as the new baseline
```

Compares each instruction's *status* against a baseline, not a pass/fail count.
A test moving from *verified* to *passes-but-checks-nothing* is a serious
regression that a pass/fail count records as no change at all — that is not
hypothetical, it happened to 47.7% of this corpus once.

Only `--update` after reading the diff. It makes whatever is on disk the new
definition of correct, including any regression in it.

## What passed, what failed, and whose fault is it

```bash
cd python-isla
python3 status_report.py
python3 status_report.py --extension V
python3 status_report.py --failures-only
```

Every failure is attributed from its recorded reason:

| | |
|---|---|
| **MODEL** | the Golden Model is wrong, or disagrees with Spike — a finding worth reporting upstream |
| **FRAMEWORK** | our generator cannot produce a valid test — ours to fix |
| **ROUTED** | the symbolic engine cannot generate it and another backend covers it — not a failure |
| **EXPECTED** | the test asserts a trap and got one |

This split is the single most useful distinction on the project, and the ratio
is easy to get backwards.

---

# Two things to understand before quoting results

## Why Spike matters

For oracle-generated tests, the expected values **come from** running the model.
Checking them again on the model is circular — that run passes by construction.
It still catches harness bugs, but it says nothing about the model.

**Only the Spike run is independent evidence.** The runner labels both columns
for exactly this reason. The symbolic flow does not have this problem, because
there the expected values come from symbolic execution.

## A passing test is not evidence that anything was checked

47.7% of this corpus once passed while comparing **empty** expected-state
tables. Every test was green. The defect was invisible precisely because the
suite was passing.

That is why negative controls are mandatory, and why `documentation/python-isla/findings.md`
records the reproducer for every claim.

---

# Documentation

| Document | What |
|---|---|
| [`EXECUTION_ROADMAP.md`](documentation/EXECUTION_ROADMAP.md) | architecture, the 0→100 plan, gap analysis, validation strategy |
| [`findings.md`](documentation/python-isla/findings.md) | every defect found, each with an executed reproducer |
| [`QUICKSTART.md`](documentation/QUICKSTART.md) | shorter task-oriented walkthrough |
| [`DELIVERY_PLAN.md`](documentation/DELIVERY_PLAN.md) | two-horizon delivery plan |

## License

Apache-2.0 — the RFP's preferred licence for new repositories.
