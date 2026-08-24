# Complete Gap and Improvement Analysis
## Automated Test Generation Framework for the Sail RISC-V Golden Model

**Scope:** technical gap map for RFQ compliance. Not a project plan. No dates, no sprints.

**Evidence basis:** all findings below were produced by executing the framework, not by reading
code. Claims are marked **IMPLEMENTED**, **DEMONSTRATED**, **PARTIALLY IMPLEMENTED**,
**EXPERIMENTAL**, **PROPOSED**, or **UNKNOWN — REQUIRES VERIFICATION**.

**Two findings established during this analysis:**
the framework's own test suite is currently failing (`test_march_mabi_from_extensions`,
1 failed / 5 passed), and `python-isla` has no self-tests at all — `test_config.py` and
`scenario_tests.py` are generators, not tests of the framework.

---

# 1. Executive Summary

The framework has a **sound core and an unbuilt periphery**. Generation, ELF emission,
privileged scenarios, and coverage are real and demonstrated. Everything that turns those into a
*deliverable product* — configuration as an architecture, CI, reproducibility, packaging — is
absent or prototype-grade.

Three findings dominate:

1. **Configuration is not an architecture; it is a set of constants.** The VLEN bug is not a bug
   — it is the visible symptom of configuration being re-derived independently in four places
   (Sail config, isla IR, assembler march, simulator ISA string) with no single source of truth.
   Every configuration defect found is one instance of this.

2. **The framework depends on a forked Golden Model and cannot run without it.**
   `isla_testgen_init` / `isla_testgen_step` exist only in our fork; the IR build requires them
   by name. This directly threatens RFQ requirement 1 and Deliverable 5.

3. **Nothing verifies the framework itself.** Its own test suite is red, there is no CI in any of
   the three repositories, and the generated corpus is not persisted. The project can silently
   regress and has no mechanism to notice.

The methodology is defensible and now evidence-backed. The **engineering around it is not
production-grade**.

---

# 2. Current Architecture Understanding

**Three generators, two organisation schemes, one coverage system.**

| Component | Language | Role | Status |
|---|---|---|---|
| `isla-gen-extension` (isla-testgen) | Rust + Z3 | symbolic: solves for machine state | **DEMONSTRATED** |
| `autotest/sailtest` | Python | concrete oracle: model executes, state captured | **DEMONSTRATED** |
| template backend | Python | what neither engine can reach (4 tests) | **IMPLEMENTED** |
| `python-isla/*` | Python | sweep drivers, CSR/scenario generation, coverage | **DEMONSTRATED** |
| `sail-riscv` | Sail | the model *and* the oracle | **forked** |

**Actual verified pipeline:**

```
Sail sources ─→ parse assembly clauses (1280 insns, 0 unparsed)
             ─→ render asm ─→ cross-assembler ─→ objdump ─→ opcode bytes
             ─→ [symbolic: isla IR + Z3]  or  [oracle: run model, capture state]
             ─→ .s ─→ .o/.ld ─→ ELF (ELF32/64, EXEC, entry 0x80010000)
             ─→ run on Sail  AND  Spike (independent)
             ─→ coverage_report.py ─→ per-ELF + suite spans
             ─→ --uncovered ─→ [DEAD END: nothing reads it]
```

Two independent manifest formats exist: `tests.json` (python-isla) and `manifest.json`
(autotest). They do not interoperate.

---

# 3. Target End-State Architecture

Required stages, with the ones the RFQ actually forces:

| Stage | Required by | Purpose | Exists? |
|---|---|---|---|
| Config selection (single source) | Req 4 | one config object drives everything downstream | **NO** |
| Config → Sail | Req 4 | model behaviour | YES |
| Config → generator | Req 4 | legal instruction set for that config | PARTIAL |
| Config → toolchain | Req 4 | march/mabi | **HARDCODED** |
| Config → simulator | Req 4 | ISA string, VLEN | **HARDCODED** |
| Scenario/constraint construction | Req 5 | privileged state | YES |
| Test construction + ELF | Req 2 | deliverable artefact | YES |
| Classification/metadata | Req 3 | selection | YES (dual) |
| Execution + result collection | Deliv 3 | pass/fail + summary | PARTIAL |
| Per-ELF coverage | Req 6 | attribution | YES |
| Suite coverage | Req 7 | aggregate | YES |
| Gap analysis → targeted generation | *not required by RFQ* | closure | **NO** |
| CI integration | Deliv 4 | contractual | **NO** |

**Judgement:** the coverage→generation feedback loop is *desirable but not contractually
required*. It should not be allowed to block Deliverables 1–5.

---

