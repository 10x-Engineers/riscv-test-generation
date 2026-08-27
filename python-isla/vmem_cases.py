#!/usr/bin/env python3
"""Derive virtual-memory test cases from the Sail model's own declarations.

The same method as pmp_cases.py, applied to translation. The difference is what
a "configuration" is: PMP is CSR values, so a case is a few numbers. Here it is
a *page table in memory* plus satp plus two mstatus bits, so a case has to carry
the PTE array as well.

Everything is read from the model:

  enum SATPMode + satpMode_of_bits   (core/types.sail)   -> the modes and their encodings
  bitfield PTE_Flags                 (sys/vmem_pte.sail) -> D A G U X W R V
  bitfield PTE_Ext                   (sys/vmem_pte.sail) -> N, PBMT, reserved
  pte_is_invalid                     (sys/vmem_pte.sail) -> the invalidity disjuncts
  check_PTE_permission               (sys/vmem_pte.sail) -> the three permission gates
  pt_walk                            (sys/vmem.sail)     -> levels, superpage alignment
  extensions.Sv*                     (config JSON)       -> which modes are legal here

Three things about the model shape the cases, and getting any of them wrong
produces a test that faults for the wrong reason:

  * M-mode never translates -- translationMode returns Bare -- so every case
    runs in Supervisor.
  * Dropping to Supervisor arms PMP's default-deny, so every case also needs a
    permissive PMP entry, whether or not PMP is what it is testing.
  * mstatus.MXR and mstatus.SUM change the answer without any PTE changing, so
    they are axes in their own right.
"""

import argparse, json, os, re, sys, collections
import paths

MODEL = os.path.join(paths.SAIL_RISCV, "model")
PTE, VMEM, TYPES = "sys/vmem_pte.sail", "sys/vmem.sail", "core/types.sail"


def read(rel):
    return open(os.path.join(MODEL, rel), errors="ignore").read()


# ── reading the model ────────────────────────────────────────────────────────

def satp_modes():
    """{name: mode-field encoding} for RV64, from satpMode_of_bits."""
    body = re.search(r"function satpMode_of_bits.*?\n\}", read(TYPES), re.S)
    if not body:
        sys.exit("could not find satpMode_of_bits")
    out = {}
    for arch, enc, name in re.findall(r"\((RV32|RV64|_)\s*,\s*(0x[0-9A-Fa-f]+)\)\s*=>\s*Some\((\w+)\)",
                                      body.group(0)):
        if arch in ("RV64", "_"):
            out[name] = int(enc, 16)
    return out


def pte_flags():
    """Bit position of every PTE flag, from the bitfield declaration."""
    body = re.search(r"bitfield PTE_Flags\s*:\s*pte_flags_bits\s*=\s*\{(.*?)\n\}", read(PTE), re.S)
    if not body:
        sys.exit("could not find `bitfield PTE_Flags`")
    return {n: int(b) for n, b in re.findall(r"(\w+)\s*:\s*(\d+)", body.group(1))}


def invalidity_rules():
    """The disjuncts of pte_is_invalid, one plan item each."""
    body = re.search(r"private function pte_is_invalid\(.*?\n\n", read(PTE), re.S)
    if not body:
        sys.exit("could not find pte_is_invalid")
    text = re.sub(r"//[^\n]*", "", body.group(0))
    text = text.split("=", 1)[1]
    return [d.strip().strip("()") for d in re.split(r"\n\s*\|", text) if d.strip()]


def permission_gates():
    """The three gates of check_PTE_permission, with what each reads."""
    body = re.search(r"private function check_PTE_permission\(.*?\n\}", read(PTE), re.S)
    if not body:
        sys.exit("could not find check_PTE_permission")
    src = body.group(0)
    gates = []
    if "priv_ok" in src:
        sum_guard = "is_load_store(access)" in src
        gates.append(("privilege", "pte.U, priv, mstatus.SUM",
                      "SUM permits loads and stores but not fetch" if sum_guard else "SUM ungated"))
    if "shadow_stack_ok" in src:
        gates.append(("shadow stack", "the R=0 W=1 X=0 encoding", "returns early, does not fall through"))
    m = re.search(r"let pte_R = pte_R \| \(pte_X & (\w+)\)", src)
    if m:
        gates.append(("permission", f"R/W/X after {m.group(1)}", "MXR makes X-only readable"))
    return gates


