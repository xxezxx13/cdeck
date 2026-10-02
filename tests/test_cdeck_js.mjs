import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  CDeckError,
  createHttpSource,
  openCdeck,
  parseCdeckBuffer,
} from "../cdeck.js";

const encoder = new TextEncoder();
const httpSource = (url, fetch, options = {}) => (
  createHttpSource(url, { ...options, fetch })
);

const makeRecord = (updates = {}) => ({
  id: "asset",
  length: 1,
  meta: { name: "Example" },
  ...updates,
});

function makeArchive(index, payload = new Uint8Array()) {
  const raw = encoder.encode(JSON.stringify(index));
  const bytes = new Uint8Array(12 + raw.byteLength + payload.byteLength);
  bytes.set(encoder.encode("CDECK002"), 0);
  new DataView(bytes.buffer).setUint32(8, raw.byteLength, true);
  bytes.set(raw, 12);
  bytes.set(payload, 12 + raw.byteLength);
  return bytes;
}

function indexSource(records, size = null) {
  const raw = encoder.encode(JSON.stringify(records));
  const header = new Uint8Array(12);
  header.set(encoder.encode("CDECK002"), 0);
  new DataView(header.buffer).setUint32(8, raw.byteLength, true);
  return {
    size,
    async read(start, length) {
      if (start === 0) return header.slice(0, length);
      if (start === 12) return raw.slice(0, length);
      throw new Error(`unexpected read: ${start},${length}`);
    },
  };
}

test("V3: parses exact generic record shape", () => {
  const result = parseCdeckBuffer(
    makeArchive(
      [
        makeRecord({
          length: 3,
          meta: { nested: { value: 1 } },
        }),
      ],
      Uint8Array.from([1, 2, 3]),
    ),
  );
  assert.equal(result.format, "CDECK002");
  assert.equal(result.recordCount, 1);
  assert.equal(result.payloadBytes, 3);
  assert.equal(result.records[0].length, 3);
  assert.equal(result.records[0].meta.nested.value, 1);
  assert.deepEqual(
    Object.keys(result.records[0]).sort(),
    ["id", "length", "meta"],
  );
  assert.equal(result.records[0].offset, undefined);
  assert.equal(result.records[0].jpegOffset, undefined);
});

test("V3: index must be a top-level array", () => {
  assert.throws(
    () => parseCdeckBuffer(makeArchive({ records: [] })),
    /index must be an array/,
  );
});

test("V3: record shape is exact", () => {
  const missing = makeRecord();
  delete missing.length;
  assert.throws(
    () => parseCdeckBuffer(makeArchive([missing])),
    /missing required member: length/,
  );
  assert.throws(
    () => parseCdeckBuffer(makeArchive([makeRecord({ extra: true })])),
    /unexpected member: extra/,
  );
});

test("V3: IDs are non-empty and unique", () => {
  assert.throws(
    () => parseCdeckBuffer(makeArchive([makeRecord({ id: "" })])),
    /id must not be empty/,
  );
  assert.throws(
    () => parseCdeckBuffer(
      makeArchive([
        makeRecord({ id: "same" }),
        makeRecord({ id: "same" }),
      ]),
    ),
    /duplicate id/,
  );
});

test("V3: lengths use safe-integer rules", () => {
  for (const [value, pattern] of [
    [true, /length must be an integer/],
    [-1, /length out of range/],
    [2 ** 53, /length out of range/],
  ]) {
    assert.throws(
      () => parseCdeckBuffer(makeArchive([makeRecord({ length: value })])),
      pattern,
    );
  }
});

test("V3: zero-length payload is valid", () => {
  const result = parseCdeckBuffer(
    makeArchive([makeRecord({ length: 0 })]),
  );
  assert.equal(result.payloadBytes, 0);
  assert.equal(result.records[0].length, 0);
});

test("V3: meta must be an object", () => {
  for (const meta of [null, [], "bad", 1]) {
    assert.throws(
      () => parseCdeckBuffer(makeArchive([makeRecord({ meta })])),
      /meta must be an object/,
    );
  }
});

