"""One Golden Model configuration, parsed once, deriving everything downstream.

Before this, configuration was re-derived independently in four places -- the
model config, the symbolic IR, the assembler's `-march`, and the simulator's ISA
string -- so a test could pass on the Golden Model and fail on an independent
simulator purely because the framework disagreed with itself. That failure is
indistinguishable from a model defect, which is the most expensive false signal a
verification framework can emit.

Two facts shape the design, and both are properties of the tooling rather than
choices:

**XLEN is a runtime selector.** It picks which prebuilt IR and which cross
toolchain to use. Passing `--xlen` around was always adequate for it.

**VLEN is not.** `generate_object_riscv.rs` states it plainly: VLEN is a
build-time property of the model IR, because the model declares
`type vlenbits = bits(vlen)` and isla-sail fixes that width when it compiles the
model against a platform config. There is no runtime flag, and no SMT constraint,
that changes a compiled IR's vector width. A configuration asking for VLEN=256
cannot be served by an IR built at 128 -- it can only be served by rebuilding the
IR, which takes minutes and is a separate operation.

The consequence for this module is the important one: when a configuration and
the available IR disagree on VLEN, the only safe behaviours are to rebuild or to
refuse. Generating anyway would produce tests whose vector expectations are
silently wrong -- they would assemble, run, and pass, while testing a machine
nobody asked for. `isla_ir()` therefore raises rather than falling back.
"""
import hashlib
import json
import os
import re

import paths

# isla-sail bakes XLEN and VLEN into the IR and writes nothing alongside it
# saying so. Rather than keep a manifest beside the IR -- which is one more thing
# that can go stale, and did: TESTING_GUIDE.md still documents rebuilding at
# VLEN=64 while both checked-in IRs are VLEN=128 -- the widths are read back out
# of the IR itself. The IR is Sail-mangled text, and the register declarations
# carry the compiled widths directly:
#
#     register zPC : %bv64        <- XLEN
#     register zvr0 : %bv128      <- VLEN
#
# so introspection cannot disagree with the artefact it describes.
_PC_RE = re.compile(r"^register zPC : %bv(\d+)", re.M)
_VR_RE = re.compile(r"^register zvr0 : %bv(\d+)", re.M)

# The declarations sit in the first few hundred KB of an ~18 MB file; reading the
# whole thing to find them would make every config load cost a disk pass.
_IR_SCAN_BYTES = 4 << 20


# isla's concrete bitvector type is B129 (isla-lib/src/bitvector/b129.rs), which
# represents bitvectors up to 129 bits. A vector register wider than that has no
# representation in the symbolic engine, so VLEN 256 and 512 are outside it
# regardless of how the IR is built. The V extension separately requires
# VLEN >= 128, which leaves 128 as the only value satisfying both -- and that is
# why both checked-in IRs are compiled at 128.
ISLA_MAX_VLEN = 129


class ConfigMismatch(RuntimeError):
    """A configuration cannot be served by the IR that is available.

    Carries the rebuild command rather than only the complaint, because the fix
    is mechanical and the caller should not have to go and find it.
    """


class SymbolicPathUnavailable(ConfigMismatch):
    """A configuration is outside the symbolic engine's representational bounds.

    Distinct from ConfigMismatch because there is no rebuild that resolves it:
    the caller's correct response is to route to the concrete oracle and record
    the symbolic path as architecturally unavailable, not to fix a build.
    """


def _strip_jsonc(text):
    """The model's configs are JSON with `//` comments, which json.load rejects."""
    return re.sub(r"//.*", "", text)


