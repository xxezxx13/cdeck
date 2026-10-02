#!/usr/bin/env python3
import argparse
import json
import os
import struct
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import cdeck

LEGACY_MAGIC = b"CDECK001"
HEADER_SIZE = 12
MAX_INDEX_BYTES = 2_000_000
MAX_RECORDS = 4_000
MAX_FILE_BYTES = 4_294_967_295
COPY_CHUNK_BYTES = 1024 * 1024
REQUIRED_FIELDS = ("id", "name", "quantity", "brand", "printer", "jpegLength")
STRING_LIMITS = {"id": 500, "name": 1000, "brand": 500, "printer": 500}

class MigrationError(ValueError):
    pass

def _object_no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise MigrationError(f"duplicate JSON member: {key}")
        result[key] = value
    return result

def _reject_json_constant(value):
    raise MigrationError(f"invalid JSON constant: {value}")

def _decode_index(raw):
    if raw.startswith(b"\xef\xbb\xbf"):
        raise MigrationError("index must not contain a UTF-8 BOM")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise MigrationError("invalid UTF-8 index") from exc
    try:
        return json.loads(
            text,
            object_pairs_hook=_object_no_duplicates,
            parse_constant=_reject_json_constant,
        )
    except MigrationError:
        raise
    except json.JSONDecodeError as exc:
        raise MigrationError("invalid JSON index") from exc

def _require_string(record, field, number):
    value = record[field]
    if not isinstance(value, str):
        raise MigrationError(f"record {number}: {field} must be a string")
    if field == "id" and not value:
        raise MigrationError(f"record {number}: id must not be empty")
    if len(value.encode("utf-8")) > STRING_LIMITS[field]:
        raise MigrationError(f"record {number}: {field} exceeds UTF-8 byte limit")
    return value

def _require_integer(record, field, low, high, number):
    value = record[field]
    if isinstance(value, bool) or not isinstance(value, int):
        raise MigrationError(f"record {number}: {field} must be an integer")
    if not low <= value <= high:
        raise MigrationError(f"record {number}: {field} out of range")
    return value

def _read_legacy(path):
    path = Path(path).resolve()
    try:
        file_size = path.stat().st_size
    except OSError as exc:
        raise MigrationError(f"cannot read input: {path}") from exc
    if file_size < HEADER_SIZE:
        raise MigrationError("header shorter than 12 bytes")
    if file_size > MAX_FILE_BYTES:
        raise MigrationError("file exceeds 32-bit size limit")
    with path.open("rb") as handle:
        header = handle.read(HEADER_SIZE)
        magic = header[:8]
        if magic != LEGACY_MAGIC:
            raise MigrationError(f"expected CDECK001, found {magic!r}")
        index_length = struct.unpack("<I", header[8:12])[0]
        if index_length == 0:
            raise MigrationError("indexLength must be greater than zero")
        if index_length > MAX_INDEX_BYTES:
            raise MigrationError("indexLength exceeds 2,000,000 bytes")
        if HEADER_SIZE + index_length > file_size:
            raise MigrationError("truncated index")
        raw_index = handle.read(index_length)
    index = _decode_index(raw_index)
    if not isinstance(index, dict):
        raise MigrationError("top-level JSON value must be an object")
    if "decks" not in index:
        raise MigrationError("missing required top-level member: decks")
    decks = index["decks"]
    if not isinstance(decks, list):
        raise MigrationError("decks must be an array")
    if len(decks) > MAX_RECORDS:
        raise MigrationError("record count exceeds limit")
    warnings = [f"ignored legacy top-level member: {key}" for key in index if key != "decks"]
    ids = set()
    records = []
    payload_bytes = 0
    for number, record in enumerate(decks, start=1):
        if not isinstance(record, dict):
            raise MigrationError(f"record {number}: must be an object")
        missing = [field for field in REQUIRED_FIELDS if field not in record]
        if missing:
            raise MigrationError(f"record {number}: missing required member: {missing[0]}")
        deck_id = _require_string(record, "id", number)
        name = _require_string(record, "name", number)
        brand = _require_string(record, "brand", number)
        printer = _require_string(record, "printer", number)
        quantity = _require_integer(record, "quantity", 1, 10_000, number)
        length = _require_integer(record, "jpegLength", 1, MAX_FILE_BYTES, number)
        if deck_id in ids:
            raise MigrationError(f"record {number}: duplicate id: {deck_id}")
        ids.add(deck_id)
        meta = {"name": name, "quantity": quantity, "brand": brand, "printer": printer}
        for key, value in record.items():
            if key not in REQUIRED_FIELDS:
                meta[key] = value
        records.append({"id": deck_id, "length": length, "meta": meta})
        payload_bytes += length
        if payload_bytes > MAX_FILE_BYTES:
            raise MigrationError("cumulative JPEG payload exceeds 32-bit limit")
    payload_start = HEADER_SIZE + index_length
    expected_length = payload_start + payload_bytes
    if file_size != expected_length:
        raise MigrationError(f"file length mismatch: expected {expected_length}, actual {file_size}")
    return path, records, payload_start, payload_bytes, warnings

def migrate(source_path, output_path):
    source_path, records, payload_start, payload_bytes, warnings = _read_legacy(source_path)
    output_path = Path(output_path).resolve()
    if output_path == source_path:
        raise MigrationError("output path must differ from input path")
    if not output_path.parent.is_dir():
        raise MigrationError("output directory does not exist")
    raw_index = json.dumps(
        records,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as output:
            temporary_path = Path(output.name)
            output.write(b"CDECK002")
            output.write(struct.pack("<I", len(raw_index)))
            output.write(raw_index)
            copied = 0
            with source_path.open("rb") as source:
                source.seek(payload_start)
                remaining = payload_bytes
                while remaining:
                    chunk = source.read(min(COPY_CHUNK_BYTES, remaining))
                    if not chunk:
                        raise MigrationError("source payload changed during migration")
                    output.write(chunk)
                    copied += len(chunk)
                    remaining -= len(chunk)
            if copied != payload_bytes:
                raise MigrationError("payload copy length mismatch")
            output.flush()
            os.fsync(output.fileno())
        result = cdeck.verify_file(temporary_path)
        cdeck.verify_canonical(temporary_path, result)
        os.replace(temporary_path, output_path)
        temporary_path = None
        return result, warnings
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Convert one CDECK001 archive to canonical CDECK002."
    )
    parser.add_argument("source")
    parser.add_argument("output")
    args = parser.parse_args(argv)
    try:
        result, warnings = migrate(args.source, args.output)
    except (MigrationError, cdeck.CDeckError, OSError) as exc:
        print(f"cdeck001_to_002: {exc}", file=sys.stderr)
        return 1
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    print(f"migrated: {Path(args.output).resolve()}")
    print("records:", result["recordCount"])
    print("payload bytes:", result["payloadBytes"])
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
