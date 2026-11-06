# Locker — implement the documented object-store contract

You are extending **Locker**, a small local, single-node, S3-inspired object
store written in Go. The seed already builds, serves HTTP, has a working CLI and
stores objects and metadata. It deliberately has an unfinished storage and
protocol layer.

Your job is to make the documented behavior below true. You may restructure the
storage layer freely, but keep the CLI and HTTP contract. Do not replace the
product with a different service. The unchanged seed already passes every P0
subcase; P1 and P2 are the real challenge.

## Budget and priorities

- Aim for about **20 minutes** of work. The hard cap is **60 minutes**, with a
  **5-minute** no-output timeout. Stop and write `COMPLETION.md` when you are
  done or when time is nearly up.
- Prioritise in this order: **P0** (the seed baseline), then **P1** protocol and
  interaction behavior, then **P2** interrupted-publication, isolation and
  resource behavior.
- Use only the Go standard library. Do not add dependencies, do not use the
  network for builds, and do not require Node, a browser or a database engine.
- Keep the server loopback-only. Authentication, TLS, replication, versioning,
  lifecycle rules, deduplication and garbage collection are out of scope.

## Build, run and self-test

```bash
go test ./...
go build -trimpath -o bin/locker ./cmd/locker
./bin/locker server --data ./data --addr 127.0.0.1:9000
./bin/locker --endpoint http://127.0.0.1:9000 mb s3://bucket
```

`scripts/smoke.py` contains a mix of passing and failing public checks. It is
not the hidden suite; use it to find obvious missing behavior.

## Scoring

The hidden suite is black-box: it invokes the built binary over HTTP and the
CLI and never imports internal packages or inspects the data format. It has
three tiers:

- **P0** is the regression gate. All P0 subcases must pass for eligibility;
  P0 contributes no quality points.
- **P1** and **P2** are divided into thirteen equally weighted families. A
  family's credit is the fraction of its fixed required subcases that pass.
  Quality is the mean of those thirteen family credits, so all P0 + all P1 +
  no P2 credit is `7/13 ≈ 0.538`.
- **Eligible** means the build succeeded, every P0 subcase passed, and quality
  is at least **0.80**. Eligibility is a benchmark label, not a claim of
  production readiness.

Scored behavior covers four surfaces in one product: CLI↔HTTP↔storage
integration, ordinary HTTP object operations, protocol interactions (range,
conditional, multipart, listing, same-bucket batch operations), and storage
behavior (coherent publication, concurrency, restart recovery, key isolation,
streaming memory, behavior when the data filesystem is full). The tests use
small fixtures for most cases, one streaming 128 MiB object, and a small data
filesystem for the full-disk cases. Some tests replay generated sequences of
ordinary operations and compare every response against the documented
semantics, so partial implementations of any documented rule lose credit.

## Resource limits

These exact values are frozen for this suite. They are also in the trusted test
manifest.

| Limit | Value |
|---|---|
| Streaming fixture | `134,217,728` bytes (128 MiB) |
| Server high-water RSS cap | `100,663,296` bytes (96 MiB), measured as `VmHWM` in `/proc/<pid>/status` |
| Total large PUT + GET + range workload deadline | `180` seconds |
| Progress deadline for unrelated operations during a stalled upload | `10` seconds |
| Individual test request deadline | `60` seconds |
| Server readiness/startup deadline | `15` seconds |
| Bounded crash trials | `6` |
| Maximum operations in one batch request | `32` |
| Data filesystem size for full-disk cases | `33,554,432` bytes (32 MiB) |
| Single write attempted on the full data filesystem | `50,331,648` bytes (48 MiB) |

The memory cap is about the server process, not the Python test client. Stream
object bytes; do not read a whole object body into memory.

---

# Public contract

