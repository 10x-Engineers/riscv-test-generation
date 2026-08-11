"""Get instruction mnemonics + encodings straight from the Sail model instead
of riscv-opcodes, so a sweep only ever tests opcodes the model actually
implements.

Two separate jobs, deliberately kept apart:

1. WHICH instructions exist, and what operands they take -- parsed from the
   Sail model's own `mapping clause assembly` definitions (the same
   bidirectional block the model itself uses to print/parse assembly text).
   This is the model's declared instruction set, full stop: if a mnemonic
   isn't defined here, the model doesn't implement it and we don't test it.

2. HOW to turn one of those into an actual opcode word -- done by rendering
   real assembly text and handing it to the real RISC-V assembler, not by
   hand-computing bit positions. This mirrors the principle
   `sailtest/model.py` already established for the oracle backend: "the real
   assembler is the encoding source of truth." It also sidesteps needing to
   parse Sail's separate `encdec` mapping clauses (a much hairier bidirectional
   bit-layout DSL with guards and sub-mappings) just to answer "what bits does
   this instruction have" -- the assembler already knows.
"""

import os
import re
import subprocess
import tempfile

_REG_RE = re.compile(r"^reg_name\(")
# The FP register file's own name mappings (`extensions/FD/fdext_regs.sail`).
# Matched explicitly rather than through the string-table path for two
# reasons: `freg_name` *does* syntactically match the `<-> string` table regex,
# but its body maps through `freg_abi_name_raw(i)` rather than quoted literals,
# so it parses as a table with *zero* choices -- which would silently produce
# zero mnemonics for every F/D clause. And `freg_or_reg_name` is a
# forwards/backwards mapping (it renders x-registers instead under Zfinx),
# which the table regex doesn't match at all. Either way the right answer is
# "this is an operand position holding an f-register", which is what this says.
_FREG_RE = re.compile(r"^(freg_name|freg_or_reg_name)\(")
# The compressed (C-extension) register operands, which address only x8-x15 /
# f8-f15 -- a 3-bit field, not a 5-bit one. Rendering these as x1/f1 would
# assemble to nothing at all ("illegal operands"), so they need their own kind
# rather than folding into "reg"/"freg".
_CREG_RE = re.compile(r"^creg_name\(")
_CFREG_RE = re.compile(r"^cfreg_name\(")
# The vector register file (v0-v31), `extensions/V/vext_regs.sail`. Same
# reasoning as _FREG_RE: a third register file with its own operand syntax.
_VREG_RE = re.compile(r"^vreg_name\(")
_HEX_SIGNED_RE = re.compile(r"^hex_bits_signed_(\d+)\(")
_HEX_RE = re.compile(r"^hex_bits_(\d+)\(")
# `dec_bits_N(...)` renders a decimal number, and in every clause that uses it
# the number is part of the *mnemonic*, not an operand: Zimop's `mop.r.` ^
# dec_bits_5(mop) spells `mop.r.0` ... `mop.r.31`, Zcmop's spells `c.mop.1`,
# `c.mop.3`, ... A single representative value is rendered, the same way every
# other operand kind here renders one legal instance rather than enumerating.
_DEC_RE = re.compile(r"^dec_bits_(\d+)\(")

# Union clauses whose `imm` operand is a branch/jump *target*, not a plain
# value. Rendered as a real local-label distance (see render_asm) rather than
# a literal number: a bare literal offset with no linker present either gets
# rejected outright or -- worse -- silently resolves to a self-loop (offset
# 0), the same false-pass trap the old riscv-opcodes-driven version had to
# work around by hand-computing B-type/J-type displacements.
# The compressed forms are here too and matter just as much: `c.j`, `c.jal`,
# `c.beqz`, `c.bnez` all take a target, and rendering one as the literal `4`
# makes GNU as emit a jump to *absolute address 4* -- which then relaxes to a
# full-width `jal` and fails at runtime, having tested nothing.
BRANCH_LIKE = {"BTYPE", "JAL", "C_J", "C_JAL", "C_BEQZ", "C_BNEZ"}

# Union clauses whose memory operand addresses an *instruction fetch* rather
# than data, and so must not be redirected into the data region: JALR's
# `imm(reg)` is a jump target, already constrained to the code region.
#
# Stated as an exclusion rather than the enumerated allow-list this used to be.
# The allow-list held four unions -- LOAD/STORE plus their FP counterparts --
# and silently failed open for the two dozen others the parser now reaches
# (every compressed load/store, A's `amo*`/`lr`/`sc`, V's segment loads and
# stores, the Zicbo* cache ops): each of those got a *symbolic* base address
# instead of one pinned to the declared --memory-region, which isla is free to
# solve to any legal address at all, MMIO included. An exclusion list fails the
# safe way -- a newly-parsed extension gets the safe address by default.
CODE_MEM_UNIONS = {"JALR"}

SAFE_DATA_ADDR = 0x80020000  # must match opcode_sweep.py's --memory-region

