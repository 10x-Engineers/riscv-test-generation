# Privileged-instruction coverage

Systematic, one-by-one generation and verification of every privileged-architecture instruction
the Sail model actually implements, plus Sail specification-coverage measurement on the result.
Companion to [`model-sourced-generation.md`](model-sourced-generation.md) (the general pipeline)
and [`act4-integration-status.md`](../act4/act4-integration-status.md); this doc is scoped to the
privileged subset specifically, per the RFP's own framing: M-mode features (PMP/PMA, trap and
interrupt handling) are required, S-mode features (virtual memory / address translation) are
desirable, Hypervisor is explicitly out of scope.

## What "privileged instructions" means here, and why it's a short list

Searching the model's own `mapping clause assembly` definitions (the same parsing
`model_opcodes.py` already does — see below) for anything privilege-related turns up **15
instructions**, not the dozens one might expect:

- **`extensions/I/base_insts.sail`** (bundled into the base ISA, not a separate extension):
  `ecall`, `ebreak`, `mret`, `sret`, `wfi`, `sfence.vma`
- **`extensions/Zicsr/zicsr_insts.sail`**: `csrrw`, `csrrs`, `csrrc`, `csrrwi`, `csrrsi`, `csrrci`
- **`extensions/Svinval/svinval_insts.sail`**: `sinval.vma`, `sfence.w.inval`, `sfence.inval.ir`

**Hypervisor is confirmed out of scope for a real reason, not just per the RFP's instruction:**
`extensions/H/hext_insts.sail` exists but contains only a `currentlyEnabled` guard clause — no
`HFENCE.*`/`HLV.*`/`HSV.*` instructions are actually implemented in this model checkout. There is
nothing to generate tests for yet, independent of the RFP's scoping choice.

## Table: every privileged instruction, one by one

`ok` = generated, and the resulting ELF passes on that simulator. `FAIL` = generated, but fails
(with the reason). RV32/RV64 both run the same way; results were identical on both XLENs for
every instruction. QEMU/CVA6 weren't run for this sweep — they need `--boot-fixed-entry`, a
separate opt-in generation mode (see `model-sourced-generation.md`); nothing about the privileged
instructions themselves is QEMU/CVA6-specific, so this is a scope choice for today, not a finding.

| Instruction | Extension | Sail union | Sail | Spike | Notes |
|---|---|---|---|---|---|
| `csrrw` | Zicsr | `CSRReg` | ok | ok | |
| `csrrs` | Zicsr | `CSRReg` | ok | ok | |
| `csrrc` | Zicsr | `CSRReg` | ok | ok | |
| `csrrwi` | Zicsr | `CSRImm` | ok | ok | |
| `csrrsi` | Zicsr | `CSRImm` | ok | ok | |
| `csrrci` | Zicsr | `CSRImm` | ok | ok | |
| `sfence.vma` | I (priv.) | `SFENCE_VMA` | ok | ok | Fallthrough, no GPR effect — see "shallow pass" caveat below |
| `sinval.vma` | Svinval | `SINVAL_VMA` | ok | ok | Same caveat as `sfence.vma` |
| `sfence.w.inval` | Svinval | `SFENCE_W_INVAL` | ok | ok | Same caveat |
| `sfence.inval.ir` | Svinval | `SFENCE_INVAL_IR` | ok | ok | Same caveat |
| `ecall` | I (priv.) | `ECALL` | ok | ok | Fixed — `--expect-trap-cause`, see below |
| `ebreak` | I (priv.) | `EBREAK` | ok | ok | Fixed — same mechanism |
| `mret` | I (priv.) | `MRET` | ok | ok | Fixed — returns to **Supervisor**; `--preload-xepc mepc --preload-xpp mpp=s --pmp-allow-all` |
| `sret` | I (priv.) | `SRET` | ok | ok | Fixed — same, via `sepc`/`spp` |
| `wfi` | I (priv.) | `WFI` | ok | ok | Fixed — `--pending-interrupt` raises a machine software interrupt via the CLINT so WFI's wake condition is already satisfied |

