const MAGIC = "CDECK002";
const HEADER_SIZE = 12;
const MAX_INDEX_BYTES = 16 * 1024 * 1024;
const MAX_RECORDS = 4_000;
const DEFAULT_MAX_FULL_BYTES = 64 * 1024 * 1024;
const FIELDS = ["id", "length", "meta"];
const OFFSET = Symbol("cdeck.offset");
const decoder = new TextDecoder("utf-8", { fatal: true });
export class CDeckError extends Error {}
function bytesFrom(input) {
  if (input instanceof ArrayBuffer) return new Uint8Array(input);
  if (ArrayBuffer.isView(input)) {
    return new Uint8Array(input.buffer, input.byteOffset, input.byteLength);
  }
  throw new CDeckError("input must be an ArrayBuffer or typed array");
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
function validateJson(value) {
  if (value === null || typeof value === "boolean") return;
  if (typeof value === "number") {
    if (!Number.isInteger(value)) throw new CDeckError("JSON floats are not allowed");
    if (!Number.isSafeInteger(value)) throw new CDeckError("JSON integer exceeds safe integer limit");
    return;
  }
  if (typeof value === "string") {
    for (const char of value) {
      const code = char.codePointAt(0);
      if (code >= 0xd800 && code <= 0xdfff) throw new CDeckError("JSON string contains lone surrogate");
    }
    return;
  }
  if (Array.isArray(value)) { value.forEach(validateJson); return; }
  if (typeof value === "object") {
    for (const [key, item] of Object.entries(value)) { validateJson(key); validateJson(item); }
    return;
  }
  throw new CDeckError("unsupported JSON value type");
}
function validateIndex(index) {
  if (!Array.isArray(index)) {
    throw new CDeckError("index must be an array");
  }
  if (index.length > MAX_RECORDS) {
    throw new CDeckError("record count exceeds limit");
  }
  const ids = new Set();
  const records = [];
  let payloadBytes = 0;
  for (let i = 0; i < index.length; i += 1) {
    const number = i + 1;
    const record = index[i];
    if (record === null || typeof record !== "object" || Array.isArray(record)) {
      throw new CDeckError(`record ${number}: must be an object`);
    }
    for (const field of FIELDS) {
      if (!Object.hasOwn(record, field)) {
        throw new CDeckError(`record ${number}: missing required member: ${field}`);
      }
    }
    const extra = Object.keys(record).find((field) => !FIELDS.includes(field));
    if (extra !== undefined) {
      throw new CDeckError(`record ${number}: unexpected member: ${extra}`);
    }
    const { id, length, meta } = record;
    if (typeof id !== "string") {
      throw new CDeckError(`record ${number}: id must be a string`);
    }
    if (!id) {
      throw new CDeckError(`record ${number}: id must not be empty`);
    }
    if (!Number.isInteger(length)) {
      throw new CDeckError(`record ${number}: length must be an integer`);
    }
    if (!Number.isSafeInteger(length) || length < 0) {
      throw new CDeckError(`record ${number}: length out of range`);
    }
    if (meta === null || typeof meta !== "object" || Array.isArray(meta)) {
      throw new CDeckError(`record ${number}: meta must be an object`);
    }
    validateJson(id);
    validateJson(meta);
    if (ids.has(id)) {
      throw new CDeckError(`record ${number}: duplicate id: ${id}`);
    }
    ids.add(id);
    records.push({ id, length, meta, [OFFSET]: payloadBytes });
    payloadBytes += length;
    if (!Number.isSafeInteger(payloadBytes)) {
      throw new CDeckError("cumulative payload length exceeds safe integer limit");
    }
  }
  return { records, payloadBytes };
}
function validateRead(start, length) {
  if (!Number.isSafeInteger(start) || start < 0) {
    throw new CDeckError("read start must be a non-negative safe integer");
  }
  if (!Number.isSafeInteger(length) || length <= 0) {
    throw new CDeckError("read length must be a positive safe integer");
  }
  const end = start + length;
  if (!Number.isSafeInteger(end)) {
    throw new CDeckError("read range exceeds safe integer limit");
  }
  return end;
}
function validateContentRange(value, start, end) {
  if (value === null) return null;
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
  if (match[3] === "*") return null;
  const total = Number(match[3]);
  if (!Number.isSafeInteger(total) || end > total) {
    throw new CDeckError("invalid Content-Range total");
  }
  return total;
}
function networkError(error) {
  if (error?.name === "AbortError") return error;
  const message = error instanceof Error ? error.message : error;
  return new CDeckError(`network failure: ${message}`);
}
export function createHttpSource(url, {
  maxFullBytes = DEFAULT_MAX_FULL_BYTES,
  fetch: fetchImpl = globalThis.fetch,
} = {}) {
  if (!Number.isSafeInteger(maxFullBytes) || maxFullBytes < 0) {
    throw new CDeckError("maxFullBytes must be a non-negative safe integer");
  }
  if (typeof fetchImpl !== "function") {
    throw new CDeckError("fetch implementation is required");
  }
  let fullBody = null;
  let etag = null;
  let size = null;
  return {
    get size() {
      return size;
    },
    async read(start, length, signal) {
      const end = validateRead(start, length);
      if (fullBody === null) {
        let response;
        try {
          response = await fetchImpl(url, {
            method: "GET",
            headers: { Range: `bytes=${start}-${end - 1}` },
            signal,
          });
        } catch (error) {
          throw networkError(error);
        }
        if (response.status !== 200 && response.status !== 206) {
          throw new CDeckError(
            `unexpected HTTP status: ${response.status}`,
          );
        }
        const responseEtag = response.headers.get("ETag");
        const strongEtag = (
          responseEtag !== null && !responseEtag.startsWith("W/")
            ? responseEtag
            : null
        );
        if (etag !== null && strongEtag !== etag) {
          throw new CDeckError("ETag changed");
        }
        if (etag === null && strongEtag !== null) etag = strongEtag;
        if (response.status === 206) {
          const total = validateContentRange(
            response.headers.get("Content-Range"),
            start,
            end,
          );
          if (total !== null) {
            if (size !== null && total !== size) {
              throw new CDeckError("file size changed");
            }
            size = total;
          }
        }
        if (response.status === 200) {
          const value = response.headers.get("Content-Length");
          if (value !== null) {
            const declared = Number(value);
            if (!Number.isSafeInteger(declared) || declared < 0) {
              throw new CDeckError("invalid Content-Length");
            }
            if (declared > maxFullBytes) {
              throw new CDeckError("full response exceeds maxFullBytes");
            }
          }
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
          return body;
        }
        if (body.byteLength > maxFullBytes) {
          throw new CDeckError("full response exceeds maxFullBytes");
        }
        if (size !== null && body.byteLength !== size) {
          throw new CDeckError("file size changed");
        }
        size = body.byteLength;
        fullBody = body;
      }
      if (end > fullBody.byteLength) {
        throw new CDeckError(
          "full response shorter than requested range",
        );
      }
      return fullBody.slice(start, end);
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
      "indexLength exceeds 16 MiB runtime limit",
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
  const { records, payloadBytes } = validateIndex(
    decodeIndex(bytesFrom(rawIndex)),
  );
  const expectedFileLength = payloadStart + payloadBytes;
  if (!Number.isSafeInteger(expectedFileLength)) {
    throw new CDeckError("derived file length exceeds safe integer limit");
  }
  if (actualLength !== null && actualLength !== expectedFileLength) {
    throw new CDeckError(
      `file length mismatch: expected ${expectedFileLength}, actual ${actualLength}`,
    );
  }
  for (const record of records) {
    record[OFFSET] += payloadStart;
  }
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
function attachReader(deck, read) {
  deck.read = async (record, signal) => {
    if (!deck.records.includes(record)) {
      throw new CDeckError("record does not belong to deck");
    }
    if (record.length === 0) return new Uint8Array();
    const bytes = bytesFrom(await read(record[OFFSET], record.length, signal));
    if (bytes.byteLength !== record.length) {
      throw new CDeckError(`payload read length mismatch: expected ${record.length}, actual ${bytes.byteLength}`);
    }
    return bytes;
  };
  return deck;
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
  const deck = collectionFromIndex(
    indexLength,
    index,
    source.size ?? null,
  );
  return attachReader(
    deck,
    (start, length, signal) => source.read(start, length, signal),
  );
}
export function parseCdeckBuffer(input) {
  const bytes = bytesFrom(input);
  const indexLength = readHeader(bytes);
  const payloadStart = HEADER_SIZE + indexLength;
  if (payloadStart > bytes.byteLength) {
    throw new CDeckError("truncated index");
  }
  const deck = collectionFromIndex(
    indexLength,
    bytes.subarray(
      HEADER_SIZE,
      payloadStart,
    ),
    bytes.byteLength,
  );
  return attachReader(
    deck,
    (start, length) => bytes.slice(start, start + length),
  );
}
