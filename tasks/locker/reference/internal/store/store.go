package store

import (
	"crypto/md5"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"sync"
	"time"
)

// Errors are mapped to the documented Locker error responses by the server.
var (
	ErrNoSuchBucket   = errors.New("no such bucket")
	ErrNoSuchKey      = errors.New("no such key")
	ErrNoSuchUpload   = errors.New("no such upload")
	ErrBucketNotEmpty = errors.New("bucket not empty")
	ErrIncomplete     = errors.New("incomplete request body")
	ErrInvalid        = errors.New("invalid argument")
	// ErrPrecondition reports a failed IfMatch or an absent batch Source.
	ErrPrecondition = errors.New("precondition failed")
)

// Object is one immutable, published object version.
type Object struct {
	Blob        string    `json:"blob"`
	Size        int64     `json:"size"`
	ETag        string    `json:"etag"`
	ContentType string    `json:"content_type"`
	ModTime     time.Time `json:"mod_time"`
}

// Bucket contains the object map. Keys are never interpreted as paths.
type Bucket struct {
	Objects map[string]Object `json:"objects"`
}

// Manifest is the atomically replaced publication document.
type Manifest struct {
	Buckets map[string]Bucket `json:"buckets"`
}

// Part is a stored multipart part.
type Part struct {
	Blob string
	ETag string
	Size int64
}

// Upload is a process-local multipart session.
type Upload struct {
	mu          sync.Mutex
	ID          string
	Bucket      string
	Key         string
	ContentType string
	Dir         string
	Parts       map[int]Part
	Dead        bool
}

// Store is a small local object store built from immutable blobs and one
// atomically renamed manifest.
type Store struct {
	root         string
	manifestPath string
	blobDir      string
	stageDir     string
	multipartDir string

	mu       sync.Mutex
	snapshot Manifest
	uploads  map[string]*Upload

	// fault is an author-only negative-control switch. It is never used by
	// candidate code and is absent from the seed and PROMPT.md.
	fault string
}

// CompletionPart is one entry from a CompleteMultipartUpload request.
type CompletionPart struct {
	PartNumber int
	ETag       string
}

// Open loads an existing store or initializes a genuinely new directory.
func Open(root string) (*Store, error) {
	root = filepath.Clean(root)
	fault := os.Getenv("LOCKER_FAULT")
	if !knownFault(fault) {
		return nil, fmt.Errorf("unknown LOCKER_FAULT %q", fault)
	}
	s := &Store{
		root:         root,
		manifestPath: filepath.Join(root, "manifest.json"),
		blobDir:      filepath.Join(root, "blobs"),
		stageDir:     filepath.Join(root, "staging"),
		multipartDir: filepath.Join(root, "multipart"),
		uploads:      map[string]*Upload{},
		fault:        fault,
	}

	data, err := os.ReadFile(s.manifestPath)
	if errors.Is(err, os.ErrNotExist) {
		entries, readErr := os.ReadDir(root)
		if readErr != nil && !errors.Is(readErr, os.ErrNotExist) {
			return nil, readErr
		}
		for _, entry := range entries {
			switch entry.Name() {
			case "blobs", "staging", "multipart":
				continue
			default:
				return nil, fmt.Errorf("manifest.json is missing but %s exists", entry.Name())
			}
		}
		for _, dir := range []string{s.root, s.blobDir, s.stageDir, s.multipartDir} {
			if err := os.MkdirAll(dir, 0o755); err != nil {
				return nil, err
			}
		}
		s.snapshot = Manifest{Buckets: map[string]Bucket{}}
		return s, s.saveLocked(&s.snapshot)
	}
	if err != nil {
		return nil, err
	}
	if err := json.Unmarshal(data, &s.snapshot); err != nil {
		return nil, fmt.Errorf("malformed manifest: %w", err)
	}
	if s.snapshot.Buckets == nil {
		s.snapshot.Buckets = map[string]Bucket{}
	}
	for name, bucket := range s.snapshot.Buckets {
		if bucket.Objects == nil {
			bucket.Objects = map[string]Object{}
			s.snapshot.Buckets[name] = bucket
		}
		for key, object := range bucket.Objects {
			if object.Blob == "" {
				return nil, fmt.Errorf("manifest object %s/%s has no blob", name, key)
			}
			if _, statErr := os.Stat(filepath.Join(s.blobDir, object.Blob)); statErr != nil {
				return nil, fmt.Errorf("manifest object %s/%s references missing blob: %w", name, key, statErr)
			}
		}
	}
	for _, dir := range []string{s.blobDir, s.stageDir, s.multipartDir} {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			return nil, err
		}
	}
	return s, nil
}

