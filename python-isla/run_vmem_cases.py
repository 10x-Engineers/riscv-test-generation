#!/usr/bin/env python3
"""Generate and run every case in cases/vmem.toml.

Each case is a translation setup derived by vmem_cases.py: a satp mode, the
root-table entries, the mstatus bits, an address and an access. Every case runs
in Supervisor (M-mode does not translate) with a permissive PMP entry (dropping
to Supervisor arms PMP's default-deny), so those two flags are added here rather
than repeated in every case.
"""
import argparse, os, subprocess, sys, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scenario_tests as st

OPCODE = {"Load(Data)": 0x00012083, "Store(Data)": 0x00112023}


def addr_setup_for(addr):
    lo, hi = addr & 0xfff, addr >> 12
    if lo >= 0x800:
        hi, lo = hi + 1, lo - 0x1000
    ops = [(hi << 12) | 0x137, 0x02011113, 0x02015113]
    if lo:
        ops.append(0x00010113 | ((lo & 0xfff) << 20))
    return [f"0x{o:08x}" for o in ops]


def parse_cases(path):
    cases, cur = [], None
    for line in open(path):
        line = line.split("#")[0].strip()
        if line == "[[case]]":
            cur = {}; cases.append(cur); continue
        if cur is None or "=" not in line:
            continue
        k, v = (x.strip() for x in line.split("=", 1))
        if v.startswith("["):
            cur[k] = [x.strip().strip('"') for x in v.strip("[]").split(",") if x.strip()]
        elif v.startswith('"'):
            cur[k] = v.strip('"')
        else:
            cur[k] = True if v == "true" else (False if v == "false" else int(v, 0))
    return cases


MODE = {"Bare": 0, "Sv39": 8, "Sv48": 9, "Sv57": 10}


def main():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default=os.path.join(here, "cases/vmem.toml"))
    ap.add_argument("--out", default=os.path.expanduser("~/.cache/riscv-sweep/vmem-cases"))
    ap.add_argument("--config", default=os.path.join(here, "autotest/configs/rv64.json"))
    ap.add_argument("--only")
    a = ap.parse_args()

    cases = parse_cases(a.cases)
    if a.only:
        cases = [c for c in cases if a.only in c["id"]]
    os.makedirs(a.out, exist_ok=True)
    env = dict(os.environ, LD_LIBRARY_PATH=st.Z3_LIB)
    tally = collections.Counter()
    print(f"{'CASE':32s} {'GEN':5s} {'SAIL':9s} {'SPIKE':6s}  {'EXPECTED':9s} SPIKE ISA")
    print("-" * 80)
    for c in cases:
        if c["access"] not in OPCODE:
            print(f"{c['id']:32s} skip                     no opcode for {c['access']}")
            tally["skipped"] += 1; continue
        ops = addr_setup_for(c["addr"]) + [f"0x{OPCODE[c['access']]:08x}"]
        ents = ",".join(c.get("entries", []))
        if c.get("pmp_deny_l1"):
            # Only the level-1 table's page, one page above the root.
            L1 = 0x80013000
            flags = ["--run-in-supervisor", "--pmp-case",
                     f"{hex((L1 >> 2) | 0x1ff)},{hex((1 << 54) - 1)}:0x98,0x1f"]
        elif c.get("pmp_deny_page_table"):
            # PMP entry 0 denies the 4KiB page the table lives in (a fixed
            # address, see PAGE_TABLE_ADDR in generate_object_riscv.rs);
            # entry 1 permits everything else so the harness still runs.
            PT = 0x80012000
            flags = ["--run-in-supervisor", "--pmp-case",
                     f"{hex((PT >> 2) | 0x1ff)},{hex((1 << 54) - 1)}:0x98,0x1f"]
        else:
            flags = ["--run-in-supervisor", "--pmp-allow-all"]
        if c["mode"] != "Bare" or ents:
            flags += ["--vm-case",
                      f"{MODE[c['mode']]}:{ents}:{','.join(c.get('mstatus', []))}"]
        if str(c["expect"]).startswith("trap"):
            flags += ["--expect-trap-cause", c["expect"].split(":")[1], "--trap-is-pass"]
        prefix = os.path.join(a.out, c["id"])
        r = subprocess.run([st.ISLA_BIN, "-A", "riscv-ir/riscv64.ir", "-C", "riscv-ir/riscv64.toml",
                            "-a", "riscv64", "--memory-region", "0x80020000-0x80030000",
                            "-o", prefix, "-n", "1", *ops, *flags],
                           cwd=st.ISLA_DIR, env=env, capture_output=True, text=True, timeout=300)
        if r.returncode:
            print(f"{c['id']:32s} FAIL                     generation failed")
            tally["gen failed"] += 1; continue
        elf = prefix + ".elf"
        s = subprocess.run([st.SAIL_SIM, "--config", a.config, elf],
                           capture_output=True, text=True, timeout=120)
        sail = "SUCCESS" if "SUCCESS" in s.stdout else ("FAILURE" if "FAILURE" in s.stdout else "?")
        # Spike's ISA string has to name the extensions the case relies on, or
        # it disagrees with Sail for a reason that has nothing to do with the
        # model. A case that sets menvcfg.ADUE needs Svadu, or Spike leaves the
        # bit clear, takes the page-fault path, and the mismatch reads as a
        # Golden Model divergence when it is an argument to the simulator.
        isa = "rv64imac_zicsr"
        if "adue" in c.get("mstatus", []):
            isa += "_svadu"
        k = subprocess.run([st.SPIKE, f"--isa={isa}", elf], capture_output=True, timeout=120)
        spike = "pass" if k.returncode == 0 else "FAIL"
        ok = sail == "SUCCESS" and spike == "pass"
        tally["pass" if ok else "MISMATCH"] += 1
        print(f"{c['id']:32s} ok    {sail:9s} {spike:6s}  {c['expect']:9s} {isa if isa != 'rv64imac_zicsr' else ''}")
    print("-" * 80)
    print("  " + "   ".join(f"{k}: {v}" for k, v in sorted(tally.items())))


if __name__ == "__main__":
    main()
