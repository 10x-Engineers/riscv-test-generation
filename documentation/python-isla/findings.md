# Findings from the model-sourced opcode sweep

Concrete, reproduced defects and divergences turned up by generating and actually
*running* tests, as opposed to gaps in our own coverage (those live in
[`coverage-expansion-plan.md`](coverage-expansion-plan.md)). Every entry here has a
minimal reproducer that was executed, not a hypothesis.

Three categories, deliberately separated, because they need different owners:

- **A. Golden-model defects and divergences** — either the two implementations disagree
  (A1–A3), or the model contradicts itself so a legal program cannot run (A4). This is
  what the framework exists to find.
- **B. `isla-testgen` defects** — our generator produces a wrong test. These invalidate
  results until fixed, so they matter more than a failing instruction does.
- **C. Framework limits** — the harness cannot express a correct test for some class of
  state. Not a bug in anything, but it bounds what a passing result means.

---

## A1. PMP default-deny for non-Machine accesses: Sail denies, Spike permits

**Reproducer**: generate a standalone `mret` that returns to User or Supervisor mode,
with no PMP entry configured.

```
isla-testgen -A riscv-ir/riscv64.ir -C riscv-ir/riscv64.toml -a riscv64 \
    --memory-region 0x80020000-0x80030000 -o /tmp/neg -n 1 0x30200073 \
    --preload-xepc mepc --preload-xpp mpp=u
sail_riscv_sim /tmp/neg.elf   # FAILURE
spike --isa=rv64imac_zicsr /tmp/neg.elf   # exit 0
```

**What happens**: after the `mret`, the hart is in User mode and fetches the next
instruction. Sail takes an access fault; Spike does not.

**Which is right**: Sail. The privileged spec is explicit — if no PMP entry matches an
S-mode or U-mode access *and at least one PMP entry is implemented*, the access fails.
`pmp_control.sail`'s `pmpCheck` implements exactly that:

```sail
if priv == Machine then None() else Some(accessFaultFromAccessType(access))
```

**Not a configuration artifact**, which was checked specifically: both simulators
default to 16 implemented PMP entries with none configured (`sail_riscv_sim
--print-default-config` reports `memory.pmp.count` 16; `spike --help` reports
`--pmpregions` default 16). So the two are configured identically and still disagree.

**Why it took a privilege-transition test to find it**: every test before this one ran
entirely in Machine mode, where unmatched accesses are permitted by both. The
divergence is invisible until something actually leaves M-mode.

This same behaviour is why `--pmp-allow-all` exists (see
[`privileged-instruction-coverage.md`](privileged-instruction-coverage.md)); it also
makes a useful negative control, since dropping the flag must fail on Sail and does.

## A2. PMP CSR reset values differ (`pmpaddr0`)

Sweeping all six Zicsr instructions against `pmpaddr0` (0x3b0) passes 6/6 on Sail and
0/6 on Spike, while the immediately adjacent `pmpaddr1` (0x3b1) passes 6/6 on both.
This is the reset-value mismatch already documented in
`isla-gen-extension/PMP_PLAN.md`, now reproduced from the CSR sweep rather than by
hand — recorded here so it isn't rediscovered as new. Only entry 0 is affected.

## A3. Spike aborts on `stimecmp` access without Zicntr

`csrrw x1, stimecmp, x2` under `spike --isa=rv64imac_zicsr_sstc` **dumps core**.
Adding `zicntr` (`--isa=rv64imac_zicsr_zicntr_sstc`) makes it pass. Sail handles the
same ELF correctly either way.

Sstc's implementation reaches the `time` CSR, so requiring Zicntr is arguable — but
aborting rather than raising an illegal instruction is not. Low severity for us (the
workaround is one ISA-string entry, now in `csr_sweep.py`), potentially worth
reporting upstream to Spike.

## A4. Every valid shadow-stack page trips an assertion in Sail

**Category note**: unlike A1–A3 this is not a Sail/Spike disagreement. It is a defect in
the Golden Model on its own terms — two parts of the same file contradict each other, so
the code that handles shadow stacks can never run.

**Reproducer**: map a Sv39 gigapage whose PTE has the shadow-stack encoding (R=0, W=1,
X=0 → permission bits `0xc5`) and touch it from S-mode. The generator does this for any
`ssamoswap.*` test; the run ends with

```
Assertion failed: sys/vmem_pte.sail:143.24-143.25
```

