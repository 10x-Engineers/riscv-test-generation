# isla fork — what changed and why

Every source-level change in the `10x-Engineers/isla` fork (commit `00ffeb4`, vs. upstream
`rems-project/isla`) that was needed to make `isla-testgen` work against the RISC-V Golden
Model. Seven files, four independent reasons. This is the file-level companion to
[`pipeline-flow.md`](pipeline-flow.md) (which shows the *runtime* flow through `isla-lib`);
this diagram shows the *fork's diff*, grouped by why each file needed to change.

## Flowchart

```mermaid
flowchart TD
    ROOT["<b>10x-Engineers/isla</b> @ 00ffeb4<br/>fork of rems-project/isla"]

    ROOT --> G1
    ROOT --> G2
    ROOT --> G3
    ROOT --> G4

    subgraph G1["Reason 1 — upstream Sail API drift (mechanical compat fixes)"]
      direction TB
      JIB["<b>isla-sail/jib_ir.ml</b><br/>2 sites: I_funcall's bool tag → Call/Extern variant"]
      PLUGIN["<b>isla-sail/sail_plugin_isla.ml</b><br/>Same I_funcall fix (3 sites) +<br/>CT_bit→CT_fbits 1, rewrite-pass<br/>reordering, dropped/added rewrite<br/>passes, removed opt_magic_hash"]
      JIB -.->|"why: upstream isla-sail must track<br/>whatever Sail's Jib IR type looks like<br/>on the Sail version isla-sail links against"| G1NOTE1[" "]
      PLUGIN -.->|"why: same driver — plus Sail's own<br/>rewrite pipeline and type-checker<br/>changed shape between versions"| G1NOTE2[" "]
    end

    subgraph G2["Reason 2 — the PMP struct/vector engine bug (the real blocker)"]
      direction TB
      PRIMOP1["<b>isla-lib/src/primop.rs</b><br/>vector_access + vector_update:<br/>new struct-typed-element branch<br/>(per-field ITE instead of smt_value)"]
      PRIMOP1 -.->|"why: smt_value has no case for<br/>Val::Struct — RISC-V's PMP config is a<br/>vector(64, Pmpcfg_ent), a vector of<br/>bitfield structs, not bitvectors.<br/>This is what unblocked symbolic<br/>PMP register access (PMP_PLAN.md)."| G2NOTE[" "]
    end

    subgraph G3["Reason 3 — target-generality (RV32 vs. the RV64-only assumption)"]
      direction TB
      MEM["<b>isla-lib/src/memory.rs</b><br/>Overlap::Unique read path: use the<br/>symbolic address's own bit-width<br/>instead of hardcoded 64"]
      MEM -.->|"why: isla-lib was written assuming<br/>every target is 64-bit; RV32 addresses<br/>are 32-bit, so the hardcoded width<br/>silently mis-sized the read"| G3NOTE[" "]
    end

    subgraph G4["Reason 4 — Sail primops the model calls that isla had no implementation for"]
      direction TB
      BV["<b>isla-lib/src/bitvector.rs</b><br/>+ trailing_zeros() on the BV trait"]
      B129["<b>isla-lib/src/bitvector/b129.rs</b><br/>trailing_zeros() impl"]
      B64["<b>isla-lib/src/bitvector/b64.rs</b><br/>trailing_zeros() impl"]
      PRIMOP2["<b>isla-lib/src/primop.rs</b><br/>smt_ctz + count_trailing_zeros primop"]
      PRIMOP3["<b>isla-lib/src/primop.rs</b><br/>LR/SC reservation stubs:<br/>cancel_/valid_/match_/load_reservation"]
      PRIMOP4["<b>isla-lib/src/primop.rs</b><br/>sys_enable_experimental_extensions stub<br/>+ vector_init (aliased to undefined_vector)"]

      BV --> B129
      BV --> B64
      B129 --> PRIMOP2
      B64 --> PRIMOP2
      PRIMOP2 -.->|"why: a base/bitmanip instruction's<br/>Sail semantics call count_trailing_zeros;<br/>isla had no extern for it at all"| G4NOTE1[" "]
      PRIMOP3 -.->|"why: sys/sys_reservation.sail declares<br/>these as externs with no isla impl —<br/>stubbed conservatively (never reserved)<br/>so anything calling them (incl.<br/>reset_sys()'s cancel_reservation())<br/>doesn't crash with VariableNotFound"| G4NOTE2[" "]
      PRIMOP4 -.->|"why: two more model externs isla<br/>had no implementation for — stubbed<br/>to the same defaults a real<br/>deployment would use"| G4NOTE3[" "]
    end

    classDef note fill:none,stroke:none,color:#666,font-style:italic;
    class G1NOTE1,G1NOTE2,G2NOTE,G3NOTE,G4NOTE1,G4NOTE2,G4NOTE3 note;
```

## Reading the grouping

| Reason | Files | Nature | Proposal relevance |
|---|---|---|---|
| 1. Sail API drift | `jib_ir.ml`, `sail_plugin_isla.ml` | Mechanical — adapts to type/API changes in the Sail version isla-sail compiles against. No new capability. | Evidence of the Rust/Isla stack's maintenance cost (criterion #2) — this is the "drift" the `EXECUTION_PLAN.md` cites as a reason not to make ISLA the backbone. |
| 2. PMP struct/vector fix | `primop.rs` (`vector_access`, `vector_update`) | A genuine engine gap closed — isla couldn't symbolically index a vector of structs at all before this. | The single most significant fix in the set — directly unblocks the **PMP** row of the coverage-risk matrix (`🟠 partial → readable`). |
| 3. Target generality | `memory.rs` | A latent RV64-only assumption, exposed by adding an RV32 target. | Necessary for any RV32 config; not RISC-V-specific once fixed — benefits all future 32-bit targets. |
| 4. Missing primops | `bitvector.rs`, `b129.rs`, `b64.rs`, `primop.rs` (4 stubs + 1 real feature) | Model calls externs isla never implemented — some given real semantics (`count_trailing_zeros`), some conservatively stubbed (atomics reservation, experimental-extensions gate). | The atomics stubs are exactly why the coverage-risk matrix marks **Atomics** as "moderate (reservation stubbed)" rather than proven — real LR/SC semantics still need implementing, not just unblocking. |

**Net read:** of 7 changed files, only 1 (`primop.rs`'s struct-vector branch) is a hard engine
capability that was missing; the rest is either mechanical version-tracking (Reason 1),
a latent bug incidentally exposed by RV32 (Reason 3), or externs stubbed just enough to not
crash (Reason 4). That matches the plan's framing: ISLA is workable as a specialist, but each
new instruction class risks hitting another one of these — which is the argument for probing
canaries early (`comparison/coverage-risk-matrix.md`) rather than assuming the stack is now
fully general.
