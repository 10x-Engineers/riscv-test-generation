# riscv-test-generation

Automatic RISC-V test generation from the **Sail RISC-V Golden Model** — the working repo for
our response to the RISC-V International RFP *"Automatic Test Generation using the Sail RISC-V
Golden Model"* (**v1.1**). Proposal deadline is **Monday, 31 August 2026** — extended from the
original 31 July, and confirmed against the v1.1 document. That date is for the *proposal*
(technical plan + estimated timeline + cost, to `tech-proposals@riscv.org`), not for the
framework; deliverables 1–5 are post-award.

## The approach

**Routing is by extension.** Floating point and Vector route to the concrete Oracle; every other
extension routes to ISLA, which symbolically executes the model and uses Z3 to derive the operands
that reach a chosen behaviour. A small template backend covers branch and jump cases.

Because FP and Vector are deferred under the RFP, the Oracle leg is not used to generate tests in
the committed scope. Its job here is a different one:

- it **reads the branch structure of the Sail model** and derives the configurations that structure
  implies — PMP, PMA, exceptions, interrupts, translation;
- it **emits the assembly that sets each one up**, and that setup code is embedded in the object
  emitter, selected by flag.

Generating one test takes both legs. The scenario goes to the emitter as flags, ISLA solves the
operands, and the emitter writes a single program: the setup code, the instruction under test with
those operands, and the expected result. Each program runs on **both Sail and Spike** and the two
results are compared. Coverage is collected from the instrumented model, and the scenarios it shows
as missing return to the generation engine, which derives a case for each.

**The proposal**, with the full flow and diagrams:
<https://claude.ai/code/artifact/f8ee98c6-d48f-40d0-8191-9a20b0b3502d>

Reasoning and RFP compliance mapping:
[`documentation/EXECUTION_PLAN.md`](documentation/EXECUTION_PLAN.md).

## A worked example, in the repo

`demo/pmp-lw-denied/` holds one case end to end: a load denied by PMP, generated, run, and
measured. Generate it with

```sh
isla-testgen -A riscv-ir/riscv64.ir -C riscv-ir/riscv64.toml -a riscv64 \
  --memory-region 0x80020000-0x80030000 -o lw_pmp_read_denied -n 1 \
  0x80020137 0x02011113 0x02015113 0x00012083 \
  --pmp-deny 0x80020000 --expect-trap-cause 5 --trap-is-pass
```

then run it on both simulators:

```sh
sail_riscv_sim --config <cfg>.json lw_pmp_read_denied.elf   # SUCCESS
spike --isa=rv64imac_zicsr        lw_pmp_read_denied.elf   # exit 0
spike --isa=rv64imac_zicsr        NEG_wrong_cause.elf      # FAILED, exit 1 — the negative control
```

The negative control is the point: the same test with the expected cause changed must fail, or a
passing run proves nothing. Replaying the corpus on the instrumented model and regenerating the
testplan moves PMP from **81/226 spans** (one `lw`) to **85/226** (adding sw, lr, sc and amo) —
which says the permission check distinguishes access kinds and little else in that region, so the
way to move PMP coverage is to vary the address relationship and the configuration. That is what
the derived cases in `cases/*.toml` do.

## Where it stands

Measured **24 August 2026** from one clean run of the whole corpus — 1,878 ELFs,
1,810 reaching SUCCESS, 107 seconds. Reproduce any row with the command beside it.

| Scope | Branch spans | All spans | Command |
|---|---|---|---|
| Whole model | **77.5%** | 60.9% | `coverage_report.py` |
| Full RFP scope | **77.6%** | 61.0% | `--scope rfp-scope.txt` |
| Current deliverable | **66.3%** | 48.3% | `--scope rfp-scope-current.txt` |
| Privileged only | **70.5%** | 44.5% | `--scope rfp-scope-privileged.txt` |

All four apply `--exclude-spans` (see Stage 4). **Always quote the scope with the number** —
the four differ by up to eleven points and none of them is "the" coverage figure.

| | |
|---|---|
| Instructions parsed from the model | 1280, across 23 extensions, zero unparsed clauses |
| Corpus | 1,878 ELFs |
| Testplan items | 2,347 — 1,035 complete, 520 partial, 414 not started |
| Coverage ceiling | **94.2%** — see [`TESTPLAN.md`](documentation/python-isla/TESTPLAN.md) |
| Failures in scope | **2** — PMP entry 0, on the model's side |
| Framework failures in scope | **0** |

