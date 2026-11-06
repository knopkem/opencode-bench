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

Run:

```bash
python3 tasks/locker/tests/negative_controls.py \
  --binary tasks/locker/reference/bin/locker
```

Every control prints `detected`. A `MISSED` line means the evaluator is not
sensitive to that defect and the suite must not be treated as validated.

These controls cover the four defects listed in the design plan. They do not
claim that every crash interleaving is deterministic; process-kill tests sample
bounded windows and do not certify power-loss durability.
