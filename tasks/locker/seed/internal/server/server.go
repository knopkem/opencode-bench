package server

import (
	"encoding/xml"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"locker/internal/store"
)

// Server is the seed's deliberately simple HTTP surface. Range, conditional
// and multipart support are not wired yet.
type Server struct {
	store *store.Store
	log   *log.Logger
}

func New(st *store.Store, logger *log.Logger) *Server {
	if logger == nil {
		logger = log.Default()
	}
	return &Server{store: st, log: logger}
}

func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path == "/health" {
		if r.Method != http.MethodGet && r.Method != http.MethodHead {
			writeError(w, r, http.StatusMethodNotAllowed, "MethodNotAllowed", "method not allowed")
			return
		}
		w.Header().Set("Content-Type", "text/plain; charset=utf-8")
		w.WriteHeader(http.StatusOK)
		if r.Method != http.MethodHead {
			_, _ = w.Write([]byte("ok"))
		}
		return
	}
	bucket, key, hasKey, err := splitTarget(r)
	if err != nil {
		writeError(w, r, http.StatusBadRequest, "InvalidArgument", err.Error())
		return
	}
	if bucket == "" {
		if r.Method != http.MethodGet && r.Method != http.MethodHead {
			writeError(w, r, http.StatusMethodNotAllowed, "MethodNotAllowed", "method not allowed")
			return
		}
		s.listBuckets(w, r)
		return
	}
	if !validBucket(bucket) {
		writeError(w, r, http.StatusBadRequest, "InvalidArgument", "invalid bucket name")
		return
	}
	if !hasKey {
		s.serveBucket(w, r, bucket)
		return
	}
	if !utf8.ValidString(key) {
		writeError(w, r, http.StatusBadRequest, "InvalidArgument", "invalid UTF-8 key")
		return
	}
	query := r.URL.Query()
	if _, ok := query["uploads"]; ok {
		writeError(w, r, http.StatusNotImplemented, "NotImplemented", "multipart is not implemented")
		return
	}
	if _, ok := query["uploadId"]; ok {
		writeError(w, r, http.StatusNotImplemented, "NotImplemented", "multipart is not implemented")
		return
	}
	if _, ok := query["partNumber"]; ok {
		writeError(w, r, http.StatusNotImplemented, "NotImplemented", "multipart is not implemented")
		return
	}
	s.serveObject(w, r, bucket, key)
}

func splitTarget(r *http.Request) (bucket, key string, hasKey bool, err error) {
	escaped := r.URL.EscapedPath()
	if escaped == "" {
		escaped = "/"
	}
	if !strings.HasPrefix(escaped, "/") {
		return "", "", false, errors.New("malformed path")
	}
	rest := escaped[1:]
	if rest == "" {
		return "", "", false, nil
	}
	rawBucket, rawKey, found := strings.Cut(rest, "/")
	bucket, err = url.PathUnescape(rawBucket)
	if err != nil || !utf8.ValidString(bucket) {
		return "", "", false, errors.New("malformed bucket encoding")
	}
	if !found {
		return bucket, "", false, nil
	}
	key, err = url.PathUnescape(rawKey)
	if err != nil || !utf8.ValidString(key) {
		return "", "", false, errors.New("malformed key encoding")
	}
	if key == "" {
		return "", "", false, errors.New("empty object key")
	}
	return bucket, key, true, nil
}

func validBucket(name string) bool {
	if name == "" || name == "." || name == ".." || name == "health" {
		return false
	}
	return !strings.Contains(name, "/") && utf8.ValidString(name)
}

func (s *Server) listBuckets(w http.ResponseWriter, r *http.Request) {
	type bucketName struct {
		Name string `xml:"Name"`
	}
	result := struct {
		XMLName xml.Name `xml:"ListAllMyBucketsResult"`
		Buckets struct {
			Bucket []bucketName `xml:"Bucket"`
		} `xml:"Buckets"`
	}{}
	for _, name := range s.store.ListBuckets() {
		result.Buckets.Bucket = append(result.Buckets.Bucket, bucketName{Name: name})
	}
	writeXML(w, r, http.StatusOK, result)
}

func (s *Server) serveBucket(w http.ResponseWriter, r *http.Request, bucket string) {
	switch r.Method {
	case http.MethodPut:
		if err := s.store.CreateBucket(bucket); err != nil {
			writeStoreError(w, r, err)
			return
		}
		w.WriteHeader(http.StatusOK)
	case http.MethodDelete:
		if err := s.store.DeleteBucket(bucket); err != nil {
			writeStoreError(w, r, err)
			return
		}
		w.WriteHeader(http.StatusNoContent)
	case http.MethodGet, http.MethodHead:
		s.listObjects(w, r, bucket)
	default:
		writeError(w, r, http.StatusMethodNotAllowed, "MethodNotAllowed", "method not allowed")
	}
}