**The contradiction, in one file**:

- [`vmem_pte.sail:94`](../../../sail-riscv/model/sys/vmem_pte.sail) — `pte_is_invalid`
  treats R=0/W=1/X=0 as **invalid only when `menvcfg.SSE == 0`**. With SSE=1 the PTE is
  declared **valid**, which is correct: that encoding *is* the shadow-stack page.
- `vmem_pte.sail:143` — the permission check then asserts
  ```sail
  assert(pte_W ==> pte_R);   // "Writable pages must be readable"
  ```
  A shadow-stack PTE has W=1 and R=0, so this assertion fails for exactly the PTEs the
  previous step just admitted.
- `vmem_pte.sail:160` — the shadow-stack handling itself,
  `if not(pte_R) & pte_W & not(pte_X) then { ... }`, is **unreachable**. Control never
  gets past line 143 to reach it. Its own `assert(bool_bit(menvcfg[SSE]))` on the next
  line shows the author expected to arrive here with a valid SS page.

So the line-143 assertion is a leftover from before shadow stacks existed. It needs the
SS encoding carved out — the comment above it ("Since we assume a valid PTE") is the
premise that stopped holding when line 94 was extended.

**The assertion was only half of it.** Relaxing it and re-running showed a second copy of
the same stale assumption, which the assertion had been hiding by aborting first:

- `vmem_pte.sail:220` — step 8's permission table has `Atomic(_) => pte_W & pte_R`. An SS
  page is W=1/R=0 by definition, so step 7 permits the `SSAMOSWAP`
  (`Atomic(_, _, _, ShadowStack, ShadowStack) => true`) and step 8 immediately denies it.

Fixed by returning from the SS branch instead of falling through — that match is exhaustive
over access types and is the complete rule for an SS page, so step 8 has nothing left to
decide.

**Fix, with the intermediate state kept because it is the evidence**:

| | result of an S-mode `SSAMOSWAP.D` to an Sv39 SS page |
|---|---|
| Before | `Assertion failed: sys/vmem_pte.sail:143.24-143.25` — model aborts |
| Assertion hunk only | store/AMO page fault (`mcause 0xF`) — the walk completes and step 7 is reachable, but step 8 denies |
| Both hunks | **SUCCESS** |

The middle row is what showed the assertion was not the whole defect. `ctest` on the full
model suite is **664/664, zero regressions**.

**Submitted** as a PR on our sail-riscv fork:
<https://github.com/10x-Engineers/sail-riscv-testgen/pull/2> (branch
`fix/shadow-stack-pte-permission`). Not filed against `riscv/sail-riscv`.

**Still open, and not part of that PR**: the M-mode `ssamoswap` tests continue to fail with
a store/AMO *access* fault. M-mode does not translate, so they never reach this code. That
is a separate question and may well be our test setup rather than the model.

**Consequence for us**: no shadow-stack behaviour was testable at all — the scenario test
that maps an SS page and touches it from S-mode aborted the model outright.

**Correction — this was *not* the cause of the 12 `ssamoswap.*` sweep failures**, though it
was recorded that way for a while. Those 12 are ours. The sweep emits `ssamoswap` as an
ordinary **M-mode** instruction test, and the spec says *"Use of Zicfiss in M-mode is not
supported"*. The model implements that faithfully at
[`vmem.sail:416`](../../../sail-riscv/model/sys/vmem.sail):

```sail
if is_shadow_stack_access(access) then {
  if (mode == Bare & effPriv != Machine) | effPriv == Machine
  then return Err(E_SAMO_Access_Fault(), init_ext_ptw);
};
```

So an M-mode `ssamoswap` is *required* to fault, and a test expecting it to succeed is a
wrong test. Confirmed by running all eight RV64 forms against the fixed model: still 0/8,
unchanged by the fix.

**Fixed** by adding `"SSAMOSWAP": 7` to `opcode_sweep.TRAP_EXPECT` — all eight mnemonic
forms share one union, so one entry covers them. cfi is now **12/12 RV64, 8/8 RV32, zero
failures**, on Sail *and* Spike. Spike agreeing matters: two independent implementations
raising the same fault is what makes this an architectural rule rather than a Sail quirk.

**Negative control**: the same instruction with `--expect-trap-cause 5` **fails** on both
simulators, so the test checks the *cause*, not merely that something trapped. A
trap-expecting test that passes for any trap is the trap-shaped version of a vacuous test.

