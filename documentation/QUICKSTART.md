# Quickstart — generate and run a test suite

The two things this framework does, end to end: **generate** self-checking tests straight from
the Sail RISC-V Golden Model's own declared instruction set, then **run** them and get a
pass/fail summary across whichever simulators you have installed. This doc is the practical
"how do I actually run it" companion to the design docs in this directory — see
[`python-isla/model-sourced-generation.md`](python-isla/model-sourced-generation.md) for how the
pipeline works internally.

## Prerequisites

| Tool | Used for | Notes |
|---|---|---|
| Rust + Cargo | building `isla-testgen` | `isla-gen-extension/` submodule |
| A Sail RISC-V checkout, built (`sail_riscv_sim`) | the Golden Model itself | `sail-riscv` |
| Z3 | SMT solving inside isla-lib | any recent build; needs to be on `LD_LIBRARY_PATH` |
| `riscv{32,64}-unknown-elf-as`/`objdump` | encoding + assembling generated tests | a standard RISC-V GNU toolchain |
| Spike | reference execution | optional but recommended — `riscv-isa-sim` |
| QEMU (`qemu-system-riscv{32,64}`) | reference execution | optional; only needed for `--boot-fixed-entry` runs |
| CVA6 + Verilator | RTL execution | optional, more involved — see [`python-isla/model-sourced-generation.md`](python-isla/model-sourced-generation.md#building-the-cva6-rtl-target) |

### Build `isla-testgen`

```
cd isla-gen-extension
cargo build --release
cd isla/isla-sail && make && cd ../..
```

This produces `target/release/isla-testgen` and the compiled Jib IR used at generation time.

### Point the driver script at your local paths

`python-isla/opcode_sweep.py` has its environment (Sail checkout, isla-testgen build, Z3, RISC-V
toolchain, Spike) as constants near the top of the file (`SAIL_RISCV_ROOT`, `ISLA_TESTGEN_DIR`,
`Z3_LIB_DIR`, `SAIL_RISCV_SIM`, `RISCV_TOOLCHAIN_DIR`, `SPIKE_BIN`) — update those to match your
checkout locations before running.

## Generate a test suite and run it everywhere available, in one command

```
cd python-isla
python3 opcode_sweep.py extensions/I/base_insts.sail --xlen 32
```

- The positional argument(s) are `.sail` files under `<sail-riscv>/model/` — pass whichever
  extension file(s) you want swept (multiple files in one run are fine).
- `--xlen 64` switches the whole pipeline (assembler, isla-testgen IR, simulator flags) to RV64.
- Every instruction the given file(s) declare is parsed straight from the model
  (`mapping clause assembly`), rendered as real assembly, assembled with the real toolchain,
  fed to `isla-testgen`, and the resulting ELF is run on every simulator found on `PATH`.

Example output, one line per instruction:

```
PASS  addi           0x00400093              gen=ok sail= ok  spike= ok  qemu= --
...
42/44 instructions: parsed from the model, generated, and passing on every available simulator (ok/FAIL/-- = pass/fail/not installed)
  sail   42 pass, 0 fail, 2 not installed
  spike  42 pass, 0 fail, 0 not installed
  qemu   0 pass, 0 fail, 44 not installed
```

`--` means the simulator wasn't found on `PATH` (not a failure); `FAIL` is a real mismatch worth
investigating. Generated `.s`/`.ld`/`.elf` files land in `--out-dir` (default `/tmp/opcode-sweep`),
one set per instruction — inspect these directly if a `FAIL` needs digging into.

## Running on QEMU or CVA6 (RTL)

QEMU and CVA6 both always boot to a fixed physical address regardless of the ELF's entry point,
so they need tests generated in the framework's `--boot-fixed-entry` mode:

```
python3 opcode_sweep.py extensions/I/base_insts.sail --xlen 64 --boot-fixed-entry
```

This is the mode that's been verified against all four targets (Sail, Spike, QEMU, and CVA6 RTL)
on the same generated ELF. CVA6 isn't driven automatically by the script (it needs a one-time
Verilator build first) — see
[`python-isla/model-sourced-generation.md`](python-isla/model-sourced-generation.md#building-the-cva6-rtl-target)
for the build command, then run the produced testbench binary directly against any
`--boot-fixed-entry`-generated `.elf`.

## Generating an ACT4-compatible test

A separate, also mutually-exclusive-with-`--boot-fixed-entry` mode produces tests that plug into
`riscv-arch-test`'s (ACT4) own build/signature pipeline instead — see
[`act4/act4-integration-status.md`](act4/act4-integration-status.md) for the full picture and the
exact `isla-testgen` invocation.

## Sweeping an extension with its own march/isa requirements

Some extensions need the assembler/Spike told explicitly they're enabled (Zicsr, Svinval — the
assembler otherwise calls the mnemonic unrecognized, and Spike enables some but not all
extensions by default). `--extension` labels the run, picks the output subdirectory
(`<out-dir>/<extension>/`), and looks up the right march/isa suffix from `EXTENSION_MARCH`:

