import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";

import {
  CDeckError,
  createHttpSource,
  createLazyImageLoader,
  openCdeck,
  parseCdeckBuffer,
} from "../cdeck.js";

const fixture = (name) => (
  fs.readFileSync(
    new URL(`fixtures/${name}`, import.meta.url),
  )
);

const sharedVectors = JSON.parse(
  fs.readFileSync(
    new URL("fixtures/vectors.json", import.meta.url),
    "utf8",
  ),
);

test(
  "V2: Python and JavaScript share fixture vectors",
  () => {
    for (const vector of sharedVectors) {
      if (vector.valid) {
        const result = parseCdeckBuffer(
          fixture(vector.file),
        );
        assert.equal(
          result.recordCount,
          vector.recordCount,
        );
        assert.equal(
          result.expectedFileLength,
          vector.expectedFileLength,
        );
      } else {
        assert.throws(
          () => parseCdeckBuffer(
            fixture(vector.file),
          ),
          (error) => (
            error instanceof CDeckError
            && error.message.includes(vector.error)
          ),
          vector.file,
        );
      }
    }
  },
);

const invalid = [
  ["wrong-magic.cdeck", /wrong magic/],
  [
    "unsupported-generation.cdeck",
    /unsupported CDECK generation/,
  ],
  [
    "zero-index.cdeck",
    /indexLength must be greater than zero/,
  ],
  [
    "malformed-utf8.cdeck",
    /invalid UTF-8 index/,
  ],
  [
    "invalid-json.cdeck",
    /invalid JSON index/,
  ],
  [
    "missing-required-member.cdeck",
    /missing required member/,
  ],
  [
    "duplicate-id.cdeck",
    /duplicate id/,
  ],
  [
    "invalid-quantity.cdeck",
    /quantity out of range/,
  ],
  [
    "invalid-jpeg-length.cdeck",
    /jpegLength out of range/,
  ],
  [
    "truncated-payload.cdeck",
    /file length mismatch/,
  ],
];

test(
  "valid fixture parses with absolute JPEG offset",
  () => {
    const result = parseCdeckBuffer(
      fixture("valid-one-record.cdeck"),
    );

    assert.equal(
      result.format,
      "CDECK001",
    );

    assert.equal(
      result.indexLength,
      205,
    );

    assert.equal(
      result.payloadStart,
      217,
    );

    assert.equal(
      result.recordCount,
      1,
    );

    assert.equal(
      result.expectedFileLength,
      11720,
    );

    assert.equal(
      result.records[0].jpegOffset,
      217,
    );

    assert.equal(
      result.records[0].jpegLength,
      11503,
    );
  },
);

for (const [name, pattern] of invalid) {
  test(
    `rejects ${name}`,
    () => {
      assert.throws(
        () => parseCdeckBuffer(fixture(name)),
        pattern,
      );
    },
  );
}

test(
  "accepts ArrayBuffer input",
  () => {
    const buffer = fixture(
      "valid-one-record.cdeck",
    );

    const arrayBuffer = buffer.buffer.slice(
      buffer.byteOffset,
      buffer.byteOffset + buffer.byteLength,
    );

    assert.equal(
      parseCdeckBuffer(arrayBuffer).recordCount,
      1,
    );
  },
);

test(
  "respects typed-array byteOffset",
  () => {
    const source = fixture(
      "valid-one-record.cdeck",
    );

    const wrapped = new Uint8Array(
      source.byteLength + 20,
    );

    wrapped.set(source, 10);

    const view = wrapped.subarray(
      10,
      10 + source.byteLength,
    );

    assert.equal(
      parseCdeckBuffer(view).recordCount,
      1,
    );
  },
);

test(
  "rejects UTF-8 BOM",
  () => {
    const index = new TextEncoder().encode(
      "{\"decks\":[]}",
    );

    const bytes = new Uint8Array(
      12 + 3 + index.byteLength,
    );

    bytes.set(
      new TextEncoder().encode("CDECK001"),
      0,
    );

    new DataView(bytes.buffer).setUint32(
      8,
      3 + index.byteLength,
      true,
    );

    bytes.set(
      [0xef, 0xbb, 0xbf],
      12,
    );

    bytes.set(index, 15);

    assert.throws(
      () => parseCdeckBuffer(bytes),
      /UTF-8 BOM/,
    );
  },
);