test("V3: metadata profile accepts recursive safe values", () => {
  const result = parseCdeckBuffer(
    makeArchive([
      makeRecord({
        length: 0,
        meta: {
          array: [null, true, false, -Number.MAX_SAFE_INTEGER, Number.MAX_SAFE_INTEGER],
          text: "é😀",
          nested: { "😀": 2, "β": 1, a: 0 },
        },
      }),
    ]),
  );
  assert.equal(result.records[0].meta.text, "é😀");
});

test("V3: metadata profile rejects forbidden values", () => {
  const cases = [
    [{ value: 1.5 }, /JSON floats are not allowed/],
    [{ value: Number.MAX_SAFE_INTEGER + 1 }, /JSON integer exceeds safe integer limit/],
    [{ value: "\ud800" }, /JSON string contains lone surrogate/],
    [{ "\ud800": "bad" }, /JSON string contains lone surrogate/],
  ];
  for (const [meta, pattern] of cases) {
    assert.throws(
      () => parseCdeckBuffer(
        makeArchive([makeRecord({ length: 0, meta })]),
      ),
      pattern,
    );
  }
});

test("V3: unknown metadata keys are preserved", () => {
  const result = parseCdeckBuffer(
    makeArchive([
      makeRecord({
        length: 0,
        meta: { future: { nested: true } },
      }),
    ]),
  );
  assert.equal(result.records[0].meta.future.nested, true);
});

test("V3: ArrayBuffer and typed-array views are accepted", () => {
  const archive = makeArchive(
    [makeRecord()],
    Uint8Array.from([1]),
  );
  const buffer = archive.buffer.slice(
    archive.byteOffset,
    archive.byteOffset + archive.byteLength,
  );
  assert.equal(parseCdeckBuffer(buffer).recordCount, 1);
  const wrapped = new Uint8Array(archive.byteLength + 20);
  wrapped.set(archive, 10);
  assert.equal(
    parseCdeckBuffer(
      wrapped.subarray(10, 10 + archive.byteLength),
    ).recordCount,
    1,
  );
});

test("V3: UTF-8 BOM is rejected", () => {
  const raw = encoder.encode("[]");
  const bytes = new Uint8Array(15 + raw.byteLength);
  bytes.set(encoder.encode("CDECK002"), 0);
  new DataView(bytes.buffer).setUint32(8, raw.byteLength + 3, true);
  bytes.set([0xef, 0xbb, 0xbf], 12);
  bytes.set(raw, 15);
  assert.throws(() => parseCdeckBuffer(bytes), /UTF-8 BOM/);
});

test("V3: malformed JSON and UTF-8 are rejected", () => {
  for (const [raw, pattern] of [
    [Uint8Array.from([0x7b]), /invalid JSON index/],
    [Uint8Array.from([0xff]), /invalid UTF-8 index/],
  ]) {
    const bytes = new Uint8Array(12 + raw.byteLength);
    bytes.set(encoder.encode("CDECK002"), 0);
    new DataView(bytes.buffer).setUint32(8, raw.byteLength, true);
    bytes.set(raw, 12);
    assert.throws(() => parseCdeckBuffer(bytes), pattern);
  }
});

test("V3: CDECK001 is rejected as an unsupported generation", () => {
  const bytes = new Uint8Array(12);
  bytes.set(encoder.encode("CDECK001"), 0);
  new DataView(bytes.buffer).setUint32(8, 1, true);
  assert.throws(
    () => parseCdeckBuffer(bytes),
    /unsupported CDECK generation/,
  );
});

test("V3: header validation remains strict", () => {
  const wrong = new Uint8Array(12);
  wrong.set(encoder.encode("BADDECK!"), 0);
  new DataView(wrong.buffer).setUint32(8, 1, true);
  assert.throws(() => parseCdeckBuffer(wrong), /wrong magic/);
  const future = new Uint8Array(12);
  future.set(encoder.encode("CDECK999"), 0);
  new DataView(future.buffer).setUint32(8, 1, true);
  assert.throws(
    () => parseCdeckBuffer(future),
    /unsupported CDECK generation/,
  );
  const zero = new Uint8Array(12);
  zero.set(encoder.encode("CDECK002"), 0);
  assert.throws(
    () => parseCdeckBuffer(zero),
    /indexLength must be greater than zero/,
  );
});

