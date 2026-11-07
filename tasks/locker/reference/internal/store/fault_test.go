package store

import (
	"errors"
	"os"
	"strings"
	"testing"
)

// openFaultStore opens a store with the given author-only LOCKER_FAULT set.
func openFaultStore(t *testing.T, fault string) *Store {
	t.Helper()
	t.Setenv("LOCKER_FAULT", fault)
	s, err := Open(t.TempDir())
	if err != nil {
		t.Fatalf("Open with fault %q: %v", fault, err)
	}
	if err := s.CreateBucket("b"); err != nil {
		t.Fatalf("CreateBucket: %v", err)
	}
	return s
}

func countEntries(t *testing.T, dir string) int {
	t.Helper()
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatalf("ReadDir %s: %v", dir, err)
	}
	return len(entries)
}

// completeWithLostBucket drives Complete into a post-rename commitObject
// failure by removing the bucket from the in-memory snapshot after the upload
// session has been created. The manifest on disk is irrelevant to the error.
func completeWithLostBucket(t *testing.T, s *Store) error {
	t.Helper()
	id, err := s.Initiate("b", "k", "text/plain")
	if err != nil {
		t.Fatalf("Initiate: %v", err)
	}
	etag, err := s.UploadPart("b", "k", id, 1, strings.NewReader("payload"), 7)
	if err != nil {
		t.Fatalf("UploadPart: %v", err)
	}
	s.mu.Lock()
	delete(s.snapshot.Buckets, "b")
	s.mu.Unlock()
	_, err = s.Complete("b", "k", id, []CompletionPart{{PartNumber: 1, ETag: etag}})
	return err
}

type deleteBucketReader struct {
	store *Store
	base  *strings.Reader
	done  bool
}

func (r *deleteBucketReader) Read(p []byte) (int, error) {
	if !r.done {
		r.done = true
		r.store.mu.Lock()
		delete(r.store.snapshot.Buckets, "b")
		r.store.mu.Unlock()
	}
	return r.base.Read(p)
}

func putWithLostBucket(t *testing.T, s *Store) error {
	t.Helper()
	body := &deleteBucketReader{store: s, base: strings.NewReader("payload")}
	_, err := s.PutObject("b", "k", body, 7, "text/plain")
	return err
}

func TestPutDiscardsBlobOnCommitError(t *testing.T) {
	s := openFaultStore(t, "")
	if err := putWithLostBucket(t, s); !errors.Is(err, ErrNoSuchBucket) {
		t.Fatalf("expected ErrNoSuchBucket, got %v", err)
	}
	if n := countEntries(t, s.blobDir); n != 0 {
		t.Fatalf("orphaned blob left behind: blobs dir has %d entries", n)
	}
}

func TestPutKeepBlobOnCommitErrorLeavesOrphan(t *testing.T) {
	s := openFaultStore(t, "keep-blob-on-commit-error")
	if err := putWithLostBucket(t, s); !errors.Is(err, ErrNoSuchBucket) {
		t.Fatalf("expected ErrNoSuchBucket, got %v", err)
	}
	if n := countEntries(t, s.blobDir); n != 1 {
		t.Fatalf("keep-blob-on-commit-error should leak the blob, blobs dir has %d entries", n)
	}
}

func TestCompleteDiscardsBlobOnCommitError(t *testing.T) {
	s := openFaultStore(t, "")
	if err := completeWithLostBucket(t, s); !errors.Is(err, ErrNoSuchBucket) {
		t.Fatalf("expected ErrNoSuchBucket, got %v", err)
	}
	if n := countEntries(t, s.blobDir); n != 0 {
		t.Fatalf("orphaned blob left behind: blobs dir has %d entries", n)
	}
}

func TestKeepBlobOnCommitErrorLeavesOrphan(t *testing.T) {
	s := openFaultStore(t, "keep-blob-on-commit-error")
	if err := completeWithLostBucket(t, s); !errors.Is(err, ErrNoSuchBucket) {
		t.Fatalf("expected ErrNoSuchBucket, got %v", err)
	}
	if n := countEntries(t, s.blobDir); n != 1 {
		t.Fatalf("keep-blob-on-commit-error should leak the blob, blobs dir has %d entries", n)
	}
}

