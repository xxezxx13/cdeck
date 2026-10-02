# CDECK File Format 2

## Status

This document specifies **CDECK File Format 2**.

Its wire-format generation identifier is:

```text
CDECK002
```

CDECK software release numbering is independent from file-format generation.

CDECK V3 reads and writes CDECK002.

CDECK001 is the previous generation and is intentionally not part of the permanent V3 runtime reader. Migration is handled by the standalone `tools/cdeck001_to_002.py` utility.

## Purpose

CDECK002 is a deterministic, read-oriented, range-addressable container for application-defined metadata and arbitrary binary payloads.

The core format defines:

- record identity;
- application-defined metadata;
- payload lengths and order;
- deterministic index encoding;
- derived payload addressing;
- structural validation;
- safe numeric bounds;
- exact byte-source reads;
- browser HTTP range transport.

The core format does not assign meaning to payload bytes or metadata keys.

Media types, rendering, image decoding, DOM behavior, lazy-loading policy, Blob URL lifecycle, and domain-specific validation belong to applications.

## Binary Layout

A CDECK002 file consists of:

```text
12-byte header
UTF-8 JSON index
payload 0
payload 1
...
payload N
```

Payloads are contiguous and appear in record order.

There is no footer, padding, alignment block, payload table, stored payload offset, stored total length, embedded checksum block, MIME table, compression layer, or encryption layer.

All multibyte binary integers in the header are little-endian.

## Header

The header is exactly 12 bytes:

```text
Offset  Size  Type       Field
0       8     byte[8]    magic
8       4     uint32 LE  indexLength
```

Requirements:

```text
magic == ASCII "CDECK002"
indexLength > 0
```

The reference implementations apply a 16 MiB runtime guard:

```text
indexLength <= 16 * 1024 * 1024
```

This 16 MiB value is a reference runtime safety limit, not an expanded integer field in the wire format. The header field itself remains uint32 LE.

Readers must reject unsupported CDECK generations.

## JSON Index

The index occupies exactly:

```text
bytes 12 through (12 + indexLength - 1)
```

The index must:

- be UTF-8;
- contain no UTF-8 BOM;
- decode without malformed UTF-8;
- contain valid JSON;
- have a top-level JSON array;
- contain at most 4,000 records.

Property order is not significant for structural validity.

Canonical encoding imposes a deterministic property order described later in this specification.

## Record Shape

Every record must be a JSON object containing exactly:

```json
{
  "id": "asset-001",
  "length": 123,
  "meta": {}
}
```

The required core members are:

```text
id
length
meta
```

No other top-level record member is permitted.

Application-specific values belong inside `meta`.

## id

`id` must:

- be a JSON string;
- be non-empty;
- contain no lone Unicode surrogate;
- be unique within the archive.

ID equality uses the decoded JSON string value.

CDECK002 does not impose an application naming convention on IDs.

## length

`length` is the exact payload length in bytes.

It must be a non-negative integer in the JavaScript safe-integer range:

```text
0 <= length <= 9007199254740991
```

Boolean values are not integers for this purpose.

A zero-length payload is valid.

## meta

`meta` must be a JSON object.

It may contain arbitrary application-defined keys and nested values that satisfy the CDECK002 JSON value profile.

The core preserves metadata but does not interpret its meaning.

Unknown metadata keys are valid.

## JSON Value Profile

Values inside `meta`, and strings used by the core, are restricted to a deterministic cross-runtime subset of JSON.

Allowed values are:

- null;
- booleans;
- integers from `-9007199254740991` through `9007199254740991`;
- strings without lone Unicode surrogates;
- arrays containing allowed values;
- objects with string keys and allowed values.

Floating-point JSON numbers are not allowed.

NaN and Infinity are not valid JSON and are rejected.

All object keys are themselves subject to the string rules.

## Duplicate JSON Members

Conforming CDECK002 JSON objects must not contain duplicate member names.

The reference Python parser detects and rejects duplicate JSON members.

The browser implementation uses native `JSON.parse()`. Native parsing does not expose duplicate-member information after parsing, so the browser reader cannot reliably detect this condition.

A file containing duplicate JSON members remains non-conforming even when an implementation cannot detect the violation.

## Payload Addressing

Payload offsets are derived and are not stored.

Define:

```text
payloadStart = 12 + indexLength
```

For record `i`:

```text
relativeOffset[0] = 0

relativeOffset[i] =
    sum(record[j].length for j < i)

absoluteOffset[i] =
    payloadStart + relativeOffset[i]
```

The cumulative payload length must remain within the JavaScript safe-integer range.

The complete derived file length is:

```text
expectedFileLength =
    payloadStart + sum(record[*].length)
```

`expectedFileLength` must also be a safe integer.

When the complete representation length is known, it must equal `expectedFileLength` exactly.

Trailing bytes and truncated payloads therefore make the archive invalid.

## Payload Rules

Payload bytes:

- appear in record order;
- are contiguous;
- contain no gaps or padding;
- contain exactly `length` bytes for each record;
- are opaque to the CDECK core.

The core does not decode, normalize, transcode, recompress, or otherwise rewrite payload contents.

## Numeric Model

CDECK002 deliberately uses the JavaScript safe-integer model rather than BigInt or uint64 fields.

The maximum supported integer is:

```text
9007199254740991
```

This applies to record lengths, cumulative payload lengths, derived addresses, derived file length, and HTTP range arithmetic.

Values at and above 2^32 are valid when they remain safe integers.

The value 2^53 is not valid.

## Canonical Encoding

Structural validity and canonical encoding are separate concepts.

A structurally valid CDECK002 archive may contain a semantically equivalent noncanonical JSON representation.

The reference canonical encoder uses the semantic equivalent of:

```python
json.dumps(
    records,
    ensure_ascii=False,
    allow_nan=False,
    separators=(",", ":"),
    sort_keys=True,
).encode("utf-8")
```

Therefore canonical index output:

- is UTF-8 without BOM;
- contains no insignificant whitespace;
- recursively sorts object keys;
- emits non-ASCII Unicode directly when JSON escaping is not otherwise required;
- uses deterministic JSON string escaping;
- contains no floating-point numbers;
- contains no duplicate object members.

Canonical key ordering follows the reference Unicode string ordering used by the canonical encoder.

`python3 cdeck.py verify FILE --canonical` reconstructs the semantic index, re-encodes it canonically, and requires byte-for-byte equality with the stored index.

## Builder Manifest

The reference builder consumes a UTF-8 JSON manifest whose top level is an array.

Each manifest entry contains exactly:

```json
{
  "id": "asset-001",
  "path": "payloads/example.bin",
  "meta": {}
}
```

`path` is builder-only input and is not written to the CDECK002 record.

Manifest rules:

- no more than 4,000 entries;
- exact entry shape `{id,path,meta}`;
- `id` follows normal record ID rules;
- `meta` follows the normal metadata profile;
- `path` is a non-empty string;
- absolute payload paths are rejected;
- paths containing a `..` component are rejected;
- resolved payload files must remain inside the manifest directory;
- symlinks resolving inside that directory are allowed;
- symlinks escaping that directory are rejected;
- each resolved payload must be a regular file.

Payload length is derived from the source file size.

Manifest order becomes record and payload order.

The builder does not infer IDs, media types, or metadata from filenames.

## Reference Build Semantics

The reference builder:

- validates the manifest;
- derives record lengths from source payload files;
- canonicalizes the index;
- copies payload bytes unchanged;
- writes through a temporary file in the destination directory;
- flushes and fsyncs the completed temporary output;
- verifies the generated archive against the source manifest and payloads;
- atomically replaces the requested destination.

The output path must not replace the manifest or any source payload.

Equivalent source inputs produce byte-identical output.

## Source Verification

Source-aware verification rebuilds the expected record model from the manifest.

For each record it requires:

- exact equality between the derived source record and archive record;
- SHA-256 equality between the source payload and embedded payload slice.

SHA-256 is used by verification tooling only.

CDECK002 does not store payload checksums in the wire format.

## Inspection

Inspection tooling may expose derived information that is not present on the wire.

The reference Python inspector adds an absolute `offset` field to its inspection result.

`offset` is not a CDECK002 record member and must not be serialized into the index.

## Byte Source Contract

A byte source conceptually provides:

```text
read(start, length, signal?)
```

`start` must be a non-negative safe integer.

`length` must be a positive safe integer.

`start + length` must remain a safe integer.

A successful non-zero read returns exactly `length` bytes.

The optional `signal` is used for cancellation by sources that support it.

## Browser Open Contract

`openCdeck(source)` requires an object providing `read(start, length)`.

Opening proceeds as:

```text
read(0, 12)
validate header
read(12, indexLength)
decode and validate index
derive payload addresses
validate complete size when source.size is known
```

Opening does not read payload bytes.

The returned deck exposes public records containing only:

```text
id
length
meta
```

Payload offsets are private implementation state.

`deck.read(record, signal)` accepts only a record object belonging to that deck.

For a non-empty record, it requests exactly the derived payload range and verifies the returned byte length.

For a zero-length record, it returns an empty byte array without calling the underlying source.

