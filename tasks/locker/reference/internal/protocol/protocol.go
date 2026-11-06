// Package protocol contains the mechanical HTTP helpers used by Locker.
//
// These helpers intentionally cover only the small, documented Locker subset:
// one byte range and the paired preconditions described in PROMPT.md.
package protocol

import (
	"errors"
	"net/http"
	"strconv"
	"strings"
	"time"
)

// ErrInvalidRange means the Range header is malformed or unsatisfiable. Both
// cases are reported as 416 by the server.
var ErrInvalidRange = errors.New("invalid range")

// ParseRange parses a single byte range. It returns partial=false when the
// header is empty. start and end are inclusive. An oversized suffix is clipped
// to the whole body, as required by the Locker contract.
func ParseRange(header string, size int64) (start, end int64, partial bool, err error) {
	if header == "" {
		return 0, 0, false, nil
	}
	const prefix = "bytes="
	if !strings.HasPrefix(header, prefix) {
		return 0, 0, false, ErrInvalidRange
	}
	spec := strings.TrimSpace(strings.TrimPrefix(header, prefix))
	if spec == "" || strings.Contains(spec, ",") {
		return 0, 0, false, ErrInvalidRange
	}
	if size < 0 {
		return 0, 0, false, ErrInvalidRange
	}
	dash := strings.IndexByte(spec, '-')
	if dash < 0 {
		return 0, 0, false, ErrInvalidRange
	}
	left := strings.TrimSpace(spec[:dash])
	right := strings.TrimSpace(spec[dash+1:])

	if left == "" {
		// Suffix range: -N. An empty object has no satisfiable range.
		suffix, convErr := strconv.ParseInt(right, 10, 64)
		if convErr != nil || suffix <= 0 || size == 0 {
			return 0, 0, false, ErrInvalidRange
		}
		if suffix >= size {
			return 0, size - 1, true, nil
		}
		return size - suffix, size - 1, true, nil
	}

	first, convErr := strconv.ParseInt(left, 10, 64)
	if convErr != nil || first < 0 {
		return 0, 0, false, ErrInvalidRange
	}
	if first >= size {
		return 0, 0, false, ErrInvalidRange
	}
	if right == "" {
		return first, size - 1, true, nil
	}
	last, convErr := strconv.ParseInt(right, 10, 64)
	if convErr != nil || last < 0 || last < first {
		return 0, 0, false, ErrInvalidRange
	}
	if last >= size {
		last = size - 1
	}
	return first, last, true, nil
}

// ConditionalResult is the action to take after evaluating preconditions.
type ConditionalResult int

const (
	ConditionalContinue ConditionalResult = iota
	ConditionalNotModified
	ConditionalPreconditionFailed
)

// EvaluateConditional applies the documented paired precedence:
// If-Match, otherwise If-Unmodified-Since; then If-None-Match, otherwise
// If-Modified-Since. Invalid dates are ignored. The caller evaluates this
// before Range.
func EvaluateConditional(header http.Header, modTime time.Time, etag string) ConditionalResult {
	modTime = modTime.UTC().Truncate(time.Second)

	if value := header.Get("If-Match"); value != "" {
		if !etagMatches(value, etag) {
			return ConditionalPreconditionFailed
		}
	} else if value := header.Get("If-Unmodified-Since"); value != "" {
		if date, err := http.ParseTime(value); err == nil {
			if modTime.After(date.UTC()) {
				return ConditionalPreconditionFailed
			}
		}
	}

	if value := header.Get("If-None-Match"); value != "" {
		if etagMatches(value, etag) {
			return ConditionalNotModified
		}
	} else if value := header.Get("If-Modified-Since"); value != "" {
		if date, err := http.ParseTime(value); err == nil {
			if !modTime.After(date.UTC()) {
				return ConditionalNotModified
			}
		}
	}

	return ConditionalContinue
}

func etagMatches(value, etag string) bool {
	value = strings.TrimSpace(value)
	if value == "*" {
		return true
	}
	// The public subset is one quoted ETag. Accepting the unquoted form as
	// well is harmless and avoids rejecting well-meaning clients.
	return strings.Trim(value, `"`) == strings.Trim(etag, `"`)
}
