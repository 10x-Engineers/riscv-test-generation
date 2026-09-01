# Privileged test generation — measured result

All figures below come from one clean run on **31 August 2026**, reproducible with
`demo/privileged/run.sh --clean`. Every claim is followed by the command that
produces it.

## Corpus: 1,144 generated ELFs, privileged only

| Generator | ELFs | Targets |
|---|---|---|
| CSR sweep | 1,005 | Every CSR the model names — 169 per XLEN, 6 access instructions each |
| Scenario tests | 53 | Privilege transitions, PMP, Sv translation, interrupts, PMA |
| Derived PMP cases | 36 | Address-match modes, permissions, locking, boundary arithmetic |
| Derived virtual-memory cases | 25 | Sv39/Sv48 walks, PTE validity, MXR/SUM, page-table faults |
| Derived trap cases | 16 | Exception causes, trap values, delegation |
| Zicsr + Svinval opcodes | 9 | The privileged instructions themselves |
| **Total** | **1,144** | |

    find demo/privileged/corpus -name '*.elf' | wc -l

## Coverage of the privileged architecture

Measured against the coverage-instrumented Sail model, privileged scope.

| Span kind | Covered | Reachable | Coverage |
|---|---|---|---|
| Functions | 184 | 226 | 81.4% |
| Branches | 539 | 694 | 77.7% |
| Expressions | 799 | 1,572 | 50.8% |
| **All spans** | **1,522** | **2,492** | **61.1%** |

    cd python-isla
    eval "$(python3 paths.py --export)"
    D=$PWD/../demo/privileged
    python3 coverage_report.py --elf-dir $D/corpus --exclude-spans $D/results/exclusions.txt
    python3 coverage_report.py --no-replay --scope rfp-scope-privileged.txt \
            --exclude-spans $D/results/exclusions.txt

## How the denominator is built

    2,868  spans in the privileged scope of the instrumented build
   -  376  structurally unreachable, each with a written reason
    -----
    2,492  reachable spans — the denominator above

The exclusions are the reverse directions of Sail's bidirectional `mapping`
constructs: real instrumented code that encodes and decodes from one
declaration, where only one direction is ever executed. The other is used for
disassembly, which no running test performs. Reasons are machine-readable in
`results/exclusions.json`.

    python3 python-isla/derive_span_exclusions.py \
            -o demo/privileged/results/exclusions.txt \
            --json demo/privileged/results/exclusions.json

## Where the coverage lands, by model file

Strongest: `Zicsr/zicsr_insts` 88.9%, `pmp/pmp_regs` 82.6%,
`pointer_masking/pm_utils` 81.2%, `pmp/pmp_control` 74.0%,
`sys/sys_control` 73.4%, `sys/vmem` 72.8%, `postlude/csr_end` 66.8%.

Weakest: `sys/vmem_ptw` 15.0% (9/60 — the page-table walker),
`sys/pma` 18.8% (16/85), `Sscofpmf` 21.4%, `core/vmem_types` 42.5%.

    python3 coverage_report.py --no-replay --scope rfp-scope-privileged.txt \
            --exclude-spans $D/results/exclusions.txt --top 40

## What this figure does and does not claim

Coverage here measures what the model **executed**, not what was **verified**.
On replay, **665 of 1,144 ELFs reached SUCCESS and 479 did not**, and the spans
reached by those 479 still count toward the figure — the instrumented simulator
records coverage as it runs, regardless of the test's own verdict.

    python3 coverage_report.py --elf-dir $D/corpus \
            --exclude-spans $D/results/exclusions.txt | grep replayed

Splitting those 479 into model faults and framework faults is outstanding. The
failures are concentrated rather than random: on RV64, 87 CSRs had no passing
instruction, almost all of them `pmpcfg1`–`15`, `pmpaddr16`–`63`, the `stateen`
family and the trigger CSRs — WARL or configuration-dependent registers, where a
legalised read-back not matching a static expectation is the more likely cause
than a model defect.

## Scope matters — the same corpus supports four percentages

| Scope | Branches | All spans |
|---|---|---|
| Privileged only | 539/694 · 77.7% | 1,522/2,492 · 61.1% |
| Current deliverable | 734/1,139 · 64.4% | 2,194/4,427 · 49.6% |
| Full RFP scope | 741/2,671 · 27.7% | 2,217/9,245 · 24.0% |
| Whole model | 970/2,992 · 32.4% | 2,709/10,157 · 26.7% |

The whole-model and full-RFP rows are not meaningful for this corpus: floating
point and the vector extensions were deliberately not generated, so those files
sit at 0% and depress the figure by construction. Always quote the scope and the
span kind with the number.