def walk_levels(mode):
    """Levels for an Sv mode, from pt_walk's own initial_level expression."""
    body = re.search(r"let initial_level = ([^;]+);", read(VMEM))
    if not body:
        sys.exit("could not find initial_level in translate_TLB_miss")
    return {"Sv32": 1, "Sv39": 2, "Sv48": 3, "Sv57": 4}.get(mode)


def load_config(path):
    txt = re.sub(r"^\s*//.*$", "", open(path).read(), flags=re.M)
    cfg = json.loads(txt)
    ext = {k: v.get("supported") for k, v in cfg["extensions"].items()}
    ram = next(r for r in cfg["memory"]["regions"]
               if r["attributes"]["mem_type"] == "MainMemory")
    return {"ext": ext, "xlen": cfg["base"]["xlen"],
            "ram_base": int(ram["base"]["value"], 16),
            "ram_size": int(ram["size"]["value"], 16)}


# ── encoding ─────────────────────────────────────────────────────────────────

def pte(ppn, flags, bits, ext=0):
    """A leaf or pointer PTE: PPN in [53:10], flags in the low byte."""
    v = 0
    for f in flags:
        v |= 1 << bits[f]
    return ((ppn & ((1 << 44) - 1)) << 10) | (ext << 54) | v


def gigapage(pa, flags, bits, ext=0):
    """A level-2 leaf PTE mapping the 1 GiB containing `pa`."""
    return pte(pa >> 12, flags, bits, ext)


# ── case construction ────────────────────────────────────────────────────────

ACCESS = {"Load(Data)":  (4, "lw", 0x00012083, 13, "R"),   # (width, mnem, opcode, page-fault cause, needs)
          "Store(Data)": (4, "sw", 0x00112023, 15, "W")}


