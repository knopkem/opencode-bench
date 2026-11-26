# Migration Plan: Public Harness with a Separate Evaluator Image

Status: public history rewritten and force-pushed; private evaluator repository created; agent/evaluator images pushed; final package/source visibility remains  
Date: 2026-11-25

## 0. Current implementation state

Implemented locally:

- `bench.py` uses separate agent/build and evaluator image configuration,
  defaults to the public GHCR `edge` images, supports `build-eval`, validates
  the evaluator manifest and suite listing, and records image provenance in
  scores and submissions.
- `docker/agent.Dockerfile` remains the public agent/build recipe.
- `docker/evaluator.Dockerfile` builds the private-suite evaluator layer.
- `.github/workflows/agent-image.yml` publishes the public agent image.
- The private suite, reference, historical planning documents and raw detailed
  reports now live in the private `knopkem/opencode-bench-eval` repository. Its
  local checkout is the ignored `.private-eval/` directory inside this working
  tree, including the private evaluator-image workflow under `.github/workflows/`.
- Public documentation, submission metadata and regression tests reflect the
  split.

Completed for the public repository:

- The migration was committed and all public branches were rewritten with
  `git-filter-repo`.
- The rewritten history was verified for removed paths and distinctive hidden
  test content, then force-pushed.
- The private pre-rewrite bundle, ref map and old-to-new commit map are stored
  under `.private-eval/history/`.

Image releases:

- `ghcr.io/knopkem/opencode-bench-agent:edge` was pushed and its package made
  public. Anonymous HTTP manifest access returns 200.
- `ghcr.io/knopkem/opencode-bench-eval:edge` was built from
  `knopkem/opencode-bench-eval`, passed the private reference suite in CI, and
  was pushed. Its package remains private until the owner explicitly changes
  visibility.

Still pending because they require repository-owner actions:

1. Make `opencode-bench-eval` public only when ready to distribute the
   evaluator suite image. Anyone pulling it can inspect and recover the tests.
2. Re-run the GitHub issue/artifact audit immediately before changing source
   repository visibility.
3. Make the public source repository public only after those checks pass.

## 1. Purpose and threat model

Migrate `opencode-bench` back to a public source repository without publishing the
current evaluation sources and detailed run artifacts as ordinary GitHub files.

The primary goals are to:

- reduce the chance that LLMs discover evaluation tests through GitHub search,
  source-code crawls, or future source-code training corpora;
- prevent the agent container from reading evaluation tests during a run;
- avoid publishing new detailed test results, assertion failures, traces, and
  screenshots as easily crawled text;
- preserve convenient, reproducible, one-command scoring using publicly pullable
  container images.

This plan treats a public evaluator image as **scrape reduction, not
confidentiality**. Anyone can pull an OCI image and inspect its files and layers.
Deleting a file in a later Dockerfile layer does not remove it from the image.
A determined person or an agent with suitable network access can therefore recover
and republish the tests.

This plan assumes that publishing sanitized aggregate scores is acceptable. Those
scores and the public leaderboard remain crawlable. If no benchmark results may be
public at all, the public issue-based leaderboard must also be disabled or moved
behind access control. Neither `robots.txt`, `noai` metadata, nor license terms can
guarantee exclusion from training.

## 2. Target architecture

### Public source repository

Keep the following public:

- `bench.py`, `PROMPT.md`, and the task seed;
- public smoke tests and unit tests for the harness;
- the generic agent/build Dockerfile;
- scoring, reporting, submission, and leaderboard code;
- sanitized aggregate results and community submission tooling.

Remove from the public source tree:

- `tasks/spriteforge-lite/tests/`;
- `tasks/spriteforge-lite/reference/`;
- raw generated run artifacts and detailed hidden-test output.

### Private evaluation-source repository

Create a private repository, for example `opencode-bench-eval`, containing:

