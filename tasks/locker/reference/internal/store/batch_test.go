package store

import (
	"bytes"
	"errors"
	"io"
	"os"
	"strings"
	"testing"
)

func newBatchStore(t *testing.T) *Store {
	t.Helper()
	s, err := Open(t.TempDir())
	if err != nil {
		t.Fatalf("Open: %v", err)
	}
	if err := s.CreateBucket("b"); err != nil {
		t.Fatalf("CreateBucket: %v", err)
	}
	return s
}

func putText(t *testing.T, s *Store, key, body string) Object {
	t.Helper()
	object, err := s.PutObject("b", key, strings.NewReader(body), int64(len(body)), "text/plain")
	if err != nil {
		t.Fatalf("PutObject %s: %v", key, err)
	}
	return object
}

func readKey(t *testing.T, s *Store, key string) string {
	t.Helper()
	_, file, err := s.GetObject("b", key)
	if err != nil {
		t.Fatalf("GetObject %s: %v", key, err)
	}
	defer file.Close()
	data, err := io.ReadAll(file)
	if err != nil {
		t.Fatalf("read %s: %v", key, err)
	}
	return string(data)
}

func TestBatchInOrderWorkingCopy(t *testing.T) {
	s := newBatchStore(t)
	a := putText(t, s, "a", "A1")

	if err := s.Batch("b", []BatchOp{
		{Kind: "copy", Source: "a", Target: "h"},
		{Kind: "delete", Key: "h", IfMatch: a.ETag},
	}); err != nil {
		t.Fatalf("in-order batch: %v", err)
	}
	if _, err := s.HeadObject("b", "h"); !errors.Is(err, ErrNoSuchKey) {
		t.Fatalf("h should be absent, got %v", err)
	}

	if err := s.Batch("b", []BatchOp{
		{Kind: "copy", Source: "a", Target: "h"},
		{Kind: "delete", Key: "h", IfMatch: `"00000000000000000000000000000000"`},
	}); !errors.Is(err, ErrPrecondition) {
		t.Fatalf("expected ErrPrecondition, got %v", err)
	}
	if got := readKey(t, s, "a"); got != "A1" {
		t.Fatalf("a changed after failed batch: %q", got)
	}
	if _, err := s.HeadObject("b", "h"); !errors.Is(err, ErrNoSuchKey) {
		t.Fatalf("h should still be absent, got %v", err)
	}
}

func TestBatchPreconditionLeavesManifestUnchanged(t *testing.T) {
	s := newBatchStore(t)
	putText(t, s, "a", "A")
	before, err := os.ReadFile(s.manifestPath)
	if err != nil {
		t.Fatalf("read manifest: %v", err)
	}
	if err := s.Batch("b", []BatchOp{
		{Kind: "copy", Source: "a", Target: "b"},
		{Kind: "delete", Key: "a", IfMatch: `"not-the-etag"`},
	}); !errors.Is(err, ErrPrecondition) {
		t.Fatalf("expected ErrPrecondition, got %v", err)
	}
	after, err := os.ReadFile(s.manifestPath)
	if err != nil {
		t.Fatalf("read manifest: %v", err)
	}
	if !bytes.Equal(before, after) {
		t.Fatal("manifest changed after a failed batch")
	}
	if _, err := s.HeadObject("b", "b"); !errors.Is(err, ErrNoSuchKey) {
		t.Fatalf("b should be absent, got %v", err)
	}
}

func TestBatchRenameChain(t *testing.T) {
	s := newBatchStore(t)
	putText(t, s, "r0", "chain")
	if err := s.Batch("b", []BatchOp{
		{Kind: "rename", Source: "r0", Target: "r1"},
		{Kind: "rename", Source: "r1", Target: "r2"},
		{Kind: "rename", Source: "r2", Target: "r3"},
	}); err != nil {
		t.Fatalf("rename chain: %v", err)
	}
	if got := readKey(t, s, "r3"); got != "chain" {
		t.Fatalf("r3 = %q", got)
	}
	for _, key := range []string{"r0", "r1", "r2"} {
		if _, err := s.HeadObject("b", key); !errors.Is(err, ErrNoSuchKey) {
			t.Fatalf("%s should be absent, got %v", key, err)
		}
	}
}

func TestBatchDeleteAbsent(t *testing.T) {
	s := newBatchStore(t)
	if err := s.Batch("b", []BatchOp{{Kind: "delete", Key: "nothing"}}); err != nil {
		t.Fatalf("delete absent without IfMatch: %v", err)
	}
	if err := s.Batch("b", []BatchOp{{Kind: "delete", Key: "nothing", IfMatch: "*"}}); !errors.Is(err, ErrPrecondition) {
		t.Fatalf("wildcard on absent key: expected ErrPrecondition, got %v", err)
	}
}

func TestBatchOrphanedCopyKeepsBytes(t *testing.T) {
	s := newBatchStore(t)
	putText(t, s, "a", "original")
	if err := s.Batch("b", []BatchOp{{Kind: "copy", Source: "a", Target: "c"}}); err != nil {
		t.Fatalf("copy: %v", err)
	}
	if err := s.DeleteObject("b", "a"); err != nil {
		t.Fatalf("delete source: %v", err)
	}
	if got := readKey(t, s, "c"); got != "original" {
		t.Fatalf("copy lost its bytes: %q", got)
	}
	putText(t, s, "a", "replacement")
	if got := readKey(t, s, "c"); got != "original" {
		t.Fatalf("copy changed with source: %q", got)
	}
}

type failingWriter struct{}

func (failingWriter) Write([]byte) (int, error) { return 0, errors.New("disk full") }

func TestErrWriterRecordsStorageError(t *testing.T) {
	ew := &errWriter{w: failingWriter{}}
	if _, err := ew.Write([]byte("data")); err == nil {
		t.Fatal("expected the write error to propagate")
	}
	if ew.err == nil {
		t.Fatal("errWriter did not record the storage error")
	}
}

func TestOpenRejectsUnknownFault(t *testing.T) {
	t.Setenv("LOCKER_FAULT", "unknown-name")
	if _, err := Open(t.TempDir()); err == nil {
		t.Fatal("Open accepted an unknown LOCKER_FAULT")
	}
}

func TestOpenAcceptsKnownFaults(t *testing.T) {
	for _, name := range []string{
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
		"metadata-before-blob-amp",
	} {
		t.Run(name, func(t *testing.T) {
			t.Setenv("LOCKER_FAULT", name)
			if _, err := Open(t.TempDir()); err != nil {
				t.Fatalf("Open rejected %q: %v", name, err)
			}
		})
	}
}