The positive half — an S-mode `ssamoswap` against a mapped shadow-stack page — is a
scenario test rather than an opcode sweep, and that one now passes too, because of the
model fix above.

**Worth stating plainly, because it is the failure mode this file exists to prevent**: a
real, reproduced model defect was found nearby, and 12 unrelated failures were attached to
it because the story fit. The defect was real; the attribution was not. A finding earns
failures only when the reproducer covers *those* failures.

**Not a configuration artefact**: the failure needs `menvcfg.SSE=1`, which is the only
setting under which the instruction is legal at all. There is no configuration in which
a shadow-stack test both assembles and avoids the assertion.


---

## B1. `isla-testgen` drops the effect of compressed instructions in a sequence

**The most consequential finding here**, because it silently produces tests with
expected values that no correct implementation can satisfy.

**Minimal reproducer** — `addi x9,x0,5`, then `c.addi x9,1`, then `c.slli x9,4`:

```
isla-testgen -A riscv-ir/riscv64.ir -C riscv-ir/riscv64.toml -a riscv64 \
    --memory-region 0x80020000-0x80030000 -o /tmp/mc -n 1 0x00500493 0x0485 0x0492
# Final registers: zx9:#x0000000000000005 zPC:#x0000000080000008
```

Expected `x9 = (5+1) << 4 = 0x60`. isla records 5 — the value after the *32-bit*
instruction, with both compressed instructions' effects missing.

**PC is correct** (`0x80000008` = 4 + 2 + 2 bytes), and isla reports "1 successful
execution" for each opcode, so placement and instruction-width handling are both
right. Only the register effect is lost.

**Scope**: any generated test with a compressed instruction in a non-final position.
A single compressed instruction on its own is usually fine — `c.addi`, `c.mv`, `c.li`,
`c.nop`, `c.j` all pass. It bites hardest on compressed loads and stores, whose
address-setup prelude GNU as compresses as a matter of course.

**Current workaround** (in `model_opcodes.render_asm`): wrap the prelude in
`.option norvc` so only the instruction under test is compressed, keeping it in the
one position isla handles. This makes every C-extension load/store pass. It is a
workaround, not a fix — a test *sequence* of compressed instructions is still not
generatable.

**Second symptom, same root cause — `c.jr`/`c.jalr`.** These fail even as the only
instruction in the test, and the generated `.s` shows why: `initial_gpr_values` is
**empty**. isla placed no constraint at all on the jump's target register, so at
runtime it jumps to whatever that register happens to hold and takes an instruction
access fault.

That's the tell that this isn't merely a sequencing issue. Compare the uncompressed
`jalr`, which passes: there, isla has to place its own exit trampoline at the jump's
computed target, and that requirement is what forces the target register to a valid
code address. For `c.jr` the effect that would have driven that constraint is dropped,
so nothing forces it — and `c.j` passes only because its target is PC-relative and
needs no register constrained. So the accurate statement is: **isla does not model a
compressed instruction's register/control-flow effects**, and the non-final-position
case is where that shows up most often, not the whole of it.

**Resolved for `c.jr`/`c.jalr` by a template, not by fixing isla** — see C11. The
underlying isla defect stands; only the coverage gap it caused is closed.

## B2. Opcode width was inferred from string padding, not the encoding

`opcode_sweep.py` padded every opcode to 8 hex digits, and isla-testgen reads the
digit count as the instruction width. A 16-bit compressed instruction handed over as
8 digits becomes a 32-bit opcode with a zero upper half, which decodes as an illegal
instruction at runtime.

Fixed by keying on the encoding itself: RISC-V sets bits `[1:0] == 0b11` for 32-bit
instructions, so `(op & 3) == 3` distinguishes them with no extra bookkeeping. Noted
because it was invisible until the C extension was swept at all — every earlier sweep
happened to be 32-bit-only.

## B3. Zkr's `seed` CSR cannot be generated: missing `get_16_random_bits`

```
Failed path Error Function get_16_random_bits does not exist
```

The model's `seed` read calls a foreign function isla-lib has no implementation for,
so symbolic execution cannot get past it. This is a missing primop in the generator —
no CSR address or ISA string will work around it. Zkr is untestable by this framework
until it's stubbed.

---

## C1. Self-updating CSRs cannot be checked against a static expected value

