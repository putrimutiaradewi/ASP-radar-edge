"""CAT062 - System Track Data.

Implements a practical subset of SUR.ET1.ST05.2000-STD-07-01 (CAT062):

    FRN 1  I062/010  Data Source Identifier          (2 bytes)
    FRN 2  I062/015  Service Identification           (1 byte)
    FRN 3  I062/070  Time of Track Information         (3 bytes)
    FRN 4  I062/040  Track Number                      (2 bytes)
    FRN 5  I062/105  Calculated Position (WGS-84)      (8 bytes)
    FRN 6  I062/136  Measured Flight Level              (2 bytes)
    FRN 7  I062/185  Calculated Track Velocity (Vx,Vy)  (4 bytes)
    FRN 8  I062/670  Target Address (simplified)        (3 bytes)
    FRN 9  I062/380  Target Identification (simplified) (6 bytes)

CAT062 represents the fused *system track* (output of a tracker
combining multiple sensors), as opposed to CAT020/021/048 which are
single-sensor target reports.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from encoder.common import (
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

CATEGORY = 62


def encode_service_identification(service_id: int) -> bytes:
    return bytes([service_id & 0xFF])


def encode_track_number(track_number: int) -> bytes:
    return (track_number & 0xFFFF).to_bytes(2, "big")


def encode_target_identification(callsign: str) -> bytes:
    return pack_ia5_6bit(callsign, num_chars=8)


def encode_cat062_record(aircraft: "Aircraft", sac: int, sic: int, service_id: int = 1) -> bytes:
    """Build one full CAT062 system track record for a single aircraft
    snapshot."""
    items = [
        (True, encode_sac_sic(sac, sic)),                                          # FRN1 010
        (True, encode_service_identification(service_id)),                        # FRN2 015
        (True, encode_time_of_day(aircraft.timestamp_seconds)),                    # FRN3 070
        (True, encode_track_number(aircraft.track_number)),                        # FRN4 040
        (True, encode_wgs84_position_32(aircraft.latitude, aircraft.longitude)),   # FRN5 105
        (not aircraft.on_ground, encode_flight_level(aircraft.flight_level)),      # FRN6 136
        (True, encode_cartesian_velocity(aircraft.ground_speed_kt, aircraft.track_deg)),  # FRN7 185
        (True, encode_icao_address(aircraft.icao_address)),                        # FRN8 670
        (True, encode_target_identification(aircraft.callsign)),                   # FRN9 380
    ]
    body = build_record(items)
    return wrap_record(CATEGORY, body)
