"""CAT247 - Version Number Exchange, edition 1.2.

Byte-for-byte replication of the periodic (every 60 s) category
version report the reference Juanda (WARR) ADS-B ground station emits
(FSPEC f0, 20-byte record):

    FRN 1  I247/010  Data Source Identifier   (2 bytes)
    FRN 2  I247/015  Service Identification   (1 byte)
    FRN 3  I247/140  Time of Day              (3 bytes)
    FRN 4  I247/550  Category Version Report  (1 + 3*N bytes)

The version report announces: CAT021 ed 0.26, CAT023 ed 1.2 and
CAT247 ed 1.2 — matching the encoders in this generator.
"""
from __future__ import annotations

from encoder.common import build_record, encode_sac_sic, encode_time_of_day, wrap_record

CATEGORY = 247

SERVICE_ID = b"\x01"  # I247/015

# I247/550: REP=3, then (category, edition-main, edition-sub) triplets:
# CAT021 v0.26, CAT023 v1.2, CAT247 v1.2
CATEGORY_VERSIONS = bytes.fromhex("03" "15001a" "170102" "f70102")


def encode_cat247_record(sac: int, sic: int, timestamp_seconds: float) -> bytes:
    """Build one CAT247 category-version report."""
    items = [
        (True, encode_sac_sic(sac, sic)),               # FRN1 010
        (True, SERVICE_ID),                             # FRN2 015
        (True, encode_time_of_day(timestamp_seconds)),  # FRN3 140
        (True, CATEGORY_VERSIONS),                      # FRN4 550
    ]
    body = build_record(items)
    return wrap_record(CATEGORY, body)
