package protocol

import "encoding/xml"

// These structs are provided so handlers do not spend the run re-typing the
// documented XML shapes. They are plain encoding/xml structs with no
// namespaces. Use them directly or as a starting point.

type ListAllMyBucketsResult struct {
	XMLName xml.Name `xml:"ListAllMyBucketsResult"`
	Buckets struct {
		Bucket []struct {
			Name string `xml:"Name"`
		} `xml:"Bucket"`
	} `xml:"Buckets"`
}

type ListBucketResult struct {
	XMLName               xml.Name `xml:"ListBucketResult"`
	Name                  string   `xml:"Name"`
	Prefix                string   `xml:"Prefix"`
	Delimiter             string   `xml:"Delimiter"`
	MaxKeys               int      `xml:"MaxKeys"`
	KeyCount              int      `xml:"KeyCount"`
	IsTruncated           bool     `xml:"IsTruncated"`
	Contents              []Content
	CommonPrefixes        []CommonPrefix
	NextContinuationToken string `xml:"NextContinuationToken,omitempty"`
}

type Content struct {
	XMLName      xml.Name `xml:"Contents"`
	Key          string   `xml:"Key"`
	Size         int64    `xml:"Size"`
	ETag         string   `xml:"ETag"`
	LastModified string   `xml:"LastModified"`
}

type CommonPrefix struct {
	XMLName xml.Name `xml:"CommonPrefixes"`
	Prefix  string   `xml:"Prefix"`
}

type InitiateMultipartUploadResult struct {
	XMLName  xml.Name `xml:"InitiateMultipartUploadResult"`
	Bucket   string   `xml:"Bucket"`
	Key      string   `xml:"Key"`
	UploadID string   `xml:"UploadId"`
}

type ListPartsResult struct {
	XMLName  xml.Name `xml:"ListPartsResult"`
	Bucket   string   `xml:"Bucket"`
	Key      string   `xml:"Key"`
	UploadID string   `xml:"UploadId"`
	Part     []Part   `xml:"Part"`
}

type Part struct {
	PartNumber int    `xml:"PartNumber"`
	ETag       string `xml:"ETag"`
	Size       int64  `xml:"Size"`
}

// CompleteMultipartUpload is also the wire request sent by clients.
type CompleteMultipartUpload struct {
	XMLName xml.Name            `xml:"CompleteMultipartUpload"`
	Part    []CompletionPartXML `xml:"Part"`
}

type CompletionPartXML struct {
	PartNumber int    `xml:"PartNumber"`
	ETag       string `xml:"ETag"`
}

type CompleteMultipartUploadResult struct {
	XMLName xml.Name `xml:"CompleteMultipartUploadResult"`
	Bucket  string   `xml:"Bucket"`
	Key     string   `xml:"Key"`
	ETag    string   `xml:"ETag"`
}

type Error struct {
	XMLName xml.Name `xml:"Error"`
	Code    string   `xml:"Code"`
	Message string   `xml:"Message"`
}
