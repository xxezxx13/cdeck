#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import struct
import sys
import tempfile
from pathlib import Path
MAGIC = b'CDECK002'
HEADER_SIZE = 12
MAX_INDEX_BYTES = 16 * 1024 * 1024
MAX_RECORDS = 4000
MAX_SAFE_INTEGER = 9007199254740991
REQUIRED_FIELDS = ("id", "length", "meta")
COPY_CHUNK_BYTES = 1024 * 1024
class CDeckError(ValueError):
    pass
def _object_no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CDeckError(f'duplicate JSON member: {key}')
        result[key] = value
    return result
def _require_id(record, record_number):
    value = record["id"]
    if not isinstance(value, str):
        raise CDeckError(f"record {record_number}: id must be a string")
    if not value:
        raise CDeckError(f"record {record_number}: id must not be empty")
    return value
def _require_length(record, record_number):
    value = record["length"]
    if isinstance(value, bool) or not isinstance(value, int):
        raise CDeckError(f"record {record_number}: length must be an integer")
    if not 0 <= value <= MAX_SAFE_INTEGER:
        raise CDeckError(f"record {record_number}: length out of range")
    return value
def _reject_json_constant(value):
    raise CDeckError(f'invalid JSON constant: {value}')
def _decode_index(raw):
    if raw.startswith(b'\xef\xbb\xbf'):
        raise CDeckError('index must not contain a UTF-8 BOM')
    try:
        text = raw.decode('utf-8', errors='strict')
    except UnicodeDecodeError as exc:
        raise CDeckError('invalid UTF-8 index') from exc
    try:
        return json.loads(text, object_pairs_hook=_object_no_duplicates, parse_constant=_reject_json_constant)
    except CDeckError:
        raise
    except json.JSONDecodeError as exc:
        raise CDeckError('invalid JSON index') from exc