func (s *Store) saveLocked(next *Manifest) error {
	data, err := json.MarshalIndent(next, "", "  ")
	if err != nil {
		return err
	}
	temp, err := os.CreateTemp(s.root, ".manifest-*")
	if err != nil {
		return err
	}
	tempName := temp.Name()
	ok := false
	defer func() {
		temp.Close()
		if !ok {
			os.Remove(tempName)
		}
	}()
	if _, err = temp.Write(data); err != nil {
		return err
	}
	if err = temp.Sync(); err != nil {
		return err
	}
	if err = temp.Close(); err != nil {
		return err
	}
	if err = os.Rename(tempName, s.manifestPath); err != nil {
		return err
	}
	ok = true
	return syncDir(s.root)
}

func syncDir(path string) error {
	dir, err := os.Open(path)
	if err != nil {
		return err
	}
	defer dir.Close()
	return dir.Sync()
}

func cloneBuckets(buckets map[string]Bucket) map[string]Bucket {
	next := make(map[string]Bucket, len(buckets))
	for name, bucket := range buckets {
		next[name] = bucket
	}
	return next
}

func cloneBucket(bucket Bucket) Bucket {
	objects := make(map[string]Object, len(bucket.Objects)+1)
	for key, object := range bucket.Objects {
		objects[key] = object
	}
	return Bucket{Objects: objects}
}

func randomName() (string, error) {
	raw := make([]byte, 16)
	if _, err := rand.Read(raw); err != nil {
		return "", err
	}
	return hex.EncodeToString(raw), nil
}

func quotedMD5(hexDigest string) string {
	return `"` + hexDigest + `"`
}

// CreateBucket creates a bucket idempotently.
func (s *Store) CreateBucket(name string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if _, exists := s.snapshot.Buckets[name]; exists {
		return nil
	}
	buckets := cloneBuckets(s.snapshot.Buckets)
	buckets[name] = Bucket{Objects: map[string]Object{}}
	next := Manifest{Buckets: buckets}
	if err := s.saveLocked(&next); err != nil {
		return err
	}
	s.snapshot = next
	return nil
}

// DeleteBucket deletes an empty bucket with no live uploads.
func (s *Store) DeleteBucket(name string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	bucket, exists := s.snapshot.Buckets[name]
	if !exists {
		return ErrNoSuchBucket
	}
	if len(bucket.Objects) > 0 {
		return ErrBucketNotEmpty
	}
	for _, upload := range s.uploads {
		if upload.Bucket == name && !upload.Dead {
			return ErrBucketNotEmpty
		}
	}
	buckets := cloneBuckets(s.snapshot.Buckets)
	delete(buckets, name)
	next := Manifest{Buckets: buckets}
	if err := s.saveLocked(&next); err != nil {
		return err
	}
	s.snapshot = next
	return nil
}

// BucketExists reports whether a bucket is currently committed.
func (s *Store) BucketExists(name string) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	_, ok := s.snapshot.Buckets[name]
	return ok
}

// ListBuckets returns bucket names in bytewise order.
func (s *Store) ListBuckets() []string {
	s.mu.Lock()
	defer s.mu.Unlock()
	names := make([]string, 0, len(s.snapshot.Buckets))
	for name := range s.snapshot.Buckets {
		names = append(names, name)
	}
	sort.Strings(names)
	return names
}

// Objects returns a copy of a bucket's object metadata.
func (s *Store) Objects(bucketName string) (map[string]Object, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	bucket, ok := s.snapshot.Buckets[bucketName]
	if !ok {
		return nil, ErrNoSuchBucket
	}
	objects := make(map[string]Object, len(bucket.Objects))
	for key, object := range bucket.Objects {
		objects[key] = object
	}
	return objects, nil
}

