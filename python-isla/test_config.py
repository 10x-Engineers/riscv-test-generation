"""What a generated test *requires*, written where a runner can read it.

The RFP asks for tests "organised by ISA extension ... so that tests can be
easily identified and optionally included when testing differing
configurations". Directory layout answers the first half. It cannot answer the
second: a directory name cannot say "this test needs at least one PMP entry" or
"this needs MXLEN 32", and those are exactly the facts a runner needs in order
to decide whether a test applies to the configuration in front of it.

So each test also carries a header block, and each output directory gets a
`tests.json` manifest. The header format is `riscv-arch-test`'s, deliberately:

    ##### START_TEST_CONFIG #####
    # REQUIRED_EXTENSIONS: ['I', 'Zicsr']
    # params:
    #   MXLEN: 32
    # MARCH: rv32i_zicsr
    ##### END_TEST_CONFIG #####

Matching their format means our tests are selectable by the same runner that
selects theirs, which is the ACT4-compatibility consideration in the RFP, rather
than a second parallel convention nobody else consumes.

**Which field is authoritative.** `MARCH` is, because it is the string the
assembler was actually invoked with and therefore the one that provably encodes
these instructions -- it is not reconstructed here. `REQUIRED_EXTENSIONS` is a
best-effort human-readable rendering of the same information; where a march part
has no known spelling it is passed through unchanged rather than guessed at.
"""
import json
import os

# Spelling of extension names as the RFP and riscv-arch-test write them, keyed
# by the lowercase march part. Only entries whose casing is not mechanical are
# listed; anything absent is passed through with its first letter capitalised,
# which is right for the Z* families and harmless elsewhere.
_MARCH_NAMES = {
    "m": "M", "a": "A", "f": "F", "d": "D", "c": "C", "b": "B", "v": "V",
    "h": "H", "q": "Q",
}


def march_string(xlen, march_ext):
    """The -march the assembler is given. Kept in one place because it is the
    field that has to be true: it was validated by the assembler accepting the
    instruction, unlike anything reconstructed after the fact."""
    return f"rv{xlen}i" + (f"_{march_ext}" if march_ext else "")


def required_extensions(march_ext, extra=()):
    """Human-readable extension list for the header. `I` is always present --
    every test's preamble is base-integer code regardless of what it exercises."""
    names = ["I"]
    for part in (march_ext or "").split("_"):
        if not part:
            continue
        names.append(_MARCH_NAMES.get(part, part[:1].upper() + part[1:]))
    for e in extra:
        if e and e not in names:
            names.append(e)
    return names


def header(exts, march, xlen, params=None):
    """The riscv-arch-test config block, as text ending in a newline.

    `params` are configuration requirements beyond the extension set -- the
    things a directory name cannot express. Values are written as given, so a
    constraint like '>0' stays a constraint rather than being flattened to a
    number that would then read as an exact requirement.
    """
    p = {"MXLEN": xlen}
    p.update(params or {})
    lines = ["##### START_TEST_CONFIG #####",
             f"# REQUIRED_EXTENSIONS: {exts}",
             "# params:"]
    lines += [f"#   {k}: {v}" for k, v in p.items()]
    lines += [f"# MARCH: {march}", "##### END_TEST_CONFIG #####", ""]
    return "\n".join(f"{line}" if line.startswith("#") else line for line in lines) + "\n"


def annotate(s_path, block):
    """Prepend the block to a generated `.s`, once.

    `#` is a line comment for the RISC-V assembler, so this is inert to the
    build -- the same reason riscv-arch-test can carry it in the source. Written
    idempotently because sweeps are re-run, and a test accumulating five copies
    of its own header would be a silly way to break the parser that reads it.
    """
    if not os.path.exists(s_path):
        return False
    with open(s_path) as f:
        text = f.read()
    if "START_TEST_CONFIG" in text:
        return False
    with open(s_path, "w") as f:
        f.write(block + text)
    return True


class Manifest:
    """Per-directory index of what was generated and what each test needs.

    The header serves a source-level runner; this serves anything consuming the
    ELFs, which is how our own tooling and the simulators use them. Same facts,
    written twice, because the two consumers cannot read each other's format.
    """

    def __init__(self, extension, xlen, march):
        self.extension = extension
        self.xlen = xlen
        self.march = march
        self.tests = []

    def add(self, name, elf_path, exts, params=None, note=None):
        self.tests.append({
            "name": name,
            "elf": os.path.basename(elf_path),
            "required_extensions": exts,
            "params": dict({"MXLEN": self.xlen}, **(params or {})),
            **({"note": note} if note else {}),
        })

    def write(self, out_dir):
        if not self.tests:
            return None
        path = os.path.join(out_dir, "tests.json")
        with open(path, "w") as f:
            json.dump({"extension": self.extension, "xlen": self.xlen,
                       "march": self.march, "tests": self.tests}, f, indent=1)
        return path
