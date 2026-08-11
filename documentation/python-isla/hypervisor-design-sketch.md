# Hypervisor extension — design sketch (not an implementation)

Scoped as "design sketch only": size the real work, ground it in what actually exists (both in
this model and upstream), and leave the framework ready to pick it up — no Golden Model or
generator code changes in this pass. Companion to
[`privileged-instruction-coverage.md`](privileged-instruction-coverage.md)'s "Hypervisor
readiness" section, which covers the framework-side seam (the `HLV`/`HSV` operand-shape gap);
this doc covers the model side, which is where the real work actually is.

## Where things stand today, confirmed rather than assumed

- The model's own `README.md` "supported extensions" list — the one enumerated in this
  conversation, all ~90 lines of it — **does not mention H anywhere**. Checked directly
  (`grep -n` against the README), not inferred. The model doesn't even claim partial H support;
  it isn't on the list at all.
- `extensions/H/hext_insts.sail` contains only `currentlyEnabled(Ext_H)`'s guard clause. No
  instructions implemented.
- **35 hard `internal_error(..., "Hypervisor extension not supported")` rejections across 10
  files** (`sys_exceptions.sail`, `sys_control.sail`, `base_insts.sail`, `vmem_pte.sail`,
  CSR-access paths in `sys_regs.sail`, and others) — every one an explicit crash on
  `VirtualUser`/`VirtualSupervisor` privilege mode, confirmed by reading the actual `match`
  arms, not by counting string occurrences blindly.

## Upstream already has a substantial draft — this is the real finding

Checked `riscv/sail-riscv`'s own Extension Roadmap wiki page (linked from this model's own
README) and its GitHub PRs directly:

- **PR #612, "Hypervisor extension"** (`defermelowie`, opened 2024-11-06, still open/unmerged).
  **2471 additions, 557 deletions across 38 files** — 5 new files
  (`riscv_csr_hext.sail`, `riscv_hext_control.sail`, `riscv_hext_regs.sail`,
  `riscv_insts_hext.sail`, `riscv_types_hext.sail`, ~860 new lines total) plus substantial
  modifications concentrated almost exactly where the 35-rejection-site grep above already
  pointed: `riscv_sys_control.sail` (+239/-147), `riscv_vmem.sail` (+314/-102),
  `riscv_sys_regs.sail` (+87/-52), `riscv_vmem_ptw.sail` (+114/-26), `riscv_vmem_tlb.sail`
  (+88/-27), `riscv_types.sail` (+232/-39), `riscv_sync_exception.sail` (+56/-5),
  `riscv_insts_zicsr.sail` (+67/-28), `riscv_insts_base.sail` (+88/-37). Two independent signals
  (a grep against this checkout, and someone else's real implementation diff) landing on the same
  set of files is a good cross-check that the scope estimate is right, not a guess.
- **The PR is explicitly orphaned, with an open invitation already on the table.** Maintainer
  `pmundkur` (a core `sail-riscv` maintainer) commented in March 2025 asking to rebase, and
  offered to "pick it up and continue" if the author couldn't. The author (`defermelowie`)
  replied: *"I'm afraid that I won't be able to dedicate a lot of time to this work soon...
  feel free to pick-up and continue with this PR... I'm willing to help where possible."* No
  visible follow-up commits since.
- **It's currently `dirty`** (real merge conflicts against current `master`) — not because the
  approach is wrong, but because the whole model was reorganized into the
  `extensions/`/`core/`/`sys/`/`postlude/` layout this project has been working against
  *since* the PR was authored. The PR still references the old flat `model/riscv_*.sail` naming
  (e.g. `model/riscv_sys_control.sail` instead of today's `model/sys/sys_control.sail`) — a real
  remapping exercise per file, not a mechanical `git rebase`.
- **Real H-specific test infrastructure already exists and was used to validate this PR**,
  independent of anything in this repo: the author's own
  [`riscv-hext-asm-tests`](https://github.com/defermelowie/riscv-hext-asm-tests), a fork of José
  Martins' [`riscv-hyp-tests`](https://github.com/josecm/riscv-hyp-tests), and a fork of
  dramforever's `riscv-hs-tests`. Regression coverage included the existing `riscv-tests` suite
  and booting Linux on OpenSBI without virtualization.

## The realistic path is picking this up, not a from-scratch design

Given an explicit, still-open maintainer invitation sitting on a substantial, previously-working
implementation, an independent from-scratch H implementation would be duplicated effort against
the spirit of an already-negotiated handoff — not the right thing to propose. The realistic path,
if this is pursued:

1. Rebase PR #612's content onto the current file layout — remap each modified old-path file to
   its new home (e.g. `riscv_vmem.sail`'s changes now likely split across several `sys/vmem_*.sail`
   files, given today's finer-grained layout), and re-verify against the current Sail version.
2. Re-validate against the PR's own already-established test list: `riscv-tests` regression,
   Linux+OpenSBI boot, and the three H-specific external suites above — before adding anything
   from this project's own framework on top.
3. Coordinate with `pmundkur`/`defermelowie` directly rather than reimplementing in isolation —
   this is squarely deliverable #5's "if the Golden Model needs extending, file PRs" territory,
   and here the PR essentially already exists and is waiting for exactly this kind of pickup.

## What this project's framework would need once H lands

Already covered in detail in
[`privileged-instruction-coverage.md`](privileged-instruction-coverage.md#hypervisor-readiness--whats-already-generic-vs-what-actually-blocks-it):
extension labeling, output layout, and march/isa awareness already generalize with no code
change; `HLV`/`HSV`'s bare `(rs1)` addressing needs a small, targeted parser addition once real
instructions exist to check the addition against. One more option worth noting now, given this
research: rather than inventing hypervisor scenario tests from zero, the three existing H-specific
suites above (particularly `riscv-hyp-tests`, purpose-built for exactly this) are a plausible
integration target the same way ACT4 already is — worth a closer look once instructions actually
exist to generate against.

## Sizing and recommendation

This is genuinely substantial, independent of anything this project does: ~3000 lines of diff from
one experienced contributor, still incomplete after ~9 months even before the required rebase for
the file reorganization. Combined with the fact that the RFP explicitly scopes H as optional/
future, and this project's own *required* M-mode gaps (trap-handler infrastructure, PMP-violation
scenarios, interrupt delivery — see `privileged-instruction-coverage.md`'s "What's still open")
aren't closed yet: **recommend flagging PR #612 and the maintainer invitation as the realistic
route in any proposal text, but not starting implementation work now.** The framework-side prep
(reserved `EXTENSION_MARCH` entry, this doc, the operand-shape note) means picking it up later
should be fast once either the Golden Model work lands or priorities shift.