# mscratch -- a plain-R/W, Machine-only CSR with no side effects, safe as a
# stand-in target for any CSRRW/CSRRS/CSRRC/CSRRWI/CSRRSI/CSRRCI instance.
CSR_ADDR = "0x340"

# union_name -> immediate to render, where the default (a small positive value)
# isn't legal for that instruction. The encoding constrains these beyond the
# field width, and the model states the constraint in its `encdec` clause
# rather than its `assembly` one, so it isn't visible to this parser: GNU as
# rejects `c.addi16sp sp, 4` outright because the immediate must be a nonzero
# multiple of 16.
# Vector memory instructions that take a *stride* in a plain register operand
# (`vlse32.v vd, (rs1), rs2`). The stride decides which addresses are touched,
# so leaving it unconstrained lets isla solve it to anything and the access
# walks straight out of the declared --memory-region. Confirmed: the same
# instruction fails with the stride register untouched and passes with a small
# constant loaded into it first.
STRIDED_UNIONS = {"VLSSEGTYPE", "VSSSEGTYPE", "VLSTYPE", "VSSTYPE"}

# Vector-crypto instructions defined over 256-bit *element groups* (EGW=256):
# SM3, SM4, AES, SHA-2, GHASH. They require LMUL * VLEN >= 256, which LMUL=1
# only satisfies on an implementation with VLEN >= 256.
#
# That difference is real and shows up as a simulator disagreement: Sail's
# default config is `vlen_exp: 8` (VLEN=256) and accepts them at LMUL=1, while
# this Spike build's VLEN is fixed at compile time and smaller, so it raises
# an illegal instruction -- correctly. LMUL=2 satisfies the constraint on
# both, which turns an apparent divergence back into a passing test on each.
EGW256_PREFIXES = ("vsm3", "vsm4", "vaes", "vsha2", "vghsh", "vgmul")

# Zvbc's carry-less multiplies are defined for 64-bit elements only.
SEW64_PREFIXES = ("vclmul",)

# The element width a vector memory mnemonic encodes -- `vlseg5e64.v` -> 64,
# `vle32.v` -> 32. Trailing `ff` (fault-only-first) and the `.v` suffix are
# already stripped by the time this matches.
_VMEM_EEW = re.compile(r"e(8|16|32|64)(ff)?\.v$")

IMM_OVERRIDE = {
    "C_ADDI16SPN": 16,
    "C_ADDI16SP": 16,
}


class Insn:
    def __init__(self, union_name, mnemonic, operand_kinds, xlen_guard=None):
        self.union_name = union_name
        self.mnemonic = mnemonic
        self.operand_kinds = operand_kinds  # [("reg",) | ("imm", bits, signed) | ("mem", bits, signed)]
        # 32/64 if the clause carries a `when xlen == N` guard, else None --
        # the model's own statement that this mnemonic only exists at that
        # XLEN (RV64's `mulw`/`divw`/`addw`/`ld`/`sd`, ...). Sweeping at the
        # other XLEN isn't a failure, it's out of scope; see opcode_sweep.py.
        self.xlen_guard = xlen_guard


# --- parsing the Sail source ---------------------------------------------

def _parse_string_tables(text):
    """name -> [possible strings] for every `mapping NAME : T <-> string = {...}`
    block, regardless of whether it's a per-op mnemonic table (itype_mnemonic)
    or a smaller helper table (width_mnemonic, maybe_u, ...) -- both look the
    same to the model's own assembly clauses, so we parse both the same way."""
    tables = {}
    # The mapped-from type is `[^<]+?`, not `\w+`: A's `maybe_aqrl` maps from
    # a *tuple* type, `(bool, bool) <-> string`, and a `\w+` type pattern
    # misses it -- which silently cost the entire A extension, since every
    # lr/sc/amo clause resolves its `.aq`/`.rl` suffix through that table.
    for m in re.finditer(
            r"mapping\s+(\w+)\s*:\s*[^<]+?\s*<->\s*string\s*=\s*\{(.*?)\n\}", text, re.S):
        name, body = m.group(1), m.group(2)
        literals = re.findall(r'<->\s*"([^"]*)"', body)
        # A table is only usable if *every* row maps to a quoted literal. Some
        # don't: `fence_bits` has one literal row ("0") and one that computes
        # its text from sub-mappings (`bit_maybe_i(i) ^ ...`), and `freg_name`
        # computes all of its. Keeping just the literal rows would silently
        # narrow the operand space to an unrepresentative corner -- for
        # `fence_bits` specifically, to the single spelling the model's own
        # comment says GNU as rejects. None means "can't model this", which
        # makes the clause a *reported* skip rather than a confidently-wrong
        # render.
        tables[name] = literals if len(literals) == body.count("<->") else None
    return tables


_GUARD_RE = re.compile(r"\bwhen\s+(.+)$", re.S)
_XLEN_GUARD_RE = re.compile(r"\bxlen\s*==\s*(\d+)")