def _validate_json(value):
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, int):
        if abs(value) > MAX_SAFE_INTEGER:
            raise CDeckError("JSON integer exceeds safe integer limit")
        return
    if isinstance(value, float):
        raise CDeckError("JSON floats are not allowed")
    if isinstance(value, str):
        if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
            raise CDeckError("JSON string contains lone surrogate")
        return
    if isinstance(value, list):
        for item in value:
            _validate_json(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise CDeckError("JSON object key must be a string")
            _validate_json(key)
            _validate_json(item)
        return
    raise CDeckError("unsupported JSON value type")
def _validate_index(index):
    if not isinstance(index, list):
        raise CDeckError("index must be an array")
    if len(index) > MAX_RECORDS:
        raise CDeckError("record count exceeds limit")
    ids = set()
    offsets = []
    payload_bytes = 0
    for number, record in enumerate(index, start=1):
        if not isinstance(record, dict):
            raise CDeckError(f"record {number}: must be an object")
        missing = [
            field for field in REQUIRED_FIELDS
            if field not in record
        ]
        if missing:
            raise CDeckError(
                f"record {number}: missing required member: {missing[0]}"
            )
        extra = [
            field for field in record
            if field not in REQUIRED_FIELDS
        ]
        if extra:
            raise CDeckError(
                f"record {number}: unexpected member: {extra[0]}"
            )
        record_id = _require_id(record, number)
        length = _require_length(record, number)
        meta = record["meta"]
        if not isinstance(meta, dict):
            raise CDeckError(f"record {number}: meta must be an object")
        _validate_json(record_id)
        _validate_json(meta)
        if record_id in ids:
            raise CDeckError(
                f"record {number}: duplicate id: {record_id}"
            )
        ids.add(record_id)
        offsets.append(payload_bytes)
        payload_bytes += length
        if payload_bytes > MAX_SAFE_INTEGER:
            raise CDeckError(
                "cumulative payload length exceeds safe integer limit"
            )
    return (index, offsets, payload_bytes)
def verify_file(path):
    path = Path(path)
    file_size = path.stat().st_size
    if file_size < HEADER_SIZE:
        raise CDeckError("header shorter than 12 bytes")
    if file_size > MAX_SAFE_INTEGER:
        raise CDeckError("file exceeds safe integer limit")
    with path.open("rb") as handle:
        header = handle.read(HEADER_SIZE)
        magic = header[:8]
        if magic != MAGIC:
            if magic.startswith(b"CDECK"):
                raise CDeckError(
                    f"unsupported CDECK generation: {magic!r}"
                )
            raise CDeckError(f"wrong magic: {magic!r}")
        index_length = struct.unpack("<I", header[8:12])[0]
        if index_length == 0:
            raise CDeckError(
                "indexLength must be greater than zero"
            )
        if index_length > MAX_INDEX_BYTES:
            raise CDeckError(
                "indexLength exceeds 16 MiB runtime limit"
            )
        if HEADER_SIZE + index_length > file_size:
            raise CDeckError("truncated index")
        raw_index = handle.read(index_length)
        if len(raw_index) != index_length:
            raise CDeckError("truncated index")
    index = _decode_index(raw_index)
    records, offsets, payload_bytes = _validate_index(index)
    payload_start = HEADER_SIZE + index_length
    expected_file_length = payload_start + payload_bytes
    if expected_file_length > MAX_SAFE_INTEGER:
        raise CDeckError(
            "derived file length exceeds safe integer limit"
        )
    if file_size != expected_file_length:
        raise CDeckError(
            f"file length mismatch: expected {expected_file_length}, actual {file_size}"
        )
    return {
        "format": "CDECK002",
        "indexLength": index_length,
        "payloadStart": payload_start,
        "recordCount": len(records),
        "payloadBytes": payload_bytes,
        "expectedFileLength": expected_file_length,
        "offsets": offsets,
        "warnings": [],
    }
def _read_verified_records(path, result, context, raw=False):
    with path.open("rb") as handle:
        handle.seek(HEADER_SIZE)
        raw_index = handle.read(result["indexLength"])
    if len(raw_index) != result["indexLength"]:
        raise CDeckError(f"archive changed during {context}")
    records = _decode_index(raw_index)
    return (raw_index, records) if raw else records
def verify_canonical(path, result=None):
    path = Path(path)
    result = result or verify_file(path)
    raw, records = _read_verified_records(
        path,
        result,
        "canonical verification",
        True,
    )
    if raw != _encode_canonical_index(records):
        raise CDeckError("index is not canonical")
    return result
def inspect_file(path):
    path = Path(path)
    result = verify_file(path)
    wire_records = _read_verified_records(
        path,
        result,
        "inspection",
    )
    records = []
    for wire_record, offset in zip(
        wire_records,
        result["offsets"],
        strict=True,
    ):
        record = dict(wire_record)
        record["offset"] = result["payloadStart"] + offset
        records.append(record)
    inspected = dict(result)
    inspected["records"] = records
    return inspected
def _sha256(path, start=0, length=None):
    digest = hashlib.sha256()
    remaining = length
    with Path(path).open('rb') as handle:
        handle.seek(start)
        while remaining is None or remaining:
            size = COPY_CHUNK_BYTES if remaining is None else min(COPY_CHUNK_BYTES, remaining)
            chunk = handle.read(size)
            if not chunk:
                break
            digest.update(chunk)
            if remaining is not None:
                remaining -= len(chunk)
    if remaining not in (None, 0):
        raise CDeckError('short embedded payload during SHA-256 verification')
    return digest.digest()
def verify_source(path, manifest_path):
    path = Path(path)
    result = verify_file(path)
    source_records, source_paths = _read_manifest(manifest_path)
    archive_records = _read_verified_records(path, result, "source verification")
    if len(source_records) != len(archive_records):
        raise CDeckError(f"source record count mismatch: {len(source_records)} != {len(archive_records)}")
    rows = zip(source_records, source_paths, archive_records, result["offsets"], strict=True)
    for number, (source_record, source_path, archive_record, offset) in enumerate(rows, start=1):
        if source_record != archive_record:
            raise CDeckError(f"record {number}: metadata mismatch")
        if _sha256(source_path) != _sha256(path, result["payloadStart"] + offset, archive_record["length"]):
            raise CDeckError(f"record {number}: payload SHA-256 mismatch")
    verified = dict(result)
    verified["sourceVerified"] = len(source_records)
    return verified
def _read_manifest(manifest_path):
    manifest_path = Path(manifest_path).resolve()
    try:
        manifest = _decode_index(manifest_path.read_bytes())
    except OSError as exc:
        raise CDeckError(f"cannot read manifest: {manifest_path}") from exc
    if not isinstance(manifest, list):
        raise CDeckError("manifest must be an array")
    if len(manifest) > MAX_RECORDS:
        raise CDeckError("record count exceeds limit")
    root = manifest_path.parent.resolve(strict=True)
    fields = ("id", "path", "meta")
    records = []
    source_paths = []
    for number, item in enumerate(manifest, start=1):
        if not isinstance(item, dict):
            raise CDeckError(f"record {number}: manifest entry must be an object")
        missing = [field for field in fields if field not in item]
        extra = [field for field in item if field not in fields]
        if missing:
            raise CDeckError(f"record {number}: missing manifest member: {missing[0]}")
        if extra:
            raise CDeckError(f"record {number}: unexpected manifest member: {extra[0]}")
        record_id = _require_id(item, number)
        meta = item["meta"]
        if not isinstance(meta, dict):
            raise CDeckError(f"record {number}: meta must be an object")
        _validate_json(record_id)
        _validate_json(meta)
        value = item["path"]
        if not isinstance(value, str) or not value:
            raise CDeckError(f"record {number}: path must be a non-empty string")
        _validate_json(value)
        source_ref = Path(value)
        if source_ref.is_absolute() or ".." in source_ref.parts:
            raise CDeckError(f"record {number}: unsafe payload path")
        try:
            source_path = (root / source_ref).resolve(strict=True)
            source_path.relative_to(root)
        except ValueError:
            raise CDeckError(f"record {number}: unsafe payload path")
        except OSError as exc:
            raise CDeckError(f"record {number}: missing payload: {value}") from exc
        if not source_path.is_file():
            raise CDeckError(f"record {number}: missing payload: {value}")
        records.append({"id": record_id, "length": source_path.stat().st_size, "meta": meta})
        source_paths.append(source_path)
    _validate_index(records)
    return records, source_paths
def _encode_canonical_index(records):
    _validate_index(records)
    raw = json.dumps(
        records,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if not raw:
        raise CDeckError("encoded index is empty")
    if len(raw) > MAX_INDEX_BYTES:
        raise CDeckError("encoded index exceeds 16 MiB runtime limit")
    return raw
def build_collection(manifest_path, output_path):
    manifest_path = Path(manifest_path).resolve()
    output_path = Path(output_path).resolve()
    records, source_paths = _read_manifest(manifest_path)
    raw_index = _encode_canonical_index(records)
    expected_file_length = HEADER_SIZE + len(raw_index) + sum(record["length"] for record in records)
    if expected_file_length > MAX_SAFE_INTEGER:
        raise CDeckError("derived file length exceeds safe integer limit")
    if output_path == manifest_path:
        raise CDeckError("output path must not replace manifest")
    if output_path in source_paths:
        raise CDeckError("output path must not replace source payload")
    if not output_path.parent.is_dir():
        raise CDeckError("output directory does not exist")
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=output_path.parent, prefix=f".{output_path.name}.", suffix=".tmp", delete=False) as output:
            temporary_path = Path(output.name)
            output.write(MAGIC)
            output.write(struct.pack("<I", len(raw_index)))
            output.write(raw_index)
            for record, source_path in zip(records, source_paths, strict=True):
                copied = 0
                with source_path.open("rb") as source:
                    while chunk := source.read(COPY_CHUNK_BYTES):
                        output.write(chunk)
                        copied += len(chunk)
                if copied != record["length"]:
                    raise CDeckError(f"source payload changed during build: {source_path}")
            output.flush()
            os.fsync(output.fileno())
        result = verify_source(temporary_path, manifest_path)
        if result["expectedFileLength"] != expected_file_length:
            raise CDeckError("post-build length verification failed")
        os.replace(temporary_path, output_path)
        temporary_path = None
        return result
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
def _print_summary(result):
    print('format:', result['format'])
    print('index bytes:', result['indexLength'])
    print('records:', result['recordCount'])
    print('payload bytes:', result['payloadBytes'])
    print('file bytes:', result['expectedFileLength'])
    if 'sourceVerified' in result:
        print('source verified:', result['sourceVerified'])
    for warning in result['warnings']:
        print(f'warning: {warning}', file=sys.stderr)
def _verify_command(
    path, source=None, quiet=False, canonical=False
):
    result = (
        verify_source(path, source)
        if source else verify_file(path)
    )
    if canonical:
        verify_canonical(path, result)
    if quiet:
        for warning in result["warnings"]:
            print(f"warning: {warning}", file=sys.stderr)
    else:
        _print_summary(result)
def _inspect_command(
    path,
    limit,
    json_output=False,
    record_id=None,
):
    result = inspect_file(path)
    records = result["records"]
    if record_id is not None:
        records = [
            record
            for record in records
            if record["id"] == record_id
        ]
        if not records:
            raise CDeckError(
                f"record id not found: {record_id}"
            )
        result = dict(result)
        result["records"] = records
    if json_output:
        print(json.dumps(
            result,
            ensure_ascii=False,
            separators=(",", ":"),
        ))
        return
    _print_summary(result)
    print("payload starts:", result["payloadStart"])
    shown = (
        records
        if limit is None
        else records[:limit]
    )
    for number, record in enumerate(
        shown,
        start=1,
    ):
        print(
            "{}: id={!r} offset={} length={} meta={!r}".format(
                number,
                record["id"],
                record["offset"],
                record["length"],
                record["meta"],
            )
        )
    if len(shown) != len(records):
        print(
            f"... {len(records) - len(shown)} record(s) not shown"
        )
def _build_command(manifest_path, output_path):
    result = build_collection(manifest_path, output_path)
    print(f'built: {Path(output_path).resolve()}')
    _print_summary(result)
def main(argv=None):
    parser = argparse.ArgumentParser(prog="cdeck")
    subparsers = parser.add_subparsers(
        dest="command", required=True
    )
    build_parser = subparsers.add_parser("build")
    build_parser.add_argument("manifest")
    build_parser.add_argument("output")
    inspect_parser = subparsers.add_parser("inspect")
    inspect_parser.add_argument("path")
    inspect_parser.add_argument("--limit", type=int, default=20)
    inspect_parser.add_argument("--json", action="store_true")
    inspect_parser.add_argument("--id")
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("path")
    verify_parser.add_argument("--source")
    verify_parser.add_argument("--quiet", action="store_true")
    verify_parser.add_argument("--canonical", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            _build_command(args.manifest, args.output)
        elif args.command == "inspect":
            if args.limit is not None and args.limit < 0:
                raise CDeckError(
                    "inspect limit must not be negative"
                )
            _inspect_command(
                args.path,
                args.limit,
                args.json,
                args.id,
            )
        else:
            _verify_command(
                args.path,
                args.source,
                args.quiet,
                args.canonical,
            )
    except (CDeckError, OSError) as exc:
        print(f"cdeck: {exc}", file=sys.stderr)
        return 1
    return 0
if __name__ == '__main__':
    raise SystemExit(main())
