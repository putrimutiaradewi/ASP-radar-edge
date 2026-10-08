"""CAT034 - Radar Service Messages (monoradar system status).

Implements a practical subset of SUR.ET1.ST05.2000-STD-02b-01 (CAT034).
Like CAT023, this category is emitted by/about the sensor itself
(north-marker, sector crossing and status messages), not per-target.

    FRN 1  I034/010  Data Source Identifier      (2 bytes)
    FRN 2  I034/000  Message Type                 (1 byte)
    FRN 3  I034/030  Time of Day                  (3 bytes)
    FRN 4  I034/020  Sector Number                (1 byte)
    FRN 5  I034/041  Antenna Rotation Speed        (2 bytes)
    FRN 6  I034/050  System Configuration Status   (1 byte, only on
                                                     message type 2/3)
"""
from __future__ import annotations

from encoder.common import build_record, encode_sac_sic, encode_time_of_day, wrap_record

CATEGORY = 34

# Message types (I034/000)
MSG_TYPE_NORTH_MARKER = 1
MSG_TYPE_SECTOR_CROSSING = 2
MSG_TYPE_GEOGRAPHICAL_FILTERING = 3
MSG_TYPE_JAMMING_STRATEGY = 4


def encode_message_type(msg_type: int) -> bytes:
    return bytes([msg_type & 0xFF])


def encode_sector_number(sector_deg: float) -> bytes:
    """1 byte, LSB = 360/256 degrees."""
    raw = int(round((sector_deg % 360.0) * (256.0 / 360.0))) & 0xFF
    return bytes([raw])


def encode_antenna_rotation_speed(seconds_per_rev: float) -> bytes:
    """2 bytes, LSB = 1/128 s."""
    raw = int(round(seconds_per_rev * 128.0)) & 0xFFFF
    return raw.to_bytes(2, "big")


def encode_system_status(operational: bool) -> bytes:
    """I034/050 (simplified single byte): NOGO bit only."""
    byte = 0 if operational else 0b10000000
    return bytes([byte])


def encode_cat034_record(
    sac: int,
    sic: int,
    timestamp_seconds: float,
    sector_deg: float,
    rotation_period_s: float = 4.0,
    message_type: int = MSG_TYPE_SECTOR_CROSSING,
    operational: bool = True,
) -> bytes:
    """Build one full CAT034 radar service record."""
    include_status = message_type in (MSG_TYPE_SECTOR_CROSSING, MSG_TYPE_GEOGRAPHICAL_FILTERING)
    items = [
        (True, encode_sac_sic(sac, sic)),                                  # FRN1 010
        (True, encode_message_type(message_type)),                        # FRN2 000
        (True, encode_time_of_day(timestamp_seconds)),                    # FRN3 030
        (True, encode_sector_number(sector_deg)),                         # FRN4 020
        (True, encode_antenna_rotation_speed(rotation_period_s)),         # FRN5 041
        (include_status, encode_system_status(operational)),              # FRN6 050
    ]
    body = build_record(items)
    return wrap_record(CATEGORY, body)