## Buffered Parsing

`parseCdeckBuffer(input)` accepts an ArrayBuffer or typed-array view.

Typed-array byte offsets and byte lengths are respected.

The complete input representation length must exactly equal the length derived from the index.

Payload reads are served from local slices.

## HTTP Source

`createHttpSource(url, options)` implements an exact-read source using HTTP GET requests.

It does not require a preliminary HEAD request.

For each uncached request it sends:

```text
Range: bytes=start-(start + length - 1)
```

Only HTTP status 200 and 206 are accepted.

## HTTP 206 Behavior

For `206 Partial Content`:

- the body must contain exactly the requested number of bytes;
- a visible `Content-Range` must syntactically describe the requested range;
- a numeric complete-size value must be a safe integer;
- the requested end must not exceed the numeric complete size;
- repeated numeric complete-size observations must agree.

A missing `Content-Range`, or a total represented as `*`, leaves complete source size unknown.

## HTTP Representation Identity

The first visible strong ETag establishes the reference HTTP source snapshot identity.

After a strong ETag is locked, later network responses must present that same strong ETag.

Weak ETags do not establish byte identity.

A source without a visible strong ETag remains usable but does not receive the same hard snapshot guarantee.

## HTTP 200 Fallback

A server may ignore Range and return `200 OK` with the complete representation.

The reference source supports this as a bounded fallback.

The default maximum full response is:

```text
64 * 1024 * 1024 bytes
```

This may be changed with `maxFullBytes`.

If `Content-Length` is present on a 200 response:

- it must be a non-negative safe integer;
- it must not exceed `maxFullBytes`.

The declared bound is checked before buffering the body.

After buffering:

- actual body length must not exceed `maxFullBytes`;
- actual body length becomes the known source size;
- any previously observed source size must agree;
- later reads are satisfied from the cached complete body without another fetch.

A requested slice extending past the complete body is rejected.

## Network Errors and Cancellation

HTTP and network failures are surfaced as CDECK errors.

An underlying AbortError is preserved rather than wrapped as a generic network failure.

## Validation Requirements

CDECK files must be treated as untrusted input.

Reference implementations reject at minimum:

- headers shorter than 12 bytes;
- wrong magic;
- unsupported CDECK generations;
- zero indexLength;
- indexLength beyond the 16 MiB runtime guard;
- truncated indexes;
- UTF-8 BOM;
- malformed UTF-8;
- invalid JSON;
- non-array top-level indexes;
- more than 4,000 records;
- non-object records;
- missing core members;
- unexpected core members;
- empty or non-string IDs;
- duplicate IDs;
- invalid lengths;
- non-object meta values;
- forbidden JSON values;
- cumulative payload length beyond safe-integer range;
- derived file length beyond safe-integer range;
- complete representation length mismatch when known;
- short payload reads;
- invalid HTTP ranges;
- inconsistent source sizes;
- changed locked strong ETags;
- unsupported HTTP status codes;
- network failures.

Bounds must be checked before attacker-controlled values drive range arithmetic, allocation, or slicing.

## Shared Conformance Vectors

The repository contains the shared CDECK002 conformance corpus in:

```text
tests/vectors/
```

`tests/vectors/vectors.json` defines expected structural results consumed by both Python and JavaScript tests.

The corpus includes canonical and noncanonical valid archives, malformed framing, malformed UTF-8 and JSON, record-shape failures, metadata-profile failures, numeric boundary failures, zero-length payloads, overflow, truncation, trailing bytes, and a known CDECK001 migration result.

The vector generator must reproduce committed vector bytes deterministically.

## CDECK001 Migration

CDECK001 compatibility is intentionally isolated from the CDECK002 runtime core.

The standalone migrator:

```text
tools/cdeck001_to_002.py
```

validates a legacy CDECK001 archive, maps legacy card metadata into the CDECK002 `meta` object, preserves record order, preserves payload order, copies payload bytes unchanged, and emits canonical CDECK002.

The migration tool is not part of the CDECK002 wire format.

## Deterministic Output

Canonical generation preserves:

- manifest record order;
- exact payload bytes;
- deterministic recursive JSON key order;
- compact JSON separators;
- UTF-8 without BOM;
- no timestamps;
- no filesystem metadata;
- no randomness;
- no compression.

## Non-Goals

CDECK002 does not define:

- database transactions;
- in-place mutation;
- compression;
- encryption;
- embedded payload checksums;
- stored payload offsets;
- MIME inference;
- media decoding;
- application rendering;
- application metadata schemas;
- backend services.

These are application or deployment concerns, not core format responsibilities.