**All 15 pass end-to-end on both Sail and Spike, RV32 and RV64**, up from 10/15 when this
document was first written and 12/15 after the `--expect-trap-cause` fix. The remaining three
were closed by setting privileged state in the **harness preamble** rather than in the opcode
sequence handed to isla — see [`coverage-expansion-plan.md`](coverage-expansion-plan.md)'s M0 for
why that distinction was the whole problem, and the four flags it produced.

Two things about the `mret`/`sret` results are worth stating explicitly, because "the test
passes" is a weaker claim than it looks for a privilege-transition instruction:

- **They target Supervisor, not Machine.** An `mret` with `MPP` left at its reset value, or set
  to Machine, would pass without ever changing privilege. These deliberately land in a different
  mode than they started in.
- **There is a negative control, and it fails as it must.** Drop `--pmp-allow-all` and both tests
  fail on Sail — precisely because the privilege change really happened, and PMP then correctly
  denies the next fetch from Supervisor. That failure is the evidence the passes aren't vacuous.
  Spike passes either way, which is itself a finding: see [`findings.md`](findings.md) A1, where
  Sail turns out to be the one following the spec.

The originally-anticipated complication — that reading `mstatus` back is illegal once outside
M-mode, so a post-transition check would need `sstatus` instead — did not arise, because the
check is the harness's ordinary final-state comparison run after control returns, not a read
performed while in the lower privilege.

### Fixed: `ecall`/`ebreak` via a trap-aware harness mode

`isla-testgen` gained `--expect-trap-cause <mcause>` (`target.rs`'s `Target::expect_trap_cause`,
wired through `testgen.rs`, implemented in `generate_object_riscv.rs`'s `trap_vector` emission).
When set, the harness's trap vector — previously an unconditional fail on *any* trap — instead
checks `mcause` against the given value; on a match, it advances `mepc` past the trapping
instruction (2 or 4 bytes, decided by reading the instruction's own low 16 bits — porting the
exact fix already proven necessary in ACT4's own trap handler for compressed instructions, see
`act4/trap-handler-findings.md`'s ebreak finding, rather than re-discovering it) and `mret`s back
into the normal flow; anything else (wrong cause, or no trap at all) still fails as before.
`opcode_sweep.py`'s `TRAP_EXPECT` dict maps `ECALL`→11 (`E_M_EnvCall`, since the harness always
starts in Machine mode) and `EBREAK`→3 (`E_Breakpoint`), and both are verified passing on Sail and
Spike, RV32 and RV64. The negative path was checked too, not just the positive one: generating
`ecall` with a deliberately wrong expected cause (3 instead of 11) still fails, confirming the
check is real rather than a no-op.

This directly reuses ACT4's *proven logic* (the compressed-instruction EPC-advance fix
specifically), not ACT4's build — an earlier plan to "route these through ACT4's `--signature`
pipeline" turned out not to work as stated: ACT4's real trap handler lives in `rvtest_setup.h`,
unconditionally pulled in via every test's `#include "riscv_arch_test.h"`, which our generated
`--signature`-mode files never emit (confirmed directly: `generate_object_riscv.rs` never writes
an `#include`). Our earlier "ACT4-compatible" tests matched ACT4's *format* (entry symbol,
section names, header) closely enough to sit in ACT4's test tree and build via its Makefile, but
never actually linked against ACT4's real environment code — invisible for non-trapping
instructions (nothing ever jumps to `mtvec`), but would have meant our own preamble's `mtvec`
write silently overriding whatever ACT4's real boot sequence installed, for any trapping one. Kept
the harness self-contained instead, porting the one piece of ACT4's logic that was actually needed
(the EPC-advance fix) rather than restructuring around ACT4's macro system.

## The generic harness's real limit: control-flow and privilege-changing instructions

Every instruction the harness already handled (ALU, branch, load/store — see
[`rv_i-opcode-sweep-findings.md`](../isla-gen/rv_i-opcode-sweep-findings.md)) shares one property:
it either falls through to the next instruction or jumps somewhere the harness itself controls
(a known branch target). `ecall`/`ebreak`/`mret`/`sret` break that assumption outright:

- `ecall`/`ebreak` unconditionally call the model's `trap()` — confirmed by reading
  `execute ECALL`/`execute EBREAK` directly, not inferred from the failure. There's no trap
  handler wired into the harness's preamble, so the trap runs off into whatever's at the (mostly
  unconfigured) trap vector.
- `mret`/`sret` call `set_next_pc(prepare_xret_target(...))` and change `cur_privilege` — again
  read directly from `execute MRET`/`execute SRET`. The harness's fallthrough assumption (finish
  the instruction, compare state, halt) never applies; execution goes wherever `mepc`/`sepc`
  happened to solve to.

Empirically, both fail the same way: `sail_riscv_sim` prints `FAILURE: 1`, Spike prints
`*** FAILED *** (tohost = 1)` — i.e. the harness's own fail path *does* get reached eventually
(not a crash or a hang), just because control flow lands somewhere unintended, not because the
instruction's real semantics are wrong. This matches an earlier, separate finding for `ecall`/
`ebreak` specifically (see
[`act4/trap-handler-findings.md`](../act4/trap-handler-findings.md), found via ACT4's own
trap-handler infrastructure) — this sweep reconfirms it generically, for all four control-flow-
changing instructions, directly against this harness rather than ACT4's.

**`wfi` is a related but distinct risk.** Its Sail semantics (`Enter_Wait(WAIT_WFI)`) are legal to
implement as an immediate return (which Sail's own C emulator does) or a real wait (which Spike
does, correctly, since WFI is architecturally permitted to block until an interrupt). With no
interrupt ever pending in this minimal harness, Spike waits forever — a genuine hang, not a bug in
Spike or the model. Any future harness variant that includes `wfi` needs to either always follow
it with an interrupt injection, or accept it needs its own dedicated (not generic) handling.

**Fixing this is real, separate follow-up work** — a trap/privilege-aware harness variant (install
a real handler, expect a specific `mcause`, verify `mepc`/privilege post-state, then explicitly
resume) — not something to force into the single-instruction fallthrough model. Sizing that is
future scoping, not something this sweep resolves.

## The "shallow pass" caveat for `sfence.vma`/Svinval

The 4 fence-like instructions that do pass have no GPR side effects at all (they're TLB
maintenance operations) — the harness's only check is a GPR/memory diff, so passing here proves
"generates correctly, model accepts and retires it, doesn't crash or trap" and nothing about
*whether the TLB flush actually did the right thing*, which isn't observable through GPRs by
construction. That's a real, honest limit of instruction-level opcode coverage as a proxy for
address-translation correctness — see the S-mode gap analysis below.

## Two real generator bugs found and fixed while adding this coverage

Both are general fixes to `python-isla/model_opcodes.py`, not privileged-instruction-specific,
and both were previously silently losing instructions rather than erroring loudly:

1. **CSR operand kind wasn't modeled at all.** `CSRReg`/`CSRImm`'s assembly template embeds
   `csr_name_map(csr)` — a *`scattered mapping`* (one `mapping clause csr_name_map` per CSR,
   spread across dozens of files; see `core/csr_begin.sail`), not the single self-contained
   `mapping NAME : T <-> string = {...}` block `_parse_string_tables` looks for. Before this fix,
   every CSR-instruction template silently failed to evaluate and `csrrw`/`csrrs`/etc. were never
   generated — not reported as skipped, just absent. Fixed by special-casing `csr_name_map` into
   a new `("csr", None)` fragment kind, rendered as a fixed real CSR address (`0x340`, `mscratch`
   — plain R/W, Machine-only, no side effects) rather than by parsing the real name table:
   confirmed empirically that GNU `as` accepts a raw numeric CSR address in place of the symbolic
   name (`csrrw x1, 0x340, x2` assembles identically to `csrrw x1, mscratch, x2`).
