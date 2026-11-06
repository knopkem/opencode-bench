#!/usr/bin/env python3
"""Run the hidden Locker behavioural suite.

    python3 run.py --binary /abs/path/locker --manifest manifest.json \
        --seed 20261106 --output results.json

A `--family` run is an author-only diagnostic and is never eligible for
leaderboard ingestion.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import test_runner


def main() -> int:
    parser = argparse.ArgumentParser(description="Locker hidden test runner")
    parser.add_argument("--binary", required=True, help="absolute path to the locker binary")
    parser.add_argument(
        "--manifest",
        default=str(Path(__file__).resolve().parent / "manifest.json"),
    )
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--family", default=None, help="author-only diagnostic family")
    parser.add_argument("--scratch", default=None, help="tmpfs for the p2.faults family")
    parser.add_argument(
        "--generator-seed",
        type=int,
        default=None,
        help="author-only history generator seed (marks the result diagnostic)",
    )
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    binary = os.path.abspath(args.binary)
    if not os.path.isfile(binary) or not os.access(binary, os.X_OK):
        print(f"error: {binary} is not an executable file", file=sys.stderr)
        return 2
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    fixed_seed = manifest.get("seed")
    if args.seed is not None and fixed_seed is not None and args.seed != fixed_seed:
        print(
            f"error: fixtures are fixed at seed {fixed_seed}; refusing to relabel as {args.seed}",
            file=sys.stderr,
        )
        return 2

    includes_faults = args.family is None or args.family == "p2.faults"
    scratch_error: str | None = None
    if includes_faults:
        required = int(manifest["limits"]["fault_fs_bytes"])
        message = (
            f"error: p2.faults requires --scratch pointing at a tmpfs of {required} bytes"
        )
        if args.scratch is None:
            scratch_error = "infrastructure: no scratch filesystem"
            print(message, file=sys.stderr)
        else:
            try:
                stats = os.statvfs(args.scratch)
                total = stats.f_frsize * stats.f_blocks
                writable = os.access(args.scratch, os.W_OK)
            except OSError as exc:
                scratch_error = f"infrastructure: scratch size unavailable ({exc})"
                print(message, file=sys.stderr)
            else:
                if abs(total - required) > 1048576 or not writable:
                    scratch_error = f"infrastructure: scratch size {total}"
                    print(message, file=sys.stderr)

    result = test_runner.run_suite(
        binary,
        manifest,
        family=args.family,
        scratch=args.scratch,
        scratch_error=scratch_error,
        generator_seed=args.generator_seed,
    )

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    failed = 0
    total = 0
    for family in result["tests"]:
        passed = sum(1 for case in family["cases"] if case["ok"])
        failed += len(family["cases"]) - passed
        total += len(family["cases"])
        print(
            f"{family['id']:<16} {passed}/{len(family['cases'])}  {family['title']}",
            file=sys.stderr,
        )
        for case in family["cases"]:
            if not case["ok"]:
                print(f"  FAIL {case['id']}: {case['error']}", file=sys.stderr)
    print(f"passed {total - failed}/{total} subcases", file=sys.stderr)
    if scratch_error is not None:
        return 3
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