The two are finding **A2** (PMP entry 0's reset value differs from Spike) in
[`documentation/python-isla/findings.md`](documentation/python-isla/findings.md).
That file is the deliverable's real output: defects found by generating and
*running* tests, each with a reproducer that was executed. It also carries **A4**,
a two-part defect that made the Golden Model's shadow-stack code unreachable —
found here, fixed, and open as a PR on our fork.

Hypervisor, floating point and the vector extensions are deferred to a follow-up
RFQ iteration. They are still generated and run — being able to extend to them is
itself a requirement — but they are reported separately rather than folded into
the current deliverable's numbers.

---

# The pipeline

Seven stages, and the last one closes the loop back to the first.

```
   Configuration ──► Generation ──► Simulation ──┬──► Coverage report ──► Feedback & record
        ▲                                        │
        │                                        └──► Failure reports ──► Triage & debug
        │
        └──────── Coverage scripts identify holes and reconfigure ◄────────┘
```

| # | Stage | Runs it | Automated |
|---|---|---|---|
| 1 | Configuration | Sail JSON configs + two CMake builds | yes |
| 2 | Generation | `opcode_sweep.py` / `sailtest generate` | yes |
| 3 | Simulation | `sail_riscv_sim`, Spike, QEMU, CVA6 RTL | yes |
| 4 | Coverage report | `coverage_report.py` | yes |
| 5 | Failure reports | `status_report.py`, `regression.py` | yes |
| 6 | Feedback & record | `testplan.py` | yes |
| 7 | Identify holes → reconfigure | testplan directives | **manual by design** |

Stage 7 is deliberately a human decision. The uncovered list and its classification are
produced automatically and published as a machine-readable interface precisely so an
automatic consumer can be added later without redesigning what produces it; building that
consumer is not in the committed scope. See §2.8 of the technical plan.

---

## Stage 1 · Configuration

One Golden Model config drives everything downstream — instruction set, register widths,
which extensions exist, what the assembler is told.

**Two builds, kept separate** so a coverage run can never silently use an uninstrumented
emulator:

```bash
# the fast simulator that runs tests
cmake -B sail-riscv/build -S sail-riscv -DCMAKE_BUILD_TYPE=Release
cmake --build sail-riscv/build -j$(nproc)

# the instrumented one, plus the span manifest everything downstream measures against
cmake -B sail-riscv/build-coverage -S sail-riscv -DCMAKE_BUILD_TYPE=Release -DCOVERAGE=ON
cmake --build sail-riscv/build-coverage -j$(nproc)
```

The coverage build emits `sail-riscv/build-coverage/sail_riscv_model.branch_info` — the
**15,157-span manifest** that is the denominator for every coverage figure in this repo.

**Configs.** The model's build generates all 16 of its CI configurations:

```bash
ls sail-riscv/build/config/*.json     # rv{32,64}d × VLEN {64,128,256,512} × ELEN {32,64}
```

We currently generate against **two** — `rv32d_v128_e64` and `rv64d_v128_e64`. VLEN is pinned
to 128 because it is a *compile-time* type in Sail (`type vlenbits = bits(vlen)`) and ISLA's
concrete bitvector caps at 129 bits, so it cannot represent VLEN 256 or 512. The oracle has no
such limit; widening it across the matrix is the largest open item against RFP Goal 4.

**Check the toolchain resolved correctly:**

```bash
python3 python-isla/paths.py
```

Prints where each tool was found and flags anything missing. Every entry is overridable:

```bash
export SAIL_RISCV=/path/to/sail-riscv
export SPIKE_BIN=/path/to/spike
export Z3_LIB_DIR=/path/to/z3/lib
```

**Or let it set them for you.** `--export` emits the resolved paths as shell exports:

```bash
eval "$(python3 python-isla/paths.py --export)"
```

Two of those do real work beyond documentation: `LD_LIBRARY_PATH` gains the Z3 directory,
because `isla-testgen` loads Z3 at run time rather than linking it, and `PATH` gains the
toolchain directory so the assembler and linker resolve. A path that does not exist is emitted
as a comment rather than an export, so a wrong value is never pinned and the fallback order
still applies next run; `--include-missing` overrides that.

`SAIL_RISCV` prefers whichever checkout is actually **built**, so a fresh clone uses the
submodule and an existing sibling checkout keeps working — neither needs configuration.

---

## Stage 2 · Generation

Two engines, routed by capability. Both emit the same contract: a `.S` source, a linked
`.elf`, and a `manifest.json` — self-checking (`a0 == 0` means pass) with
`riscv-arch-test`-compatible headers and a signature region.

### Symbolic — one instruction, solved

The instruction list comes from the model's own `.sail` sources, so you name a **model file**,
not a list of instructions:

```bash
cd python-isla
python3 opcode_sweep.py extensions/I/base_insts.sail --xlen 64
```

It parses every instruction that file implements, renders one legal instance as assembly,
assembles it with the real toolchain to obtain the true opcode word, hands that to
`isla-testgen` + Z3 to solve for a reachable path, then **runs the result** — generation not
erroring is not proof, since a self-targeting `jal` "succeeds" at generation and hangs forever
when executed.

```bash
--xlen {32,64}                   which XLEN to generate for
--only add,sub,xor               restrict to specific mnemonics
--extension Zicsr                label the run; picks the -march/--isa string
--out-dir DIR                    where the ELFs land
--boot-fixed-entry               for QEMU / CVA6, which ignore the ELF entry point
--isla-arg=--run-in-supervisor   pass a flag through to isla-testgen (note the `=`)
```

The `=` form is required for any pass-through value beginning with `--`; without it argparse
consumes it as a flag of its own.

**Batch drivers:**

```bash
python3 sweep_all.py                 # every extension, both XLENs
python3 sweep_all.py I M A           # just these
python3 sweep_all.py privileged      # a named group: M5, M6, M7, privileged

# a full sweep takes hours; an interrupted run resumes rather than restarting
SWEEP_OUT=~/.cache/riscv-sweep/elfs SWEEP_RESUME=1 python3 sweep_all.py
python3 sweep_status.py              # how far a running sweep has got
```

**CSR sweep** — all six Zicsr instructions against each CSR in turn, rather than once against
one register. Roughly ten privileged extensions define no mnemonics at all and exist only as
CSR addresses; this is the only place they are exercised:

```bash
python3 csr_sweep.py --xlen 64 --from-model      # every CSR the model defines
python3 csr_sweep.py --xlen 64 --only mscratch,mepc
```

**Scenario tests** — architectural *state* rather than a single instruction: privilege
transitions, PMP violations, Sv39 page-table walks, PMA regions, interrupt delegation:

```bash
python3 scenario_tests.py --xlen 64
python3 scenario_tests.py --xlen 64 --only m4_amo_unsupported
```

**Every scenario carries a negative control that must fail.** The runner checks both
directions, so a control that stops failing is reported as a regression rather than passing
quietly.

### Concrete oracle — sequences, observed

Used where the symbolic engine structurally cannot go. It runs a random instruction sequence
on the Golden Model and records the resulting architectural state as the expectation.

```bash
cd autotest
PYTHONPATH=src python3 -m sailtest.cli backends       # list what is available

PYTHONPATH=src python3 -m sailtest.cli generate \
    --config configs/rv64.json --sail-riscv ../sail-riscv \
    --backend model-fp --count 8 --out out

PYTHONPATH=src python3 -m sailtest.cli run \
    --manifest out/rv64/manifest.json --config configs/rv64.json
```

Backends: `model`, `model-fp`, `model-scalar`, `model-v`, `model-vk`, `model-vk64`,
`template`. Generation is reproducible — the same `--seed` produces byte-identical `.S`.

### Why two engines

ISLA cannot execute floating-point softfloat calls or represent vector state
(`Symbolic (bit)vector length in zeros`). The oracle cannot aim — it observes what a random
sequence happened to do. Vector is oracle-only by necessity; FP is oracle-by-measurement,
having been benchmarked both ways. See
[`documentation/python-isla/findings.md`](documentation/python-isla/findings.md).

---

## Stage 3 · Simulation

Generated ELFs run on up to four targets:

| Target | Role |
|---|---|
| `sail_riscv_sim` | the reference — but see the circularity warning below |
| **Spike** | **the independent check**; the only one that is evidence about the model |
| QEMU | a second independent implementation |
| CVA6 (Verilator) | RTL, for the ELFs that boot at a fixed entry point |

Most runs happen automatically as part of `opcode_sweep.py` and `sailtest run`. To replay a
corpus by hand:

```bash
sail-riscv/build/c_emulator/sail_riscv_sim \
    --config sail-riscv/build/config/rv64d_v128_e64.json test.elf
spike --isa=rv64i_zicsr test.elf
```

QEMU and CVA6 ignore the ELF entry point, which is why `--boot-fixed-entry` exists.

---

## Stage 4 · Coverage report

Replays every ELF on the instrumented simulator, accumulating `sail_coverage`, and ratios it
against `branch_info`.

```bash
cd python-isla
python3 coverage_report.py                                   # replay + report (~107s)
python3 coverage_report.py --no-replay                       # reuse existing data
python3 coverage_report.py --scope rfp-scope-privileged.txt  # privileged only
python3 coverage_report.py --per-elf out.txt                 # each ELF measured alone
python3 coverage_report.py --uncovered todo.txt              # what is not reached
python3 coverage_report.py --exclude-spans /tmp/excl.txt     # drop unreachable code
```

**Spans come in three kinds** and mean different things: `F` a function was entered, `B` a
branch was taken, `T` a finer-grained per-expression span. Quoting "coverage" without saying
which inflates or deflates by twenty points — `B` alone is 77.5% where all three together are
60.9%.

**Exclusions.** The denominator contains code no executing program can reach — chiefly Sail's
bidirectional `mapping` declarations, whose disassembly direction is real instrumented code
that nothing runs. Derive them, never hand-list them, because line numbers move:

```bash
python3 derive_span_exclusions.py -o /tmp/excl.txt --json /tmp/excl.json
```

Produces 1,050 ranges with a reason each. `coverage_report.py` prints how many it removed, so
the figure stays auditable rather than quietly improved.

**Scope files** — a coverage number means nothing without saying what it was measured over:

| File | Covers |
|---|---|
| `rfp-scope-privileged.txt` | the privileged architecture only — the RFP's #1 criterion |
| `rfp-scope-current.txt` | the current deliverable (excludes deferred FP/vector) |
| `rfp-scope.txt` | the full RFP scope |

Each justifies its exclusions in comments.

> **A failing test writes no coverage file at all.** Every negative control is invisible to
> this stage. Do not read a coverage gap as "no test exists" without checking the failure
> report too.

---

## Stage 5 · Failure reports

```bash
cd python-isla
python3 status_report.py                  # per-extension, with attribution
python3 status_report.py --extension V
python3 status_report.py --failures-only
python3 coverage_matrix.py                # per-instruction, across BOTH generators
```

Every failure is attributed from its recorded reason:

| | |
|---|---|
| **MODEL** | the Golden Model is wrong, or disagrees with Spike — worth reporting upstream |
| **FRAMEWORK** | our generator cannot produce a valid test — ours to fix |
| **ROUTED** | the symbolic engine cannot generate it and another backend covers it — not a failure |
| **EXPECTED** | the test asserts a trap and got one |

This split is the single most useful distinction on the project, and the ratio is easy to get
backwards. Promote a failure to MODEL only with a written-up reproducer in `findings.md`.

**Regressions:**

```bash
python3 regression.py            # exits 1 if anything regressed
python3 regression.py --update   # accept current state as the new baseline
```

Compares each instruction's *status*, not a pass/fail count. A test moving from *verified* to
*passes-but-checks-nothing* is a serious regression that a pass/fail count records as no
change at all — not hypothetical, it happened to 47.7% of this corpus once.

Only `--update` after reading the diff. It makes whatever is on disk the new definition of
correct, including any regression in it.

---

## Stage 6 · Feedback and record

Turns coverage into a plan: every target the model implements, what stimulus reaches it,
whether the corpus reaches it, and what to generate for the ones it does not.

```bash
cd python-isla
python3 derive_span_exclusions.py -o /tmp/excl.txt
python3 coverage_report.py                       # refresh status first (~107s)

python3 testplan.py \
    --coverage \
    --exclude-spans /tmp/excl.txt \
    --baseline ../documentation/python-isla/results/testplan.json \
    -o testplan --html --xlsx --fail-on-new-holes
```

Outputs `testplan.{md,csv,json,html,xlsx}` plus `testplan-spans.csv` (one row per span, the
audit trail).

**It refuses stale inputs.** A manifest older than the model describes the *previous* model;
a coverage log older than the manifest joins status onto spans that have since moved. Both
produce a plausible-looking plan, which is the dangerous kind, so both stop the run.
`--allow-stale` overrides deliberately.

**`--baseline` separates new holes from the standing backlog** — new hole, regression, widened,
closed — and `--fail-on-new-holes` exits non-zero on either, so a model bump fails CI rather
than quietly enlarging the backlog.

A lighter tool that classifies only the uncovered remainder:

```bash
python3 coverage_report.py --uncovered uncov.txt
python3 coverage_analysis.py uncov.txt --model-dir ../sail-riscv/model -o queue --xlsx
```

Full detail: [`documentation/python-isla/TESTPLAN.md`](documentation/python-isla/TESTPLAN.md).

---

## Stage 7 · Identify holes and reconfigure

The plan names each gap and the command that would close it — for example a helper function
resolved through the call graph to the instructions that reach it:

```
carryless_mul    core/arithmetic.sail:20    4 spans, 0 covered
  execute an instruction that calls this helper: CLMUL, CLMULH, VCLMUL_VV, ...
  opcode_sweep.py extensions/B/zbc_insts.sail extensions/vector_crypto/zvbc_insts.sail
```

Working the queue means picking a row, running its `generate` command, and re-measuring —
back to Stage 2. Not every gap is closed by generating more of the same: a representational
gap needs a routing change, a never-selected gap needs a suite-selection fix, and only "no
test generated for it" is closed by generation alone.

**This decision is a human one, by design.** The classification is automatic; acting on it is
not. The interface is machine-readable so an automatic consumer can be added later.

---

# Getting started

## 1. Clone

```bash
git clone --recurse-submodules https://github.com/10x-Engineers/riscv-test-generation.git
cd riscv-test-generation
```

`--recurse-submodules` is required. Without it the submodules arrive empty and nothing runs.

| Folder | Repo | Purpose |
|---|---|---|
| `sail-riscv/` | `sail-riscv-testgen` @ `riscv-testgen-support` | the Golden Model |
| `isla-gen-extension/` | `isla-testgen` | symbolic engine (carries a nested `isla` submodule) |
| `autotest/` | `sail-riscv-autotest` | concrete oracle |

The folder names deliberately differ from the repo names. Already cloned without submodules?

```bash
git submodule update --init --recursive
```

## 2. Prerequisites

| Tool | Needed for | Notes |
|---|---|---|
| **Sail compiler** | **building the model at all** | via opam; see below — the model will not configure without it |
| RISC-V cross-compiler | everything | `riscv64-unknown-elf-gcc`, or Clang with a RISC-V target |
| Z3 | the symbolic engine | loaded at runtime via `LD_LIBRARY_PATH` |
| Rust toolchain | building `isla-testgen` | |
| CMake | building the model | |
| Spike | **differential testing** | the independent check |
| QEMU, Verilator | optional extra targets | |
| `openpyxl` | `--xlsx` outputs | optional; skipped with a warning if absent |

### Sail, and the step that is easy to miss

The Golden Model is written in Sail and is compiled by the Sail compiler, so
`sail` must be **on `PATH` in the shell you build from**:

```bash
opam install sail          # first time only
eval $(opam env)           # every new shell -- this is the step that gets missed
sail --version             # must print a version before you continue
```

Without it, `cmake` fails at configure time with *"Sail not found"* and nothing
downstream exists. Verified by building a genuinely fresh clone, where this was
the first thing to break.

## 3. Build

**The Sail model, twice.** `CMAKE_BUILD_TYPE` is required — the model's CMakeLists has no
default and stops with *"No build type selected"* if you leave it out.

```bash
# the fast simulator that runs tests
cmake -B sail-riscv/build -S sail-riscv -DCMAKE_BUILD_TYPE=Release
cmake --build sail-riscv/build -j$(nproc)

# the instrumented one, plus the span manifest every coverage figure is measured against
cmake -B sail-riscv/build-coverage -S sail-riscv -DCMAKE_BUILD_TYPE=Release -DCOVERAGE=ON
cmake --build sail-riscv/build-coverage -j$(nproc)
```

Two builds, kept separate, so a coverage run can never silently use an uninstrumented emulator.
The second emits `sail-riscv/build-coverage/sail_riscv_model.branch_info`, the span manifest that
is the denominator for every coverage number here. A `Release` build alone gives you a runner but
no denominator.

If a configure attempt already failed, it leaves a `CMakeCache.txt` behind; re-running with the
flag normally just works, and `rm -rf sail-riscv/build` is the clean way out if CMake complains
about a stale cache.

**Then the symbolic engine:**

```bash
cd isla-gen-extension
LIBRARY_PATH=/path/to/z3/lib cargo build --release --bin isla-testgen
cd ..
```

## 4. Verify

```bash
python3 python-isla/paths.py
```

---

# Script reference

## `python-isla/` — the symbolic flow, and all measurement

| Script | What it does |
|---|---|
| `paths.py` | Resolves every tool and directory once, from `$ENV` → repo-relative → `PATH`. Import it rather than hardcoding paths; run it to see what resolved. |
| `model_opcodes.py` | Gets mnemonics and encodings straight from the Sail model rather than `riscv-opcodes`, so a sweep only ever tests what this model implements. |
| `model_config.py` | Parses one Golden Model configuration and derives everything downstream from it. |
| `test_config.py` | Writes what a generated test *requires* into a form a runner can read (`REQUIRED_EXTENSIONS`, `MARCH`, params). |
| `opcode_sweep.py` | **Stage 2.** Per-file opcode sweep: parse → render → assemble → solve → run. The single-extension tool; use it when debugging one instruction. |
| `sweep_all.py` | **Stage 2.** Batch driver over extension groups and both XLENs, resumable. |
| `sweep_status.py` | Reports how far a running `sweep_all.py` has got. |
| `csr_sweep.py` | **Stage 2.** Zicsr's six instructions × every CSR the model defines. |
| `scenario_tests.py` | **Stage 2.** Privileged scenarios — state setup, not single instructions. Each carries a negative control. |
| `coverage_report.py` | **Stage 4.** Replays the corpus, ratios `sail_coverage` against `branch_info`. Scope, exclusions, per-ELF, uncovered list. |
| `derive_span_exclusions.py` | **Stage 4.** Derives the spans no executing program can reach, with a reason each. Regenerate after any model change; line numbers move. |
| `status_report.py` | **Stage 5.** Per-extension pass/fail split by MODEL / FRAMEWORK / ROUTED / EXPECTED. |
| `regression.py` | **Stage 5.** Diffs status against a reviewable baseline; exits 1 on regression. |
| `coverage_matrix.py` | **Stage 5.** Per-instruction status across *both* generators in one table — neither number is honest alone. |
| `testplan.py` | **Stage 6.** Derives the full testplan from the model's branch structure; stale-input guard, baseline diff, `--fail-on-new-holes`. |
| `testplan_html.py` | Presentation only for `testplan.py --html`. Computes nothing; a figure here that is not in the CSV is a bug. |
| `coverage_analysis.py` | **Stage 6.** Lighter alternative: classifies only the uncovered remainder into a stimulus queue. |

## `autotest/` — the concrete oracle

| Module | What it does |
|---|---|
| `sailtest/cli.py` | Entry point: `generate`, `run`, `backends`. |
| `sailtest/config.py` | Reads a Golden Model configuration file. |
| `sailtest/model.py` | Derives the instruction set from the Sail model itself. |
| `sailtest/oracle.py` | The Golden Model used as a test oracle — runs it, reads back the state. |
| `sailtest/selfcheck.py` | Shared scaffolding for tests whose expected results come from the model. |
| `sailtest/program.py` | A generated program, rendered as assembly. Owns the ACT header and signature region. |
| `sailtest/toolchain.py` | Compiles a generated `.S` into an ELF the same way the Golden Model does. |
| `sailtest/suite.py` | Generates a suite from a config and organises it by extension. |
| `sailtest/runner.py` | Runs a generated suite on a simulator and summarises pass/fail. |
| `sailtest/backends/model_backend.py` | Model-derived generation — the instruction set comes from Sail. |
| `sailtest/backends/template_backend.py` | Baseline backend: hand-written self-checking tests, capped at what neither engine reaches. |
| `bisect_probe.py` | Narrows a failing generated test to the instruction responsible. |

## `tools/`

| Script | What it does |
|---|---|
| `inventory/extract_inventory.py` | Derives the complete architectural inventory from the model — 1,255 targets across instructions, CSRs, exceptions, interrupts, modes, PMP. |
| `inventory/csv_to_xlsx.py` | Combines the inventory CSVs into one filterable workbook. |
| `md2pdf.py` | Renders the technical plan to PDF. |
| `mk_docx.py` | Renders to DOCX. |
| `proof.py` | Evidence helper for claims made in the plan. |

---

# How the output is organised

Tests are grouped by ISA extension, so a configuration can include the ones that apply and
skip the rest.

```
<out-dir>/Zicond/czero_eqz.elf          opcode sweeps: <extension>/<mnemonic>
<out-dir>/Smstateen/mstateen0/Zicsr/    CSR sweep: <extension>/<csr>/
<out-dir>/rv64/PMP/m2_load_denied.elf   scenarios: <xlen>/<feature>/
```

Ten privileged extensions define no instructions at all and exist only as CSR addresses, which
is why the CSR sweep groups by extension rather than by register name — for those, that
directory is the only place the extension appears.

## Selecting tests for a configuration

A directory name cannot say "needs at least one PMP entry", so every generated test carries a
`riscv-arch-test`-format header, and every directory gets a `tests.json`:

```
##### START_TEST_CONFIG #####
# REQUIRED_EXTENSIONS: ['I', 'Zicsr', 'Sm']
# params:
#   MXLEN: 64
#   NUM_PMP_ENTRIES: '>0'
# MARCH: rv64i_zicsr
##### END_TEST_CONFIG #####
```

The format is `riscv-arch-test`'s deliberately, so the same runner that selects their tests can
select ours.

**`MARCH` is the authoritative field.** It is the string the assembler was actually given, so
it provably encodes these instructions. `REQUIRED_EXTENSIONS` is a readable rendering of the
same fact.

```json
{"name": "m2_NEG_wrong_cause", "elf": "m2_NEG_wrong_cause.elf",
 "params": {"MXLEN": 64, "NUM_PMP_ENTRIES": "'>0'"},
 "note": "negative control: must FAIL"}
```

Do not let a runner silently drop those. A negative control is the test that proves the others
can fail; keeping the suite and discarding its controls leaves you with tests that always pass.

---

# Three things to understand before quoting results

## Why Spike matters

For oracle-generated tests the expected values **come from** running the model. Checking them
again on the model is circular — that run passes by construction. It still catches harness
bugs, but it says nothing about the model.

**Only the Spike run is independent evidence.** The runner labels both columns for exactly this
reason. The symbolic flow does not have this problem, because there the expected values come
from symbolic execution.

## A passing test is not evidence that anything was checked

47.7% of this corpus once passed while comparing **empty** expected-state tables. Every test
was green. The defect was invisible precisely because the suite was passing.

That is why negative controls are mandatory, and why `findings.md` records the reproducer for
every claim.

## A coverage number is meaningless without its scope and span kind

Four scopes and three span kinds give twelve different true answers for "our coverage".
Whole-model branch coverage is 77.5%; all-span coverage within the current deliverable is
48.3%. Both are correct. Quote the scope, the span kind, and the corpus size together, or the
number is not reproducible.

A directory missing from `coverage_report.py`'s `DEFAULT_ELF_DIRS` is silently missing from
every figure, and looks identical to code no test reaches. That has happened three times.

---

# Documentation

| Document | What |
|---|---|
| [`EXECUTION_PLAN.md`](documentation/EXECUTION_PLAN.md) | architecture, the 0→100 plan, gap analysis, validation strategy |
| [`findings.md`](documentation/python-isla/findings.md) | every defect found, each with an executed reproducer |
| [`TESTPLAN.md`](documentation/python-isla/TESTPLAN.md) | how the testplan is derived, the ceiling, and how new holes are detected |
| [`METHODOLOGY.md`](documentation/METHODOLOGY.md) | why this generation methodology was selected, and against what alternatives |
| [`TECHNICAL_PLAN.md`](documentation/TECHNICAL_PLAN.md) | the pipeline, stage by stage, in the proposal's own terms |
| [`GAP_ANALYSIS.md`](documentation/GAP_ANALYSIS.md) | what is committed, what is deferred, and on what reasoning |
| [`QUICKSTART.md`](documentation/QUICKSTART.md) | shorter task-oriented walkthrough |
| [`DELIVERY_PLAN.md`](documentation/DELIVERY_PLAN.md) | two-horizon delivery plan |

## License

Apache-2.0 — the RFP's preferred licence for new repositories.
