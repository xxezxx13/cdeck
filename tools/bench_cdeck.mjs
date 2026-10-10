import { performance } from "node:perf_hooks";
import { createBlobSource, openCdeck } from "../cdeck.js";

const encoder = new TextEncoder();
const median = values => values.toSorted((a, b) => a - b)[Math.floor(values.length / 2)];

function makeArchive(count, recordBytes) {
  const records = Array.from({ length: count }, (_, i) => ({
    id: `record-${i}`,
    length: recordBytes,
    meta: { sample: true },
  }));
  const index = encoder.encode(JSON.stringify(records));
  const header = new Uint8Array(12);
  header.set(encoder.encode("CDECK002"));
  new DataView(header.buffer).setUint32(8, index.byteLength, true);
  const payload = new Uint8Array(count * recordBytes);
  for (let i = 0; i < count; i++) {
    payload.fill(i % 251, i * recordBytes, (i + 1) * recordBytes);
  }
  return new Blob([header, index, payload]);
}

async function runOnce(blob, count, recordBytes) {
  const base = createBlobSource(blob);
  let calls = 0;
  let bytes = 0;
  const source = {
    size: base.size,
    async read(start, length, signal) {
      calls++;
      bytes += length;
      return base.read(start, length, signal);
    },
  };
  const start = performance.now();
  const deck = await openCdeck(source);
  const opened = performance.now();
  if (deck.recordCount !== count) throw new Error("record count mismatch");
  const picks = [0, Math.floor(count / 2), count - 1];
  for (const i of picks) {
    const data = await deck.read(deck.records[i]);
    if (data.byteLength !== recordBytes || data[0] !== i % 251) {
      throw new Error(`payload mismatch at record ${i}`);
    }
  }
  const finished = performance.now();
  const expectedBytes = blob.size - count * recordBytes + picks.length * recordBytes;
  if (calls !== 5 || bytes !== expectedBytes) {
    throw new Error("selective-read accounting mismatch");
  }
  return { openMs: opened - start, readMs: finished - opened, calls, bytes };
}

console.log(`CDECK Blob benchmark | Node ${process.version}`);
console.log("Synthetic in-memory archives; 2 warmups, 5 measured runs");

for (const count of [251, 500]) {
  const recordBytes = 100_000;
  const blob = makeArchive(count, recordBytes);
  const samples = [];
  for (let i = 0; i < 7; i++) {
    const result = await runOnce(blob, count, recordBytes);
    if (i >= 2) samples.push(result);
  }
  const open = median(samples.map(s => s.openMs));
  const read = median(samples.map(s => s.readMs));
  const { calls, bytes } = samples[0];
  console.log(`Records: ${count} | Archive: ${(blob.size / 1048576).toFixed(2)} MiB`);
  console.log(`  Open median: ${open.toFixed(3)} ms`);
  console.log(`  Three reads median: ${read.toFixed(3)} ms`);
  console.log(`  Source calls: ${calls} | Bytes requested: ${bytes}`);
}

const { createHttpSource } = await import("../cdeck.js");

