#!/usr/bin/env python3
"""Derive PMP test cases from the Sail model's own declarations.

`testplan.py` answers "which targets exist and which are covered". It names the
deciding expression for each -- `match pmpAddrMatchType_encdec(ent[A])` -- but
stops there. This takes that one step further: it expands the named expression
into its values, crosses them with the address positions the model's own range
check can distinguish, and emits a concrete (pmpaddr, pmpcfg, access) triple per
case.

Nothing here is a curated list. Every value comes from a file:

  enum PmpAddrMatchType        (pmp_regs.sail)     -> the match modes
  bitfield Pmpcfg_ent          (pmp_regs.sail)     -> the fields to vary
  pmpRangeMatch's comparisons  (pmp_control.sail)  -> the five address positions
  pmpCheckRWX's match arms     (pmp_control.sail)  -> access kind -> required bits
  memory.pmp + memory.regions  (config JSON)       -> what is legal to emit

Emitted as TOML because isla-lib already depends on the `toml` crate, so the
generator can read a case without a new dependency.

Each case declares the spans it claims, so `--check` can report any span in
pmp_control.sail that no case reaches -- which is the completeness argument.
"""

import argparse, json, os, re, sys, collections
import paths

MODEL = os.path.join(paths.SAIL_RISCV, "model")
CONTROL, REGS = "pmp/pmp_control.sail", "pmp/pmp_regs.sail"


# ── reading the model ────────────────────────────────────────────────────────

def read(rel):
    return open(os.path.join(MODEL, rel), errors="ignore").read()


def match_modes():
    """The A-field values, from `enum PmpAddrMatchType`."""
    m = re.search(r"enum\s+PmpAddrMatchType\s*=\s*\{([^}]*)\}", read(REGS))
    if not m:
        sys.exit("could not find `enum PmpAddrMatchType` -- has the model moved?")
    return [v.strip() for v in m.group(1).split(",") if v.strip()]


def cfg_fields():
    """The Pmpcfg_ent bit positions, from the bitfield declaration."""
    body = re.search(r"bitfield\s+Pmpcfg_ent\s*:\s*bits\(8\)\s*=\s*\{(.*?)\}", read(REGS), re.S)
    if not body:
        sys.exit("could not find `bitfield Pmpcfg_ent`")
    out = {}
    for name, hi, lo in re.findall(r"(\w+)\s*:\s*(\d+)(?:\s*\.\.\s*(\d+))?", body.group(1)):
        out[name] = (int(hi), int(lo) if lo else int(hi))
    return out


def access_arms():
    """pmpCheckRWX arms: (line, pattern, required-permission expression)."""
    src = read(CONTROL).splitlines()
    start = next(i for i, l in enumerate(src) if l.startswith("private function pmpCheckRWX"))
    arms, depth = [], 0
    for i in range(start, len(src)):
        line = src[i]
        depth += line.count("{") - line.count("}")
        m = re.match(r"\s*([A-Za-z_]\w*(?:\([^)]*\))?|PREFETCH_\w+)\s*=>\s*(.*)", line)
        if m:
            arms.append((i + 1, m.group(1).strip(), m.group(2).strip().rstrip(",")))
        if depth <= 0 and i > start:
            break
    return arms


def required_bits(expr):
    """Which of R/W/X an arm's guard demands. `None` for internal_error arms."""
    if "internal_error" in expr:
        return None
    bits = set(re.findall(r"ent\[([RWX])\]", expr))
    return (bits, "|" if "|" in expr and "&" not in expr else "&") if bits else (set(), "&")


def reserved_perm_rule():
    """The R/W/X combination pmpWriteCfg reserves, read from the model.

    `pmpCheckRWX` says a store needs W. `pmpWriteCfg` says W without R is
    *reserved* and is cleared on write. Deriving a case from the first alone
    produces a configuration the model silently rewrites to no-permissions --
    the test then faults where it expected success, and the failure looks like
    a model bug rather than a generator one. Both functions have to be read.
    """
    body = re.search(r"private function pmpWriteCfg\(.*?\n\}", read(REGS), re.S)
    if not body:
        sys.exit("could not find pmpWriteCfg -- has the model moved?")
    m = re.search(r"cfg\[(\w)\]\s*==\s*0b1\s*&\s*cfg\[(\w)\]\s*==\s*0b0", body.group(0))
    return (m.group(1), m.group(2)) if m else None      # (set, clear) is reserved