- the hidden Playwright suite;
- the reference implementation and its validation instructions;
- an evaluator Dockerfile and suite manifest;
- the trusted workflow that builds and publishes evaluator images;
- optionally, private official run artifacts and detailed reports.

The private repository is the source of truth for evaluation-source changes. The
public repository remains the source of truth for the harness, prompt, seed, and
scoring contract.

### Public agent image

Publish an image such as:

```text
ghcr.io/knopkem/opencode-bench-agent:<version>
```

It contains OpenCode, Node, Playwright, browsers, seed dependencies, and generic
build/server tooling. It must not contain hidden tests, the reference
implementation, run results, registry credentials, or OpenCode credentials.

Use this image for both the agent and application build containers.

### Public evaluator image

Publish a separate image such as:

```text
ghcr.io/knopkem/opencode-bench-eval:<suite-version>
```

Build it in the private evaluation-source repository from a pinned agent-image
version. Copy the hidden suite into `/opt/opencode-bench/tests` and include a small
manifest containing at least:

- suite version;
- P0/P1/P2 expected test counts;
- evaluator image version or source revision;
- compatible harness version, if needed.

Use this image only after the agent container has exited. Do not bake generated
results into it.

Record the immutable evaluator image digest in each local score. Use versioned,
immutable tags for releases; `latest` may be a convenience alias but must not be
the value recorded for reproducibility.

## 3. Runtime flow

The target flow is:

1. Pull or build the public agent image.
2. Start the agent container using only the agent image, the seed workdir, and the
   required OpenCode configuration.
3. Stop and remove the agent container.
4. Build and typecheck the generated app using the agent image.
5. Start the evaluator container using the evaluator image, with the built
   `dist/` mounted read-only and the run directory mounted for output.
6. Run Playwright with networking disabled, as the current evaluator does.
7. Write detailed artifacts only to the local, gitignored run directory.
8. Publish only explicitly sanitized aggregate submission data, if requested.

The evaluator container no longer needs a host `TESTS_DIR` bind mount. It must
still receive no agent credentials and must not mount the agent workdir read-write.

Image separation prevents accidental local filesystem discovery by the agent.
Runtime retrieval is additionally restricted by an internal-only agent network
and a separate non-TLS-terminating egress proxy. The proxy allows only the
selected model provider and required OpenCode infrastructure, so authenticated
cloud providers continue to work; custom auxiliary endpoints require an explicit
`--allow-host`. This does not make a public evaluator image confidential to a
determined person outside the sandbox. Use a private evaluator image or trusted
remote scoring service when that stronger guarantee is required.

## 4. Harness changes

Update `bench.py` in a small, reviewable change set.

### Image configuration

Introduce distinct configuration for the two images:

- `OPENCODE_BENCH_AGENT_IMAGE` and `--agent-image`;
- `OPENCODE_BENCH_EVAL_IMAGE` and `--eval-image`.

Keep `--image` temporarily as a backwards-compatible alias for `--agent-image`,
with a deprecation notice, or retain it only for older commands where that is less
ambiguous. Document the migration for existing shell scripts and overnight runs.

Route image usage explicitly:

- `start_agent()` -> agent image;
- `build_app()` -> agent image;
- `run_tests()` -> evaluator image;
- image checks and smoke tests -> the appropriate image for each stage.

### Test execution

Change `run_tests()` so that it:

- uses the evaluator image;
- runs `/opt/opencode-bench/tests/playwright.config.ts` already present in that
  image;
- no longer mounts `TESTS_DIR`;
- preserves `--network none`, read-only `dist/`, JSON output, and existing result
  parsing and scoring behavior.

`run`, `check`, and `rescore` must no longer require a local hidden-test checkout.
Commands that do not evaluate an app, such as `report`, `leaderboard`, `backfill`,
`site`, and submission validation, must not require either image.

### Validation and metadata

Update `check` to verify:

- both images are available or provide actionable pull/build instructions;
- the agent image passes its existing tool smoke test;
- the evaluator manifest exists and matches `EXPECTED`;
- the evaluator can start Playwright successfully;
- the agent image does not contain `/opt/opencode-bench/tests` or reference files.

Add the following to local run metadata where practical:

- agent image reference and digest;
- evaluator image reference and digest;
- evaluator suite version;
- harness commit, which is already captured for submissions.

The result schema can remain `opencode-bench/result@1` if these are optional
metadata fields and the suite counts/scoring contract do not change. If test counts
or comparability change, follow the existing schema/template/example migration
rules instead of silently mixing scores.

## 5. Image build and release process

### Agent image workflow

Add a dedicated public image-release workflow that:

- builds only from the public build context;
- runs the agent-image smoke checks;
- pushes a versioned GHCR tag;
- uses only the permissions it needs, such as `contents: read` and
  `packages: write`.

Do not change the existing Pages workflow's permission model or add generated-file
commits.

### Evaluator image workflow

The local staging tree includes
`.private-eval/.github/workflows/evaluator-image.yml`; copy that workflow into
the private evaluation-source repository. In that repository:

1. Pin the agent base image by version or digest.
2. Validate the suite against the reference before publishing.
3. Build the evaluator image with tests and the manifest.
4. Verify that no reference implementation, credentials, local paths, or run
   results enter the image.
5. Push a versioned image and explicitly make the GHCR package public.
6. Keep workflow logs, temporary build context, and artifacts private.

Do not copy the entire private repository into the Docker build context. Prefer a
minimal context containing only the evaluator Dockerfile, suite, manifest, and
required runtime files.

Publishing an image is effectively irreversible: deleting a tag does not recover
copies already pulled. Treat every evaluator release as public disclosure to a
determined inspector.

## 6. Results and public-data hygiene

Retain the existing ignores for `runs/`, raw CSV/report output, and generated
`docs/`, and additionally audit all publication paths.

Keep private:

- `playwright.json` and `test-results/`;
- Playwright traces, screenshots, and videos;
- `test.log` and detailed failure/assertion output;
- raw per-run hidden-test reports;
- private CI artifacts containing any of the above.

Public submissions may contain only the existing allowlisted aggregate fields:
counts, quality, eligibility, model/run metadata, and performance metrics. Do not
attach raw logs or detailed reports to public issues. Audit existing public issues
and prose around result blocks for accidentally included failure details.

The public leaderboard and issue tracker will still be crawlable. Document that
publishing aggregate numbers is a deliberate tradeoff, not a technical guarantee
that they will be excluded from model training.

## 7. Required GitHub history prune and force-push

Removing `tasks/spriteforge-lite/tests/`, `reference/` and the historical
planning documents that describe evaluation coverage (`PLAN.md` and
`M2-PLAN.md`) in new commits does not remove them from Git history. Before
making the repository public again, prune those paths and historical generated
results from **all branches and tags**, then force-push the rewritten refs to
GitHub. Retaining the old public history is not an accepted migration outcome.

### 7.1 Prepare and preserve a private backup

1. Freeze pushes and ask contributors to stop based-on-old-history work until the
   rewrite is complete. Plan to close and recreate or explicitly rebase open pull
   requests; do not merge old-history branches afterward without checking their
   contents.
2. Confirm that the current suite and reference are safely stored in the private
   evaluation-source repository.
3. Create a private mirror backup and keep it outside any public build context or
   artifact directory:

   ```bash
   git clone --mirror https://github.com/knopkem/opencode-bench.git opencode-bench-pre-prune.git
   ```

4. Make a separate mirror copy for the rewrite so the backup remains untouched.
   Record the pre-rewrite default-branch and tag hashes privately.

The backup contains the exposed tests and results and must remain private. Never
push it back to the public remote as a recovery branch or tag.

### 7.2 Rewrite every branch and tag

