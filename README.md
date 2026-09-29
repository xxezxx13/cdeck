# CDECK

CDECK is a small binary container format I built because I wanted a cleaner way to serve my personal playing-card collection online.

Originally, the collection was made up of a metadata file plus hundreds of separate JPEGs. That worked fine, but it always bothered me that such a simple, read-only collection needed so many individual files sitting on the server.

I kept thinking there had to be a simpler way.

So I made one.

Instead of deploying metadata alongside a pile of loose images, CDECK rolls the whole collection into a single file:

```text
collection.cdeck
```

The browser reads a small index first, figures out where each image lives inside the file, and then pulls individual JPEGs only when they're actually needed.

That's CDECK.

It isn't trying to be a database, an archive format, or a giant framework. It's just a small format that solves a problem I personally had.

## Why I Built It

The original version of my collection site was pretty straightforward:

```text
metadata file
+
hundreds of JPEGs
```

There was nothing especially wrong with that setup. It worked.

But I wanted the deployment to feel tighter.

I wanted one collection file instead of hundreds of individual collection assets. I wanted to keep using a normal static web server. I didn't want a backend, a database, a service worker, or some big dependency stack just to display a card collection.

I also didn't want to sacrifice lazy loading. There was no reason the browser should download every image up front just because everything lived in one file.

That led to the basic idea behind CDECK:

> Put the metadata and images into one deterministic file, keep a tiny index at the front, and use HTTP Range requests to grab images only when the browser needs them.

Once I realized that was enough, I tried pretty hard not to make it more complicated than it needed to be.

## What It Looks Like

A CDECK file is basically:

```text
12-byte header
JSON index
JPEG
JPEG
JPEG
JPEG
...
```

That's it.

The header identifies the format and tells the reader how large the JSON index is.

The index contains the metadata for each record and the length of its JPEG.

Because the JPEGs are stored back-to-back, the reader doesn't need to store an offset for every image. It can calculate them.

The current format is:

```text
CDECK001
```

The first software release is:

```text
v1
```

Those are intentionally separate. A future CDECK v2 doesn't automatically mean the file format has to change.

## What Goes in a Record?

A record looks like this:

```json
{
  "id": "deck_example",
  "name": "Example Deck",
  "quantity": 1,
  "brand": "Example Brand",
  "printer": "Example Printer",
  "jpegLength": 123456
}
```

The actual JPEG bytes aren't stored inside the JSON. They live in the payload section after the index.

The JSON just contains enough information for the reader to validate the record and find the image.

## Building a CDECK File

The Python tool can build a collection from a CSV and the JPEG files it references.

```bash
python3 cdeck.py build decks.csv collection.cdeck
```

The CSV format used by v1 is:

```text
src,name,quantity,brand,printer
```

The builder validates the input, builds the file, verifies it, and then replaces the destination atomically.

One thing I cared about from the beginning was deterministic output.

If the source data hasn't changed, rebuilding the collection should produce the exact same bytes.

## Inspecting One

You can inspect a CDECK file from the command line:

```bash
python3 cdeck.py inspect collection.cdeck
```

Or limit how many records it prints:

```bash
python3 cdeck.py inspect collection.cdeck --limit 10
```

This is mostly there because I wanted an easy way to look inside the format without writing another tool every time I was debugging something.

## Verifying One

Basic verification:

```bash
python3 cdeck.py verify collection.cdeck
```

Source-aware verification:

```bash
python3 cdeck.py verify collection.cdeck --source decks.csv
```

When the original source files are available, CDECK can compare every embedded JPEG against the original using SHA-256.

That was important to me because I wanted the format to copy the JPEGs exactly as they were, not silently recompress or alter them.

CDECK itself does not store checksums inside the file. Verification belongs in the tooling rather than the format.

## Using It in the Browser

The browser-side code lives in `cdeck.js`.

A basic setup looks like this:

```javascript
import {
  createHttpSource,
  createLazyImageLoader,
  openCdeck,
} from "./cdeck.js";

const source = createHttpSource("collection.cdeck");
const collection = await openCdeck(source);
```