// PutObject streams the body to a private staging file, then publishes the
// immutable blob and its metadata through one manifest rename.
func (s *Store) PutObject(bucket, key string, body io.Reader, declared int64, contentType string) (Object, error) {
	if !s.BucketExists(bucket) {
		return Object{}, ErrNoSuchBucket
	}
	temp, err := os.CreateTemp(s.stageDir, "put-*")
	if err != nil {
		return Object{}, err
	}
	tempName := temp.Name()
	cleanup := true
	defer func() {
		temp.Close()
		if cleanup {
			os.Remove(tempName)
		}
	}()

	digest := md5.New()
	ew := &errWriter{w: temp}
	size, err := io.Copy(io.MultiWriter(ew, digest), body)
	ignoreWrite := s.fault == "ignore-write-error"
	if ignoreWrite {
		err = nil
		ew.err = nil
	}
	if ew.err != nil {
		if s.fault == "leak-temp-on-error" {
			cleanup = false
		}
		return Object{}, ew.err
	}
	if err != nil {
		return Object{}, ErrIncomplete
	}
	if declared >= 0 && size != declared && !ignoreWrite {
		return Object{}, ErrIncomplete
	}
	if syncErr := temp.Sync(); syncErr != nil && !ignoreWrite {
		return Object{}, syncErr
	}
	if closeErr := temp.Close(); closeErr != nil && !ignoreWrite {
		return Object{}, closeErr
	}
	name, err := randomName()
	if err != nil {
		return Object{}, err
	}
	blobPath := filepath.Join(s.blobDir, name)
	metadataFirst := s.fault == "metadata-before-blob" || s.fault == "metadata-before-blob-amp"
	if !metadataFirst {
		if err := os.Rename(tempName, blobPath); err != nil {
			return Object{}, err
		}
		cleanup = false
		if err := syncDir(s.blobDir); err != nil {
			return Object{}, err
		}
	}
	object := Object{
		Blob:        name,
		Size:        size,
		ETag:        quotedMD5(hex.EncodeToString(digest.Sum(nil))),
		ContentType: contentType,
		ModTime:     time.Now().UTC(),
	}
	if s.fault == "split-publication" && size > 0 {
		// Deliberately publish metadata that does not describe the blob.
		object.Size = size / 2
	}
	if metadataFirst {
		if err := s.commitObject(bucket, key, object); err != nil {
			return Object{}, err
		}
		if s.fault == "metadata-before-blob-amp" {
			time.Sleep(20 * time.Millisecond)
		}
		if err := os.Rename(tempName, blobPath); err != nil {
			return Object{}, err
		}
		cleanup = false
		if err := syncDir(s.blobDir); err != nil {
			return Object{}, err
		}
		return object, nil
	}
	if err := s.commitObject(bucket, key, object); err != nil {
		return Object{}, err
	}
	return object, nil
}