func TestConsumeUploadOnCompleteError(t *testing.T) {
	s := openFaultStore(t, "consume-upload-on-complete-error")
	id, err := s.Initiate("b", "k", "text/plain")
	if err != nil {
		t.Fatalf("Initiate: %v", err)
	}
	etag, err := s.UploadPart("b", "k", id, 1, strings.NewReader("payload"), 7)
	if err != nil {
		t.Fatalf("UploadPart: %v", err)
	}
	// Remove the part file so assembly fails after validation has passed.
	_, parts, err := s.ListParts("b", "k", id)
	if err != nil {
		t.Fatalf("ListParts: %v", err)
	}
	if err := os.Remove(parts[1].Blob); err != nil {
		t.Fatalf("remove part blob: %v", err)
	}
	if _, err := s.Complete("b", "k", id, []CompletionPart{{PartNumber: 1, ETag: etag}}); err == nil {
		t.Fatal("expected Complete to fail after the part file was removed")
	}
	// The failed completion consumed the session: every later operation
	// reports the upload missing and its directory is gone.
	if _, _, err := s.ListParts("b", "k", id); !errors.Is(err, ErrNoSuchUpload) {
		t.Fatalf("expected ErrNoSuchUpload after consumption, got %v", err)
	}
	if err := s.Abort("b", "k", id); !errors.Is(err, ErrNoSuchUpload) {
		t.Fatalf("expected ErrNoSuchUpload on abort, got %v", err)
	}
	s.mu.Lock()
	_, stillThere := s.uploads[id]
	s.mu.Unlock()
	if stillThere {
		t.Fatal("consumed upload still registered in s.uploads")
	}
	if _, err := os.Stat(parts[1].Blob); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("upload directory should be removed, stat err = %v", err)
	}
}

func TestConsumeUploadNotTriggeredByValidationError(t *testing.T) {
	s := openFaultStore(t, "consume-upload-on-complete-error")
	id, err := s.Initiate("b", "k", "text/plain")
	if err != nil {
		t.Fatalf("Initiate: %v", err)
	}
	// An empty completion list fails validation before staging begins; the
	// session must survive.
	if _, err := s.Complete("b", "k", id, nil); !errors.Is(err, ErrInvalid) {
		t.Fatalf("expected ErrInvalid, got %v", err)
	}
	if _, _, err := s.ListParts("b", "k", id); err != nil {
		t.Fatalf("upload should survive a validation error, got %v", err)
	}
}

func TestBatchDeepCopyStagingCleansTemp(t *testing.T) {
	s := openFaultStore(t, "leak-temp-on-error")
	putText(t, s, "a", "A1")

	if err := s.Batch("b", []BatchOp{{Kind: "copy", Source: "a", Target: "c"}}); err != nil {
		t.Fatalf("batch copy: %v", err)
	}
	if n := countEntries(t, s.stageDir); n != 0 {
		t.Fatalf("staging temp leaked after successful batch: %d entries", n)
	}
	if got := readKey(t, s, "c"); got != "A1" {
		t.Fatalf("batch semantics changed: c = %q", got)
	}

	// A non-leak batch error must also clean the staged temp.
	if err := s.Batch("b", []BatchOp{
		{Kind: "copy", Source: "a", Target: "d"},
		{Kind: "delete", Key: "a", IfMatch: `"not-the-etag"`},
	}); !errors.Is(err, ErrPrecondition) {
		t.Fatalf("expected ErrPrecondition, got %v", err)
	}
	if n := countEntries(t, s.stageDir); n != 0 {
		t.Fatalf("staging temp leaked after failed batch: %d entries", n)
	}
}

func TestBatchUnfaultedLeavesNoStagingTemp(t *testing.T) {
	s := newBatchStore(t)
	putText(t, s, "a", "A1")
	if err := s.Batch("b", []BatchOp{{Kind: "rename", Source: "a", Target: "r"}}); err != nil {
		t.Fatalf("batch rename: %v", err)
	}
	if n := countEntries(t, s.stageDir); n != 0 {
		t.Fatalf("unfaulted batch left staging entries: %d", n)
	}
	if got := readKey(t, s, "r"); got != "A1" {
		t.Fatalf("batch semantics changed: r = %q", got)
	}
}
