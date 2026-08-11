# Comparison against the hand-written suite

The measurements behind `documentation/METHODOLOGY.md` §4. Saved here because a
number in a document that cannot be recomputed is an assertion, not evidence —
and because one figure in an earlier draft had to be re-derived from scratch
when its raw data turned out never to have been kept.

## What is here

`data/` — span sets, as `FILE|line|col|line|col` strings. A "span" is one branch
in the Sail model's own source, matched against the model's
`sail_riscv_model.branch_info` manifest (15,157 spans).

| File | Contents |
|---|---|
| `act4_attribution.json` | I+M RV32: hand-written spans, ours, and for each branch only they reach, **how many of their 47 tests reach it**. This is the file §4.7's 184/194 split comes from. |
| `allpaths_spans.json` | The rejected path-enumeration experiment: branch sets for one-test-per-instruction versus `--all-paths-for`. |
| `priv_act4.json` | Privileged RV32, hand-written: 145 tests, 1493 branches. |
| `priv_ours.json` | Privileged RV32, our scenario corpus alone: 18 tests, 1044 branches. |
| `priv_all_ours.json` | Every RV32 corpus we generate: 286 tests, 1206 branches. |

`scripts/` — what produced them, in the order they were run:

1. `attribute_act4.py` — the I+M comparison and the shared-setup attribution.
2. `allpaths_experiment.py` — the falsification test for path enumeration.
3. `csr_closes_gap.py` — how much of the deficit the existing CSR sweep closes.
4. `priv_compare.py` → `priv_ours.py` → `priv_all_ours.py` — the privileged
   comparison. These three replay onto the same shared `sail_coverage` file and
   **must not run concurrently**.

## Running them again

These are experiment scripts, not tooling: paths to the Sail checkout, the
hand-written suite and the scratch directory are **hardcoded at the top of each
file** and will need editing. They are kept as-run rather than generalised, so
that what produced each number is unambiguous.

They need:

- a Sail model built with `-DCOVERAGE=ON` (kept separate from the ordinary
  build, so a coverage run cannot silently use an uninstrumented emulator);
- the hand-written suite built **for the Sail target**, not the Spike target —
  Spike-built ELFs poll a UART the Sail platform does not drive and hang;
- `base.isa_version` present in that suite's Sail config. The model added it as
  a required field after that config was written, and without it every build
  fails a schema check before any test runs.

## Two caveats that apply to every number here

**Coverage is measured from passing tests only.** A test that exits nonzero
writes no coverage file at all — the Sail runtime flushes on clean exit. Every
figure understates.

**Each ELF is replayed alone**, with the coverage file deleted first, so "what
this test reaches" means that test rather than what it happened to add given
replay order.