2. **The *last* `mapping clause assembly` in a file was silently dropped.** The clause-boundary
   regex only recognized a fixed set of "next construct" lookaheads (`\nmapping `, `\nfunction `,
   ...) to know where one clause's template ends — a clause with nothing after it (end of file)
   never matched a terminator and was invisibly skipped. Hit for real by `SFENCE_VMA` (last thing
   in `base_insts.sail`) and `CSRReg` (last thing in `zicsr_insts.sail`) — both instructions
   simply didn't exist in the parsed instruction set until this was fixed. Fixed by adding `\Z`
   (end of string) as a valid terminator alongside the existing constructs.

Both bugs are the same shape: a real instruction silently vanishing from the swept set with no
error, rather than a loud failure. Worth keeping in mind for any future extension added to the
sweep — a suspiciously-low instruction count from `parse_instructions` is a real signal, not
noise.

## Extension-organized output, and per-extension assembler/simulator awareness

Two changes to `opcode_sweep.py`, driven directly by the RFP's ask to organize generated tests by
extension so they can be selectively included per configuration:

- **`--extension` labels the run** (defaults to the Sail file's own parent directory name, e.g.
  `Zicsr` from `extensions/Zicsr/zicsr_insts.sail` — matches the model's own layout with no extra
  bookkeeping) and determines the output subdirectory: `<out-dir>/<extension>/<mnemonic>.{s,ld,elf}`.
- **`EXTENSION_MARCH`** maps each extension label to the assembler/Spike extension suffix its
  instructions need. This isn't cosmetic — both the assembler and Spike reject or misbehave
  without it: `csrrw` fails outright (`unrecognized opcode ... extension 'zicsr' required`)
  without `-march=...zicsr`; `sinval.vma` **generates fine, passes Sail, and then fails
  specifically on Spike** (exit 1) unless `--isa=...svinval` is given explicitly — Spike enables
  Zicsr by default but not Svinval. Found by actually running it, not by reading Spike's docs.
  Every entry includes `zicsr` unconditionally since the harness preamble's own `csrw mtvec`
  needs it regardless of what's under test (same reasoning as the ACT4-compatibility header's
  always-appended `Zicsr`, see `act4/act4-integration-status.md`).
- `riscv-ir/riscv32.toml` and `riscv64.toml`'s `assembler` field (used by `isla-testgen`'s own
  final ELF build, separate from the sweep's own opcode-lookup step) gained `_svinval` for the
  same reason — Svinval instructions would otherwise fail at isla-testgen's own build step even
  after the sweep's own encoding step was fixed.

## Coverage measurement

The Sail-generated C++ emulator can track specification branch coverage at build time
(`-DCOVERAGE=ON`), confirmed by reading `sail-riscv`'s own `CMakeLists.txt` rather than assumed
from the README's one-paragraph mention. Getting a working coverage build required one extra step
not documented anywhere obvious: the coverage runtime (`libsail_coverage.a`) is a small Rust crate
shipped *inside* the Sail install (`$(opam var share)/sail/lib/coverage/`) that CMake expects
already built and copied into place — `cargo build --release && make` in that directory produces
it. `sailcov`, the report-generation tool, is similarly not part of the opam install as a binary;
it's an OCaml source directory in the `sail` opam package's own build tree, built with
`dune build ./main.exe`.

**Method**: built a separate `sail-riscv/build-coverage/` (CMake `-DCOVERAGE=ON`, kept apart from
the normal `/build` — both gitignored), ran every generated privileged-instruction ELF (all 15
instructions, RV32 and RV64 — 30 runs total, including the 5 that fail their own pass/fail check)
through it with `--sailcov-file`, then combined the accumulated trace with the build's
`sail_riscv_model.branch_info` manifest (the "all possible branches" file, also a build output)
via `sailcov` to get real per-file branch coverage.

