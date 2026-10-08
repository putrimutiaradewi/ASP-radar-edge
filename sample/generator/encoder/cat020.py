"""CAT020 - Multilateration (MLAT) Target Reports, edition 1.5.

Byte-for-byte replication of the record structures observed in the
reference Juanda (WARR) surveillance feed:

    FRN 1  I020/010  Data Source Identifier      (2 bytes)
    FRN 2  I020/020  Target Report Descriptor    (2 bytes, FX-chained)
    FRN 3  I020/140  Time of Day                 (3 bytes)
    FRN 4  I020/041  Position WGS-84             (8 bytes, LSB 180/2^25)
    FRN 5  I020/042  Position Cartesian          (6 bytes, LSB 0.5 m)
    FRN 6  I020/161  Track Number                (2 bytes)
    FRN 7  I020/170  Track Status                (1 byte)
    FRN 9  I020/202  Velocity Cartesian          (4 bytes, LSB 0.25 m/s)
    FRN 10 I020/090  Flight Level                (2 bytes, airborne variant)
    FRN 12 I020/220  Target Address              (3 bytes)
    FRN 13 I020/245  Target Identification       (7 bytes, when known)
    FRN 16 I020/210  Calculated Acceleration     (2 bytes)
    FRN 20 I020/400  Contributing Receivers      (1 + 16 bytes)
    FRN 22 I020/230  Comms/ACAS Capability       (2 bytes)
    FRN 27 RE        Reserved Expansion          (30 bytes: accuracy
                     subfields + embedded time, replicated from the
                     real feed with the time patched per record)

Three record variants matching the real feed's FSPECs:

    GROUND          ff4d4584  (96-byte record) ground target w/ callsign
    GROUND_NOID     ff494584  (89-byte record) ground target, no callsign
    AIRBORNE        ff6d4584  (98-byte record) airborne target w/ FL

plus a VEHICLE variant (ff4d6584) — the GROUND layout with I020/300
Vehicle Fleet Identification (FRN 17), marking the target as a ground
vehicle rather than an aircraft. Not present in the reference capture
(no vehicles were moving) but spec-correct per ed 1.5.
"""
from __future__ import annotations

import math
from typing import TYPE_CHECKING

from encoder.common import (
    bearing_distance_nm,
    build_record,
    encode_cartesian_velocity,
    encode_flight_level,
    encode_icao_address,
    encode_sac_sic,
    encode_time_of_day,
    encode_wgs84_position_32,
    pack_ia5_6bit,
    wrap_record,
)

if TYPE_CHECKING:
    from simulation.aircraft import Aircraft

CATEGORY = 20

# I020/020 Target Report Descriptor (2 octets, FX-chained) as captured
TRD_GROUND = b"\x41\x30"
TRD_AIRBORNE = b"\x41\x20"

TRACK_STATUS = b"\x1a"        # I020/170
ACCELERATION = b"\x00\x00"    # I020/210 (Ax=Ay=0)
COMMS_ACAS = b"\x1c\x00"      # I020/230

# Reserved Expansion templates captured from the real feed. 30 bytes:
# accuracy subfields (constant) + a 3-byte embedded time at offset
# 24..27 which is patched per record.
RE_TOD_OFFSET = 24
RE_GROUND = bytes.fromhex("1ed8d00000000000000005000a00030004000200020050c59f366b4f0800")
RE_GROUND_NOID = bytes.fromhex("1ed8d00000000000000006000b0004000500020002003bc7bd366b4f08ff")
RE_AIRBORNE = bytes.fromhex("1ed8d002e40004000701b300200029000d00b8001200fab807368d9c0802")


def encode_cartesian_position(ref_lat: float, ref_lon: float, lat: float, lon: float) -> bytes:
    """I020/042: X, Y each 3 bytes signed, LSB = 0.5 m, relative to the
    MLAT system reference point."""
    dist_nm, bearing = bearing_distance_nm(ref_lat, ref_lon, lat, lon)
    dist_m = dist_nm * 1852.0
    x = dist_m * math.sin(math.radians(bearing))
    y = dist_m * math.cos(math.radians(bearing))

    def enc(v: float) -> bytes:
        raw = int(round(v / 0.5))
        raw = max(-(1 << 23), min((1 << 23) - 1, raw))
        if raw < 0:
            raw += 1 << 24
        return raw.to_bytes(3, "big")

    return enc(x) + enc(y)