func (s *Server) listObjects(w http.ResponseWriter, r *http.Request, bucket string) {
	query := r.URL.Query()
	prefix := query.Get("prefix")
	delimiter := query.Get("delimiter")
	objects, err := s.store.Objects(bucket)
	if err != nil {
		writeStoreError(w, r, err)
		return
	}
	keys := make([]string, 0, len(objects))
	for key := range objects {
		if strings.HasPrefix(key, prefix) {
			keys = append(keys, key)
		}
	}
	sort.Strings(keys)
	type content struct {
		Key          string `xml:"Key"`
		Size         int64  `xml:"Size"`
		ETag         string `xml:"ETag"`
		LastModified string `xml:"LastModified"`
	}
	result := struct {
		XMLName     xml.Name  `xml:"ListBucketResult"`
		Name        string    `xml:"Name"`
		Prefix      string    `xml:"Prefix"`
		Delimiter   string    `xml:"Delimiter"`
		MaxKeys     int       `xml:"MaxKeys"`
		KeyCount    int       `xml:"KeyCount"`
		IsTruncated bool      `xml:"IsTruncated"`
		Contents    []content `xml:"Contents"`
	}{
		Name:      bucket,
		Prefix:    prefix,
		Delimiter: delimiter,
		MaxKeys:   1000,
		KeyCount:  len(keys),
	}
	for _, key := range keys {
		object := objects[key]
		result.Contents = append(result.Contents, content{
			Key:          key,
			Size:         object.Size,
			ETag:         object.ETag,
			LastModified: object.ModTime.UTC().Format(time.RFC3339),
		})
	}
	writeXML(w, r, http.StatusOK, result)
}

func (s *Server) serveObject(w http.ResponseWriter, r *http.Request, bucket, key string) {
	switch r.Method {
	case http.MethodPut:
		contentType := r.Header.Get("Content-Type")
		if contentType == "" {
			contentType = "application/octet-stream"
		}
		object, err := s.store.PutObject(bucket, key, r.Body, r.ContentLength, contentType)
		if err != nil {
			writeStoreError(w, r, err)
			return
		}
		w.Header().Set("ETag", object.ETag)
		w.WriteHeader(http.StatusOK)
	case http.MethodGet, http.MethodHead:
		object, file, err := s.store.GetObject(bucket, key)
		if err != nil {
			writeStoreError(w, r, err)
			return
		}
		defer file.Close()
		w.Header().Set("ETag", object.ETag)
		w.Header().Set("Content-Type", object.ContentType)
		w.Header().Set("Content-Length", strconv.FormatInt(object.Size, 10))
		w.Header().Set("Last-Modified", object.ModTime.UTC().Format(http.TimeFormat))
		w.WriteHeader(http.StatusOK)
		if r.Method != http.MethodHead {
			_, _ = io.Copy(w, file)
		}
	case http.MethodDelete:
		if err := s.store.DeleteObject(bucket, key); err != nil {
			writeStoreError(w, r, err)
			return
		}
		w.WriteHeader(http.StatusNoContent)
	default:
		writeError(w, r, http.StatusMethodNotAllowed, "MethodNotAllowed", "method not allowed")
	}
}

func writeXML(w http.ResponseWriter, r *http.Request, status int, value any) {
	w.Header().Set("Content-Type", "application/xml")
	w.WriteHeader(status)
	if r.Method == http.MethodHead {
		return
	}
	_ = xml.NewEncoder(w).Encode(value)
}

func writeError(w http.ResponseWriter, r *http.Request, status int, code, message string) {
	if message == "" {
		message = code
	}
	type errorBody struct {
		XMLName xml.Name `xml:"Error"`
		Code    string   `xml:"Code"`
		Message string   `xml:"Message"`
	}
	writeXML(w, r, status, errorBody{Code: code, Message: message})
}

func writeStoreError(w http.ResponseWriter, r *http.Request, err error) {
	switch {
	case errors.Is(err, store.ErrNoSuchBucket):
		writeError(w, r, http.StatusNotFound, "NoSuchBucket", "the bucket does not exist")
	case errors.Is(err, store.ErrNoSuchKey):
		writeError(w, r, http.StatusNotFound, "NoSuchKey", "the key does not exist")
	case errors.Is(err, store.ErrBucketNotEmpty):
		writeError(w, r, http.StatusConflict, "BucketNotEmpty", "the bucket is not empty")
	case errors.Is(err, store.ErrIncomplete):
		writeError(w, r, http.StatusBadRequest, "InvalidArgument", err.Error())
	default:
		writeError(w, r, http.StatusInternalServerError, "InternalError", fmt.Sprint("storage error: ", err))
	}
}