**Important nuance**: Sail's branch-coverage tracking happens *inside* the model's own execution,
independent of whether our external `tohost` pass/fail check later succeeds. So even the 5
instructions that fail the harness's pass/fail check (`ecall`, `ebreak`, `mret`, `sret`) still
run their real `execute` clause and contribute real coverage — the harness limitation described
above affects test *verification*, not coverage *measurement*. This is worth knowing before
reading the numbers below as somehow discounted for the failing instructions — they aren't.

| File | Branches covered | Total branches | % |
|---|---|---|---|
| `zicsr_insts.sail` | 33 | 50 | 66% |
| `svinval_insts.sail` | 14 | 21 | 67% |
| `base_insts.sail` (whole file) | 126 | 348 | 36% |
| `base_insts.sail`, privileged-only lines (536–710: `ecall`/`mret`/`sret`/`ebreak`/`wfi`/`sfence.vma`) | ~14 | ~71 | ~20% (line-granularity estimate, not exact branch-id correlation) |

`base_insts.sail`'s whole-file 36% is **not** the privileged subset's own coverage — that file
also contains the entire base ALU/branch/load-store instruction set (already covered separately
by the `rv_i` sweep, not re-included in this run), so the meaningful number for this doc is the
line-restricted ~20% estimate. Zicsr and Svinval's own files are wholly privileged-instruction
code, so their 66%/67% are directly representative. The uncovered remainder in all three files is
overwhelmingly the untaken side of privilege/error-condition branches a single "happy path" test
per instruction can't reach by construction (illegal-CSR-access rejections, `SRET`'s
`mstatus[TSR]` trap path, `SFENCE_VMA`'s `User`/virtual-mode illegal-instruction arms, WARL/error
arms) — closing that gap needs deliberately-crafted negative/edge-case variants per instruction,
not more happy-path instances of the same opcode.

HTML reports (source-annotated, colour-coded covered/uncovered) and the raw coverage data are in
`/tmp/priv-coverage-report/` and `/tmp/priv-coverage.covdata` from this run — not checked into the
repo (scratch output), but the method above is fully reproducible.

## Mapping back to the RFP's privileged-ISA scope

- **M-mode, PMP/PMA (required)**: partially covered by earlier work, not by this sweep. PMP CSR
  access (`pmpcfg0`/`pmpaddr0` via `csrrw`) was previously unblocked at the isla-lib level (see
  `isla-gen-extension/PMP_PLAN.md`) and a real Sail-vs-Spike PMP reset-value mismatch was found
  and documented there (both legal per spec — the priv spec leaves PMP reset value
  platform-defined). What's still missing: an actual PMP-*violation* scenario (configure a
  restrictive PMP entry, attempt an access that should be denied, confirm the trap) — that's a
  multi-instruction scenario test, a different shape of test than this sweep's one-instruction
  model, and not yet built. The *primitive* now exists though: `--pmp-allow-all` configures a
  real PMP entry from the preamble, so a restrictive variant is a small step rather than a new
  mechanism. And a genuine PMP behavioural divergence has already come out of this work —
  [`findings.md`](findings.md) A1, where Sail correctly denies an unmatched non-Machine access
  and Spike permits it.
- **M-mode, trap and interrupt handling (required)**: **closed for all six base-ISA privileged
  instructions.** `ecall`/`ebreak` via `--expect-trap-cause`; `mret`/`sret` via
  `--preload-xepc`/`--preload-xpp`/`--pmp-allow-all`, landing in Supervisor with a verified
  negative control; `wfi` via `--pending-interrupt`, which raises a real machine software
  interrupt through the CLINT.

  One honest caveat on scope: `--pending-interrupt` makes an interrupt *pending and enabled* but
  deliberately leaves `mstatus.MIE` clear, so no trap is taken. That is the right shape for
  testing WFI, but it means full *interrupt delivery* — the trap actually being entered, `mcause`
  carrying the interrupt bit, the handler running — still hasn't been tested. The machinery to do
  it is now all present (CLINT access, `mie`, a trap-aware vector); the test isn't written.
