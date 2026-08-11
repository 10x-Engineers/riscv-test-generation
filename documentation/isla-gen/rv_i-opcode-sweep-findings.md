# rv_i systematic opcode sweep — bugs found and fixed

Result of building `python-isla/opcode_sweep.py`: a driver that pulls every instruction's
exact encoding from `riscv-opcodes` (canonical, current — not the abandoned 2019 OCaml
generator) for a given extension, constructs one legal opcode per instruction, and feeds them
to `isla-testgen` sequentially, verifying each generated ELF actually passes on
`sail_riscv_sim` (not just "isla-testgen exited 0").

**Final result for `rv_i` (37 instructions): 35/37 pass end-to-end.** The other 2 (`ecall`,
`ebreak`) fail by design of the generic harness, not a bug — see below.

Three real, previously-undiscovered bugs were found and fixed in the course of getting there.
Two live in the shared `isla-testgen` harness code and affect any target, not just this sweep.

## Problem 1 — opcode hex string loses leading zeros → wrong bit-width parsed

**Where:** our own driver (`opcode_sweep.py`), not `isla-testgen` itself.

**Symptom:** every opcode whose top hex digit was `0` failed with an SMT sort error —
`Sorts (_ BitVec 20) and (_ BitVec 16) are incompatible`.

**Cause:** Python's `hex(0x00010093)` returns `'0x10093'` — it strips leading zeros.
`isla-testgen`'s opcode-string parser reads the string's digit count as the instruction's
bit-width (5 hex digits → tried to build a 20-bit value), not just the numeric value.

**Fix:** format opcodes as `f"0x{opcode:08x}"` (explicit 8-digit zero-padding) instead of
`hex(opcode)`.

## Problem 2 — address-taking instructions defaulted to unreachable/self-referential encodings

**Where:** our own driver.

**Symptom:** branches failed generation outright ("Unsatisfiable"); loads/stores/`jal`/`jalr`
either failed or — worse — `jal`/`jalr` "succeeded" at generation while producing an ELF that
would hang forever if actually executed (a jump-to-self).

**Cause:** every variable field defaulted to `0`. For `rs1` on a load/store, `x0` is the
*hardwired* concrete zero register, so `address = imm12` alone can never reach the declared
`--memory-region` with a 12-bit immediate. For branches/`jal`, an immediate of `0` means
"branch/jump to your own address" — the harness either rejects it as unsatisfiable, or (for
`jal`) accepts it because a single unconditional self-jump is technically satisfiable at
generation time, without anyone checking that *running* it would loop forever.

**Fix:** non-zero `rs1` (kept symbolic, not architectural zero); real B-type/J-type immediate
bit-shuffle encoders (`encode_b_imm`/`encode_j_imm`) producing a small, valid non-zero
displacement instead of `0`.

## Problem 3 — MMIO write silently untracked, compounded by an off-by-one register-table bug

This is the one that took real digging (instrumented `extract_state.rs`, rebuilt, traced
instruction-by-instruction against `sail_riscv_sim`). Two independent bugs stacked on each
other for stores specifically.

### 3a — a fully-symbolic address register can resolve into MMIO, where writes aren't modeled

**Where:** `isla-testgen`'s harness (affects any target using a fully free address register,
not RiscV-specific).

**Symptom:** `sb`/`sh`/`sw` (and initially `lbu`/`lh`/`lhu` too) generated a "successful"
ELF whose expected post-state simply never mentioned the store's target address at all — the
check only covered the instruction bytes themselves.

**Cause:** the current `sail-riscv` model's compiled PMA (physical memory attribute) table
defines an MMIO region at `0x02000000`–`0x12000000`, explicitly marked
`read_idempotent: false, write_idempotent: false`. With `rs1` left fully symbolic, isla is
free to solve it to *any* architecturally-valid address — including MMIO — and a write there
doesn't produce a normal, comparable `Event::WriteMem` the way an ordinary RAM write does.
(The TOML's `[symbolic_addrs]` section looks like it exists to bias exactly this kind of
address into a safe window — it's parsed into `isla-lib`'s config struct, but grep confirms
it is **never actually read anywhere else in the codebase**. Dead configuration.)

**Fix (workaround, not a code change):** prepend a `lui rs1, <safe-addr>` instruction before
the actual load/store, so the address register is concrete and guaranteed to land inside our
declared `--memory-region` (real RAM) rather than leaving it symbolic. Implemented in
`opcode_sweep.py::build_lui_opcode`.

### 3b — register-table read offset assumes a fixed 31-slot layout; the table is actually compact

**Where:** `isla-testgen`'s `generate_object_riscv.rs` — a genuine bug in shared harness code,
now fixed, affecting every future test on this target.

**Symptom:** even after 3a's fix landed the store's data at the right address, the generated
ELF *still* failed — tracing showed `bne x2, x30` comparing the real (correct) `x2` against a
garbage expected value read from the wrong offset.

**Cause:** `write_register_data` writes `initial_gpr_values`/`final_gpr_values` as a **compact**
list containing only whichever registers actually appear in the trace (e.g. just `x2`, if
that's the only one touched) — not a fixed 31-entry table indexed by register number. But the
code that reads it back computed each register's offset as `4 * (register_number - 1)`,
i.e. assuming `x1` always occupies slot 0. Whenever `x1` isn't present (any instruction with
no `rd` — every store, plus any load/store test run in isolation without also touching `x1`),
the fixed-number formula points at the wrong slot, reading whatever happens to follow in
memory. This is why loads mostly "worked" by accident (`rd=x1` filled slot 0, matching the
formula) while every store failed outright, and why it only became fully visible once a
correctly-addressed store (via fix 3a) let execution reach this line at all.

**Fix:** in both loops (`preamble`'s initial-GPR load, `finish`'s final-GPR compare), compute
each register's offset from its **position in the actual list** (`4 * i`, via `.enumerate()`)
rather than from the register number. The final-GPR loop still advances the position counter
for skipped `entry_reg`/`exit_reg` entries (since `write_register_data` still reserves a slot
for them), it just doesn't emit a compare for them.

## What this means for the coverage-risk matrix

- **Base ALU, Branch/jump, Load/store (aligned)** rows: now backed by a full, systematic sweep
  of every `rv_i` instruction (not just one hand-picked canary each), all passing on both
  `isla-testgen` generation and `sail_riscv_sim` execution.
- **Traps/exceptions** (`ecall`/`ebreak`): confirmed failing *by design* — the generic
  pass/fail harness installs a trap handler that fails on any unexpected exception, and these
  two instructions intentionally trap. Testing them correctly needs a trap-aware harness
  variant. Unchanged status: still ◻ untested for the *real* trap-handling class — this sweep
  just confirms the generic harness correctly refuses to silently pass them.
- Fix 3b (the register-offset bug) was **latent in every prior single-instruction test too** —
  it only stayed invisible because `rd` was always forced to `x1`, coincidentally satisfying
  the broken formula. Any future multi-instruction sequence, or any single instruction with no
  `rd`, would have hit it. Worth flagging as a real correctness gap that predates this sweep.
