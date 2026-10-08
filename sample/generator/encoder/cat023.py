"""CAT023 - CNS/ATM Ground Station and Service Status Reports, ed 1.2.

Byte-for-byte replication of the two report types the reference Juanda
(WARR) ADS-B ground station emits, each every 2 seconds:

Ground Station Status (FSPEC d900, 13-byte record):
    FRN 1  I023/010  Data Source Identifier  (2 bytes)
    FRN 2  I023/000  Report Type             (1 byte, 1 = ground station)
    FRN 4  I023/070  Time of Day             (3 bytes)
    FRN 5  I023/100  Ground Station Status   (2 bytes, FX-chained 01 04)

Service Status (FSPEC f580, 16-byte record):
    FRN 1  I023/010  Data Source Identifier  (2 bytes)
    FRN 2  I023/000  Report Type             (1 byte, 2 = service status)
    FRN 3  I023/015  Service Type & Id       (1 byte, 0x12)
    FRN 4  I023/070  Time of Day             (3 bytes)
    FRN 6  I023/101  Service Configuration   (3 bytes, FX-chained 00 21 04)
    FRN 8  I023/110  Service Status          (1 byte, 0x08)
"""
from __future__ import annotations

from encoder.common import build_record, encode_sac_sic, encode_time_of_day, wrap_record

CATEGORY = 23

REPORT_TYPE_GROUND_STATION = b"\x01"
REPORT_TYPE_SERVICE = b"\x02"
SERVICE_TYPE_ID = b"\x12"                       # I023/015
GROUND_STATION_STATUS = bytes.fromhex("0104")   # I023/100 (FX-chained)
SERVICE_CONFIGURATION = bytes.fromhex("002104")  # I023/101 (FX-chained)
SERVICE_STATUS = b"\x08"                        # I023/110


def encode_cat023_ground_station_status(sac: int, sic: int, timestamp_seconds: float) -> bytes:
    """Ground Station Status report (real feed FSPEC d900)."""
    items = [
        (True, encode_sac_sic(sac, sic)),               # FRN1 010
        (True, REPORT_TYPE_GROUND_STATION),             # FRN2 000
        (False, b""),                                   # FRN3 015
        (True, encode_time_of_day(timestamp_seconds)),  # FRN4 070
        (True, GROUND_STATION_STATUS),                  # FRN5 100
    ]
    # The real feed pads this FSPEC to 2 octets (d9 00).
    body = build_record(items, min_fspec_octets=2)
    return wrap_record(CATEGORY, body)


def encode_cat023_service_status(sac: int, sic: int, timestamp_seconds: float) -> bytes:
    """Service Status report (real feed FSPEC f580)."""
    items = [
        (True, encode_sac_sic(sac, sic)),               # FRN1 010
        (True, REPORT_TYPE_SERVICE),                    # FRN2 000
        (True, SERVICE_TYPE_ID),                        # FRN3 015
        (True, encode_time_of_day(timestamp_seconds)),  # FRN4 070
        (False, b""),                                   # FRN5 100
        (True, SERVICE_CONFIGURATION),                  # FRN6 101
        (False, b""),                                   # FRN7 200
        (True, SERVICE_STATUS),                         # FRN8 110
    ]
    body = build_record(items)
    return wrap_record(CATEGORY, body)


def encode_cat023_record(
    sac: int,
    sic: int,
    timestamp_seconds: float,
    service_id: int = 1,
    operational: bool = True,
    degraded: bool = False,
    overloaded: bool = False,
) -> bytes:
    """Backwards-compatible entry point: emits the Ground Station
    Status variant (the parameters beyond time are retained for API
    compatibility; the real feed reports a fixed operational status)."""
    del service_id, operational, degraded, overloaded
    return encode_cat023_ground_station_status(sac, sic, timestamp_seconds)
