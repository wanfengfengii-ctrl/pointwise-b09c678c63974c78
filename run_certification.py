"""Single entry point wiring the real verification and feature demo.

The manifest (``.pointwise-runtime.json``) invokes only the standard library
subprocess module against the project's own test modules and demo scripts; this
wrapper never just prints success.  A failing subprocess aborts the whole run
with its real exit code and streamed output.
"""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PY = sys.executable


def run(argv):
    print("\n$ %s" % " ".join(argv), flush=True)
    completed = subprocess.run(argv, cwd=ROOT)
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


def verify():
    # Original complete regression suite, untouched tests under tests/.
    run([PY, "-B", "-m", "unittest", "discover", "-s", "tests", "-t", ".", "-v"])
    # This round's public feature checks.
    run([PY, "-B", "-m", "unittest", "-v", "certification_checks"])
    # Independent finite probes against the untouched codec and resolver.
    run([PY, "-B", "-m", "certification_examples", "--check-source"])
    # Independent bounded enumeration corroboration plus recursion probes.
    run([PY, "-B", "enumeration_check.py", "--iterations", "800", "--strict"])


def demo():
    # Original warehouse demo (history, reopen, mixed-version batches).
    run([PY, "-B", "-m", "warehouse", "demo"])
    # Certification demo: certificate generation, batch publication, certified
    # job creation and the real run_job result across a close/reopen cycle.
    run([PY, "-B", "-m", "certification_examples", "--demo"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--verify", action="store_true")
    mode.add_argument("--demo", action="store_true")
    args = parser.parse_args()
    if args.verify:
        verify()
    else:
        demo()


if __name__ == "__main__":
    main()
