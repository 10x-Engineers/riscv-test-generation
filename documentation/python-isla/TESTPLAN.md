# Deriving a testplan from the Sail model

`python-isla/testplan.py` reads the model's own branch structure and produces a
testplan: every target the model implements, what stimulus reaches it, whether
the current corpus reaches it, and what to generate for the ones it does not.

It exists because the two tools either side of it answer different questions.
`coverage_report.py` says *how much* of the model is reached. `coverage_analysis.py`
turns the uncovered remainder into a stimulus queue. Neither is a plan: a plan
has to exist before any test runs, has to enumerate the whole target set rather
than the current shortfall, and has to name each target in terms a reviewer
recognises — an instruction, a privilege transition, a CSR field — rather than a
file and a line number.

## Running it

```bash
cd python-isla

# 1. the ranges no generated test can reach, derived from the sources
python3 derive_span_exclusions.py -o /tmp/excl.txt

# 2. replay the corpus (~110s for 1,878 ELFs) so status is current
python3 coverage_report.py

# 3. the plan, with current status joined in
python3 testplan.py \
    --coverage ~/Documents/sail-riscv/sail_coverage \
    --exclude-spans /tmp/excl.txt \
    -o testplan --xlsx
```

Without `--coverage` the same command produces the plan as it stands before any
test runs — every item *not started*, which is the form to review the plan in
rather than the form to work from.

`--scope rfp-scope.txt` takes the same scope file as `coverage_report.py`.

Outputs: `testplan.md` (reviewer-facing), `testplan.csv` (one row per plan
item), `testplan-spans.csv` (one row per span, the audit trail), `testplan.json`
(for the generator), and `testplan.xlsx` with both sheets filtered.

## How a span becomes a plan item

The target set is the model's instrumented span manifest, so the plan is
complete by construction: it cannot omit a behaviour the model implements,
because every behaviour the model implements is compiled into a span. Four
derivation steps turn one into an item, each read from the sources rather than
from a table maintained here.

**1. Ownership.** Every span falls inside exactly one top-level definition.
`function clause execute RTYPE(...)` owns its spans; so does `pmpCheck`.

**2. Discrimination.** Inside an execute clause the model discriminates
instructions with a `match` on the operand enum:

```sail
function clause execute RTYPE(rs2, rs1, rd, op) = {
  X(rd) = match op {
    ADD  => X(rs1) + X(rs2),
    SRA  => shift_bits_right_arith(...),
```

Each arm is its own span. So an uncovered span at `base_insts.sail:246` is not
"an expression in RTYPE" — it is *`sra` was never executed*.

**3. Naming.** The arm's pattern is an enum constructor, and the model already
states its assembly text in a mnemonic mapping (`SRA <-> "sra"`). Those mappings
are unreachable *as code* — `derive_span_exclusions.py` removes them from the
denominator for exactly that reason — but they are the model's own authority on
what a constructor is called. Excluded from measurement, used for description.

**4. Stimulus.** Where no arm applies, two things are tried in order. If the
span's own line carries a discriminating guard, it is classified into what a
generator must arrange: a privilege level, a CSR field, a configuration key, an
XLEN. If it does not — the common case for helper functions, which carry no
guard at all — the call graph is walked backwards to the instructions that reach
it, so `carryless_mul` is reported as *execute one of: CLMUL, CLMULH,
VCLMUL_VV…* rather than as an unexplained gap.

Anything still unrecognised is reported as `unclassified` rather than guessed
at. A wrong directive costs a generation cycle; an honest gap costs a minute of
reading.

## The ownership cross-check

`branch_info`'s `F` rows carry the function name the compiler assigned. Those
1941 named spans are a control on the containment index, which has to carry the
other 39,466 spans that have no name — a parsing bug there would misattribute
most of the plan with nothing to reveal it. The check runs on every invocation
and prints the agreement rate; below 90% it warns that the plan should not be
trusted.

It earns its place. On the first run it reported 89.0%, and the disagreements
were real: `_DEFS` did not allow for the `private` modifier, so every `private
function` was swallowed by the definition above it. That is the same omission
that cost `extract_inventory.py` its PMP address-match types. With it fixed the
rate is 99.9% (696/697), the single remaining disagreement being a naming
convention rather than a misattribution.

## Two kinds of "cannot be reached"

These are kept apart on purpose, because they justify different things.

**Held unreachable** — from `derive_span_exclusions.py`. Whole declaration forms
reasoned about structurally: the disassembly direction of a bidirectional
mapping is not reachable by running a program, for any such mapping. Strong
enough to leave the denominator.

**Out of reach** — decided per span, from its own text: enum-to-text mappings
in another declaration form, diagnostic string builders (`exceptionType_to_str`),
and Hypervisor code the model does not implement. These stay *in* the
denominator and are reported in their own section. A per-span judgement is not a
strong enough basis to move a coverage figure, so they are excluded from the
schedule but not deducted from the percentage.

The distinction is what keeps the headline number honest: the plan is allowed to
say "do not schedule this", and not allowed to say "so it does not count".

## Crosses

Two different things get called a cross, and the plan's answer differs for each.

### Operand-value crosses: no, and it cannot

ACT4's `cr_rs1_rs2_edges` crosses 11 edge values on `rs1` against 11 on `rs2`,
per instruction. The plan covers none of these, and extending it would not help,
because the model has no branch on operand values for arithmetic:

```sail
ADD  => X(rs1) + X(rs2),
```

That is **one span**, and it is covered the first time any `add` executes,
whatever the operands. All 121 combinations are indistinguishable to source
coverage. This is a property of the metric, not a gap in the tooling, and it is
the reason the RVVI/SVA coverpoints are measured *alongside* this plan rather
than replaced by it. Anyone reading a Sail coverage percentage as evidence of
value-space testing is reading it wrong.