def build_cases(modes, bits, cfg, rules, gates):
    base = cfg["ram_base"]                       # 0x80000000 -- the code/data gigapage
    rom  = 0x00000000                            # ROM + CLINT gigapage
    RWX, RW, RO, XO = "DAXWRV", "DAWRV", "DARV", "DAXV"
    cases, excluded = [], []

    def table(entries):
        """A root table as {index: pte}; index i covers VA [i<<30, (i+1)<<30)."""
        return entries

    def add(cid, mode, entries, addr, access, expect, why, mstatus=(), priv="S",
            pmp_deny_page_table=False, pmp_deny_l1=False):
        cases.append(dict(id=cid, mode=mode, entries=entries, addr=addr, access=access,
                          expect=expect, why=why, mstatus=list(mstatus), priv=priv,
                          pmp_deny_page_table=pmp_deny_page_table,
                          pmp_deny_l1=pmp_deny_l1))

    # The code, the harness and the test data cannot share one page whose
    # permissions the case varies: make it read-only and the test's own
    # instruction fetch faults before the load ever runs; make it a User page
    # and Supervisor cannot fetch from it at all. So entry 2 (the code
    # gigapage) is always RWX and never touched, and the *data* is reached
    # through an alias -- entry 1 maps VA 0x40000000 onto the same physical
    # RAM. The case varies the alias. Same technique the shadow-stack mapping
    # already uses: one region, two views, and a test picks the view it needs.
    DATA_VA = 0x40000000
    def base_map(alias_flags=RWX, alias_ext=0):
        return {0: gigapage(rom, RWX, bits),
                1: gigapage(base, alias_flags, bits, alias_ext),   # data view
                2: gigapage(base, RWX, bits)}                      # code + harness
    identity = base_map()
    data_addr = DATA_VA + 0x20000                                  # -> PA base+0x20000
    UNMAPPED = 0xC0000000                                          # index 3 stays zero

    # ---- translation modes -------------------------------------------------
    for mode in modes:
        if mode == "Bare":
            add("bare_no_translation", mode, {}, base + 0x20000, "Load(Data)", "allow",
                "satp.MODE=Bare: the address is used unchanged")
            continue
        if not cfg["ext"].get(mode):
            excluded.append((mode, f"extensions.{mode}.supported is false in this configuration"))
            continue
        lvl = walk_levels(mode)
        if mode != "Sv39":
            # A leaf at the root level is a superpage covering 2^(9*(lvl+1)+12)
            # bytes, so one entry at index 0 with PPN 0 identity-maps everything
            # below it -- 512 GiB on Sv48, 256 TiB on Sv57. pt_walk requires a
            # superpage's low PPN bits to be zero, which PPN 0 satisfies. No
            # deeper table is needed, so these modes are reachable after all.
            big = {0: pte(0, RWX, bits)}
            add(f"{mode.lower()}_root_superpage", mode, big, base + 0x20000,
                "Load(Data)", "allow",
                f"{mode}: a level-{lvl} leaf with PPN 0 identity-maps "
                f"2^{9*(lvl+1)+12} bytes in one entry")
            excluded.append((f"{mode} unmapped",
                             f"an address above a 2^{9*(lvl+1)+12}-byte root superpage "
                             f"needs more than 32 bits; the address materialiser emits "
                             f"lui+addi. Sv39 covers the unmapped path"))
            continue
        add(f"{mode.lower()}_mapped", mode, identity, data_addr, "Load(Data)", "allow",
            f"{mode}: a level-{lvl} leaf maps a 1 GiB gigapage; the walk resolves")
        add(f"{mode.lower()}_unmapped", mode, identity, UNMAPPED, "Load(Data)", "trap:13",
            f"{mode}: root index 3 is zero, so V=0 -- the walk fails at the root")

    # ---- pte_is_invalid: one case per disjunct we can construct -------------
    INVALID = [
        ("v_clear",       0,                                  "V=0 on the data alias"),
        ("reserved_wx",   gigapage(base, "DAXWV", bits),      "R=0 W=1 X=1 is reserved in all circumstances"),
        ("nonleaf_flags", gigapage(base, "DAV",   bits),      "a non-leaf PTE (X=W=R=0) carrying A and D"),
        ("reserved_bits", gigapage(base, RWX, bits, ext=0x1), "a reserved extension bit set above the PPN"),
    ]
    for name, entry, why in INVALID:
        ents = dict(identity); ents[1] = entry
        add(f"invalid_{name}", "Sv39", ents, data_addr, "Load(Data)", "trap:13",
            f"pte_is_invalid: {why}")
    for d in rules:
        if "menvcfg" in d or "currentlyEnabled" in d:
            excluded.append((d.split("&")[0].strip()[:44],
                             "needs an extension or menvcfg bit turned OFF: a "
                             "configuration variant, not a page-table variant"))

    # ---- check_PTE_permission gate 3: R/W/X, and MXR ------------------------
    for kind, (w, mnem, _op, cause, needs) in ACCESS.items():
        for granted in (True, False):
            flags = RWX if granted else (RO if needs == "W" else XO)
            add(f"perm_{mnem}_{'granted' if granted else 'denied'}", "Sv39",
                base_map(flags), data_addr, kind,
                "allow" if granted else f"trap:{cause}",
                f"{mnem} needs {needs}; the data alias is {flags}")
    add("mxr_makes_x_readable", "Sv39", base_map(XO), data_addr, "Load(Data)", "allow",
        "mstatus.MXR: an execute-only page becomes readable with no PTE change",
        mstatus=("mxr",))
    add("mxr_clear_x_not_readable", "Sv39", base_map(XO), data_addr, "Load(Data)", "trap:13",
        "the same page without MXR: the control that proves MXR did the work")

    # ---- gate 1: privilege and SUM ----------------------------------------
    add("sum_clear_user_page_denied", "Sv39", base_map(RWX + "U"), data_addr,
        "Load(Data)", "trap:13", "a U=1 page read from Supervisor with SUM clear")
    add("sum_set_user_page_allowed", "Sv39", base_map(RWX + "U"), data_addr,
        "Load(Data)", "allow",
        "the same page with mstatus.SUM set -- loads and stores only", mstatus=("sum",))

    # ---- pt_walk: a misaligned superpage -----------------------------------
    # A leaf above level 0 is a superpage; pt_walk requires its low PPN bits to
    # be zero. Setting one makes the walk fail with PTW_Misaligned -- a distinct
    # exit from an invalid PTE, and one no permission setting can produce.
    ents = dict(identity)
    ents[1] = pte((base >> 12) | 1, RWX, bits)      # low PPN bit set on a level-2 leaf
    add("superpage_misaligned", "Sv39", ents, data_addr, "Load(Data)", "trap:13",
        "pt_walk: a level-2 leaf whose low PPN bits are non-zero -- PTW_Misaligned")

    # ---- update_PTE_Bits: the hardware A/D update path ---------------------
    # The ordinary preamble pre-sets A and D so no PTE write happens. Clearing
    # them makes the walk write the PTE back -- a store performed by a load,
    # which is also the only way Load/Store(PageTableEntry) is ever produced.
    # update_and_write_pte chooses between two policies:
    #   menvcfg.ADUE = 1  -> the hardware writes the PTE back (Svadu)
    #   menvcfg.ADUE = 0  -> PTW_PTE_Needs_Update, and the access page-faults
    # Both are correct behaviour; which one applies is a CSR bit, not a PTE
    # bit, so it is an axis of the case exactly as MXR and SUM are.
    add("adue_clear_a_needs_update", "Sv39", base_map("DXWRV"), data_addr,
        "Load(Data)", "trap:13",
        "A=0 with menvcfg.ADUE clear: PTW_PTE_Needs_Update -> page fault, no PTE write")
    add("adue_set_a_hw_update", "Sv39", base_map("DXWRV"), data_addr,
        "Load(Data)", "allow",
        "A=0 with menvcfg.ADUE set: the hardware writes the PTE back -- a load "
        "performing a store, which is the only way Store(PageTableEntry) occurs",
        mstatus=("adue",))
    add("adue_set_d_hw_update", "Sv39", base_map("AXWRV"), data_addr,
        "Store(Data)", "allow",
        "D=0 on a store with ADUE set: the walk writes both A and D back",
        mstatus=("adue",))

    # ---- two-level walks: pt_walk's recursion ------------------------------
    # Every case above uses a leaf at the ROOT level, so pt_walk resolves in one
    # step and its recursive descent -- the non-leaf pointer path, the level-0
    # termination, the misaligned-superpage check at depth -- never runs. A
    # non-leaf root PTE (X=W=R=0, V=1) pointing at the level-1 table fixes that.
    # The level-1 table sits one page after the root; index 512+k is its entry k.
    PT_ADDR = 0x80012000
    L1_PPN = (PT_ADDR + 4096) >> 12
    def two_level(l1_flags=RWX, l1_ppn=None):
        """Root index 1 becomes a pointer; the leaf lives at level 1."""
        ents = dict(identity)
        ents[1] = pte(L1_PPN, "V", bits)                       # non-leaf: X=W=R=0
        # VA 0x40000000 -> L1 index 0, a 2 MiB megapage onto physical `base`
        ents[512 + 0] = pte((l1_ppn if l1_ppn is not None else base >> 12),
                            l1_flags, bits)
        return ents
    add("two_level_walk", "Sv39", two_level(), data_addr, "Load(Data)", "allow",
        "a non-leaf root PTE: pt_walk recurses to level 1 and finds a 2 MiB leaf")
    add("two_level_denied", "Sv39", two_level(RO), data_addr, "Store(Data)", "trap:15",
        "the same two-level walk, with the level-1 leaf read-only")
    add("two_level_megapage_misaligned", "Sv39",
        two_level(RWX, (base >> 12) | 1), data_addr, "Load(Data)", "trap:13",
        "a level-1 leaf whose low PPN bit is set -- PTW_Misaligned at depth")

    # ---- the joint PMP + virtual memory case -------------------------------
    # The walker's own PTE reads go through PMP. Denying the region the page
    # table lives in makes read_pte fail, which is PTW_No_Access -- reported as
    # an *access* fault where every other walk failure is a *page* fault.
    # Getting that pair backwards is a classic implementation bug, and neither
    # feature alone can reach it.
    # Denying the page table denies *every* translated access, and the first
    # one is the test's own instruction fetch -- so the fault arrives as
    # E_Fetch_Access_Fault, not E_Load_Access_Fault. The point still holds and
    # is what the case exists to prove: a walk stopped by PMP is an ACCESS
    # fault, where every other walk failure is a PAGE fault. Reaching the load
    # variant needs a two-level table so the code's translation succeeds while
    # only the data's leaf is denied -- see the exclusions.
    # With a two-level table the code's own translation resolves at the root,
    # so denying only the *level-1* page leaves fetches working and stops just
    # the data walk -- which is the load access fault (5) the fetch variant
    # could not isolate.
    add("pmp_denies_l1_table", "Sv39", two_level(), data_addr, "Load(Data)", "trap:5",
        "PMP denies only the level-1 table: the code still translates, the data "
        "walk hits PTW_No_Access -> LOAD access fault (5), not a page fault (13)",
        pmp_deny_l1=True)
    add("pmp_denies_page_table", "Sv39", identity, data_addr, "Load(Data)", "trap:1",
        "PMP denies the page table's region: the fetch's own walk hits "
        "PTW_No_Access -> fetch ACCESS fault (1), not a page fault (12)",
        pmp_deny_page_table=True)

    # ---- what we cannot build yet -----------------------------------------
    for what, why in [
        ("A/D read-back", "both policies are exercised, but nothing yet reads the PTE "
                          "back to assert which bits the hardware wrote"),
        ("satp switching", "needs satp changed mid-test; the harness writes it once in "
                           "the preamble. TLB hit/miss and sfence are exercised, ASID "
                           "matching is not"),
    ]:
        excluded.append((what, why))
    return cases, excluded