Locker is a local, single-node object store implementing exactly this subset.
Paths use `/b` for a bucket and `/b/k` for a non-empty object key. Split the
escaped path into bucket/key, then percent-decode each exactly once; do not
clean dot segments or decode a second time. Malformed encoding, invalid UTF-8
and invalid bucket names return 400. `/health` is reserved for readiness. Query
parameters use ordinary URL query decoding.

## HTTP surface

- `PUT/GET/HEAD/DELETE /bucket/key`
- bucket create/list/delete
- `GET /bucket?list-type=2&prefix=&delimiter=&max-keys=&continuation-token=`
- multipart: initiate, upload part, list parts, complete, abort
- range and conditional GET/HEAD
- XML responses for list and multipart; raw bytes for object data
- no XML namespaces, no auth
- `GET /health` returns 200 with plain text `ok`

| Request | Success | Required response |
|---|---|---|
| `GET /health` | 200 | Plain text `ok` |
| `GET /` | 200 | `ListAllMyBucketsResult`, names bytewise sorted |
| `PUT /b` | 200 | Empty body; creating an existing bucket is idempotent |
| `DELETE /b` | 204 | Empty body; absent bucket 404, non-empty bucket 409 |
| `GET /b` or `GET /b?list-type=2` | 200 | `ListBucketResult`; supports the documented listing parameters |
| `POST /b?batch` | 200 | Atomic same-bucket batch of copy/rename/delete operations; empty body on success |
| `PUT /b/k` | 200 | Empty body; quoted ETag header; bucket must already exist |
| `GET /b/k` | 200 or 206 | Object bytes and version headers |
| `HEAD /b/k` | Same as GET | Same version/range headers, never a body |
| `DELETE /b/k` | 204 | Empty body, including an absent key in an existing bucket |
| `POST /b/k?uploads` | 200 | `InitiateMultipartUploadResult`; bucket must exist |
| `PUT /b/k?uploadId=U&partNumber=N` | 200 | Empty body and quoted MD5 ETag of the part |
| `GET /b/k?uploadId=U` | 200 | `ListPartsResult`, numeric part order |
| `POST /b/k?uploadId=U` | 200 | Completion XML request; `CompleteMultipartUploadResult` response |
| `DELETE /b/k?uploadId=U` | 204 | Abort, empty body |

Object operations on an absent bucket return 404. GET/HEAD of an absent key
return 404. Multipart routing takes precedence over ordinary object routing;
invalid/combined multipart action parameters return 400. Unsupported methods
return 405. Part numbers are decimal integers from 1 through 10000 inclusive.
Malformed completion XML or an empty part list returns 400 without mutation.
The `batch` query parameter is valid only as `POST` on a bucket path; `batch`
combined with any listing or multipart parameter, or present on an object
path, returns 400.

Valid object/part PUTs support `Expect: 100-continue`: send the interim 100
response when beginning to read the body, before waiting for the remaining
bytes. This allows clients to coordinate an unfinished upload without guessing
whether the server has started handling it.

## Version headers and ETags

A successful object GET/HEAD includes `ETag`, `Content-Length`, `Content-Type`,
`Last-Modified` (HTTP date) and `Accept-Ranges: bytes`. Preserve the upload's
`Content-Type`, defaulting to `application/octet-stream`. Multipart uses the
type supplied at initiation, not individual part types. A completed object's
ETag is the quoted lowercase MD5 of its entire body. Empty objects are valid.

Bytes, ETag, length, content type and modification time belong to one published
version. A GET's headers must describe the body that GET returns. Separate
GET/HEAD/list requests may legitimately observe different versions while writes
are active; compare them after writes stop or after restart.

## Range GET/HEAD

Single range only: `start-end`, `start-`, `-suffix`.

For a body of length `n`:

- clip a range end above `n-1`;
- an oversized positive suffix returns the whole body with 206;
- negative/reversed bounds, zero suffix, multiple ranges and ranges starting at
  or beyond `n` return 416, including any range on an empty object;
- all 416 responses include `Content-Range: bytes */n`; their body is not
  graded;
