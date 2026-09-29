# CDECK File Format 1

## Status

This document specifies **CDECK File Format 1**.

Software release numbering is independent from file-format generation.

CDECK V1 reads and writes:

    CDECK File Format 1
    magic = "CDECK001"

## Purpose

CDECK001 is a self-contained, read-oriented container for the
Cardeckatcher playing-card collection.

It stores:

- structured deck metadata;
- one JPEG payload per deck;
- deterministic record order;
- derived random-access JPEG addressing.

CDECK001 does not provide a database engine, transactions, mutation,
compression, encryption, embedded checksums, thumbnails, arbitrary
schemas, or generic asset types.

## Binary Layout

A CDECK001 file is:

    12-byte header
    UTF-8 JSON index
    JPEG 0
    JPEG 1
    ...
    JPEG N

There is no footer, padding, checksum block, stored total file length,
or stored JPEG offset.

All multibyte binary integers are little-endian.

## Header

The header is exactly 12 bytes:

    Offset  Size  Type       Field
    0       8     byte[8]    magic
    8       4     uint32 LE  indexLength

Requirements:

    magic == ASCII "CDECK001"
    indexLength > 0
    indexLength <= 2,000,000
    payloadStart = 12 + indexLength

Readers must reject unsupported format generations.

## JSON Index

The index occupies exactly:

    bytes 12 through (12 + indexLength - 1)

The index:

- is UTF-8;
- must not contain a UTF-8 BOM;
- must reject malformed UTF-8;
- must contain valid JSON;
- has canonical top-level shape `{"decks":[]}`.

Maximum record count:

    4,000

Maximum encoded index size:

    2,000,000 UTF-8 bytes

Canonical writer output must be compact and deterministic.

Readers must not depend on JSON property order.

## Canonical Deck Record

Every canonical record contains:

    {
      "id": "deck-id",
      "name": "Deck Name",
      "quantity": 1,
      "brand": "Brand",
      "printer": "Printer",
      "jpegLength": 123456
    }

Canonical CDECK001 records do not contain:

    src
    jpegOffset
    mime
    width
    height
    checksum
    thumbnail

## Field Requirements

### id

- JSON string
- non-empty
- unique within the file
- at most 500 UTF-8 bytes

### name

- JSON string
- at most 1,000 UTF-8 bytes

### quantity

- JSON integer
- `1 <= quantity <= 10,000`

### brand

- JSON string
- at most 500 UTF-8 bytes
- empty string is valid

### printer

- JSON string
- at most 500 UTF-8 bytes
- empty string is valid

### jpegLength

- JSON integer
- greater than zero
- at most `0xffffffff`

No Unicode normalization form is imposed.

ID equality uses the decoded JSON string value.

## Cardeckatcher V1 ID Derivation

For the verified Cardeckatcher source collection:

    id = filename stem of src

Example:

    images/deck_188_1ST_v1.jpg
    ->
    deck_188_1ST_v1

The source CSV row order is preserved.

Records are not sorted by ID or numeric filename prefix.

## JPEG Addressing

JPEG offsets are derived rather than stored.

For record `i`:

    relativeOffset[0] = 0

    relativeOffset[i] =
        sum(decks[j].jpegLength for j < i)

    absoluteOffset[i] =
        payloadStart + relativeOffset[i]

Expected total file length:

    expectedFileLength =
        12 + indexLength + sum(decks[*].jpegLength)

Requirement:

    expectedFileLength <= 0xffffffff

JavaScript `Number` is sufficient for CDECK001.

`BigInt` is not required.

## JPEG Payload Rules

JPEG payloads must:

- appear in record order;
- be contiguous;
- contain no gaps;
- contain no padding;
- contain exactly `jpegLength` bytes for their record;
- be copied unchanged from the source JPEG.

CDECK treats JPEG bytes as opaque payload data.

CDECK does not decode, transcode, recompress, normalize, or otherwise
rewrite JPEG internals.

## Complete File Length

If the complete representation length is available, it must equal:

    12 + indexLength + sum(jpegLength)

A mismatch makes the CDECK file invalid.

## Unknown JSON Members

Canonical writers:

- emit every required member;
- do not emit duplicate member names;
- do not emit undefined members.

Canonical source builders reject unexpected source fields.

Browser readers:

- validate every required recognized member;
- ignore unknown top-level members;
- ignore unknown record members;
- reject missing required members;
- reject invalid recognized values.

Verifiers:

- validate required members;
- reject duplicate JSON member names;
- should warn about unknown members.

## Duplicate JSON Members

Canonical writers must never emit duplicate JSON object member names.

The reference Python verifier must reject duplicate member names.

Browser readers may use native `JSON.parse()` and are not required to
implement a custom duplicate-key tokenizer.

Duplicate-key files remain non-conforming.

## Exact Read Contract

A byte source conceptually provides:

    read(start, length)

and returns exactly the requested bytes or fails.

Reference remote parsing proceeds as:

    read(0, 12)
    validate header
    read(12, indexLength)
    fatal UTF-8 decode
    parse JSON
    validate index
    derive JPEG offsets
    render metadata
    retrieve JPEG ranges lazily

A fixed 64 KiB initial prefix is not part of CDECK001.

## HTTP Range Behavior

For HTTP range reads:

    Range: bytes=start-(start + length - 1)

For `206 Partial Content`:

- body length must equal the requested length;
- visible `Content-Range` should be validated;
- visible complete size must agree with the derived expected file length.

If a Range request receives `200 OK`:

- treat Range as unsupported or ignored;
- cache the complete response;
- validate its length after parsing the index;
- satisfy later exact reads from local slices.

A preliminary `HEAD` request is not required.

Range support is an optimization, not a correctness requirement.

## Lazy JPEG Retrieval

The browser implementation uses one `IntersectionObserver`.

Deck metadata may be rendered before image payload retrieval.

When a deck approaches the viewport:

    derived JPEG range
    -> exact read
    -> Blob(type="image/jpeg")
    -> URL.createObjectURL()
    -> <img>.src

Duplicate concurrent retrieval of the same image must be prevented.

Blob URLs must be revoked when no longer needed.

## Validation Requirements

Treat CDECK files as untrusted input.

Reject at minimum:

- header shorter than 12 bytes;
- wrong magic;
- unsupported generation;
- zero `indexLength`;
- `indexLength` above 2,000,000;
- truncated index;
- malformed UTF-8;
- invalid JSON;
- invalid top-level shape;
- non-array `decks`;
- more than 4,000 records;
- missing required members;
- recognized members with wrong types;
- over-limit UTF-8 strings;
- invalid `quantity`;
- invalid `jpegLength`;
- duplicate IDs;
- cumulative size above `0xffffffff`;
- complete size mismatch when known;
- short HTTP range response;
- impossible ranges;
- HTTP or network failure.

Attacker-controlled values must be bounds-checked before they drive
allocation or slicing.

Metadata inserted into HTML must use safe text/property APIs rather
than untrusted `innerHTML`.

## Integrity Verification

CDECK001 contains no embedded checksum.

Source-aware verification compares, for every deck:

    SHA-256(source JPEG)
    ==
    SHA-256(embedded JPEG slice)

It also verifies exact preservation of:

    name
    quantity
    brand
    printer

## Deterministic Output

Equivalent source inputs should produce byte-identical CDECK output.

Canonical generation therefore preserves:

- CSV row order;
- deterministic filename-stem IDs;
- stable JSON member order;
- compact JSON separators;
- UTF-8 without BOM;
- unchanged JPEG bytes;
- no timestamps;
- no filesystem metadata;
- no randomness;
- no compression.