def encode_track_number(track_number: int) -> bytes:
    return (track_number & 0x0FFF).to_bytes(2, "big")


def encode_target_identification(callsign: str) -> bytes:
    """I020/245: STI byte (0x80 = downlinked) + 6 bytes IA5 callsign."""
    return b"\x80" + pack_ia5_6bit(callsign, num_chars=8)


def encode_contributing_receivers(aircraft: "Aircraft") -> bytes:
    """I020/400: REP=16 + one presence-bitmask byte per receiver unit.
    The real feed reports contribution only in the last two units; the
    exact mask varies slightly per record."""
    mask = 0x05 if (int(aircraft.icao_address, 16) + int(aircraft.timestamp_seconds)) % 2 == 0 else 0x07
    return b"\x10" + b"\x00" * 14 + bytes([0x0E, mask])


def _patched_re(template: bytes, timestamp_seconds: float) -> bytes:
    """Return the RE template with its embedded time-of-day patched
    (the real feed stamps it ~0.35 s after the record's ToD)."""
    tod = int(round((timestamp_seconds + 0.35) * 128.0)) & 0xFFFFFF
    return template[:RE_TOD_OFFSET] + tod.to_bytes(3, "big") + template[RE_TOD_OFFSET + 3:]


def encode_cat020_record(
    aircraft: "Aircraft",
    sac: int,
    sic: int,
    ref_lat: float,
    ref_lon: float,
    variant: str = "auto",
    vehicle_fleet_id: int = 1,
) -> bytes:
    """Build one CAT020 ed 1.5 record.

    variant: "ground", "ground_noid", "airborne", "vehicle" (ground
    target carrying I020/300 Vehicle Fleet Identification) or "auto"
    (picks ground/airborne from the aircraft state).
    """
    if variant == "auto":
        variant = "ground" if aircraft.on_ground else "airborne"

    airborne = variant == "airborne"
    with_ident = variant != "ground_noid"
    is_vehicle = variant == "vehicle"

    re_template = {
        "ground": RE_GROUND,
        "ground_noid": RE_GROUND_NOID,
        "airborne": RE_AIRBORNE,
        "vehicle": RE_GROUND,
    }[variant]

    items = [
        (True, encode_sac_sic(sac, sic)),                                   # FRN1 010
        (True, TRD_AIRBORNE if airborne else TRD_GROUND),                   # FRN2 020
        (True, encode_time_of_day(aircraft.timestamp_seconds)),             # FRN3 140
        (True, encode_wgs84_position_32(aircraft.latitude, aircraft.longitude)),  # FRN4 041
        (True, encode_cartesian_position(ref_lat, ref_lon, aircraft.latitude, aircraft.longitude)),  # FRN5 042
        (True, encode_track_number(aircraft.track_number)),                 # FRN6 161
        (True, TRACK_STATUS),                                               # FRN7 170
        (False, b""),                                                       # FRN8 070
        (True, encode_cartesian_velocity(aircraft.ground_speed_kt, aircraft.track_deg)),  # FRN9 202
        (airborne, encode_flight_level(aircraft.flight_level)),             # FRN10 090
        (False, b""),                                                       # FRN11 100
        (True, encode_icao_address(aircraft.icao_address)),                 # FRN12 220
        (with_ident, encode_target_identification(aircraft.callsign)),      # FRN13 245
        (False, b""), (False, b""),                                         # FRN14-15
        (True, ACCELERATION),                                               # FRN16 210
        (is_vehicle, bytes([vehicle_fleet_id & 0xFF])),                     # FRN17 300 (vehicle fleet id)
        (False, b""), (False, b""),                                         # FRN18-19
        (True, encode_contributing_receivers(aircraft)),                    # FRN20 400
        (False, b""),                                                       # FRN21 250
        (True, COMMS_ACAS),                                                 # FRN22 230
        (False, b""), (False, b""), (False, b""), (False, b""),             # FRN23-26
        (True, _patched_re(re_template, aircraft.timestamp_seconds)),       # FRN27 RE
    ]
    body = build_record(items)
    return wrap_record(CATEGORY, body)
