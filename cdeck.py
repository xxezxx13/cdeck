#!/usr/bin/env python3
import argparse
import csv
import hashlib
import json
import os
import struct
import sys
import tempfile
from pathlib import Path
MAGIC = b'CDECK001'
HEADER_SIZE = 12
MAX_INDEX_BYTES = 2000000
MAX_RECORDS = 4000
MAX_FILE_BYTES = 4294967295
REQUIRED_FIELDS = ('id', 'name', 'quantity', 'brand', 'printer', 'jpegLength')
STRING_LIMITS = {'id': 500, 'name': 1000, 'brand': 500, 'printer': 500}
SOURCE_FIELDS = ('src', 'name', 'quantity', 'brand', 'printer')
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

def _utf8_size(value):
    return len(value.encode('utf-8'))

def _require_string(record, field, record_number):
    value = record[field]
    if not isinstance(value, str):
        raise CDeckError(f'record {record_number}: {field} must be a string')
    if field == 'id' and (not value):
        raise CDeckError(f'record {record_number}: id must not be empty')
    if _utf8_size(value) > STRING_LIMITS[field]:
        raise CDeckError(f'record {record_number}: {field} exceeds UTF-8 byte limit')
    return value

def _require_integer(record, field, low, high, record_number):
    value = record[field]
    if isinstance(value, bool) or not isinstance(value, int):
        raise CDeckError(f'record {record_number}: {field} must be an integer')
    if not low <= value <= high:
        raise CDeckError(f'record {record_number}: {field} out of range')
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

def _validate_index(index):
    if not isinstance(index, dict):
        raise CDeckError('top-level JSON value must be an object')
    if 'decks' not in index:
        raise CDeckError('missing required top-level member: decks')
    decks = index['decks']
    if not isinstance(decks, list):
        raise CDeckError('decks must be an array')
    if len(decks) > MAX_RECORDS:
        raise CDeckError('record count exceeds limit')
    warnings = [f'unknown top-level member: {key}' for key in index if key != 'decks']
    ids = set()
    payload_bytes = 0
    offsets = []
    for number, record in enumerate(decks, start=1):
        if not isinstance(record, dict):
            raise CDeckError(f'record {number}: must be an object')
        missing = [field for field in REQUIRED_FIELDS if field not in record]
        if missing:
            raise CDeckError(f'record {number}: missing required member: {missing[0]}')
        warnings.extend((f'record {number}: unknown member: {key}' for key in record if key not in REQUIRED_FIELDS))
        deck_id = _require_string(record, 'id', number)
        _require_string(record, 'name', number)
        _require_string(record, 'brand', number)
        _require_string(record, 'printer', number)
        _require_integer(record, 'quantity', 1, 10000, number)
        jpeg_length = _require_integer(record, 'jpegLength', 1, MAX_FILE_BYTES, number)
        if deck_id in ids:
            raise CDeckError(f'record {number}: duplicate id: {deck_id}')
        ids.add(deck_id)
        offsets.append(payload_bytes)
        payload_bytes += jpeg_length
        if payload_bytes > MAX_FILE_BYTES:
            raise CDeckError('cumulative JPEG payload exceeds 32-bit limit')
    return (decks, offsets, payload_bytes, warnings)

def verify_file(path):
    path = Path(path)
    file_size = path.stat().st_size
    if file_size < HEADER_SIZE:
        raise CDeckError('header shorter than 12 bytes')
    if file_size > MAX_FILE_BYTES:
        raise CDeckError('file exceeds 32-bit size limit')
    with path.open('rb') as handle:
        header = handle.read(HEADER_SIZE)
        magic = header[:8]
        if magic != MAGIC:
            if magic.startswith(b'CDECK'):
                raise CDeckError(f'unsupported CDECK generation: {magic!r}')
            raise CDeckError(f'wrong magic: {magic!r}')
        index_length = struct.unpack('<I', header[8:12])[0]
        if index_length == 0:
            raise CDeckError('indexLength must be greater than zero')
        if index_length > MAX_INDEX_BYTES:
            raise CDeckError('indexLength exceeds 2,000,000 bytes')
        if HEADER_SIZE + index_length > file_size:
            raise CDeckError('truncated index')
        raw_index = handle.read(index_length)
        if len(raw_index) != index_length:
            raise CDeckError('truncated index')
    index = _decode_index(raw_index)
    decks, offsets, payload_bytes, warnings = _validate_index(index)
    payload_start = HEADER_SIZE + index_length
    expected_file_length = payload_start + payload_bytes
    if expected_file_length > MAX_FILE_BYTES:
        raise CDeckError('derived file length exceeds 32-bit limit')
    if file_size != expected_file_length:
        raise CDeckError(f'file length mismatch: expected {expected_file_length}, actual {file_size}')
    return {'format': 'CDECK001', 'indexLength': index_length, 'payloadStart': payload_start, 'recordCount': len(decks), 'payloadBytes': payload_bytes, 'expectedFileLength': expected_file_length, 'offsets': offsets, 'warnings': warnings}

