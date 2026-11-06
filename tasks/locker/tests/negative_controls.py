#!/usr/bin/env python3
"""Author-only sensitivity checks for the Locker evaluator.

Each control runs its intended hidden cases against the reference with a
deliberate fault injected through the private `LOCKER_FAULT` environment. A
control passes when at least one intended case raises an assertion. This file is
never mounted into a candidate workdir and is not used for scoring.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import helpers as h
import test_runner as runner

MANIFEST = json.loads((Path(__file__).resolve().parent / "manifest.json").read_text())
BASE_LIMITS = dict(MANIFEST["limits"])
BASE_LIMITS["fixture_seed"] = MANIFEST["seed"]

CONTROLS = [
    {
        "label": "split metadata from body",
        "fault": "split-publication",
        "cases": [("p1.concurrency", "coherent", runner.p1_concurrency_coherent)],
        "overrides": {"progress_deadline_s": 5, "request_deadline_s": 10},
        "amplified": None,
        "needs_scratch": False,
    },
    {
        "label": "lost unrelated write",
        "fault": "lost-writes",
        "cases": [("p1.concurrency", "no-lost-writes", runner.p1_concurrency_no_lost_writes)],
        "overrides": {"progress_deadline_s": 5, "request_deadline_s": 10},
        "amplified": None,
        "needs_scratch": False,
    },
    {
        "label": "global lock across body input",
        "fault": "global-lock",
        "cases": [("p1.concurrency", "progress", runner.p1_concurrency_progress)],
        "overrides": {"progress_deadline_s": 2, "request_deadline_s": 10},
        "amplified": None,
        "needs_scratch": False,
    },
    {
        "label": "replace object before multipart validation",
        "fault": "validate-after-replace",
        "cases": [("p1.multipart", "stale-retry", runner.p1_multipart_stale_retry)],
        "overrides": {"progress_deadline_s": 10, "request_deadline_s": 10},
        "amplified": None,
        "needs_scratch": False,
    },
    {
        "label": "commit a batch after every operation",
        "fault": "per-op-commit",
        "cases": [("p1.batch", "preconditions", runner.p1_batch_preconditions)],
        "overrides": {},
        "amplified": None,
        "needs_scratch": False,
    },
    {
        "label": "evaluate batch preconditions before the lock",
        "fault": "precondition-at-parse",
        "cases": [("p1.batch", "preconditions", runner.p1_batch_preconditions)],
        "overrides": {},
        "amplified": None,
        "needs_scratch": False,
    },
    {
        "label": "delete a blob still referenced by a copy",
        "fault": "share-no-refcount",
        "cases": [("p1.batch", "independence", runner.p1_batch_independence)],
        "overrides": {},
        "amplified": None,
        "needs_scratch": False,
    },
    {
        "label": "acknowledge a batch before publishing it",
        "fault": "ack-before-publish",
        "cases": [("p2.batch", "kill-after-ack", runner.p2_batch_kill_after_ack)],
        "overrides": {},
        "amplified": "ack-before-publish-amp",
        "needs_scratch": False,
    },
    {
        "label": "leak staged temp space after a write error",
        "fault": "leak-temp-on-error",
        "cases": [("p2.faults", "space-released", runner.p2_faults_space_released)],
        "overrides": {},
        "amplified": None,
        "needs_scratch": True,
    },
    {
        "label": "publish a truncated body after a write error",
        "fault": "ignore-write-error",
        "cases": [("p2.faults", "put-preserves-old", runner.p2_faults_put_preserves_old)],
        "overrides": {},
        "amplified": None,
        "needs_scratch": True,
    },
    {
        "label": "register a multipart part before its body is stored",
        "fault": "phantom-part",
        "cases": [("p2.faults", "multipart-staging", runner.p2_faults_multipart_staging)],
        "overrides": {},
        "amplified": None,
        "needs_scratch": True,
    },
    {
        "label": "clone the bucket before taking the commit lock",
        "fault": "stale-snapshot-clone",
        "cases": [("p1.concurrency", "no-lost-writes", runner.p1_concurrency_no_lost_writes)],
        "overrides": {"progress_deadline_s": 5, "request_deadline_s": 10},
        "amplified": None,
        "needs_scratch": False,
    },
    {
        "label": "publish metadata before moving the blob",
        "fault": "metadata-before-blob",
        "cases": [("p1.concurrency", "coherent", runner.p1_concurrency_coherent)],
        "overrides": {"progress_deadline_s": 5, "request_deadline_s": 10},
        "amplified": "metadata-before-blob-amp",
        "needs_scratch": False,
    },
]


def _limits(control: dict, scratch: str | None) -> dict:
    limits = dict(BASE_LIMITS)
    limits.update(control["overrides"])
    if control["needs_scratch"]:
        limits["scratch_dir"] = scratch
    return limits


def _with_fault_env(fault: str | None):
    old = os.environ.get("LOCKER_FAULT")

    class _Env:
        def __enter__(self):
            if fault is None:
                os.environ.pop("LOCKER_FAULT", None)
            else:
                os.environ["LOCKER_FAULT"] = fault
            return self

        def __exit__(self, exc_type, exc, tb):
            if old is None:
                os.environ.pop("LOCKER_FAULT", None)
            else:
                os.environ["LOCKER_FAULT"] = old
            return False

    return _Env()


def _attempt(control: dict, binary: str, limits: dict, fault: str | None) -> tuple[str, str | None, str]:
    """Return (outcome, case_id, detail) where outcome is detected/error/missed."""
    for _family, case_id, func in control["cases"]:
        try:
            if control["needs_scratch"]:
                with _with_fault_env(fault):
                    func(binary, limits)
            else:
                with h.Server(binary, env=None if fault is None else {"LOCKER_FAULT": fault}) as server:
                    func(server, limits)
        except h.CaseFailure as exc:
            return "detected", case_id, str(exc)
        except Exception as exc:  # noqa: BLE001
            return "error", case_id, f"{type(exc).__name__}: {exc}"
    return "missed", None, ""


def _measure(control: dict, binary: str, limits: dict, fault: str, repeat: int) -> tuple[int, int, dict, list[str]]:
    detected = 0
    errors = 0
    by_case: dict[str, int] = {}
    details: list[str] = []
    for _ in range(repeat):
        outcome, case_id, detail = _attempt(control, binary, limits, fault)
        if outcome == "detected":
            detected += 1
            by_case[case_id] = by_case.get(case_id, 0) + 1
        elif outcome == "error":
            errors += 1
            details.append(f"{case_id}: {detail}")
    return detected, errors, by_case, details


def _check_scratch(path: str | None) -> str | None:
    if path is None:
        return "no scratch"
    required = int(MANIFEST["limits"]["fault_fs_bytes"])
    try:
        stats = os.statvfs(path)
        total = stats.f_frsize * stats.f_blocks
        writable = os.access(path, os.W_OK)
    except OSError:
        return "unreadable scratch"
    if abs(total - required) > 1048576 or not writable:
        return f"scratch size {total}"
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--scratch", default=None, help="tmpfs for the ENOSPC controls")
    parser.add_argument("--control", default=None, help="run one control by label or fault name")
    parser.add_argument("--repeat", type=int, default=1)
    args = parser.parse_args()

    selected = CONTROLS
    if args.control:
        selected = [
            control
            for control in CONTROLS
            if args.control in (control["label"], control["fault"])
        ]
        if not selected:
            print(f"error: unknown control {args.control!r}", file=sys.stderr)
            return 2

    scratch_problem = _check_scratch(args.scratch)
    natural = amplified_only = missed = errors = skipped = 0
    total = 0
    for control in selected:
        total += 1
        label = control["label"]
        limits = _limits(control, args.scratch)
        if control["needs_scratch"] and scratch_problem is not None:
            skipped += 1
            print(f"SKIPPED   {label} ({scratch_problem})")
            continue

        baseline_outcome, case_id, detail = _attempt(control, args.binary, limits, None)
        if baseline_outcome == "detected":
            errors += 1
            print(f"BASELINE  {label}: unfaulted binary failed {case_id} ({detail})")
            continue
        if baseline_outcome == "error":
            errors += 1
            print(f"BASELINE  {label}: unfaulted binary errored {case_id} ({detail})")
            continue

        natural_count, natural_errors, natural_cases, natural_details = _measure(
            control, args.binary, limits, control["fault"], args.repeat
        )
        errors += natural_errors
        if natural_errors:
            print(f"ERROR     {label}: {natural_details[0]}")

        amp_count = 0
        amp_cases: dict[str, int] = {}
        amp_details: list[str] = []
        if control["amplified"]:
            amp_count, amp_errors, amp_cases, amp_details = _measure(
                control, args.binary, limits, control["amplified"], args.repeat
            )
            errors += amp_errors
            if amp_errors:
                print(f"ERROR     {label} (amplified): {amp_details[0]}")

        cases_label = ", ".join(
            f"{case} {count}/{args.repeat}"
            for case, count in sorted(natural_cases.items())
        )
        amp_label = ""
        if control["amplified"]:
            amp_label = f" (amplified {amp_count}/{args.repeat})"
        if natural_count == args.repeat:
            natural += 1
            print(f"detected  {label}: {cases_label} [natural{amp_label}]")
        elif control["amplified"] and amp_count == args.repeat:
            amplified_only += 1
            cases_label = ", ".join(
                f"{case} {count}/{args.repeat}" for case, count in sorted(amp_cases.items())
            )
            print(
                f"detected* {label}: {cases_label} [amplified only; "
                f"natural {natural_count}/{args.repeat}]"
            )
        elif natural_count or amp_count:
            missed += 1
            print(
                f"FLAKY     {label}: natural {natural_count}/{args.repeat}"
                + (f", amplified {amp_count}/{args.repeat}" if control["amplified"] else "")
            )
        else:
            missed += 1
            print(f"MISSED    {label}: no intended case detected the fault")

    print(
        f"controls: {total} total, {natural} detected natural, "
        f"{amplified_only} detected amplified-only, {missed} missed, "
        f"{errors} errors, {skipped} skipped"
    )
    if skipped:
        return 3
    return 0 if missed == 0 and errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