The reader starts by requesting just the 12-byte header.

Then it requests the JSON index.

After that, it knows everything it needs to know about the collection without downloading the image payloads.

Images can then be loaded individually as they come into view.

## Lazy Loading

This was one of the parts I didn't want to lose when moving everything into one file.

CDECK uses `IntersectionObserver` so images can still load on demand.

When an image gets close to the viewport, the reader calculates its byte range, requests those bytes, creates an `image/jpeg` Blob, and gives the resulting object URL to the image element.

So even though the collection is physically stored as one file, the browser doesn't have to treat it like one giant download.

## HTTP Range Requests

CDECK works best when the server supports byte ranges.

A request might look like:

```text
Range: bytes=123456-234567
```

And a static server can return just that part of the file with:

```text
206 Partial Content
```

But I didn't want CDECK to completely fall apart on a server that ignores Range requests.

If the server responds with the entire file using `200 OK`, the reader can keep that response in memory and satisfy later reads from local slices instead.

So Range support is an optimization, not a hard requirement.

## Hosting

A normal static web server is enough.

The recommended content type is:

```text
application/octet-stream
```

The important thing is that the `.cdeck` file is served as-is.

The server should not transparently compress or transform it, because the browser is reading specific byte positions from the stored representation.

No special backend is required.

## What CDECK Doesn't Try to Be

I deliberately left a lot of things out.

There is no:

- SQL
- query language
- transaction system
- journaling
- database engine
- compression
- encryption
- signature system
- service worker
- IndexedDB cache
- SQLite
- WebAssembly
- thumbnail system
- arbitrary asset support
- plugin system

That's intentional.

I didn't want to build a general-purpose container format and then spend the rest of the project maintaining features I never needed.

CDECK is supposed to stay small enough that I can come back to it months later, read the code, and understand what it's doing without having to reconstruct an entire architecture in my head.

## The Format

The file starts with a 12-byte header:

```text
Offset  Size  Field
0       8     CDECK001
8       4     indexLength, little-endian uint32
```

After that comes the UTF-8 JSON index.

Then the JPEG payloads follow one after another in record order.

There is no footer.

There is no padding between images.

There are no stored JPEG offsets.

There is no stored total file size.

The reader derives all of that from the header and `jpegLength` values.

If you want the exact rules and validation requirements, see [`SPEC.md`](SPEC.md).

## A Few Things I Care About

CDECK v1 was built around a few simple rules:

- same input should produce the same output
- original JPEG bytes should stay unchanged
- metadata should survive exactly
- malformed files should fail cleanly
- browsers shouldn't need to download every image up front
- static hosting should be enough
- the implementation should stay small
- the format should be understandable without specialized tooling

Those constraints matter more to me than adding features for the sake of having them.

## Repository Layout

```text
cdeck.py
cdeck.js
SPEC.md
tests/
```

`cdeck.py` contains the builder, verifier, and inspection commands.

`cdeck.js` contains the browser reader, HTTP byte source, and lazy image loader.

`SPEC.md` is the actual file-format specification.

`tests/` contains the Python and JavaScript test suites along with valid and intentionally broken CDECK fixtures.

## Requirements

The Python side uses the standard library only.

No third-party Python packages are required.

For browser use, CDECK relies on normal modern web APIs including:

```text
fetch
TextDecoder
TextEncoder
Blob
URL.createObjectURL
IntersectionObserver
```

## Versioning

I'm using simple whole-number software releases:

```text
v1
v2
v3
...
```

The file format has its own generation number:

```text
CDECK001
```

If the software improves without changing the format, the format stays `CDECK001`.

If the wire format ever genuinely needs to change, I'd rather introduce a new generation cleanly than slowly mutate the meaning of the old one.

## License

CDECK is released under the **GNU General Public License version 2 only (GPL-2.0-only)**.

See [`LICENSE`](LICENSE) for the full license text.
