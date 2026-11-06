package cli

import (
	"encoding/json"
	"encoding/xml"
	"errors"
	"flag"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"

	"locker/internal/server"
	"locker/internal/store"
)

const defaultEndpoint = "http://127.0.0.1:9000"

// Run executes the locker command line and returns the process exit code.
func Run(args []string) int {
	endpoint := os.Getenv("LOCKER_ENDPOINT")
	if endpoint == "" {
		endpoint = defaultEndpoint
	}
	for len(args) > 0 {
		switch args[0] {
		case "--endpoint":
			if len(args) < 2 {
				return usageError("--endpoint requires a URL")
			}
			endpoint = args[1]
			args = args[2:]
		case "-h", "--help":
			printUsage(os.Stdout)
			return 0
		default:
			if strings.HasPrefix(args[0], "-") {
				return usageError("unknown option " + args[0])
			}
			goto command
		}
	}
command:
	if len(args) == 0 {
		return usageError("missing command")
	}
	command := args[0]
	switch command {
	case "server":
		return runServer(args[1:])
	case "mb", "rb", "cp", "ls", "rm":
		base, err := url.Parse(endpoint)
		if err != nil || base.Scheme == "" || base.Host == "" {
			return runtimeError("invalid endpoint " + endpoint)
		}
		client := &http.Client{}
		switch command {
		case "mb":
			return bucketCommand(client, base, args[1:], http.MethodPut)
		case "rb":
			return bucketCommand(client, base, args[1:], http.MethodDelete)
		case "cp":
			return copyCommand(client, base, args[1:])
		case "ls":
			return listCommand(client, base, args[1:])
		case "rm":
			return removeCommand(client, base, args[1:])
		}
	case "help":
		printUsage(os.Stdout)
		return 0
	}
	return usageError("unknown command " + command)
}

func printUsage(out io.Writer) {
	fmt.Fprintln(out, "usage: locker [--endpoint URL] COMMAND ...")
	fmt.Fprintln(out, "commands: server, mb, rb, cp, ls, rm")
	fmt.Fprintln(out, "server flags: --data DIR --addr HOST:PORT")
}

func usageError(message string) int {
	fmt.Fprintln(os.Stderr, "locker:", message)
	printUsage(os.Stderr)
	return 2
}

func runtimeError(message string) int {
	fmt.Fprintln(os.Stderr, "locker:", message)
	return 1
}

func runServer(args []string) int {
	flags := flag.NewFlagSet("server", flag.ContinueOnError)
	flags.SetOutput(os.Stderr)
	data := flags.String("data", "./data", "data directory")
	addr := flags.String("addr", "127.0.0.1:9000", "listen address")
	if err := flags.Parse(args); err != nil {
		return 2
	}
	if flags.NArg() != 0 {
		return usageError("server takes only --data and --addr")
	}
	backend, err := store.Open(*data)
	if err != nil {
		return runtimeError(err.Error())
	}
	handler := server.New(backend, nil)
	httpServer := &http.Server{
		Addr:              *addr,
		Handler:           handler,
		ReadHeaderTimeout: 10 * time.Second,
	}
	if err := httpServer.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		return runtimeError(err.Error())
	}
	return 0
}

type s3Target struct {
	bucket string
	key    string
}

func parseS3(value string) (s3Target, error) {
	if !strings.HasPrefix(value, "s3://") {
		return s3Target{}, errors.New("expected an s3:// URI")
	}
	rest := strings.TrimPrefix(value, "s3://")
	rawBucket, rawKey, hasKey := strings.Cut(rest, "/")
	bucket, err := url.PathUnescape(rawBucket)
	if err != nil || bucket == "" {
		return s3Target{}, errors.New("invalid bucket in " + value)
	}
	target := s3Target{bucket: bucket}
	if hasKey {
		key, err := url.PathUnescape(rawKey)
		if err != nil {
			return s3Target{}, errors.New("invalid key in " + value)
		}
		target.key = key
	}
	return target, nil
}

func targetURL(base *url.URL, target s3Target) string {
	build := *base
	build.Path = "/" + target.bucket
	if target.key != "" {
		build.Path += "/" + target.key
	}
	build.RawQuery = ""
	return build.String()
}

func bucketCommand(client *http.Client, base *url.URL, args []string, method string) int {
	if len(args) != 1 {
		return usageError("mb/rb require one s3://bucket URI")
	}
	target, err := parseS3(args[0])
	if err != nil || target.key != "" {
		return usageError("expected s3://bucket")
	}
	response, err := client.Do(mustRequest(method, targetURL(base, target), nil, 0))
	if err != nil {
		return runtimeError(err.Error())
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return runtimeError(responseError(response))
	}
	return 0
}