func (s *Store) commitObject(bucketName, key string, object Object) error {
	if s.fault == "lost-writes" && strings.HasSuffix(key, "7") {
		return nil
	}
	if s.fault == "stale-snapshot-clone" {
		return s.commitObjectStale(bucketName, key, object)
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	bucket, ok := s.snapshot.Buckets[bucketName]
	if !ok {
		return ErrNoSuchBucket
	}
	replaced := bucket.Objects[key]
	buckets := cloneBuckets(s.snapshot.Buckets)
	updated := cloneBucket(bucket)
	updated.Objects[key] = object
	buckets[bucketName] = updated
	next := Manifest{Buckets: buckets}
	if err := s.saveLocked(&next); err != nil {
		return err
	}
	s.snapshot = next
	if s.fault == "share-no-refcount" && replaced.Blob != "" && replaced.Blob != object.Blob {
		os.Remove(filepath.Join(s.blobDir, replaced.Blob))
	}
	return nil
}

// commitObjectStale is an author-only mutant: it clones the bucket before
// taking the write lock, so a concurrent commit between the two lock windows
// is lost.
func (s *Store) commitObjectStale(bucketName, key string, object Object) error {
	s.mu.Lock()
	bucket, ok := s.snapshot.Buckets[bucketName]
	if !ok {
		s.mu.Unlock()
		return ErrNoSuchBucket
	}
	stale := cloneBucket(bucket)
	s.mu.Unlock()

	runtime.Gosched()

	s.mu.Lock()
	defer s.mu.Unlock()
	stale.Objects[key] = object
	buckets := cloneBuckets(s.snapshot.Buckets)
	buckets[bucketName] = stale
	next := Manifest{Buckets: buckets}
	if err := s.saveLocked(&next); err != nil {
		return err
	}
	s.snapshot = next
	return nil
}

// GetObject resolves one version and opens its immutable blob before releasing
// the manifest mutex. The caller closes the file.
func (s *Store) GetObject(bucketName, key string) (Object, *os.File, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	bucket, ok := s.snapshot.Buckets[bucketName]
	if !ok {
		return Object{}, nil, ErrNoSuchBucket
	}
	object, ok := bucket.Objects[key]
	if !ok {
		return Object{}, nil, ErrNoSuchKey
	}
	file, err := os.Open(filepath.Join(s.blobDir, object.Blob))
	if err != nil {
		return Object{}, nil, err
	}
	return object, file, nil
}

// HeadObject resolves metadata without opening the blob.
func (s *Store) HeadObject(bucketName, key string) (Object, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	bucket, ok := s.snapshot.Buckets[bucketName]
	if !ok {
		return Object{}, ErrNoSuchBucket
	}
	object, ok := bucket.Objects[key]
	if !ok {
		return Object{}, ErrNoSuchKey
	}
	return object, nil
}

// DeleteObject deletes an object idempotently within an existing bucket.
func (s *Store) DeleteObject(bucketName, key string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	bucket, ok := s.snapshot.Buckets[bucketName]
	if !ok {
		return ErrNoSuchBucket
	}
	if _, ok := bucket.Objects[key]; !ok {
		return nil
	}
	removed := bucket.Objects[key]
	buckets := cloneBuckets(s.snapshot.Buckets)
	updated := cloneBucket(bucket)
	delete(updated.Objects, key)
	buckets[bucketName] = updated
	next := Manifest{Buckets: buckets}
	if err := s.saveLocked(&next); err != nil {
		return err
	}
	s.snapshot = next
	if s.fault == "share-no-refcount" && removed.Blob != "" {
		os.Remove(filepath.Join(s.blobDir, removed.Blob))
	}
	return nil
}

// BatchOp is one validated batch operation. Kind is "copy", "rename" or
// "delete". Source/Target are used by copy and rename; Key by delete.
// IfMatch is "" when absent, "*" or a quoted ETag otherwise.
type BatchOp struct {
	Kind    string
	Source  string
	Target  string
	Key     string
	IfMatch string
}

// Batch applies ops in order to a working copy of the bucket and publishes
// the result as a single manifest replacement, or changes nothing.
func (s *Store) Batch(bucketName string, ops []BatchOp) error {
	if s.fault == "precondition-at-parse" {
		return s.batchCheckThenAct(bucketName, ops)
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	bucket, ok := s.snapshot.Buckets[bucketName]
	if !ok {
		return ErrNoSuchBucket
	}
	working := cloneBucket(bucket) // shallow copy of the Objects map
	now := time.Now().UTC()
	perOp := s.fault == "per-op-commit"
	for _, op := range ops {
		switch op.Kind {
		case "copy", "rename":
			src, ok := working.Objects[op.Source]
			if !ok || !ifMatchOK(op.IfMatch, src.ETag) {
				return ErrPrecondition
			}
			dst := src        // shares Blob, Size, ETag, ContentType
			dst.ModTime = now // Last-Modified is the batch commit time
			working.Objects[op.Target] = dst
			if op.Kind == "rename" {
				delete(working.Objects, op.Source)
			}
		case "delete":
			cur, ok := working.Objects[op.Key]
			if op.IfMatch != "" && (!ok || !ifMatchOK(op.IfMatch, cur.ETag)) {
				return ErrPrecondition
			}
			delete(working.Objects, op.Key)
		default:
			return ErrInvalid
		}
		if perOp {
			buckets := cloneBuckets(s.snapshot.Buckets)
			buckets[bucketName] = cloneBucket(working)
			next := Manifest{Buckets: buckets}
			if err := s.saveLocked(&next); err != nil {
				return err
			}
			s.snapshot = next
		}
	}
	if perOp {
		return nil
	}
	buckets := cloneBuckets(s.snapshot.Buckets)
	buckets[bucketName] = working
	next := Manifest{Buckets: buckets}
	if err := s.saveLocked(&next); err != nil {
		return err
	}
	s.snapshot = next
	return nil
}

// batchCheckThenAct is an author-only mutant: it evaluates every precondition
// against the committed state, drops the lock, then applies the operations
// without re-checking them.
func (s *Store) batchCheckThenAct(bucketName string, ops []BatchOp) error {
	s.mu.Lock()
	bucket, ok := s.snapshot.Buckets[bucketName]
	if !ok {
		s.mu.Unlock()
		return ErrNoSuchBucket
	}
	committed := cloneBucket(bucket)
	s.mu.Unlock()

	for _, op := range ops {
		switch op.Kind {
		case "copy", "rename":
			src, ok := committed.Objects[op.Source]
			if !ok || !ifMatchOK(op.IfMatch, src.ETag) {
				return ErrPrecondition
			}
		case "delete":
			cur, ok := committed.Objects[op.Key]
			if op.IfMatch != "" && (!ok || !ifMatchOK(op.IfMatch, cur.ETag)) {
				return ErrPrecondition
			}
		default:
			return ErrInvalid
		}
	}

	s.mu.Lock()
	defer s.mu.Unlock()
	bucket, ok = s.snapshot.Buckets[bucketName]
	if !ok {
		return ErrNoSuchBucket
	}
	working := cloneBucket(bucket)
	now := time.Now().UTC()
	for _, op := range ops {
		switch op.Kind {
		case "copy", "rename":
			src, ok := working.Objects[op.Source]
			if !ok {
				continue
			}
			dst := src
			dst.ModTime = now
			working.Objects[op.Target] = dst
			if op.Kind == "rename" {
				delete(working.Objects, op.Source)
			}
		case "delete":
			delete(working.Objects, op.Key)
		default:
			return ErrInvalid
		}
	}
	buckets := cloneBuckets(s.snapshot.Buckets)
	buckets[bucketName] = working
	next := Manifest{Buckets: buckets}
	if err := s.saveLocked(&next); err != nil {
		return err
	}
	s.snapshot = next
	return nil
}

func ifMatchOK(ifMatch, etag string) bool {
	if ifMatch == "" || ifMatch == "*" {
		return true
	}
	return normalizeETag(ifMatch) == normalizeETag(etag)
}

// Initiate starts a process-local multipart session.
func (s *Store) Initiate(bucket, key, contentType string) (string, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if _, ok := s.snapshot.Buckets[bucket]; !ok {
		return "", ErrNoSuchBucket
	}
	id, err := randomName()
	if err != nil {
		return "", err
	}
	dir := filepath.Join(s.multipartDir, id)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return "", err
	}
	s.uploads[id] = &Upload{
		ID:          id,
		Bucket:      bucket,
		Key:         key,
		ContentType: contentType,
		Dir:         dir,
		Parts:       map[int]Part{},
	}
	return id, nil
}

func (s *Store) lookupUpload(bucket, key, id string) (*Upload, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	upload := s.uploads[id]
	if upload == nil || upload.Dead || upload.Bucket != bucket || upload.Key != key {
		return nil, ErrNoSuchUpload
	}
	return upload, nil
}

// UploadPart streams one part before taking the session lock.
func (s *Store) UploadPart(bucket, key, id string, number int, body io.Reader, declared int64) (string, error) {
	upload, err := s.lookupUpload(bucket, key, id)
	if err != nil {
		return "", err
	}
	temp, err := os.CreateTemp(upload.Dir, "part-*")
	if err != nil {
		return "", err
	}
	tempName := temp.Name()
	cleanup := true
	defer func() {
		temp.Close()
		if cleanup {
			os.Remove(tempName)
		}
	}()
	if s.fault == "phantom-part" {
		upload.mu.Lock()
		if !upload.Dead {
			upload.Parts[number] = Part{Blob: tempName, ETag: quotedMD5("phantom"), Size: 0}
		}
		upload.mu.Unlock()
	}
	digest := md5.New()
	ew := &errWriter{w: temp}
	size, err := io.Copy(io.MultiWriter(ew, digest), body)
	if ew.err != nil {
		if s.fault == "leak-temp-on-error" {
			cleanup = false
		}
		return "", ew.err
	}
	if err != nil || (declared >= 0 && size != declared) {
		return "", ErrIncomplete
	}
	if err := temp.Sync(); err != nil {
		return "", err
	}
	if err := temp.Close(); err != nil {
		return "", err
	}
	etag := quotedMD5(hex.EncodeToString(digest.Sum(nil)))

	upload.mu.Lock()
	defer upload.mu.Unlock()
	if upload.Dead {
		return "", ErrNoSuchUpload
	}
	name, err := randomName()
	if err != nil {
		return "", err
	}
	partName := filepath.Join(upload.Dir, fmt.Sprintf("%d-%s", number, name))
	if err := os.Rename(tempName, partName); err != nil {
		return "", err
	}
	cleanup = false
	upload.Parts[number] = Part{Blob: partName, ETag: etag, Size: size}
	return etag, nil
}

// ListParts returns a copy of the session's parts.
func (s *Store) ListParts(bucket, key, id string) ([]int, map[int]Part, error) {
	upload, err := s.lookupUpload(bucket, key, id)
	if err != nil {
		return nil, nil, err
	}
	upload.mu.Lock()
	defer upload.mu.Unlock()
	if upload.Dead {
		return nil, nil, ErrNoSuchUpload
	}
	numbers := make([]int, 0, len(upload.Parts))
	parts := make(map[int]Part, len(upload.Parts))
	for number, part := range upload.Parts {
		numbers = append(numbers, number)
		parts[number] = part
	}
	sort.Ints(numbers)
	return numbers, parts, nil
}

// Complete validates the entire supplied list before assembling or publishing.
func (s *Store) Complete(bucket, key, id string, requested []CompletionPart) (Object, error) {
	upload, err := s.lookupUpload(bucket, key, id)
	if err != nil {
		return Object{}, err
	}
	upload.mu.Lock()
	defer upload.mu.Unlock()
	if upload.Dead {
		return Object{}, ErrNoSuchUpload
	}
	if len(requested) == 0 {
		return Object{}, ErrInvalid
	}
	seen := map[int]bool{}
	numbers := make([]int, 0, len(requested))
	invalid := false
	for _, item := range requested {
		if item.PartNumber < 1 || item.PartNumber > 10000 || seen[item.PartNumber] {
			invalid = true
			break
		}
		part, ok := upload.Parts[item.PartNumber]
		if !ok || normalizeETag(part.ETag) != normalizeETag(item.ETag) {
			invalid = true
			break
		}
		seen[item.PartNumber] = true
		numbers = append(numbers, item.PartNumber)
	}
	if invalid {
		if s.fault == "validate-after-replace" {
			s.publishFaultPart(upload, requested)
		}
		return Object{}, ErrInvalid
	}
	sort.Ints(numbers)

	temp, err := os.CreateTemp(s.stageDir, "complete-*")
	if err != nil {
		return Object{}, err
	}
	tempName := temp.Name()
	cleanup := true
	defer func() {
		temp.Close()
		if cleanup {
			os.Remove(tempName)
		}
	}()
	digest := md5.New()
	ew := &errWriter{w: temp}
	var size int64
	for _, number := range numbers {
		part := upload.Parts[number]
		file, err := os.Open(part.Blob)
		if err != nil {
			return Object{}, err
		}
		written, copyErr := io.Copy(io.MultiWriter(ew, digest), file)
		file.Close()
		if ew.err != nil {
			return Object{}, ew.err
		}
		if copyErr != nil {
			return Object{}, copyErr
		}
		size += written
	}
	if err := temp.Sync(); err != nil {
		return Object{}, err
	}
	if err := temp.Close(); err != nil {
		return Object{}, err
	}
	name, err := randomName()
	if err != nil {
		return Object{}, err
	}
	if err := os.Rename(tempName, filepath.Join(s.blobDir, name)); err != nil {
		return Object{}, err
	}
	cleanup = false
	if err := syncDir(s.blobDir); err != nil {
		return Object{}, err
	}
	object := Object{
		Blob:        name,
		Size:        size,
		ETag:        quotedMD5(hex.EncodeToString(digest.Sum(nil))),
		ContentType: upload.ContentType,
		ModTime:     time.Now().UTC(),
	}
	if err := s.commitObject(bucket, key, object); err != nil {
		return Object{}, err
	}

	upload.Dead = true
	s.mu.Lock()
	if s.uploads[id] == upload {
		delete(s.uploads, id)
	}
	s.mu.Unlock()
	os.RemoveAll(upload.Dir)
	return object, nil
}

// Abort removes a session without touching the published object.
func (s *Store) Abort(bucket, key, id string) error {
	upload, err := s.lookupUpload(bucket, key, id)
	if err != nil {
		return err
	}
	upload.mu.Lock()
	defer upload.mu.Unlock()
	if upload.Dead {
		return ErrNoSuchUpload
	}
	upload.Dead = true
	s.mu.Lock()
	if s.uploads[id] == upload {
		delete(s.uploads, id)
	}
	s.mu.Unlock()
	os.RemoveAll(upload.Dir)
	return nil
}

// publishFaultPart is an author-only negative control: it mutates the target
// object before completion validation has succeeded.
func (s *Store) publishFaultPart(upload *Upload, requested []CompletionPart) {
	for _, item := range requested {
		part, ok := upload.Parts[item.PartNumber]
		if !ok {
			continue
		}
		source, err := os.Open(part.Blob)
		if err != nil {
			return
		}
		temp, err := os.CreateTemp(s.stageDir, "fault-*")
		if err != nil {
			source.Close()
			return
		}
		tempName := temp.Name()
		digest := md5.New()
		_, err = io.Copy(io.MultiWriter(temp, digest), source)
		source.Close()
		if err == nil {
			err = temp.Sync()
		}
		if closeErr := temp.Close(); err == nil {
			err = closeErr
		}
		if err != nil {
			os.Remove(tempName)
			return
		}
		name, err := randomName()
		if err != nil {
			os.Remove(tempName)
			return
		}
		if err := os.Rename(tempName, filepath.Join(s.blobDir, name)); err != nil {
			os.Remove(tempName)
			return
		}
		object := Object{
			Blob:        name,
			Size:        part.Size,
			ETag:        quotedMD5(hex.EncodeToString(digest.Sum(nil))),
			ContentType: upload.ContentType,
			ModTime:     time.Now().UTC(),
		}
		_ = s.commitObject(upload.Bucket, upload.Key, object)
		return
	}
}

// errWriter records the first write error so a storage write failure is not
// mistaken for an incomplete client body.
type errWriter struct {
	w   io.Writer
	err error
}

func (e *errWriter) Write(p []byte) (int, error) {
	n, err := e.w.Write(p)
	if err != nil {
		e.err = err
	}
	return n, err
}

func knownFault(name string) bool {
	switch name {
	case "",
		"split-publication",
		"lost-writes",
		"global-lock",
		"validate-after-replace",
		"per-op-commit",
		"precondition-at-parse",
		"share-no-refcount",
		"ack-before-publish",
		"ack-before-publish-amp",
		"leak-temp-on-error",
		"ignore-write-error",
		"phantom-part",
		"stale-snapshot-clone",
		"metadata-before-blob",
		"metadata-before-blob-amp":
		return true
	}
	return false
}

func normalizeETag(value string) string {
	if len(value) >= 2 && value[0] == '"' && value[len(value)-1] == '"' {
		return value[1 : len(value)-1]
	}
	return value
}
