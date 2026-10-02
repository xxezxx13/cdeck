import contextlib
import io
import json
import struct
import tempfile
import unittest
from pathlib import Path

import cdeck

FIXTURES = Path(__file__).parent / "fixtures"


class CDeckVerifierTests(unittest.TestCase):
    def make_manifest(self, manifest, files=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        for relative, data in (files or {}).items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        manifest_path = root / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        return root, manifest_path

    def run_cli(self, *args):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = cdeck.main(list(args))
        return status, stdout.getvalue(), stderr.getvalue()

    def assert_invalid(self, name, message):
        with self.assertRaisesRegex(
            cdeck.CDeckError,
            message,
        ):
            cdeck.verify_file(FIXTURES / name)

    def assert_invalid_path(self, path, message):
        with self.assertRaisesRegex(
            cdeck.CDeckError,
            message,
        ):
            cdeck.verify_file(path)

    def make_record(self, **updates):
        record = {
            "id": "asset_test",
            "length": 1,
            "meta": {"name": "Test"},
        }
        record.update(updates)
        return record

    def write_bytes(self, data):
        handle = tempfile.NamedTemporaryFile(
            suffix=".cdeck",
            delete=False,
        )

        path = Path(handle.name)
        handle.write(data)
        handle.close()

        self.addCleanup(
            path.unlink,
            missing_ok=True,
        )

        return path

    def write_archive(self, index, payload=b"x"):
        raw = json.dumps(
            index,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

        data = (
            b"CDECK002"
            + struct.pack("<I", len(raw))
            + raw
            + payload
        )

        return self.write_bytes(data)

    def test_valid_fixture(self):
        path = self.write_archive(
            [self.make_record()],
            payload=b"x",
        )

        result = cdeck.verify_file(path)

        self.assertEqual(
            result["format"],
            "CDECK002",
        )
        self.assertEqual(
            result["recordCount"],
            1,
        )
        self.assertEqual(
            result["payloadBytes"],
            1,
        )
        self.assertEqual(
            result["warnings"],
            [],
        )

    def test_wrong_magic(self):
        self.assert_invalid(
            "wrong-magic.cdeck",
            "wrong magic",
        )

    def test_unsupported_generation(self):
        self.assert_invalid(
            "unsupported-generation.cdeck",
            "unsupported CDECK generation",
        )

    def test_zero_index(self):
        self.assert_invalid(
            "zero-index.cdeck",
            "indexLength must be greater than zero",
        )

    def test_malformed_utf8(self):
        self.assert_invalid(
            "malformed-utf8.cdeck",
            "invalid UTF-8 index",
        )

    def test_invalid_json(self):
        self.assert_invalid(
            "invalid-json.cdeck",
            "invalid JSON index",
        )

    def test_missing_required_member(self):
        record = self.make_record()
        del record["length"]

        path = self.write_archive(
            [record],
            payload=b"",
        )

        self.assert_invalid_path(
            path,
            "missing required member: length",
        )

    def test_duplicate_id(self):
        path = self.write_archive(
            [
                self.make_record(id="same"),
                self.make_record(id="same"),
            ],
            payload=b"xx",
        )

        self.assert_invalid_path(
            path,
            "duplicate id",
        )

    def test_invalid_length(self):
        path = self.write_archive(
            [self.make_record(length=-1)],
            payload=b"",
        )

        self.assert_invalid_path(
            path,
            "length out of range",
        )

    def test_unsafe_length_rejected(self):
        path = self.write_archive(
            [
                self.make_record(
                    length=cdeck.MAX_SAFE_INTEGER + 1
                )
            ],
            payload=b"",
        )

        self.assert_invalid_path(
            path,
            "length out of range",
        )

    def test_truncated_payload(self):
        path = self.write_archive(
            [self.make_record(length=2)],
            payload=b"x",
        )

        self.assert_invalid_path(
            path,
            "file length mismatch",
        )

    def test_duplicate_json_member(self):
        self.assert_invalid(
            "duplicate-json-member.cdeck",
            "duplicate JSON member",
        )

    def test_boolean_is_not_integer(self):
        path = self.write_archive(
            [self.make_record(length=True)],
            payload=b"",
        )

        self.assert_invalid_path(
            path,
            "length must be an integer",
        )

    def test_empty_id_rejected(self):
        path = self.write_archive(
            [self.make_record(id="")],
            payload=b"x",
        )

        self.assert_invalid_path(
            path,
            "id must not be empty",
        )

    def test_meta_must_be_object(self):
        path = self.write_archive(
            [self.make_record(meta=[])],
            payload=b"x",
        )

        self.assert_invalid_path(
            path,
            "meta must be an object",
        )

    def test_extra_core_member_rejected(self):
        path = self.write_archive(
            [
                self.make_record(
                    extra="value"
                )
            ],
            payload=b"x",
        )

        self.assert_invalid_path(
            path,
            "unexpected member: extra",
        )

    def test_nonstandard_json_constant_rejected(self):
        raw = b"{\"decks\":[],\"future\":NaN}"

        data = (
            b"CDECK002"
            + struct.pack("<I", len(raw))
            + raw
        )

        path = self.write_bytes(data)

        self.assert_invalid_path(
            path,
            "invalid JSON constant: NaN",
        )

    def test_utf8_bom_rejected(self):
        index = "{\"decks\":[]}".encode(
            "utf-8"
        )

        raw = b"\xef\xbb\xbf" + index

        data = (
            b"CDECK002"
            + struct.pack("<I", len(raw))
            + raw
        )

        path = self.write_bytes(data)

        self.assert_invalid_path(
            path,
            "must not contain a UTF-8 BOM",
        )

    def test_derived_offsets(self):
        records = [
            self.make_record(
                id="a",
                length=2,
            ),
            self.make_record(
                id="b",
                length=3,
            ),
            self.make_record(
                id="c",
                length=1,
            ),
        ]

        path = self.write_archive(
            records,
            payload=b"abcdef",
        )

        result = cdeck.verify_file(path)

        self.assertEqual(
            result["offsets"],
            [0, 2, 5],
        )
        self.assertEqual(
            result["payloadBytes"],
            6,
        )

    def test_index_length_limit(self):
        data = (
            b"CDECK002"
            + struct.pack(
                "<I",
                cdeck.MAX_INDEX_BYTES + 1,
            )
        )

        path = self.write_bytes(data)

        self.assert_invalid_path(
            path,
            "indexLength exceeds 16 MiB runtime limit",
        )

    def test_record_count_limit(self):
        records = [
            self.make_record(
                id=f"asset_{number}",
                length=0,
            )
            for number in range(
                cdeck.MAX_RECORDS + 1
            )
        ]

        path = self.write_archive(
            records,
            payload=b"",
        )

        self.assert_invalid_path(
            path,
            "record count exceeds limit",
        )

    def test_index_must_be_array(self):
        path = self.write_archive(
            {"records": []},
            payload=b"",
        )

        self.assert_invalid_path(
            path,
            "index must be an array",
        )

    def test_build_is_deterministic_and_preserves_manifest_order(self):
        manifest = [
            {"id": "beta", "path": "payloads/beta.bin", "meta": {"name": "Beta"}},
            {"id": "alpha", "path": "payloads/alpha.bin", "meta": {"name": "Alpha"}},
            {"id": "empty", "path": "payloads/empty.bin", "meta": {}},
        ]
        root, manifest_path = self.make_manifest(
            manifest,
            files={
                "payloads/beta.bin": b"\x00\xffBINARY",
                "payloads/alpha.bin": b"ALPHA",
                "payloads/empty.bin": b"",
            },
        )
        first = root / "first.cdeck"
        second = root / "second.cdeck"
        first_result = cdeck.build_collection(manifest_path, first)
        second_result = cdeck.build_collection(manifest_path, second)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual(first_result, second_result)
        inspected = cdeck.inspect_file(first)
        self.assertEqual([record["id"] for record in inspected["records"]], ["beta", "alpha", "empty"])
        self.assertEqual([record["length"] for record in inspected["records"]], [8, 5, 0])
        self.assertTrue(all("path" not in record for record in inspected["records"]))
        self.assertEqual(first.read_bytes()[inspected["payloadStart"]:], b"\x00\xffBINARYALPHA")
        cdeck.verify_canonical(first)

    def test_manifest_must_be_array(self):
        root, manifest_path = self.make_manifest({"id": "bad"})
        with self.assertRaisesRegex(cdeck.CDeckError, "manifest must be an array"):
            cdeck.build_collection(manifest_path, root / "output.cdeck")

    def test_manifest_entry_shape_is_exact(self):
        cases = [
            ([{"id": "x", "meta": {}}], "missing manifest member: path"),
            ([{"id": "x", "path": "payload.bin", "meta": {}, "extra": 1}], "unexpected manifest member: extra"),
        ]
        for manifest, message in cases:
            with self.subTest(message=message):
                root, manifest_path = self.make_manifest(manifest)
                with self.assertRaisesRegex(cdeck.CDeckError, message):
                    cdeck.build_collection(manifest_path, root / "output.cdeck")

    def test_manifest_rejects_duplicate_ids(self):
        manifest = [
            {"id": "same", "path": "a.bin", "meta": {}},
            {"id": "same", "path": "b.bin", "meta": {}},
        ]
        root, manifest_path = self.make_manifest(manifest, {"a.bin": b"A", "b.bin": b"B"})
        with self.assertRaisesRegex(cdeck.CDeckError, "duplicate id"):
            cdeck.build_collection(manifest_path, root / "output.cdeck")

    def test_manifest_validates_metadata_profile(self):
        cases = [
            ({"value": 1.5}, "JSON floats are not allowed"),
            ({"value": cdeck.MAX_SAFE_INTEGER + 1}, "JSON integer exceeds safe integer limit"),
        ]
        for meta, message in cases:
            with self.subTest(message=message):
                manifest = [{"id": "x", "path": "payload.bin", "meta": meta}]
                root, manifest_path = self.make_manifest(manifest, {"payload.bin": b""})
                with self.assertRaisesRegex(cdeck.CDeckError, message):
                    cdeck.build_collection(manifest_path, root / "output.cdeck")

    def test_build_rejects_missing_payload(self):
        manifest = [{"id": "missing", "path": "missing.bin", "meta": {}}]
        root, manifest_path = self.make_manifest(manifest)
        with self.assertRaisesRegex(cdeck.CDeckError, "missing payload"):
            cdeck.build_collection(manifest_path, root / "output.cdeck")

    def test_build_rejects_unsafe_payload_paths(self):
        root, manifest_path = self.make_manifest([], {"payload.bin": b"X"})
        values = ["../outside.bin", str((root / "payload.bin").resolve())]
        for value in values:
            with self.subTest(path=value):
                manifest = [{"id": "x", "path": value, "meta": {}}]
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                with self.assertRaisesRegex(cdeck.CDeckError, "unsafe payload path"):
                    cdeck.build_collection(manifest_path, root / "output.cdeck")

    def test_build_rejects_symlink_escape(self):
        outside = self.write_bytes(b"OUTSIDE")
        manifest = [{"id": "escape", "path": "escape.bin", "meta": {}}]
        root, manifest_path = self.make_manifest(manifest)
        (root / "escape.bin").symlink_to(outside)
        with self.assertRaisesRegex(cdeck.CDeckError, "unsafe payload path"):
            cdeck.build_collection(manifest_path, root / "output.cdeck")

    def test_build_allows_internal_symlink_and_arbitrary_binary(self):
        payload = b"\x00OPAQUE-BINARY\xff\x01"
        manifest = [{"id": "binary", "path": "link.bin", "meta": {"kind": "opaque"}}]
        root, manifest_path = self.make_manifest(manifest, {"real.bin": payload})
        (root / "link.bin").symlink_to("real.bin")
        archive = root / "output.cdeck"
        result = cdeck.build_collection(manifest_path, archive)
        self.assertEqual(archive.read_bytes()[result["payloadStart"]:], payload)

    def test_build_rejects_overwriting_manifest_or_payload(self):
        manifest = [{"id": "x", "path": "payload.bin", "meta": {}}]
        root, manifest_path = self.make_manifest(manifest, {"payload.bin": b"X"})
        with self.assertRaisesRegex(cdeck.CDeckError, "output path must not replace manifest"):
            cdeck.build_collection(manifest_path, manifest_path)
        with self.assertRaisesRegex(cdeck.CDeckError, "output path must not replace source payload"):
            cdeck.build_collection(manifest_path, root / "payload.bin")

    def test_source_equivalence_passes_and_detects_payload_change(self):
        manifest = [{"id": "x", "path": "payload.bin", "meta": {"name": "Original"}}]
        root, manifest_path = self.make_manifest(manifest, {"payload.bin": b"ABC"})
        archive = root / "collection.cdeck"
        cdeck.build_collection(manifest_path, archive)
        result = cdeck.verify_source(archive, manifest_path)
        self.assertEqual(result["sourceVerified"], 1)
        (root / "payload.bin").write_bytes(b"ABD")
        with self.assertRaisesRegex(cdeck.CDeckError, "payload SHA-256 mismatch"):
            cdeck.verify_source(archive, manifest_path)

    def test_source_equivalence_detects_metadata_change(self):
        manifest = [{"id": "x", "path": "payload.bin", "meta": {"name": "Original"}}]
        root, manifest_path = self.make_manifest(manifest, {"payload.bin": b"ABC"})
        archive = root / "collection.cdeck"
        cdeck.build_collection(manifest_path, archive)
        manifest[0]["meta"]["name"] = "Changed"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(cdeck.CDeckError, "metadata mismatch"):
            cdeck.verify_source(archive, manifest_path)

    def test_inspect_derives_absolute_offsets(self):
        records = [
            self.make_record(
                id="a",
                length=2,
            ),
            self.make_record(
                id="b",
                length=3,
            ),
        ]

        path = self.write_archive(
            records,
            payload=b"abcde",
        )

        result = cdeck.inspect_file(path)

        self.assertEqual(
            [
                record["offset"]
                for record in result["records"]
            ],
            [
                result["payloadStart"],
                result["payloadStart"] + 2,
            ],
        )

        self.assertEqual(
            [
                record["length"]
                for record in result["records"]
            ],
            [2, 3],
        )

    def test_inspect_does_not_add_offset_to_wire_record(self):
        path = self.write_archive(
            [self.make_record()],
            payload=b"x",
        )

        result = cdeck.inspect_file(path)

        self.assertIn(
            "offset",
            result["records"][0],
        )

        data = path.read_bytes()
        index_length = struct.unpack(
            "<I",
            data[8:12],
        )[0]

        index = json.loads(
            data[12:12 + index_length].decode(
                "utf-8"
            )
        )

        self.assertNotIn(
            "offset",
            index[0],
        )

    def test_v3_rejects_cdeck001_generation(self):
        raw = b"[]"
        path = self.write_bytes(
            b"CDECK001" + struct.pack("<I", len(raw)) + raw
        )
        with self.assertRaisesRegex(
            cdeck.CDeckError,
            "unsupported CDECK generation",
        ):
            cdeck.verify_file(path)

    def test_v3_metadata_profile_values(self):
        valid = self.make_record(
            length=0,
            meta={
                "array": [None, True, False, -cdeck.MAX_SAFE_INTEGER, cdeck.MAX_SAFE_INTEGER],
                "text": "é😀",
            },
        )
        records, offsets, total = cdeck._validate_index([valid])
        self.assertEqual(offsets, [0])
        self.assertEqual(total, 0)
        self.assertEqual(records[0]["meta"]["text"], "é😀")
        cases = [
            ({"value": 1.5}, "JSON floats are not allowed"),
            ({"value": cdeck.MAX_SAFE_INTEGER + 1}, "JSON integer exceeds safe integer limit"),
            ({"value": "\ud800"}, "JSON string contains lone surrogate"),
            ({1: "bad"}, "JSON object key must be a string"),
        ]
        for meta, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(cdeck.CDeckError, message):
                    cdeck._validate_index([self.make_record(length=0, meta=meta)])

    def test_v3_canonical_encoder_profile(self):
        raw = cdeck._encode_canonical_index([
            self.make_record(
                id="x",
                length=0,
                meta={
                    "z": "é😀",
                    "a": "\b\t\n\f\r\x00\x1f",
                    "nest": {"😀": 2, "β": 1, "a": 0},
                },
            )
        ])
        expected = (
            '[{"id":"x","length":0,"meta":'
            '{"a":"\\b\\t\\n\\f\\r\\u0000\\u001f",'
            '"nest":{"a":0,"β":1,"😀":2},"z":"é😀"}}]'
        ).encode("utf-8")
        self.assertEqual(raw, expected)

    def test_v3_canonical_rejects_equivalent_noncanonical_forms(self):
        raws = [
            b'[{"id":"x","length":0,"meta":{"b":1,"a":2}}]',
            b'[{"id":"\\u0078","length":0,"meta":{}}]',
        ]
        for raw in raws:
            with self.subTest(raw=raw):
                path = self.write_bytes(
                    b"CDECK002" + struct.pack("<I", len(raw)) + raw
                )
                self.assertEqual(cdeck.verify_file(path)["recordCount"], 1)
                with self.assertRaisesRegex(cdeck.CDeckError, "index is not canonical"):
                    cdeck.verify_canonical(path)

    def test_v3_verify_canonical_accepts_writer_encoding(self):
        raw = cdeck._encode_canonical_index([self.make_record()])
        path = self.write_bytes(
            b"CDECK002" + struct.pack("<I", len(raw)) + raw + b"x"
        )

        status, stdout, stderr = self.run_cli(
            "verify",
            str(path),
            "--canonical",
        )

        self.assertEqual(status, 0)
        self.assertIn(
            "format: CDECK002",
            stdout,
        )
        self.assertEqual(stderr, "")

    def test_v3_verify_canonical_rejects_noncanonical_index(self):
        index = [self.make_record()]

        raw = json.dumps(
            index,
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")

        path = self.write_bytes(
            b"CDECK002"
            + struct.pack("<I", len(raw))
            + raw
            + b"x"
        )

        self.assertEqual(
            cdeck.verify_file(path)[
                "recordCount"
            ],
            1,
        )

        status, stdout, stderr = self.run_cli(
            "verify",
            str(path),
            "--canonical",
        )

        self.assertEqual(status, 1)
        self.assertEqual(stdout, "")
        self.assertIn(
            "index is not canonical",
            stderr,
        )

    def test_v3_inspect_json_emits_json_only(self):
        path = self.write_archive(
            [self.make_record()],
            payload=b"x",
        )

        status, stdout, stderr = self.run_cli(
            "inspect",
            str(path),
            "--json",
        )

        self.assertEqual(status, 0)

        result = json.loads(stdout)

        self.assertEqual(
            result["recordCount"],
            1,
        )
        self.assertEqual(
            len(result["records"]),
            1,
        )
        self.assertEqual(stderr, "")

    def test_v3_inspect_id_filters_exact_record(self):
        path = self.write_archive(
            [
                self.make_record(
                    id="alpha"
                ),
                self.make_record(
                    id="beta"
                ),
            ],
            payload=b"xx",
        )

        status, stdout, stderr = self.run_cli(
            "inspect",
            str(path),
            "--id",
            "beta",
        )

        self.assertEqual(status, 0)
        self.assertIn(
            "id=" + repr("beta"),
            stdout,
        )
        self.assertNotIn(
            "id=" + repr("alpha"),
            stdout,
        )
        self.assertEqual(stderr, "")

    def test_v3_inspect_id_rejects_unknown_record(self):
        path = self.write_archive(
            [
                self.make_record(
                    id="alpha"
                )
            ],
            payload=b"x",
        )

        status, stdout, stderr = self.run_cli(
            "inspect",
            str(path),
            "--id",
            "missing",
        )

        self.assertEqual(status, 1)
        self.assertEqual(stdout, "")
        self.assertIn(
            "record id not found: missing",
            stderr,
        )

    def test_v3_verify_quiet_has_no_success_output(self):
        path = self.write_archive(
            [self.make_record()],
            payload=b"x",
        )

        status, stdout, stderr = self.run_cli(
            "verify",
            str(path),
            "--quiet",
        )

        self.assertEqual(status, 0)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")

    def test_v3_numeric_model_boundaries(self):
        values = [
            (2 ** 32) - 1,
            2 ** 32,
            (2 ** 32) + 1,
            8 * (2 ** 30),
            cdeck.MAX_SAFE_INTEGER,
        ]

        for value in values:
            with self.subTest(value=value):
                records, offsets, total = (
                    cdeck._validate_index([
                        self.make_record(
                            length=value
                        )
                    ])
                )

                self.assertEqual(
                    offsets,
                    [0],
                )
                self.assertEqual(
                    total,
                    value,
                )
                self.assertEqual(
                    records[0]["length"],
                    value,
                )

        records, offsets, total = (
            cdeck._validate_index([
                self.make_record(
                    length=0
                )
            ])
        )

        self.assertEqual(offsets, [0])
        self.assertEqual(total, 0)

        with self.assertRaisesRegex(
            cdeck.CDeckError,
            "length out of range",
        ):
            cdeck._validate_index([
                self.make_record(
                    length=2 ** 53
                )
            ])

        with self.assertRaisesRegex(
            cdeck.CDeckError,
            "cumulative payload length exceeds safe integer limit",
        ):
            cdeck._validate_index([
                self.make_record(
                    id="first",
                    length=cdeck.MAX_SAFE_INTEGER,
                ),
                self.make_record(
                    id="second",
                    length=1,
                ),
            ])


if __name__ == "__main__":
    unittest.main()