def _split_xlen_guard(template):
    """(template_without_guard, 32|64|None). A clause may end in a `when ...`
    guard. Only two shapes of guard occur across the whole model, and they
    need opposite treatment:

    * `when xlen == 64` -- the model declaring that this mnemonic exists only
      at that XLEN (M's `mulw`/`divw`, I's `addw`/`ld`/`sd`). Returned, so the
      sweep can skip rather than report a bogus failure at the other XLEN.
    * operand-value constraints -- `rd != zreg`, `imm != zeros()`,
      `rd != sp & imm != 0b0`, and so on. Every one of these is already
      satisfied by how render_asm picks operands (it never renders x0, never
      renders sp as a chosen register, and always renders a nonzero
      immediate), so they need no handling beyond being stripped.

    Stripping is required either way: left attached, the guard text runs into
    the clause's last fragment and stops it matching (this is what hid C's
    `c.lwsp`/`c.ldsp`, whose `sp_reg_name()` ended up as
    `sp_reg_name()\\n  when rd != zreg`)."""
    stripped = template.strip()
    m = _GUARD_RE.search(stripped)
    if not m:
        return template, None
    xm = _XLEN_GUARD_RE.search(m.group(1))
    return stripped[:m.start()], int(xm.group(1)) if xm else None


def _parse_assembly_clauses(text):
    """Yield (union_name, template) for each `mapping clause assembly`, in
    both the standard bidirectional `= NAME(...) <-> template` form and the
    one-directional `= forwards NAME(...) => template` form.

    The forwards form is not exotic and skipping it is not free: `fence` and
    `fence.i` are written that way (the model's own comment says why -- "no
    way to write a zero regidx as a literal" -- so their zero-register
    operands are fixed in the pattern rather than passed through), and
    `fence.i` is the *entire* Zifencei extension. Instruction->text is the
    only direction this module needs, so a forwards clause carries everything
    required; the matching `backwards` clauses are text->instruction and stay
    unmatched, which is correct rather than a gap."""
    clause_re = re.compile(
        r"mapping clause assembly\s*=\s*(?:forwards\s+)?(\w+)\s*\([^)]*\)\s*(?:<->|=>)\s*"
        # `\Z` (end of string) alongside the usual next-construct lookaheads:
        # a file whose *last* declaration is an assembly clause (no trailing
        # blank line or following construct after it) would otherwise never
        # find a terminator and silently lose that clause -- hit for real by
        # SFENCE_VMA (last thing in base_insts.sail) and CSRReg (last thing
        # in zicsr_insts.sail).
        r"(.+?)(?=\nmapping |\nfunction |\nunion |\nval |\n\$\[|\n\n|\Z)", re.S)
    for m in clause_re.finditer(text):
        yield m.group(1), m.group(2)


