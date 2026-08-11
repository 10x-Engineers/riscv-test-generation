# ACT4 compatibility — implementation status

Tracks the concrete steps laid out in
[`isla-gen-extension/ACT4_COMPATIBILITY_PLAN.md`](../../isla-gen-extension/ACT4_COMPATIBILITY_PLAN.md)
against what's actually implemented in `generate_object_riscv.rs`/`target.rs`/`testgen.rs`, so
the plan document (which was written before implementation) and the real state of the code don't
drift apart. See that file for the full reasoning behind each step; this doc only records status
and where each piece lives.

| # | Step | Status |
|---|---|---|
| 1 | Cross-check isla-sail compile config against ACT4's own `sail.json` | Done — diffed against `config/sail/sail-rv32-max/sail.json`; found it missing `isa_version` (a schema-conformance gap in ACT4's own tracked file against current `sail_riscv_sim`, not something on our side to fix). Worked around locally (`/tmp/act4-sail-config-patched/sail.json`) rather than editing ACT4's tracked config. |
| 2 | Adopt ACT4's exact `tohost`/`fromhost` declaration | Done — `.pushsection .tohost,"aw",@progbits` / `.align 8` / `.type tohost, @object` / `.size tohost, 8`, byte-for-byte matching ACT4's `RVMODEL_DATA_SECTION` macro. Unconditional (not gated behind `--signature`) — this was also the fix for QEMU's separate `HTIF tohost must be 8 bytes` requirement, so it applies to every generation mode. |
| 3 | Add optional ACT4-format signature output | Done, behind `--signature` — `begin_signature`/`end_signature` region (`.data`, sized `xlen_bytes * sig_count`), written via a signature-dump loop in `finish:` that stores each checked register's already-verified value with `sw`/`sd` (XLEN-aware). |
| 4 | Add `START_TEST_CONFIG`/`END_TEST_CONFIG` header | Done, behind `--signature` — generated from the same `Target`/instruction metadata already available (`RVTEST_ISA`, `REQUIRED_EXTENSIONS` list, march string), matching `docs/DeveloperGuide.md`'s documented format. `Zicsr` is always appended to the extension list regardless of what the tested instruction needs, since the preamble's `csrw mtvec` requires it — found the hard way (`-march=rv32i` rejected `csrw mtvec,x6` during a compile-check). |
| 5 | Prove it via `config/sail/` first | Done — a generated test built with `--signature` was placed and run through ACT4's own `sail-rv32-max` pipeline before attempting Spike or a real DUT config. |
| 6 | Directory placement / `make` integration | Done — three changes, previously scoped as optional/stretch, all implemented: <br>• **Entry symbol**: `rvtest_entry_point` is a second label aliased at the same address as our own `preamble`, so ACT4's `ENTRY(rvtest_entry_point)` linker directive resolves correctly without changing our own harness's control flow.<br>• **Section naming**: the first code-region section is named `.text.init` instead of `.text0` (only when `name == 0`) — matches ACT4's fixed linker script's one fixed-address section; a no-op when using our own generated `.ld` instead.<br>• **Two-pass build compatibility**: verified against ACT4's real `-DSIGNATURE`/`-DRVTEST_SELFCHECK` two-pass compile (`build_plan.py`'s `gen_compile_tasks`), not just a single ad-hoc invocation.<br>Demonstrated with a generated file placed directly in ACT4's own tree (`tests/rv32i/I_isla_generated/I-addi-00.S`, untracked — a working example, not a permanent addition to the ACT4 checkout). |
| 7 | Coverpoint tagging | Explicitly out of scope for now (per the plan) — registering generated tests against ACT4's `coverpoints/priv/*.yaml` schema is separate, later work; steps 1–6 only require tests to *run* under ACT4's pipeline, not be formally coverage-tracked by it. |

## How to generate an ACT4-compatible test

```
./target/release/isla-testgen -A riscv-ir/riscv32.ir -C riscv-ir/riscv32.toml -a riscv32 \
    --memory-region 0x80020000-0x80030000 --signature --required-extensions I -o out/addi 0x00400093
```

`--signature` turns on the header + signature-region output; `--required-extensions` (default
`I`) feeds `REQUIRED_EXTENSIONS`/the `-march` string in the generated header — pass whatever
extension the instruction under test actually needs (`Zicsr` is added automatically). Omit
`--signature` for the framework's own default self-checking layout (no header, no signature
region — internal `tohost` pass/fail only).

## One important constraint carried over from the plan

`--signature` and `--boot-fixed-entry` (see
[`python-isla/model-sourced-generation.md`](../python-isla/model-sourced-generation.md)) are
**mutually exclusive in a single generated ELF** — ACT4's fixed linker script expects test bytes
at `0x80000000` (`.text.init`), while `--boot-fixed-entry` needs the harness preamble there
instead, for QEMU/CVA6's fixed-boot-address requirement. A given generation run targets one
downstream consumer or the other; this was true when the plan was written and remains true now
that both are implemented — it's a property of the two consumers' physical-layout requirements,
not something further engineering on our side resolves.

## What's still open

- **Real ACT4 test-list integration** (a `make`-target or script that drives `isla-testgen` and
  drops output directly into ACT4's `tests/rv32i_m/<ext>/src/`-style layout, then invokes ACT4's
  own `make <config>`) has been proven manually (step 6) but isn't yet wired as a standing,
  reusable script — worth doing before this is presented as "push-button ACT4 integration"
  rather than "verified compatible."
- **RV64 `--signature` mode** hasn't been separately re-verified against an RV64 ACT4 config
  (`sail-rv64-max`) the way RV32 has — the code path is XLEN-aware (`xlen_bytes`-derived
  `sw`/`sd`, signature region sizing), but an actual RV64 ACT4 run hasn't been done yet.
