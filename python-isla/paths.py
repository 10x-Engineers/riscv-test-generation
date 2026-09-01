"""Where everything lives, resolved once instead of hardcoded in five files.

Before this, `SAIL_RISCV`, `ISLA_DIR`, `SPIKE` and friends were absolute paths
to one developer's home directory, repeated across `opcode_sweep.py`,
`csr_sweep.py`, `coverage_report.py` and `scenario_tests.py`. A fresh clone
could not run anything without editing four files, which is a poor answer to the
RFP's requirement for documentation "to enable long-term maintenance of the
framework by the Golden Model community".

Resolution order, per path:

1. an environment variable, so CI and other machines can override without edits;
2. a location derived from this repository, since `sail-riscv` and
   `isla-gen-extension` are submodules and therefore have known relative paths;
3. whatever is on `PATH`, for the tools that are ordinarily installed.

Nothing here fails at import time. A missing tool is a problem for the script
that needs it, reported with the variable to set -- importing this module to run
an unrelated report should not abort because Spike is not installed.
"""
import argparse
import os
import shlex
import shutil

# This file lives in <repo>/python-isla/, so the repository root is one up.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _dir(env, *fallbacks):
    """First of: $env, then each fallback that exists. Last fallback if none do."""
    v = os.environ.get(env)
    if v:
        return os.path.abspath(os.path.expanduser(v))
    for f in fallbacks:
        if f and os.path.exists(f):
            return f
    return fallbacks[-1] if fallbacks else ""


def _tool(env, name, *fallbacks):
    """A binary: $env, then explicit fallbacks, then PATH."""
    v = os.environ.get(env)
    if v:
        return os.path.abspath(os.path.expanduser(v))
    for f in fallbacks:
        if f and os.path.exists(f):
            return f
    return shutil.which(name) or (fallbacks[-1] if fallbacks else name)


# --- The Golden Model ------------------------------------------------------
# A submodule of this repo (pinned to `riscv-testgen-support`). The sibling
# checkout is kept as a fallback so existing working copies keep running.
def _pick_sail_riscv():
    """Prefer a checkout that has actually been built.

    Both locations are legitimate: the submodule is what a fresh clone gets,
    the sibling is the established developer checkout. Picking the *built* one
    means neither case needs an environment variable, and a half-set-up
    submodule cannot silently shadow a working tree. Ties go to the submodule,
    because that is the reproducible one.
    """
    candidates = [os.path.join(REPO_ROOT, "sail-riscv"),
                  os.path.join(os.path.dirname(REPO_ROOT), "sail-riscv")]
    v = os.environ.get("SAIL_RISCV")
    if v:
        return os.path.abspath(os.path.expanduser(v))
    for c in candidates:
        if os.path.exists(os.path.join(c, "build/c_emulator/sail_riscv_sim")):
            return c
    for c in candidates:
        if os.path.isdir(c):
            return c
    return candidates[0]


SAIL_RISCV = _pick_sail_riscv()

SAIL_SIM = _tool("SAIL_RISCV_SIM", "sail_riscv_sim",
                 os.path.join(SAIL_RISCV, "sail_riscv_sim"),
                 os.path.join(SAIL_RISCV, "build/c_emulator/sail_riscv_sim"))

# Built separately with -DCOVERAGE=ON; kept apart from the ordinary build so a
# coverage run never silently uses an uninstrumented emulator.
COVERAGE_SIM = _tool("SAIL_RISCV_COVERAGE_SIM", "",
                     os.path.join(SAIL_RISCV, "build-coverage/c_emulator/sail_riscv_sim"))
BRANCH_INFO = os.environ.get(
    "SAIL_BRANCH_INFO",
    os.path.join(SAIL_RISCV, "build-coverage/sail_riscv_model.branch_info"))
COVERAGE_FILE = os.path.join(SAIL_RISCV, "sail_coverage")


def sail_config(xlen, vlen=128, elen=64, build="build"):
    """Path to one of the model's own generated config files.

    These are produced by the model's build, so they track the model rather
    than being copied here and going stale.
    """
    return os.path.join(SAIL_RISCV, build, "config",
                        f"rv{xlen}d_v{vlen}_e{elen}.json")


