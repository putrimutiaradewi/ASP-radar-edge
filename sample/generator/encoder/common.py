"""Low level, spec-shared ASTERIX encoding primitives.

These helpers implement the bit-level encodings that are reused across
many ASTERIX categories (FSPEC, time of day, WGS-84 positions, IA5
6-bit callsigns, Mode 3/A codes, flight levels, ...). Every function
here returns real, spec-shaped bytes -- nothing here is a random or
placeholder byte array.
"""
from __future__ import annotations

import math
from typing import List, Sequence, Tuple

# --------------------------------------------------------------------------
# FSPEC (Field Specification)
# --------------------------------------------------------------------------


def encode_fspec(presence: Sequence[bool], min_octets: int = 0) -> bytes:
    """Encode a list of "is item N present" booleans into an ASTERIX FSPEC.

    FSPEC is a sequence of octets. Within each octet, bits 8..2 (MSB
    first) indicate presence of the next 7 data items (in FRN order),
    and bit 1 (LSB) is the "Field Extension" (FX) bit: 1 means another
    FSPEC octet follows, 0 means this is the last octet.

    `min_octets` pads the FSPEC with trailing all-zero extension octets
    (FX chained) up to the requested size. Several real surveillance
    systems (including the reference Juanda feed) always emit a fixed
    FSPEC width even when the last octets carry no presence bits.
    """
    if not presence:
        raise ValueError("presence list must not be empty")

    octets: List[int] = []
    for start in range(0, len(presence), 7):
        chunk = presence[start:start + 7]
        byte = 0
        for i, present in enumerate(chunk):
            if present:
                byte |= 1 << (7 - i)  # bit8 .. bit2
        octets.append(byte)

    while len(octets) < min_octets:
        octets.append(0x00)

    result = bytearray()
    for idx, byte in enumerate(octets):
        if idx < len(octets) - 1:
            byte |= 0x01  # FX = 1, more octets follow
        result.append(byte)
    return bytes(result)


def build_record(items: Sequence[Tuple[bool, bytes]], min_fspec_octets: int = 0) -> bytes:
    """Build FSPEC + concatenated present-item payloads (no CAT/LEN).

    `items` is an ordered sequence (FRN 1, 2, 3, ...) of
    (is_present, encoded_bytes) tuples. Items that are not present are
    skipped entirely, which is what gives ASTERIX its variable length
    records.
    """
    presence = [present for present, _ in items]
    fspec = encode_fspec(presence, min_octets=min_fspec_octets)
    payload = bytearray(fspec)
    for present, data in items:
        if present:
            payload += data
    return bytes(payload)


def wrap_record(category: int, record_body: bytes) -> bytes:
    """Wrap a record body (FSPEC + items) with CAT and 2-byte LEN.

    LEN counts the whole record: CAT(1) + LEN(2) + body.
    """
    length = 3 + len(record_body)
    if length > 0xFFFF:
        raise ValueError("record too long for a 16-bit length field")
    return bytes([category & 0xFF]) + length.to_bytes(2, "big") + record_body


def wrap_data_block(category: int, record_bodies: Sequence[bytes]) -> bytes:
    """Wrap one or more record bodies (each FSPEC+items, no CAT/LEN)
    that share the same category into a single ASTERIX data block:
    CAT(1) + LEN(2) + concatenated record bodies. This is how real
    systems batch several records of the same category into one
    network message."""
    body = bytearray()
    for rec in record_bodies:
        body += rec
    length = 3 + len(body)
    return bytes([category & 0xFF]) + length.to_bytes(2, "big") + bytes(body)


# --------------------------------------------------------------------------
# Time of Day  (LSB = 1/128 s, 3 bytes, range 0..86400s)
# --------------------------------------------------------------------------


def encode_time_of_day(seconds_since_midnight: float) -> bytes:
    value = int(round(seconds_since_midnight * 128.0)) & 0xFFFFFF
    return value.to_bytes(3, "big")


# --------------------------------------------------------------------------
# WGS-84 Position
# --------------------------------------------------------------------------


