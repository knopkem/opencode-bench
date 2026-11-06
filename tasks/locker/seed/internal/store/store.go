package store

import (
	"crypto/md5"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"sort"
	"time"
)

// Seed v0 deliberately stores metadata in a plain JSON file and object bytes
// at their final path. It has no atomic publication and no synchronization.
var (
	ErrNoSuchBucket   = errors.New("no such bucket")
	ErrNoSuchKey      = errors.New("no such key")
	ErrBucketNotEmpty = errors.New("bucket not empty")
	ErrIncomplete     = errors.New("incomplete request body")
)

type Object struct {
	Size        int64     `json:"size"`
	ETag        string    `json:"etag"`
	ContentType string    `json:"content_type"`
	ModTime     time.Time `json:"mod_time"`
}

type Meta struct {
	Buckets map[string]map[string]Object `json:"buckets"`
}

type Store struct {
	root      string
	metaPath  string
	objectDir string
	meta      Meta
}

func Open(root string) (*Store, error) {
	s := &Store{
		root:      filepath.Clean(root),
		metaPath:  filepath.Join(root, "metadata.json"),
		objectDir: filepath.Join(root, "objects"),
	}
	if err := os.MkdirAll(s.objectDir, 0o755); err != nil {
		return nil, err
	}
	data, err := os.ReadFile(s.metaPath)
	if errors.Is(err, os.ErrNotExist) {
		s.meta = Meta{Buckets: map[string]map[string]Object{}}
		return s, s.save()
	}
	if err != nil {
		return nil, err
	}
	if err := json.Unmarshal(data, &s.meta); err != nil {
		return nil, err
	}
	if s.meta.Buckets == nil {
		s.meta.Buckets = map[string]map[string]Object{}
	}
	return s, nil
}

func (s *Store) save() error {
	data, err := json.MarshalIndent(s.meta, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(s.metaPath, data, 0o644)
}

func (s *Store) CreateBucket(name string) error {
	if _, ok := s.meta.Buckets[name]; ok {
		return nil
	}
	s.meta.Buckets[name] = map[string]Object{}
	return s.save()
}

func (s *Store) DeleteBucket(name string) error {
	bucket, ok := s.meta.Buckets[name]
	if !ok {
		return ErrNoSuchBucket
	}
	if len(bucket) > 0 {
		return ErrBucketNotEmpty
	}
	delete(s.meta.Buckets, name)
	_ = os.Remove(filepath.Join(s.objectDir, name))
	return s.save()
}

func (s *Store) BucketExists(name string) bool {
	_, ok := s.meta.Buckets[name]
	return ok
}

func (s *Store) ListBuckets() []string {
	names := make([]string, 0, len(s.meta.Buckets))
	for name := range s.meta.Buckets {
		names = append(names, name)
	}
	sort.Strings(names)
	return names
}

func (s *Store) Objects(bucket string) (map[string]Object, error) {
	objects, ok := s.meta.Buckets[bucket]
	if !ok {
		return nil, ErrNoSuchBucket
	}
	copy := make(map[string]Object, len(objects))
	for key, object := range objects {
		copy[key] = object
	}
	return copy, nil
}

func (s *Store) objectPath(bucket, key string) string {
	return filepath.Join(s.objectDir, bucket, filepath.FromSlash(key))
}

func (s *Store) PutObject(bucket, key string, body io.Reader, declared int64, contentType string) (Object, error) {
	if !s.BucketExists(bucket) {
		return Object{}, ErrNoSuchBucket
	}
	data, err := io.ReadAll(body)
	if err != nil || (declared >= 0 && int64(len(data)) != declared) {
		return Object{}, ErrIncomplete
	}
	path := s.objectPath(bucket, key)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return Object{}, err
	}
	if err := os.WriteFile(path, data, 0o644); err != nil {
		return Object{}, err
	}
	sum := md5.Sum(data)
	object := Object{
		Size:        int64(len(data)),
		ETag:        `"` + hex.EncodeToString(sum[:]) + `"`,
		ContentType: contentType,
		ModTime:     time.Now().UTC(),
	}
	if s.meta.Buckets[bucket] == nil {
		s.meta.Buckets[bucket] = map[string]Object{}
	}
	s.meta.Buckets[bucket][key] = object
	if err := s.save(); err != nil {
		return Object{}, err
	}
	return object, nil
}

func (s *Store) GetObject(bucket, key string) (Object, *os.File, error) {
	objects, ok := s.meta.Buckets[bucket]
	if !ok {
		return Object{}, nil, ErrNoSuchBucket
	}
	object, ok := objects[key]
	if !ok {
		return Object{}, nil, ErrNoSuchKey
	}
	file, err := os.Open(s.objectPath(bucket, key))
	if err != nil {
		return Object{}, nil, err
	}
	return object, file, nil
}

func (s *Store) DeleteObject(bucket, key string) error {
	objects, ok := s.meta.Buckets[bucket]
	if !ok {
		return ErrNoSuchBucket
	}
	if _, ok := objects[key]; !ok {
		return nil
	}
	delete(objects, key)
	_ = os.Remove(s.objectPath(bucket, key))
	return s.save()
}
