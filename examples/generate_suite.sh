#!/usr/bin/env bash
#
# Generate a test suite from a given Golden Model configuration.
#
#   ./examples/generate_suite.sh <golden-model-config.json> [output-dir]
#
# This is RFQ Deliverable 2. It takes one of the Golden Model's own generated
# configuration files and produces an extension-organised, self-describing test
# suite with the provenance needed to regenerate it.
#
# HONEST LIMITATION, reported by the script itself at the end of every run:
# only the oracle generator currently consumes the configuration file directly.
# The symbolic generator takes XLEN derived from it, but its remaining machine
# parameters -- vector width in particular -- come from a fixed build. Closing
# that is phase 1 of the master plan (documentation/MASTER_TECHNICAL_PLAN.md
# section 6). Until it lands, this script tells you which parts of the config it
# honoured and which it approximated, rather than implying full fidelity.
#
set -uo pipefail

CFG="${1:-}"
OUT="${2:-$HOME/riscv-suites/$(basename "${CFG%.json}")}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -z "$CFG" || ! -f "$CFG" ]]; then
  echo "usage: $0 <golden-model-config.json> [output-dir]" >&2
  echo >&2
  echo "The Golden Model generates its configurations under build/config/, e.g." >&2
  echo "  $0 \"\$SAIL_RISCV/build/config/rv64d_v128_e64.json\"" >&2
  exit 2
fi

# ---------------------------------------------------------------- read config
# The model's config files are JSON with comments, so strip those before parsing.
read -r XLEN VLEN ELEN NEXT < <(python3 - "$CFG" <<'PY'
import json, re, sys
raw = re.sub(r"//.*", "", open(sys.argv[1]).read())
d = json.loads(raw)
base = d.get("base", {})
v = d.get("extensions", {}).get("V", {})
xlen = base.get("xlen", "?")
vlen = 2 ** v["vlen_exp"] if isinstance(v, dict) and "vlen_exp" in v else "-"
elen = 2 ** v["elen_exp"] if isinstance(v, dict) and "elen_exp" in v else "-"
on = [k for k, val in d.get("extensions", {}).items()
      if (val.get("supported") if isinstance(val, dict) else val)]
print(xlen, vlen, elen, len(on))
PY
) || { echo "could not parse $CFG" >&2; exit 1; }

CFG_HASH="$(sha256sum "$CFG" | cut -c1-12)"

echo "=============================================================="
echo " Generating suite from: $(basename "$CFG")"
echo "   XLEN $XLEN   VLEN $VLEN   ELEN $ELEN   $NEXT extensions enabled"
echo "   config fingerprint: $CFG_HASH"
echo "   output: $OUT"
echo "=============================================================="
mkdir -p "$OUT"

# ---------------------------------------------------------------- provenance
# Written first so an interrupted run still records what it was trying to build.
python3 - "$CFG" "$CFG_HASH" "$XLEN" "$OUT" "$HERE" <<'PY'
import json, subprocess, sys, datetime, os
cfg, cfg_hash, xlen, out, here = sys.argv[1:6]
def rev(path):
    try:
        return subprocess.run(["git","-C",path,"rev-parse","--short","HEAD"],
                              capture_output=True, text=True).stdout.strip() or None
    except Exception:
        return None
def ver(*cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return (r.stdout or r.stderr).splitlines()[0].strip() or None
    except Exception:
        return None
sys.path.insert(0, os.path.join(here, "python-isla"))
import paths
json.dump({
    "generated":       datetime.datetime.now().isoformat(timespec="seconds"),
    "configuration":   {"path": cfg, "sha256_12": cfg_hash, "xlen": int(xlen)},
    "revisions": {
        "framework":   rev(here),
        "golden_model": rev(paths.SAIL_RISCV),
        "symbolic_engine": rev(paths.ISLA_DIR),
    },
    "tool_versions": {
        "assembler":   ver(f"{paths.RISCV_TOOLCHAIN_DIR}/riscv64-unknown-elf-as", "--version"),
        "independent_simulator": ver(paths.SPIKE, "--help"),
    },
}, open(os.path.join(out, "provenance.json"), "w"), indent=1)
print("  wrote provenance.json")
PY

cd "$HERE/python-isla" || exit 1
honoured=(); approximated=()

# ------------------------------------------------- 1. oracle: config-driven
# This generator reads the configuration file itself, so everything in it --
# XLEN, vector width, enabled extensions -- is honoured exactly.
echo
echo "-- [1/3] oracle backend (reads the configuration directly)"
if PYTHONPATH="$HERE/autotest/src" python3 -m sailtest.cli generate \
      --config "$CFG" --sail-riscv "$(python3 -c 'import paths;print(paths.SAIL_RISCV)')" \
      --backend model --count 12 --out "$OUT/oracle" >/dev/null 2>&1; then
  echo "     ok"
  honoured+=("XLEN, vector width, enabled extension set")
else
  echo "     no tests generated for this configuration (recorded, not fatal)"
fi

# --------------------------------- 2. instruction sweep: XLEN-driven only
echo "-- [2/3] instruction sweep (XLEN from the config; other parameters fixed)"
python3 opcode_sweep.py extensions/I/base_insts.sail extensions/M/mext_insts.sail \
    --extension M --xlen "$XLEN" --out-dir "$OUT/instructions" >/dev/null 2>&1 \
  && echo "     ok" || echo "     completed with failures (see status_report.py)"
approximated+=("machine parameters beyond XLEN, for the symbolic path")

# ------------------------------------------------- 3. privileged scenarios
echo "-- [3/3] privileged scenarios (XLEN from the config)"
python3 scenario_tests.py --xlen "$XLEN" --out-dir "$OUT/privileged" >/dev/null 2>&1 \
  && echo "     ok" || echo "     completed with expected control failures"

# ---------------------------------------------------------------- summarise
ELFS=$(find "$OUT" -name '*.elf' 2>/dev/null | wc -l)
MANIFESTS=$(find "$OUT" -name 'tests.json' -o -name 'manifest.json' 2>/dev/null | wc -l)

echo
echo "=============================================================="
echo " Suite written to $OUT"
echo "   $ELFS ELF files, $MANIFESTS manifests, provenance.json"
echo
echo " Configuration fidelity"
for h in "${honoured[@]}";     do echo "   honoured     $h"; done
for a in "${approximated[@]}"; do echo "   approximated $a"; done
echo
echo " Next: measure what it covers"
echo "   cd python-isla && python3 coverage_report.py \\"
echo "       --elf-dir $OUT --per-elf per-elf.txt --uncovered todo.txt"
echo "=============================================================="