test("V3: index runtime guard is 16 MiB", () => {
  const bytes = new Uint8Array(12);
  bytes.set(encoder.encode("CDECK002"), 0);
  new DataView(bytes.buffer).setUint32(
    8,
    (16 * 1024 * 1024) + 1,
    true,
  );
  assert.throws(
    () => parseCdeckBuffer(bytes),
    /16 MiB runtime limit/,
  );
});

test("V3: HTTP 200 fallback rejects declared oversized body before buffering", async () => {
  let bodyReads = 0;
  const source = httpSource(
    "test-source",
    async () => ({
      status: 200,
      headers: new Headers({ "Content-Length": "3" }),
      async arrayBuffer() {
        bodyReads += 1;
        return Uint8Array.from([1, 2, 3]).buffer;
      },
    }),
    { maxFullBytes: 2 },
  );
  await assert.rejects(
    source.read(0, 1),
    /full response exceeds maxFullBytes/,
  );
  assert.equal(bodyReads, 0);
});

test("V3: HTTP 200 fallback checks actual buffered size", async () => {
  let bodyReads = 0;
  const source = httpSource(
    "test-source",
    async () => ({
      status: 200,
      headers: new Headers(),
      async arrayBuffer() {
        bodyReads += 1;
        return Uint8Array.from([1, 2, 3]).buffer;
      },
    }),
    { maxFullBytes: 2 },
  );
  await assert.rejects(
    source.read(0, 1),
    /full response exceeds maxFullBytes/,
  );
  assert.equal(bodyReads, 1);
});

test("V3: maxFullBytes option requires a non-negative safe integer", () => {
  assert.throws(
    () => createHttpSource("test-source", { maxFullBytes: -1 }),
    /maxFullBytes must be a non-negative safe integer/,
  );
  assert.throws(
    () => createHttpSource(
      "test-source",
      { maxFullBytes: Number.MAX_SAFE_INTEGER + 1 },
    ),
    /maxFullBytes must be a non-negative safe integer/,
  );
});

test("HTTP source reads exact 206 range", async () => {
  const calls = [];

  const source = httpSource(
    "https://example.test/collection.cdeck",
    async (url, options) => {
      calls.push({ url, options });

      return new Response(
        Uint8Array.from([2, 3, 4]),
        {
          status: 206,
          headers: {
            "Content-Range": "bytes 2-4/10",
          },
        },
      );
    },
  );

  assert.deepEqual(
    [...await source.read(2, 3)],
    [2, 3, 4],
  );

  assert.equal(calls.length, 1);
  assert.equal(calls[0].options.method, "GET");
  assert.equal(
    calls[0].options.headers.Range,
    "bytes=2-4",
  );
});

test("HTTP source caches 200 fallback", async () => {
  let calls = 0;

  const source = httpSource(
    "https://example.test/collection.cdeck",
    async () => {
      calls += 1;

      return new Response(
        Uint8Array.from([0, 1, 2, 3, 4, 5]),
        { status: 200 },
      );
    },
  );

  assert.deepEqual(
    [...await source.read(2, 2)],
    [2, 3],
  );

  assert.deepEqual(
    [...await source.read(4, 2)],
    [4, 5],
  );

  assert.equal(calls, 1);
});

test("HTTP source rejects short 206 body", async () => {
  const source = httpSource(
    "https://example.test/collection.cdeck",
    async () => new Response(
      Uint8Array.from([0, 1]),
      {
        status: 206,
        headers: {
          "Content-Range": "bytes 0-2/10",
        },
      },
    ),
  );

  await assert.rejects(
    source.read(0, 3),
    /partial response length mismatch/,
  );
});

test("HTTP source validates Content-Range", async () => {
  const source = httpSource(
    "https://example.test/collection.cdeck",
    async () => new Response(
      Uint8Array.from([0, 1, 2]),
      {
        status: 206,
        headers: {
          "Content-Range": "bytes 1-3/10",
        },
      },
    ),
  );

  await assert.rejects(
    source.read(0, 3),
    /Content-Range does not match/,
  );
});

test("HTTP source permits hidden Content-Range", async () => {
  const source = httpSource(
    "https://example.test/collection.cdeck",
    async () => new Response(
      Uint8Array.from([7, 8]),
      { status: 206 },
    ),
  );

  assert.deepEqual(
    [...await source.read(5, 2)],
    [7, 8],
  );
});