# 4. Test-Generation Methodology Assessment

| # | Question | Answer | Status |
|---|---|---|---|
| 1 | Primary methodology | Hybrid: model-sourced instruction set; state via preamble or solver; body via oracle or symbolic | DEMONSTRATED |
| 2 | Why selected | Neither backend covers the space; gaps are complementary, measured | DEMONSTRATED |
| 3–4 | Alternatives | Constrained-random (undirected vs a branch metric); pure grammar (model already yields grammar); formal equivalence (answers a different question) | reasoned |
| 5 | Sail's role | **Three roles**: instruction source, oracle, coverage target — this triple role is the circularity risk | IMPLEMENTED |
| 6–8 | ISLA / SMT / isla-gen | Solves initial state for a feasible path; Z3 backend; `--all-paths-for` exists | DEMONSTRATED |
| 9 | Scenario representation | Python tuples `(name, opcodes, flags, expect)` — **hardcoded opcode hex** | PARTIALLY IMPLEMENTED |
| 10–11 | Constraints / assignments | isla flags (`--pmp-deny`, `--sv39`, `--preload-xepc`) → Z3 | DEMONSTRATED |
| 12 | Assignment → test | isla emits `.s` / `.ld`, assembles, links | DEMONSTRATED |
| 13 | Privileged scenarios | Preamble-constructed, not solver-constructed | DEMONSTRATED |
| 14 | Legality per config | **Assembler round-trip only** — no config-derived legality check | **WEAK** |
| 15 | ELF | verified ELF32/64 | DEMONSTRATED |
| 16 | Validation | Sail + Spike differential, expected-state tables, negative controls | PARTIAL |
| 17 | Coverage | Sail branch / function / expression spans | DEMONSTRATED |
| 18 | Coverage → generation | **Not connected** | **NOT IMPLEMENTED** |

**Verdict:** coherent yes · reproducible partial (no version capture) · extensible yes (backend
registry) · configuration-aware **no** · suitable for privileged yes · suitable for scale partial.

**Methodological weaknesses:**

- Scenarios are hardcoded hex, not derived — they cannot follow the model.
- Legality is assembler-checked, not config-checked, so an instruction illegal in the *selected
  config* can still be emitted.
- Sail's triple role means coverage and oracle share a failure mode.
- No gap → generation link.

---

# 5. Configuration Architecture Assessment

**The underlying architectural problem:** configuration is *re-derived* at four points instead of
*propagated* from one.

| Derivation point | Location | Current behaviour |
|---|---|---|
| Sail model | `SAIL_CONFIG` dict | hardcoded to 2 of 16 |
| isla IR | compile-time `vlen` baked into type | requires IR rebuild |
| assembler march | `toolchain.py:104` | `march += "_zvl128b"` |
| Spike ISA | `runner.py:40` | `_SPIKE_VLEN_EXT = "zvl128b"` |

These four must agree and nothing enforces it. When they disagree the test **passes on Sail and
fails on Spike** — indistinguishable from a model bug.

**Required conceptual flow:** one `Configuration` object, loaded from the Golden Model's own JSON
(already the canonical format — the oracle proves this by consuming `rv64d_v256_e64.json`
directly), which *derives* every downstream artefact: Sail `--config`, generator legality set,
march/mabi, simulator ISA string, and the metadata stamped into each test.

**What must be configurable:** XLEN, extension set, VLEN/ELEN, PMP entry count and granularity,
satp modes, endianness, counter enables.

**Validation:** reject incompatible combinations *at load* with a named reason (e.g. V requires
`elen_exp >= 6`, `vlen_exp >= 7`), rather than surfacing as
`RuntimeError: oracle returned 0 values`.

---

# 6. Privileged ISA Assessment

## M-mode

| Capability | Current | Limitation | Required |
|---|---|---|---|
| PMP | violation + 2 controls, both sims | `pmpcfg0` struct-typed fails generation; entry-0 divergence (A2) open | config-driven entry count; CSR generation fix |
| PMA | region-attribute configs | **RV64 `.d` forms only** → skips at RV32 | RV32 forms |
| Traps | ecall/ebreak/illegal via `--expect-trap-cause` | narrow cause set | full cause enumeration |
| Interrupts | delivery **and** delegation, both sims | machine-level delegation impossible (`legalize_mideleg` hardwires MEI/MTI/MSI = 0) — architectural, not a defect | document; extend to timer/external |
| CSRs | 172 model-derived; 37 curated swept | wide run never completed | complete + record |
| Privilege transitions | mret/sret to M/S/U + control | — | RV32 parity |

## S-mode

