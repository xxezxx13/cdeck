import csv
import json
import struct
import tempfile
import unittest
from pathlib import Path

import cdeck

FIXTURES = Path(__file__).parent / "fixtures"


class CDeckVerifierTests(unittest.TestCase):
    def make_source(self, rows, files=None, fields=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)

        root = Path(temporary.name)

        for relative, data in (files or {}).items():
            path = root / relative
            path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )
            path.write_bytes(data)

        csv_path = root / "decks.csv"

        with csv_path.open(
            "w",
            encoding="utf-8",
            newline="",
        ) as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=fields or list(
                    cdeck.SOURCE_FIELDS
                ),
            )
            writer.writeheader()
            writer.writerows(rows)

        return root, csv_path

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
            "id": "deck_test",
            "name": "Test Deck",
            "quantity": 1,
            "brand": "",
            "printer": "",
            "jpegLength": 1,
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
            b"CDECK001"
            + struct.pack("<I", len(raw))
            + raw
            + payload
        )

        return self.write_bytes(data)

    def test_valid_fixture(self):
        result = cdeck.verify_file(
            FIXTURES / "valid-one-record.cdeck"
        )

        self.assertEqual(
            result["format"],
            "CDECK001",
        )

        self.assertEqual(
            result["recordCount"],
            1,
        )

        self.assertEqual(
            result["expectedFileLength"],
            11720,
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
        self.assert_invalid(
            "missing-required-member.cdeck",
            "missing required member",
        )

    def test_duplicate_id(self):
        self.assert_invalid(
            "duplicate-id.cdeck",
            "duplicate id",
        )

    def test_invalid_quantity(self):
        self.assert_invalid(
            "invalid-quantity.cdeck",
            "quantity out of range",
        )

    def test_invalid_jpeg_length(self):
        self.assert_invalid(
            "invalid-jpeg-length.cdeck",
            "jpegLength out of range",
        )

    def test_truncated_payload(self):
        self.assert_invalid(
            "truncated-payload.cdeck",
            "file length mismatch",
        )

    def test_duplicate_json_member(self):
        self.assert_invalid(
            "duplicate-json-member.cdeck",
            "duplicate JSON member",
        )

    def test_boolean_is_not_integer(self):
        path = self.write_archive(
            {
                "decks": [
                    self.make_record(quantity=True)
                ]
            }
        )

        self.assert_invalid_path(
            path,
            "quantity must be an integer",
        )

    def test_empty_id_rejected(self):
        path = self.write_archive(
            {
                "decks": [
                    self.make_record(id="")
                ]
            }
        )

        self.assert_invalid_path(
            path,
            "id must not be empty",
        )

    def test_string_byte_limit_uses_utf8_bytes(self):
        path = self.write_archive(
            {
                "decks": [
                    self.make_record(
                        id="é" * 251
                    )
                ]
            }
        )

        self.assert_invalid_path(
            path,
            "id exceeds UTF-8 byte limit",
        )

    def test_unknown_members_are_warnings(self):
        path = self.write_archive(
            {
                "decks": [
                    self.make_record(
                        extra="value"
                    )
                ],
                "future": True,
            }
        )

        result = cdeck.verify_file(path)

        self.assertEqual(
            result["warnings"],
            [
                "unknown top-level member: future",
                "record 1: unknown member: extra",
            ],
        )

    def test_nonstandard_json_constant_rejected(self):
        raw = b"{\"decks\":[],\"future\":NaN}"

        data = (
            b"CDECK001"
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
            b"CDECK001"
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
                jpegLength=2,
            ),
            self.make_record(
                id="b",
                jpegLength=3,
            ),
            self.make_record(
                id="c",
                jpegLength=1,
            ),
        ]

        path = self.write_archive(
            {"decks": records},
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
            b"CDECK001"
            + struct.pack(
                "<I",
                2_000_001,
            )
        )

        path = self.write_bytes(data)

        self.assert_invalid_path(
            path,
            "indexLength exceeds 2,000,000 bytes",
        )

    def test_record_count_limit(self):
        records = [
            self.make_record(
                id=f"deck_{number}"
            )
            for number in range(4001)
        ]

        path = self.write_archive(
            {"decks": records},
            payload=b"x" * 4001,
        )

        self.assert_invalid_path(
            path,
            "record count exceeds limit",
        )

    def test_missing_decks_member(self):
        path = self.write_archive(
            {"future": []},
            payload=b"",
        )

        self.assert_invalid_path(
            path,
            "missing required top-level member: decks",
        )


    def test_build_is_deterministic_and_preserves_order(self):
        rows = [
            {
                "src": "images/deck_002_Beta.jpg",
                "name": "Beta",
                "quantity": "2",
                "brand": "",
                "printer": "",
            },
            {
                "src": "images/deck_001_Alpha.jpg",
                "name": "Alpha",
                "quantity": "1",
                "brand": "Brand",
                "printer": "Printer",
            },
        ]

        root, csv_path = self.make_source(
            rows,
            files={
                "images/deck_001_Alpha.jpg": b"AA",
                "images/deck_002_Beta.jpg": b"BBB",
            },
        )

        first = root / "first.cdeck"
        second = root / "second.cdeck"

        first_result = cdeck.build_collection(
            csv_path,
            first,
        )

        second_result = cdeck.build_collection(
            csv_path,
            second,
        )

        self.assertEqual(
            first.read_bytes(),
            second.read_bytes(),
        )

        self.assertEqual(
            first_result["recordCount"],
            2,
        )

        self.assertEqual(
            first_result,
            second_result,
        )

        data = first.read_bytes()
        index_length = struct.unpack(
            "<I",
            data[8:12],
        )[0]

        payload_start = 12 + index_length

        index = json.loads(
            data[12:payload_start].decode("utf-8")
        )

        self.assertEqual(
            [
                record["id"]
                for record in index["decks"]
            ],
            [
                "deck_002_Beta",
                "deck_001_Alpha",
            ],
        )

        self.assertEqual(
            data[payload_start:],
            b"BBBAA",
        )

    def test_build_rejects_unexpected_source_field(self):
        rows = [
            {
                "src": "images/deck_001_A.jpg",
                "name": "A",
                "quantity": "1",
                "brand": "",
                "printer": "",
                "extra": "bad",
            }
        ]

        root, csv_path = self.make_source(
            rows,
            files={
                "images/deck_001_A.jpg": b"A",
            },
            fields=[
                *cdeck.SOURCE_FIELDS,
                "extra",
            ],
        )

        with self.assertRaisesRegex(
            cdeck.CDeckError,
            "unexpected source field: extra",
        ):
            cdeck.build_collection(
                csv_path,
                root / "output.cdeck",
            )

    def test_build_rejects_missing_jpeg(self):
        rows = [
            {
                "src": "images/deck_001_Missing.jpg",
                "name": "Missing",
                "quantity": "1",
                "brand": "",
                "printer": "",
            }
        ]

        root, csv_path = self.make_source(rows)

        with self.assertRaisesRegex(
            cdeck.CDeckError,
            "missing JPEG",
        ):
            cdeck.build_collection(
                csv_path,
                root / "output.cdeck",
            )

    def test_build_rejects_duplicate_derived_id(self):
        rows = [
            {
                "src": "images/deck_same.jpg",
                "name": "One",
                "quantity": "1",
                "brand": "",
                "printer": "",
            },
            {
                "src": "other/deck_same.jpg",
                "name": "Two",
                "quantity": "1",
                "brand": "",
                "printer": "",
            },
        ]

        root, csv_path = self.make_source(
            rows,
            files={
                "images/deck_same.jpg": b"A",
                "other/deck_same.jpg": b"B",
            },
        )

        with self.assertRaisesRegex(
            cdeck.CDeckError,
            "duplicate id",
        ):
            cdeck.build_collection(
                csv_path,
                root / "output.cdeck",
            )

    def test_build_rejects_unsafe_source_path(self):
        rows = [
            {
                "src": "../outside.jpg",
                "name": "Outside",
                "quantity": "1",
                "brand": "",
                "printer": "",
            }
        ]

        root, csv_path = self.make_source(rows)

        with self.assertRaisesRegex(
            cdeck.CDeckError,
            "unsafe src path",
        ):
            cdeck.build_collection(
                csv_path,
                root / "output.cdeck",
            )



    def test_source_equivalence_passes(self):
        rows = [
            {
                "src": "images/deck_001_A.jpg",
                "name": "Alpha",
                "quantity": "2",
                "brand": "Brand",
                "printer": "Printer",
            },
            {
                "src": "images/deck_002_B.jpg",
                "name": "Beta",
                "quantity": "1",
                "brand": "",
                "printer": "",
            },
        ]

        root, csv_path = self.make_source(
            rows,
            files={
                "images/deck_001_A.jpg": b"AAA",
                "images/deck_002_B.jpg": b"BBBB",
            },
        )

        archive = root / "collection.cdeck"

        cdeck.build_collection(
            csv_path,
            archive,
        )

        result = cdeck.verify_source(
            archive,
            csv_path,
        )

        self.assertEqual(
            result["sourceVerified"],
            2,
        )

    def test_source_equivalence_detects_image_change(self):
        rows = [
            {
                "src": "images/deck_001_A.jpg",
                "name": "Alpha",
                "quantity": "1",
                "brand": "",
                "printer": "",
            }
        ]

        root, csv_path = self.make_source(
            rows,
            files={
                "images/deck_001_A.jpg": b"AAA",
            },
        )

        archive = root / "collection.cdeck"

        cdeck.build_collection(
            csv_path,
            archive,
        )

        image = root / "images/deck_001_A.jpg"
        image.write_bytes(b"AAB")

        with self.assertRaisesRegex(
            cdeck.CDeckError,
            "JPEG SHA-256 mismatch",
        ):
            cdeck.verify_source(
                archive,
                csv_path,
            )

    def test_source_equivalence_detects_metadata_change(self):
        rows = [
            {
                "src": "images/deck_001_A.jpg",
                "name": "Alpha",
                "quantity": "1",
                "brand": "",
                "printer": "",
            }
        ]

        root, csv_path = self.make_source(
            rows,
            files={
                "images/deck_001_A.jpg": b"AAA",
            },
        )

        archive = root / "collection.cdeck"

        cdeck.build_collection(
            csv_path,
            archive,
        )

        rows[0]["name"] = "Omega"

        with csv_path.open(
            "w",
            encoding="utf-8",
            newline="",
        ) as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=list(
                    cdeck.SOURCE_FIELDS
                ),
            )
            writer.writeheader()
            writer.writerows(rows)

        with self.assertRaisesRegex(
            cdeck.CDeckError,
            "metadata mismatch: name",
        ):
            cdeck.verify_source(
                archive,
                csv_path,
            )



    def test_inspect_derives_absolute_offsets(self):
        records = [
            self.make_record(
                id="a",
                jpegLength=2,
            ),
            self.make_record(
                id="b",
                jpegLength=3,
            ),
        ]

        path = self.write_archive(
            {"decks": records},
            payload=b"abcde",
        )

        result = cdeck.inspect_file(path)

        self.assertEqual(
            result["recordCount"],
            2,
        )

        self.assertEqual(
            [
                record["jpegOffset"]
                for record in result["records"]
            ],
            [
                result["payloadStart"],
                result["payloadStart"] + 2,
            ],
        )

        self.assertEqual(
            [
                record["jpegLength"]
                for record in result["records"]
            ],
            [2, 3],
        )

    def test_inspect_does_not_add_offset_to_wire_record(self):
        path = FIXTURES / "valid-one-record.cdeck"
        result = cdeck.inspect_file(path)

        self.assertIn(
            "jpegOffset",
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
            "jpegOffset",
            index["decks"][0],
        )


if __name__ == "__main__":
    unittest.main()
