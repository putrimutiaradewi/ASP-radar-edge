"""CAT048 - Monoradar (PSR/SSR) Target Reports.

Implements a practical subset of SUR.ET1.ST05.2000-STD-04-02 (CAT048):

    FRN 1  I048/010  Data Source Identifier      (2 bytes)
    FRN 2  I048/140  Time of Day                  (3 bytes)
    FRN 3  I048/020  Target Report Descriptor     (1 byte)
    FRN 4  I048/040  Measured Position (Polar)    (4 bytes)
    FRN 5  I048/070  Mode 3/A Code                (2 bytes)
    FRN 6  I048/090  Flight Level                 (2 bytes)
    FRN 7  I048/220  Target Address                (3 bytes, SSR/Mode-S only)
    FRN 8  I048/240  Aircraft Identification       (6 bytes, Mode-S only)
    FRN 9  I048/161  Track Number                  (2 bytes)

Position is transmitted in radar-polar coordinates (rho, theta)
relative to the sensor site, matching real monoradar output; the
scenario/encoder layer computes rho/theta from the aircraft's
lat/lon and the radar site location (airport reference point).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from encoder.common import (
    bearing_distance_nm,
    build_record,
    encode_flight_level,
    encode_icao_address,
    encode_mode3a,
    encode_polar_position,
    encode_sac_sic,
    encode_time_of_day,
    pack_ia5_6bit,
    wrap_record,
)

if TYPE_CHECKING:
    from simulation.aircraft import Aircraft

CATEGORY = 48


def encode_target_report_descriptor(mode_s: bool, on_ground: bool) -> bytes:
    """I048/020: TYP(3) SIM(1) RDP(1) SPI(1) RAB(1), FX=0.
    TYP: 3=combined PSR/SSR, 4=Mode-S all-call (single octet, no FX)."""
    typ = 4 if mode_s else 3
    byte = (typ & 0x07) << 5
    return bytes([byte])


def encode_aircraft_identification(callsign: str) -> bytes:
    """I048/240: 6 bytes, packed 8-char 6-bit IA5 (Mode-S only)."""
    return pack_ia5_6bit(callsign, num_chars=8)


def encode_track_number(track_number: int) -> bytes:
    return (track_number & 0xFFFF).to_bytes(2, "big")


def encode_cat048_record(
    aircraft: "Aircraft",
    sac: int,
    sic: int,
    radar_lat: float,
    radar_lon: float,
    mode_s: bool = True,
) -> bytes:
    """Build one full CAT048 record for a single aircraft snapshot, as
    seen by a radar sensor located at (radar_lat, radar_lon)."""
    distance_nm, bearing_deg = bearing_distance_nm(radar_lat, radar_lon, aircraft.latitude, aircraft.longitude)

    items = [
        (True, encode_sac_sic(sac, sic)),                                          # FRN1 010
        (True, encode_time_of_day(aircraft.timestamp_seconds)),                    # FRN2 140
        (True, encode_target_report_descriptor(mode_s, aircraft.on_ground)),       # FRN3 020
        (True, encode_polar_position(distance_nm, bearing_deg)),                   # FRN4 040
        (True, encode_mode3a(aircraft.squawk)),                                    # FRN5 070
        (not aircraft.on_ground, encode_flight_level(aircraft.flight_level)),      # FRN6 090
        (mode_s, encode_icao_address(aircraft.icao_address)),                      # FRN7 220
        (mode_s, encode_aircraft_identification(aircraft.callsign)),               # FRN8 240
        (True, encode_track_number(aircraft.track_number)),                        # FRN9 161
    ]
    body = build_record(items)
    return wrap_record(CATEGORY, body)
