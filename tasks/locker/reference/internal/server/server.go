package server

import (
	"encoding/base64"
	"encoding/json"
	"encoding/xml"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"os"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
	"unicode/utf8"

	"locker/internal/protocol"
	"locker/internal/store"
)

// Server implements the documented Locker HTTP subset.
type Server struct {
	store *store.Store
	log   *log.Logger

	// fault is an author-only negative-control switch, not part of the
	// public contract and never present in the seed.
	fault    string
	globalMu sync.Mutex
}

// New returns an HTTP handler backed by st.
func New(st *store.Store, logger *log.Logger) *Server {
	if logger == nil {
		logger = log.Default()
	}
	return &Server{store: st, log: logger, fault: os.Getenv("LOCKER_FAULT")}
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
	if s.fault == "global-lock" {
		s.globalMu.Lock()
		defer s.globalMu.Unlock()
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
	if strings.Contains(name, "/") {
		return false
	}
	return utf8.ValidString(name)
}

// ---------------------------------------------------------------------------
// Bucket and listing routes

func (s *Server) listBuckets(w http.ResponseWriter, r *http.Request) {
	type bucketName struct {
		Name string `xml:"Name"`
	}
	type buckets struct {
		Bucket []bucketName `xml:"Bucket"`
	}
	result := struct {
		XMLName xml.Name `xml:"ListAllMyBucketsResult"`
		Buckets buckets  `xml:"Buckets"`
	}{}
	for _, name := range s.store.ListBuckets() {
		result.Buckets.Bucket = append(result.Buckets.Bucket, bucketName{Name: name})
	}
	writeXML(w, r, http.StatusOK, result)
}

func (s *Server) serveBucket(w http.ResponseWriter, r *http.Request, bucket string) {
	query := r.URL.Query()
	if _, ok := query["uploadId"]; ok {
		writeError(w, r, http.StatusBadRequest, "InvalidArgument", "uploadId is not valid on a bucket")
		return
	}
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
	if value, ok := query["list-type"]; ok {
		if len(value) != 1 || value[0] != "2" {
			writeError(w, r, http.StatusBadRequest, "InvalidArgument", "unsupported list-type")
			return
		}
	}
	prefix := query.Get("prefix")
	delimiter := query.Get("delimiter")
	maxKeys := 1000
	if value, ok := query["max-keys"]; ok {
		if len(value) != 1 {
			writeError(w, r, http.StatusBadRequest, "InvalidArgument", "invalid max-keys")
			return
		}
		parsed, err := strconv.Atoi(value[0])
		if err != nil || parsed < 1 || parsed > 1000 {
			writeError(w, r, http.StatusBadRequest, "InvalidArgument", "invalid max-keys")
			return
		}
		maxKeys = parsed
	}

	objects, err := s.store.Objects(bucket)
	if err != nil {
		writeStoreError(w, r, err)
		return
	}

	type item struct {
		name     string
		isPrefix bool
		object   store.Object
	}
	items := make([]item, 0, len(objects))
	prefixes := map[string]bool{}
	for key, object := range objects {
		if !strings.HasPrefix(key, prefix) {
			continue
		}
		if delimiter != "" {
			remainder := key[len(prefix):]
			if index := strings.Index(remainder, delimiter); index >= 0 {
				group := prefix + remainder[:index+len(delimiter)]
				if !prefixes[group] {
					prefixes[group] = true
					items = append(items, item{name: group, isPrefix: true})
				}
				continue
			}
		}
		items = append(items, item{name: key, object: object})
	}
	sort.Slice(items, func(i, j int) bool {
		if items[i].name != items[j].name {
			return items[i].name < items[j].name
		}
		return !items[i].isPrefix && items[j].isPrefix
	})

	start := 0
	if token := query.Get("continuation-token"); token != "" {
		after, err := decodeToken(token)
		if err != nil || after.Bucket != bucket || after.Prefix != prefix || after.Delimiter != delimiter {
			writeError(w, r, http.StatusBadRequest, "InvalidArgument", "invalid continuation token")
			return
		}
		start = sort.Search(len(items), func(index int) bool {
			return items[index].name > after.After
		})
	}

	end := start + maxKeys
	truncated := false
	if end < len(items) {
		truncated = true
	} else {
		end = len(items)
	}
	page := items[start:end]

	type content struct {
		Key          string `xml:"Key"`
		Size         int64  `xml:"Size"`
		ETag         string `xml:"ETag"`
		LastModified string `xml:"LastModified"`
	}
	type commonPrefix struct {
		Prefix string `xml:"Prefix"`
	}
	result := struct {
		XMLName               xml.Name       `xml:"ListBucketResult"`
		Name                  string         `xml:"Name"`
		Prefix                string         `xml:"Prefix"`
		Delimiter             string         `xml:"Delimiter"`
		MaxKeys               int            `xml:"MaxKeys"`
		KeyCount              int            `xml:"KeyCount"`
		IsTruncated           bool           `xml:"IsTruncated"`
		Contents              []content      `xml:"Contents"`
		CommonPrefixes        []commonPrefix `xml:"CommonPrefixes"`
		NextContinuationToken string         `xml:"NextContinuationToken,omitempty"`
	}{
		Name:        bucket,
		Prefix:      prefix,
		Delimiter:   delimiter,
		MaxKeys:     maxKeys,
		KeyCount:    len(page),
		IsTruncated: truncated,
	}
	for _, entry := range page {
		if entry.isPrefix {
			result.CommonPrefixes = append(result.CommonPrefixes, commonPrefix{Prefix: entry.name})
			continue
		}
		result.Contents = append(result.Contents, content{
			Key:          entry.name,
			Size:         entry.object.Size,
			ETag:         entry.object.ETag,
			LastModified: entry.object.ModTime.UTC().Format(time.RFC3339),
		})
	}
	if truncated && len(page) > 0 {
		token, err := encodeToken(tokenState{
			Bucket:    bucket,
			Prefix:    prefix,
			Delimiter: delimiter,
			After:     page[len(page)-1].name,
		})
		if err != nil {
			writeStoreError(w, r, err)
			return
		}
		result.NextContinuationToken = token
	}
	writeXML(w, r, http.StatusOK, result)
}

type tokenState struct {
	Bucket    string `json:"bucket"`
	Prefix    string `json:"prefix"`
	Delimiter string `json:"delimiter"`
	After     string `json:"after"`
}

func encodeToken(state tokenState) (string, error) {
	data, err := json.Marshal(state)
	if err != nil {
		return "", err
	}
	return base64.RawURLEncoding.EncodeToString(data), nil
}

func decodeToken(value string) (tokenState, error) {
	var state tokenState
	data, err := base64.RawURLEncoding.DecodeString(value)
	if err != nil {
		return state, err
	}
	if err := json.Unmarshal(data, &state); err != nil {
		return state, err
	}
	if state.After == "" && state.Bucket == "" {
		return state, errors.New("empty token")
	}
	return state, nil
}

// ---------------------------------------------------------------------------
// Object and multipart routes

func (s *Server) serveObject(w http.ResponseWriter, r *http.Request, bucket, key string) {
	query := r.URL.Query()
	uploads := hasKey(query, "uploads")
	uploadID := query.Get("uploadId")
	partValue, hasPart := query["partNumber"]

	if uploads && (uploadID != "" || hasPart) {
		writeError(w, r, http.StatusBadRequest, "InvalidArgument", "combined multipart parameters")
		return
	}
	if hasPart && uploadID == "" {
		writeError(w, r, http.StatusBadRequest, "InvalidArgument", "partNumber requires uploadId")
		return
	}
	if hasPart && r.Method != http.MethodPut {
		writeError(w, r, http.StatusBadRequest, "InvalidArgument", "partNumber is only valid for part upload")
		return
	}

	switch r.Method {
	case http.MethodPut:
		if uploads {
			writeError(w, r, http.StatusBadRequest, "InvalidArgument", "uploads is not valid for PUT")
			return
		}
		if uploadID != "" {
			if len(partValue) != 1 {
				writeError(w, r, http.StatusBadRequest, "InvalidArgument", "one partNumber is required")
				return
			}
			number, err := strconv.Atoi(partValue[0])
			if err != nil || number < 1 || number > 10000 {
				writeError(w, r, http.StatusBadRequest, "InvalidArgument", "invalid part number")
				return
			}
			etag, err := s.store.UploadPart(bucket, key, uploadID, number, r.Body, r.ContentLength)
			if err != nil {
				writeStoreError(w, r, err)
				return
			}
			w.Header().Set("ETag", etag)
			w.WriteHeader(http.StatusOK)
			return
		}
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
		if uploads {
			writeError(w, r, http.StatusBadRequest, "InvalidArgument", "uploads is not valid for GET")
			return
		}
		if hasPart {
			writeError(w, r, http.StatusBadRequest, "InvalidArgument", "partNumber is not valid for GET")
			return
		}
		if uploadID != "" {
			s.listParts(w, r, bucket, key, uploadID)
			return
		}
		s.getObject(w, r, bucket, key, r.Method == http.MethodHead)
	case http.MethodPost:
		if uploads {
			contentType := r.Header.Get("Content-Type")
			if contentType == "" {
				contentType = "application/octet-stream"
			}
			id, err := s.store.Initiate(bucket, key, contentType)
			if err != nil {
				writeStoreError(w, r, err)
				return
			}
			result := struct {
				XMLName  xml.Name `xml:"InitiateMultipartUploadResult"`
				Bucket   string   `xml:"Bucket"`
				Key      string   `xml:"Key"`
				UploadID string   `xml:"UploadId"`
			}{Bucket: bucket, Key: key, UploadID: id}
			writeXML(w, r, http.StatusOK, result)
			return
		}
		if uploadID != "" {
			s.completeUpload(w, r, bucket, key, uploadID)
			return
		}
		writeError(w, r, http.StatusMethodNotAllowed, "MethodNotAllowed", "method not allowed")
	case http.MethodDelete:
		if uploads {
			writeError(w, r, http.StatusBadRequest, "InvalidArgument", "uploads is not valid for DELETE")
			return
		}
		if hasPart {
			writeError(w, r, http.StatusBadRequest, "InvalidArgument", "partNumber is not valid for DELETE")
			return
		}
		if uploadID != "" {
			if err := s.store.Abort(bucket, key, uploadID); err != nil {
				writeStoreError(w, r, err)
				return
			}
			w.WriteHeader(http.StatusNoContent)
			return
		}
		if err := s.store.DeleteObject(bucket, key); err != nil {
			writeStoreError(w, r, err)
			return
		}
		w.WriteHeader(http.StatusNoContent)
	default:
		writeError(w, r, http.StatusMethodNotAllowed, "MethodNotAllowed", "method not allowed")
	}
}

func (s *Server) getObject(w http.ResponseWriter, r *http.Request, bucket, key string, head bool) {
	object, file, err := s.store.GetObject(bucket, key)
	if err != nil {
		writeStoreError(w, r, err)
		return
	}
	if file != nil {
		defer file.Close()
	}

	action := protocol.EvaluateConditional(r.Header, object.ModTime, object.ETag)
	if action == protocol.ConditionalPreconditionFailed {
		w.WriteHeader(http.StatusPreconditionFailed)
		return
	}
	w.Header().Set("ETag", object.ETag)
	w.Header().Set("Last-Modified", object.ModTime.UTC().Format(http.TimeFormat))
	if action == protocol.ConditionalNotModified {
		w.WriteHeader(http.StatusNotModified)
		return
	}

	start, end, partial, err := protocol.ParseRange(r.Header.Get("Range"), object.Size)
	w.Header().Set("Content-Type", object.ContentType)
	w.Header().Set("Accept-Ranges", "bytes")
	if err != nil {
		w.Header().Set("Content-Range", fmt.Sprintf("bytes */%d", object.Size))
		w.WriteHeader(http.StatusRequestedRangeNotSatisfiable)
		return
	}
	if partial {
		length := end - start + 1
		w.Header().Set("Content-Range", fmt.Sprintf("bytes %d-%d/%d", start, end, object.Size))
		w.Header().Set("Content-Length", strconv.FormatInt(length, 10))
		w.WriteHeader(http.StatusPartialContent)
		if !head && file != nil {
			if _, err := file.Seek(start, io.SeekStart); err != nil {
				return
			}
			_, _ = io.CopyN(w, file, length)
		}
		return
	}

	w.Header().Set("Content-Length", strconv.FormatInt(object.Size, 10))
	w.WriteHeader(http.StatusOK)
	if !head && file != nil {
		if _, err := file.Seek(0, io.SeekStart); err != nil {
			return
		}
		_, _ = io.Copy(w, file)
	}
}

func (s *Server) listParts(w http.ResponseWriter, r *http.Request, bucket, key, id string) {
	numbers, parts, err := s.store.ListParts(bucket, key, id)
	if err != nil {
		writeStoreError(w, r, err)
		return
	}
	type partXML struct {
		PartNumber int    `xml:"PartNumber"`
		ETag       string `xml:"ETag"`
		Size       int64  `xml:"Size"`
	}
	result := struct {
		XMLName  xml.Name  `xml:"ListPartsResult"`
		Bucket   string    `xml:"Bucket"`
		Key      string    `xml:"Key"`
		UploadID string    `xml:"UploadId"`
		Part     []partXML `xml:"Part"`
	}{Bucket: bucket, Key: key, UploadID: id}
	for _, number := range numbers {
		part := parts[number]
		result.Part = append(result.Part, partXML{PartNumber: number, ETag: part.ETag, Size: part.Size})
	}
	writeXML(w, r, http.StatusOK, result)
}

func (s *Server) completeUpload(w http.ResponseWriter, r *http.Request, bucket, key, id string) {
	type requestPart struct {
		PartNumber int    `xml:"PartNumber"`
		ETag       string `xml:"ETag"`
	}
	var request struct {
		XMLName xml.Name      `xml:"CompleteMultipartUpload"`
		Part    []requestPart `xml:"Part"`
	}
	body, err := io.ReadAll(io.LimitReader(r.Body, 1<<20))
	if err != nil {
		writeError(w, r, http.StatusBadRequest, "InvalidArgument", "could not read completion XML")
		return
	}
	if err := xml.Unmarshal(body, &request); err != nil {
		writeError(w, r, http.StatusBadRequest, "InvalidArgument", "malformed completion XML")
		return
	}
	parts := make([]store.CompletionPart, 0, len(request.Part))
	for _, part := range request.Part {
		parts = append(parts, store.CompletionPart{PartNumber: part.PartNumber, ETag: part.ETag})
	}
	object, err := s.store.Complete(bucket, key, id, parts)
	if err != nil {
		writeStoreError(w, r, err)
		return
	}
	result := struct {
		XMLName xml.Name `xml:"CompleteMultipartUploadResult"`
		Bucket  string   `xml:"Bucket"`
		Key     string   `xml:"Key"`
		ETag    string   `xml:"ETag"`
	}{Bucket: bucket, Key: key, ETag: object.ETag}
	writeXML(w, r, http.StatusOK, result)
}

// ---------------------------------------------------------------------------
// Response helpers

func hasKey(values url.Values, key string) bool {
	_, ok := values[key]
	return ok
}

func writeXML(w http.ResponseWriter, r *http.Request, status int, value any) {
	w.Header().Set("Content-Type", "application/xml")
	w.WriteHeader(status)
	if r.Method == http.MethodHead {
		return
	}
	encoder := xml.NewEncoder(w)
	_ = encoder.Encode(value)
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
	case errors.Is(err, store.ErrNoSuchUpload):
		writeError(w, r, http.StatusNotFound, "NoSuchUpload", "the upload does not exist")
	case errors.Is(err, store.ErrBucketNotEmpty):
		writeError(w, r, http.StatusConflict, "BucketNotEmpty", "the bucket is not empty")
	case errors.Is(err, store.ErrIncomplete), errors.Is(err, store.ErrInvalid):
		writeError(w, r, http.StatusBadRequest, "InvalidArgument", err.Error())
	default:
		writeError(w, r, http.StatusInternalServerError, "InternalError", "internal storage error")
	}
}
