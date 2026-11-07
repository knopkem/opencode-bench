package server

import (
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"locker/internal/store"
)

func newTestServer(t *testing.T) (*httptest.Server, *store.Store) {
	t.Helper()
	backend, err := store.Open(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	return httptest.NewServer(New(backend, nil)), backend
}

func TestBucketObjectRoundTrip(t *testing.T) {
	ts, _ := newTestServer(t)
	defer ts.Close()
	client := ts.Client()

	request, _ := http.NewRequest(http.MethodPut, ts.URL+"/bucket", nil)
	response, err := client.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	response.Body.Close()
	if response.StatusCode != http.StatusOK {
		t.Fatalf("create bucket: %d", response.StatusCode)
	}

	request, _ = http.NewRequest(http.MethodPut, ts.URL+"/bucket/key", strings.NewReader("hello"))
	request.Header.Set("Content-Type", "text/plain")
	response, err = client.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	response.Body.Close()
	if response.StatusCode != http.StatusOK {
		t.Fatalf("put object: %d", response.StatusCode)
	}
	if got := response.Header.Get("ETag"); got != `"5d41402abc4b2a76b9719d911017c592"` {
		t.Fatalf("etag = %q", got)
	}

	response, err = client.Get(ts.URL + "/bucket/key")
	if err != nil {
		t.Fatal(err)
	}
	body, _ := io.ReadAll(response.Body)
	response.Body.Close()
	if string(body) != "hello" {
		t.Fatalf("body = %q", body)
	}
}

func TestWrongOpStatusMapsStorageErrorTo503(t *testing.T) {
	t.Setenv("LOCKER_FAULT", "wrong-op-status")
	backend, err := store.Open(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	srv := New(backend, nil)

	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodPut, "/bucket/key", nil)
	srv.writeStoreError(recorder, request, errors.New("disk failure"))

	if recorder.Code != http.StatusServiceUnavailable {
		t.Fatalf("status = %d, want 503", recorder.Code)
	}
	body, _ := io.ReadAll(recorder.Body)
	if !strings.Contains(string(body), "InternalError") {
		t.Fatalf("error body = %q, want InternalError code", body)
	}
}