```
python3 opcode_sweep.py extensions/Zicsr/zicsr_insts.sail --xlen 32 --extension Zicsr
python3 opcode_sweep.py extensions/Svinval/svinval_insts.sail --xlen 32 --extension Svinval
```

`--extension` defaults to the Sail file's own parent directory name if omitted. `--only
name1,name2` restricts a sweep to specific mnemonics, e.g. to re-check one instruction without
re-running a whole file. See
[`python-isla/privileged-instruction-coverage.md`](python-isla/privileged-instruction-coverage.md)
for a full worked example (every privileged instruction the model implements, swept and measured
for Sail specification coverage).

## Generating a single instruction directly (finer control)

`opcode_sweep.py` is a convenience wrapper. To generate one specific opcode by hand — useful
when debugging a single failure without re-running a whole extension's sweep:

```
LD_LIBRARY_PATH=<z3-lib-dir> target/release/isla-testgen \
    -A riscv-ir/riscv32.ir -C riscv-ir/riscv32.toml -a riscv32 \
    --memory-region 0x80020000-0x80030000 -n 1 -o /tmp/one-test 0x00400093
```

`-n 1` requests one instruction; the trailing hex value(s) are the opcode word(s) to test, always
given as a full-width, zero-padded hex string (`0x00400093`, not `0x400093` — isla-testgen's
opcode parser reads the digit count as the instruction's bit width). Run the produced
`/tmp/one-test.elf` on any simulator directly, e.g. `sail_riscv_sim --rv32 /tmp/one-test.elf`.

## Seeing where the whole corpus stands

Two reports, both run from `riscv-test-generation/python-isla/`. They read the results
already on disk (`~/.cache/riscv-sweep/`), so they are instant and do not re-run anything.

**"Did anything get worse?"**

```
cd riscv-test-generation/python-isla
python3 regression.py
```

Compares every instruction's *status* against the stored baseline and prints what
regressed, what improved, and what disappeared. **Exits 1 if anything regressed**, so it
works unchanged as a CI step.

It compares status rather than pass/fail on purpose. A test can go from *verified* to
*passes but checks nothing* — that is a serious regression, and a pass/fail count records
it as no change at all. That is not hypothetical: it is exactly what happened to 47.7% of
the corpus before `findings.md` B4 was fixed.

```
python3 regression.py --update        # accept the current state as the new baseline
python3 regression.py --json out.json # machine-readable
```

Re-baseline only after you have looked at the diff and agree with it. `--update` makes
whatever is on disk the new definition of "correct", including any regression in it.

**"What passed, what failed, and whose fault is it?"**

```
cd riscv-test-generation/python-isla
python3 status_report.py
```

Per-extension pass/fail/skip counts, then every failure attributed to one of:

| | meaning |
|---|---|
| **MODEL** | the Golden Model is wrong, or it and Spike disagree. A finding worth reporting upstream. Only assigned where a written-up reproducer exists (`findings.md` A1–A4) — "Sail passes, Spike fails" on its own is more often an ISA-string difference. |
| **FRAMEWORK** | our generator or harness cannot produce a valid test. Ours to fix, and split by *which* limitation so the report says what to fix. |
| **ROUTED** | isla cannot generate it and the oracle covers it instead. Not a failure — the test exists and passes. Decided by asking whether the oracle corpus actually contains the instruction. |
| **EXPECTED** | the test asserts a trap and got one, or is a negative control. |

```
python3 status_report.py --extension V     # one extension in detail
python3 status_report.py --failures-only   # skip the passing rows
python3 status_report.py --all-scope       # include V/FP/H, which the RFQ defers
```

By default the deferred extensions (V, vector-crypto, bfloat16, FD, H) are reported
separately rather than folded in. Left in the main table they dominate it — over 1200 of
the failures are these, and almost all are instructions isla structurally cannot execute
that the oracle covers instead.
