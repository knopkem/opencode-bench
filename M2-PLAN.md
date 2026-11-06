# M2 — Public community leaderboard

Status: planned after M1. M1 (metric correctness + local leaderboard) is
implemented.

## Goal

Publish a live leaderboard from GitHub Issues, with schema validation,
moderation and automatic GitHub Pages deployment. Issues remain the source of
truth; the workflow does not commit generated files.

```
Issue form (JSON block, `benchmark-result` label)
        │  opened / edited / reopened / closed / labeled / unlabeled
        ▼
GitHub Actions
        │  gh issue list --label benchmark-result --state open
        ▼
./bench.py site --issues issues.json --output docs/
        │  parse → validate → dedupe → render
        ▼
docs/index.html + docs/data/results.json
        ▼
GitHub Pages (artifact deploy)
```

## Non-goals

- Structured hardware capture, auto-detection or hardware filters.
- Hardware-normalised speed or per-hardware ranking.
- Independent replay verification.
- Multi-rep median aggregation.
- Harder hidden tests or task changes.

Hardware remains a single optional free-text string. The public page uses the
M1 metrics and the shared renderer.

## 1. Submission contract

Canonical format: exactly one fenced `json` block in the issue body.

```json
{
  "schema": "opencode-bench/result@1",
  "run_id": "20261104T171135Z-local-swift-1.5-iq4_xs-default-r1",
  "started_at": "2026-11-04T17:11:35Z",
  "harness": {
    "commit": "2fc7714",
    "opencode_version": "2.2.5",
    "suite": "spriteforge-lite",
    "hidden_tests": {"P0": 5, "P1": 9, "P2": 6}
  },
  "model": {
    "ref": "local/swift-1.5-iq4_xs",
    "name": "Swift 1.5 Qwen3.8 27B IQ4_XS",
    "provider_type": "local",
    "params_b": 27.3,
    "quantization": "IQ4_XS",
    "weights_gb": 15.5,
    "context_tokens": 262144
  },
  "run": {
    "variant": "default",
    "thinking": "on",
    "reasoning_effort": "high",
    "rep": 1,
    "outcome": "complete",
    "wall_s": 1136,
    "wall_ex_self_test_s": 1100,
    "self_test_s": 36,
    "self_test_runs": 9,
    "self_test_failures": 2,
    "build_tool_s": 1.2,
    "dev_tool_s": 0,
    "retries": 0,
    "retry_s": 0,
    "compactions": 0,
    "build_ok": true,
    "typecheck_ok": true,
    "test_ok": true,
    "counts": {
      "P0": {"pass": 5, "total": 5},
      "P1": {"pass": 9, "total": 9},
      "P2": {"pass": 6, "total": 6}
    },
    "eligible": true,
    "steps": 45,
    "output_tokens": 80047,
    "reasoning_tokens": 0,
    "total_tokens": null,
    "max_request_tokens": null,
    "mean_request_tokens": null,
    "gen_tokens_per_s": null,
    "gen_tokens_per_s_median": null,
    "cost_usd": 0,
    "hardware": "LAN llama.cpp server; inference host hardware not captured",
    "notes": "Clean run after a CUDA fault on the server."
  },
  "evidence": {
    "level": "self-reported",
    "report_url": null,
    "score_sha256": null,
    "notes": "Single rep, default variant."
  }
}
```

Rules:

- `quality` and `eligible` are recomputed from `counts`, never trusted.
- Hidden-test totals must match the current `EXPECTED` (5/9/6).
- `wall_ex_self_test_s` must agree with `wall_s - self_test_s` within tolerance.
- `run_id` format and `started_at` must be valid; `started_at` should agree with
  the run-id timestamp.
- Counts, times, tokens and costs must be non-negative.
- `test_ok` must agree with the counts (all pass vs at least one failure).
- `hardware` is one optional string, max 300 characters. No structured block.
- `evidence.report_url`, if present, must be `https://` and is never fetched by
  CI.
- Body and JSON size are capped.
- Duplicate `run_id` is rejected unless resolved by a maintainer.

CI overwrites these fields from the issue, so they cannot be forged in the body:

- `submitter` = issue author
- `issue` = issue number
- `url` = issue URL
- `submitted_at` = issue `createdAt`
- `evidence.level` = `self-reported` or `verified` from labels

## 2. Files to add

```
submissions/schema.json
submissions/TEMPLATE.md
submissions/examples/issue-1.json
.github/ISSUE_TEMPLATE/benchmark-result.yml
.github/ISSUE_TEMPLATE/config.yml
.github/workflows/community-leaderboard.yml
```

`submissions/schema.json` documents the contract. `bench.py` contains the
authoritative validator plus cross-field checks; fixtures keep the two in sync.

## 3. CLI

### `bench.py submit`

