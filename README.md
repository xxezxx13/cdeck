# CDECK

CDECK is a small, deterministic container format for bundling metadata and binary assets into one range-addressable file.

I originally built it for my personal playing-card collection. That site used a metadata file plus hundreds of separate JPEGs, and I wanted a cleaner deployment without adding a database or backend.

The original use case is still what motivated CDECK, but the format itself is now generic. The core does not know what an asset means, what media type it contains, or how an application should render it.

A CDECK file contains:

```text
12-byte header
UTF-8 JSON index
payload 0
payload 1
...
payload N
```

The browser can read the small header and index first, derive the byte location of every payload, and request individual payload ranges only when the application needs them.

CDECK is not trying to be a database, archive suite, media framework, or package ecosystem. It is intentionally small.

## Why I Built It

The original Cardeckatcher deployment looked roughly like this:

```text
metadata
+
hundreds of images
```

That worked, but I wanted one portable collection file while keeping ordinary static hosting and lazy access to individual assets.

That led to the central idea:

> Put metadata and payloads into one deterministic file, keep a compact index at the front, and use byte ranges to retrieve only what the application needs.

CDECK V3 generalizes that idea beyond playing cards. Card-specific schema, image rendering, JPEG assumptions, lazy image loading, Blob URL management, and other application behavior belong outside the core.

## Current Format

The current wire format is:

```text
CDECK002
```

CDECK software uses whole-number releases such as `v1`, `v2`, and `v3`. Software release numbers and file-format generations are separate.

CDECK V3 reads and writes `CDECK002`.

`CDECK001` is the previous generation. V3 intentionally does not keep permanent CDECK001 parsing inside the browser/runtime core. A standalone migration tool is provided instead.

## Record Model

The CDECK002 index is a top-level JSON array.

Every record has exactly three core members:

```json
{
  "id": "asset-001",
  "length": 123456,
  "meta": {
    "name": "Example"
  }
}
```

`id` identifies the record.

`length` is the exact payload length in bytes.

`meta` is an application-defined JSON object. CDECK validates its JSON value profile but does not assign domain meaning to its keys.

Payload offsets are not stored. They are derived from the header, encoded index length, record order, and preceding record lengths.

Zero-length payloads are valid.

## Building a CDECK File

The Python builder consumes a JSON manifest.

Example:

```json
[
  {
    "id": "asset-001",
    "path": "payloads/example.bin",
    "meta": {
      "name": "Example"
    }
  }
]
```

`path` is builder input only. It is not stored as a core record member.

Build with:

```bash
python3 cdeck.py build manifest.json collection.cdeck
```

Payload files may contain arbitrary binary data.

The builder keeps manifest order, copies payload bytes unchanged, rejects unsafe paths that escape the manifest source directory, permits symlinks that still resolve inside that directory, validates the generated archive, and replaces the destination atomically.

Equivalent inputs produce byte-identical canonical output.

## Inspecting a CDECK File

Inspect an archive:

```bash
python3 cdeck.py inspect collection.cdeck
```

Limit displayed records:

```bash
python3 cdeck.py inspect collection.cdeck --limit 10
```

Inspect one record by ID:

```bash
python3 cdeck.py inspect collection.cdeck --id asset-001
```

Emit machine-readable JSON:

```bash
python3 cdeck.py inspect collection.cdeck --json
```

## Verifying a CDECK File

Structural verification:

```bash
python3 cdeck.py verify collection.cdeck
```

Silent successful verification:

```bash
python3 cdeck.py verify collection.cdeck --quiet
```

Canonical byte-profile verification:

```bash
python3 cdeck.py verify collection.cdeck --canonical
```

Structural validity and canonical encoding are intentionally separate.

A structurally valid CDECK002 index may use a noncanonical JSON byte representation. `--canonical` reconstructs the semantic index using the deterministic CDECK profile and requires the stored index bytes to match exactly.

When the original manifest and payload files are available, source-aware verification can also compare the archive against its source inputs:

```bash
python3 cdeck.py verify collection.cdeck --source manifest.json
```

CDECK does not embed payload checksums in the wire format.

## Browser API

The public JavaScript surface is intentionally small:

```javascript
CDeckError
createHttpSource(url, options)
openCdeck(source)
parseCdeckBuffer(buffer)
```

A normal remote setup looks like:

```javascript
import {
  createHttpSource,
  openCdeck,
} from "./cdeck.js";

const source = createHttpSource("collection.cdeck");
const deck = await openCdeck(source);

const record = deck.records[0];
const bytes = await deck.read(record);
```

`deck.records` exposes public records containing `id`, `length`, and `meta`.

Physical offsets remain private implementation details.

`deck.read(record, signal)` retrieves exactly that record payload. A zero-length record returns an empty byte array without issuing a source read.

`parseCdeckBuffer()` provides the same deck/read model for an already-buffered local representation.

