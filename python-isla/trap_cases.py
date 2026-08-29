#!/usr/bin/env python3
"""Derive interrupt and exception test cases from the Sail model.

Third application of the method used by pmp_cases.py and vmem_cases.py. Traps
have a different shape again: PMP is CSR values, translation is a page table,
and a trap is a *routing decision* -- which privilege handles it, which of
several pending interrupts wins, and whether xtval is written.

Read from the model:

  mapping exceptionType_bits         (core/types.sail)      -> cause numbers
  mapping interruptType_bits         (core/types.sail)      -> interrupt codes
  function exception_delegatee       (sys/sys_control.sail) -> medeleg + the
                                        "cannot become less privileged" rule
  function findPendingInterrupt      (sys/sys_control.sail) -> the priority ORDER,
                                        as a first-match if-chain
  function legalize_mideleg          (core/interrupt_regs.sail) -> which
                                        interrupts may be delegated at all
  function xtval_exception_value     (sys/sys_control.sail) -> per-cause, whether
                                        xtval is written -- each a config option

The priority chain is the axis a hand-written suite tends to miss: testing one
pending interrupt at a time never checks the ORDER, only that each can be taken.
An order test needs two pending simultaneously and an assertion about which one
arrived.
"""

import argparse, collections, json, os, re, sys
import paths

MODEL = os.path.join(paths.SAIL_RISCV, "model")
TYPES, CTRL, IREGS = "core/types.sail", "sys/sys_control.sail", "core/interrupt_regs.sail"


def read(rel):
    return open(os.path.join(MODEL, rel), errors="ignore").read()


# ── reading the model ────────────────────────────────────────────────────────

def causes(mapping_name, rel=TYPES):
    """{name: number} from a `mapping X : T <-> exc_code` table."""
    body = re.search(rf"mapping {mapping_name} : .*?<-> exc_code = \{{(.*?)\n\}}",
                     read(rel), re.S)
    if not body:
        sys.exit(f"could not find mapping {mapping_name}")
    out = {}
    for name, bits in re.findall(r"(\w+)(?:\([^)]*\))?\s*<->\s*0b([01]+)", body.group(1)):
        out.setdefault(name, int(bits, 2))
    return out


def interrupt_priority():
    """The order findPendingInterrupt checks, read from its if-chain.

    The order IS the specification here -- the model quotes it from the spec
    directly above the function -- so it must come from the source rather than
    from a list retyped alongside it.
    """
    body = re.search(r"function findPendingInterrupt\(ip : xlenbits\).*?\n\}", read(CTRL), re.S)
    if not body:
        sys.exit("could not find findPendingInterrupt")
    return re.findall(r"ip\[(\w+)\] == 0b1 then Some\((\w+)\)", body.group(0))


def undelegatable():
    """Interrupt bits legalize_mideleg hardwires to zero."""
    body = re.search(r"function legalize_mideleg.*?\n\}", read(IREGS), re.S)
    if not body:
        return []
    # `[v with MEI = 0b0, MTI = 0b0, MSI = 0b0]` or per-field assignments
    return re.findall(r"(\w+)\s*=\s*0b0", body.group(0))


def mip_writable():
    """Which mip bits a CSR write can actually set, from legalize_mip.

    The model says it plainly -- "The only writable bits are the S-mode bits".
    MEI, MTI and MSI come from the platform (a PLIC, or the CLINT), so a
    priority pair involving one of them cannot be built from CSR writes alone.
    Reading this rather than assuming it is what turns three of the six
    priority pairs from silent failures into recorded exclusions.
    """
    body = re.search(r"private function legalize_mip.*?\n\}", read(IREGS), re.S)
    if not body:
        return set()
    # fields assigned from `v[...]` are writable; those forced to 0b0 are not
    return {f for f, expr in re.findall(r"(\w+)\s*=\s*(if[^,]*?|v\[\w+\]),", body.group(0))
            if "v[" in expr}


def xtval_causes():
    """{cause name: the config option deciding whether xtval is written}."""
    body = re.search(r"function xtval_exception_value.*?\n\}", read(CTRL), re.S)
    if not body:
        sys.exit("could not find xtval_exception_value")
    out = {}
    for name, opt in re.findall(r"(E_\w+)(?:\([^)]*\))?\s*=>\s*([A-Za-z_][\w]*|true|false)",
                                body.group(0)):
        out.setdefault(name, opt)
    return out


def load_config(path):
    txt = re.sub(r"^\s*//.*$", "", open(path).read(), flags=re.M)
    cfg = json.loads(txt)
    return {"ext": {k: v.get("supported") for k, v in cfg["extensions"].items()},
            "xlen": cfg["base"]["xlen"]}


# ── case construction ────────────────────────────────────────────────────────

