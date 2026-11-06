package server

import (
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