```bash
./bench.py submit --run RUN_ID --print
./bench.py submit --run RUN_ID --gh
./bench.py submit --check FILE
./bench.py submit --template
```

- `--run` defaults to the newest `score.json` by mtime.
- Reads the run's `score.json`, whitelists fields, drops absolute artifact paths
  (`report`, `agent_log`, `build_log`, etc.), and computes `score_sha256`.
- Includes all M1 metrics: `self_test_s`, `self_test_runs`,
  `self_test_failures`, `build_tool_s`, `dev_tool_s`, `wall_ex_self_test_s`,
  `self_test_share`, `retries`, `retry_s`, `gen_tokens_per_s_median`.
- `--hardware "..."` can add or override the free-text hardware note.
- `--print` emits exactly the fenced JSON block for the issue form.
- `--gh` creates an issue with title `[Result] <model> — <quality> — <wall>`,
  body containing the block, and label `benchmark-result`.
- `--check` accepts an issue body or a JSON block, validates it offline and
  prints errors. It needs neither Docker nor the OpenCode CLI.
- `--template` prints the JSON skeleton with `null` placeholders.

### `bench.py site`

```bash
./bench.py site --issues issues.json --output docs --errors errors.json
```

- Reads the JSON produced by `gh issue list`.
- Skips pull requests, closed issues and issues labeled `hidden`.
- Extracts exactly one block with `"schema": "opencode-bench/result@1"`.
- Validates every entry; invalid entries are excluded and reported by issue
  number in `errors.json`.
- Adds `source=community`, issue URL, submitter, submitted time and evidence
  level from labels.
- Deduplicates by `run_id`, preferring a `verified` submission, then the newest
  issue.
- Calls the shared M1 `render_leaderboard` renderer.
- Writes `docs/index.html`, `docs/data/results.json` and `docs/.nojekyll`.
- No network access and no Docker required.

### `bench.py moderate`

```bash
./bench.py moderate --issue 12 --hide
./bench.py moderate --issue 12 --show
```

Wrapper around `gh issue edit --add-label hidden` / `--remove-label hidden`.
The label remains the actual mechanism and can also be applied from the GitHub
UI.

## 4. Public page

Use the same renderer as the local leaderboard, with a public-specific title and
footer. Differences:

- **Source / evidence** column: amber `self-reported` or green
  `maintainer-checked`, linking to the issue.
- Optional free-text hardware in the model tooltip/sub-line.
- Same **Wall basis** selector, eligible speed baseline and speed cap as M1.
- Footer: self-reported results are not independently reproduced; wall is
  hardware-dependent; tok/s excludes failed/retried attempts; link to the
  schema and issue template.
- Empty state with links to “Submit a result” and the template.
- Keep the 30-second refresh option for local preview; on Pages it refreshes the
  deployed snapshot.

The public page reads only issues, not the local `results.csv`. Maintainers who
want their local runs listed can submit them with `bench.py submit --gh` or add
the same JSON block to an issue.

## 5. Issue form

`.github/ISSUE_TEMPLATE/benchmark-result.yml`:

- Name: `Benchmark result`.
- Title prefix: `[Result] `.
- Auto-label: `benchmark-result`.
- Intro text with:
  - link to `submissions/TEMPLATE.md` and the schema;
  - `./bench.py submit --run RUN_ID --print` instructions;
  - a warning not to invent hardware, use `null` when unknown.
- One required textarea with the JSON skeleton in a fenced `json` block.
- One required confirmation checkbox: the run used the unmodified hidden suite
  and the submitter did not edit `score.json`.

`.github/ISSUE_TEMPLATE/config.yml` can point to the schema and a discussion
link if desired.

## 6. Validation and evidence

Validator checks:

- schema marker and required fields;
- `EXPECTED` totals, quality and eligibility consistency;
- `test_ok` vs counts;
- `run_id`, `started_at`, wall/self-test consistency;
- non-negative counts/tokens/times;
- hardware length;
- `https://` evidence URL;
- body/JSON size;
- duplicate `run_id`.

Evidence tiers:

- `self-reported` — schema-valid; listed immediately with an amber badge.
- `verified` — maintainer adds the `verified` label; green
  “maintainer-checked” badge. This is a manual check, not a CI replay.
- Any edit clears `verified`; a maintainer re-adds it after review.

Invalid issues are excluded and get a single bot comment with exact errors plus
`needs-attention`. Valid issues clear `needs-attention`.

## 7. Moderation

- `benchmark-result` — inclusion label, applied by the issue form.
- `verified` — manual trust badge.
- `hidden` — manual exclusion. Never auto-cleared.
- `needs-attention` — automatic validation-failure label.

The page lists open issues with `benchmark-result` and without `hidden`.

Withdrawal options, documented in README:

- add `hidden` (temporary and reversible);
- remove `benchmark-result`;
- close the issue;
- delete a test issue; the daily rebuild reconciles it.