| Capability | Current | Limitation |
|---|---|---|
| Address translation | Sv39 mapped + fault + **2 controls**, both sims | **Sv39 / RV64 only** |
| Sv32 | **absent** | every S-mode scenario *skips* at RV32 — reads as success |
| Sv48 / Sv57 | absent | — |
| Svadu / Svade / Svnapot / Svpbmt / Svvptc | absent | PTE content variants |

## Hypervisor

**Should remain out of scope** — the RFQ permits it. Note: an orphaned upstream draft exists
(sail-riscv PR #612) and should be checked before any from-scratch proposal.

---

# 7. Coverage Methodology Assessment

**What is measured:** Sail *source* spans — functions (738), branches (4084), expressions
(10335), total 15157 — from the model's own `branch_info` manifest with `-DCOVERAGE=ON`. This is
**source/code coverage of the model**, not instruction or architectural-feature coverage.

**Appropriateness:** correct, because the RFQ asks for coverage "in terms of the Sail code of the
Golden Model". It is also the only metric that makes hand-written and generated suites
comparable.

**Verified working:** per-ELF (replayed alone, absolute + greedy marginal, **reconciliation
assert** union == suite total), suite aggregation, per-file tables, scope exclusions with stated
justification.

**Structural defects:**

1. **A failing test writes no coverage file.** Coverage is *coverage from passing tests only*.
   Negative controls must fail — therefore **the tests that prove the suite can fail are
   invisible to coverage**. This is a design contradiction, not a limitation.
2. Coverage is not configuration-tagged. Spans from an RV32 run and an RV64 run are unioned
   without provenance, so "80%" cannot be attributed to a configuration.
3. No per-extension coverage attribution from spans (only `coverage_matrix.py`'s per-mnemonic
   status).

---

# 8. Coverage Quality Assessment

- **100% is not achievable and not meaningful.** The manifest includes hypervisor code (out of
  scope), `device_tree.sail`, `simple_interrupt_generator.sail`, and the Sail standard library
  (already excluded, 25/182 spans, with reason).
- **Exclusions are handled** via scope files with in-file justification — a genuine strength that
  should be preserved and formalised.
- **Cross-configuration comparison is currently invalid** — see defect 2 above.
- **Coverage cannot yet identify missing architectural scenarios**: it names uncovered spans, not
  the machine state needed to reach them. Converting a span into a scenario is a human step.

---

# 9. Turn-Key / Productization Assessment

Every step a clean user cannot currently perform:

| Step | Blocker | Status |
|---|---|---|
| Install deps | `python-isla` has **no manifest**; Z3 path defaults to a developer cache | **BLOCKED** |
| Build | Rust build required; IR rebuild needs `isla-sail` + OCaml + **the forked model** | **BLOCKED** |
| RV64 symbolic | `riscv64.ir` / `riscv64.toml` **untracked** — absent from a fresh clone | **BLOCKED** |
| Reproducibility | committed `riscv32.ir` (18,151,866 B) != working tree (18,151,902 B) | **BROKEN** |
| Select config | no CLI | **MISSING** |
| Persist suite | defaults `/tmp/*`; none exist on disk | **MISSING** |
| Suite summary | per-driver prints; no unified result artefact | PARTIAL |

---

# 10. CI Integration Assessment

**0 workflows across all three repositories.** Deliverable 4 requires *approved and merged* PRs
into the Golden Model CI — currently at stage zero.

**Model CI reality (verified):** `ci.yml` matrices on OS/CMake, not ISA. The 16 configs come from
`test/CMakeLists.txt` (`vlen in {64,128,256,512}` x `elen in {32,64}` x `rv{32,64}d`), and
**VLEN=512 + ELEN=32 is explicitly excluded upstream** for runtime reasons. This corrects an
earlier audit finding: that failing combination is **not a required config**.

**Recommendation: CI must run pre-generated, version-pinned suites, not generate on every run.**
Generation is 1.3 s for a trivial instruction but minutes for `pmpcfg0` and unbounded
(OOM-killed) for `aes64im` — unacceptable CI variance. Generation belongs in a separate scheduled
job producing a released artefact.

---

# 11. Documentation Assessment

**Exists:** README (with the organisation/selection section), QUICKSTART, TECHNICAL_PLAN,
METHODOLOGY, findings.md (A1–A4 / B1–B6 / C1–C13), extension-coverage-tracker, TESTING_GUIDE.

**Missing (user):** installation from clean checkout; configuration guide; troubleshooting;
result-summary interpretation.

**Missing (developer):** architecture / module boundaries; how to add a backend; how to add a
scenario; IR rebuild procedure with upstream implications; coverage scope policy; release/update
process for tracking the model.

**Defective:** `TESTING_GUIDE.md` hardcodes `/home/jk/...` paths; `METHODOLOGY.md` §1.1 contains a
**false [V]-tagged claim** about using the unmodified model.

---

# 12. Upstream / Golden Model Assessment

| Change | Classification | Upstreamable? | Evidence |
|---|---|---|---|
| `isla_testgen_init` / `isla_testgen_step` in `main.sail` | **REQUIRED for framework** (symbolic path) | Likely — entry points for external tooling | absent from `upstream/master`; IR build requires `--isla-preserve` on both |
| De-scattered CSR accessors (`csr_end.sail`, +418 lines) | **REQUIRED for framework** | Uncertain — large, invasive | same fork commit |
| `reset_pmp()` fix | **Optional improvement / possible defect fix** | Yes | same commit |
| A4 shadow-stack PTE assertion fix | **Model defect fix** | Yes — already PR'd on fork | prior work |
| Other files in the 35-file commit | **UNKNOWN — REQUIRES VERIFICATION** | — | not individually analysed |

**Critical:** the fork commit is a single 35-file, +671/−454 blob. It **must be decomposed into
separately-reviewable PRs** before any upstream attempt. No maintainer will review it as one
commit.

---

# 13. Complete Improvement Register

| ID | Area | Current Problem | Required Improvement | Why Required | RFQ | Pri | Dep |
|---|---|---|---|---|---|---|---|
| CFG-01 | Config | Config re-derived in 4 places | Single `Configuration` object loaded from Golden Model JSON | root cause of all config defects | 4 | **P0** | — |
| CFG-02 | Config | `SAIL_CONFIG` hardcoded to 2 of 16 | Config path as CLI arg to every driver | cannot select a config | 4 | **P0** | CFG-01 |
| CFG-03 | Config | `runner.py:40` `_SPIKE_VLEN_EXT="zvl128b"` | Derive Spike ISA from config | VLEN=256 fails 3/3 on Spike | 4 | **P0** | CFG-01 |
| CFG-04 | Config | `toolchain.py:104` `march += "_zvl128b"` | Derive march/mabi from config | same root cause | 4 | **P0** | CFG-01 |
| CFG-05 | Config | Illegal combos surface as `RuntimeError: oracle returned 0 values` | Validate at load, reject with reason | unusable diagnostics | 4 | P1 | CFG-01 |
| CFG-06 | Config | VLEN baked into isla IR at compile time | Per-config IR build or documented constraint | symbolic cannot vary VLEN | 4 | P1 | CFG-01 |
| CFG-07 | Config | isla `B129` type caps VLEN <= 128 | Document as hard boundary; route >128 to oracle | structural | 4 | P1 | CFG-06 |
| CFG-08 | Config | `--config-override` overrides isla TOML, not model config | Rename / clarify | misleading capability | 4 | P3 | — |
| CFG-09 | Config | RV32+V unsupported (`model-v generates for RV64 only`) | Generalise RV64-only signature path | model CI includes rv32d vector | 4 | P1 | CFG-01 |
| CFG-10 | Config | Config not recorded in coverage output | Tag spans with config | cross-config comparison invalid | 6,7 | P1 | CFG-01 |
| MODEL-01 | Upstream | Framework requires forked model | Upstream entry points, or generate them | **Req 1 + Deliv 5** | 1 | **P0** | — |
| MODEL-02 | Upstream | 35-file single commit | Decompose into reviewable PRs | unreviewable as-is | Deliv 5 | **P0** | MODEL-01 |
| MODEL-03 | Upstream | 45 commits behind master | Rebase + rebase policy | "current model" | 1 | **P0** | MODEL-02 |
| MODEL-04 | Upstream | Fork rot risk | CI job tracking upstream | staleness recurs | 1 | P2 | MODEL-03 |
| MODEL-05 | Upstream | A4 fix unmerged | Drive PR to merge | Deliv 5 | Deliv 5 | P2 | — |
| GEN-01 | Generation | Scenarios are hardcoded opcode hex | Derive from model / assembler | cannot follow the model | 5 | P2 | — |
| GEN-02 | Generation | Legality checked only by assembler | Config-derived legality filter | can emit illegally-configured tests | 4,5 | P1 | CFG-01 |
| GEN-03 | Generation | F/D fails via `opcode_sweep` (`riscv_f32Add does not exist`) | Route FP to oracle automatically | manual routing today | 4 | P1 | — |
| GEN-04 | Generation | Two manifest formats | Converge on one schema | selection is ambiguous | 3 | P2 | — |
| GEN-05 | Generation | ELF `Tag_RISCV_arch` omits extension under test | Do not rely on it; document | misleading identifier | 3 | P3 | — |
| PRIV-01 | S-mode | **Sv32 absent → RV32 scenarios skip** | Implement Sv32 | largest coverage gap; skip reads as pass | 5 | **P1** | CFG-01 |
| PRIV-02 | S-mode | Sv48 / Sv57 absent | Depth variants | S-mode completeness | 5 | P2 | PRIV-01 |
| PRIV-03 | S-mode | PTE content variants absent | Svadu / Svade / Svnapot / Svpbmt / Svvptc | "highly desirable" | 5 | P2 | PRIV-01 |
| PRIV-04 | M-mode | PMA uses RV64 `.d` forms only | RV32 atomic forms | 24 branches; RV32 parity | 5 | P1 | CFG-01 |
| PRIV-05 | M-mode | `pmpcfg0` struct-typed generation fails | Fix or route | PMP CSR hole | 5 | P2 | — |
| PRIV-06 | M-mode | 172-CSR sweep never completed | Complete, record both XLENs | unrecorded claim | 5 | P1 | — |
| PRIV-07 | M-mode | Trap causes narrow | Enumerate full cause set | trap completeness | 5 | P2 | — |
| PRIV-08 | M-mode | 3 RV32 scenario regressions | Diagnose / fix | red in our own corpus | 5 | P1 | — |
| PRIV-09 | M-mode | Machine-interrupt delegation impossible | Document as architectural | avoid re-investigation | 5 | P3 | — |
| COV-01 | Coverage | Failing tests write no coverage → controls invisible | Separate control accounting | design contradiction | 6,7 | **P1** | — |
| COV-02 | Coverage | No per-extension span attribution | Map spans → extension | Req 3 + 7 interaction | 3,7 | P2 | — |
| COV-03 | Coverage | Scope files informal | Formalise exclusion policy | defensibility | 6,7 | P2 | — |
| COV-04 | Coverage | `--uncovered` never consumed | Gap → scenario workflow (human-in-loop first) | closure | — | P3 | COV-02 |
| VAL-01 | Validation | No mechanical vacuity / mutation enforcement | Corpus-wide enforcement | **47.7% precedent** | 2,6 | **P0** | — |
| VAL-02 | Validation | Oracle circular against Sail | Mandate Spike; fail suite if absent | tooling warns but permits | 2 | P1 | — |
| VAL-03 | Validation | Retry-once masks nondeterminism | Record flakes explicitly | `c.srli` unexplained | 2 | P2 | — |
| VAL-04 | Validation | Failure classification ad-hoc | Taxonomy: model / framework / config / flake | status reporting need | 2 | P1 | — |
| VAL-05 | Validation | QEMU / CVA6 never demonstrated | Verify or remove | **UNKNOWN** | Deliv 3 | P1 | — |
| SCALE-01 | Scale | `aes64im` / `cpop` OOM-killed | Formalise routing on OOM vs timeout | machine-wide outages occurred | — | P2 | — |
| SCALE-02 | Scale | No parallel generation | Parallelise sweeps | full sweep > 1 h/XLEN | — | P2 | — |
| SCALE-03 | Scale | No duplicate detection | Dedup by span signature | 2/5 ELFs added nothing | 7 | P3 | COV-02 |
| SCALE-04 | Scale | Resume only in `csr_sweep` | Generalise checkpointing | sweeps lost 3x | — | P2 | — |
| REPRO-01 | Repro | No version capture | Record Sail / isla / toolchain / Spike / config / seed per suite | cannot reproduce | Deliv 2 | **P1** | CFG-01 |
| REPRO-02 | Repro | Committed IR != working IR | Remove blob; build reproducibly | silent divergence | 1 | **P0** | MODEL-01 |
| REPRO-03 | Repro | `riscv64.ir` / `.toml` untracked | Build from source in setup | RV64 impossible on clean clone | 4 | **P0** | REPRO-02 |
| REPRO-04 | Repro | No persisted corpus | Versioned suite output location | organisation is moot without it | 3 | P1 | — |
| ARCH-01 | Arch | `/home/jk` fallback in `paths.py:111` | Remove | portability | Deliv 1 | P2 | — |
| ARCH-02 | Arch | No dependency manifest in `python-isla` | Add | install impossible | Deliv 1 | **P0** | — |
| ARCH-03 | Arch | Duplicated march / ISA logic in 2 components | Extract shared module | root of CFG-03/04 | 4 | P1 | CFG-01 |
| ARCH-04 | Arch | `python-isla` is flat scripts | Package with entry points | maintainability | Deliv 1 | P2 | ARCH-02 |
| ARCH-05 | Arch | Three repos, no coordinated versioning | Release / pinning policy | community maintenance | Deliv 1 | P2 | — |
| FTEST-01 | Framework tests | **Own suite failing** (1/6) | Fix + gate | framework unreliable | Deliv 1 | **P0** | — |
| FTEST-02 | Framework tests | `python-isla` has **no tests** | Unit + integration tests | untested core | Deliv 1 | **P1** | ARCH-02 |
| FTEST-03 | Framework tests | No config-matrix test | Per-config smoke test | regressions invisible | 4 | P1 | CFG-01 |
| FTEST-04 | Framework tests | No negative tests for the framework | Assert bad configs rejected | CFG-05 needs proof | 4 | P2 | CFG-05 |
| CI-01 | CI | **0 workflows anywhere** | Framework CI | Deliv 4 precondition | Deliv 4 | **P0** | ARCH-02 |
| CI-02 | CI | No config matrix in CI | Matrix over the model's 14 exercised configs | Req 4 proof | 4 | P1 | CFG-01, CI-01 |
| CI-03 | CI | No Golden Model CI PR | Draft, submit, drive to merge | **Deliv 4 is merge-gated** | Deliv 4 | **P0** | CI-01 |
| CI-04 | CI | Generation cost unbounded | Pre-generated pinned suites in CI | runtime variance | Deliv 4 | P1 | REPRO-04 |
| DOC-01 | Docs | `METHODOLOGY.md` §1.1 false [V] claim | Correct it | states the opposite of fact | 1 | **P0** | — |
| DOC-02 | Docs | `TESTING_GUIDE.md` hardcodes `/home/jk` | Rewrite portably | unusable by others | Deliv 1 | P1 | — |
| DOC-03 | Docs | No clean-checkout install guide | Write + verify on clean env | Deliv 1 | Deliv 1 | **P1** | ARCH-02 |
| DOC-04 | Docs | No developer architecture doc | Module boundaries, extension points | community maintenance | Deliv 1 | P1 | ARCH-04 |
| DOC-05 | Docs | No IR-rebuild doc with upstream context | Document coupling | maintenance blocker | 1 | P1 | MODEL-01 |
| DOC-06 | Docs | No troubleshooting guide | Common failures + causes | usability | Deliv 1 | P2 | — |
| EX-01 | Examples | No example suite-generation script | `examples/generate_suite.sh <config>` | **Deliverable 2** | Deliv 2 | **P0** | CFG-02 |
| EX-02 | Examples | No example run / summary script | `examples/run_suite.sh` + summary | **Deliverable 3** | Deliv 3 | **P0** | RES-01 |
| RES-01 | Results | No unified result format | JSON result DB + summary | Deliverable 3 | Deliv 3 | **P0** | VAL-04 |
| RES-02 | Results | No per-test result records | Per-test status / stdout / timing | Deliverable 3 | Deliv 3 | P1 | RES-01 |
| RES-03 | Results | Only Sail + Spike wired | Add QEMU or Whisper | RFQ names them | Deliv 3 | P1 | VAL-05 |
| LIC-01 | Licence | 18 MB IR blob derived from BSD-2 model | Human review; prefer removal | attribution | 8 | P1 | REPRO-02 |
| LIC-02 | Licence | No NOTICE / attribution at root | Add third-party attributions | Apache-2.0 practice | 8 | P2 | — |
| LIC-03 | Licence | Fork distribution unreviewed | Human / legal review | distribution model | 8 | P2 | MODEL-02 |
| PERF-01 | Perf | No timing baseline | Instrument per-stage timing | no optimisation without evidence | — | P2 | — |
| PERF-02 | Perf | CI runtime unknown | Measure after CI-01 | Deliv 4 feasibility | Deliv 4 | P2 | CI-01 |

---

# 14. Improvement Prioritization

## P0 — blocks architecture or RFQ acceptance (17 items)

- **CFG-01…04** — the configuration defect is architectural; every VLEN symptom traces here.
  *Evidence:* VLEN=256 passes Sail, fails 3/3 on Spike, root-caused to two hardcoded constants.
- **MODEL-01…03** — Req 1 is checkable in five minutes by a reviewer.
  *Evidence:* entry points verified absent from `upstream/master`; IR build requires them by
  name; 45 commits behind.
- **VAL-01** — *Evidence:* 47.7% of a corpus once passed while comparing empty expected-state
  tables.
- **REPRO-02 / 03** — *Evidence:* committed IR != working IR (36-byte delta); `riscv64.ir`
  untracked.
- **ARCH-02, FTEST-01, CI-01, CI-03** — *Evidence:* no dependency manifest; own test suite red;
  zero workflows; Deliverable 4 is merge-gated and at stage zero.
- **DOC-01** — a published document asserts the opposite of verified fact.
- **EX-01, EX-02, RES-01** — Deliverables 2 and 3 do not exist.

## P1 — required for a major RFQ capability (24 items)

Notably **PRIV-01 (Sv32)**: at RV32 the S-mode scenarios *skip*, which reads as success — the
single most misleading gap in the project. **COV-01**: negative controls are structurally
invisible to coverage. **FTEST-02**: the largest component has no tests.

## P2 — production quality (21 items) · P3 — desirable (7 items)

---

# 15. Dependency Graph

```
CFG-01 (single Configuration object)
 ├─→ CFG-02 (CLI selection) ─→ EX-01 (Deliverable 2)
 ├─→ CFG-03/04 (Spike + toolchain derivation) ─→ ARCH-03 (shared module)
 ├─→ CFG-05 (validation) ─→ FTEST-04
 ├─→ CFG-06 ─→ CFG-07 (VLEN boundary)
 ├─→ CFG-09 (RV32+V), GEN-02 (legality), PRIV-01/04
 ├─→ CFG-10 ─→ COV-02 ─→ COV-03, SCALE-03, COV-04
 └─→ FTEST-03 ─→ CI-02

MODEL-01 (upstream entry points)
 ├─→ MODEL-02 (decompose) ─→ MODEL-03 (rebase) ─→ MODEL-04 (tracking CI)
 │                        └─→ LIC-03
 ├─→ REPRO-02 (drop IR blob) ─→ REPRO-03 (build IR) ─→ LIC-01
 └─→ DOC-05

ARCH-02 (dependency manifest)
 ├─→ ARCH-04 (package) ─→ DOC-03, DOC-04
 └─→ FTEST-02 ─→ CI-01 ─→ CI-02, CI-04, PERF-02
                       └─→ CI-03 (Deliverable 4)

VAL-04 (failure taxonomy) ─→ RES-01 ─→ RES-02, EX-02 (Deliverable 3)
                                    └─→ RES-03 (QEMU/Whisper) ← VAL-05

VAL-01 (vacuity enforcement) ── independent, gates every coverage claim
REPRO-04 (persist corpus) ─→ CI-04
```

**Must be solved first, in order:** `CFG-01` · `MODEL-01` · `ARCH-02` · `VAL-01`.
Nothing downstream is safe to build until these land.

---

# 16. RFQ Traceability Matrix

| Improvement | RFQ Req | Deliverable | Current Evidence | Required End State |
|---|---|---|---|---|
| MODEL-01/02/03 | 1 | 5 | fork required; 45 behind | upstream or upstreamable, PRs merged |
| GEN-05 | 2 | 1 | ELF verified valid | unchanged; metadata not from ELF attrs |
| VAL-01 | 2, 6 | 1 | 47.7% precedent | every test provably able to fail |
| GEN-04, REPRO-04 | 3 | 1, 2 | dual manifests; corpus not persisted | one schema, persisted, versioned |
| CFG-01…10, ARCH-03 | 4 | 1, 4 | 2 of 16 configs; VLEN broken | config-driven end to end |
| PRIV-01…08 | 5 | 1 | M-mode yes; S-mode Sv39/RV64 only | RV32 parity + Sv32 |
| COV-01/02/03 | 6, 7 | 1 | per-ELF + suite work; controls invisible | config- and extension-tagged |
| LIC-01/02/03 | 8 | 1 | Apache-2.0 + BSD-2 | reviewed, attributed |
| DOC-01…06 | — | 1 | one false claim; no dev docs | user + developer docs |
| EX-01 | — | 2 | absent | one command per config |
| EX-02, RES-01/02/03 | — | 3 | absent | run + collect + summary |
| CI-01/02/03/04 | — | 4 | 0 workflows | **merged** model-CI PRs |

---

# 17. Deliverable Acceptance Criteria

**D1 — Repository + documentation.** Clean-machine clone → documented install → generate → run →
coverage, with no file editing and no undocumented environment variable. Developer docs
sufficient for a Golden Model maintainer to add a backend and a scenario unaided. Framework's own
test suite green in CI.

**D2 — Example generation.** `examples/generate_suite.sh <golden-model-config.json>` produces an
extension-organised, manifest-carrying suite for **any** of the model's configs, with recorded
provenance (Sail / isla / toolchain / Spike versions, seed, config hash) sufficient to reproduce
byte-identically.

**D3 — Simulator run + summary.** `examples/run_suite.sh <suite> --simulator {spike|qemu|whisper}`
→ per-test status, timing, stdout/stderr on failure, classified failures, machine-readable result
file, human summary. Must run on a simulator we did not write.

**D4 — Golden Model CI.** Five distinct states, only the last counts: local implementation → PR
created → reviewed → approved → **merged**. Currently at *pre-implementation*.

**D5 — Model modifications.** Per change: no-modification-required / identified / implemented
locally / PR created / approved / **merged**. Currently: two changes required, implemented
locally, **no PR for the entry points**.

---

# 18. Top 10 Technical Risks

1. **Upstream rejects the de-scattered CSR accessors** (+418 lines, invasive) — would strand the
   symbolic backend. *Highest risk; no mitigation identified.*
2. **Deliverable 4 depends on third-party review latency** — merge is outside our control and
   cannot be compressed by effort.
3. **Configuration rework touches every component** — high blast radius, and the current test
   suite cannot detect regressions.
4. **Vacuity enforcement may invalidate existing pass numbers** — the honest outcome of VAL-01
   could be a large reported regression.
5. **isla `B129` caps VLEN <= 128 permanently** — the config matrix can never be fully served by
   the symbolic path.
6. **Sail's triple role** (instruction source, oracle, coverage target) means one model change
   can move all three numbers at once.
7. **Fork divergence accelerates** — already 45 commits; grows with every upstream release.
8. **Generation cost is unbounded** (OOM kills, not timeouts) — threatens any CI runtime budget.
9. **RV32+V is unimplemented and in the model's CI matrix** — a visible Req 4 gap.
10. **No CI means silent regression** — the framework's own suite is *already* red and nobody
    noticed.

---

# 19. Recommended Architectural Decisions

1. **Adopt the Golden Model's JSON config as the single source of truth** — the oracle already
   proves it consumes it directly. Do not invent a config format.
2. **Retain** the model parser, oracle, coverage system, scenario/preamble harness, and the
   negative-control discipline.
3. **Refactor** configuration into one propagated object; extract the duplicated march/ISA logic;
   package `python-isla`.
4. **Replace nothing.** No evidence supports discarding any component.
5. **Narrow the symbolic backend** to what it uniquely does — solving for machine state — rather
   than treating it as the general engine.
6. **Split generation from CI**: CI consumes pinned, released suites; generation runs on a
   schedule.
7. **Decompose the fork commit immediately**, before any other upstream work.
8. **Treat coverage as reporting, not as a control loop**, for this deliverable — the loop is not
   RFQ-required and would consume P0 time.

---

# 20. What Should NOT Be Done

- **Do not pursue coverage-guided generation now.** It is not RFQ-required, and the one measured
  attempt (`--all-paths-for`) returned 5 of 194 branches. It would consume effort that
  Deliverables 2–4 need.
- **Do not upstream the 35-file commit as-is.** It will be rejected and will damage credibility
  for the PRs that matter.
- **Do not commit the 18 MB IR blob.** It is a derived artefact of a BSD-2 model, already
  diverges from the working tree, and creates a licensing question for no benefit.
- **Do not "fix" the VLEN bugs individually.** Patching `runner.py:40` and `toolchain.py:104`
  hides the architectural defect and it will regrow at the next derivation point.
- **Do not chase 100% coverage.** It is unreachable (hypervisor, device tree) and pursuing it
  optimises the metric rather than the verification.
- **Do not add QEMU / CVA6 support before verifying the existing claims.** Both are currently
  **UNKNOWN**; adding a third unverified simulator multiplies the problem.
- **Do not let generated ELFs into the framework repository.** Persist suites as versioned
  release artefacts.
- **Do not treat local CI as Deliverable 4.** Only merged upstream PRs satisfy it — the
  distinction is contractual.
- **Do not implement Sv48 / Sv57 before Sv32.** Sv32 unblocks the entire RV32 privileged path;
  the others are depth on a working harness.

---

# Appendix — Items explicitly not verified

- **RV32 + F/D via the oracle** — plausible, not tested. **UNKNOWN — REQUIRES VERIFICATION.**
- **QEMU and CVA6 execution** — code paths exist; every run reported `-- not installed`.
  **UNKNOWN — REQUIRES VERIFICATION.**
- **The 172-CSR `--from-model` sweep** — the wide run was interrupted and never recorded.
  **UNKNOWN — REQUIRES VERIFICATION.**
- **Individual files within the 35-file fork commit** — not analysed one by one.
  **UNKNOWN — REQUIRES VERIFICATION.**

No repository files, GitHub issues, milestones, or project boards were modified in producing this
analysis.