def _read_verified_decks(path, result, context, raw=False):
    with path.open("rb") as handle:
        handle.seek(HEADER_SIZE)
        raw_index = handle.read(result["indexLength"])
    if len(raw_index) != result["indexLength"]:
        raise CDeckError(f"archive changed during {context}")
    decks = _decode_index(raw_index)["decks"]
    return (raw_index, decks) if raw else decks

def verify_canonical(path, result=None):
    path = Path(path)
    result = result or verify_file(path)
    raw, decks = _read_verified_decks(
        path, result, "canonical verification", True
    )
    records = [
        {field: deck[field] for field in REQUIRED_FIELDS}
        for deck in decks
    ]
    if raw != _encode_canonical_index(records):
        raise CDeckError("index is not canonical")
    return result

def inspect_file(path):
    path = Path(path)
    result = verify_file(path)
    decks = _read_verified_decks(path, result, 'inspection')
    records = []
    for deck, offset in zip(decks, result['offsets'], strict=True):
        record = dict(deck)
        record['jpegOffset'] = result['payloadStart'] + offset
        records.append(record)
    inspected = dict(result)
    inspected['records'] = records
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
        raise CDeckError('short embedded JPEG during SHA-256 verification')
    return digest.digest()

def verify_source(path, csv_path):
    path = Path(path)
    csv_path = Path(csv_path).resolve()
    result = verify_file(path)
    source_records, source_paths = _read_source(csv_path)
    archive_records = _read_verified_decks(
        path, result, 'source verification'
    )
    if len(source_records) != len(archive_records):
        raise CDeckError(f'source record count mismatch: {len(source_records)} != {len(archive_records)}')
    rows = zip(source_records, source_paths, archive_records, result['offsets'], strict=True)
    for number, (source_record, source_path, archive_record, offset) in enumerate(rows, start=1):
        for field in REQUIRED_FIELDS:
            if source_record[field] != archive_record[field]:
                raise CDeckError(f'record {number}: metadata mismatch: {field}')
        source_hash = _sha256(source_path)
        embedded_hash = _sha256(path, result['payloadStart'] + offset, archive_record['jpegLength'])
        if source_hash != embedded_hash:
            raise CDeckError(f'record {number}: JPEG SHA-256 mismatch')
    verified = dict(result)
    verified['sourceVerified'] = len(source_records)
    return verified

def _read_source(csv_path):
    try:
        handle = csv_path.open('r', encoding='utf-8-sig', newline='')
    except OSError as exc:
        raise CDeckError(f'cannot open source CSV: {csv_path}') from exc
    with handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames
        if fields is None:
            raise CDeckError('source CSV has no header')
        if len(fields) != len(set(fields)):
            raise CDeckError('source CSV contains duplicate field names')
        missing = [field for field in SOURCE_FIELDS if field not in fields]
        unexpected = [field for field in fields if field not in SOURCE_FIELDS]
        if missing:
            raise CDeckError(f'missing source field: {missing[0]}')
        if unexpected:
            raise CDeckError(f'unexpected source field: {unexpected[0]}')
        rows = list(reader)
    if len(rows) > MAX_RECORDS:
        raise CDeckError('record count exceeds limit')
    root = csv_path.parent.resolve(strict=True)
    records = []
    source_paths = []
    for row_number, row in enumerate(rows, start=2):
        if None in row:
            raise CDeckError(f'source row {row_number}: extra CSV values')
        if any((row.get(field) is None for field in SOURCE_FIELDS)):
            raise CDeckError(f'source row {row_number}: missing CSV value')
        src = row['src']
        if not src:
            raise CDeckError(f'source row {row_number}: src must not be empty')
        source_ref = Path(src)
        if source_ref.is_absolute() or '..' in source_ref.parts:
            raise CDeckError(f'source row {row_number}: unsafe src path')
        if source_ref.suffix.lower() not in ('.jpg', '.jpeg'):
            raise CDeckError(f'source row {row_number}: src must reference JPEG')
        try:
            jpeg_path = (root / source_ref).resolve(strict=True)
        except OSError:
            raise CDeckError(f'source row {row_number}: missing JPEG: {src}')
        try:
            jpeg_path.relative_to(root)
        except ValueError:
            raise CDeckError(f'source row {row_number}: unsafe src path')
        if not jpeg_path.is_file():
            raise CDeckError(f'source row {row_number}: missing JPEG: {src}')
        with jpeg_path.open('rb') as jpeg:
            if jpeg.read(2) != b'\xff\xd8':
                raise CDeckError(f'source row {row_number}: JPEG must start with FF D8')
        quantity_text = row['quantity'].strip()
        if not quantity_text or not quantity_text.isascii() or (not quantity_text.isdigit()):
            raise CDeckError(f'source row {row_number}: invalid quantity')
        quantity = int(quantity_text)
        jpeg_length = jpeg_path.stat().st_size
        record = {'id': source_ref.stem, 'name': row['name'], 'quantity': quantity, 'brand': row['brand'], 'printer': row['printer'], 'jpegLength': jpeg_length}
        records.append(record)
        source_paths.append(jpeg_path)
    _validate_index({'decks': records})
    return (records, source_paths)