async function runHttpOnce(blob, count, recordBytes, status) {
  let requests = 0;
  let requestedBytes = 0;
  let returnedBytes = 0;

  const fakeFetch = async (_url, options) => {
    const range = /^bytes=(\d+)-(\d+)$/.exec(options.headers.Range);
    if (!range) throw new Error("Invalid benchmark Range");
    const begin = Number(range[1]);
    const finish = Number(range[2]) + 1;
    requests++;
    requestedBytes += finish - begin;

    const responseBlob = status === 206
      ? blob.slice(begin, finish)
      : blob;
    returnedBytes += responseBlob.size;

    return new Response(responseBlob, {
      status,
      headers: status === 206
        ? {
          "Content-Range": `bytes ${begin}-${finish - 1}/${blob.size}`,
          ETag: `"snapshot"`,
        }
        : {
          "Content-Length": String(blob.size),
          ETag: `"snapshot"`,
        },
    });
  };

  const source = createHttpSource("benchmark.cdeck", {
    fetch: fakeFetch,
  });

  const start = performance.now();
  const deck = await openCdeck(source);
  const opened = performance.now();

  if (deck.recordCount !== count || source.size !== blob.size) {
    throw new Error("HTTP archive metadata mismatch");
  }

  for (const i of [0, Math.floor(count / 2), count - 1]) {
    const data = await deck.read(deck.records[i]);
    if (data.byteLength !== recordBytes || data[0] !== i % 251) {
      throw new Error(`HTTP payload mismatch at record ${i}`);
    }
  }

  const finished = performance.now();
  const selectiveBytes = blob.size - count * recordBytes + 3 * recordBytes;
  const expectedRequests = status === 206 ? 5 : 1;
  const expectedRequested = status === 206 ? selectiveBytes : 12;
  const expectedReturned = status === 206 ? selectiveBytes : blob.size;

  if (requests !== expectedRequests ||
      requestedBytes !== expectedRequested ||
      returnedBytes !== expectedReturned) {
    throw new Error(`HTTP ${status} request accounting mismatch`);
  }

  return {
    openMs: opened - start,
    readMs: finished - opened,
    requests,
    requestedBytes,
    returnedBytes,
  };
}

console.log("");
console.log("CDECK simulated HTTP benchmark");
console.log("Native Response streams; no network latency");

for (const count of [251, 500]) {
  const recordBytes = 100_000;
  const blob = makeArchive(count, recordBytes);

  for (const status of [206, 200]) {
    const samples = [];
    for (let i = 0; i < 7; i++) {
      const result = await runHttpOnce(blob, count, recordBytes, status);
      if (i >= 2) samples.push(result);
    }

    const open = median(samples.map(s => s.openMs));
    const read = median(samples.map(s => s.readMs));
    const { requests, requestedBytes, returnedBytes } = samples[0];

    console.log(`Records: ${count} | HTTP ${status}`);
    console.log(`  Open median: ${open.toFixed(3)} ms`);
    console.log(`  Three reads median: ${read.toFixed(3)} ms`);
    console.log(`  Requests: ${requests}`);
    console.log(`  Requested range bytes: ${requestedBytes}`);
    console.log(`  Returned body bytes: ${returnedBytes}`);
  }
}

const { parseCdeckBuffer } = await import("../cdeck.js");

async function runBufferedOnce(blob, count, recordBytes) {
  const started = performance.now();
  const bytes = new Uint8Array(await blob.arrayBuffer());
  const loaded = performance.now();
  const deck = parseCdeckBuffer(bytes);
  const opened = performance.now();

  if (deck.recordCount !== count || bytes.byteLength !== blob.size) {
    throw new Error("buffered archive metadata mismatch");
  }

  for (const i of [0, Math.floor(count / 2), count - 1]) {
    const data = await deck.read(deck.records[i]);
    if (data.byteLength !== recordBytes || data[0] !== i % 251) {
      throw new Error(`buffered payload mismatch at record ${i}`);
    }
  }

  const finished = performance.now();
  return {
    bufferMs: loaded - started,
    parseMs: opened - loaded,
    readMs: finished - opened,
    bytes: bytes.byteLength,
  };
}

console.log("");
console.log("CDECK full-buffer parsing benchmark");
console.log("In-memory Blob buffering plus synchronous parsing");

for (const count of [251, 500]) {
  const recordBytes = 100_000;
  const blob = makeArchive(count, recordBytes);
  const samples = [];

  for (let i = 0; i < 7; i++) {
    const result = await runBufferedOnce(blob, count, recordBytes);
    if (i >= 2) samples.push(result);
  }

  const buffer = median(samples.map(s => s.bufferMs));
  const parse = median(samples.map(s => s.parseMs));
  const read = median(samples.map(s => s.readMs));

  console.log(`Records: ${count} | Full buffer`);
  console.log(`  Buffer median: ${buffer.toFixed(3)} ms`);
  console.log(`  Parse median: ${parse.toFixed(3)} ms`);
  console.log(`  Three reads median: ${read.toFixed(3)} ms`);
  console.log(`  Full archive bytes: ${samples[0].bytes}`);
}