test(
  "rejects invalid input object",
  () => {
    assert.throws(
      () => parseCdeckBuffer({}),
      CDeckError,
    );
  },
);

test(
  "native JSON parsing does not detect duplicate keys",
  () => {
    const result = parseCdeckBuffer(
      fixture("duplicate-json-member.cdeck"),
    );

    assert.equal(
      result.records[0].id,
      "duplicate",
    );
  },
);


test("HTTP source reads exact 206 range", async () => {
  const calls = [];

  const source = createHttpSource(
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

  const source = createHttpSource(
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
  const source = createHttpSource(
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
  const source = createHttpSource(
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
  const source = createHttpSource(
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
  const source = createHttpSource(
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
  const source = createHttpSource(
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

test("HTTP source rejects invalid bounds before fetch", async () => {
  const source = createHttpSource(
    "https://example.test/collection.cdeck",
    async () => {
      throw new Error("fetch must not run");
    },
  );

  await assert.rejects(
    source.read(-1, 1),
    /read start must be a non-negative integer/,
  );

  await assert.rejects(
    source.read(0, 0),
    /read length must be a positive integer/,
  );

  await assert.rejects(
    source.read(0xffffffff, 1),
    /read range exceeds 32-bit file limit/,
  );
});

test("HTTP 200 fallback validates bounds", async () => {
  const source = createHttpSource(
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


test("V2: strong ETag locks HTTP source snapshot", async () => {
  let request = 0;
  let bodyReads = 0;
  const quote = String.fromCharCode(34);
  const source = createHttpSource(
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

test("V2: weak ETag does not lock HTTP source snapshot", async () => {
  let request = 0;
  const quote = String.fromCharCode(34);
  const source = createHttpSource(
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

test("V2: HTTP source captures and enforces numeric total size", async () => {
  let request = 0;
  let bodyReads = 0;
  const source = createHttpSource(
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

test("V2: hidden Content-Range leaves HTTP source size unknown", async () => {
  const source = createHttpSource(
    "test-source",
    async () => new Response(
      Uint8Array.from([7, 8]),
      { status: 206 },
    ),
  );

  await source.read(5, 2);
  assert.equal(source.size, null);
});

test("V2: HTTP 200 fallback records complete source size", async () => {
  const source = createHttpSource(
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

function httpArchiveSource(bytes, total) {
  const indexLength = new DataView(
    bytes.buffer,
    bytes.byteOffset,
    bytes.byteLength,
  ).getUint32(8, true);
  const payloadStart = 12 + indexLength;
  let request = 0;

  return createHttpSource(
    "test-source",
    async () => {
      request += 1;
      const start = request === 1 ? 0 : 12;
      const end = request === 1 ? 12 : payloadStart;

      return new Response(
        bytes.slice(start, end),
        {
          status: 206,
          headers: {
            "Content-Range": `bytes ${start}-${end - 1}/${total}`,
          },
        },
      );
    },
  );
}

test("V2: openCdeck accepts matching known source size", async () => {
  const bytes = fixture("valid-one-record.cdeck");
  const source = httpArchiveSource(bytes, bytes.byteLength);

  const result = await openCdeck(source);

  assert.equal(source.size, bytes.byteLength);
  assert.equal(result.expectedFileLength, bytes.byteLength);
  assert.equal(result.recordCount, 1);
});

test("V2: openCdeck rejects mismatched known source size", async () => {
  const bytes = fixture("valid-one-record.cdeck");
  const source = httpArchiveSource(
    bytes,
    bytes.byteLength + 1,
  );

  await assert.rejects(
    openCdeck(source),
    /file length mismatch/,
  );
});

function lazyHarness() {
  let instance;
  let observerCount = 0;

  class FakeObserver {
    constructor(callback) {
      this.callback = callback;
      this.observed = new Set();
      observerCount += 1;
      instance = this;
    }

    observe(target) {
      this.observed.add(target);
    }

    unobserve(target) {
      this.observed.delete(target);
    }

    disconnect() {
      this.observed.clear();
    }

    trigger(entries) {
      return this.callback(entries);
    }
  }

  return {
    FakeObserver,
    get instance() {
      return instance;
    },
    get observerCount() {
      return observerCount;
    },
  };
}

test("V2: HTTP source forwards optional abort signal", async () => {
  const controller = new AbortController();
  let seenSignal = null;
  const source = createHttpSource(
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

test("V2: lazy failure reports once and callback can retry", async () => {
  const harness = lazyHarness();
  const target = {};
  const record = {
    jpegOffset: 10,
    jpegLength: 1,
  };
  const errors = [];
  let attempts = 0;
  let retryPromise;
  let loader;

  loader = createLazyImageLoader(
    {
      async read() {
        attempts += 1;
        if (attempts === 1) {
          throw new Error("broken read");
        }
        return Uint8Array.from([1]);
      },
    },
    {
      IntersectionObserver: harness.FakeObserver,
      createObjectURL: () => "blob:retry",
      revokeObjectURL: () => {},
      onError(error, errorTarget, errorRecord) {
        errors.push([error, errorTarget, errorRecord]);
        retryPromise = loader.retry(errorTarget);
      },
    },
  );

  loader.observe(target, record);

  await harness.instance.trigger([
    {
      target,
      isIntersecting: true,
    },
  ]).catch(() => {});

  assert.equal(errors.length, 1);
  assert.equal(errors[0][0].message, "broken read");
  assert.equal(errors[0][1], target);
  assert.equal(errors[0][2], record);

  await retryPromise;

  assert.equal(attempts, 2);
  assert.equal(target.src, "blob:retry");
});

test("V2: retry never duplicates an in-flight load", async () => {
  const harness = lazyHarness();
  const target = {};
  let reads = 0;
  let resolveRead;

  const loader = createLazyImageLoader(
    {
      read() {
        reads += 1;
        return new Promise((resolve) => {
          resolveRead = resolve;
        });
      },
    },
    {
      IntersectionObserver: harness.FakeObserver,
      createObjectURL: () => "blob:single",
      revokeObjectURL: () => {},
    },
  );

  loader.observe(
    target,
    {
      jpegOffset: 20,
      jpegLength: 1,
    },
  );

  const pending = harness.instance.trigger([
    {
      target,
      isIntersecting: true,
    },
  ]);

  const retry = loader.retry(target);

  assert.equal(reads, 1);

  resolveRead(Uint8Array.from([1]));

  await pending;
  await retry;

  assert.equal(reads, 1);
  assert.equal(target.src, "blob:single");
});

test("V2: release aborts load and blocks late installation", async () => {
  const harness = lazyHarness();
  const target = {};
  let seenSignal = null;
  let resolveRead;

  const loader = createLazyImageLoader(
    {
      read(start, length, signal) {
        seenSignal = signal ?? null;
        return new Promise((resolve) => {
          resolveRead = resolve;
        });
      },
    },
    {
      IntersectionObserver: harness.FakeObserver,
      createObjectURL: () => "blob:late",
      revokeObjectURL: () => {},
    },
  );

  loader.observe(
    target,
    {
      jpegOffset: 30,
      jpegLength: 1,
    },
  );

  const pending = harness.instance.trigger([
    {
      target,
      isIntersecting: true,
    },
  ]);

  loader.release(target);
  resolveRead(Uint8Array.from([1]));

  await pending;

  assert.ok(seenSignal);
  assert.equal(seenSignal.aborted, true);
  assert.equal(target.src, undefined);
});

test("V2: disconnect aborts active loads without onError", async () => {
  const harness = lazyHarness();
  const targets = [{}, {}];
  const signals = [];
  const errors = [];

  const loader = createLazyImageLoader(
    {
      read(start, length, signal) {
        signals.push(signal ?? null);
        return new Promise((resolve, reject) => {
          if (!signal) {
            reject(new Error("missing abort signal"));
            return;
          }

          signal.addEventListener(
            "abort",
            () => {
              const error = new Error("aborted");
              error.name = "AbortError";
              reject(error);
            },
            { once: true },
          );
        });
      },
    },
    {
      IntersectionObserver: harness.FakeObserver,
      createObjectURL: () => "blob:unused",
      revokeObjectURL: () => {},
      onError: (error) => errors.push(error),
    },
  );

  for (let index = 0; index < targets.length; index += 1) {
    loader.observe(
      targets[index],
      {
        jpegOffset: 40 + index,
        jpegLength: 1,
      },
    );
  }

  const pending = harness.instance.trigger(
    targets.map((target) => ({
      target,
      isIntersecting: true,
    })),
  ).catch((error) => error);

  await Promise.resolve();
  loader.disconnect();

  const outcome = await pending;

  assert.equal(outcome, undefined);
  assert.equal(signals.length, 2);
  assert.ok(signals.every((signal) => signal?.aborted));
  assert.equal(errors.length, 0);
});

test("lazy loader does not fetch offscreen images", async () => {
  const harness = lazyHarness();
  let reads = 0;
  const image = {};

  const loader = createLazyImageLoader(
    {
      async read() {
        reads += 1;
        return Uint8Array.from([1, 2, 3]);
      },
    },
    {
      IntersectionObserver: harness.FakeObserver,
      createObjectURL: () => "blob:test",
      revokeObjectURL: () => {},
    },
  );

  loader.observe(
    image,
    {
      jpegOffset: 100,
      jpegLength: 3,
    },
  );

  assert.equal(harness.observerCount, 1);
  assert.equal(reads, 0);

  await harness.instance.trigger([
    {
      target: image,
      isIntersecting: false,
    },
  ]);

  assert.equal(reads, 0);
  assert.equal(image.src, undefined);
});

test("intersection fetches JPEG range once", async () => {
  const harness = lazyHarness();
  const reads = [];
  const image = {};
  let blob;

  const loader = createLazyImageLoader(
    {
      async read(start, length) {
        reads.push([start, length]);
        return Uint8Array.from([9, 8, 7]);
      },
    },
    {
      IntersectionObserver: harness.FakeObserver,
      createObjectURL(value) {
        blob = value;
        return "blob:image";
      },
      revokeObjectURL: () => {},
    },
  );

  loader.observe(
    image,
    {
      jpegOffset: 321,
      jpegLength: 3,
    },
  );

  const entry = {
    target: image,
    isIntersecting: true,
  };

  await Promise.all([
    harness.instance.trigger([entry]),
    harness.instance.trigger([entry]),
  ]);

  assert.deepEqual(
    reads,
    [[321, 3]],
  );

  assert.equal(image.src, "blob:image");
  assert.equal(blob.type, "image/jpeg");
  assert.equal(blob.size, 3);
});

test("lazy loader revokes Blob URL on release", async () => {
  const harness = lazyHarness();
  const image = {};
  const revoked = [];

  const loader = createLazyImageLoader(
    {
      async read() {
        return Uint8Array.from([1]);
      },
    },
    {
      IntersectionObserver: harness.FakeObserver,
      createObjectURL: () => "blob:release",
      revokeObjectURL: (url) => revoked.push(url),
    },
  );

  loader.observe(
    image,
    {
      jpegOffset: 12,
      jpegLength: 1,
    },
  );

  await harness.instance.trigger([
    {
      target: image,
      isIntersecting: true,
    },
  ]);

  loader.release(image);

  assert.deepEqual(
    revoked,
    ["blob:release"],
  );
});

test("lazy loader disconnect revokes all loaded URLs", async () => {
  const harness = lazyHarness();
  const revoked = [];
  let number = 0;

  const loader = createLazyImageLoader(
    {
      async read() {
        return Uint8Array.from([1]);
      },
    },
    {
      IntersectionObserver: harness.FakeObserver,
      createObjectURL: () => `blob:${++number}`,
      revokeObjectURL: (url) => revoked.push(url),
    },
  );

  const first = {};
  const second = {};

  loader.observe(
    first,
    {
      jpegOffset: 10,
      jpegLength: 1,
    },
  );

  loader.observe(
    second,
    {
      jpegOffset: 11,
      jpegLength: 1,
    },
  );

  await harness.instance.trigger([
    {
      target: first,
      isIntersecting: true,
    },
    {
      target: second,
      isIntersecting: true,
    },
  ]);

  loader.disconnect();

  assert.deepEqual(
    revoked.sort(),
    ["blob:1", "blob:2"],
  );
});


test("openCdeck reads only header and index", async () => {
  const bytes = fixture("valid-one-record.cdeck");
  const calls = [];

  const result = await openCdeck({
    async read(start, length) {
      calls.push([start, length]);
      return bytes.slice(start, start + length);
    },
  });

  assert.deepEqual(
    calls,
    [[0, 12], [12, 205]],
  );

  assert.equal(result.recordCount, 1);
  assert.equal(result.payloadStart, 217);
  assert.equal(result.records[0].jpegOffset, 217);
  assert.equal(result.expectedFileLength, 11720);
});

test("lazy loader can observe card and fill image", async () => {
  const harness = lazyHarness();
  const card = {};
  const image = {};
  let intersections = 0;

  const loader = createLazyImageLoader(
    {
      async read() {
        return Uint8Array.from([1, 2]);
      },
    },
    {
      IntersectionObserver: harness.FakeObserver,
      createObjectURL: () => "blob:child",
      revokeObjectURL: () => {},
      onIntersect: () => {
        intersections += 1;
      },
    },
  );

  loader.observe(
    card,
    {
      jpegOffset: 10,
      jpegLength: 2,
    },
    image,
  );

  await harness.instance.trigger([
    {
      target: card,
      isIntersecting: true,
    },
  ]);

  assert.equal(intersections, 1);
  assert.equal(image.src, "blob:child");
});


function makeArchive(index, payload = new Uint8Array()) {
  const encoded = new TextEncoder().encode(
    JSON.stringify(index),
  );

  const bytes = new Uint8Array(
    12 + encoded.byteLength + payload.byteLength,
  );

  bytes.set(
    new TextEncoder().encode("CDECK001"),
    0,
  );

  new DataView(bytes.buffer).setUint32(
    8,
    encoded.byteLength,
    true,
  );

  bytes.set(encoded, 12);
  bytes.set(payload, 12 + encoded.byteLength);

  return bytes;
}

test("Phase 12: unknown JSON members are ignored", () => {
  const archive = makeArchive(
    {
      futureTopLevel: "ignored",
      decks: [
        {
          id: "deck_future",
          name: "Future",
          quantity: 1,
          brand: "",
          printer: "",
          jpegLength: 1,
          futureRecordMember: {
            nested: true,
          },
        },
      ],
    },
    Uint8Array.from([0xff]),
  );

  const result = parseCdeckBuffer(archive);

  assert.equal(result.recordCount, 1);
  assert.equal(
    result.records[0].id,
    "deck_future",
  );
  assert.equal(
    result.records[0].futureRecordMember.nested,
    true,
  );
});

test("Phase 12: maximum-size index is accepted", () => {
  const prefix = new TextEncoder().encode(
    JSON.stringify({ decks: [] }),
  );

  const indexLength = 2_000_000;
  const bytes = new Uint8Array(
    12 + indexLength,
  );

  bytes.set(
    new TextEncoder().encode("CDECK001"),
    0,
  );

  new DataView(bytes.buffer).setUint32(
    8,
    indexLength,
    true,
  );

  bytes.set(prefix, 12);
  bytes.fill(
    0x20,
    12 + prefix.byteLength,
  );

  const result = parseCdeckBuffer(bytes);

  assert.equal(
    result.indexLength,
    2_000_000,
  );

  assert.equal(result.recordCount, 0);
  assert.equal(
    result.expectedFileLength,
    bytes.byteLength,
  );
});

test("Phase 12: openCdeck rejects bad header before index read", async () => {
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

  assert.deepEqual(
    calls,
    [[0, 12]],
  );
});

test("Phase 12: repeated observer notifications stay single-load", async () => {
  const harness = lazyHarness();
  const target = {};
  let reads = 0;
  let urls = 0;

  const loader = createLazyImageLoader(
    {
      async read() {
        reads += 1;

        return Uint8Array.from([
          0xff,
          0xd8,
          0xff,
        ]);
      },
    },
    {
      IntersectionObserver: harness.FakeObserver,
      createObjectURL: () => {
        urls += 1;
        return "blob:repeat";
      },
      revokeObjectURL: () => {},
    },
  );

  loader.observe(
    target,
    {
      jpegOffset: 100,
      jpegLength: 3,
    },
  );

  const entry = {
    target,
    isIntersecting: true,
  };

  await harness.instance.trigger([entry]);
  await harness.instance.trigger([entry]);
  await harness.instance.trigger([entry]);

  assert.equal(reads, 1);
  assert.equal(urls, 1);
});
