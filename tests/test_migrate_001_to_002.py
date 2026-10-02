import hashlib
import json
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import cdeck

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "cdeck001_to_002.py"
FIXTURES = Path(__file__).parent / "fixtures"

class CDeckMigrationTests(unittest.TestCase):
    def run_tool(self, source, output):
        return subprocess.run(
            [sys.executable, str(TOOL), str(source), str(output)],
            text=True,
            capture_output=True,
            check=False,
        )

    def payload(self, path):
        data = Path(path).read_bytes()
        index_length = struct.unpack("<I", data[8:12])[0]
        return data[12 + index_length:]

    def write_legacy(self, root, records, payloads):
        raw = json.dumps(
            {"decks": records},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        path = root / "legacy.cdeck"
        path.write_bytes(
            b"CDECK001"
            + struct.pack("<I", len(raw))
            + raw
            + b"".join(payloads)
        )
        return path

    def test_valid_fixture_migrates_deterministically(self):
        source = FIXTURES / "valid-one-record.cdeck"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first.cdeck"
            second = root / "second.cdeck"
            a = self.run_tool(source, first)
            b = self.run_tool(source, second)
            self.assertEqual(a.returncode, 0, a.stderr)
            self.assertEqual(b.returncode, 0, b.stderr)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            result = cdeck.verify_file(first)
            cdeck.verify_canonical(first, result)
            inspected = cdeck.inspect_file(first)
            record = inspected["records"][0]
            self.assertEqual(record["id"], "deck_100_LM_Filters_USPCC")
            self.assertEqual(record["length"], 11503)
            self.assertEqual(record["meta"]["name"], "USPCC L&M Filters")
            self.assertEqual(record["meta"]["quantity"], 1)
            self.assertEqual(record["meta"]["brand"], "The United States Playing Card Company")
            self.assertEqual(record["meta"]["printer"], "The United States Playing Card Company")
            old_hash = hashlib.sha256(self.payload(source)).digest()
            new_hash = hashlib.sha256(self.payload(first)).digest()
            self.assertEqual(old_hash, new_hash)

    def test_record_order_and_payload_order_are_preserved(self):
        records = [
            {"id": "b", "name": "Beta", "quantity": 2, "brand": "", "printer": "", "jpegLength": 2},
            {"id": "a", "name": "Alpha", "quantity": 1, "brand": "Brand", "printer": "Printer", "jpegLength": 3},
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.write_legacy(root, records, [b"BB", b"AAA"])
            output = root / "output.cdeck"
            result = self.run_tool(source, output)
            self.assertEqual(result.returncode, 0, result.stderr)
            inspected = cdeck.inspect_file(output)
            self.assertEqual([r["id"] for r in inspected["records"]], ["b", "a"])
            self.assertEqual(self.payload(output), b"BBAAA")

    def test_invalid_legacy_fixture_is_rejected(self):
        source = FIXTURES / "invalid-quantity.cdeck"
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "output.cdeck"
            result = self.run_tool(source, output)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("quantity out of range", result.stderr)
            self.assertFalse(output.exists())

    def test_input_cannot_be_replaced(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "legacy.cdeck"
            source.write_bytes((FIXTURES / "valid-one-record.cdeck").read_bytes())
            result = self.run_tool(source, source)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("output path must differ", result.stderr)
            self.assertEqual(source.read_bytes()[:8], b"CDECK001")

if __name__ == "__main__":
    unittest.main()
