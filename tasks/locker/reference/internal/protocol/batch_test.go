package protocol

import (
	"strings"
	"testing"
)

func TestParseBatchValid(t *testing.T) {
	body := `<Batch>
  <Copy><Source>a</Source><Target>b</Target><IfMatch>"abc"</IfMatch></Copy>
  <Rename><Source>b</Source><Target>c</Target></Rename>
  <Delete><Key>c</Key><IfMatch>*</IfMatch></Delete>
</Batch>`
	ops, err := ParseBatch(strings.NewReader(body), 32)
	if err != nil {
		t.Fatalf("ParseBatch: %v", err)
	}
	if len(ops) != 3 {
		t.Fatalf("got %d ops", len(ops))
	}
	if ops[0].Kind != "copy" || ops[0].Source != "a" || ops[0].Target != "b" || ops[0].IfMatch != `"abc"` {
		t.Fatalf("unexpected copy op: %+v", ops[0])
	}
	if ops[1].Kind != "rename" || ops[1].Source != "b" || ops[1].Target != "c" {
		t.Fatalf("unexpected rename op: %+v", ops[1])
	}
	if ops[2].Kind != "delete" || ops[2].Key != "c" || ops[2].IfMatch != "*" {
		t.Fatalf("unexpected delete op: %+v", ops[2])
	}
}

func TestParseBatchRejectsInvalid(t *testing.T) {
	ops33 := "<Batch>" + strings.Repeat("<Delete><Key>k</Key></Delete>", 33) + "</Batch>"
	bodies := map[string]string{
		"malformed":       `<Batch><Copy>`,
		"zero ops":        `<Batch></Batch>`,
		"wrong root":      `<Ops><Delete><Key>a</Key></Delete></Ops>`,
		"too many ops":    ops33,
		"unknown op":      `<Batch><Move><Source>a</Source><Target>b</Target></Move></Batch>`,
		"missing target":  `<Batch><Copy><Source>a</Source></Copy></Batch>`,
		"empty source":    `<Batch><Rename><Source></Source><Target>b</Target></Rename></Batch>`,
		"missing key":     `<Batch><Delete></Delete></Batch>`,
		"equal endpoints": `<Batch><Copy><Source>a</Source><Target>a</Target></Copy></Batch>`,
		"empty IfMatch":   `<Batch><Delete><Key>a</Key><IfMatch></IfMatch></Delete></Batch>`,
		"bare IfMatch":    `<Batch><Delete><Key>a</Key><IfMatch>abc</IfMatch></Delete></Batch>`,
		"delete source":   `<Batch><Delete><Key>a</Key><Source>b</Source></Delete></Batch>`,
		"copy key":        `<Batch><Copy><Source>a</Source><Target>b</Target><Key>c</Key></Copy></Batch>`,
	}
	for label, body := range bodies {
		if _, err := ParseBatch(strings.NewReader(body), 32); err == nil {
			t.Errorf("%s: expected an error", label)
		}
	}
}

func TestParseBatchAtLimit(t *testing.T) {
	body := "<Batch>" + strings.Repeat("<Delete><Key>k</Key></Delete>", 32) + "</Batch>"
	ops, err := ParseBatch(strings.NewReader(body), 32)
	if err != nil {
		t.Fatalf("32 ops should parse: %v", err)
	}
	if len(ops) != 32 {
		t.Fatalf("got %d ops", len(ops))
	}
}