Install and review `git-filter-repo`, then run it in the disposable mirror. Adapt
the path list only after checking historical names; at minimum remove the hidden
suite, reference, and generated result/report paths:

```bash
git filter-repo --invert-paths \
  --path tasks/spriteforge-lite/tests \
  --path tasks/spriteforge-lite/reference \
  --path PLAN.md \
  --path M2-PLAN.md \
  --path runs \
  --path results.csv \
  --path results.csv.lock \
  --path report.md \
  --path leaderboard.html \
  --path docs
```

Do not remove the public root `tests/` harness unit suite, public task smoke tests,
`submissions/`, or other source needed by the rewritten public HEAD. Check the
resulting tree and run the public unit tests before pushing.

Retain the generated old-to-new commit map privately. Existing submissions and
`score.json` files may contain pre-rewrite `harness.commit` hashes; those links will
no longer resolve after the force-push. Account for this in result validation and
historical reports rather than silently treating those submissions as fabricated.
Do not rewrite public result metadata to imply that an old run used a different
harness revision.

### 7.3 Verify before and after the force-push

Before pushing, verify that no matching paths remain in any rewritten ref:

```bash
git log --all -- tasks/spriteforge-lite/tests tasks/spriteforge-lite/reference PLAN.md M2-PLAN.md
git rev-list --objects --all | grep -E ' (tasks/spriteforge-lite/(tests|reference)(/|$)|(^|/)(PLAN|M2-PLAN)\.md$|runs(/|$)|results[^/]*\.csv(\.lock)?$|report\.md$|leaderboard\.html$|docs(/|$))'
```

Both commands must produce no matching historical paths. Also inspect all refs and
scan historical blobs for distinctive evaluation content, since path filtering does
not remove tests that were copied to a different filename.

After local verification:

1. Temporarily adjust branch/tag protections only as needed for an administrator to
   perform the rewrite.
2. Re-add the GitHub remote if `git-filter-repo` removed it.
3. Force-push every rewritten branch and tag:

   ```bash
   git push origin --force --all
   git push origin --force --tags
   ```

4. Restore branch/tag protections immediately.
5. Fetch a fresh mirror from GitHub and repeat the path/content checks against the
   remote refs, not just the local rewrite.
6. Require contributors to make a fresh clone or explicitly reset onto rewritten
   refs. Old branches must never be pushed back wholesale.

### 7.4 Clean up GitHub data not covered by force-push

A force-push rewrites normal Git refs, but it does not scrub every GitHub object or
product surface. Audit and handle separately:

- pull-request refs and open/closed PR contents;
- issue comments, attachments, and existing result prose;
- release notes, release assets, and releases attached to rewritten tags;
- Actions logs/artifacts and Pages deployments;
- container packages, package provenance, and old image tags;
- direct links to old commit SHAs and GitHub's cached/dangling objects.

If old commits or pull-request refs remain retrievable, ask GitHub Support to clear
cached views and unreachable objects as appropriate. Keep the repository private
until the required remote verification and cleanup are complete.

History pruning does not erase existing forks, clones, external caches, prior
crawling, or training. For a cleaner future benchmark baseline, author the next
suite privately rather than reusing the previously public assertions unchanged.
Do not claim that old tests or old results are secret again.

## 8. Documentation updates

Update the public documentation to cover:

- anonymous pulling of both images;
- versioned agent/evaluator defaults and overrides;
- local agent-image builds versus private evaluator-source builds;
- the fact that evaluator tests are publicly recoverable from the image;
- the distinction between sanitized public scores and private raw artifacts;
- offline evaluator execution after images have been pulled;
- migration from the old `--image` option and local `TESTS_DIR` layout.

Update repository instructions that currently assume hidden tests and the reference
live under `tasks/spriteforge-lite/`. Move reference-validation instructions to the
private evaluation-source repository. Keep the documented P0/P1/P2 counts in sync
with the evaluator manifest and `EXPECTED`.

## 9. Validation plan