- `HEAD` has the corresponding GET status and headers but no body;
- `If-Range` is outside this subset.

## Conditional GET/HEAD

Evaluate preconditions before Range. Required order:

1. evaluate `If-Match` when present, otherwise `If-Unmodified-Since`;
2. then evaluate `If-None-Match` when present, otherwise
   `If-Modified-Since`;
3. stop on a failed precondition or not-modified result.

Use HTTP date precision of whole seconds and ignore invalid dates. Support one
quoted ETag or `*`; weak tags and lists of validators are outside this subset.
Dates at or after modification time yield 304 for `If-Modified-Since`; dates
before it yield 412 for `If-Unmodified-Since`, subject to the paired precedence
above. 304 and 412 responses have no body; 304 includes the current ETag.

## Listing and pagination

Bytewise sorted keys; parameters `prefix`, `delimiter`, `max-keys` (default
1000), opaque continuation token. `CommonPrefixes` count toward the page limit.
Echo the listing prefix/delimiter, using empty strings when omitted. `KeyCount`
includes objects and grouped prefixes. When present, `list-type` must be `2`;
other values return 400.

- Group delimiter prefixes before applying the page limit.
- Order keys and grouped prefixes bytewise and continue without duplicates or
  omissions.
- Compute page boundaries from the combined bytewise ordering; XML may
  serialize objects and prefixes in separate groups, sorted within each group.
- Tokens are valid only for the same bucket/prefix/delimiter query. Malformed
  or mismatched tokens return 400.
- `max-keys` is a decimal integer from 1 through 1000 inclusive; omitted means
  1000, other values return 400. An explicitly empty `max-keys` is invalid.
- Tokens remain usable across restart when the query and namespace are
  unchanged. Namespace mutation between pages is outside the contract.
- `NextContinuationToken` is required only when truncated; it is otherwise
  absent.

## Buckets and keys

- Bucket names are non-empty UTF-8 strings with no slash. `.`, `..` and
  `health` are reserved. Deleting a bucket with objects or live uploads returns
  409.
- Object keys are UTF-8 with slashes allowed. Decode URL escaping exactly once.
  `a` and `a/b` can coexist; identical keys in different buckets are
  independent. For example, URL path `%252F` names a literal `%2F`, not `/`.
- Accepting a traversal-looking key must not alias another key, overwrite
  unrelated data, or escape the data directory. You may reject it with 400 or
  store it safely.

## Persistence, publication and concurrency

- The internal format is free; there is no compatibility requirement with seed
  v0 data.
- Body and metadata must publish as one coherent version. In-place metadata and
  direct writes to the final object path are not sufficient.
- Per-key writes have an atomic publication order consistent with
  non-overlapping operations. Readers see a complete version. Writes to
  different keys are not lost.
- If write A finishes before B starts, B replaces A. Overlapping writes may
  publish in either order, consistent with observations. A final non-overlapping
  write must be visible everywhere after it succeeds.
- An incomplete PUT (a body shorter than its declared length) cannot replace
  an existing object. A disconnected client that already sent the entire body
  may still have committed; do not confuse this with an incomplete-body
  failure.
- An incomplete upload must not block reads of an existing object or
  operations on unrelated keys beyond the published progress deadline.
- Process kill during publication leaves the old or new complete version. A
  write acknowledged before the kill must survive. Power-loss durability is not
  graded.
- All server state, including temporary and staging files, lives under
  `--data`. If a write fails because the data filesystem is full or for any
  other operational reason, respond 500 `InternalError` or close the connection
  without a success status; leave the published version of every key and every
  upload session unchanged; release the temporary space the failed write
  consumed so that later writes which fit can succeed; keep serving other
  requests; and after a restart load the committed state. Never acknowledge a
  write whose bytes were not completely stored.
- On restart, load the committed state. A genuinely new directory starts empty.
  A malformed existing manifest or missing referenced data is an explicit
  startup error; do not silently initialize a fresh store over damaged state.

## Multipart transitions