def _eval_template(template, tables):
    """Split a `^`-joined assembly template into ordered fragments:
    ("str", [possible strings]) | ("reg", None) | ("imm", bits, signed) |
    ("csr", None) | ("spc",) | ("sep",). Returns None if the template uses a
    construct we don't model (an operand kind, or a bare identifier that isn't
    a known string table).

    `spc()` and `sep()` are kept as fragments rather than dropped, because
    they *are* the structure: `spc()` is the one boundary between mnemonic and
    operands, `sep()` the boundary between operands. Dropping them (as this
    did originally) leaves the mnemonic's extent to be guessed from "leading
    run of string fragments", which is wrong whenever an operand is itself a
    string -- Zicbom's `cbo.clean (rs1)` had its `"("` swallowed into the
    mnemonic, and `fence`'s two `fence_bits` operands were indistinguishable
    from mnemonic suffixes. `opt_spc()` is genuinely optional whitespace, not
    a boundary, and is still dropped: it appears *inside* the `imm(reg)`
    address shape, where treating it as a separator would break the match."""
    frags = []
    for part in (p.strip() for p in template.split("^")):
        if part in ("opt_spc()", ""):
            continue
        if part == "spc()":
            frags.append(("spc",))
            continue
        if part == "sep()":
            frags.append(("sep",))
            continue
        if part.startswith('"') and part.endswith('"'):
            frags.append(("str", [part[1:-1]]))
        elif _REG_RE.match(part):
            frags.append(("reg", None))
        elif _FREG_RE.match(part):
            frags.append(("freg", None))
        elif _CREG_RE.match(part):
            frags.append(("creg", None))
        elif _CFREG_RE.match(part):
            frags.append(("cfreg", None))
        elif _VREG_RE.match(part):
            frags.append(("vreg", None))
        elif part == "sp_reg_name()":
            # C's stack-pointer-relative forms (`c.lwsp`, `c.swsp`, ...) name
            # the base register in the mapping rather than taking it as an
            # operand -- it's always x2. A fixed literal, not a register kind.
            frags.append(("str", ["sp"]))
        elif _HEX_SIGNED_RE.match(part):
            frags.append(("imm", int(_HEX_SIGNED_RE.match(part).group(1)), True))
        elif _HEX_RE.match(part):
            frags.append(("imm", int(_HEX_RE.match(part).group(1)), False))
        elif _DEC_RE.match(part):
            # "1" specifically, not "0": Zcmop's value is `mop @ 0b1`, so its
            # low bit is always set and `c.mop.0` doesn't exist. 1 is a legal
            # value for every clause using this, which the assembler confirms.
            frags.append(("str", ["1"]))
        else:
            head = part.split("(", 1)[0]
            if head == "csr_name_map":
                # `csreg <-> string` for CSR mnemonics (Zicsr's CSRReg/CSRImm)
                # is declared `scattered mapping` in core/csr_begin.sail --
                # one `mapping clause csr_name_map` per CSR, scattered across
                # every CSR's own file, not a single contained `mapping NAME
                # : T <-> string = {...}` block. _parse_string_tables can't
                # (and doesn't try to) collect those, so this can never be a
                # hit in `tables`. Special-cased instead of parsed: we only
                # need *an* operand-position marker here, not the real name
                # table, because GNU as accepts a raw numeric CSR address in
                # place of the symbolic name (confirmed: `csrrw x1, 0x340,
                # x2` assembles identically to `csrrw x1, mscratch, x2`) --
                # see render_asm's CSR_ADDR choice.
                frags.append(("csr", None))
            elif head == "maybe_vmask":
                # V's optional mask suffix. Declared *reversed*
                # (`string <-> bits(1)`, extensions/V/vext_utils_insts.sail),
                # so the table parser doesn't see it, and its masked row is
                # `sep() ^ "v0.t"` rather than a plain literal anyway. The
                # unmasked spelling is the empty string, i.e. nothing at all,
                # which is exactly the one legal instance to render -- masked
                # operation is a distinct behaviour to test deliberately, not
                # something to switch on by accident in a decode sweep. So
                # this contributes no fragment rather than an empty one.
                continue
            elif head == "vtype_assembly":
                # `vsetvli`'s vtype operand. Its own mapping composes five
                # sub-mappings under a `when` guard, so it can't be
                # enumerated from literals. `e8, m1, ta, ma` is the canonical
                # minimal legal setting (SEW=8, LMUL=1, tail- and
                # mask-agnostic) and satisfies that guard (sew[2] != 1,
                # lmul != 0b100, vtr == 0).
                frags.append(("str", ["e8, m1, ta, ma"]))
            elif head == "fence_bits":
                # `fence`'s predecessor/successor sets. The table itself can't
                # be enumerated (one row computes its text from four
                # sub-mappings -- see _parse_string_tables), and its one
                # literal row, "0", is precisely the spelling the model's own
                # comment says GNU as rejects. "iorw" -- the full set, and the
                # spelling every real toolchain accepts -- is rendered
                # instead, so base I's `fence` is actually covered rather than
                # reported as unmodelable.
                frags.append(("str", ["iorw"]))
            elif tables.get(head):
                frags.append(("str", tables[head]))
            else:
                # Either not a known table at all, or a known one we can't
                # fully enumerate (tables[head] is None -- see
                # _parse_string_tables). Both mean "don't guess": the clause
                # is skipped and reported.
                return None
    return frags