test("HTTP source rejects unexpected status", async () => {
  const source = httpSource(
    "https://example.test/collection.cdeck",
    async () => new Response(
      null,
      { status: 503 },
    ),
  );

  await assert.rejects(
    source.read(0, 1),
    /unexpected HTTP status: 503/,
  );
});

test("HTTP source reports network failure", async () => {
  const source = httpSource(
    "https://example.test/collection.cdeck",
    async () => {
      throw new Error("offline");
    },
  );

  await assert.rejects(
    source.read(0, 1),
    /network failure: offline/,
  );
});

test("HTTP source rejects unsafe bounds before fetch", async () => {
  let calls = 0;
  const source = httpSource(
    "https://example.test/collection.cdeck",
    async () => {
      calls += 1;
      throw new Error("fetch must not run");
    },
  );

  await assert.rejects(
    source.read(-1, 1),
    /read start must be a non-negative safe integer/,
  );

  await assert.rejects(
    source.read(0, 0),
    /read length must be a positive safe integer/,
  );

  await assert.rejects(
    source.read(Number.MAX_SAFE_INTEGER + 1, 1),
    /read start must be a non-negative safe integer/,
  );

  await assert.rejects(
    source.read(0, Number.MAX_SAFE_INTEGER + 1),
    /read length must be a positive safe integer/,
  );

  await assert.rejects(
    source.read(Number.MAX_SAFE_INTEGER, 1),
    /read range exceeds safe integer limit/,
  );

  assert.equal(calls, 0);
});

test("V3: HTTP source addresses ranges beyond 32-bit", async () => {
  const starts = [
    (2 ** 32) - 1,
    2 ** 32,
    (2 ** 32) + 1,
    8 * (2 ** 30),
    Number.MAX_SAFE_INTEGER - 1,
  ];
  const seen = [];

  const source = httpSource(
    "test-source",
    async (url, options) => {
      const range = options.headers.Range;
      const start = starts[seen.length];

      seen.push(range);

      return new Response(
        Uint8Array.from([seen.length]),
        {
          status: 206,
          headers: {
            "Content-Range": `bytes ${start}-${start}/${Number.MAX_SAFE_INTEGER}`,
          },
        },
      );
    },
  );

  for (const start of starts) {
    const bytes = await source.read(start, 1);
    assert.equal(bytes.byteLength, 1);
  }

  assert.deepEqual(
    seen,
    starts.map((start) => `bytes=${start}-${start}`),
  );
  assert.equal(source.size, Number.MAX_SAFE_INTEGER);
});

test("V3: HTTP source rejects 2^53 Content-Range total", async () => {
  const source = httpSource(
    "test-source",
    async () => new Response(
      Uint8Array.from([1]),
      {
        status: 206,
        headers: {
          "Content-Range": "bytes 0-0/9007199254740992",
        },
      },
    ),
  );

  await assert.rejects(
    source.read(0, 1),
    /invalid Content-Range total/,
  );
});

test("HTTP 200 fallback validates bounds", async () => {
  const source = httpSource(
    "https://example.test/collection.cdeck",
    async () => new Response(
      Uint8Array.from([0, 1]),
      { status: 200 },
    ),
  );

  await assert.rejects(
    source.read(1, 2),
    /full response shorter than requested range/,
  );
});


test("V3: strong ETag locks HTTP source snapshot", async () => {
  let request = 0;
  let bodyReads = 0;
  const quote = String.fromCharCode(34);
  const source = httpSource(
    "test-source",
    async () => {
      request += 1;
      return {
        status: 206,
        headers: new Headers({
          "Content-Range": request === 1 ? "bytes 0-1/10" : "bytes 2-3/10",
          ETag: quote + (request === 1 ? "snapshot-a" : "snapshot-b") + quote,
        }),
        async arrayBuffer() {
          bodyReads += 1;
          return Uint8Array.from([1, 2]).buffer;
        },
      };
    },
  );

  await source.read(0, 2);

  await assert.rejects(
    source.read(2, 2),
    /ETag changed/,
  );

  assert.equal(bodyReads, 1);
});