- Upload IDs are scoped to their bucket/key.
- Parts may be any body size; part numbers are positive and re-uploading a part
  replaces it.
- Same-session mutations serialize. A part re-upload or abort cannot change the
  parts a successful completion validated. If completion wins, a concurrent
  abort or re-upload returns 404; if abort wins, completion returns 404 and the
  published object is unchanged. These races must not produce a 5xx or a mixed
  version.
- Completion supplies a non-empty list of unique part numbers and current
  ETags. Missing parts, duplicate numbers or stale ETags return 400 without
  changing the object or upload, so a corrected completion can succeed.
- Completion validates the entire supplied list before touching the object,
  assembles only the requested parts in numeric order, and consumes the upload.
- Abort removes the upload without changing the published object.
- Operations using an aborted, completed or unknown upload ID return 404.
- In-progress multipart session recovery and orphan cleanup after a kill are not
  graded. The published object and subsequent ordinary operations are.

## Batch operations

`POST /b?batch` applies up to 32 copy, rename and delete operations to keys of
one bucket as a single atomic change. The request body is XML:

```xml
<Batch>
  <Copy><Source>KEY</Source><Target>KEY</Target><IfMatch>"MD5"</IfMatch></Copy>
  <Rename><Source>KEY</Source><Target>KEY</Target></Rename>
  <Delete><Key>KEY</Key><IfMatch>*</IfMatch></Delete>
</Batch>
```

- Operations apply in document order to a working copy of the bucket's
  committed namespace taken when the batch commits, so a later operation sees
  the effects of earlier operations in the same batch.
- `Copy`: `Source` must exist in the working copy. `Target` receives Source's
  bytes, ETag and Content-Type; its Last-Modified is the batch commit time. An
  existing Target is replaced. Source and Target may not be equal.
- `Rename`: a Copy followed by removal of Source.
- `Delete`: removes `Key`. Without `IfMatch`, deleting an absent key is a
  no-op. With `IfMatch`, the key must exist and match.
- `IfMatch` is optional on every operation and names the operation's Source or
  Key. Its value is one quoted ETag, which must equal that key's current ETag
  in the working copy, or `*`, which only requires the key to exist.
- Any failed `IfMatch` or any Copy/Rename whose Source is absent fails the
  whole batch with 412 `PreconditionFailed` and changes nothing.
- Malformed XML, zero operations, more than 32 operations, an unknown
  operation element, a missing or empty Source/Target/Key, or Source equal to
  Target returns 400 `InvalidArgument` and changes nothing. An absent bucket
  returns 404.
- Success is 200 with an empty body. Afterwards every touched key is readable
  with the stated ETag, Content-Type, Content-Length and bytes, including
  range and conditional requests, and listings report the new sizes and ETags.
- Atomicity: a batch publishes as one version change for every key it
  touches. Any single GET, HEAD or listing request observes either all of a
  batch's effects or none of them. A batch acknowledged with 200 survives a
  process kill; a batch interrupted before acknowledgment leaves either all or
  none of its effects after restart.
- Concurrency: batches, ordinary writes and multipart completions on the same
  bucket serialize in some order consistent with every observation; they never
  produce a 5xx, a mixed version or a deadlock.
- Independence: after a copy or rename, later writes or deletes of either key
  never change the other key's bytes, ETag or Content-Type. Whether a copy
  shares storage with its source internally is your choice; independence and
  survival across restarts are graded.
- Multipart sessions are not affected by batches. A session on a key stays
  usable after that key is renamed, copied over or deleted, and a later
  completion replaces whatever is then at the key.

## XML shapes

XML uses UTF-8, no namespaces, and `Content-Type: application/xml`. Whitespace,
XML declarations and sibling field order are not significant. Uppercase values
are substitutions, not literal expected data.