def _build_variants(union_name, frags):
    """(mnemonic, operand_kinds) for every concrete mnemonic a clause covers --
    more than one when the clause is parameterised over a mnemonic table
    (ITYPE's `op`, RTYPE's `op`, ...)."""
    # The mnemonic is everything up to the first `spc()` -- the model's own
    # mnemonic/operand boundary -- not "the leading run of string fragments".
    # An operand-less instruction (`ecall`, `fence.i`, `c.nop`) has no `spc()`
    # at all, in which case the whole clause is the mnemonic.
    i = frags.index(("spc",)) if ("spc",) in frags else len(frags)
    mnem_choices = [f[1] for f in frags[:i]]
    if not mnem_choices or any(f[0] != "str" for f in frags[:i]):
        return None
    # Separator markers have done their job once the mnemonic is delimited:
    # the operand shapes below are recognised positionally, and a literal `,`
    # between them carries no further information. Any *further* `spc()` is
    # dropped alongside `sep()` -- bfloat16's `vfwmaccbf16.vv` separates two
    # of its operands with `spc()` where every sibling clause uses `sep()`,
    # and treating that as structural rather than cosmetic would lose the
    # clause over a typo in the model.
    rest = [f for f in frags[i + 1:] if f[0] not in ("sep", "spc")]

    def _paren_base(k):
        """(consumed, base_kind) if `rest[k:]` starts with a parenthesised base
        register -- `"(" ^ reg_name(rs1) ^ ")"`, or its `sp` / compressed-
        register variants. Returns (0, None) otherwise."""
        if (k + 2 < len(rest) and rest[k] == ("str", ["("])
                and rest[k + 2] == ("str", [")"])):
            inner = rest[k + 1]
            if inner[0] in ("reg", "creg"):
                return 3, inner[0]
            if inner == ("str", ["sp"]):
                return 3, "sp"
        return 0, None

    operand_kinds = []
    j = 0
    while j < len(rest):
        kind = rest[j]
        consumed, base = _paren_base(j + 1) if kind[0] == "imm" else (0, None)
        if consumed:
            operand_kinds.append(("mem", kind[1], kind[2], base))
            j += 1 + consumed
            continue
        consumed, base = _paren_base(j)
        if consumed:
            # A bare `(reg)` with no offset at all: A's `lr`/`sc`/`amo*` all
            # address memory that way (`amoadd.w rd, rs2, (rs1)`), which the
            # offset-first pattern above doesn't match. Same rendering, with
            # the offset simply absent rather than zero -- GNU as rejects
            # `amoadd.w a0, a1, 0(a2)` outright, so it can't just be
            # normalised into the other shape.
            operand_kinds.append(("mem", None, None, base))
            j += consumed
            continue
        if kind[0] == "reg":
            operand_kinds.append(("reg",))
            j += 1
        elif kind[0] in ("freg", "creg", "cfreg", "vreg"):
            operand_kinds.append((kind[0],))
            j += 1
        elif kind[0] == "imm":
            operand_kinds.append(("imm", kind[1], kind[2]))
            j += 1
        elif kind[0] == "csr":
            operand_kinds.append(("csr",))
            j += 1
        elif kind[0] == "str" and kind[1] and kind[1][0][:1].isalnum():
            # A string table in *operand* position rather than mnemonic
            # position -- F/D's `frm_mnemonic(rm)` rounding mode ("rne",
            # "rtz", ...), which the assembler takes as a real trailing
            # operand. One representative choice is rendered, same as every
            # other operand kind here renders one legal instance rather than
            # enumerating the space. Punctuation-only tables deliberately
            # don't land here: an unmatched "("/")" means the surrounding
            # `imm(reg)` shape wasn't recognised, and quietly rendering it as
            # a literal operand would produce a *plausible but wrong*
            # instruction instead of a visible skip.
            operand_kinds.append(("lit", kind[1][0]))
            j += 1
        else:
            return None  # a stray literal in the operand stream -- not modeled

    mnemonics = [""]
    for choices in mnem_choices:
        mnemonics = [m + c for m in mnemonics for c in choices]
    return [(m, operand_kinds) for m in mnemonics if m]


def parse_instructions(sail_paths, table_paths=()):
    """(mnemonic -> Insn, [(union_name, reason) skipped]), parsed from the
    given `.sail` file paths.

    `table_paths` are parsed for `<-> string` mnemonic tables only, never for
    instructions. They're needed because a clause's mnemonic table doesn't
    have to live in the same file as the clause: M's `div`/`divu`/`rem`/`remu`
    all resolve their `maybe_u(is_unsigned)` fragment against a table declared
    in `extensions/I/base_insts.sail`. Without those extra files, `maybe_u`
    isn't a known table, `_eval_template` gives up, and 4 of M's 6 mnemonics
    vanish from the sweep with no error at all -- which is exactly the failure
    mode this whole model-sourced approach exists to prevent, so the skips are
    now returned rather than swallowed. Callers should report them."""
    tables = {}
    texts = []
    for path in list(table_paths) + list(sail_paths):
        with open(path) as f:
            t = f.read()
        if path in sail_paths:
            texts.append(t)
        tables.update(_parse_string_tables(t))

    insns = {}
    skipped = []
    for t in texts:
        for union_name, template in _parse_assembly_clauses(t):
            template, xlen_guard = _split_xlen_guard(template)
            frags = _eval_template(template, tables)
            if frags is None:
                skipped.append((union_name, "unmodeled template fragment "
                                            "(operand kind, or a mnemonic table not in the parsed files)"))
                continue
            variants = _build_variants(union_name, frags)
            if variants is None:
                skipped.append((union_name, "unmodeled operand layout"))
                continue
            for mnemonic, operand_kinds in variants:
                insns[mnemonic] = Insn(union_name, mnemonic, operand_kinds, xlen_guard)
    return insns, skipped


# --- rendering + assembling -----------------------------------------------

# Operand kinds that a clause may declare but the assembler may reject for a
# particular mnemonic. See opcodes_for's retry.
OPTIONAL_KINDS = ("lit",)