test("V3: weak ETag does not lock HTTP source snapshot", async () => {
  let request = 0;
  const quote = String.fromCharCode(34);
  const source = httpSource(
    "test-source",
    async () => {
      request += 1;
      return new Response(
        Uint8Array.from([request, request]),
        {
          status: 206,
          headers: {
            "Content-Range": request === 1 ? "bytes 0-1/10" : "bytes 2-3/10",
            ETag: "W/" + quote + (request === 1 ? "one" : "two") + quote,
          },
        },
      );
    },
  );

  await source.read(0, 2);
  await source.read(2, 2);

  assert.equal(request, 2);
});

test("V3: HTTP source captures and enforces numeric total size", async () => {
  let request = 0;
  let bodyReads = 0;
  const source = httpSource(
    "test-source",
    async () => {
      request += 1;
      return {
        status: 206,
        headers: new Headers({
          "Content-Range": request === 1 ? "bytes 0-1/10" : "bytes 2-3/11",
        }),
        async arrayBuffer() {
          bodyReads += 1;
          return Uint8Array.from([1, 2]).buffer;
        },
      };
    },
  );

  assert.equal(source.size, null);
  await source.read(0, 2);
  assert.equal(source.size, 10);

  await assert.rejects(
    source.read(2, 2),
    /file size changed/,
  );

  assert.equal(bodyReads, 1);
  assert.equal(source.size, 10);
});

test("V3: hidden Content-Range leaves HTTP source size unknown", async () => {
  const source = httpSource(
    "test-source",
    async () => new Response(
      Uint8Array.from([7, 8]),
      { status: 206 },
    ),
  );

  await source.read(5, 2);
  assert.equal(source.size, null);
});

test("V3: HTTP 200 fallback records complete source size", async () => {
  const source = httpSource(
    "test-source",
    async () => new Response(
      Uint8Array.from([0, 1, 2, 3, 4, 5]),
      { status: 200 },
    ),
  );

  assert.equal(source.size, null);
  await source.read(2, 2);
  assert.equal(source.size, 6);
});

test("V3: HTTP source forwards optional abort signal", async () => {
  const controller = new AbortController();
  let seenSignal = null;
  const source = httpSource(
    "test-source",
    async (url, options) => {
      seenSignal = options.signal ?? null;
      return new Response(
        Uint8Array.from([1, 2]),
        {
          status: 206,
          headers: {
            "Content-Range": "bytes 0-1/2",
          },
        },
      );
    },
  );

  await source.read(0, 2, controller.signal);
  assert.equal(seenSignal, controller.signal);
});


test("V3: parsed deck reads payload and keeps offsets private", async () => {
  const records = [
    { id: "data", length: 3, meta: { kind: "opaque" } },
    { id: "empty", length: 0, meta: {} },
  ];
  const raw = encoder.encode(JSON.stringify(records));
  const archive = new Uint8Array(12 + raw.length + 3);
  archive.set(encoder.encode("CDECK002"), 0);
  new DataView(archive.buffer).setUint32(8, raw.length, true);
  archive.set(raw, 12);
  archive.set([0, 255, 7], 12 + raw.length);
  const deck = parseCdeckBuffer(archive);
  assert.equal(deck.records[0].offset, undefined);
  assert.deepEqual(Object.keys(deck.records[0]), ["id", "length", "meta"]);
  assert.deepEqual([...await deck.read(deck.records[0])], [0, 255, 7]);
  assert.deepEqual([...await deck.read(deck.records[1])], []);
  await assert.rejects(
    deck.read({ ...deck.records[0] }),
    /record does not belong to deck/,
  );
});

test("V3: source deck reads exact payload range and forwards signal", async () => {
  const records = [
    { id: "data", length: 3, meta: {} },
    { id: "empty", length: 0, meta: {} },
  ];
  const raw = encoder.encode(JSON.stringify(records));
  const archive = new Uint8Array(12 + raw.length + 3);
  archive.set(encoder.encode("CDECK002"), 0);
  new DataView(archive.buffer).setUint32(8, raw.length, true);
  archive.set(raw, 12);
  archive.set([4, 5, 6], 12 + raw.length);
  const calls = [];
  const source = {
    size: archive.length,
    async read(start, length, signal) {
      calls.push({ start, length, signal });
      return archive.slice(start, start + length);
    },
  };
  const deck = await openCdeck(source);
  const openedCalls = calls.length;
  const signal = new AbortController().signal;
  assert.deepEqual([...await deck.read(deck.records[0], signal)], [4, 5, 6]);
  assert.equal(calls.length, openedCalls + 1);
  assert.equal(calls.at(-1).start, deck.payloadStart);
  assert.equal(calls.at(-1).length, 3);
  assert.equal(calls.at(-1).signal, signal);
  assert.deepEqual([...await deck.read(deck.records[1], signal)], []);
  assert.equal(calls.length, openedCalls + 1);
});

