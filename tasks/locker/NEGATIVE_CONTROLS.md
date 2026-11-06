# Locker negative controls

These controls are **private**. They live in `tasks/locker/tests/`, are never
mounted into a candidate workdir, and are never mentioned in `PROMPT.md`, the
seed or the image. Their only purpose is to prove that each race, crash and
fault family detects a *plausible* bug rather than a conspicuous corruption.
`LOCKER_FAULT` is an author-only switch in the reference implementation.

Command used (pinned image, network off, frozen 32 MiB tmpfs):

```bash
docker run --rm --network none --tmpfs /scratch:rw,size=33554432 \
  -v <reference-bin>:/bin-under-test:ro \
  -v "$PWD/tasks/locker/tests:/opt/opencode-bench/locker-tests:ro" \
  -e HOME=/tmp -e TMPDIR=/tmp -w /opt/opencode-bench/locker-tests \
  opencode-bench-locker:latest \
  python3 -B negative_controls.py --binary /bin-under-test/locker \
  --scratch /scratch --repeat 10
```

Reference base commit: `1c12961` (WP6), plus the WP7 fault-switch commit.
Image: `opencode-bench-locker:latest` (Ubuntu 24.04, Go 1.22.2, Python 3.12.3).

| fault | realistic bug | detecting cases | natural rate | amplified rate | notes |
|---|---|---|---|---|---|
| `split-publication` | metadata published that does not describe the blob | `p1.concurrency/coherent` | 10/10 | — | natural |
| `lost-writes` | silently drops writes to keys ending in `7` | `p1.concurrency/no-lost-writes` | 10/10 | — | natural |
| `global-lock` | one process lock held across body input | `p1.concurrency/progress` | 10/10 | — | natural |
| `validate-after-replace` | publishes a part before completion validation | `p1.multipart/stale-retry` | 10/10 | — | natural |
| `per-op-commit` | commits a batch after every operation, no rollback | `p1.batch/preconditions` | 10/10 | — | natural |
| `precondition-at-parse` | evaluates `IfMatch` before the commit lock | `p1.batch/preconditions` | 10/10 | — | natural |
| `share-no-refcount` | deletes a blob still referenced by a copy | `p1.batch/independence` | 10/10 | — | natural |
| `ack-before-publish` | sends/flushes 200 before the manifest rename | `p2.batch/kill-after-ack` | 10/10 | 10/10 (`-amp`) | natural and amplified |
| `leak-temp-on-error` | leaves staged temp space after ENOSPC | `p2.faults/space-released` | 10/10 | — | needs tmpfs |
| `ignore-write-error` | publishes a truncated blob and returns 200 | `p2.faults/put-preserves-old` | 10/10 | — | needs tmpfs |
| `phantom-part` | registers a part before its body is stored | `p2.faults/multipart-staging` | 10/10 | — | needs tmpfs |
| `stale-snapshot-clone` | clones the bucket before taking the commit lock | `p1.concurrency/no-lost-writes` | 10/10 | — | natural |
| `metadata-before-blob` | saves metadata before moving the blob into place | `p1.concurrency/coherent` | 10/10 | 10/10 (`-amp`) | natural and amplified |

Summary: 13 controls, 13 detected naturally 10/10, amplified variants 10/10,
0 missed, 0 errors, 0 skipped. Each control also passes its baseline on the
unfaulted reference before the fault is injected.
