const MAGIC = "CDECK001";
const HEADER_SIZE = 12;
const MAX_INDEX_BYTES = 2_000_000;
const MAX_RECORDS = 4_000;
const MAX_FILE_BYTES = 0xffffffff;
const FIELDS = ["id", "name", "quantity", "brand", "printer", "jpegLength"];
const LIMITS = { id: 500, name: 1000, brand: 500, printer: 500 };
const decoder = new TextDecoder("utf-8", { fatal: true });
const encoder = new TextEncoder();
export class CDeckError extends Error {}
function bytesFrom(input) {
  if (input instanceof ArrayBuffer) return new Uint8Array(input);
  if (ArrayBuffer.isView(input)) {
    return new Uint8Array(input.buffer, input.byteOffset, input.byteLength);
  }
  throw new CDeckError("input must be an ArrayBuffer or typed array");
}
function requireString(record, field, number) {
  const value = record[field];
  if (typeof value !== "string") {
    throw new CDeckError(`record ${number}: ${field} must be a string`);
  }
  if (field === "id" && !value) {
    throw new CDeckError(`record ${number}: id must not be empty`);
  }
  if (encoder.encode(value).byteLength > LIMITS[field]) {
    throw new CDeckError(
      `record ${number}: ${field} exceeds UTF-8 byte limit`,
    );
  }
  return value;
}
function requireInteger(record, field, low, high, number) {
  const value = record[field];
  if (!Number.isInteger(value)) {
    throw new CDeckError(`record ${number}: ${field} must be an integer`);
  }
  if (value < low || value > high) {
    throw new CDeckError(`record ${number}: ${field} out of range`);
  }
  return value;
}
function decodeIndex(raw) {
  if (
    raw.length >= 3
    && raw[0] === 0xef
    && raw[1] === 0xbb
    && raw[2] === 0xbf
  ) {
    throw new CDeckError("index must not contain a UTF-8 BOM");
  }
  let text;
  try {
    text = decoder.decode(raw);
  } catch {
    throw new CDeckError("invalid UTF-8 index");
  }
  try {
    return JSON.parse(text);
  } catch {
    throw new CDeckError("invalid JSON index");
  }
}
function validateIndex(index) {
  if (
    index === null
    || typeof index !== "object"
    || Array.isArray(index)
  ) {
    throw new CDeckError("top-level JSON value must be an object");
  }
  if (!Object.hasOwn(index, "decks")) {
    throw new CDeckError("missing required top-level member: decks");
  }
  const decks = index.decks;
  if (!Array.isArray(decks)) {
    throw new CDeckError("decks must be an array");
  }
  if (decks.length > MAX_RECORDS) {
    throw new CDeckError("record count exceeds limit");
  }
  const ids = new Set();
  const records = [];
  let payloadBytes = 0;
  for (let i = 0; i < decks.length; i += 1) {
    const number = i + 1;
    const record = decks[i];
    if (
      record === null
      || typeof record !== "object"
      || Array.isArray(record)
    ) {
      throw new CDeckError(`record ${number}: must be an object`);
    }
    for (const field of FIELDS) {
      if (!Object.hasOwn(record, field)) {
        throw new CDeckError(
          `record ${number}: missing required member: ${field}`,
        );
      }
    }
    const id = requireString(record, "id", number);
    requireString(record, "name", number);
    requireString(record, "brand", number);
    requireString(record, "printer", number);
    requireInteger(record, "quantity", 1, 10_000, number);
    const jpegLength = requireInteger(
      record, "jpegLength", 1, MAX_FILE_BYTES, number,
    );
    if (ids.has(id)) {
      throw new CDeckError(`record ${number}: duplicate id: ${id}`);
    }
    ids.add(id);
    records.push({ ...record, jpegOffset: payloadBytes });
    payloadBytes += jpegLength;
    if (payloadBytes > MAX_FILE_BYTES) {
      throw new CDeckError("cumulative JPEG payload exceeds 32-bit limit");
    }
  }
  return { records, payloadBytes };
}
function validateRead(start, length) {
  if (!Number.isInteger(start) || start < 0) {
    throw new CDeckError("read start must be a non-negative integer");
  }
  if (!Number.isInteger(length) || length <= 0) {
    throw new CDeckError("read length must be a positive integer");
  }
  const end = start + length;
  if (!Number.isSafeInteger(end) || end > MAX_FILE_BYTES) {
    throw new CDeckError("read range exceeds 32-bit file limit");
  }
  return end;
}
function validateContentRange(value, start, end) {
  if (value === null) return;
  const match = /^bytes (\d+)-(\d+)\/(\d+|\*)$/i.exec(value);
  if (!match) throw new CDeckError("invalid Content-Range");
  if (
    Number(match[1]) !== start
    || Number(match[2]) !== end - 1
  ) {
    throw new CDeckError(
      "Content-Range does not match requested range",
    );
  }
  if (match[3] !== "*") {
    const total = Number(match[3]);
    if (
      !Number.isSafeInteger(total)
      || total > MAX_FILE_BYTES
      || end > total
    ) {
      throw new CDeckError("invalid Content-Range total");
    }
  }
}
function networkError(error) {
  const message = error instanceof Error ? error.message : error;
  return new CDeckError(`network failure: ${message}`);
}
export function createHttpSource(url, fetchImpl = globalThis.fetch) {
  if (typeof fetchImpl !== "function") {
    throw new CDeckError("fetch implementation is required");
  }
  let fullBody = null;
  return {
    async read(start, length) {
      const end = validateRead(start, length);
      if (fullBody !== null) {
        if (end > fullBody.byteLength) {
          throw new CDeckError(
            "full response shorter than requested range",
          );
        }
        return fullBody.slice(start, end);
      }
      let response;
      try {
        response = await fetchImpl(url, {
          method: "GET",
          headers: { Range: `bytes=${start}-${end - 1}` },
        });
      } catch (error) {
        throw networkError(error);
      }
      if (response.status !== 200 && response.status !== 206) {
        throw new CDeckError(
          `unexpected HTTP status: ${response.status}`,
        );
      }
      let body;
      try {
        body = new Uint8Array(await response.arrayBuffer());
      } catch (error) {
        throw networkError(error);
      }
      if (response.status === 206) {
        if (body.byteLength !== length) {
          throw new CDeckError(
            `partial response length mismatch: expected ${length}, actual ${body.byteLength}`,
          );
        }
        validateContentRange(
          response.headers.get("Content-Range"),
          start,
          end,
        );
        return body;
      }
      if (body.byteLength > MAX_FILE_BYTES) {
        throw new CDeckError("full response exceeds 32-bit file limit");
      }
      fullBody = body;
      if (end > fullBody.byteLength) {
        throw new CDeckError(
          "full response shorter than requested range",
        );
      }
      return fullBody.slice(start, end);
    },
  };
}
export function createLazyImageLoader(source, options = {}) {
  if (!source || typeof source.read !== "function") {
    throw new CDeckError("lazy loader requires readable source");
  }
  const Observer = (
    options.IntersectionObserver
    ?? globalThis.IntersectionObserver
  );
  const createUrl = (
    options.createObjectURL
    ?? globalThis.URL?.createObjectURL?.bind(globalThis.URL)
  );
  const revokeUrl = (
    options.revokeObjectURL
    ?? globalThis.URL?.revokeObjectURL?.bind(globalThis.URL)
  );
  if (typeof Observer !== "function") {
    throw new CDeckError("IntersectionObserver is required");
  }
  if (
    typeof createUrl !== "function"
    || typeof revokeUrl !== "function"
  ) {
    throw new CDeckError("Blob URL support is required");
  }
  const states = new Map();
  const load = async (target) => {
    const state = states.get(target);
    if (!state || state.url) return state?.url;
    if (!state.promise) {
      state.promise = source.read(
        state.record.jpegOffset,
        state.record.jpegLength,
      ).then((bytes) => {
        if (states.get(target) !== state) return null;
        const url = createUrl(
          new Blob([bytes], { type: "image/jpeg" }),
        );
        state.url = url;
        state.image.src = url;
        return url;
      }).catch((error) => {
        state.promise = null;
        throw error;
      });
    }
    return state.promise;
  };
  const observer = new Observer(async (entries) => {
    const jobs = [];
    for (const entry of entries) {
      if (
        !entry.isIntersecting
        || !states.has(entry.target)
      ) {
        continue;
      }
      observer.unobserve(entry.target);
      options.onIntersect?.(entry.target);
      jobs.push(load(entry.target));
    }
    await Promise.all(jobs);
  }, options.observerOptions);
  const release = (target) => {
    const state = states.get(target);
    observer.unobserve(target);
    if (state?.url) revokeUrl(state.url);
    states.delete(target);
  };
  return {
    observe(target, record, image = target) {
      if (states.has(target)) return;
      states.set(target, {
        record,
        image,
        promise: null,
        url: null,
      });
      observer.observe(target);
    },
    release,
    disconnect() {
      observer.disconnect();
      for (const [target] of states) {
        const state = states.get(target);
        if (state?.url) revokeUrl(state.url);
      }
      states.clear();
    },
  };
}
function readHeader(input) {
  const bytes = bytesFrom(input);
  if (bytes.byteLength < HEADER_SIZE) {
    throw new CDeckError("header shorter than 12 bytes");
  }
  const magic = decoder.decode(
    bytes.subarray(0, 8),
  );
  if (magic !== MAGIC) {
    if (magic.startsWith("CDECK")) {
      throw new CDeckError(
        `unsupported CDECK generation: ${magic}`,
      );
    }
    throw new CDeckError(`wrong magic: ${magic}`);
  }
  const view = new DataView(
    bytes.buffer,
    bytes.byteOffset,
    bytes.byteLength,
  );
  const indexLength = view.getUint32(8, true);
  if (indexLength === 0) {
    throw new CDeckError(
      "indexLength must be greater than zero",
    );
  }
  if (indexLength > MAX_INDEX_BYTES) {
    throw new CDeckError(
      "indexLength exceeds 2,000,000 bytes",
    );
  }
  return indexLength;
}
function collectionFromIndex(
  indexLength,
  rawIndex,
  actualLength = null,
) {
  const payloadStart = HEADER_SIZE + indexLength;
  const {
    records: relativeRecords,
    payloadBytes,
  } = validateIndex(
    decodeIndex(bytesFrom(rawIndex)),
  );
  const expectedFileLength = (
    payloadStart + payloadBytes
  );
  if (expectedFileLength > MAX_FILE_BYTES) {
    throw new CDeckError(
      "derived file length exceeds 32-bit limit",
    );
  }
  if (
    actualLength !== null
    && actualLength !== expectedFileLength
  ) {
    throw new CDeckError(
      `file length mismatch: expected ${expectedFileLength}, actual ${actualLength}`,
    );
  }
  const records = relativeRecords.map(
    (record) => ({
      ...record,
      jpegOffset: payloadStart + record.jpegOffset,
    }),
  );
  return {
    format: MAGIC,
    indexLength,
    payloadStart,
    recordCount: records.length,
    payloadBytes,
    expectedFileLength,
    records,
  };
}
export async function openCdeck(source) {
  if (!source || typeof source.read !== "function") {
    throw new CDeckError(
      "CDECK source must provide read(start, length)",
    );
  }
  const header = bytesFrom(
    await source.read(0, HEADER_SIZE),
  );
  if (header.byteLength !== HEADER_SIZE) {
    throw new CDeckError(
      "header shorter than 12 bytes",
    );
  }
  const indexLength = readHeader(header);
  const index = bytesFrom(
    await source.read(
      HEADER_SIZE,
      indexLength,
    ),
  );
  if (index.byteLength !== indexLength) {
    throw new CDeckError("truncated index");
  }
  return collectionFromIndex(
    indexLength,
    index,
  );
}
export function parseCdeckBuffer(input) {
  const bytes = bytesFrom(input);
  if (bytes.byteLength > MAX_FILE_BYTES) {
    throw new CDeckError(
      "file exceeds 32-bit size limit",
    );
  }
  const indexLength = readHeader(bytes);
  const payloadStart = HEADER_SIZE + indexLength;
  if (payloadStart > bytes.byteLength) {
    throw new CDeckError("truncated index");
  }
  return collectionFromIndex(
    indexLength,
    bytes.subarray(
      HEADER_SIZE,
      payloadStart,
    ),
    bytes.byteLength,
  );
}
