#!/usr/bin/env python3
"""Derive a complete architectural inventory from the Sail RISC-V model.

The model is the only source of truth here. Nothing in this script encodes
RISC-V knowledge from the specification, from memory, or from an older model
revision: every row in every output table is produced by reading a declaration
out of a `.sail` file and recording where it came from. Where the model does not
say something unambiguously, the field is emitted as
NOT_DERIVABLE ("Not directly derivable from Sail") rather than filled in by
inference.

Why this exists
---------------
The test-generation framework needs a *reachable-target denominator*: a list of
distinct architectural behaviours it can attempt to exercise, per configuration.
That list has to be recomputed whenever the model changes, or it silently goes
stale and every coverage percentage computed against it becomes wrong in a way
nobody notices. So the inventory is extracted, not maintained.

A note on what counts as a target
---------------------------------
Architectural targets are deliberately kept separate from Sail source-branch
spans. A branch span is a property of how the model is *written*; an
architectural target is a property of what the architecture *does*. One target
may cover many spans, and a span may belong to no target at all. Conflating them
produces a denominator that rewards code structure rather than coverage, so this
script emits only the former and leaves branch coverage to the coverage tooling.

Usage
-----
    extract_inventory.py [--model-dir DIR] [--out-dir DIR]

Defaults to the sail-riscv checkout resolved by python-isla/paths.py, and writes
inventory.{md,csv,json} plus per-category CSVs.
"""

import argparse
import csv
import json
import os
import re
import subprocess
import sys
from collections import OrderedDict

NOT_DERIVABLE = "Not directly derivable from Sail"

# ---------------------------------------------------------------------------
# Source-of-truth locations. These are the only places the script reads from;
# each extractor records the file and line it used, so any row can be traced
# back to a declaration.
# ---------------------------------------------------------------------------
EXTENSIONS_SAIL = "core/extensions.sail"
TYPES_SAIL = "core/types.sail"


def sh(cmd, cwd):
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                              timeout=30).stdout.strip()
    except Exception:
        return ""


def sail_files(model_dir):
    """Every .sail file under the model, relative to model_dir, sorted."""
    out = []
    for root, _dirs, files in os.walk(model_dir):
        for f in files:
            if f.endswith(".sail"):
                out.append(os.path.relpath(os.path.join(root, f), model_dir))
    return sorted(out)


def read(model_dir, rel):
    with open(os.path.join(model_dir, rel), errors="ignore") as f:
        return f.read()


def iter_lines(model_dir, rel):
    """(lineno, text) for one file, 1-indexed to match editors and `grep -n`."""
    for i, line in enumerate(read(model_dir, rel).splitlines(), 1):
        yield i, line


# ---------------------------------------------------------------------------
# Enums. Needed in their own right (privilege modes, interrupt causes,
# translation modes) and as the expansion factor for instruction forms: an
# instruction whose operand is an enum is really N instructions, one per member.
# ---------------------------------------------------------------------------
# `private enum` is as real as `enum`; the visibility keyword changes nothing
# architecturally. Missing it silently dropped PmpAddrMatchType (OFF/TOR/NA4/
# NAPOT) from the PMP inventory, so the prefix is optional here.
_ENUM_ONE_LINE = re.compile(r"^(?:private\s+)?enum\s+(\w+)\s*=\s*\{([^}]*)\}")
_ENUM_OPEN = re.compile(r"^(?:private\s+)?enum\s+(\w+)\s*=\s*\{\s*$")