class ModelConfig:
    """A parsed Golden Model configuration file.

    Everything downstream derives from an instance of this rather than restating
    the configuration: the IR to run against, the assembler's `-march`, the
    independent simulator's ISA string, and the coverage scope.
    """

    def __init__(self, path, data):
        self.path = os.path.abspath(path)
        self._d = data
        with open(self.path, "rb") as f:
            self.sha256_12 = hashlib.sha256(f.read()).hexdigest()[:12]

    @classmethod
    def load(cls, path):
        with open(path) as f:
            return cls(path, json.loads(_strip_jsonc(f.read())))

    # --- what the model says -------------------------------------------------

    @property
    def xlen(self):
        return int(self._d.get("base", {}).get("xlen"))

    def _v(self, key, default=None):
        v = self._d.get("extensions", {}).get("V", {})
        if isinstance(v, dict) and key in v:
            return 2 ** v[key]
        return default

    @property
    def vlen(self):
        """None when the configuration does not enable V at all."""
        return self._v("vlen_exp")

    @property
    def elen(self):
        return self._v("elen_exp")

    @property
    def extensions(self):
        """Names of every extension the configuration marks supported.

        The model writes these two ways -- `{"supported": true}` for most, a bare
        boolean for a few -- so both forms are accepted rather than assuming one.
        """
        out = set()
        for name, val in self._d.get("extensions", {}).items():
            on = val.get("supported") if isinstance(val, dict) else val
            if on:
                out.add(name)
        return out

    def enabled(self, name):
        return name in self.extensions

    # --- what the rest of the framework needs --------------------------------

    def isla_ir(self):
        """Path to the IR matching this configuration, or raise.

        Refusing is deliberate. Falling back to a near-miss IR would produce
        tests that assemble and pass while encoding the wrong vector width.
        """
        ir = os.path.join(paths.ISLA_DIR, paths_for_xlen(self.xlen)["isla_ir"])
        if not os.path.exists(ir):
            raise ConfigMismatch(
                f"{ir} does not exist.\n  Build it:\n    {self.rebuild_command()}")

        built = ir_widths(ir)

        if built["xlen"] != self.xlen:
            raise ConfigMismatch(
                f"{os.path.basename(ir)} was compiled for XLEN={built['xlen']}, "
                f"configuration wants XLEN={self.xlen}.")

        # V absent from the configuration means no vector test will be generated,
        # so the IR's vector width is irrelevant and must not block the run.
        if self.vlen is not None and built["vlen"] != self.vlen:
            if self.vlen > ISLA_MAX_VLEN:
                # Not a build problem. isla's concrete bitvector type is B129, so
                # a vector register wider than 129 bits has no representation at
                # all -- rebuilding the IR at this VLEN would produce something
                # isla cannot execute. The configuration is oracle-only, which is
                # a routing outcome rather than a failure.
                raise SymbolicPathUnavailable(
                    f"VLEN={self.vlen} exceeds isla's representational bound.\n"
                    f"  isla's concrete bitvector type is B129 (max {ISLA_MAX_VLEN} "
                    f"bits), so a {self.vlen}-bit vector register cannot be "
                    f"represented. Rebuilding the IR does not change this.\n"
                    f"  This configuration is oracle-only by design. Route vector "
                    f"generation to the concrete oracle and record the symbolic "
                    f"path as unavailable, with this as the architectural reason.")
            raise ConfigMismatch(
                f"VLEN mismatch: {os.path.basename(ir)} was compiled at "
                f"VLEN={built['vlen']}, configuration "
                f"'{os.path.basename(self.path)}' specifies VLEN={self.vlen}.\n"
                f"  VLEN is fixed when isla-sail compiles the model "
                f"(`type vlenbits = bits(vlen)`); no runtime flag and no solver "
                f"constraint changes it.\n"
                f"  Rebuild the IR for this configuration:\n"
                f"    {self.rebuild_command()}")

        return ir

    def isla_toml(self):
        return os.path.join(paths.ISLA_DIR, paths_for_xlen(self.xlen)["isla_toml"])

    def rebuild_command(self):
        """The isla-sail invocation that would produce a matching IR.

        The two --isla-preserve flags are required: without them isla-sail prunes
        the harness entry points, since they are not reachable from its default
        root-function list.
        """
        out = f"riscv{self.xlen}"
        return (f"cd {paths.SAIL_RISCV}/model && isla-sail "
                f"--isla-preserve isla_testgen_init "
                f"--isla-preserve isla_testgen_step "
                f"--config {self.path} --all-modules riscv.sail_project -o {out}"
                f" && cp {out}.ir {paths.ISLA_DIR}/riscv-ir/")

    def describe(self):
        v = (f"VLEN {self.vlen}  ELEN {self.elen}"
             if self.vlen else "V not enabled")
        return (f"{os.path.basename(self.path)}  [{self.sha256_12}]\n"
                f"  XLEN {self.xlen}   {v}   "
                f"{len(self.extensions)} extensions enabled")


def paths_for_xlen(xlen):
    """The per-XLEN IR/TOML names, kept here so callers need not import the
    sweep driver just to resolve a path."""
    if xlen not in (32, 64):
        raise ValueError(f"unsupported XLEN {xlen}")
    return {"isla_ir": f"riscv-ir/riscv{xlen}.ir",
            "isla_toml": f"riscv-ir/riscv{xlen}.toml"}


_ir_cache = {}


def ir_widths(ir_path):
    """{'xlen': N, 'vlen': N or None} compiled into an isla IR.

    `vlen` is None when the IR carries no vector registers, which is a legitimate
    state (a model built with V disabled), not an error.
    """
    key = (ir_path, os.path.getmtime(ir_path))
    if key in _ir_cache:
        return _ir_cache[key]
    with open(ir_path, "r", errors="ignore") as f:
        head = f.read(_IR_SCAN_BYTES)
    pc, vr = _PC_RE.search(head), _VR_RE.search(head)
    if not pc:
        raise ConfigMismatch(
            f"Could not read XLEN from {ir_path}: no `register zPC` declaration "
            f"in the first {_IR_SCAN_BYTES >> 20} MB. Is this an isla IR?")
    out = {"xlen": int(pc.group(1)), "vlen": int(vr.group(1)) if vr else None}
    _ir_cache[key] = out
    return out


def load(path):
    return ModelConfig.load(path)


if __name__ == "__main__":
    import sys
    cfg = ModelConfig.load(sys.argv[1])
    print(cfg.describe())
    try:
        print("  IR:", cfg.isla_ir())
    except ConfigMismatch as e:
        print("  IR: UNAVAILABLE\n   ", str(e).replace("\n", "\n    "))