## HTTP Range Requests

`createHttpSource()` is designed for ordinary static HTTP hosting.

The reader first requests the 12-byte header, then the JSON index. Payloads can then be retrieved independently.

A payload request uses a normal HTTP byte range:

```text
Range: bytes=start-end
```

With `206 Partial Content`, CDECK validates exact response length and visible `Content-Range` information.

Numeric total-size observations must remain consistent. A visible strong ETag locks representation identity for later network reads; weak ETags do not establish that guarantee.

No preliminary `HEAD` request is required.

If a server ignores Range and returns `200 OK`, the complete representation may be buffered and cached instead. The default full-response ceiling is 64 MiB and can be changed with `maxFullBytes`.

```javascript
const source = createHttpSource("collection.cdeck", {
  maxFullBytes: 64 * 1024 * 1024,
});
```

This makes byte-range support an optimization rather than a correctness requirement for reasonably sized archives.

## Hosting

A normal static web server is enough.

Recommended content type:

```text
application/octet-stream
```

The `.cdeck` representation should be served without transparent transformation because byte addressing refers to the stored representation.

Servers with byte-range support provide the most efficient behavior.

## CDECK001 Migration

V3 includes a standalone converter for previous-generation archives:

```bash
python3 tools/cdeck001_to_002.py old.cdeck new.cdeck
```

The converter validates CDECK001 input, maps the legacy card fields into CDECK002 `meta`, preserves record and payload order, copies payload bytes unchanged, and writes canonical CDECK002 output.

Keeping migration outside the runtime allows the CDECK002 core to remain clean and generic.

## Canonical Encoding

CDECK002 uses a deliberately small deterministic JSON profile rather than a full external canonicalization standard.

Canonical writer output uses compact UTF-8 JSON without a BOM, deterministic recursive object-key ordering, and no insignificant whitespace.

The supported metadata value profile includes objects, arrays, strings, booleans, null, and safe integers.

Floating-point JSON numbers are not part of the CDECK002 metadata profile.

Integers and derived byte addresses must remain within JavaScript safe-integer range:

```text
0 .. 9007199254740991
```

## Format Layout

The header is exactly 12 bytes:

```text
Offset  Size  Field
0       8     ASCII CDECK002
8       4     indexLength, little-endian uint32
```

The UTF-8 JSON index starts at byte 12.

Raw payloads follow immediately after the index, contiguously and in record order.

There is no footer, payload table, stored payload offset, padding, alignment block, compression layer, checksum block, or MIME table.

The exact normative rules are in [`SPEC.md`](SPEC.md).

## Shared Conformance Vectors

The repository includes a deterministic CDECK002 conformance corpus under:

```text
tests/vectors/
```

`tests/vectors/vectors.json` is consumed by both the Python and JavaScript test suites.

The corpus covers valid canonical and noncanonical archives plus malformed framing, UTF-8, JSON, record shape, metadata, safe-integer, overflow, truncation, trailing-byte, zero-length, and migration cases.

The vector generator is deterministic, and the tests verify that regeneration produces identical bytes.

## Design Boundaries

The CDECK core owns:

- deterministic encoding
- record identity
- opaque application metadata
- payload lengths and order
- derived addressing
- structural and canonical validation
- numeric bounds
- HTTP range transport
- representation consistency
- the minimal browser read API

Applications own:

- metadata meaning
- media types
- image or document decoding
- rendering and DOM behavior
- lazy-loading policy
- Blob URL lifecycle
- application-specific validation

## Things CDECK Deliberately Does Not Add

- a database engine
- transactions
- in-place mutation
- compression
- encryption
- embedded payload checksums
- stored offsets
- MIME inference
- application schema rules
- a backend service

Those omissions are intentional.

## Repository Layout

```text
cdeck.py
cdeck.js
SPEC.md
tools/
tests/
tests/vectors/
.github/workflows/ci.yml
```

`cdeck.py` contains the generic builder, inspector, verifier, and canonical writer.

`cdeck.js` contains the browser parser, HTTP byte source, and deck payload-read API.

`tools/cdeck001_to_002.py` is the standalone legacy migrator.

`SPEC.md` is the normative CDECK002 wire-format specification.

Production code is kept under a hard 1,000-line ceiling across `cdeck.py` and `cdeck.js`.

## Requirements

The Python implementation uses only the standard library.

The JavaScript implementation uses standard modern web and JavaScript APIs.

No database or server-side runtime is required to read a hosted CDECK archive.

## Versioning

Software releases use whole numbers:

```text
v1
v2
v3
...
```

The wire format has its own generation identifier.

`v1` and `v2` used CDECK001.

`v3` uses CDECK002.

A future software release does not require a new wire generation unless the actual format contract changes.

## License

CDECK is released under the **GNU General Public License version 2 only (GPL-2.0-only)**.

See [`LICENSE`](LICENSE) for the full license text.