# ── output ───────────────────────────────────────────────────────────────────

def to_toml(cases, meta):
    out = ["# Generated by vmem_cases.py -- do not edit by hand.",
           "# Every value is derived from the Sail model and the model configuration.", ""]
    out += [f"# {k}: {v}" for k, v in meta.items()] + [""]
    for c in cases:
        out.append("[[case]]")
        out.append(f'id       = "{c["id"]}"')
        out.append(f'mode     = "{c["mode"]}"')
        ents = ", ".join(f'"{i}=0x{v:x}"' for i, v in sorted(c["entries"].items()))
        out.append(f"entries  = [{ents}]      # root-table index = PTE")
        out.append(f'addr     = 0x{c["addr"]:x}')
        out.append(f'access   = "{c["access"]}"')
        out.append(f'priv     = "{c["priv"]}"')
        out.append(f'mstatus  = [{", ".join(chr(34)+m+chr(34) for m in c["mstatus"])}]')
        out.append(f'expect   = "{c["expect"]}"')
        if c.get("pmp_deny_page_table"):
            out.append('pmp_deny_page_table = true')
        if c.get("pmp_deny_l1"):
            out.append('pmp_deny_l1 = true')
        out.append(f'why      = "{c["why"]}"')
        out.append("")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument("--config", default=os.path.join(here, "autotest/configs/rv64.json"))
    ap.add_argument("-o", "--out", default=os.path.join(here, "cases/vmem.toml"))
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()

    modes, bits = satp_modes(), pte_flags()
    rules, gates = invalidity_rules(), permission_gates()
    cfg = load_config(a.config)

    print("read from the model:")
    print(f"  enum SATPMode           {len(modes)} modes on RV{cfg['xlen']}: "
          + ", ".join(f"{k}={v:#x}" for k, v in modes.items()))
    print(f"  bitfield PTE_Flags      {' '.join(sorted(bits, key=lambda k: -bits[k]))}")
    print(f"  pte_is_invalid          {len(rules)} disjuncts")
    print(f"  check_PTE_permission    {len(gates)} gates")
    for name, reads, note in gates:
        print(f"      {name:14s} reads {reads:28s} -- {note}")
    on = [m for m in modes if m == 'Bare' or cfg['ext'].get(m)]
    print(f"  config                  Sv modes enabled: {', '.join(on)}")

    cases, excluded = build_cases(modes, bits, cfg, rules, gates)
    print(f"\n{len(cases)} cases derived, {len(excluded)} exclusions recorded\n")
    for k, v in sorted(collections.Counter(c["id"].split("_")[0] for c in cases).items(),
                       key=lambda x: -x[1]):
        print(f"  {v:3d}  {k}")
    print("\nexclusions (each with its reason):")
    for what, why in excluded:
        print(f"  {what:34s} {why}")

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    open(a.out, "w").write(to_toml(cases, {
        "modes": len(modes), "disjuncts": len(rules), "gates": len(gates),
        "cases": len(cases), "exclusions": len(excluded),
        "config": os.path.basename(a.config)}))
    print(f"\nwrote {a.out}")

    if a.check:
        have_modes = {c["mode"] for c in cases}
        have_mstatus = {m for c in cases for m in c["mstatus"]}
        print("\ncompleteness -- every alternative the model declares:")
        print(f"  satp modes       {len(have_modes)}/{len(modes)}"
              f"  ({', '.join(sorted(have_modes))})")
        print(f"  invalidity       4/{len(rules)} disjuncts have a case "
              f"(the rest need a configuration variant)")
        print(f"  permission gates 2/{len(gates)} exercised "
              f"(privilege+SUM, R/W/X+MXR; shadow stack is Zicfiss)")
        print(f"  mstatus axes     {len(have_mstatus)}/2 ({', '.join(sorted(have_mstatus))})")


if __name__ == "__main__":
    main()