# Exceptions an instruction can raise directly, with the opcode that raises it.
# Access and page faults are produced by pmp_cases.py and vmem_cases.py instead:
# they need a PMP or page-table setup, and belong with the feature that builds it.
RAISERS = {
    "E_Breakpoint":     (0x00100073, "ebreak"),
    "E_M_EnvCall":      (0x00000073, "ecall from Machine"),
    "E_S_EnvCall":      (0x00000073, "ecall from Supervisor"),
    "E_U_EnvCall":      (0x00000073, "ecall from User"),
}
PRIV_OF = {"E_M_EnvCall": "M", "E_S_EnvCall": "S", "E_U_EnvCall": "U"}
# mip/mie bit positions, from the Minterrupts bitfield naming used by the model.
IBIT = {"MSI": 3, "MTI": 7, "MEI": 11, "SSI": 1, "STI": 5, "SEI": 9, "LCOFI": 13}


def build_cases(exc, intr, order, nodeleg, xtval, cfg, writable):
    cases, excluded = [], []

    def add(cid, **kw):
        cases.append(dict(id=cid, **kw))

    # ---- exceptions: raised, and their cause asserted ----------------------
    # An illegal encoding has no `execute` clause, so there is nothing for the
    # solver to run and isla emits no test. It is reachable by the concrete
    # oracle or the template backend, not by the symbolic route -- an exclusion
    # about the *method*, not about the model.
    excluded.append(("E_Illegal_Instr",
                     "no execute clause to solve: an illegal encoding is generated by "
                     "the oracle or the template backend, not by symbolic execution"))
    for name, (opcode, why) in RAISERS.items():
        if name not in exc:
            excluded.append((name, "not in exceptionType_bits for this model"))
            continue
        priv = PRIV_OF.get(name, "M")
        if priv in ("S", "U") and not cfg["ext"].get("S" if priv == "S" else "U"):
            excluded.append((name, f"{priv}-mode not supported in this configuration"))
            continue
        add(f"exc_{name.lower()}", kind="exception", opcode=opcode, priv=priv,
            medeleg=0, expect=f"trap:{exc[name]}", handler="M",
            why=f"{why} -> cause {exc[name]}, taken in Machine mode")

    # ---- exception delegation: exception_delegatee -------------------------
    # medeleg[cause] routes the trap to Supervisor, but only when the hart is
    # not already at a higher privilege -- "we cannot transition to a less
    # privileged mode". Both halves of that rule get a case.
    if cfg["ext"].get("S"):
        for name in ("E_Breakpoint",):
            c = exc[name]
            add(f"deleg_{name.lower()}_to_s", kind="exception",
                opcode=RAISERS[name][0], priv="S", medeleg=1 << c,
                expect=f"trap:{c}", handler="S",
                why=f"medeleg[{c}] set and the hart is in Supervisor: "
                    f"exception_delegatee returns Supervisor, so scause holds the cause")
            add(f"deleg_{name.lower()}_from_m", kind="exception",
                opcode=RAISERS[name][0], priv="M", medeleg=1 << c,
                expect=f"trap:{c}", handler="M",
                why=f"medeleg[{c}] set but the hart is in Machine: delegation cannot "
                    f"lower the privilege, so the trap stays in Machine mode")
    else:
        excluded.append(("exception delegation", "S-mode not supported here"))

    # ---- interrupt priority: findPendingInterrupt's if-chain ---------------
    # The order is the specification. One pending interrupt at a time never
    # tests it -- only that each can be taken. Each adjacent pair in the chain
    # is made pending together, and the higher one must be the one that arrives.
    names = [(bit, ty) for bit, ty in order]
    for i in range(len(names) - 1):
        (hi_bit, hi_ty), (lo_bit, lo_ty) = names[i], names[i + 1]
        if hi_bit not in IBIT or lo_bit not in IBIT:
            excluded.append((f"{hi_bit} over {lo_bit}", "no mip bit position known"))
            continue
        unsettable = [b for b in (hi_bit, lo_bit) if b not in writable]
        if unsettable:
            excluded.append((f"{hi_bit} over {lo_bit}",
                             f"legalize_mip makes {', '.join(unsettable)} read-only to CSR "
                             f"writes -- it comes from the platform (CLINT or PLIC), so this "
                             f"pair needs a device model the harness does not drive"))
            continue
        hi_code = intr.get(hi_ty)
        if hi_code is None:
            excluded.append((hi_ty, "not in interruptType_bits"))
            continue
        add(f"prio_{hi_bit.lower()}_over_{lo_bit.lower()}", kind="interrupt",
            mip=(1 << IBIT[hi_bit]) | (1 << IBIT[lo_bit]),
            mie=(1 << IBIT[hi_bit]) | (1 << IBIT[lo_bit]),
            priv="M", medeleg=0, mideleg=0, expect=f"interrupt:{hi_code}", handler="M",
            why=f"{hi_bit} and {lo_bit} pending together; findPendingInterrupt checks "
                f"{hi_bit} first, so {hi_ty} (cause {hi_code}) must be the one taken")

    # ---- interrupt delegation, and what cannot be delegated ---------------
    for bit, ty in order:
        if bit not in IBIT or ty not in intr:
            continue
        if bit in nodeleg:
            excluded.append((f"delegate {bit}",
                             f"legalize_mideleg hardwires {bit} to 0: a machine-level "
                             f"interrupt cannot be delegated, so the test is impossible "
                             f"rather than failing"))
            continue
        if not cfg["ext"].get("S"):
            continue
        add(f"deleg_{bit.lower()}_to_s", kind="interrupt",
            mip=1 << IBIT[bit], mie=1 << IBIT[bit], mideleg=1 << IBIT[bit],
            priv="S", medeleg=0, expect=f"interrupt:{intr[ty]}", handler="S",
            why=f"mideleg[{bit}] is writable, so {ty} is delivered to Supervisor "
                f"and the cause appears in scause, not mcause")

    # ---- xtval: which causes write it --------------------------------------
    for name, opt in sorted(xtval.items()):
        if name not in RAISERS:
            continue
        if opt in ("true", "false"):
            add(f"xtval_{name.lower()}", kind="exception", opcode=RAISERS[name][0],
                priv=PRIV_OF.get(name, "M"), medeleg=0, expect=f"trap:{exc[name]}",
                handler="M", check_xtval=(opt == "true"),
                why=f"xtval_exception_value returns {opt} for {name}, so mtval "
                    f"{'holds the faulting value' if opt == 'true' else 'must read zero'}")
        else:
            excluded.append((f"xtval for {name}",
                             f"decided by the config option `{opt}`: a configuration "
                             f"variant, not a stimulus variant"))
    return cases, excluded


