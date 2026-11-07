# Locker seed v0

## Stack and commands

- Go standard library only. Do not add modules or require network access.
- `go test ./...`
- `go build -trimpath -o bin/locker ./cmd/locker`
- `./bin/locker server --data ./data --addr 127.0.0.1:9000`
- `./bin/locker --endpoint http://127.0.0.1:9000 mb s3://bucket`
- `python3 scripts/smoke.py` (starts its own server on a free loopback port)

## What already works

- HTTP server, bucket create/list/delete, object PUT/GET/HEAD/DELETE.
- CLI `mb`, `rb`, `cp`, `ls`, `rm` through the HTTP API.
- MD5 ETags, content type, last modified, JSON metadata persistence.
- Basic XML list responses and a public smoke script.

## Deliberate v0 limits

- Object bytes are written directly to their final path and metadata is
  rewritten in place. Publication is not atomic.
- There is no synchronization between concurrent writers and readers.
- `Range` and conditional headers are ignored.
- Multipart endpoints return 501.
- Listing has no pagination or delimiter handling.
- Large uploads are read into memory.

The task contract is in `/work/PROMPT.md`. You may replace the storage layer,
but keep the CLI and HTTP contract and the command names above.

## Repository rules

- Work incrementally: prefer focused edits and small new files over replacing
  an entire large file in one step. Keep the tree buildable as you go and run
  the Go tests or build after meaningful changes.
- Do not add a web UI, browser tests, Node tooling or a database engine.
- Do not special-case the public smoke checks; implement the documented
  behavior generally.
- Keep the data directory self-contained; client commands must not read it.
- Write `COMPLETION.md` when you stop.