def render_asm(insn, xlen=32, csr_addr=CSR_ADDR, drop_kinds=()):
    """Return real assembly text (a full .s body) for one instance of `insn`,
    with registers/immediates chosen the same way opcode_sweep.py's old
    build_opcode did: every register nonzero (checkable state, and x0 is
    hardwired so a register operand must be real to have any effect), data
    addresses pre-loaded via `lui` so an isla-solved base register lands in
    our declared --memory-region instead of a fully free (and isla-legal!)
    MMIO address, and branch/jump targets a real local-label distance instead
    of a literal 0 (which would either be rejected or silently self-loop)."""
    reg_counter = [1]
    freg_counter = [1]

    def next_reg():
        r = "x%d" % reg_counter[0]
        reg_counter[0] += 1
        return r

    def next_freg():
        # A separate counter and a separate register file: f0 is a perfectly
        # ordinary FP register (unlike x0, which is hardwired to zero), so
        # there's no reason to skip it -- but starting at f1 keeps the two
        # numbering schemes visually parallel in generated assembly, which
        # matters when reading a failing test by hand.
        r = "f%d" % freg_counter[0]
        freg_counter[0] += 1
        return r

    # The C extension's 3-bit register fields reach only x8-x15 / f8-f15.
    creg_counter = [8]
    cfreg_counter = [8]

    def next_creg():
        r = "x%d" % (8 + (creg_counter[0] - 8) % 8)
        creg_counter[0] += 1
        return r

    def next_cfreg():
        r = "f%d" % (8 + (cfreg_counter[0] - 8) % 8)
        cfreg_counter[0] += 1
        return r

    # v8, v16, v24 -- not v1, v2, v3. Two separate constraints force this:
    #
    #  * v0 is *the* mask register, so a masked instruction naming it as a data
    #    operand overlaps its own mask, which several vector instructions make
    #    architecturally illegal.
    #  * A widening, narrowing or segment instruction addresses a register
    #    *group* of EMUL registers, and the group must be aligned to its own
    #    size. `vwadd.vv v1, v2, v3` widens into a 2-register destination
    #    group starting at an odd register, which is illegal -- confirmed
    #    directly: that instruction fails on Sail while `vwadd.vv v8, v16, v24`
    #    passes. This accounted for the large majority of the V sweep's
    #    remaining failures (every `vw*`/`vn*` and every `v*seg*`).
    #
    # 8-aligned and 8 apart is the only spacing that satisfies both for every
    # legal EMUL (up to 8) without operands overlapping. That leaves exactly
    # three usable registers, which covers vd/vs1/vs2; anything needing a
    # fourth falls back to sequential numbering rather than silently colliding.
    VREG_SLOTS = ["v8", "v16", "v24"]
    vreg_counter = [0]

    def next_vreg():
        i = vreg_counter[0]
        vreg_counter[0] += 1
        return VREG_SLOTS[i] if i < len(VREG_SLOTS) else "v%d" % (1 + i - len(VREG_SLOTS))

    prelude = []
    parts = []
    trailer = ""
    operand_kinds = [k for k in insn.operand_kinds if k[0] not in drop_kinds]

    if any(k[0] == "vreg" for k in insn.operand_kinds):
        # `vtype` resets with its `vill` (illegal-configuration) bit set, so a
        # vector instruction traps as illegal until some `vset*` has run --
        # separately from, and in addition to, mstatus.VS being enabled (see
        # isla-testgen's --enable-vector). Confirmed by tracing: with VS on,
        # `vadd.vv v1, v2, v3` disassembles and *starts*, then still traps.
        # `rs1 = x0` with a nonzero `rd` is the standard "set vl to VLMAX"
        # form, so the whole vector register group is live for the
        # instruction under test rather than a zero-length prefix of it.
        # `vset*` instructions themselves have no vector register operands and
        # so correctly don't get this.
        # SEW=32, not 8. A vector floating-point instruction requires SEW to
        # be a supported FP element width (16/32/64) -- at SEW=8 every one of
        # them raises an illegal instruction, which accounted for the large
        # majority of the V sweep's failures on *both* simulators. 32 is the
        # narrowest width that is legal for FP, still legal for every integer
        # operation, and still leaves room for widening ops to reach ELEN=64.
        # SEW is matched to the element width the mnemonic itself encodes,
        # where it has one. Pinning it at 32 breaks the segment instructions:
        # EMUL scales as EEW/SEW, and a segment instruction requires
        # `nf * EMUL <= 8`, so `vlseg5e64.v` under SEW=32 needs 5 * 2 = 10
        # registers and is illegal. At SEW=64 it needs 5 and is fine.
        # Confirmed both ways on Sail. For everything else 32 stays the
        # default -- it is the narrowest width legal for floating point.
        m = _VMEM_EEW.search(insn.mnemonic)
        if m:
            sew = m.group(1)
        elif insn.mnemonic.startswith(SEW64_PREFIXES) or insn.mnemonic.endswith(".vf8"):
            # `.vf8` extends each element from SEW/8 bits. At SEW=32 that
            # source width is 4 bits, which is not a legal element width, so
            # `vsext.vf8`/`vzext.vf8` are illegal there. SEW=64 gives an 8-bit
            # source. (`.vf2` and `.vf4` are fine at 32 -- 16- and 8-bit
            # sources -- so only the vf8 pair needs this.)
            sew = "64"
        elif "bf16" in insn.mnemonic:
            # BF16 is a 16-bit format, so every vector bf16 instruction needs
            # SEW=16 -- the widening ones (`vfwcvtbf16`, `vfwmaccbf16`) take
            # bf16 *inputs* and produce f32, so it is the input width that SEW
            # must match, and the narrowing `vfncvtbf16` is the mirror. All
            # four failed at SEW=32 and pass at 16; the two *scalar* bf16
            # conversions were unaffected throughout, which is what pointed at
            # SEW rather than at bf16 support.
            sew = "16"
        else:
            sew = "32"
        lmul = "m2" if insn.mnemonic.startswith(EGW256_PREFIXES) else "m1"
        prelude.append("vsetvli %s, x0, e%s, %s, ta, ma" % (next_reg(), sew, lmul))

    for kind in operand_kinds:
        if kind[0] == "reg":
            r = next_reg()
            if insn.union_name in STRIDED_UNIONS and parts:
                # The stride is the trailing register operand of a strided
                # vector access (the leading operands are the destination and
                # the base). A small, element-sized constant keeps every
                # generated address inside the declared memory region.
                prelude.append("li %s, 8" % r)
            parts.append(r)
        elif kind[0] == "freg":
            parts.append(next_freg())
        elif kind[0] == "creg":
            parts.append(next_creg())
        elif kind[0] == "cfreg":
            parts.append(next_cfreg())
        elif kind[0] == "vreg":
            parts.append(next_vreg())
        elif kind[0] == "lit":
            parts.append(kind[1])
        elif kind[0] == "imm":
            if insn.union_name in BRANCH_LIKE:
                parts.append(".Ltarget")
                trailer = "nop\n.Ltarget:\n"
            else:
                bits, signed = kind[1], kind[2]
                cap = (1 << (bits - 1)) - 1 if signed else (1 << bits) - 1
                parts.append(str(IMM_OVERRIDE.get(insn.union_name, min(4, max(cap, 1)))))
        elif kind[0] == "mem":
            base = kind[3]
            # `sp` is named by the mapping itself (C's *sp forms), so there's
            # no free register to choose -- and no way to `lui` it to a safe
            # address either without clobbering the harness's own stack
            # pointer, which is why those forms don't get the prelude below.
            base_reg = {"reg": next_reg, "creg": next_creg,
                        "sp": lambda: "sp"}[base]()
            if base == "sp":
                # C's *sp forms name x2 in the mapping rather than taking a
                # base operand, and nothing has put a usable address in it:
                # the harness never sets up a stack, and isla is free to solve
                # x2 to any legal address at all. Load it explicitly, the same
                # way a chosen base register gets loaded below.
                prelude.append("li sp, %#x" % SAFE_DATA_ADDR)
            elif insn.union_name not in CODE_MEM_UNIONS:
                prelude.append("lui %s, %d" % (base_reg, SAFE_DATA_ADDR >> 12))
                # RV64-only: `lui`'s result is sign-extended to 64 bits, and
                # SAFE_DATA_ADDR (0x80020000) has bit 31 set, so on RV64 this
                # `lui` alone produces 0xffffffff80020000, not 0x80020000 --
                # sending the load/store outside the declared --memory-region
                # entirely. clear the sign-extended upper bits back to zero.
                # (RV32 has no upper bits to extend into, so this is a no-op
                # there and is skipped rather than emitting dead instructions.)
                if xlen == 64:
                    prelude.append("slli %s, %s, 32" % (base_reg, base_reg))
                    prelude.append("srli %s, %s, 32" % (base_reg, base_reg))
            # Offset 0, not a nonzero constant: SAFE_DATA_ADDR (0x80020000) is
            # far more than 8-byte aligned, so base+0 stays naturally aligned
            # for every access width up through RV64's 8-byte ld/sd. A
            # nonzero offset (this used to be a fixed +4) can misalign a
            # wider access -- e.g. +4 is fine for lw (needs 4-byte alignment)
            # but not for ld (needs 8-byte) -- which sends the model down its
            # misaligned-access-splitting path (split_misaligned, sys/
            # split_access_utils.sail). That path computes
            # count_trailing_zeros on the (still-symbolic, at this point in
            # solving) base address, and that symbolic bit-count then flows
            # into a zeros() call as a non-concrete length, which isla-lib's
            # zeros() primop can't handle -- "Symbolic (bit)vector length in
            # zeros". Confirmed via `--all-events` tracing on the ld/sd
            # failure: this is a bug in what address we hand the instruction,
            # not a model or isla-lib limitation. jalr's target-address mem
            # operand shares this rendering path and offset 0 is equally
            # valid there (jump to base+0).
            #
            # kind[1] is None for the offset-less `(reg)` shape (A's
            # lr/sc/amo*), where GNU as rejects an explicit `0(reg)` outright
            # rather than accepting it as an equivalent spelling.
            parts.append(("0(%s)" if kind[1] is not None else "(%s)") % base_reg)
        elif kind[0] == "csr":
            # A fixed, real CSR address rather than the symbolic name (see
            # _eval_template's csr_name_map note for why we don't parse the
            # real name table). Defaults to mscratch (0x340: plain read/
            # write, Machine-only, no side effects) but overridable per call
            # -- see opcode_sweep.py's --csr, used to widen coverage across
            # real named CSRs (PMP, Smstateen, Sstc, counters, ...) instead
            # of only ever exercising the same one.
            parts.append(csr_addr)

    body = ("%s %s" % (insn.mnemonic, ", ".join(parts))) if parts else insn.mnemonic
    # `.option norvc` around the prelude, deliberately, even where the sweep's
    # own -march enables C.
    #
    # isla-testgen loses the register effect of a *compressed* instruction that
    # isn't the last one in a multi-opcode sequence. Minimal reproducer:
    # `addi x9,x0,5` (32-bit) then `c.addi x9,1` then `c.slli x9,4` leaves
    # isla's recorded final x9 at 5, not 0x60 -- while PC does advance the full
    # 4+2+2 bytes, so placement and instruction widths are right and only the
    # state effect is dropped. The generated test then carries expected values
    # that no correct hardware produces, and "fails" on every simulator.
    #
    # The prelude only exists to set up an address or vtype, so nothing is lost
    # by encoding it full-width, and this keeps the compressed instruction
    # under test in the one position isla handles correctly -- last. Without
    # it, every compressed load/store fails, because their preludes get
    # compressed by the assembler as a matter of course.
    lines = [".text", ".option push", ".option norvc"] + prelude + [".option pop", body]
    if trailer:
        lines.append(trailer.rstrip("\n"))
    return "\n".join(lines) + "\n"