`mcycle`, `minstret`, `mip`, and `time` fail all six Zicsr instructions **identically
on Sail and Spike**. Two independent implementations agreeing is the tell: the
expectation is what's wrong, not the implementations.

The harness compares final architectural state against the single value isla solved.
A register that advances on its own as instructions retire can never match that,
however correct it is.

Partly addressed by `--inhibit-counters`, which freezes `mcycle`/`minstret` via
`mcountinhibit` before the test — enough to make `cycle` pass 6/6.

**Not** fully addressed, and the reason is worth being precise about: the harness
preloads GPRs and a fixed list of system registers, and `mcycle` is not among them.
A test that reads `mcycle` into a GPR therefore depends on a runtime value the harness
never set, so it stays non-deterministic even with counting frozen. Making these
testable means extending the harness's system-register preload to arbitrary CSRs —
well-scoped work, not yet done.

## C2. WARL CSRs read back a legalised value, not the written one

`satp` passes 3 of 6, on both simulators. `satp` is WARL: writing an arbitrary value
gets legalised, so the readback differs from what was written, and a
write-then-compare test fails for correct behaviour.

Testing these properly means asserting the *legalisation rule* rather than
value equality — a different test shape, in the same family as C1.

## C3. The model's assembly syntax is broader than the assembler's, for F/D conversions

The F/D assembly clauses emit a rounding mode uniformly, via `frm_mnemonic(rm)`. GNU as
rejects it on the conversions where rounding cannot occur:

```
Error: illegal operands `fcvt.d.s f1,f2,rne'
```

Widening float conversions (`fcvt.d.s`, `fcvt.d.h`, `fcvt.s.h`) and integer-to-double
(`fcvt.d.w`, `fcvt.d.wu`) are exact, so the spec gives their `rm` field no meaning — the
model's clause is simply more general than the real syntax. Six instructions across
RV32/RV64 were failing on this.

Resolved without hand-maintaining a list of which mnemonics over-declare: `opcodes_for`
retries once with the trailing literal operand dropped when the assembler says "illegal
operands". The assembler is this module's encoding source of truth, so taking its answer
and re-rendering is the consistent move. All six now pass.

## C4. Vector register *groups* must be aligned, and the renderer wasn't

The single largest source of failures in the whole project, and entirely self-inflicted:
the renderer allocated vector operands as `v1, v2, v3`.

A widening, narrowing or segment instruction addresses a register **group** of EMUL
registers, and the group must be aligned to its own size. `vwadd.vv v1, v2, v3` widens
into a *two*-register destination group starting at an odd register, which is
architecturally illegal. Confirmed directly:

```
vwadd.vv v1, v2, v3    -> FAIL on Sail
vwadd.vv v8, v16, v24  -> ok
```

Fixed by allocating from `v8`/`v16`/`v24` — 8-aligned and 8 apart, the only spacing that
satisfies both this and "don't collide with the v0 mask register" for every legal EMUL
up to 8. **Recovered 290 instructions** across the M7 sweep (916 → 1206 passing).

Two things worth recording about how this was assessed, not just fixed:

- **A one-instruction-per-family sample over-projected the fix by half.** Sampling ten
  representatives suggested it would clear ~413 of 432 failures; the real figure was 290,
  about 67%. Families here contain addressing-mode variants that differ structurally, so
  a single representative isn't representative. Sample more than one per family, or state
  the extrapolation as an estimate rather than a result.
- **The residual is a different constraint, not a weaker version of the same one.** Most
  of what still fails is *indexed* and *strided* segment addressing (`vloxseg`, `vluxseg`,
  `vsuxseg`, `vlsseg`, `vssseg`), which carries a second register group for the
  index/stride vector whose EMUL derives from the **index element width**, not SEW — so
  `v8`/`v16`/`v24` doesn't automatically satisfy its alignment and non-overlap rules
  either.

## C5. Identical opcodes pass or fail run to run — *not* machine load (superseded)

**This entry originally blamed machine load. That was wrong, and the correction matters
more than the original claim.**

The symptom was real: re-running 18 apparent V failures turned 15 green, and
`vclmulh.vx` passed one run and failed the next on an identical opcode. The
explanation — wall-clock timeouts under contention — was plausible and false.

The actual cause is B4. On an **idle** machine, `vmin.vv` still failed roughly 1 run in
8. `finalize` (execution.rs) picks the harness's scratch registers by excluding
everything the trace touches, then takes the first two out of a `HashSet`, whose
iteration order varies per run. Because vector instructions never executed inside isla,
the destination of the generator's own `vsetvli` prelude was never in the trace, so it
stayed in the pool — and roughly 7% of the time isla chose *that* register to hold the
address of `finish`. The test then jumped to a vector length instead of the harness
epilogue.

Measured directly: over 30 runs of the same two opcodes, `x1` — the `vsetvli`
destination — was chosen twice. Over 20 runs of a scalar `add x1, x2, x3`, `x1`/`x2`/`x3`
were chosen zero times, because there the exclusion works.

Fixing B4 fixes this: with the vector unit enabled inside isla the write to `x1` appears
in the trace and the register is correctly excluded.

The retry-once-on-simulator-failure logic added for the wrong reason is kept — it costs
one re-run on an already-failing test and does guard against genuine timeout noise — but
it was **masking a real defect, not compensating for the environment**. A retry that
turns a failure green is a signal to investigate, not a fix.

Two general lessons, both about this project's own reasoning:

- "The machine was busy" is the most available explanation for nondeterminism and needs
  the same evidence as any other. The distinguishing experiment — re-run the identical
  input on an idle machine — is cheap and was not run before the conclusion was written.
- The original entry cited "15 of 18 turned green" as *support* for the load theory. It
  is equally consistent with a ~7% per-run failure probability, which is what it was.
  Agreement with a hypothesis is not evidence when the alternatives were never stated.

## C6. VLEN differs between the simulators, and it reads as a divergence

`vsm3me.vv` passes on Sail and raises `trap_illegal_instruction` on Spike. That was one
step from being written up as a golden-model divergence. It is not one.

SM3, SM4, AES, SHA-2 and GHASH operate on 256-bit **element groups** (EGW=256), which
require `LMUL * VLEN >= 256`. Sail's default config is `vlen_exp: 8` — VLEN=256 — so
LMUL=1 satisfies it. This Spike build's VLEN is fixed at compile time and smaller (it has
no `--varch` option at all), so it *correctly* rejects them. Generating these at LMUL=2
satisfies the constraint on both, and they pass everywhere.

The general lesson is worth more than the specific fix: **a configuration difference
between two simulators presents identically to a behavioural divergence.** Before
reporting one, check the configs — VLEN, ELEN, PMP entry count, enabled extensions.

## C7. Mnemonic cross-products over-generate

A clause's mnemonics are built as the cross-product of its string tables — LOAD's is
`"l" ^ width_mnemonic ^ maybe_u` — which yields `ldu` alongside the real
`lb`/`lbu`/`lh`/`lhu`/`lw`/`lwu`/`ld`, because the model constrains legal combinations
in its `encdec` clause, not its `assembly` one.

These are reported in their own `NOENC` category ("no such mnemonic at either XLEN")
rather than counted as failures — they aren't instructions. They are still *listed*,
so that a wrong `-march` string, which would put every mnemonic in the sweep into that
category at once, is obvious rather than silent.

## B4. `--enable-fp`/`--enable-vector` set the harness but not isla, so 47.7% of the corpus checked nothing

The single most consequential finding so far, and it had been passing green for the whole
project.

Both flags emitted `csrs mstatus, ...` into the *generated preamble* — correct, and
necessary — but never touched mstatus inside isla's own symbolic execution. isla
therefore ran every FP and vector instruction with FS and VS still at their reset value
of Off, where the model refuses the instruction before it does anything.

The traces show it exactly. For a scalar `add x1, x2, x3`:

```
... Rzx2 Rzx3 Wzx1 RznextPC WzPC
```

For `vsetvli x1, x0, e32, m1, ta, ma`:

```
... Rzmstatus RznextPC WzPC
```

It reads mstatus, and advances the PC. No write to `x1`, `vl` or `vtype` anywhere.

Two separate bugs fall out of that, and both were live:

**The generated tests verified nothing.** isla extracts expected state from the events it
observed. With no events, `initial_gpr_values` and `final_gpr_values` come out *empty*,
and the emitted test checks only that the 8–12 code bytes are unmodified and that
execution reached `finish` without trapping. `fadd.s` passed on both simulators and
compared no value at all. Measured across a full sweep's generated assembly:

| extension | tests | verify no state | |
|---|---:|---:|---|
| V | 627 | 317 | 50.6% |
| FD | 120 | 114 | 95.0% |
| vector_crypto | 41 | 41 | 100% |
| C | 34 | 26 | 76.5% |
| bfloat16 | 6 | 6 | 100% |
| I | 57 | 5 | 8.8% |
| **whole corpus** | **1098** | **524** | **47.7%** |

Some of that is legitimate — Zihintntl, Zihintpause, Zifencei, Svinval and Zawrs have no
architectural result, so "executes and doesn't trap" *is* the test. C's share is B1, the
compressed-instruction defect, now quantified. The real gap is the ~504 FP and vector
tests that should have been checking a result.

**Scratch-register collisions.** See C5.

Fixed in `RiscV::init()` (target.rs), which sets mstatus.FS/VS in the model state to
match what the preamble emits. The two must agree: if isla computes expected state for a
machine with the unit off and the harness runs with it on, the test is wrong in the other
direction.

The detection lesson: **a test that cannot fail will never tell you it is broken.** What
exposed this was not a failing test but an *inconsistently* failing one — chasing why a
fixed opcode failed 1 run in 8 led to reading the generated assembly, which is where the
empty tables were sitting in plain sight. Worth adding a standing check that a generated
test asserts *something*; the corpus had no such check, and a 47.7% vacuity rate went
unnoticed across every previous sweep.

## B5. isla cannot execute the V extension's element paths at all

With B4 fixed, vector instructions execute for the first time — and immediately fail:

```
Instruction 0x0d0070d7   1 successful execution(s)     # vsetvli
Instruction 0x170c0457   Failed path Error Symbolic (bit)vector length in zeros
```

Every V instruction that reaches `read_vreg`/`write_vreg` hits it. What still works is
`vset*` and the mask instructions (`vfirst.m`, `vmsbf.m`), which don't go through the
element accessors.

Ruled out, each by direct experiment rather than inspection:

- **VLEN.** Fails identically on the VLEN=64 and VLEN=128 IR.
- **Symbolic vector registers.** Listing `vr0..vr31` in `regs()` makes them symbolic
  inputs; removing them again changes nothing.
- **Undefined vector CSRs.** `vstart`, `vl`, `vtype` and `vcsr` are undefined at reset
  and so symbolic. Pinning all four to their architectural reset values in
  `RiscV::init()` changes nothing.
- **Undefined vector registers.** Zeroing all 32 in `RiscV::init()` changes nothing.

So the symbolic length is produced inside the model's parametric element plumbing, not by
any input this project controls. Fixing it means teaching isla to concretise vector
lengths — an upstream change of unknown size, not a harness change.

**Routing decision: V goes to the oracle.** This is the "not scalable to extend" case.
Note what it means for the prior numbers: no V test isla has ever produced verified
vector behaviour. Before B4 the instruction was skipped; after B4 it fails to generate.
The 604/627 "passing" figure measured decode and liveness.

## B6. isla cannot execute FP arithmetic: SoftFloat is not in the IR

Same shape as B5, different cause and a much clearer boundary. The Sail model implements
FP arithmetic by calling out to SoftFloat, which exists as C in the simulator but not in
the IR isla reads:

```
Failed path Error Function riscv_f32Add does not exist
Failed path Error Function riscv_f32Eq does not exist
```

The split is clean and worth stating precisely, because it decides the routing:

- **isla can do** the bit-manipulation forms — `fsgnj`/`fsgnjn`/`fsgnjx`, `fmv.x.w`,
  `fmv.w.x`, `fclass`. These now generate *with real FP register comparison* (see the
  `initial_fpr_values`/`final_fpr_values` tables and the `fld`/`fsd` blocks in
  generate_object_riscv.rs).
- **isla cannot do** anything that computes: add, mul, div, sqrt, fma, the conversions,
  and — less obviously — the *comparisons*, since `feq`/`flt`/`fle` are SoftFloat calls
  too.

Bridging this would mean implementing ~40 SoftFloat primops against Z3's FloatingPoint
theory. That is possible in principle and a poor trade: FP constraint solving is slow,
and the oracle already runs the real thing for free.

**Routing decision: FP arithmetic goes to the oracle.** Verified end to end — 50
generated tests, 1200 FP instructions, expected values from the Golden Model, passing on
both `sail_riscv_sim` and Spike (`model-fp` backend, autotest).

## C8. VLEN=128 is the only value that works, and both bounds are hard

The IR is compiled against a platform config, so VLEN is fixed at build time
(`type vlenbits = bits(vlen)`). It had been built from `rv32d_v64_e32.json` — VLEN=64 —
while `sail_riscv_sim` ran at its own default of 256 and Spike at its own. Three
different vector lengths across the three tools that have to agree.

That is not a cosmetic mismatch. It surfaced as `vsetvli` **failing on Sail and passing
on Spike** once vl/vtype started being compared: isla expected `vl = 4`
(VLEN=128 ÷ SEW=32), Sail produced 8 at VLEN=256. Exactly the C6 trap again, in a new
place.

VLEN is pinned to 128 everywhere, and it is the only legal choice:

- **Lower bound:** the V extension requires `vlen_exp >= 7`, i.e. VLEN >= 128. The old
  VLEN=64 IR was an *embedded* vector profile — testing V against it was never testing V.
- **Upper bound:** isla's bitvector type is `B129`. A 256-bit vector register cannot be
  represented at all, so Sail's default was never reachable from isla.

Set in three places, all of which must stay in step: the IR (rebuilt via `isla-sail
--config .../rv{32,64}d_v128_e64.json`), `SAIL_CONFIG` in opcode_sweep.py, and
`SPIKE_VLEN_EXT = "zvl128b"` in the same file (this Spike has no `--varch`, but takes
VLEN through the `Zvl*b` ISA extensions).

## C9. Every oracle-generated test failed on Spike, for a memory-map reason

The oracle backend's tests passed on `sail_riscv_sim` and failed 100% on Spike —
including plain integer ones, which is what showed it wasn't a real divergence.

Spike refused to load the ELF at all:

```
Access exception occurred while loading payload ...
Memory address 0x20c0000 is invalid
```

The sail-riscv test harness these programs link against places its HTIF block in a
`.bss.mmio.htif` section at `0x020c0000`; Spike's default map has RAM at `0x80000000`
and nothing there. The region also can't simply be widened to cover the MMIO range,
because Spike puts its own CLINT at `0x02000000` and rejects the overlap.

Fixed in runner.py with `-m0x20c0000:0x1000,0x80000000:0x8000000`. All previously
failing suites now pass on Spike unchanged, which turns the oracle from a
single-simulator checker into a differential one — and that is what makes it a
legitimate destination for the B5/B6 routing rather than a fallback.

## C10. Oracle false-pass audit: one real hole, and one that is structural

Prompted by B4 — a 47.7% vacuity rate in the isla corpus went unnoticed because every
affected test passed — the oracle backend was audited the same way rather than assumed
sound. Method: mutate each expected value independently and require every mutation to be
caught, on both simulators.

**Sound, verified:**

- **Every check is live.** All 14 expected values in a generated FP test, mutated one at
  a time by a single bit, are caught on Sail *and* Spike: `CAUGHT 14 / 14`. The earlier
  7-value form was also 7/7.
- **Traps are not silently skipped.** Injecting `.word 0x00000000` into a passing test's
  body makes it fail (`FAILURE: 1001` on Sail, nonzero exit on Spike). This mattered
  because a trap that is skipped *identically* in the probe and the final test would
  agree with itself and pass — the exact shape of B4.
- **FP really executes.** crt0 sets `MSTATUS_FS`, and expected values include results
  like `0x4030000000000000` (16.0) that cannot be produced by moving a seed constant.
- **Expected values are not degenerate.** Across 30 tests: 151 distinct values out of
  420, no test with fewer than three distinct expectations, 17% zeros (unremarkable for
  FP on random bit patterns, where NaN and zero are common results).

**The real hole, now fixed:** the FP probe moved the FP results *into* `OUT_REGS` before
printing, which overwrote every integer result the body had computed. A third of the FP
instruction set writes an integer register — `fclass`, `feq`/`flt`/`fle`, `fcvt.w.*`,
`fmv.x.*` — so those instructions executed and nothing ever looked at what they produced:
**52 of 144 body instructions (36%) in a six-test sample**. Not vacuous — the FP half was
still checked — but coverage was being claimed for instructions whose results were
discarded before the comparison.

Fixed by stashing both halves of the result state in a memory buffer before any printing
(there are 14 values and only 12 callee-saved GPRs, and `printf` may clobber the FP
registers), then checking integer and FP results separately. Checks per test went 7 → 14.

**The structural one, which cannot be fixed and must be reported honestly:** the expected
values *come from* running the probe on `sail_riscv_sim`. Checking them again on
`sail_riscv_sim` is circular — that run is guaranteed to pass unless the probe and the
final program are not equivalent. It is not worthless (it catches harness bugs, and it
did catch the injected illegal instruction), but it is **not evidence about the model**.

Only the Spike run is independent evidence. So for oracle-generated tests:

- "30/30 on Sail" means *the harness is self-consistent*.
- "30/30 on Spike" means *Sail and Spike agree*, which is the actual result.

This is the inverse of the isla flow, where expected values come from symbolic execution
of the model and both simulator runs are meaningful. Worth stating in any summary that
quotes an oracle pass rate, because "passed against the Golden Model" reads as
verification and, for the Sail column, isn't.

## C11. Neither generator can produce a compressed jump, for two different reasons

`c.jr` and `c.jalr` were the last framework failures in the current deliverable. Both
generators failed on them, and it is worth writing down that the two causes are
unrelated — the obvious guess, "compressed jumps are just hard", is wrong.

- **isla** does not model a compressed instruction's control-flow effect at all, so it
  never constrains the target register and the test jumps to garbage. That is B1, and it
  is a defect in isla rather than a limit of the approach.
- **The oracle** refuses jumps by design. It derives expected values by running a *probe*
  program and then emits a second, different program with those values baked in. A jump
  target's address is not the same in the two, so the probe's answer does not describe
  the test. Emitting one anyway would produce a test that passes for the wrong reason.

Fixed with a **template** instead — a third, much smaller backend that hand-writes the
few tests neither generator can derive:

```asm
    la   t0, 1f
    c.jr t0
    li   t1, 0          # poison: only executes if the jump did not happen