- **S-mode, virtual memory / address translation (desirable)**: `sfence.vma`/Svinval opcodes
  generate and pass (all 15 in the table above now do), but per the "shallow pass" caveat, that only
  proves the instructions exist and don't crash — no actual page-table-walk/translation scenario
  (map a page, fault on an unmapped one, flush and confirm a stale translation is gone) has been
  attempted.
- **Hypervisor (out of scope)**: confirmed doubly out of scope — both by the RFP's own framing and
  because the model itself only stubs `currentlyEnabled(Ext_H)` with no instructions implemented.
  Nothing to test even if it were in scope.

## Hypervisor readiness — what's already generic vs. what actually blocks it

Leaving Hypervisor out for now (per the RFP's own scoping) doesn't mean designing it out of the
framework. Most of the framework generalizes to it for free, deliberately:

- **Parsing** (`model_opcodes.py`) works off whatever `mapping clause assembly` blocks exist in
  the Sail source — it has no notion of "H is special." The day `HFENCE.VVMA`/`HFENCE.GVMA`/
  `HINVAL.VVMA`/`HINVAL.GVMA` (all shaped like `SFENCE_VMA`/`SINVAL_VMA` — two plain register
  operands, already modeled) get real definitions, they parse with no code change.
- **Extension labeling and output layout** (`opcode_sweep.py`'s `_default_extension`) already
  infers the label from the model's own `extensions/H/` directory — `--extension H` and
  `<out-dir>/H/` work with zero code change.
- **March/isa awareness** is a one-line `EXTENSION_MARCH` entry away — reserved (commented out)
  in the source now, since there's no real instruction to verify a march string against yet.

**One real parser gap, found by checking rather than assuming**: `HLV.*`/`HSV.*` (hypervisor
load/store) address memory as `(rs1)` with no immediate offset — RISC-V's own hypervisor spec
confirms this addressing form. The current "mem" operand detection in
`model_opcodes.py::_build_variants` specifically requires an `imm(reg)` shape (that's how `lw`/
`sw`/`jalr` are structured); a bare `(rs1)` wouldn't match. Not fixed preemptively — the
instructions don't exist in this Sail checkout yet, so there's no real template text to parse
against, and guessing at Sail syntax ahead of the actual source would contradict how every other
part of this parser was built (against real code, not assumption). Small, targeted addition when
the day comes, same shape as this round's CSR fix.

**The real blocker isn't the test framework — it's the Golden Model, and there's already a real
path to closing it.** `extensions/H/hext_insts.sail` currently contains only
`currentlyEnabled(Ext_H)`'s guard clause (no instructions implemented), and 35 hard
`internal_error("Hypervisor extension not supported")` rejections across 10 files block Virtual
privilege mode throughout trap delivery, CSR access, and page-table walks. But this isn't a
from-scratch problem: upstream `sail-riscv` already has a substantial, maintainer-endorsed draft
(PR #612, ~3000 lines, currently orphaned with an open invitation to pick it up) that touches
almost exactly those same areas. Full sizing, the PR's own file list, and the recommended path:
[`hypervisor-design-sketch.md`](hypervisor-design-sketch.md). Once real H instructions exist
(whether from that PR or otherwise), the three framework pieces above mean picking it up on our
side should be fast; until then, there's nothing real to sweep.

## What's still open

- Trap/privilege-aware harness variant for `ecall`/`ebreak`/`mret`/`sret` (and, separately, a
  `wfi`-safe variant that doesn't risk hanging Spike).
- PMP-violation and address-translation *scenario* tests (multi-instruction, state-setup-dependent
  — a different generation shape than this sweep's one-opcode-at-a-time model).
- Interrupt delivery, not attempted at all yet.
- Config-awareness beyond RV32/RV64: F/D and VLEN/ELEN variation (per the RFP's CI-config list)
  haven't been touched by this sweep — none of the 15 privileged instructions are F/D/V-dependent,
  so this specific sweep doesn't need them, but the framework-wide config story is still open.
- QEMU/CVA6 weren't run for this sweep (would need `--boot-fixed-entry`); nothing
  privileged-instruction-specific blocks it, just not done today.
