#!/bin/bash
# Where the privileged corpus stands. Note: match on the python process, not the
# bare filename -- `pgrep -f csr_sweep.py` also matches this script, which reads
# as RUNNING forever.
D=$(cd "$(dirname "$0")" && pwd)
if ps -eo args | grep -q "^[^ ]*python[0-9.]* .*csr_sweep\.py"; then RUN=RUNNING; else RUN="not running"; fi
LAST=$(find "$D/corpus" -name '*.elf' -printf '%T@\n' 2>/dev/null | sort -rn | head -1)
printf "csr sweep      : %s\n" "$RUN"
printf "last ELF written: %s s ago\n" "$(awk -v t="$LAST" 'BEGIN{printf "%d", systime()-t}')"
printf "load           : %s\n\n" "$(uptime | sed 's/.*average: //')"
printf "%-16s %6s\n" "generator" "elfs"
for d in "$D"/corpus/*/; do printf "  %-14s %6d\n" "$(basename "$d")" "$(find "$d" -name '*.elf' | wc -l)"; done
printf "  %-14s %6d\n" "TOTAL" "$(find "$D/corpus" -name '*.elf' | wc -l)"
