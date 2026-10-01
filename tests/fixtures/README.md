# Fixture Manifest

Fixture source JPEG: deck_100_LM_Filters_USPCC.jpg
Source JPEG bytes: 11503
Valid fixture ID: deck_100_LM_Filters_USPCC

Expected results:

`vectors.json` contains shared expected fixture results consumed by both the Python and JavaScript test suites.

PASS  valid-one-record.cdeck
FAIL  wrong-magic.cdeck
FAIL  unsupported-generation.cdeck
FAIL  zero-index.cdeck
FAIL  malformed-utf8.cdeck
FAIL  invalid-json.cdeck
FAIL  missing-required-member.cdeck
FAIL  duplicate-id.cdeck
FAIL  invalid-quantity.cdeck
FAIL  invalid-jpeg-length.cdeck
FAIL  truncated-payload.cdeck
FAIL  duplicate-json-member.cdeck