### Control-flow crosses: yes, and they are named

Where the model does branch on combinations, the plan reports the conjunction.
`--` each span carries its full chain of enclosing conditions, not just the
nearest one, because a directive naming only the innermost sends the generator
after a state it will never reach.

| Depth | Spans | Share | Uncovered |
|---|---:|---:|---:|
| unconditional | 11215 | 74.9% | 2313 |
| one condition | 2844 | 19.0% | 1552 |
| two at once | 705 | 4.7% | 184 |
| three at once | 176 | 1.2% | 101 |
| four or more | 35 | 0.2% | 16 |

916 spans (6.1%) need two or more conditions to hold together; 301 are
uncovered, across 130 outstanding plan items flagged `cross=yes`. They cluster exactly where
the architecture genuinely branches on combinations — `pt_walk`, `pmaCheck`,
`check_PTE_permission`, `doCSR`, `ZICBOZ`:

```
TP1683  Zicboz  ZICBOZ  depth 4  14 to do
  get_transformed_data_addr(...) AND translateAddr(vaddr, access)
  AND mem_write_ea(paddr, cache_block_size, false) AND ...
```

One caveat on the depth figure: `} else if c then {` is an *alternative*, not a
nesting — exactly one of the chain holds, never all of them. Counting those as
nesting put `clint_store` at depth 8 and claimed eight conditions had to hold
simultaneously. The index applies a line's leading `}` before its `{`, so an
else-if chain stays at constant depth and only genuine nesting increments it.

## Keeping up with model changes

The plan is derived, so a model update flows through without editing anything
here: a new `.sail` file becomes a new feature, its `match` arms become
instruction targets, its mnemonic mapping names them, and its helpers are
attributed to whichever instructions call them. Verified against a synthetic
extension added to a copy of the model — new feature, three new mnemonics, and
`foo_saturate` correctly reported as *execute one of: fooadd, foosub, foomax*,
with no configuration.

Two things do **not** update themselves, and both used to fail silently.

### Stale inputs are refused

`branch_info` is emitted by the coverage build. Edit the model without
rebuilding and the target set is the *previous* model's: new code is invisible,
and the plan claims full coverage of behaviour that no longer exists. Nothing
about the output looks wrong.

`sail_coverage` is matched to spans by line number. A log collected before a
model edit lines up against shifted spans, so status is joined to the wrong
rows — again silently, in whichever direction the edit shifted things.

Both are checked on every run and both stop it:

```
STALE INPUT: the model is newer than the span manifest:
    extensions/I/base_insts.sail changed after sail_riscv_model.branch_info was built.
    The plan would describe the previous model, and new code would not appear as a hole.
    Fix: cmake --build /home/jk/Documents/sail-riscv/build-coverage
```

`--allow-stale` proceeds anyway, for deliberately inspecting an older plan.

### New holes are separated from the standing backlog

A plan that only states the current position cannot answer *"is this gap new?"*,
which is the question a model update raises. `--baseline` diffs against a
previous `testplan.json` and splits the change four ways:

| | |
|---|---|
| **new hole** | a target that did not exist before and is not covered — usually model code that was just added |
| **regression** | a target that was fully covered and no longer is |
| **widened** | a target that still exists but grew uncovered spans |
| **closed** | work finished since the baseline |

Items are keyed on `(feature, target, stimulus)`, not on `item_id`, which is
positional and shifts whenever anything is inserted above it.

```bash
python3 testplan.py --coverage ~/Documents/sail-riscv/sail_coverage \
    --exclude-spans /tmp/excl.txt \
    --baseline documentation/python-isla/results/testplan.json \
    -o testplan --fail-on-new-holes
```

`--fail-on-new-holes` exits non-zero on a new hole or a regression, so a model
bump fails CI rather than quietly enlarging the backlog. Check the baseline
`testplan.json` in alongside the code and refresh it deliberately.

Both paths are tested: a synthetic extension added to the model produced six new
holes named `fooadd`/`foosub`/`foomax` plus its helper, and a deliberately
shrunk corpus produced 506 regressions. Both exited 1.

## The ceiling

Covering every item in the plan does **not** reach 100%, and the plan is built
so that this is visible rather than discovered later.

| | Spans |
|---|---:|
| Reachable (denominator) | 10,682 |
| Covered by the current corpus | 6,516 (61.0%) |
| Outstanding | 4,166 |
| — of which can never be covered | 621 |
| **Ceiling if every schedulable item completes** | **94.2%** |

The 621 are the `out of reach` items: enum-to-text mappings in a declaration
form the exclusion script does not recognise, diagnostic string builders, and
Hypervisor code the model does not implement. They stay in the denominator
because a per-span judgement is not a strong enough basis to move a coverage
figure — so the honest consequence is that 100% is arithmetically unavailable.

Two things make even 94.2% optimistic. 342 spans are `unclassified`, where the
plan cannot say what would reach them; if all proved unreachable the ceiling
falls to 91.0%. And a large block is gated on configurations outside the two we
build, against the sixteen in the model's own CI matrix.

More fundamentally: the plan classifies *stimulus*, not *reachability*. An item
reading "run in Supervisor mode" is a hypothesis. Only executing the span proves
it can be executed.

## A finding this surfaced

`derive_span_exclusions.py` recognises `mapping name : T <-> string` but not
`mapping clause name = Ctor <-> "text"`. The second form accounts for 453 spans
that are unreachable on identical grounds — chiefly `csr_name_map` in
`Zihpm`/`Sscofpmf`/`Stateen` and `extensionName` in `core/extensions.sail`.
They currently sit in the denominator as `naming_mapping` items. Extending the
exclusion script to the second declaration form would move them, and is the
right fix; it has not been made, so the figures above still count them.