_OBJDUMP_LINE_RE = re.compile(r"^\s*[0-9a-f]+:\s+([0-9a-f]+)\s")


def opcodes_for(insn, xlen, as_bin="as", objdump_bin="objdump", timeout=15, march_ext="", csr_addr=CSR_ADDR):
    """Assemble one instance of `insn` and return (opcode_word_list, error).

    The returned list is in program order and includes any `lui` prelude the
    lui-address-fixup added -- exactly the sequence isla-testgen should be
    given, in the order isla-testgen should execute it.

    `march_ext`, if given, is appended to the base `rv{xlen}i` march string
    (e.g. "zicsr", "zicsr_svinval") -- needed for any instruction outside
    the bare-I encoding space (CSR access, Svinval, ...); the assembler
    rejects an unrecognized-extension mnemonic outright rather than silently
    misencoding it, so an instruction whose union isn't in plain I always
    needs its extension named here explicitly. `csr_addr` -- see
    render_asm's."""
    src = render_asm(insn, xlen, csr_addr=csr_addr)
    march = "rv%di" % xlen + ("_" + march_ext if march_ext else "")
    with tempfile.TemporaryDirectory() as d:
        s_path = os.path.join(d, "t.s")
        o_path = os.path.join(d, "t.o")
        with open(s_path, "w") as f:
            f.write(src)
        try:
            proc = subprocess.run(
                [as_bin, "-march=%s" % march, "-mno-relax", "-o", o_path, s_path],
                capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return None, "TIMEOUT (assemble)"
        if proc.returncode != 0:
            err = (proc.stderr or "assemble failed").strip().splitlines()
            msg = err[-1] if err else "assemble failed"
            # One retry without the clause's optional trailing literal operand.
            # F/D's assembly clauses emit a rounding mode uniformly --
            # `frm_mnemonic(rm)` -- but GNU as rejects it on the conversions
            # where rounding cannot occur ("illegal operands `fcvt.d.s
            # f1,f2,rne\'"): widening float conversions and integer-to-double
            # are exact, so the spec gives their rm field no meaning. The
            # assembler is this module's encoding source of truth (see the
            # module docstring), so the right response is to take its answer
            # and re-render, not to hand-maintain a list of which mnemonics
            # the model over-declares.
            if "illegal operands" in msg and any(
                    k[0] in OPTIONAL_KINDS for k in insn.operand_kinds):
                retry = render_asm(insn, xlen, csr_addr=csr_addr, drop_kinds=OPTIONAL_KINDS)
                if retry != src:
                    with open(s_path, "w") as f:
                        f.write(retry)
                    proc = subprocess.run(
                        [as_bin, "-march=%s" % march, "-mno-relax", "-o", o_path, s_path],
                        capture_output=True, text=True, timeout=timeout)
            if proc.returncode != 0:
                return None, msg

        dump = subprocess.run([objdump_bin, "-d", o_path],
                               capture_output=True, text=True, timeout=timeout)
        opcodes = [int(m.group(1), 16) for m in
                   (_OBJDUMP_LINE_RE.match(line) for line in dump.stdout.splitlines())
                   if m]
        if not opcodes:
            return None, "objdump produced no instructions"
        return opcodes, None