func removeCommand(client *http.Client, base *url.URL, args []string) int {
	if len(args) != 1 {
		return usageError("rm requires one s3://bucket/key URI")
	}
	target, err := parseS3(args[0])
	if err != nil || target.key == "" {
		return usageError("expected s3://bucket/key")
	}
	response, err := client.Do(mustRequest(http.MethodDelete, targetURL(base, target), nil, 0))
	if err != nil {
		return runtimeError(err.Error())
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return runtimeError(responseError(response))
	}
	return 0
}

func copyCommand(client *http.Client, base *url.URL, args []string) int {
	if len(args) != 2 {
		return usageError("cp requires LOCAL s3://bucket/key or s3://bucket/key LOCAL")
	}
	sourceRemote := strings.HasPrefix(args[0], "s3://")
	destRemote := strings.HasPrefix(args[1], "s3://")
	if sourceRemote == destRemote {
		return usageError("cp requires exactly one remote s3:// URI")
	}
	if destRemote {
		return uploadCommand(client, base, args[0], args[1])
	}
	return downloadCommand(client, base, args[0], args[1])
}

func uploadCommand(client *http.Client, base *url.URL, local, remote string) int {
	target, err := parseS3(remote)
	if err != nil || target.key == "" {
		return usageError("expected s3://bucket/key destination")
	}
	file, err := os.Open(local)
	if err != nil {
		return runtimeError(err.Error())
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil {
		return runtimeError(err.Error())
	}
	if info.IsDir() {
		return runtimeError("cp does not copy directories")
	}
	request := mustRequest(http.MethodPut, targetURL(base, target), file, info.Size())
	request.Header.Set("Content-Type", "application/octet-stream")
	response, err := client.Do(request)
	if err != nil {
		return runtimeError(err.Error())
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return runtimeError(responseError(response))
	}
	return 0
}

func downloadCommand(client *http.Client, base *url.URL, remote, local string) int {
	target, err := parseS3(remote)
	if err != nil || target.key == "" {
		return usageError("expected s3://bucket/key source")
	}
	response, err := client.Do(mustRequest(http.MethodGet, targetURL(base, target), nil, 0))
	if err != nil {
		return runtimeError(err.Error())
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return runtimeError(responseError(response))
	}
	file, err := os.Create(local)
	if err != nil {
		return runtimeError(err.Error())
	}
	if _, err := io.Copy(file, response.Body); err != nil {
		file.Close()
		os.Remove(local)
		return runtimeError(err.Error())
	}
	if err := file.Close(); err != nil {
		os.Remove(local)
		return runtimeError(err.Error())
	}
	return 0
}

func mustRequest(method, target string, body io.Reader, length int64) *http.Request {
	request, err := http.NewRequest(method, target, body)
	if err != nil {
		panic(err)
	}
	if body != nil {
		request.ContentLength = length
	}
	return request
}

type listResult struct {
	Contents []struct {
		Key  string `xml:"Key"`
		Size int64  `xml:"Size"`
		ETag string `xml:"ETag"`
	} `xml:"Contents"`
	IsTruncated           bool   `xml:"IsTruncated"`
	NextContinuationToken string `xml:"NextContinuationToken"`
}

func listCommand(client *http.Client, base *url.URL, args []string) int {
	if len(args) != 1 {
		return usageError("ls requires one s3://bucket[/prefix] URI")
	}
	target, err := parseS3(args[0])
	if err != nil {
		return usageError(err.Error())
	}
	encoder := json.NewEncoder(os.Stdout)
	token := ""
	for {
		build := *base
		build.Path = "/" + target.bucket
		query := url.Values{}
		query.Set("list-type", "2")
		query.Set("prefix", target.key)
		query.Set("max-keys", "1000")
		if token != "" {
			query.Set("continuation-token", token)
		}
		build.RawQuery = query.Encode()
		response, err := client.Do(mustRequest(http.MethodGet, build.String(), nil, 0))
		if err != nil {
			return runtimeError(err.Error())
		}
		var result listResult
		decodeErr := xml.NewDecoder(io.LimitReader(response.Body, 8<<20)).Decode(&result)
		response.Body.Close()
		if response.StatusCode < 200 || response.StatusCode >= 300 {
			return runtimeError("list failed with status " + response.Status)
		}
		if decodeErr != nil {
			return runtimeError("invalid list response: " + decodeErr.Error())
		}
		for _, item := range result.Contents {
			line := struct {
				Key  string `json:"key"`
				Size int64  `json:"size"`
				ETag string `json:"etag"`
			}{Key: item.Key, Size: item.Size, ETag: item.ETag}
			if err := encoder.Encode(line); err != nil {
				return runtimeError(err.Error())
			}
		}
		if !result.IsTruncated || result.NextContinuationToken == "" {
			return 0
		}
		token = result.NextContinuationToken
	}
}

func responseError(response *http.Response) string {
	data, _ := io.ReadAll(io.LimitReader(response.Body, 64<<10))
	message := strings.TrimSpace(string(data))
	if message == "" {
		message = response.Status
	}
	return fmt.Sprintf("%s: %s", response.Status, message)
}
