#!/bin/bash
# Generate the privileged test corpus from the Sail model, then measure coverage.
#
# Runs strictly one generator at a time. Running them in parallel oversubscribes
# the solver and has taken the machine down; the memory cap below is the second
# line of defence.
#
#   ./run.sh          generate what is missing, then measure
#   ./run.sh --clean  discard the corpus and start over
#   ./run.sh --report measure only, no generation

set -u
D=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$D/../.." && pwd)
cd "$REPO"

eval "$(python3 python-isla/paths.py --export)"
export ISLA_MEM_LIMIT_GIB=2          # a runaway Z3 query cannot take the box with it

MODE=${1:-}
[ "$MODE" = "--clean" ] && { echo ">> discarding the existing corpus"; rm -rf "$D/corpus"; }
mkdir -p "$D/corpus" "$D/results"

step () {                            # step <label> <command...>
  printf '\n>>> %s\n' "$1"; shift
  local t0=$SECONDS
  nice -n 10 "$@" 2>&1 | tail -4
  printf '    (%ds, corpus now %d ELFs)\n' "$((SECONDS-t0))" "$(find "$D/corpus" -name '*.elf' | wc -l)"
}

if [ "$MODE" != "--report" ]; then
  # --- privileged scenarios: privilege transitions, PMP, Sv, interrupts, PMA ---
  for x in 32 64; do
    step "scenario tests RV$x" python3 -u python-isla/scenario_tests.py \
         --xlen $x --out-dir "$D/corpus/scenario-tests"
  done

  # --- cases derived from the model's own decision structure ------------------
  for k in pmp trap vmem; do
    step "derived $k cases" python3 -u python-isla/run_${k}_cases.py --out "$D/corpus/cases-$k"
  done

  # --- every CSR the model names, both XLENs (the long pole, ~30 min) ---------
  for x in 64 32; do
    step "CSR sweep RV$x" python3 -u python-isla/csr_sweep.py \
         --xlen $x --out-dir "$D/corpus/csr-sweep" --from-model
  done

  # --- the privileged instructions themselves --------------------------------
  SWEEP_OUT="$D/corpus/opcode" SWEEP_TIMEOUT=900 \
    step "Zicsr + Svinval opcodes" python3 -u python-isla/sweep_all.py privileged
fi

# --- what no executing program can reach, with a written reason each ----------
step "derive span exclusions" python3 python-isla/derive_span_exclusions.py \
     -o "$D/results/exclusions.txt" --json "$D/results/exclusions.json"

# --- replay once, then report every scope off that one trace ------------------
cd "$REPO/python-isla"
X="--exclude-spans $D/results/exclusions.txt"
echo; echo ">>> replaying the corpus on the instrumented model"
nice -n 10 python3 coverage_report.py --elf-dir "$D/corpus" $X 2>&1 | tail -12

echo
printf '%-24s %-16s %-16s\n' "scope" "branches" "all spans"
for s in rfp-scope-privileged rfp-scope-current rfp-scope; do
  out=$(nice -n 10 python3 coverage_report.py --no-replay --scope $s.txt $X 2>&1)
  b=$(echo "$out" | awk '/^branches/{printf "%s/%s %s", $2, $3, $4}')
  a=$(echo "$out" | awk '/^ALL/{printf "%s/%s %s", $2, $3, $4}')
  printf '%-24s %-16s %-16s\n' "${s#rfp-scope-}" "$b" "$a"
done
echo
echo "Quote the scope and the span kind with any number: the same corpus"
echo "supports four different percentages."