1:  li   t1, 1
```

The poison instruction is what makes it a real test rather than a smoke test: if the
jump does not take, `t1` ends up 0 and the check fails. `t0` is cleared before the
comparison so the expected state does not depend on where the linker placed the label,
which is the same problem that stopped the oracle — sidestepped rather than solved.

**Verified, not assumed** — three separate checks, because a control-flow test that
"passes" is exactly the shape of a vacuous one:

1. The encodings really are compressed: `8282 -> 2 bytes = COMPRESSED jr t0` and
   `9282 -> 2 bytes = COMPRESSED jalr t0`. Had the assembler expanded them to 32-bit
   `jalr`, the test would have passed while covering nothing.
2. `PASS 4 FAIL 0` on Sail **and** on Spike, so the result is independent evidence
   (see C9/C10 on why the Spike column is the one that counts).
3. **Negative control**: replacing `c.jr t0` with `nop` makes the test fail
   (`FAILURE: 1`) while the original succeeds.

**Cost of the approach**: templates are hand-written, so they do not scale and they are
not derived from the model. Kept deliberately small for that reason — four tests, for
instructions with a *named, understood* reason why generation cannot reach them. Any
growth in this backend should be read as a signal to fix a generator instead.

## C12. "The solver gave up" was a routing decision, not a dead end

Ten instructions — `cpop`, `cpopw`, `aes64im`, `aes64dsm`, `xperm4`, `xperm8` — failed
with isla timing out or exhausting memory rather than with an error. These are all
instructions whose *definition* is expensive symbolically: a population count is a
32-deep adder tree, and the AES/xperm ones are large table lookups. Nothing is wrong
with them; Z3 simply has no shortcut.

Routed to the oracle, which does not care: it executes the instruction concretely once
and reads the answer out. All ten now generate and pass, via a `model-scalar` backend
covering B/K/Zicond.

**Why this is worth recording as a finding rather than a fix**: it is the clearest case
on the project of the hybrid split paying for itself. The symbolic and concrete backends
fail on *disjoint* sets of instructions — isla cannot execute FP or vector element paths
(B5/B6) but handles branchy control flow; the oracle handles anything the model can
execute but cannot express control flow (C11) and gives circular expected values on Sail
(C10). Neither alone reaches the corpus; the union does. See
[`coverage-expansion-plan.md`](coverage-expansion-plan.md) for the per-milestone split
and [`../../autotest/README.md`](../../autotest/README.md) for what each oracle backend
covers.

**Also worth noting what it is not**: a timeout is not evidence about the model. These
ten were previously counted as failures, which overstated the failure number by making
a solver-performance property look like a test result.