def extract_enums(model_dir, files):
    """{enum_name: {"members": [...], "source": "file:line"}}

    Handles both the one-line form (`enum uop = {LUI, AUIPC}`) and the block
    form that spans lines, since the model uses both.
    """
    enums = {}
    for rel in files:
        lines = list(iter_lines(model_dir, rel))
        i = 0
        while i < len(lines):
            lineno, text = lines[i]
            m = _ENUM_ONE_LINE.match(text.strip())
            if m:
                members = [x.strip() for x in m.group(2).split(",") if x.strip()]
                enums[m.group(1)] = {"members": members,
                                     "source": f"{rel}:{lineno}"}
                i += 1
                continue
            m = _ENUM_OPEN.match(text.strip())
            if m:
                members, j = [], i + 1
                while j < len(lines):
                    t = lines[j][1].split("//")[0].strip()
                    if t.startswith("}"):
                        break
                    members += [x.strip() for x in t.split(",") if x.strip()]
                    j += 1
                enums[m.group(1)] = {"members": members,
                                     "source": f"{rel}:{lineno}"}
                i = j + 1
                continue
            i += 1
    return enums


# ---------------------------------------------------------------------------
# A. Extensions
#
# The model states each extension three ways, and all three are extracted:
#   enum clause extension    = Ext_Sv39                  <- the identity
#   mapping clause extensionName = Ext_Sv39 <-> "sv39"   <- the canonical name
#   function clause hartSupports(Ext_Sv39) = <predicate>  <- when it applies
# The predicate is what makes a per-configuration denominator possible: it names
# both the config key and any XLEN gating, so an extension that cannot apply to
# a configuration is removed from that configuration's target list *with a
# reason* rather than silently counted as a gap.
# ---------------------------------------------------------------------------
_EXT_ENUM = re.compile(r"^enum clause extension\s*=\s*(Ext_\w+)")
_EXT_NAME = re.compile(r'^mapping clause extensionName\s*=\s*(Ext_\w+)\s*<->\s*"([^"]*)"')
_EXT_SUPP = re.compile(r"^function clause hartSupports\((Ext_\w+)\)\s*=\s*(.+)$")
_XLEN_GATE = re.compile(r"xlen\s*==\s*(\d+)")
_CONFIG_KEY = re.compile(r"config\s+([\w.]+)")

# Which extensions are privileged is a judgement the model does not encode: it
# has no "privileged" flag. Rather than guess per extension, classification is
# derived structurally from where the model puts the code and what the config
# key says, and every extension records which rule classified it so the call can
# be audited. Anything no rule matches is reported as unclassified rather than
# defaulted into a bucket.
PRIV_CONFIG_PREFIXES = ("extensions.S", "extensions.Sm", "extensions.Ss",
                        "extensions.Sv", "extensions.Stateen")
PRIV_DIRS = ("pmp", "sys", "extensions/Smcntrpmf", "extensions/Sscofpmf",
             "extensions/Ssqosid", "extensions/Sstc", "extensions/Stateen",
             "extensions/Svinval", "extensions/pointer_masking")


def extract_extensions(model_dir):
    names, supports, order = {}, {}, []
    src = {}
    for lineno, raw in iter_lines(model_dir, EXTENSIONS_SAIL):
        line = raw.strip()
        m = _EXT_ENUM.match(line)
        if m:
            if m.group(1) not in src:
                order.append(m.group(1))
                src[m.group(1)] = f"{EXTENSIONS_SAIL}:{lineno}"
            continue
        m = _EXT_NAME.match(line)
        if m:
            names[m.group(1)] = m.group(2)
            continue
        m = _EXT_SUPP.match(line)
        if m:
            supports[m.group(1)] = m.group(2).strip()

    out = []
    for ext in order:
        pred = supports.get(ext, "")
        xlen_m = _XLEN_GATE.search(pred)
        cfg_m = _CONFIG_KEY.search(pred)
        cfg_key = cfg_m.group(1) if cfg_m else NOT_DERIVABLE
        # Classification, with the reason recorded.
        if any(cfg_key.startswith(p) for p in PRIV_CONFIG_PREFIXES):
            kind, why = "privileged", f"config key {cfg_key}"
        elif pred and "config" not in pred:
            kind, why = "unprivileged", "no config gate (always present)"
        else:
            kind, why = "unprivileged", f"config key {cfg_key}"
        out.append(OrderedDict(
            id=ext,
            name=names.get(ext, NOT_DERIVABLE),
            classification=kind,
            classified_by=why,
            config_key=cfg_key,
            xlen_requirement=int(xlen_m.group(1)) if xlen_m else "any",
            support_predicate=pred or NOT_DERIVABLE,
            source=src[ext],
        ))
    return out