```xml
<ListAllMyBucketsResult>
  <Buckets><Bucket><Name>BUCKET</Name></Bucket></Buckets>
</ListAllMyBucketsResult>

<ListBucketResult>
  <Name>BUCKET</Name><Prefix>PREFIX</Prefix><Delimiter>/</Delimiter>
  <MaxKeys>2</MaxKeys><KeyCount>2</KeyCount><IsTruncated>true</IsTruncated>
  <Contents><Key>KEY</Key><Size>3</Size><ETag>"MD5"</ETag>
    <LastModified>2026-01-01T00:00:00Z</LastModified></Contents>
  <CommonPrefixes><Prefix>GROUP/</Prefix></CommonPrefixes>
  <NextContinuationToken>TOKEN</NextContinuationToken>
</ListBucketResult>

<InitiateMultipartUploadResult>
  <Bucket>BUCKET</Bucket><Key>KEY</Key><UploadId>ID</UploadId>
</InitiateMultipartUploadResult>

<ListPartsResult>
  <Bucket>BUCKET</Bucket><Key>KEY</Key><UploadId>ID</UploadId>
  <Part><PartNumber>1</PartNumber><ETag>"MD5"</ETag><Size>3</Size></Part>
</ListPartsResult>

<CompleteMultipartUpload>
  <Part><PartNumber>1</PartNumber><ETag>"MD5"</ETag></Part>
</CompleteMultipartUpload>

<CompleteMultipartUploadResult>
  <Bucket>BUCKET</Bucket><Key>KEY</Key><ETag>"FULL_BODY_MD5"</ETag>
</CompleteMultipartUploadResult>

<Batch>
  <Copy><Source>KEY</Source><Target>KEY</Target><IfMatch>"MD5"</IfMatch></Copy>
  <Rename><Source>KEY</Source><Target>KEY</Target></Rename>
  <Delete><Key>KEY</Key></Delete>
</Batch>

<Error><Code>NoSuchKey</Code><Message>Human-readable explanation</Message></Error>
```

Repeat `Bucket`, `Contents`, `CommonPrefixes` and `Part` elements as needed;
empty collections have no entries. XML object timestamps are UTC RFC3339 at
whole-second precision.

Errors 400/404/409/405/412/500 use the error XML shape above, except HEAD never
has a body. Codes are `InvalidArgument`,
`NoSuchBucket`/`NoSuchKey`/`NoSuchUpload`, `BucketNotEmpty`,
`MethodNotAllowed`, `PreconditionFailed`, and `InternalError`, respectively.
Messages must be non-empty but their wording is not graded. If several inputs
are invalid, no particular validation order is required. Operational failures
must not be reported as success or reset the store silently.

## CLI contract

Use `locker [--endpoint URL] COMMAND ...`; the flag overrides
`LOCKER_ENDPOINT`, which overrides `http://127.0.0.1:9000`. Server mode uses its
own documented `--data` and `--addr` flags. Client commands never access the
data directory. The CLI goes through HTTP.

| Command | Required behavior |
|---|---|
| `mb s3://b` / `rb s3://b` | Create/delete bucket through HTTP |
| `cp LOCAL s3://b/k` | Stream upload; no automatic bucket creation |
| `cp s3://b/k LOCAL` | Stream download, preserving exact bytes |
| `ls s3://b/prefix` | List all matching objects, following continuation tokens |
| `rm s3://b/k` | Delete object through HTTP |

CLI S3 URIs use the same percent-escaping convention as HTTP. `ls` prints one
JSON object per line with `key`, `size` and `etag`, bytewise sorted; no headers,
colors or diagnostic text on stdout. An empty listing produces no lines. Other
successful client commands are silent on stdout. Exit 0 on success, 1 on
HTTP/network/local-file failure, and 2 on invalid CLI usage. Errors go to
stderr and are non-empty. Recursive copy, stdin/stdout transfer and
remote-to-remote copy are outside scope.

## Completion

When you stop, write `/work/COMPLETION.md` with a short summary of what you
implemented, what you tested, and any known gaps. Do not claim success for
behavior you did not verify.
