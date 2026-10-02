# CDECK002 Shared Vectors

This directory contains the shared CDECK002 conformance corpus consumed by both Python and JavaScript tests.

The corpus covers canonical and structurally valid noncanonical files, framing failures, malformed indexes, schema failures, metadata-profile failures, safe-integer boundaries, zero-length payloads, derived-length overflow, truncation, trailing bytes, and a known CDECK001-to-CDECK002 migration result.

`generate.py` deterministically rebuilds the corpus. The legacy CDECK001 source fixture remains in `../fixtures/valid-one-record.cdeck` for migrator testing.

JavaScript validates structural/runtime behavior. Canonical byte-profile verification is Python tooling responsibility, so `canonical` expectations are enforced by the Python shared-vector test.