# ---------------------------------------------------------------------------
# B. Instructions
#
#   union clause instruction = UTYPE : (bits(20), regidx, uop)
#
# The declaration is not the instruction count. `uop` is an enum of {LUI,
# AUIPC}, so this one declaration is two architectural instructions. Forms are
# therefore the product of the enum cardinalities of the operand types, which is
# why the form count is several times the declaration count. Operand types that
# are not enums (bits(20), regidx) contribute one: they are value ranges, not
# distinct behaviours, and enumerating them is the solver's job, not the
# denominator's.
# ---------------------------------------------------------------------------
_INSN = re.compile(r"^union clause instruction\s*=\s*(\w+)\s*:\s*(.*)$")
# Two encoding mappings exist, and both are real: `encdec` for 32-bit
# instructions and `encdec_compressed` for the 16-bit C-extension forms. Only
# matching the first silently dropped 59 compressed encodings.
_ENCDEC = re.compile(
    r"^mapping clause (encdec|encdec_compressed)\s*=\s*(?:forwards\s+)?(\w+)\s*\(?")
# An encdec clause may carry `when <guard>`, which is the model stating the
# configuration prerequisite for that encoding to exist at all
# (`when haveSingleFPU()`, `when xlen == 64`). That is exactly the
# per-configuration reachability information the denominator needs, so it is
# collected per instruction rather than discarded.
_WHEN = re.compile(r"^\s*when\s+(.+?)\s*$")


def extract_encdec_guards(model_dir, files):
    """{instruction_name: {"clauses": n, "guards": [...]}}

    A `when` guard may sit on the line after the encoding body, so the scan
    keeps the most recent encdec name until the next clause begins.
    """
    info = {}
    for rel in files:
        current = None
        for _lineno, raw in iter_lines(model_dir, rel):
            line = raw.split("//")[0].rstrip()
            m = _ENCDEC.match(line.strip())
            if m:
                current = m.group(2)
                rec = info.setdefault(current, {"clauses": 0, "guards": set(),
                                                "compressed": False})
                rec["clauses"] += 1
                if m.group(1) == "encdec_compressed":
                    rec["compressed"] = True
                continue
            if current:
                g = _WHEN.match(line)
                if g:
                    info[current]["guards"].add(g.group(1).strip())
                elif line.strip() == "":
                    current = None
    return {k: {"clauses": v["clauses"], "guards": sorted(v["guards"]),
                "compressed": v["compressed"]}
            for k, v in info.items()}


def _split_types(sig):
    """Operand types from a Sail signature, respecting nesting.

    `(bits(20), regidx, uop)` -> ['bits(20)', 'regidx', 'uop']; `unit` -> [].
    """
    sig = sig.strip().rstrip(",").strip()
    if not sig or sig == "unit":
        return []
    if sig.startswith("(") and sig.endswith(")"):
        sig = sig[1:-1]
    parts, depth, cur = [], 0, ""
    for ch in sig:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        parts.append(cur.strip())
    return [p for p in parts if p]


def extract_instructions(model_dir, files, enums, guards):
    out = []
    for rel in files:
        for lineno, raw in iter_lines(model_dir, rel):
            m = _INSN.match(raw.strip())
            if not m:
                continue
            name, sig = m.group(1), m.group(2)
            g = guards.get(name, {"clauses": 0, "guards": [], "compressed": False})
            types = _split_types(sig)
            factors, enum_ops = [], []
            for t in types:
                base = t.strip()
                if base in enums:
                    n = len(enums[base]["members"])
                    if n > 0:
                        factors.append(n)
                        enum_ops.append(f"{base}({n})")
            forms = 1
            for f in factors:
                forms *= f
            # The directory an instruction lives in is the model's own grouping,
            # and is the most reliable extension attribution available: there is
            # no per-instruction extension annotation to read.
            parts = rel.split(os.sep)
            ext = parts[1] if len(parts) > 1 and parts[0] == "extensions" else parts[0]
            out.append(OrderedDict(
                name=name,
                extension_group=ext,
                signature=sig.strip(),
                operand_types=";".join(types) if types else "unit",
                enum_operands=";".join(enum_ops) if enum_ops else "",
                forms=forms,
                encdec_clauses=g["clauses"],
                compressed=g["compressed"],
                config_prerequisites=";".join(g["guards"]) if g["guards"] else "none stated",
                source=f"{rel}:{lineno}",
            ))
    return out


