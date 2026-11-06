# Locker reference

Author/evaluator-only. Never mount this directory into a candidate workdir.

```bash
cd tasks/locker/reference
go test ./...
go build -trimpath -o bin/locker ./cmd/locker
```

The hidden suite expects an absolute binary path and creates its own temporary
data directories:

```bash
python3 tasks/locker/tests/run.py \
  --binary "$PWD/tasks/locker/reference/bin/locker" \
  --manifest tasks/locker/tests/manifest.json \
  --seed 20261106 \
  --output /tmp/locker-result.json
```

Diagnostic families are author-only and set `diagnostic: true`:

```bash
python3 tasks/locker/tests/run.py --binary ... \
  --manifest tasks/locker/tests/manifest.json \
  --family p1.concurrency --output /tmp/locker-diagnostic.json
```

Evaluator unit tests and negative controls:

```bash
python3 -m unittest discover -s tasks/locker/tests -p 'test_*.py'
python3 tasks/locker/tests/negative_controls.py \
  --binary tasks/locker/reference/bin/locker
```

The reference's private `LOCKER_FAULT` environment switch is used only by
`negative_controls.py` to prove the evaluator detects deliberately broken
publication, lost-write, blocking and multipart-validation behavior. It is not part of the public
contract and does not exist in the seed.

The evaluation image is Linux; the performance case reads `VmHWM` from
`/proc/<pid>/status`. Local macOS runs fall back to `ps` RSS and should only be
used for authoring smoke checks.
