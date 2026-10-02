#!/usr/bin/env python3
import hashlib
import json
import struct
import sys
from pathlib import Path

MAGIC = b"CDECK002"
MAX_INDEX_BYTES = 16 * 1024 * 1024
MAX_SAFE_INTEGER = 9007199254740991
HERE = Path(__file__).resolve().parent
LEGACY = HERE.parent / "fixtures" / "valid-one-record.cdeck"

def json_bytes(value, canonical=False):
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=canonical,
    ).encode("utf-8")

def frame(raw, payload=b"", magic=MAGIC, index_length=None):
    length = len(raw) if index_length is None else index_length
    return magic + struct.pack("<I", length) + raw + payload

def record(asset_id="asset-001", length=3, meta=None, **extra):
    value = {
        "id": asset_id,
        "length": length,
        "meta": {"kind": "binary", "name": "Example"} if meta is None else meta,
    }
    value.update(extra)
    return value

def canonical(records, payload=b""):
    return frame(json_bytes(records, canonical=True), payload)

def write(root, name, data):
    path = root / name
    path.write_bytes(data)
    return path

def legacy_migration_vector():
    data = LEGACY.read_bytes()
    if data[:8] != b"CDECK001":
        raise RuntimeError("legacy migration fixture is not CDECK001")
    index_length = struct.unpack("<I", data[8:12])[0]
    old = json.loads(data[12:12 + index_length].decode("utf-8"))["decks"]
    payload = data[12 + index_length:]
    records = []
    for item in old:
        records.append({
            "id": item["id"],
            "length": item["jpegLength"],
            "meta": {
                "brand": item["brand"],
                "name": item["name"],
                "printer": item["printer"],
                "quantity": item["quantity"],
            },
        })
    return canonical(records, payload), records, payload

def build(root):
    root.mkdir(parents=True, exist_ok=True)
    payload = bytes([0, 1, 2])
    base = record()
    base_raw = json_bytes([base], canonical=True)
    vectors = []

    def add(name, data, valid, error=None, canonical_state=None, record_count=None, expect=None):
        write(root, name, data)
        item = {"file": name, "valid": valid}
        if error is not None:
            item["error"] = error
        if canonical_state is not None:
            item["canonical"] = canonical_state
        if record_count is not None:
            item["recordCount"] = record_count
        if expect is not None:
            item["expect"] = expect
        vectors.append(item)

    add(
        "valid-canonical.cdeck",
        canonical([base], payload),
        True,
        canonical_state=True,
        record_count=1,
        expect={"id": "asset-001", "length": 3},
    )

    noncanonical = json_bytes([
        {
            "meta": {"name": "Example", "kind": "binary"},
            "length": 3,
            "id": "asset-001",
        }
    ])
    add(
        "valid-noncanonical.cdeck",
        frame(noncanonical, payload),
        True,
        canonical_state=False,
        record_count=1,
    )

    add("wrong-magic.cdeck", frame(base_raw, payload, magic=b"NOTCDECK"), False, "wrong magic")
    add("unsupported-generation.cdeck", frame(base_raw, payload, magic=b"CDECK003"), False, "unsupported CDECK generation")
    add("truncated-bootstrap.cdeck", b"CDECK002" + bytes([1, 0, 0]), False, "header shorter than 12 bytes")
    add("zero-index.cdeck", MAGIC + struct.pack("<I", 0), False, "indexLength must be greater than zero")
    add(
        "oversized-index.cdeck",
        MAGIC + struct.pack("<I", MAX_INDEX_BYTES + 1),
        False,
        "indexLength exceeds 16 MiB runtime limit",
    )
    add("malformed-utf8.cdeck", frame(bytes([255])), False, "invalid UTF-8 index")
    add("malformed-json.cdeck", frame(b"{"), False, "invalid JSON index")
    add(
        "index-not-array.cdeck",
        frame(json_bytes({"id": "bad"})),
        False,
        "index must be an array",
    )
    add(
        "missing-core-member.cdeck",
        frame(json_bytes([{"id": "x", "length": 0}])),
        False,
        "missing required member: meta",
    )
    add(
        "extra-core-member.cdeck",
        frame(json_bytes([record(asset_id="x", length=0, meta={}, extra=True)])),
        False,
        "unexpected member: extra",
    )
    add(
        "duplicate-id.cdeck",
        frame(json_bytes([
            record(asset_id="same", length=0, meta={}),
            record(asset_id="same", length=0, meta={}),
        ])),
        False,
        "duplicate id",
    )
    add(
        "invalid-meta.cdeck",
        frame(json_bytes([record(asset_id="x", length=0, meta=[])])),
        False,
        "meta must be an object",
    )
    add(
        "float-metadata.cdeck",
        frame(json_bytes([record(asset_id="x", length=0, meta={"value": 1.5})])),
        False,
        "JSON floats are not allowed",
    )
    add(
        "unsafe-integer.cdeck",
        frame(json_bytes([record(asset_id="x", length=MAX_SAFE_INTEGER + 1, meta={})])),
        False,
        "length",
    )
    add(
        "zero-length.cdeck",
        canonical([record(asset_id="empty", length=0, meta={"kind": "empty"})]),
        True,
        canonical_state=True,
        record_count=1,
        expect={"id": "empty", "length": 0},
    )
    add(
        "cumulative-overflow.cdeck",
        frame(json_bytes([
            record(asset_id="a", length=MAX_SAFE_INTEGER, meta={}),
            record(asset_id="b", length=1, meta={}),
        ])),
        False,
        "cumulative payload length exceeds safe integer limit",
    )
    add(
        "truncated-payload.cdeck",
        canonical([record(asset_id="short", length=3, meta={})], b"ab"),
        False,
        "file length mismatch",
    )
    add(
        "trailing-bytes.cdeck",
        canonical([record(asset_id="trail", length=1, meta={})], b"ab"),
        False,
        "file length mismatch",
    )

    migrated, migrated_records, migrated_payload = legacy_migration_vector()
    migrated_record = migrated_records[0]
    add(
        "migration-one-record.cdeck",
        migrated,
        True,
        canonical_state=True,
        record_count=1,
        expect={
            "id": migrated_record["id"],
            "length": migrated_record["length"],
            "meta": migrated_record["meta"],
            "payloadSha256": hashlib.sha256(migrated_payload).hexdigest(),
        },
    )

    manifest = root / "vectors.json"
    manifest.write_text(
        json.dumps(vectors, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

def main():
    output = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else HERE
    build(output)

if __name__ == "__main__":
    main()