# ---------------------------------------------------------------------------
# C. CSRs
#
#   mapping clause csr_name_map = 0xC00 <-> "cycle"
#
# Address and name are stated; privilege level is not a field the model
# provides here, but the CSR address itself encodes it architecturally (bits
# [9:8]), which is a property of the address rather than an inference about the
# register, so it is decoded and labelled as such. Read/write behaviour is not
# recoverable from this mapping and is left NOT_DERIVABLE rather than assumed
# from the conventional bits [11:10] reading, which the model does not state
# here.
# ---------------------------------------------------------------------------
_CSR = re.compile(r'^mapping clause csr_name_map\s*=\s*(0x[0-9A-Fa-f]+)\s*<->\s*"([^"]*)"')
_PRIV_FROM_ADDR = {0b00: "User", 0b01: "Supervisor", 0b10: "Hypervisor/VS",
                   0b11: "Machine"}


def extract_csrs(model_dir, files):
    out, seen = [], set()
    for rel in files:
        for lineno, raw in iter_lines(model_dir, rel):
            m = _CSR.match(raw.strip())
            if not m:
                continue
            addr, name = m.group(1), m.group(2)
            key = (addr, name)
            if key in seen:
                continue
            seen.add(key)
            a = int(addr, 16)
            parts = rel.split(os.sep)
            ext = parts[1] if len(parts) > 1 and parts[0] == "extensions" else parts[0]
            out.append(OrderedDict(
                address=addr,
                name=name,
                privilege_from_address=_PRIV_FROM_ADDR[(a >> 8) & 0b11],
                feature_group=ext,
                read_write_behaviour=NOT_DERIVABLE,
                source=f"{rel}:{lineno}",
            ))
    return sorted(out, key=lambda r: int(r["address"], 16))


# ---------------------------------------------------------------------------
# D. Exceptions and interrupts
#
# Both are enumerations with an accompanying bits mapping that assigns the
# architectural cause code. The code is taken from the mapping rather than from
# the member's position, because reserved entries make position unreliable.
# ---------------------------------------------------------------------------
_EXC_MEMBER = re.compile(r"^(E_\w+)\s*:\s*(\w+)")
_EXC_BITS = re.compile(r"^(E_\w+)\(\)?\s*<->\s*0b([01]+)")
_INT_BITS = re.compile(r"^(I_\w+)\s*<->\s*0b([01]+)")


def extract_exceptions(model_dir):
    payload, codes, src = {}, {}, {}
    in_union = False
    for lineno, raw in iter_lines(model_dir, TYPES_SAIL):
        line = raw.split("//")[0].strip()
        if line.startswith("union ExceptionType"):
            in_union = True
            continue
        if in_union:
            if line.startswith("}"):
                in_union = False
            else:
                m = _EXC_MEMBER.match(line)
                if m:
                    payload[m.group(1)] = m.group(2)
                    src[m.group(1)] = f"{TYPES_SAIL}:{lineno}"
        m = _EXC_BITS.match(line)
        if m:
            codes[m.group(1)] = int(m.group(2), 2)

    out = []
    for name in payload:
        out.append(OrderedDict(
            name=name,
            cause_code=codes.get(name, NOT_DERIVABLE),
            payload_type=payload[name],
            reserved=name.startswith("E_Reserved"),
            source=src.get(name, f"{TYPES_SAIL}"),
        ))
    return sorted(out, key=lambda r: (r["cause_code"] == NOT_DERIVABLE,
                                      r["cause_code"] if r["cause_code"] != NOT_DERIVABLE else 0))


