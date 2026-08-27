#!/usr/bin/env python3
"""Generate and run every case in cases/trap.toml.

Each case is a trap-routing setup derived by trap_cases.py. Exception cases
execute an instruction that raises the cause; interrupt cases make one or more
interrupts pending and let the harness's own code take the trap.
"""
import argparse, collections, os, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scenario_tests as st

NOP = "0x00000013"


def parse_cases(path):
    cases, cur = [], None
    for line in open(path):
        line = line.split("#", 1)[0].strip()
        if line == "[[case]]":
            cur = {}; cases.append(cur); continue
        if cur is None or "=" not in line:
            continue
        k, v = (x.strip() for x in line.split("=", 1))
        if v.startswith('"'):
            cur[k] = v.strip('"')
        elif v in ("true", "false"):
            cur[k] = v == "true"
        else:
            cur[k] = int(v, 0)
    return cases


def main():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default=os.path.join(here, "cases/trap.toml"))
    ap.add_argument("--out", default=os.path.expanduser("~/.cache/riscv-sweep/trap-cases"))
    ap.add_argument("--config", default=os.path.join(here, "autotest/configs/rv64.json"))
    ap.add_argument("--only")
    a = ap.parse_args()

    cases = parse_cases(a.cases)
    if a.only:
        cases = [c for c in cases if a.only in c["id"]]
    os.makedirs(a.out, exist_ok=True)
    env = dict(os.environ, LD_LIBRARY_PATH=st.Z3_LIB)
    tally = collections.Counter()
    print(f"{'CASE':34s} {'GEN':5s} {'SAIL':9s} {'SPIKE':6s}  EXPECTED")
    print("-" * 78)
    for c in cases:
        # An interrupt case needs no instruction of its own: the interrupt is
        # already pending when the test region starts, so a nop is enough for
        # the harness to have somewhere to take the trap from.
        ops = [f"0x{c['opcode']:08x}"] if c.get("opcode") is not None else [NOP]
        kind, code = c["expect"].split(":")
        flags = ["--trap-case",
                 f"{c.get('medeleg',0):#x}:{c.get('mideleg',0):#x}:"
                 f"{c.get('mip',0):#x}:{c.get('mie',0):#x}:"
                 f"{'s' if c.get('handler') == 'S' else 'm'}",
                 "--expect-trap-cause", code, "--trap-is-pass"]
        if c.get("priv") == "S":
            flags += ["--run-in-supervisor", "--pmp-allow-all"]
        elif c.get("priv") == "U":
            flags += ["--run-in-user", "--pmp-allow-all"]
        prefix = os.path.join(a.out, c["id"])
        r = subprocess.run([st.ISLA_BIN, "-A", "riscv-ir/riscv64.ir", "-C", "riscv-ir/riscv64.toml",
                            "-a", "riscv64", "--memory-region", "0x80020000-0x80030000",
                            "-o", prefix, "-n", "1", *ops, *flags],
                           cwd=st.ISLA_DIR, env=env, capture_output=True, text=True, timeout=300)
        elf = prefix + ".elf"
        if r.returncode or not os.path.exists(elf):
            why = "generation failed" if r.returncode else "generated nothing"
            print(f"{c['id']:34s} FAIL                     {why}")
            tally["gen failed"] += 1; continue
        s = subprocess.run([st.SAIL_SIM, "--config", a.config, elf],
                           capture_output=True, text=True, timeout=120)
        sail = "SUCCESS" if "SUCCESS" in s.stdout else ("FAILURE" if "FAILURE" in s.stdout else "?")
        # LCOFI exists only with Sscofpmf; without it in the ISA string Spike
        # never makes the interrupt pending and disagrees with Sail for a
        # reason that has nothing to do with the model.
        isa = "rv64imac_zicsr"
        if "lcofi" in c["id"]:
            isa += "_sscofpmf"
        k = subprocess.run([st.SPIKE, f"--isa={isa}", elf], capture_output=True, timeout=120)
        spike = "pass" if k.returncode == 0 else "FAIL"
        ok = sail == "SUCCESS" and spike == "pass"
        tally["pass" if ok else "MISMATCH"] += 1
        print(f"{c['id']:34s} ok    {sail:9s} {spike:6s}  {c['expect']}")
    print("-" * 78)
    print("  " + "   ".join(f"{k}: {v}" for k, v in sorted(tally.items())))


if __name__ == "__main__":
    main()