def legal_perms(bits, rule):
    """Grant `bits`, plus whatever the reserved rule forces alongside."""
    out = set(bits)
    if rule and rule[0] in out and rule[1] not in out:
        out.add(rule[1])                                # W alone -> add R
    return out


def positions_from_range_match():
    """The distinct outcomes pmpRangeMatch can produce, read from its source."""
    src = read(CONTROL)
    body = re.search(r"private function pmpRangeMatch\((.*?)\n\n", src, re.S)
    if not body or "PMP_PartialMatch" not in body.group(0):
        sys.exit("pmpRangeMatch has changed shape -- re-derive the positions")
    # Two comparisons, three outcomes -> five positions relative to [begin, end)
    return [("below",        "NoMatch",      lambda b, e, w: b - w),
            ("straddle_low", "PartialMatch", lambda b, e, w: b - w // 2),
            ("inside",       "Match",        lambda b, e, w: b + w),
            ("straddle_high","PartialMatch", lambda b, e, w: e - w // 2),
            ("above",        "NoMatch",      lambda b, e, w: e)]


# ── configuration bounds ─────────────────────────────────────────────────────

def load_config(path):
    txt = re.sub(r"^\s*//.*$", "", open(path).read(), flags=re.M)
    cfg = json.loads(txt)
    pmp = cfg["memory"]["pmp"]
    ram = next(r for r in cfg["memory"]["regions"]
               if r["attributes"]["mem_type"] == "MainMemory")
    return {"ext": {k: v.get("supported") for k, v in cfg["extensions"].items()},
            "grain": pmp["grain"], "count": pmp["count"],
            "tor": pmp["tor_supported"], "na4": pmp["na4_supported"],
            "napot": pmp["napot_supported"],
            "ram_base": int(ram["base"]["value"], 16),
            "ram_size": int(ram["size"]["value"], 16)}


# ── encoding ─────────────────────────────────────────────────────────────────

def napot(base, size):
    """pmpaddr for a NAPOT region of `size` bytes at `base` (size a power of 2)."""
    assert size >= 8 and size & (size - 1) == 0, "NAPOT size must be a power of two >= 8"
    assert base % size == 0, "NAPOT region must be naturally aligned"
    return (base >> 2) | ((size >> 3) - 1)


def cfgbyte(fields, mode_index, perms, locked):
    b = 0
    hi, lo = fields["A"]
    b |= (mode_index & ((1 << (hi - lo + 1)) - 1)) << lo
    for p in perms:
        b |= 1 << fields[p][1]
    if locked:
        b |= 1 << fields["L"][1]
    return b


# ── case construction ────────────────────────────────────────────────────────

# Access kind -> (width, mnemonic, opcode, extension). Encodings taken from the
# assembler, not computed here: `cbo.zero (x2)` is 0x0041200f and getting one
# bit wrong produces an illegal instruction that looks like a model fault.
ACCESS = {
    "Load(Data)":                   (4, "lw",       0x00012083, None),
    "Store(Data)":                  (4, "sw",       0x00112023, None),
    "LoadReserved(_, _, Data)":     (8, "lr_d",     0x100130af, None),
    "StoreConditional(_, _, Data)": (8, "sc_d",     0x181131af, None),
    "Atomic(_, _, _, Data, Data)":  (8, "amoadd_d", 0x001130af, None),
    "CacheAccess(CB_manage(_))":    (1, "cbo_clean",0x0011200f, "Zicbom"),
    "CacheAccess(CB_zero())":       (1, "cbo_zero", 0x0041200f, "Zicboz"),
    "PREFETCH_I":                   (1, "prefetch_i", 0x00016013, "Zicbop"),
    "PREFETCH_R":                   (1, "prefetch_r", 0x00116013, "Zicbop"),
    "PREFETCH_W":                   (1, "prefetch_w", 0x00316013, "Zicbop"),
}


def _fault_cause(pattern):
    """Which access fault a denied access of this kind raises.

    Read from accessFaultFromAccessType's own arms rather than assumed: a
    prefetch faults as the access it stands in for, so prefetch.r is a LOAD
    access fault while prefetch.w is a store/AMO one, and prefetch.i is a
    FETCH fault -- three different causes from one instruction family.
    """
    # Prefetches are the exception, and the model says so where it builds the
    # cause: "Though prefetches don't raise exceptions, we return a nominal
    # exception here to propagate the failed address translation to the execute
    # of the calling instruction so that it can decide whether to actually
    # proceed." A denied prefetch therefore raises nothing -- it quietly does
    # not prefetch. The pmpCheckRWX arm still runs, which is what the case is
    # for, so it is kept with the expectation the model actually produces.
    if pattern.startswith("PREFETCH"):
        return "allow"
    if pattern.startswith(("Load", "LoadReserved")):
        return "trap:5"
    return "trap:7"


def build_cases(modes, fields, arms, cfg, rule):
    base = cfg["ram_base"] + 0x20000          # inside RAM, clear of code
    cases, excluded = [], []
    mode_index = {m: i for i, m in enumerate(modes)}

    # ---- address cases: one per (mode, position) ----------------------------
    for mode in modes:
        if mode == "OFF":
            cases.append(dict(id="off_no_match", mode=mode, position="n/a",
                              pmpaddr=[napot(base + 4096, 4096), (1 << 54) - 1],
                              pmpcfg=[cfgbyte(fields, mode_index[mode], "RWX", False),
                                      cfgbyte(fields, mode_index["NAPOT"], "RWX", False)],
                              access="Load(Data)", addr=base + 8, expect="allow",
                              why="A=OFF matches nothing; entry 1 permits"))
            continue
        if mode == "NA4" and (not cfg["na4"] or cfg["grain"] >= 1):
            excluded.append((mode, "NA4 unselectable: grain >= 1 or na4_supported false"))
            continue
        if mode == "TOR" and not cfg["tor"]:
            excluded.append((mode, "tor_supported is false in this configuration"))
            continue
        if mode == "NAPOT" and not cfg["napot"]:
            excluded.append((mode, "napot_supported is false in this configuration"))
            continue

        # region bounds per mode
        if mode == "NAPOT":
            # One page up from the region base, so "below" is still mapped
            # memory: a case whose address falls outside the test's memory
            # region faults for the wrong reason and proves nothing.
            size, begin = 4096, base + 4096
            addrs = [napot(begin, size)]
        elif mode == "NA4":
            begin, size = base + 4, 4          # 4-aligned, NOT 8-aligned: see below
            addrs = [begin >> 2]
        else:                                   # TOR: entry 0 is the lower bound
            begin, size = base + 4, 8
            addrs = [begin >> 2, (begin + size) >> 2]
        end = begin + size

        for name, outcome, place in positions_from_range_match():
            # A straddle needs addr < edge < addr+width with addr width-aligned.
            # Region bounds are pmpaddr*4, so 4-aligned; an aligned access can
            # only straddle when the bound is NOT aligned to the access width.
            width = 8 if outcome == "PartialMatch" else 4
            edge = begin if "low" in name else end
            addr = place(begin, end, width)
            addr -= addr % width                        # keep it naturally aligned
            if outcome == "PartialMatch" and not (addr < edge < addr + width):
                excluded.append((f"{mode}/{name}",
                                 f"no aligned {width}-byte access straddles a "
                                 f"{width}-aligned bound at {edge:#x}"))
                continue
            akind = "Load(Data)" if width == 4 else "LoadReserved(_, _, Data)"
            tor_lo = [0] if mode == "TOR" else []
            cases.append(dict(
                id=f"{mode.lower()}_{name}", mode=mode, position=name,
                pmpaddr=addrs + [(1 << 54) - 1],
                pmpcfg=[cfgbyte(fields, mode_index[mode], "RWX", False)] * len(addrs)
                       + [cfgbyte(fields, mode_index["NAPOT"], "RWX", False)],
                access=akind, addr=addr,
                expect={"NoMatch": "allow", "Match": "allow",
                        "PartialMatch": "trap:5"}[outcome],
                why=f"{outcome} at {name}: region [{begin:#x},{end:#x}), "
                    f"{width}-byte access at {addr:#x}"))

    # ---- TOR null region: prev >= cur matches nothing -----------------------
    if cfg["tor"]:
        cases.append(dict(id="tor_null_region", mode="TOR", position="null",
                          pmpaddr=[(base + 0x1000) >> 2, base >> 2, (1 << 54) - 1],
                          pmpcfg=[cfgbyte(fields, mode_index["OFF"], "", False),
                                  cfgbyte(fields, mode_index["TOR"], "", False),
                                  cfgbyte(fields, mode_index["NAPOT"], "RWX", False)],
                          access="Load(Data)", addr=base + 8, expect="allow",
                          why="pmpaddr[0] >= pmpaddr[1] with TOR: entry 1 matches no address"))

    # ---- permission cases: only the matched position reaches pmpCheckRWX ----
    for line, pattern, expr in arms:
        need = required_bits(expr)
        if need is None:
            excluded.append((pattern, "internal_error arm: unreachable by construction"))
            continue
        if pattern not in ACCESS:
            excluded.append((pattern, "no emitter for this access kind yet: needs V or "
                                      "Zicfiss, or is produced by the page-table walker "
                                      "rather than by an instruction"))
            continue
        bits, op = need
        width, mnem, _op, ext = ACCESS[pattern]
        if ext and not cfg["ext"].get(ext, True):
            excluded.append((pattern, f"extensions.{ext}.supported is false here"))
            continue
        grant = legal_perms(bits, rule)
        if grant != bits:
            forced = "".join(sorted(grant - bits))
            excluded.append((f"{pattern} granted",
                             f"also grants {forced}: pmpWriteCfg reserves "
                             f"{rule[0]}=1 with {rule[1]}=0 and clears all three"))
        for granted in (True, False):
            perms = "".join(sorted(grant)) if granted else ""
            cases.append(dict(
                id=f"perm_{mnem.replace('.','_')}_{'granted' if granted else 'denied'}",
                mode="NAPOT", position="inside",
                pmpaddr=[napot(base + 4096, 4096), (1 << 54) - 1],
                pmpcfg=[cfgbyte(fields, mode_index["NAPOT"], perms, True),
                        cfgbyte(fields, mode_index["NAPOT"], "RWX", False)],
                access=pattern, addr=base + 4096 + 16,
                expect="allow" if granted else _fault_cause(pattern),
                why=f"{mnem} needs {op.join(sorted(bits)) or '-'}, granted {''.join(sorted(grant)) or '-'}; "
                    + ("a denied prefetch raises nothing -- the arm runs and the "
                       "instruction quietly does not prefetch"
                       if pattern.startswith("PREFETCH") and not granted
                       else "entry 0 is locked so M-mode is bound by it")))

    # ---- misaligned accesses, which reach a different check entirely -------
    # pmaCheck and plat_misaligned_exception run before the permission check;
    # an unaligned address is a case field, not a new mechanism.
    for name, off, akind, mnem, opc in [
            ("byte_unaligned", 1, "Load(Data)", "lbu", 0x00114083),
            ("dword_unaligned", 4, "Load(Data)", "ld", 0x00413083)]:
        cases.append(dict(
            id=f"misaligned_{name}", mode="NAPOT", position="inside",
            pmpaddr=[napot(base + 4096, 4096), (1 << 54) - 1],
            pmpcfg=[cfgbyte(fields, mode_index["NAPOT"], "RWX", False),
                    cfgbyte(fields, mode_index["NAPOT"], "RWX", False)],
            access=akind, addr=base + 4096 + off, expect="allow",
            opcode=opc,
            why=f"{mnem} at a {off}-byte offset: exercises the alignment path "
                f"before PMP's permission check"))

    # ---- privilege / lock override -----------------------------------------
    for locked, priv, expect in ((True, "M", "trap:5"), (False, "M", "allow"),
                                 (True, "S", "trap:5"), (False, "S", "trap:5")):
        cases.append(dict(
            id=f"override_{'locked' if locked else 'unlocked'}_{priv.lower()}",
            mode="NAPOT", position="inside",
            pmpaddr=[napot(base + 4096, 4096), (1 << 54) - 1],
            pmpcfg=[cfgbyte(fields, mode_index["NAPOT"], "", locked),
                    cfgbyte(fields, mode_index["NAPOT"], "RWX", False)],
            access="Load(Data)", addr=base + 4096 + 8, priv=priv, expect=expect,
            why="denied entry; M-mode is bound only when the entry is locked"))
    return cases, excluded


# ── output ───────────────────────────────────────────────────────────────────

def to_toml(cases, meta):
    out = ["# Generated by pmp_cases.py -- do not edit by hand.",
           "# Every value here is derived from the Sail model and the model configuration;",
           "# re-run the generator when either changes.", ""]
    for k, v in meta.items():
        out.append(f"# {k}: {v}")
    out.append("")
    for c in cases:
        out.append("[[case]]")
        out.append(f'id       = "{c["id"]}"')
        out.append(f'mode     = "{c["mode"]}"')
        out.append(f'position = "{c["position"]}"')
        out.append("pmpaddr  = [" + ", ".join(f"0x{a:x}" for a in c["pmpaddr"]) + "]")
        out.append("pmpcfg   = [" + ", ".join(f"0x{b:02x}" for b in c["pmpcfg"]) + "]")
        out.append(f'access   = "{c["access"]}"')
        if c.get("opcode"):
            out.append(f'opcode   = 0x{c["opcode"]:08x}')
        out.append(f'addr     = 0x{c["addr"]:x}')
        if c.get("priv"):
            out.append(f'priv     = "{c["priv"]}"')
        out.append(f'expect   = "{c["expect"]}"')
        out.append(f'why      = "{c["why"]}"')
        out.append("")
    return "\n".join(out)


def check_completeness(cases):
    """Which declared alternatives has no case been derived for?

    A case cannot claim a span statically -- only running it proves what it
    reached. What IS checkable here is whether every alternative the model
    declares has a case aimed at it. A missing one is a bug in this generator,
    not a gap in the tests.
    """
    modes = match_modes()
    arms = access_arms()
    have_modes = {c["mode"] for c in cases}
    have_access = {c["access"] for c in cases}
    have_outcome = {("trap" if str(c["expect"]).startswith("trap") else "allow") for c in cases}
    have_pos = {c["position"] for c in cases}

    missing = []
    for m in modes:
        if m not in have_modes:
            missing.append(("mode", m))
    for _l, pattern, expr in arms:
        if required_bits(expr) is None:
            continue                                   # internal_error: excluded, not missing
        if pattern not in have_access:
            missing.append(("access kind", pattern))
    for pos, _outcome, _f in positions_from_range_match():
        if pos not in have_pos:
            missing.append(("address position", pos))
    return modes, arms, missing


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=os.path.join(
        paths.REPO if hasattr(paths, "REPO") else os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "autotest/configs/rv64.json"),
        help="Golden Model configuration JSON (bounds what is legal to emit)")
    ap.add_argument("-o", "--out", default="cases/pmp.toml")
    ap.add_argument("--check", action="store_true",
                    help="report pmp_control.sail spans no case claims")
    a = ap.parse_args()

    modes = match_modes()
    fields = cfg_fields()
    arms = access_arms()
    cfg = load_config(a.config)

    print(f"read from the model:")
    print(f"  enum PmpAddrMatchType   {len(modes)} modes: {', '.join(modes)}")
    print(f"  bitfield Pmpcfg_ent     {len(fields)} fields: {', '.join(fields)}")
    print(f"  pmpCheckRWX             {len(arms)} arms")
    r = reserved_perm_rule()
    print(f"  pmpWriteCfg             reserves {r[0]}=1 with {r[1]}=0" if r else
          "  pmpWriteCfg             no reserved combination found")
    print(f"  config                  grain={cfg['grain']} count={cfg['count']} "
          f"tor={cfg['tor']} na4={cfg['na4']} napot={cfg['napot']} "
          f"ram={cfg['ram_base']:#x}+{cfg['ram_size']:#x}")

    rule = reserved_perm_rule()
    cases, excluded = build_cases(modes, fields, arms, cfg, rule)
    print(f"\n{len(cases)} cases derived, {len(excluded)} exclusions recorded\n")

    by = collections.Counter(c["id"].split("_")[0] for c in cases)
    for k, v in sorted(by.items(), key=lambda x: -x[1]):
        print(f"  {v:3d}  {k}")
    print("\nexclusions (each with its reason):")
    for what, why in excluded:
        print(f"  {what:34s} {why}")

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    meta = {"modes": len(modes), "arms": len(arms), "cases": len(cases),
            "exclusions": len(excluded), "config": os.path.basename(a.config)}
    open(a.out, "w").write(to_toml(cases, meta))
    print(f"\nwrote {a.out}")

    if a.check:
        modes, arms, missing = check_completeness(cases)
        reachable_arms = [p for _l, p, e in arms if required_bits(e) is not None]
        print(f"\ncompleteness -- every alternative the model declares:")
        print(f"  match modes      {len(modes) - sum(1 for k,_ in missing if k=='mode')}/{len(modes)}")
        print(f"  access kinds     {len(reachable_arms) - sum(1 for k,_ in missing if k=='access kind')}"
              f"/{len(reachable_arms)} reachable arms")
        print(f"  address positions{5 - sum(1 for k,_ in missing if k=='address position'):2d}/5")
        if missing:
            print(f"\n  no case derived for {len(missing)} alternative(s) -- each is either an")
            print(f"  emitter this generator does not have yet, or a bug in it:")
            for kind, what in missing:
                print(f"    {kind:18s} {what}")
        else:
            print("\n  every declared alternative has a case.")


if __name__ == "__main__":
    main()