def _encode_signed_angle(deg: float, num_bytes: int, lsb: float) -> bytes:
    max_val = 1 << (num_bytes * 8 - 1)
    raw = int(round(deg / lsb))
    raw = max(-max_val, min(max_val - 1, raw))
    if raw < 0:
        raw += 1 << (num_bytes * 8)
    return raw.to_bytes(num_bytes, "big")


def encode_wgs84_position_32(lat: float, lon: float) -> bytes:
    """I020/041, I062/105 style: 4 bytes lat + 4 bytes lon,
    LSB = 180 / 2**25 degrees."""
    lsb = 180.0 / (2 ** 25)
    return _encode_signed_angle(lat, 4, lsb) + _encode_signed_angle(lon, 4, lsb)


def encode_wgs84_position_24(lat: float, lon: float) -> bytes:
    """I021/130 style: 3 bytes lat + 3 bytes lon,
    LSB = 180 / 2**23 degrees."""
    lsb = 180.0 / (2 ** 23)
    return _encode_signed_angle(lat, 3, lsb) + _encode_signed_angle(lon, 3, lsb)


# --------------------------------------------------------------------------
# Polar position (rho/theta), used by CAT048 (I048/040)
# --------------------------------------------------------------------------


def encode_polar_position(rho_nm: float, theta_deg: float) -> bytes:
    """Measured Position in polar coordinates.
    RHO: 2 bytes, LSB = 1/256 NM. THETA: 2 bytes, LSB = 360/2**16 deg.
    """
    rho_raw = int(round(rho_nm * 256.0)) & 0xFFFF
    theta_raw = int(round((theta_deg % 360.0) * (65536.0 / 360.0))) & 0xFFFF
    return rho_raw.to_bytes(2, "big") + theta_raw.to_bytes(2, "big")


def bearing_distance_nm(ref_lat: float, ref_lon: float, lat: float, lon: float) -> Tuple[float, float]:
    """Great-circle distance (NM) and initial bearing (deg, 0-360) from
    a reference point to a target point."""
    r_nm = 3440.065
    phi1, phi2 = math.radians(ref_lat), math.radians(lat)
    dphi = math.radians(lat - ref_lat)
    dlambda = math.radians(lon - ref_lon)

    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    distance = r_nm * c

    y = math.sin(dlambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlambda)
    bearing = (math.degrees(math.atan2(y, x)) + 360.0) % 360.0
    return distance, bearing


def destination_point(lat: float, lon: float, bearing_deg: float, distance_nm: float) -> Tuple[float, float]:
    """Given a start point, initial bearing and distance, compute the
    destination lat/lon using great-circle navigation."""
    r_nm = 3440.065
    delta = distance_nm / r_nm
    theta = math.radians(bearing_deg)
    phi1 = math.radians(lat)
    lambda1 = math.radians(lon)

    phi2 = math.asin(math.sin(phi1) * math.cos(delta) + math.cos(phi1) * math.sin(delta) * math.cos(theta))
    lambda2 = lambda1 + math.atan2(
        math.sin(theta) * math.sin(delta) * math.cos(phi1),
        math.cos(delta) - math.sin(phi1) * math.sin(phi2),
    )
    return math.degrees(phi2), (math.degrees(lambda2) + 540.0) % 360.0 - 180.0


# --------------------------------------------------------------------------
# Flight Level / Geometric height
# --------------------------------------------------------------------------


def encode_flight_level(flight_level: float) -> bytes:
    """2 bytes, signed, LSB = 1/4 FL (i.e. 25 ft)."""
    raw = int(round(flight_level * 4.0))
    raw = max(-32768, min(32767, raw))
    if raw < 0:
        raw += 1 << 16
    return raw.to_bytes(2, "big")


def encode_geometric_height(height_ft: float) -> bytes:
    """2 bytes signed, LSB = 6.25 ft (I021/140)."""
    raw = int(round(height_ft / 6.25))
    raw = max(-32768, min(32767, raw))
    if raw < 0:
        raw += 1 << 16
    return raw.to_bytes(2, "big")


# --------------------------------------------------------------------------
# Mode 3/A code (squawk)
# --------------------------------------------------------------------------