## 8. CI workflow

`.github/workflows/community-leaderboard.yml`

Triggers:

- `issues`: `opened`, `edited`, `reopened`, `closed`, `labeled`, `unlabeled`.
- `push` to `main` for `bench.py`, `submissions/**` and the workflow itself.
- `workflow_dispatch`.
- Daily `schedule` to reconcile deleted issues and missed events.

Permissions:

```yaml
permissions:
  contents: read
  issues: write      # comments and labels only
  pages: write
  id-token: write
```

No `contents: write`; no bot commits; generated files are not committed.

Issue-event condition must include both inclusion and moderation labels:

```yaml
if: |
  contains(github.event.issue.labels.*.name, 'benchmark-result') ||
  (github.event.action == 'unlabeled' && github.event.label.name == 'benchmark-result') ||
  github.event.label.name == 'hidden'
```

Steps:

1. Check out `main` explicitly for issue events.
2. Set up Python 3.12.
3. Fetch all open, labeled issues:

```bash
gh issue list \
  --label benchmark-result --state open --limit 1000 \
  --json number,title,body,url,author,createdAt,updatedAt,labels \
  > "$RUNNER_TEMP/issues.json"
```

4. Run:

```bash
python3 bench.py site \
  --issues "$RUNNER_TEMP/issues.json" \
  --output docs \
  --errors "$RUNNER_TEMP/errors.json"
```

5. If the triggering issue has validation errors, comment once, add
   `needs-attention`; otherwise clear `needs-attention`.
6. On `edited`, remove `verified` (maintainer re-adds after review).
7. `actions/configure-pages` → `actions/upload-pages-artifact` →
   `actions/deploy-pages`.
8. Use a `concurrency` group to prevent overlapping rebuilds.

Do not execute issue content as code. The workflow only parses JSON.

## 9. README and setup

Add to the top:

```markdown
[Live community leaderboard](https://knopkem.github.io/opencode-bench/) ·
[Submit a result](https://github.com/knopkem/opencode-bench/issues/new?template=benchmark-result.yml)
```

Add a “Community leaderboard” section covering:

- how to submit with `bench.py submit` or the issue form;
- the LLM/agent template instructions;
- schema validation and evidence tiers;
- `hidden` / `verified` / `needs-attention` moderation;
- wall-time, self-test, retry and tok/s caveats;
- the distinction between the local and public leaderboards.

One-time setup:

- Settings → Pages → Source: GitHub Actions.
- Create labels: `benchmark-result`, `verified`, `hidden`, `needs-attention`.
- Merge the workflow to the default branch.
- Test with a throwaway issue, then hide or delete it.

## 10. Seed issue #1

Convert the existing issue #1 to the JSON block and add
`benchmark-result`. Use an honest hardware note:

```json
"hardware": "Inference host hardware not captured; LAN llama.cpp server"
```

Do not write a tolerant parser for the old markdown table.

## 11. Tests and acceptance

Unit/fixture tests:

- valid submission parses and normalizes;
- quality/eligibility mismatch rejected;
- hidden-test count mismatch rejected;
- `test_ok` contradiction rejected;
- wall/self-test inconsistency rejected;
- bad URL rejected;
- duplicate `run_id` deduped or rejected;
- `hidden` skipped;
- `verified` badge applied and cleared on edit (workflow logic/fixture);
- `site` renders a page from a fixture issues JSON without network.

Commands:

```bash
python3 -m py_compile bench.py
python3 -m unittest discover -s tests
./bench.py submit --check submissions/examples/issue-1.json
./bench.py site --issues tests/fixtures/issues.json --output /tmp/site
./bench.py site --issues tests/fixtures/issues-invalid.json \
  --output /tmp/site-bad --errors /tmp/errors.json
./bench.py check
```

Manual GitHub checks:

- valid test issue appears;
- invalid issue is excluded, commented and labeled `needs-attention`;
- `hidden` removes and restores;
- `verified` badge appears and is cleared by an edit;
- `submit --gh` creates a correctly labeled issue;
- Pages deploy succeeds and the README link works;
- `submit`, `site` and `moderate` need no Docker or OpenCode CLI.

## 12. Implementation order

1. Schema, template, example, validator, `submit --check/--template`.
2. `site` parsing/validation/dedupe and fixture tests.
3. Issue form and public renderer differences.
4. CI workflow, comments/labels, Pages deploy.
5. `moderate`, README, setup checklist.
6. Convert issue #1 and verify the first public deployment.

## 13. Deferred

- Structured hardware capture and hardware filters.
- Hardware-normalised speed or per-hardware ranking.
- Multi-rep median aggregation (the right long-term fix for “sloppy but lucky”).
- Independent replay verification.
- Harder tests or task redesign to reduce quality saturation.
- Archiving results from older hidden-test suites.
