#!/usr/bin/env python3
"""Prove a fresh clone works, end to end, in one command.

    python3 tools/verify_clone.py

This exists because "it works on the machine it was written on" is not a claim
anyone can check, and a reviewer's first five minutes with this repository
decide whether the rest of it gets read. So rather than a list of prerequisites
to satisfy by hand, this runs the actual pipeline on one instruction and reports
what happened at each stage.

Design rules, both learned here:

  * **Never fail on a missing optional tool.** Spike, QEMU and Verilator are not
    needed to demonstrate the framework; they are needed to demonstrate
    *independent* checking. A clone without Spike should report exactly that and
    still prove the rest works, rather than exiting 1 and telling the reader
    nothing about what does run.

  * **Run things, do not stat them.** Checking that a file exists proves the
    build was attempted. Generating a test, assembling it with the real
    toolchain and executing it on the model proves the pipeline works. Only the
    second is worth a reviewer's trust, and the difference has bitten this
    project before -- a suite of 1800 tests once passed while comparing empty
    expected-state tables.

Exit status is 0 when every *required* stage passed, 1 otherwise, so it can be
wired into CI unchanged.
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "python-isla"))

PASS, SKIP, FAIL = "PASS", "SKIP", "FAIL"
_MARK = {PASS: "  ok  ", SKIP: " skip ", FAIL: " FAIL "}

results = []


def record(stage, status, detail="", fix=""):
    results.append((stage, status, detail, fix))
    print(f"[{_MARK[status]}] {stage:<34} {detail}", flush=True)
    if fix and status != PASS:
        print(f"           -> {fix}", flush=True)
    return status == PASS


def run(cmd, **kw):
    kw.setdefault("capture_output", True)
    kw.setdefault("text", True)
    kw.setdefault("timeout", 180)
    try:
        return subprocess.run(cmd, **kw)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        class R:
            returncode = 127
            stdout = ""
            stderr = str(e)
        return R()


# -- stage 0: the clone itself ---------------------------------------------
def check_submodules():
    """A clone without --recurse-submodules leaves these empty, and every later
    stage then fails in a way that does not name the real cause."""
    ok = True
    for folder, marker in (("sail-riscv", "model"),
                           ("autotest", "src/sailtest"),
                           ("isla-gen-extension", "src")):
        p = os.path.join(ROOT, folder, marker)
        if os.path.exists(p):
            record(f"submodule {folder}", PASS, "populated")
        else:
            ok = record(f"submodule {folder}", FAIL, "empty",
                        "git submodule update --init --recursive") and ok
    return ok


# -- stage 1: configuration -------------------------------------------------
def check_paths():
    try:
        import paths
    except Exception as e:                                    # pragma: no cover
        return record("path resolution", FAIL, str(e))
    required = [("Golden Model checkout", paths.SAIL_RISCV, True),
                ("sail_riscv_sim", paths.SAIL_SIM, True),
                ("coverage sim", paths.COVERAGE_SIM, False),
                ("branch_info manifest", paths.BRANCH_INFO, False),
                ("isla-testgen", paths.ISLA_BIN, False),
                ("Spike", paths.SPIKE, False)]
    ok = True
    for name, value, needed in required:
        if value and os.path.exists(value):
            record(name, PASS, value)
        elif needed:
            ok = record(name, FAIL, "not found",
                        "python3 python-isla/paths.py  # shows every override") and ok
        else:
            record(name, SKIP, "not found -- optional, some stages will skip")
    return ok


def check_simulator_provenance():
    """The simulator must have been built from the checkout being measured.

    `paths.py` falls back to PATH when a checkout has not been built, which is
    the right behaviour for a developer with a system-wide install and the wrong
    one to leave unremarked: a coverage figure produced by measuring checkout A
    with a simulator built from checkout B is silently wrong, and looks exactly
    like a correct one. Surfaced by verifying a genuinely fresh clone, where the
    submodule was unbuilt and `sail_riscv_sim` resolved to an unrelated binary
    on PATH.
    """
    import paths
    sim, root = paths.SAIL_SIM, os.path.abspath(paths.SAIL_RISCV)
    if not (sim and os.path.exists(sim)):
        return record("simulator provenance", SKIP, "no simulator resolved")
    if os.path.abspath(sim).startswith(root + os.sep):
        return record("simulator provenance", PASS, "built from this checkout")
    return record(
        "simulator provenance", FAIL,
        "simulator is OUTSIDE the model checkout being measured",
        f"sim  {sim}\n              model {root}\n"
        f"              These must match, or coverage measures one model with\n"
        f"              another model's simulator. Build the checkout, or set\n"
        f"              SAIL_RISCV to the one this simulator came from.")


def check_sail():
    """The Sail compiler, without which the model does not configure at all.

    It is normally installed through opam, whose environment is not active in a
    fresh shell -- so the usual failure is not "not installed" but "installed
    and not on PATH", which the CMake error does not distinguish.
    """
    sail = shutil.which("sail")
    if sail:
        v = run([sail, "--version"])
        return record("Sail compiler", PASS,
                      (v.stdout or "").strip().splitlines()[0][:60] if v.stdout else sail)
    opam = shutil.which("opam")
    return record("Sail compiler", FAIL, "not on PATH",
                  "eval $(opam env)   # opam is installed; its environment is not active"
                  if opam else "opam install sail && eval $(opam env)")


def check_toolchain():
    cc = (shutil.which("riscv64-unknown-elf-gcc")
          or shutil.which("riscv64-linux-gnu-gcc")
          or shutil.which("clang"))
    if not cc:
        return record("RISC-V compiler", FAIL, "none found",
                      "install riscv64-unknown-elf-gcc, or clang with a RISC-V target")
    return record("RISC-V compiler", PASS, cc)


def check_configs():
    import paths
    d = os.path.join(paths.SAIL_RISCV, "build", "config")
    n = len([f for f in os.listdir(d) if f.endswith(".json")]) if os.path.isdir(d) else 0
    if n:
        return record("model configurations", PASS, f"{n} generated by the model build")
    return record("model configurations", FAIL, "none",
                  "cmake -B sail-riscv/build -S sail-riscv -DCMAKE_BUILD_TYPE=Release && cmake --build sail-riscv/build")


# -- stage 2+3: generate, assemble, execute ---------------------------------
def check_end_to_end(keep):
    """The stage that actually matters: build a test and run it on the model."""
    import paths
    if not os.path.exists(paths.SAIL_SIM):
        return record("end-to-end generate+run", SKIP, "no simulator built")
    cc = (shutil.which("riscv64-unknown-elf-gcc")
          or shutil.which("riscv64-linux-gnu-gcc"))
    if not cc:
        return record("end-to-end generate+run", SKIP, "no RISC-V compiler")

    out = tempfile.mkdtemp(prefix="verify-clone-")
    cfg = paths.sail_config(64)
    if not os.path.exists(cfg):
        return record("end-to-end generate+run", SKIP, "no rv64 config")

    env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, "autotest", "src"))
    r = run([sys.executable, "-m", "sailtest.cli", "generate",
             "--config", cfg, "--sail-riscv", paths.SAIL_RISCV,
             "--backend", "template", "--count", "2", "--out", out],
            cwd=os.path.join(ROOT, "autotest"), env=env)
    if r.returncode != 0:
        return record("generate (oracle backend)", FAIL,
                      (r.stderr or r.stdout).strip().splitlines()[-1][:120]
                      if (r.stderr or r.stdout).strip() else "non-zero exit")
    record("generate (oracle backend)", PASS, "2 self-checking tests")

    manifest = None
    for root, _d, files in os.walk(out):
        if "manifest.json" in files:
            manifest = os.path.join(root, "manifest.json")
            break
    if not manifest:
        return record("manifest written", FAIL, "no manifest.json produced")
    record("manifest written", PASS, os.path.relpath(manifest, out))

    r = run([sys.executable, "-m", "sailtest.cli", "run",
             "--manifest", manifest, "--config", cfg, "--simulator", "sail"],
            cwd=os.path.join(ROOT, "autotest"), env=env)
    body = (r.stdout or "") + (r.stderr or "")
    ok = r.returncode == 0
    record("run on the Golden Model", PASS if ok else FAIL,
           "tests executed and self-checked" if ok
           else body.strip().splitlines()[-1][:120] if body.strip() else "non-zero exit")

    if os.path.exists(paths.SPIKE):
        r2 = run([sys.executable, "-m", "sailtest.cli", "run",
                  "--manifest", manifest, "--config", cfg, "--simulator", "spike"],
                 cwd=os.path.join(ROOT, "autotest"), env=env)
        record("run on Spike (independent)", PASS if r2.returncode == 0 else FAIL,
               "the only run that is evidence about the model")
    else:
        record("run on Spike (independent)", SKIP,
               "Spike not installed -- differential checking unavailable")

    if keep:
        print(f"           generated suite kept at {out}")
    else:
        shutil.rmtree(out, ignore_errors=True)
    return ok


# -- stage 4+6: measurement -------------------------------------------------
def check_measurement():
    import paths
    if not (os.path.exists(paths.COVERAGE_SIM) and os.path.exists(paths.BRANCH_INFO)):
        return record("coverage tooling", SKIP, "no coverage build",
                      "cmake -B sail-riscv/build-coverage -S sail-riscv -DCMAKE_BUILD_TYPE=Release -DCOVERAGE=ON")
    from coverage_report import parse_spans
    n = len(parse_spans(paths.BRANCH_INFO))
    record("span manifest parses", PASS if n else FAIL, f"{n:,} instrumented spans")

    excl = os.path.join(tempfile.gettempdir(), "verify-excl.txt")
    r = run([sys.executable, os.path.join(ROOT, "python-isla",
                                          "derive_span_exclusions.py"), "-o", excl])
    record("derive exclusions", PASS if r.returncode == 0 else FAIL,
           (r.stdout or "").strip().splitlines()[0][:90] if r.returncode == 0 else "failed")

    r = run([sys.executable, os.path.join(ROOT, "python-isla", "testplan.py"),
             "--exclude-spans", excl, "-o",
             os.path.join(tempfile.gettempdir(), "verify-plan")],
            timeout=600)
    line = next((l for l in (r.stdout or "").splitlines() if "plan items" in l), "")
    return record("build the testplan", PASS if r.returncode == 0 else FAIL,
                  line.strip()[:100] or "failed")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--keep", action="store_true",
                    help="keep the generated demo suite instead of deleting it")
    args = ap.parse_args()

    print(f"Verifying the clone at {ROOT}\n")
    print("-- the clone ------------------------------------------------------")
    check_submodules()
    print("\n-- stage 1: configuration -----------------------------------------")
    check_paths()
    check_simulator_provenance()
    check_sail()
    check_toolchain()
    check_configs()
    print("\n-- stages 2-3: generate, assemble, execute ------------------------")
    check_end_to_end(args.keep)
    print("\n-- stages 4-6: measure and plan -----------------------------------")
    check_measurement()

    n_fail = sum(1 for _s, st, _d, _f in results if st == FAIL)
    n_skip = sum(1 for _s, st, _d, _f in results if st == SKIP)
    n_pass = sum(1 for _s, st, _d, _f in results if st == PASS)
    print(f"\n{'=' * 68}")
    print(f"{n_pass} passed, {n_skip} skipped, {n_fail} failed")
    if n_skip and not n_fail:
        print("\nSkipped stages are optional tooling, not defects. Each line above\n"
              "names what was missing and the command that provides it.")
    if n_fail:
        print("\nFailed stages, in order:")
        for s, st, d, fix in results:
            if st == FAIL:
                print(f"  {s}: {d}" + (f"\n    -> {fix}" if fix else ""))
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