def encode_mode3a(squawk_octal: str) -> bytes:
    """2 bytes: bits 16-14 spare/validity flags (V,G,L=0), bit13 spare,
    bits 12-1 = four 3-bit octal digits (A4 A3 A2 A1)."""
    digits = [int(c) for c in squawk_octal.strip().zfill(4)[-4:]]
    value = 0
    for d in digits:
        value = (value << 3) | (d & 0x7)
    return value.to_bytes(2, "big")


# --------------------------------------------------------------------------
# Ground speed / Track angle / Heading / Air speed
# --------------------------------------------------------------------------


def encode_ground_vector(ground_speed_kt: float, track_deg: float) -> bytes:
    """I021/160 style: GS 2 bytes LSB=2**-14 NM/s, TA 2 bytes LSB=360/2**16 deg."""
    gs_nm_per_s = ground_speed_kt / 3600.0
    gs_raw = int(round(gs_nm_per_s / (2 ** -14))) & 0xFFFF
    ta_raw = int(round((track_deg % 360.0) * (65536.0 / 360.0))) & 0xFFFF
    return gs_raw.to_bytes(2, "big") + ta_raw.to_bytes(2, "big")


def encode_cartesian_velocity(ground_speed_kt: float, track_deg: float) -> bytes:
    """I062/185 style: Vx, Vy each 2 bytes signed, LSB = 0.25 m/s."""
    speed_ms = ground_speed_kt * 0.514444
    rad = math.radians(track_deg)
    vx = speed_ms * math.sin(rad)
    vy = speed_ms * math.cos(rad)

    def enc(v: float) -> bytes:
        raw = int(round(v / 0.25))
        raw = max(-32768, min(32767, raw))
        if raw < 0:
            raw += 1 << 16
        return raw.to_bytes(2, "big")

    return enc(vx) + enc(vy)


def encode_air_speed(speed_kt: float) -> bytes:
    """I021/150: bit16 IM (0=IAS), bits15-1 speed LSB=2**-14 NM/s (as knots-ish)."""
    ias_nm_per_s = speed_kt / 3600.0
    raw = int(round(ias_nm_per_s / (2 ** -14))) & 0x7FFF
    return raw.to_bytes(2, "big")


def encode_rate_of_climb_descent(fpm: float) -> bytes:
    """2 bytes signed, LSB = 6.25 ft/min (used by several categories)."""
    raw = int(round(fpm / 6.25))
    raw = max(-32768, min(32767, raw))
    if raw < 0:
        raw += 1 << 16
    return raw.to_bytes(2, "big")


# --------------------------------------------------------------------------
# ICAO 24-bit Target Address
# --------------------------------------------------------------------------


def encode_icao_address(icao_hex: str) -> bytes:
    value = int(icao_hex, 16) & 0xFFFFFF
    return value.to_bytes(3, "big")


# --------------------------------------------------------------------------
# Data Source Identifier (SAC/SIC)
# --------------------------------------------------------------------------


def encode_sac_sic(sac: int, sic: int) -> bytes:
    return bytes([sac & 0xFF, sic & 0xFF])


# --------------------------------------------------------------------------
# IA5 6-bit packed callsign / target identification
# --------------------------------------------------------------------------

_IA5_ALPHABET = " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


def _char_to_6bit(ch: str) -> int:
    ch = ch.upper()
    if ch == " " or (ch.isalnum() and ch.isascii()):
        # This mapping matches the ICAO/ASTERIX IA5 6-bit table: it is
        # equivalent to taking the low 6 bits of the character's ASCII
        # code for space, digits and uppercase letters.
        return ord(ch) & 0x3F
    return ord(" ") & 0x3F  # unsupported char -> space


def pack_ia5_6bit(text: str, num_chars: int = 8) -> bytes:
    """Pack up to `num_chars` characters into 6-bit IA5 codes, tightly
    packed into `num_chars * 6 / 8` bytes (8 chars -> 6 bytes)."""
    padded = (text.upper() + " " * num_chars)[:num_chars]
    bits = 0
    total_bits = 0
    for ch in padded:
        code = _char_to_6bit(ch)
        bits = (bits << 6) | code
        total_bits += 6

    num_bytes = (total_bits + 7) // 8
    # left-pad to full byte boundary if needed
    bits <<= (num_bytes * 8 - total_bits)
    return bits.to_bytes(num_bytes, "big")