# --- The symbolic engine ---------------------------------------------------
ISLA_DIR = _dir("ISLA_TESTGEN_DIR", os.path.join(REPO_ROOT, "isla-gen-extension"))
ISLA_BIN = _tool("ISLA_TESTGEN_BIN", "isla-testgen",
                 os.path.join(ISLA_DIR, "target/release/isla-testgen"))
# Z3 is loaded at runtime via LD_LIBRARY_PATH, not linked, so the directory is
# what matters rather than a file.
Z3_LIB = _dir("Z3_LIB_DIR", os.path.expanduser("~/.cache/udb/z3/z3-4.16.0/x64"))

# --- Third-party simulators and toolchain ----------------------------------
# No hardcoded fallback: a path under one developer's home directory is not a
# location any other clone has, and leaving it here made the resolution order
# look machine-specific to anyone reading it. $SPIKE_BIN, then PATH.
SPIKE = _tool("SPIKE_BIN", "spike")
RISCV_TOOLCHAIN_DIR = _dir("RISCV_TOOLCHAIN_DIR",
                           os.path.dirname(SPIKE) if os.path.dirname(SPIKE) else "")


# The resolution table, in one place so `describe()` and `export()` cannot drift.
def _rows():
    return [("SAIL_RISCV", SAIL_RISCV), ("SAIL_RISCV_SIM", SAIL_SIM),
            ("SAIL_RISCV_COVERAGE_SIM", COVERAGE_SIM), ("SAIL_BRANCH_INFO", BRANCH_INFO),
            ("ISLA_TESTGEN_DIR", ISLA_DIR), ("ISLA_TESTGEN_BIN", ISLA_BIN),
            ("Z3_LIB_DIR", Z3_LIB), ("SPIKE_BIN", SPIKE),
            ("RISCV_TOOLCHAIN_DIR", RISCV_TOOLCHAIN_DIR)]


def export(include_missing=False):
    """Shell `export` lines for the resolved paths.

        eval "$(python3 python-isla/paths.py --export)"

    Two of these do real work beyond documentation. `LD_LIBRARY_PATH` has to
    carry the Z3 directory, because isla-testgen loads Z3 at run time rather
    than linking it, and `PATH` has to carry the toolchain directory so the
    assembler and linker resolve. The rest are exported so a shell session
    agrees with what the scripts resolved, instead of each re-deriving it.

    A path that does not exist is emitted as a comment rather than an export:
    pinning a wrong value would defeat the fallback order the next time a
    script runs. Pass --include-missing to export them anyway.
    """
    out = ["# resolved by paths.py -- eval \"$(python3 python-isla/paths.py --export)\""]
    for name, value in _rows():
        if value and os.path.exists(value):
            out.append("export %s=%s" % (name, shlex.quote(value)))
        elif include_missing and value:
            out.append("export %s=%s" % (name, shlex.quote(value)))
        else:
            out.append("# %s not found -- leaving it unset so resolution retries" % name)

    if Z3_LIB and os.path.isdir(Z3_LIB):
        out.append("# isla-testgen loads Z3 at run time, so it must be on the loader path")
        out.append('export LD_LIBRARY_PATH=%s${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}'
                   % shlex.quote(Z3_LIB))
    if RISCV_TOOLCHAIN_DIR and os.path.isdir(RISCV_TOOLCHAIN_DIR):
        out.append("# the assembler, linker and Spike")
        out.append('export PATH=%s${PATH:+:$PATH}' % shlex.quote(RISCV_TOOLCHAIN_DIR))
    return "\n".join(out)


def describe():
    """Human-readable resolution table -- what a script should print when a
    path turns out to be wrong, so the fix is obvious."""
    out = ["resolved paths (override any of these with the environment variable "
           "of the same name):", ""]
    for name, value in _rows():
        mark = "ok " if value and os.path.exists(value) else "MISSING"
        out.append(f"  {mark}  {name:26s} {value}")
    return "\n".join(out)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Resolve every path the framework needs.")
    ap.add_argument("--export", action="store_true",
                    help="emit shell export lines: eval \"$(paths.py --export)\"")
    ap.add_argument("--include-missing", action="store_true",
                    help="with --export, export paths that do not exist as well")
    a = ap.parse_args()
    print(export(a.include_missing) if a.export else describe())
