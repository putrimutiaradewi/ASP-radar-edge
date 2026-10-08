"""CAT019 - Multilateration System Status Messages, edition 1.2.

Byte-for-byte replication of the periodic (1 Hz) MLAT system status
message observed in the reference Juanda (WARR) surveillance feed
(FSPEC ffe0, 60-byte record):

    FRN 1  I019/010  Data Source Identifier        (2 bytes)
    FRN 2  I019/000  Message Type                  (1 byte, 2 = periodic)
    FRN 3  I019/140  Time of Day                   (3 bytes)
    FRN 4  I019/550  System Status                 (1 byte)
    FRN 5  I019/551  Tracking Processor Status     (1 byte)
    FRN 6  I019/552  Remote Sensor Status          (1 + 16*2 bytes)
    FRN 7  I019/553  Reference Transponder Status  (3 bytes, FX-chained)
    FRN 8  I019/600  Position of MLT Ref Point     (8 bytes, LSB 180/2^30)
    FRN 9  I019/610  Height of MLT Ref Point       (2 bytes)
    FRN 10 I019/620  WGS-84 Undulation             (1 byte)
"""
from __future__ import annotations

from encoder.common import (
    build_record,
    encode_sac_sic,
    encode_time_of_day,
    wrap_record,
)

CATEGORY = 19

MESSAGE_TYPE_PERIODIC = b"\x02"      # I019/000
SYSTEM_STATUS = b"\x80"              # I019/550
TRACKING_PROCESSOR_STATUS = b"\x20"  # I019/551

# I019/552: REP=16 remote sensor units with their status bytes, exactly
# as reported by the real MLAT system (units 0x01..0x10; most report
# 0x7c/0x74, unit 8 degraded 0x54, unit 14 0x70).
REMOTE_SENSOR_STATUS = bytes.fromhex(
    "10"
    "017c027c037c04740574067407740854"
    "09740a7c0b7c0c7c0d7c0e700f741074"
)

REFERENCE_TRANSPONDER_STATUS = bytes.fromhex("81c180")  # I019/553 (FX-chained)
HEIGHT_OF_REF_POINT = b"\x00\x00"                       # I019/610
WGS84_UNDULATION = b"\x00"                              # I019/620


def encode_ref_point_position(lat: float, lon: float) -> bytes:
    """I019/600: latitude + longitude, 4 bytes each, LSB = 180/2^30 deg."""
    lsb = 180.0 / (1 << 30)

    def enc(deg: float) -> bytes:
        raw = int(round(deg / lsb))
        raw = max(-(1 << 31), min((1 << 31) - 1, raw))
        if raw < 0:
            raw += 1 << 32
        return raw.to_bytes(4, "big")

    return enc(lat) + enc(lon)


def encode_cat019_record(
    sac: int,
    sic: int,
    timestamp_seconds: float,
    ref_lat: float,
    ref_lon: float,
) -> bytes:
    """Build one CAT019 periodic system status record."""
    items = [
        (True, encode_sac_sic(sac, sic)),                    # FRN1 010
        (True, MESSAGE_TYPE_PERIODIC),                       # FRN2 000
        (True, encode_time_of_day(timestamp_seconds)),       # FRN3 140
        (True, SYSTEM_STATUS),                               # FRN4 550
        (True, TRACKING_PROCESSOR_STATUS),                   # FRN5 551
        (True, REMOTE_SENSOR_STATUS),                        # FRN6 552
        (True, REFERENCE_TRANSPONDER_STATUS),                # FRN7 553
        (True, encode_ref_point_position(ref_lat, ref_lon)),  # FRN8 600
        (True, HEIGHT_OF_REF_POINT),                         # FRN9 610
        (True, WGS84_UNDULATION),                            # FRN10 620
    ]
    body = build_record(items)
    return wrap_record(CATEGORY, body)
