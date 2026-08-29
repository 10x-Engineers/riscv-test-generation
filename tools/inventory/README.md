# Sail-derived architectural inventory

`extract_inventory.py` derives a complete architectural inventory directly from
the Sail RISC-V model and writes it as Markdown, CSV and JSON. It exists to
produce the **reachable-target denominator** the test-generation framework
measures coverage against, and to keep that denominator honest when the model
changes.

## Running it

```bash
# against whatever checkout paths.py resolves
python3 extract_inventory.py --out-dir inventory-out

# against a specific revision (e.g. a clean upstream worktree)
git -C ~/Documents/sail-riscv worktree add /tmp/sail-upstream <commit>
python3 extract_inventory.py \
    --model-dir /tmp/sail-upstream/model \
    --repo-dir  /tmp/sail-upstream \
    --out-dir   inventory-out
```

Outputs: `inventory.md`, `inventory.json`, and one CSV per category
(`extensions`, `instructions`, `csrs`, `exceptions`, `interrupts`,
`privilege_modes`, `translation_modes`, `pmp_pma`, `targets`).

`inventory-upstream-05d7b1fb/` is a checked-in run against upstream
`05d7b1fb` (2026-06-30), kept so a reviewer can read the result without
building anything.

## What it reads

The model is the only source. Nothing is supplied from the specification, from
prior knowledge, or from an earlier model revision. Each category comes from a
specific declaration form:

| Category | Declaration read |
|---|---|
| Extensions | `enum clause extension`, `mapping clause extensionName`, `function clause hartSupports` |
| Instructions | `union clause instruction`, expanded by enum-typed operands |
| Encodings / prerequisites | `mapping clause encdec`, `mapping clause encdec_compressed`, and their `when` guards |
| CSRs | `mapping clause csr_name_map` |
| Exceptions | `union ExceptionType` + `exceptionType_bits` |
| Interrupts | `enum InterruptType` + `interruptType_bits` |
| Privilege modes | `enum Privilege` |
| Translation modes | `enum SATPMode` |
| PMP | `pmp/*.sail` registers, `let sys_pmp_*` config bindings, the `Pmpcfg_ent` bitfield and its fields, `PmpAddrMatchType` |

Every row carries a `source` of `file:line`, so any figure can be traced back to
the declaration that produced it.

## Two things it deliberately does not do

**It does not equate architectural targets with Sail branch spans.** A branch
span is a property of how the model is *written*; an architectural target is a
property of what the architecture *does*. One target may cover many spans, and
many spans belong to no target. Mixing them yields a denominator that moves when
the model is refactored, so branch coverage stays with the coverage tooling and
is reported separately.

**It does not guess.** Where the model does not state something unambiguously,
the field reads `Not directly derivable from Sail`. Current instances:

- **CSR read/write behaviour** — `csr_name_map` gives address and name only.
- **Privilege-mode entry/return mechanisms** — no single declaration states them.
- **PMA region attributes** — these are per-region fields of a Golden Model
  *configuration* file, not Sail declarations, so no PMA count exists in the
  model to extract.
- **Privileged vs unprivileged** — the model carries no such flag. It is derived
  from each extension's config-key prefix, and every extension row records which
  rule classified it (`classified_by`) so the call can be audited or overridden.

CSR privilege *is* reported, decoded from address bits [9:8] — that is a
property of the address itself rather than an inference about the register.

## Counting notes

- **Instruction forms (852)** expand enum-typed operands only. `UTYPE : (bits(20),
  regidx, uop)` with `uop = {LUI, AUIPC}` is two architectural instructions.
  Value-typed operands (`bits(20)`, `regidx`) contribute one form each: they are
  ranges for the solver to explore, not distinct behaviours.
- **Encdec clauses (457)** is a separate cross-check, counting explicitly written
  encoding clauses across both `encdec` (398) and `encdec_compressed` (59). It
  differs from the form count because some instructions encode an enum operand
  through a sub-mapping in a single clause.
- **CSRs (343)** excludes the catch-all `csr_name_map = reg <-> hex_bits_12(reg)`
  fallback, which renders unnamed addresses rather than naming a register.
- **Exceptions and interrupts** are reported both as declared (25 / 14) and
  excluding reserved entries (21 / 11). Only non-reserved causes become targets.