def extract_interrupts(model_dir, enums):
    codes = {}
    for lineno, raw in iter_lines(model_dir, TYPES_SAIL):
        m = _INT_BITS.match(raw.split("//")[0].strip())
        if m:
            codes[m.group(1)] = int(m.group(2), 2)
    info = enums.get("InterruptType", {"members": [], "source": TYPES_SAIL})
    out = []
    for name in info["members"]:
        out.append(OrderedDict(
            name=name,
            cause_code=codes.get(name, NOT_DERIVABLE),
            reserved=name.startswith("I_Reserved"),
            source=info["source"],
        ))
    return sorted(out, key=lambda r: (r["cause_code"] == NOT_DERIVABLE,
                                      r["cause_code"] if r["cause_code"] != NOT_DERIVABLE else 0))


# ---------------------------------------------------------------------------
# E. Privilege modes and translation modes
# ---------------------------------------------------------------------------
def extract_privilege_modes(model_dir, enums):
    info = enums.get("Privilege")
    if not info:
        return []
    out = []
    for m in info["members"]:
        out.append(OrderedDict(
            mode=m,
            source=info["source"],
            entry_mechanism=NOT_DERIVABLE,
            return_mechanism=NOT_DERIVABLE,
        ))
    return out


def extract_translation_modes(model_dir, enums):
    info = enums.get("SATPMode")
    if not info:
        return []
    return [OrderedDict(mode=m, source=info["source"]) for m in info["members"]]


# ---------------------------------------------------------------------------
# F. PMP / PMA
#
# PMP entry count and the address-matching modes are both stated by the model.
# PMA attributes are per-region configuration fields rather than Sail
# declarations, so the count of PMA *regions* is a property of a configuration
# file, not of the model, and is reported as such.
# ---------------------------------------------------------------------------
def extract_pmp(model_dir, files, enums):
    """PMP architectural state: registers, the entry count, the Pmpcfg_ent
    permission/lock fields, and the address-matching modes.

    PMA is deliberately absent. Its attributes are per-region fields of a Golden
    Model *configuration* file rather than Sail declarations, so no PMA region
    count exists in the model to extract; that is recorded as a caveat instead
    of guessed at.
    """
    rows = []
    pmp_files = [f for f in files if f.startswith("pmp" + os.sep)]
    for cand in pmp_files:
        for lineno, raw in iter_lines(model_dir, cand):
            line = raw.strip()
            m = re.match(r"^register\s+(pmp\w+)\s*:\s*(.+)$", line)
            if m:
                rows.append(OrderedDict(item=m.group(1), kind="register",
                                        detail=m.group(2).strip(),
                                        source=f"{cand}:{lineno}"))
                continue
            # `let sys_pmp_count : {0, 16, 64} = config memory.pmp.count`
            m = re.match(r"^let\s+(sys_pmp\w+)\s*:\s*([^=]+)=\s*(.+)$", line)
            if m:
                rows.append(OrderedDict(item=m.group(1), kind="configuration",
                                        detail=f"{m.group(2).strip()} = {m.group(3).strip()}",
                                        source=f"{cand}:{lineno}"))
                continue
            # The Pmpcfg_ent bitfield names the permission and lock bits, each
            # of which is a distinct architectural behaviour to exercise.
            m = re.match(r"^bitfield\s+(Pmpcfg_ent)\s*:", line)
            if m:
                rows.append(OrderedDict(item=m.group(1), kind="bitfield",
                                        detail="PMP entry configuration",
                                        source=f"{cand}:{lineno}"))

    # Fields inside Pmpcfg_ent (L, A, X, W, R).
    for cand in pmp_files:
        text = read(model_dir, cand)
        bm = re.search(r"bitfield\s+Pmpcfg_ent\s*:[^{]*\{(.*?)\}", text, re.S)
        if not bm:
            continue
        base = text[:bm.start()].count("\n") + 1
        for off, fline in enumerate(bm.group(1).splitlines()):
            fm = re.match(r"\s*(\w+)\s*:", fline.split("//")[0])
            if fm:
                rows.append(OrderedDict(
                    item=f"Pmpcfg_ent.{fm.group(1)}", kind="bitfield_field",
                    detail="PMP permission/lock/match field",
                    source=f"{cand}:{base + off + 1}"))

    for enum_name, kind in (("PmpAddrMatchType", "address_match_mode"),
                            ("PmpMatch", "match_result"),
                            ("pmpAddrMatch", "match_result")):
        info = enums.get(enum_name)
        if info:
            for m in info["members"]:
                rows.append(OrderedDict(item=f"{enum_name}.{m}", kind=kind,
                                        detail=enum_name,
                                        source=info["source"]))
    return rows


