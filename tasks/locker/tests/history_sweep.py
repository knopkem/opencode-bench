#!/usr/bin/env python3
"""Author-only sweep of generated histories against a reference binary.

    python3 history_sweep.py --binary /abs/path/locker --seeds 200

Any failure is a disagreement between the prompt-derived model/checker and the
reference and must be adjudicated against PROMPT.md, not copied from the
reference.
"""

from __future__ import annotations

import argparse
import sys

import helpers as h
import history_check
import history_gen

CASES = ("objects", "multipart", "listing", "restart", "concurrent")


def main() -> int:
    parser = argparse.ArgumentParser(description="Sweep generated Locker histories")
    parser.add_argument("--binary", required=True)
    parser.add_argument("--seeds", type=int, default=200)
    parser.add_argument("--case", action="append", choices=CASES, default=None)
    args = parser.parse_args()

    cases = tuple(args.case) if args.case else CASES
    failures: list[tuple[int, str, str]] = []
    for seed in range(args.seeds):
        limits = {
            "fixture_seed": 20261106,
            "generator_seed": seed,
            "request_deadline_s": 60,
        }
        for case in cases:
            with h.Server(args.binary) as server:
                try:
                    ops = history_gen.generate(case, seed)
                    if case == "concurrent":
                        history_check.run_concurrent(server, ops, limits)
                    else:
                        history_check.run_sequential(server, ops, limits)
                except Exception as exc:  # noqa: BLE001
                    failures.append((seed, case, str(exc).replace("\n", " ")[:400]))

    if failures:
        print(f"{'seed':>5} | {'case':<11} | failure")
        print("-" * 100)
        for seed, case, failure in failures:
            print(f"{seed:>5} | {case:<11} | {failure}")
    total = args.seeds * len(cases)
    print(
        f"summary: {total - len(failures)}/{total} histories passed, "
        f"{len(failures)} disagreement(s)"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
