#!/usr/bin/env python3
"""Generate and run every case in cases/pmp.toml.

`pmp_cases.py` derives the cases from the model; this turns each into an ELF and
runs it on the Golden Model and on Spike. The case file is the only input --
nothing here decides what a PMP entry should look like.
"""
import argparse, os, re, subprocess, sys, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths, scenario_tests as st

# access kind -> the opcodes that produce it, after x2 = the case's address
OPCODE = {
    "Load(Data)":                   0x00012083,   # lw   x1, 0(x2)
    "Store(Data)":                  0x00112023,   # sw   x1, 0(x2)
    "LoadReserved(_, _, Data)":     0x100130af,   # lr.d x1, (x2)
    "StoreConditional(_, _, Data)": 0x181131af,   # sc.d x3, x1, (x2)
    "Atomic(_, _, _, Data, Data)":  0x001130af,   # amoadd.d x1, x1, (x2)
    "CacheAccess(CB_manage(_))":    0x0011200f,   # cbo.clean (x2)
    "CacheAccess(CB_zero())":       0x0041200f,   # cbo.zero  (x2)
    "PREFETCH_I":                   0x00016013,   # prefetch.i 0(x2)
    "PREFETCH_R":                   0x00116013,   # prefetch.r 0(x2)
    "PREFETCH_W":                   0x00316013,   # prefetch.w 0(x2)
}

# Extensions the assembler must be told about for these encodings. isla emits
# raw bytes so the assembler never sees the mnemonic, but Spike must have the
# extension in its ISA string or it decodes them as illegal.
NEEDS = {"CacheAccess(CB_manage(_))": "zicbom", "CacheAccess(CB_zero())": "zicboz",
         "PREFETCH_I": "zicbop", "PREFETCH_R": "zicbop", "PREFETCH_W": "zicbop"}


def addr_setup_for(addr):
    """Opcodes that put `addr` in x2, for any address.

    lui sign-extends from bit 31 on RV64, so the slli/srli pair clears the
    upper half -- the same sequence the hand-written scenarios use. Emitted
    zero-padded to eight hex digits: isla reads the opcode width from the
    string, and `hex(0x01010113)` drops the leading zero and is rejected.
    """
    lo, hi = addr & 0xfff, addr >> 12
    if lo >= 0x800:                       # addi's immediate is signed
        hi, lo = hi + 1, lo - 0x1000
    ops = [(hi << 12) | 0x137,            # lui  x2, hi
           0x02011113,                    # slli x2, x2, 32
           0x02015113]                    # srli x2, x2, 32
    if lo:
        ops.append(0x00010113 | ((lo & 0xfff) << 20))   # addi x2, x2, lo
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
            cur[k] = [int(x, 0) for x in v.strip("[]").split(",") if x.strip()]
        elif v.startswith('"'):
            cur[k] = v.strip('"')
        else:
            cur[k] = int(v, 0)
    return cases


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default=os.path.join(paths.REPO, "cases/pmp.toml")
                    if hasattr(paths, "REPO") else "cases/pmp.toml")
    ap.add_argument("--out", default=os.path.join(os.path.expanduser("~"),
                                                  ".cache/riscv-sweep/pmp-cases"))
    ap.add_argument("--config", default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "autotest/configs/rv64.json"))
    ap.add_argument("--only", help="substring filter on case id")
    a = ap.parse_args()

    cases = parse_cases(a.cases)
    if a.only:
        cases = [c for c in cases if a.only in c["id"]]
    os.makedirs(a.out, exist_ok=True)
    env = dict(os.environ, LD_LIBRARY_PATH=st.Z3_LIB)
    tally = collections.Counter()
    print(f"{'CASE':30s} {'GEN':5s} {'SAIL':9s} {'SPIKE':6s}  EXPECTED")
    print("-" * 78)
    for c in cases:
        ops = addr_setup_for(c["addr"])
        if c["access"] not in OPCODE:
            print(f"{c['id']:30s} {'skip':5s} {'':9s} {'':6s}  no opcode for {c['access']}")
            tally["skipped"] += 1
            continue
        ops = ops + [f'0x{c.get("opcode") or OPCODE[c["access"]]:08x}']
        pmp = ",".join(hex(x) for x in c["pmpaddr"]) + ":" + ",".join(hex(x) for x in c["pmpcfg"])
        flags = ["--pmp-case", pmp]
        if str(c["expect"]).startswith("trap"):
            flags += ["--expect-trap-cause", c["expect"].split(":")[1], "--trap-is-pass"]
        if c.get("priv") in ("S", "U"):
            flags += ["--run-in-supervisor"]
        prefix = os.path.join(a.out, c["id"])
        r = subprocess.run([st.ISLA_BIN, "-A", "riscv-ir/riscv64.ir", "-C", "riscv-ir/riscv64.toml",
                            "-a", "riscv64", "--memory-region", "0x80020000-0x80030000",
                            "-o", prefix, "-n", "1", *ops, *flags],
                           cwd=st.ISLA_DIR, env=env, capture_output=True, text=True, timeout=300)
        if r.returncode:
            print(f"{c['id']:30s} {'FAIL':5s} {'':9s} {'':6s}  generation failed")
            tally["gen failed"] += 1
            continue
        elf = prefix + ".elf"
        s = subprocess.run([st.SAIL_SIM, "--config", a.config, elf],
                           capture_output=True, text=True, timeout=120)
        sail = "SUCCESS" if "SUCCESS" in s.stdout else ("FAILURE" if "FAILURE" in s.stdout else "?")
        isa = "rv64imac_zicsr"
        if c["access"] in NEEDS:
            isa += "_" + NEEDS[c["access"]]
        k = subprocess.run([st.SPIKE, f"--isa={isa}", elf],
                           capture_output=True, text=True, timeout=120)
        spike = "pass" if k.returncode == 0 else "FAIL"
        ok = sail == "SUCCESS" and spike == "pass"
        tally["pass" if ok else "MISMATCH"] += 1
        print(f"{c['id']:30s} {'ok':5s} {sail:9s} {spike:6s}  {c['expect']}")
    print("-" * 78)
    print("  " + "   ".join(f"{k}: {v}" for k, v in sorted(tally.items())))


if __name__ == "__main__":
    main()