def _encode_canonical_index(records):
    raw = json.dumps({'decks': records}, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
    if not raw:
        raise CDeckError('encoded index is empty')
    if len(raw) > MAX_INDEX_BYTES:
        raise CDeckError('encoded index exceeds 2,000,000 bytes')
    return raw

def build_collection(csv_path, output_path):
    csv_path = Path(csv_path).resolve()
    output_path = Path(output_path).resolve()
    records, source_paths = _read_source(csv_path)
    raw_index = _encode_canonical_index(records)
    payload_bytes = sum((record['jpegLength'] for record in records))
    expected_file_length = HEADER_SIZE + len(raw_index) + payload_bytes
    if expected_file_length > MAX_FILE_BYTES:
        raise CDeckError('derived file length exceeds 32-bit limit')
    if output_path == csv_path:
        raise CDeckError('output path must not replace source CSV')
    if output_path in (path.resolve() for path in source_paths):
        raise CDeckError('output path must not replace source JPEG')
    if not output_path.parent.is_dir():
        raise CDeckError('output directory does not exist')
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(mode='wb', dir=output_path.parent, prefix=f'.{output_path.name}.', suffix='.tmp', delete=False) as output:
            temporary_path = Path(output.name)
            output.write(MAGIC)
            output.write(struct.pack('<I', len(raw_index)))
            output.write(raw_index)
            for record, source_path in zip(records, source_paths, strict=True):
                copied = 0
                with source_path.open('rb') as source:
                    while True:
                        chunk = source.read(COPY_CHUNK_BYTES)
                        if not chunk:
                            break
                        output.write(chunk)
                        copied += len(chunk)
                if copied != record['jpegLength']:
                    raise CDeckError(f'source JPEG changed during build: {source_path}')
            output.flush()
            os.fsync(output.fileno())
        result = verify_source(temporary_path, csv_path)
        if result['expectedFileLength'] != expected_file_length:
            raise CDeckError('post-build length verification failed')
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
    path, limit, json_output=False, record_id=None
):
    result = inspect_file(path)
    records = result["records"]
    if record_id is not None:
        records = [
            record for record in records
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
    shown = records if limit is None else records[:limit]
    for number, record in enumerate(shown, start=1):
        print(
            "{}: id={!r} offset={} length={} quantity={} name={!r}".format(
                number,
                record["id"],
                record["jpegOffset"],
                record["jpegLength"],
                record["quantity"],
                record["name"],
            )
        )
    if len(shown) != len(records):
        print(
            f"... {len(records) - len(shown)} record(s) not shown"
        )

def _build_command(csv_path, output_path):
    result = build_collection(csv_path, output_path)
    print(f'built: {Path(output_path).resolve()}')
    _print_summary(result)

def main(argv=None):
    parser = argparse.ArgumentParser(prog="cdeck")
    subparsers = parser.add_subparsers(
        dest="command", required=True
    )
    build_parser = subparsers.add_parser("build")
    build_parser.add_argument("csv")
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
            _build_command(args.csv, args.output)
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