test("V3: deck.read rejects short source payload", async () => {
  const records = [{ id: "data", length: 3, meta: {} }];
  const raw = encoder.encode(JSON.stringify(records));
  const archive = new Uint8Array(12 + raw.length + 3);
  archive.set(encoder.encode("CDECK002"), 0);
  new DataView(archive.buffer).setUint32(8, raw.length, true);
  archive.set(raw, 12);
  const payloadStart = 12 + raw.length;
  const source = {
    size: archive.length,
    async read(start, length) {
      if (start === payloadStart) return new Uint8Array(2);
      return archive.slice(start, start + length);
    },
  };
  const deck = await openCdeck(source);
  await assert.rejects(
    deck.read(deck.records[0]),
    /payload read length mismatch/,
  );
});

test("V3: openCdeck accepts generic records", async () => {
  const result = await openCdeck(
    indexSource([
      makeRecord({ length: 3 }),
    ]),
  );
  assert.equal(result.recordCount, 1);
  assert.equal(result.records[0].length, 3);
  assert.equal(result.records[0].offset, undefined);
  assert.deepEqual(
    Object.keys(result.records[0]).sort(),
    ["id", "length", "meta"],
  );
});

test("V3: openCdeck enforces known source size", async () => {
  const records = [makeRecord({ length: 3 })];
  const raw = encoder.encode(JSON.stringify(records));
  const expected = 12 + raw.byteLength + 3;
  const accepted = await openCdeck(
    indexSource(records, expected),
  );
  assert.equal(accepted.expectedFileLength, expected);
  await assert.rejects(
    openCdeck(indexSource(records, expected + 1)),
    /file length mismatch/,
  );
});

test("V3: record address arithmetic crosses 32-bit", async () => {
  for (const length of [
    (2 ** 32) - 1,
    2 ** 32,
    (2 ** 32) + 1,
    8 * (2 ** 30),
  ]) {
    const result = await openCdeck(
      indexSource([makeRecord({ length })]),
    );
    assert.equal(result.payloadBytes, length);
  }
});

test("V3: cumulative safe-integer overflow is rejected", async () => {
  await assert.rejects(
    openCdeck(
      indexSource([
        makeRecord({
          id: "first",
          length: Number.MAX_SAFE_INTEGER,
        }),
        makeRecord({
          id: "second",
          length: 1,
        }),
      ]),
    ),
    /cumulative payload length exceeds safe integer limit/,
  );
});

test("V3: openCdeck reads only header and index", async () => {
  const records = [makeRecord({ length: 3 })];
  const raw = encoder.encode(JSON.stringify(records));
  const header = new Uint8Array(12);
  header.set(encoder.encode("CDECK002"), 0);
  new DataView(header.buffer).setUint32(8, raw.byteLength, true);
  const calls = [];
  const result = await openCdeck({
    async read(start, length) {
      calls.push([start, length]);
      if (start === 0) return header.slice(0, length);
      if (start === 12) return raw.slice(0, length);
      throw new Error("payload must not be read");
    },
  });
  assert.deepEqual(
    calls,
    [[0, 12], [12, raw.byteLength]],
  );
  assert.equal(result.recordCount, 1);
});

test("V3: openCdeck rejects bad header before index read", async () => {
  const calls = [];
  await assert.rejects(
    openCdeck({
      async read(start, length) {
        calls.push([start, length]);
        return Uint8Array.from([
          0x42, 0x41, 0x44, 0x43,
          0x44, 0x45, 0x43, 0x4b,
          1, 0, 0, 0,
        ]).slice(0, length);
      },
    }),
    /wrong magic/,
  );
  assert.deepEqual(calls, [[0, 12]]);
});

const SHARED_VECTOR_ROOT = new URL("./vectors/", import.meta.url);
const sharedVectors = JSON.parse(
  readFileSync(
    new URL("vectors.json", SHARED_VECTOR_ROOT),
    "utf8",
  ),
);