Perform validation before changing repository visibility.

### Harness regression checks

Run from the public repository:

```bash
python3 -m py_compile bench.py
python3 -m unittest discover -s tests
./bench.py check
./bench.py submit --check submissions/examples/issue-1.json
./bench.py site --issues tests/fixtures/issues.json --output /private/var/folders/2w/hgzb68wd3c37r_g6dqdm10940000gn/T/opencode/site
./bench.py site --issues tests/fixtures/issues-invalid.json --output /private/var/folders/2w/hgzb68wd3c37r_g6dqdm10940000gn/T/opencode/site-bad --errors /private/var/folders/2w/hgzb68wd3c37r_g6dqdm10940000gn/T/opencode/errors.json
actionlint .github/workflows/*.yml
```

Also verify that `--dry-run` shows the agent image for agent/build commands and the
evaluator image only for the post-agent test command.

### Evaluator correctness

Using the private reference implementation:

- build the reference successfully;
- pass all 20 hidden tests in three consecutive evaluator-image runs;
- pass all 5 public smoke tests;
- confirm the evaluator runs with no network and no host test mount;
- rescore the same saved workdir with the old and new evaluation paths and confirm
  identical test counts and quality.

Treat any reference-suite flake as a test/image bug before release.

### Isolation and release checks

Verify that:

- the agent image contains no hidden specs or reference implementation;
- the agent container receives no evaluator files, evaluator credentials, or Docker
  socket;
- the evaluator image contains the expected suite and manifest but no reference or
  run results;
- both images can be pulled anonymously;
- a clean public clone can run the documented workflow without a private checkout;
- no public workflow uploads raw test artifacts;
- a sample end-to-end run and `rescore` record the evaluator digest and suite
  version correctly.

## 10. Rollout sequence

1. Create the private evaluation-source repository and copy the current suite and
   reference there without exposing new CI logs or artifacts.
2. Implement and review the two-image harness changes while the main repository is
   still private.
3. Build release-candidate agent and evaluator images and complete all validation.
4. Execute the required all-branch/all-tag history prune and force-push, preserve
   the private commit map, and audit existing issues, PRs, releases, artifacts, and
   cached objects.
5. Publish versioned images and test anonymous pulls in a clean environment.
6. Update documentation and pin the public harness defaults to the validated image
   versions.
7. Make the source repository public.
8. Run one clean-clone end-to-end smoke evaluation and compare it with the private
   pre-release result.
9. For subsequent suite changes, publish a new immutable evaluator version rather
   than mutating an existing release tag.

## 11. Rollback and future hardening

Before rollout, retain the private pre-prune mirror, its old-to-new commit map, and
known-good local images. If migration validation fails, keep the repository private
and restore the previous single-image/local-mount workflow. Use the pre-prune mirror
only for private recovery; never force-push it back to the public remote, because
that would restore the removed tests and results. Do not attempt to solve an
accidental evaluator release merely by deleting its tag.

If stronger protection becomes necessary later, migrate to a private evaluator
image with authenticated pulls, or return to private tests mounted only on trusted
evaluators. For official self-service scoring without distributing tests at all,
use a remote trusted scoring service instead.

## 12. Completion criteria

The migration is complete when:

- every publicly reachable branch and tag has been rewritten and verified to contain
  no evaluation-source or generated-result paths, including after a fresh mirror
  fetch from GitHub;
- historical submissions with pre-rewrite harness hashes remain interpretable via
  the private old-to-new commit map;
- agent and evaluator responsibilities use separate public images;
- no agent-visible local path contains the evaluator suite;
- all harness, reference, smoke, rescore-parity, and anonymous-pull checks pass;
- raw results remain private and only approved aggregate fields are published;
- image versions and evaluator provenance are recorded for reproducibility;
- documentation clearly states that public evaluator images reduce casual/source
  corpus discovery but do not provide test secrecy or guarantee training exclusion.