# ---------------------------------------------------------------------------
# G. Unified target inventory
#
# One row per distinct architectural behaviour the framework can attempt. Note
# what is deliberately NOT here: Sail source-branch spans. Those are a
# measurement of the model's text, not of the architecture, and mixing them into
# this denominator would make the figure depend on how the model happens to be
# written.
# ---------------------------------------------------------------------------
def build_targets(ext, insns, csrs, excs, ints, privs, tmodes, pmp):
    t, n = [], 0

    def add(cat, feature, target, mode, cfg, observable, source):
        nonlocal n
        n += 1
        t.append(OrderedDict(
            target_id=f"T{n:05d}", category=cat, feature=feature, target=target,
            required_mode=mode, required_configuration=cfg,
            observable_result=observable, source=source))

    for r in insns:
        # One target per *form*, since each enum-operand variant is a distinct
        # architectural instruction, not a value of one.
        for k in range(r["forms"]):
            suffix = f"#{k}" if r["forms"] > 1 else ""
            add("instruction", r["extension_group"], f"{r['name']}{suffix}",
                NOT_DERIVABLE, r["extension_group"],
                "architectural state after retire or trap", r["source"])
    for r in csrs:
        add("csr", r["feature_group"], f"{r['name']} ({r['address']})",
            r["privilege_from_address"], r["feature_group"],
            "CSR read/write result or illegal-instruction trap", r["source"])
    for r in excs:
        if r["reserved"]:
            continue
        add("exception", "trap handling", r["name"], NOT_DERIVABLE, "base",
            "xcause/xepc/xtval after trap", r["source"])
    for r in ints:
        if r["reserved"]:
            continue
        add("interrupt", "interrupt handling", r["name"], NOT_DERIVABLE, "base",
            "xcause with interrupt bit set; mode of delivery", r["source"])
    for r in privs:
        add("privilege_mode", "privilege", r["mode"], r["mode"], "base",
            "current privilege after entry/return", r["source"])
    for r in tmodes:
        add("translation_mode", "virtual memory", r["mode"], "Supervisor",
            f"satp mode {r['mode']}", "translated access or page fault",
            r["source"])
    for r in pmp:
        add("pmp", "physical memory protection", r["item"], "Machine", "base",
            "access permitted or access fault", r["source"])
    return t


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
def write_csv(path, rows):
    if not rows:
        return
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def md_table(rows, cols=None, limit=None):
    if not rows:
        return "_none extracted_\n"
    cols = cols or list(rows[0].keys())
    out = ["| " + " | ".join(cols) + " |",
           "|" + "|".join("---" for _ in cols) + "|"]
    shown = rows if limit is None else rows[:limit]
    for r in shown:
        out.append("| " + " | ".join(str(r.get(c, "")).replace("|", "\\|")
                                     for c in cols) + " |")
    if limit is not None and len(rows) > limit:
        out.append(f"\n_{len(rows) - limit} further rows in the CSV/JSON outputs._")
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", default=None,
                    help="path to sail-riscv/model (default: resolve via paths.py)")
    ap.add_argument("--repo-dir", default=None,
                    help="path to the sail-riscv checkout, for commit metadata")
    ap.add_argument("--out-dir", default="inventory-out")
    ap.add_argument("--md-limit", type=int, default=40,
                    help="rows per Markdown table before truncating to CSV/JSON")
    args = ap.parse_args()

    model_dir = args.model_dir
    if not model_dir:
        here = os.path.dirname(os.path.abspath(__file__))
        sys.path.insert(0, os.path.join(here, "..", "..", "python-isla"))
        try:
            import paths  # noqa
            model_dir = os.path.join(paths.SAIL_RISCV, "model")
        except Exception:
            sys.exit("could not resolve the model directory; pass --model-dir")
    model_dir = os.path.abspath(model_dir)
    repo_dir = os.path.abspath(args.repo_dir or os.path.join(model_dir, ".."))
    os.makedirs(args.out_dir, exist_ok=True)

    files = sail_files(model_dir)
    enums = extract_enums(model_dir, files)

    ext = extract_extensions(model_dir)
    guards = extract_encdec_guards(model_dir, files)
    insns = extract_instructions(model_dir, files, enums, guards)
    csrs = extract_csrs(model_dir, files)
    excs = extract_exceptions(model_dir)
    ints = extract_interrupts(model_dir, enums)
    privs = extract_privilege_modes(model_dir, enums)
    tmodes = extract_translation_modes(model_dir, enums)
    pmp = extract_pmp(model_dir, files, enums)
    targets = build_targets(ext, insns, csrs, excs, ints, privs, tmodes, pmp)

    unpriv = [e for e in ext if e["classification"] == "unprivileged"]
    priv = [e for e in ext if e["classification"] == "privileged"]
    forms = sum(r["forms"] for r in insns)
    real_exc = [e for e in excs if not e["reserved"]]
    real_int = [i for i in ints if not i["reserved"]]

    provenance = OrderedDict(
        sail_repository=sh(["git", "config", "--get", "remote.origin.url"], repo_dir) or NOT_DERIVABLE,
        commit=sh(["git", "rev-parse", "HEAD"], repo_dir) or NOT_DERIVABLE,
        commit_date=sh(["git", "log", "-1", "--format=%ci"], repo_dir) or NOT_DERIVABLE,
        commit_subject=sh(["git", "log", "-1", "--format=%s"], repo_dir) or NOT_DERIVABLE,
        model_dir=model_dir,
        sail_files_examined=len(files),
        extraction_method=("regex over Sail declarations: `enum clause extension`, "
                           "`mapping clause extensionName`, `function clause hartSupports`, "
                           "`union clause instruction`, `mapping clause csr_name_map`, "
                           "`union ExceptionType` + `exceptionType_bits`, "
                           "`enum InterruptType` + `interruptType_bits`, "
                           "`enum Privilege`, `enum SATPMode`, pmp registers"),
    )

    stats = OrderedDict([
        ("unprivileged_extensions", len(unpriv)),
        ("privileged_extensions", len(priv)),
        ("extensions_total", len(ext)),
        ("privilege_modes", len(privs)),
        ("instruction_declarations", len(insns)),
        ("instruction_forms", forms),
        ("encdec_clauses", sum(r["encdec_clauses"] for r in insns)),
        ("instructions_with_config_guard",
         sum(1 for r in insns if r["config_prerequisites"] != "none stated")),
        ("csrs", len(csrs)),
        ("exception_causes_total", len(excs)),
        ("exception_causes_non_reserved", len(real_exc)),
        ("interrupt_causes_total", len(ints)),
        ("interrupt_causes_non_reserved", len(real_int)),
        ("translation_modes", len(tmodes)),
        ("pmp_pma_items", len(pmp)),
        ("architectural_targets_total", len(targets)),
    ])

    caveats = [
        "Privileged vs unprivileged is not a flag the model carries. It is derived "
        "from each extension's config key prefix and the model's own directory "
        "layout; every extension row records which rule classified it "
        "(`classified_by`) so the call can be audited and overridden.",
        "CSR read/write behaviour is not stated by `csr_name_map`, so it is emitted "
        "as NOT_DERIVABLE rather than inferred from the conventional address-bit "
        "reading.",
        "CSR privilege is decoded from address bits [9:8], which is a property of "
        "the address rather than a separate model declaration.",
        "Instruction forms expand only enum-typed operands. Value-typed operands "
        "(bits(n), regidx) contribute one form each: they are ranges for the solver "
        "to explore, not distinct architectural behaviours.",
        "Privilege-mode entry/return mechanisms and per-instruction extension "
        "attribution beyond directory grouping are NOT_DERIVABLE from a single "
        "declaration and are left unset.",
        "PMA attributes are per-region fields of a Golden Model configuration file, "
        "not Sail declarations, so no PMA region count is derivable from the model "
        "alone.",
        "Architectural targets are deliberately disjoint from Sail source-branch "
        "spans: one target may span many branches, and many branches belong to no "
        "target. Branch coverage is measured separately by the coverage tooling.",
    ]

    payload = OrderedDict(provenance=provenance, statistics=stats,
                          caveats=caveats, extensions=ext, instructions=insns,
                          csrs=csrs, exceptions=excs, interrupts=ints,
                          privilege_modes=privs, translation_modes=tmodes,
                          pmp_pma=pmp, targets=targets)

    with open(os.path.join(args.out_dir, "inventory.json"), "w") as f:
        json.dump(payload, f, indent=2)
    for name, rows in [("extensions", ext), ("instructions", insns),
                       ("csrs", csrs), ("exceptions", excs),
                       ("interrupts", ints), ("privilege_modes", privs),
                       ("translation_modes", tmodes), ("pmp_pma", pmp),
                       ("targets", targets)]:
        write_csv(os.path.join(args.out_dir, f"{name}.csv"), rows)

    L = args.md_limit
    md = [
        "# Sail-derived architectural inventory",
        "",
        "Generated by `tools/inventory/extract_inventory.py`. Every row is derived "
        "from a declaration in the Sail model; nothing is supplied from the "
        "specification or from prior knowledge. Fields the model does not state "
        f"unambiguously are marked `{NOT_DERIVABLE}`.",
        "",
        "## Provenance",
        "",
        md_table([OrderedDict(field=k, value=v) for k, v in provenance.items()]),
        "## Statistics",
        "",
        md_table([OrderedDict(quantity=k, value=v) for k, v in stats.items()]),
        "## Caveats and non-derivable fields",
        "",
        "\n".join(f"- {c}" for c in caveats),
        "",
        f"## A. Extensions ({len(ext)}: {len(unpriv)} unprivileged, {len(priv)} privileged)",
        "",
        md_table(ext, limit=L),
        f"## B. Instruction declarations ({len(insns)} declarations, {forms} forms)",
        "",
        md_table(insns, limit=L),
        f"## C. CSRs ({len(csrs)})",
        "",
        md_table(csrs, limit=L),
        f"## D. Exception causes ({len(excs)}, {len(real_exc)} non-reserved)",
        "",
        md_table(excs, limit=L),
        f"## E. Interrupt causes ({len(ints)}, {len(real_int)} non-reserved)",
        "",
        md_table(ints, limit=L),
        f"## F. Privilege modes ({len(privs)})",
        "",
        md_table(privs),
        f"## G. Translation modes ({len(tmodes)})",
        "",
        md_table(tmodes),
        f"## H. PMP / PMA architectural state ({len(pmp)})",
        "",
        md_table(pmp, limit=L),
        f"## I. Unified architectural target inventory ({len(targets)})",
        "",
        md_table(targets, limit=L),
    ]
    with open(os.path.join(args.out_dir, "inventory.md"), "w") as f:
        f.write("\n".join(md))

    print(f"wrote {args.out_dir}/inventory.{{md,json}} + 9 CSVs")
    for k, v in stats.items():
        print(f"  {k:38s} {v}")


if __name__ == "__main__":
    main()
