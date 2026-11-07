# Negative controls

`negative_controls.py` checks that the hidden suite rejects deliberately broken
behavior. The controls use the reference's private `LOCKER_FAULT` environment
switch. That switch is author-only, is not in the seed, is not in the public
prompt, and is never exposed by the candidate API.

| Fault | Intended failing case | Invariant |
|---|---|---|
| `split-publication` | `p1.concurrency/coherent` | body, length and ETag must describe one version |
| `lost-writes` | `p1.concurrency/no-lost-writes` | every acknowledged different-key write survives restart |
| `global-lock` | `p1.concurrency/progress` | an unfinished body must not block unrelated/read progress |
| `validate-after-replace` | `p1.multipart/stale-retry` | failed completion must not change the published object |
| `per-op-commit` | `p1.batch/preconditions` | a failed batch must not leave earlier operations committed |
| `precondition-at-parse` | `p1.batch/preconditions` | batch preconditions must use the commit-time working copy |
| `share-no-refcount` | `p1.batch/independence` | deleting a source must not destroy an independent copy |
| `ack-before-publish` | `p2.batch/kill-after-ack` | an acknowledged batch must be published before the response |
| `leak-temp-on-error` | `p2.faults/space-released` | a failed body write must release its staging space |
| `ignore-write-error` | `p2.faults/put-preserves-old` | a short/partial body must never be acknowledged |
| `phantom-part` | `p2.faults/multipart-staging` | a failed part upload must not register a phantom part |
| `stale-snapshot-clone` | `p1.concurrency/no-lost-writes` | concurrent commits must not overwrite each other |
| `metadata-before-blob` | `p1.concurrency/coherent` | metadata must not reference an unpublished blob |
| `keep-blob-on-commit-error` | `p2.faults/put-commit-cleanup` | a failed post-body commit must release its staged blob |
| `keep-blob-on-commit-error` | `p2.faults/multipart-completion-operational` | a failed post-assembly commit must release its assembled blob |
| `leak-temp-on-error` | `p2.faults/batch-operational-rollback` | a failed staged batch copy must release temporary space |
| `batch-presave-effect` | `p2.faults/batch-operational-rollback` | an unacknowledged batch must not expose partial effects |
| `consume-upload-on-complete-error` | `p2.faults/multipart-completion-operational` | a failed completion must leave its upload session usable |
| `phantom-part` | `p2.faults/part-replacement-preserves-old` | a failed part replacement must preserve the old part |
| `wrong-op-status` | `p2.faults/put-commit-cleanup` | an operational failure must be 500 `InternalError` or a disconnect |

Run:

```bash
python3 tasks/locker/tests/negative_controls.py \
  --binary tasks/locker/reference/bin/locker \
  --scratch /scratch
```

Every control prints `detected`. A `MISSED` line means the evaluator is not
sensitive to that defect and the suite must not be treated as validated. A few
fault names intentionally back more than one control; `--control <fault>` runs
all controls with that fault, while labels remain unique.

These controls cover the realistic corruption and recovery defects listed
above. They do not claim that every crash interleaving is deterministic;
process-kill tests sample bounded windows and do not certify power-loss
durability.
