#!/usr/bin/env python3
"""Author-only sensitivity checks for the Locker evaluator.

Each control runs one family against the reference with a deliberate fault
injected through the private `LOCKER_FAULT` environment. The control passes
when the intended invariant fails. This file is never mounted into a candidate
workdir and is not used for scoring.
"""

from __future__ import annotations

import argparse
import sys

import helpers as h
import test_runner as runner


CONTROLS = [
    (
        "split metadata from body",
        "split-publication",
        "p1.concurrency",
        "coherent",
        runner.p1_concurrency_coherent,
        {"progress_deadline_s": 5, "request_deadline_s": 10},
    ),
    (
        "lost unrelated write",
        "lost-writes",
        "p1.concurrency",
        "no-lost-writes",
        runner.p1_concurrency_no_lost_writes,
        {"progress_deadline_s": 5, "request_deadline_s": 10},
    ),
    (
        "global lock across body input",
        "global-lock",
        "p1.concurrency",
        "progress",
        runner.p1_concurrency_progress,
        {"progress_deadline_s": 2, "request_deadline_s": 10},
    ),
    (
        "replace object before multipart validation",
        "validate-after-replace",
        "p1.multipart",
        "stale-retry",
        runner.p1_multipart_stale_retry,
        {"progress_deadline_s": 10, "request_deadline_s": 10},
    ),
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    args = parser.parse_args()
    failed = False
    for label, fault, family, case, func, overrides in CONTROLS:
        limits = dict(overrides)
        detected = False
        try:
            with h.Server(args.binary, env={"LOCKER_FAULT": fault}) as server:
                func(server, limits)
        except Exception as exc:  # noqa: BLE001 - any failure is detection here
            detected = True
            print(f"detected  {family}/{case}: {label} ({type(exc).__name__}: {exc})")
        if not detected:
            failed = True
            print(f"MISSED    {family}/{case}: {label} passed despite the injected fault")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