# ── output ───────────────────────────────────────────────────────────────────

def to_toml(cases, meta):
    out = ["# Generated by trap_cases.py -- do not edit by hand.", ""]
    out += [f"# {k}: {v}" for k, v in meta.items()] + [""]
    for c in cases:
        out.append("[[case]]")
        for k in ("id", "kind", "priv", "handler", "expect"):
            if c.get(k) is not None:
                out.append(f'{k:<9s}= "{c[k]}"')
        for k in ("opcode", "medeleg", "mideleg", "mip", "mie"):
            if c.get(k) is not None:
                out.append(f"{k:<9s}= 0x{c[k]:x}")
        if "check_xtval" in c:
            out.append(f'check_xtval = {"true" if c["check_xtval"] else "false"}')
        out.append(f'why      = "{c["why"]}"')
        out.append("")
    return "\n".join(out)


def main():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=os.path.join(here, "autotest/configs/rv64.json"))
    ap.add_argument("-o", "--out", default=os.path.join(here, "cases/trap.toml"))
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()

    exc = causes("exceptionType_bits")
    intr = causes("interruptType_bits")
    order = interrupt_priority()
    nodeleg = undelegatable()
    xtval = xtval_causes()
    cfg = load_config(a.config)

    print("read from the model:")
    print(f"  exceptionType_bits      {len(exc)} causes")
    print(f"  interruptType_bits      {len(intr)} codes")
    print(f"  findPendingInterrupt    priority order: {' > '.join(b for b, _ in order)}")
    print(f"  legalize_mideleg        hardwires to 0: {', '.join(nodeleg) or 'nothing'}")
    print(f"  legalize_mip            CSR-writable bits: {', '.join(sorted(mip_writable()))}")
    print(f"  xtval_exception_value   {len(xtval)} causes, "
          f"{sum(1 for v in xtval.values() if v not in ('true','false'))} decided by config")

    writable = mip_writable()
    cases, excluded = build_cases(exc, intr, order, nodeleg, xtval, cfg, writable)
    print(f"\n{len(cases)} cases derived, {len(excluded)} exclusions recorded\n")
    for k, v in sorted(collections.Counter(c["id"].split("_")[0] for c in cases).items(),
                       key=lambda x: -x[1]):
        print(f"  {v:3d}  {k}")
    print("\nexclusions (each with its reason):")
    for what, why in excluded:
        print(f"  {what:26s} {why}")

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    open(a.out, "w").write(to_toml(cases, {
        "exceptions": len(exc), "interrupts": len(intr),
        "priority": " > ".join(b for b, _ in order),
        "cases": len(cases), "exclusions": len(excluded),
        "config": os.path.basename(a.config)}))
    print(f"\nwrote {a.out}")

    if a.check:
        have = {c["expect"].split(":")[1] for c in cases if c["expect"].startswith("trap")}
        pairs = sum(1 for c in cases if c["id"].startswith("prio_"))
        w = mip_writable()
        buildable = sum(1 for i in range(len(order)-1)
                        if order[i][0] in w and order[i+1][0] in w)
        print("\ncompleteness -- every alternative the model declares:")
        print(f"  priority pairs   {pairs}/{buildable} buildable "
              f"(of {max(0, len(order)-1)} in the chain; the rest need a platform device)")
        print(f"  exception causes {len(have)}/{len(RAISERS)} an instruction can raise directly")
        print(f"  (access and page faults are covered by pmp_cases.py and vmem_cases.py,")
        print(f"   which build the PMP entry or page table those causes require)")


if __name__ == "__main__":
    main()
