# Community result template

The public leaderboard is built from open GitHub issues labeled
`benchmark-result`. Each issue must contain **exactly one** fenced `json` block
with `"schema": "opencode-bench/result@1"`.

## Preferred: let the harness write it

```bash
./bench.py backfill --run RUN_ID   # fill effort/token metrics if the artifacts remain
./bench.py submit --run RUN_ID --print
# paste the output into the issue form, or:
./bench.py submit --run RUN_ID --gh
```

`submit` reads `runs/<run_id>/score.json`, strips local artifact paths, computes
the score hash and validates the block before printing or creating the issue.
`--gh` updates the existing open issue for the same `run_id` instead of
posting a duplicate; use `--new` to force a new issue or `--issue N` to target
one explicitly.

## LLM / agent instructions

If you are an LLM agent posting a result, copy the skeleton below and fill every
field from the actual run. Do not invent hardware, timings or token counts. Use
`null` when a value was not captured. Keep exactly one fenced `json` block.
The hidden-test totals must match the current suite (`P0: 5`, `P1: 9`,
`P2: 6`), and `quality`/`eligible` must match the counts if you include them.
Set `hardware` to one short line (for example
`"RTX 4090 24GB / Ryzen 9 7950X / 64GB DDR5"`).

```json
{
  "schema": "opencode-bench/result@1",
  "run_id": "YYYYMMDDTHHMMSSZ-provider-model-variant-r1",
  "started_at": "YYYY-MM-DDTHH:MM:SSZ",
  "harness": {
    "commit": null,
    "opencode_version": null,
    "suite": "spriteforge-lite",
    "hidden_tests": {
      "P0": 5,
      "P1": 9,
      "P2": 6
    }
  },
  "model": {
    "ref": "provider/model",
    "name": null,
    "provider_type": "unknown",
    "params_b": null,
    "quantization": null,
    "weights_gb": null,
    "context_tokens": null
  },
  "run": {
    "variant": "default",
    "thinking": "unknown",
    "reasoning_effort": null,
    "rep": 1,
    "outcome": "complete",
    "wall_s": null,
    "span_s": null,
    "agent_s": null,
    "ttft_s": null,
    "think_s": null,
    "output_s": null,
    "tool_s": null,
    "other_s": null,
    "tool_shell_s": null,
    "tool_write_s": null,
    "tool_read_s": null,
    "tool_other_s": null,
    "think_share": null,
    "output_share": null,
    "tool_share": null,
    "ttft_share": null,
    "other_share": null,
    "self_test_share": null,
    "wall_ex_self_test_s": null,
    "self_test_s": null,
    "self_test_runs": 0,
    "self_test_failures": 0,
    "build_tool_s": null,
    "dev_tool_s": null,
    "retries": 0,
    "retry_s": 0,
    "compactions": 0,
    "build_ok": false,
    "typecheck_ok": false,
    "test_ok": false,
    "counts": {
      "P0": {"pass": 0, "total": 5},
      "P1": {"pass": 0, "total": 9},
      "P2": {"pass": 0, "total": 6}
    },
    "quality": null,
    "eligible": false,
    "steps": 0,
    "input_tokens": null,
    "output_tokens": null,
    "reasoning_tokens": null,
    "cache_read_tokens": null,
    "cache_write_tokens": null,
    "compaction_tokens": null,
    "total_tokens": null,
    "max_request_tokens": null,
    "mean_request_tokens": null,
    "gen_tokens_per_s": null,
    "gen_tokens_per_s_median": null,
    "tokens_per_step": null,
    "cache_hit_rate": null,
    "cost_usd": null,
    "hardware": null,
    "notes": null
  },
  "evidence": {
    "level": "self-reported",
    "report_url": null,
    "score_sha256": null,
    "notes": null
  }
}
```

## Validation and trust

`./bench.py submit --check FILE` validates a block offline. CI revalidates every
issue, recomputes quality/eligibility and excludes malformed entries.

- `self-reported` is the default.
- `verified` means a maintainer checked the result; it is not a CI replay.
- Maintainers can add `hidden` to withdraw an entry without deleting it.

See [`schema.json`](schema.json) for the published schema and
[`examples/issue-1.json`](examples/issue-1.json) for a filled example.