const escapePattern = (value) => (
  value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
);

test("V3: shared CDECK002 vector corpus", () => {
  assert.equal(sharedVectors.length, 21);

  for (const vector of sharedVectors) {
    const bytes = readFileSync(
      new URL(vector.file, SHARED_VECTOR_ROOT),
    );

    if (!vector.valid) {
      assert.throws(
        () => parseCdeckBuffer(bytes),
        new RegExp(escapePattern(vector.error)),
        vector.file,
      );
      continue;
    }

    const deck = parseCdeckBuffer(bytes);
    assert.equal(
      deck.recordCount,
      vector.recordCount,
      vector.file,
    );

    if (!vector.expect) continue;

    const record = deck.records[0];
    assert.equal(record.id, vector.expect.id, vector.file);
    assert.equal(record.length, vector.expect.length, vector.file);

    if (vector.expect.meta) {
      assert.deepEqual(
        record.meta,
        vector.expect.meta,
        vector.file,
      );
    }

    if (vector.expect.payloadSha256) {
      const indexLength = bytes.readUInt32LE(8);
      const payload = bytes.subarray(12 + indexLength);
      const digest = createHash("sha256")
        .update(payload)
        .digest("hex");
      assert.equal(
        digest,
        vector.expect.payloadSha256,
        vector.file,
      );
    }
  }
});

test("V3: HTTP 206 end-to-end open and payload reads stay range-addressed", async () => {
  const records = [
    { id: "asset", length: 3, meta: { kind: "binary" } },
  ];
  const raw = encoder.encode(JSON.stringify(records));
  const archive = new Uint8Array(12 + raw.byteLength + 3);
  archive.set(encoder.encode("CDECK002"), 0);
  new DataView(archive.buffer).setUint32(8, raw.byteLength, true);
  archive.set(raw, 12);
  archive.set([7, 8, 9], 12 + raw.byteLength);

  const calls = [];
  const source = httpSource(
    "test-source",
    async (url, options) => {
      const range = options.headers.Range;
      const match = /^bytes=(\d+)-(\d+)$/.exec(range);
      assert.ok(match, range);
      const start = Number(match[1]);
      const end = Number(match[2]);
      calls.push(range);
      return new Response(
        archive.slice(start, end + 1),
        {
          status: 206,
          headers: {
            "Content-Range": `bytes ${start}-${end}/${archive.length}`,
            ETag: "\"snapshot\"",
          },
        },
      );
    },
  );

  const deck = await openCdeck(source);
  assert.deepEqual(
    calls,
    [
      "bytes=0-11",
      `bytes=12-${11 + raw.byteLength}`,
    ],
  );

  assert.deepEqual(
    [...await deck.read(deck.records[0])],
    [7, 8, 9],
  );
  assert.equal(calls.length, 3);
  assert.equal(
    calls[2],
    `bytes=${deck.payloadStart}-${deck.payloadStart + 2}`,
  );
});

test("V3: HTTP 200 end-to-end fallback fetches whole representation once", async () => {
  const records = [
    { id: "asset", length: 3, meta: { kind: "binary" } },
  ];
  const raw = encoder.encode(JSON.stringify(records));
  const archive = new Uint8Array(12 + raw.byteLength + 3);
  archive.set(encoder.encode("CDECK002"), 0);
  new DataView(archive.buffer).setUint32(8, raw.byteLength, true);
  archive.set(raw, 12);
  archive.set([4, 5, 6], 12 + raw.byteLength);

  const calls = [];
  const source = httpSource(
    "test-source",
    async (url, options) => {
      calls.push(options.headers.Range);
      return new Response(
        archive,
        {
          status: 200,
          headers: {
            "Content-Length": String(archive.length),
            ETag: "\"snapshot\"",
          },
        },
      );
    },
  );

  const deck = await openCdeck(source);
  assert.deepEqual(calls, ["bytes=0-11"]);
  assert.deepEqual(
    [...await deck.read(deck.records[0])],
    [4, 5, 6],
  );
  assert.deepEqual(
    [...await deck.read(deck.records[0])],
    [4, 5, 6],
  );
  assert.equal(calls.length, 1);
  assert.equal(source.size, archive.length);
});
