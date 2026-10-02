import hashlib
import json
import re
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import cdeck

ROOT = Path(__file__).resolve().parents[1]
VECTORS = Path(__file__).parent / "vectors"
MANIFEST = VECTORS / "vectors.json"
GENERATOR = VECTORS / "generate.py"
MIGRATOR = ROOT / "tools" / "cdeck001_to_002.py"
LEGACY = Path(__file__).parent / "fixtures" / "valid-one-record.cdeck"


class SharedVectorTests(unittest.TestCase):
    def payload(self, path):
        data = Path(path).read_bytes()
        index_length = struct.unpack("<I", data[8:12])[0]
        return data[12 + index_length:]

    def test_shared_cdeck002_vectors(self):
        vectors = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(len(vectors), 21)

        for vector in vectors:
            path = VECTORS / vector["file"]
            with self.subTest(file=vector["file"]):
                if not vector["valid"]:
                    with self.assertRaisesRegex(
                        cdeck.CDeckError,
                        re.escape(vector["error"]),
                    ):
                        cdeck.verify_file(path)
                    continue

                result = cdeck.verify_file(path)
                self.assertEqual(
                    result["recordCount"],
                    vector["recordCount"],
                )

                if "canonical" in vector:
                    if vector["canonical"]:
                        cdeck.verify_canonical(path, result)
                    else:
                        with self.assertRaisesRegex(
                            cdeck.CDeckError,
                            "index is not canonical",
                        ):
                            cdeck.verify_canonical(path, result)

                expected = vector.get("expect")
                if expected is None:
                    continue

                inspected = cdeck.inspect_file(path)
                record = inspected["records"][0]
                self.assertEqual(record["id"], expected["id"])
                self.assertEqual(record["length"], expected["length"])

                if "meta" in expected:
                    self.assertEqual(record["meta"], expected["meta"])

                if "payloadSha256" in expected:
                    digest = hashlib.sha256(self.payload(path)).hexdigest()
                    self.assertEqual(digest, expected["payloadSha256"])

    def test_vector_generator_is_deterministic(self):
        expected = sorted(
            path.name for path in VECTORS.glob("*.cdeck")
        ) + ["vectors.json"]

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            result = subprocess.run(
                [sys.executable, str(GENERATOR), str(output)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            for name in expected:
                self.assertEqual(
                    (output / name).read_bytes(),
                    (VECTORS / name).read_bytes(),
                    name,
                )

    def test_migration_vector_matches_standalone_migrator(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "migrated.cdeck"
            result = subprocess.run(
                [sys.executable, str(MIGRATOR), str(LEGACY), str(output)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                output.read_bytes(),
                (VECTORS / "migration-one-record.cdeck").read_bytes(),
            )


if __name__ == "__main__":
    unittest.main()
