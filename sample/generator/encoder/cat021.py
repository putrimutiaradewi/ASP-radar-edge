"""CAT021 - ADS-B Target Reports, edition 0.26.

Byte-for-byte replication of the record structures observed in the
reference Juanda (WARR) surveillance feed. The feed uses the ed 0.26
UAP (confirmed by its own CAT247 version report: CAT021 v0.26):

    FRN 1  I021/010  Data Source Identifier            (2 bytes)
    FRN 2  I021/040  Target Report Descriptor          (2 bytes)
    FRN 3  I021/030  Time of Day                       (3 bytes)
    FRN 4  I021/130  Position in WGS-84                (6 bytes, LSB 180/2^23)
    FRN 5  I021/080  Target Address                    (3 bytes)
    FRN 6  I021/140  Geometric Altitude                (2 bytes, LSB 6.25 ft)
    FRN 7  I021/090  Figure of Merit                   (2 bytes)
    FRN 8  I021/210  Link Technology                   (1 byte)
    FRN 10 I021/145  Flight Level                      (2 bytes, LSB 1/4 FL)
    FRN 14 I021/155  Barometric Vertical Rate          (2 bytes, LSB 6.25 fpm)
    FRN 15 I021/157  Geometric Vertical Rate           (2 bytes, LSB 6.25 fpm)
    FRN 16 I021/160  Ground Vector (GS + Track Angle)  (4 bytes)
    FRN 18 I021/170  Target Identification             (6 bytes IA5)
    FRN 19 I021/095  Velocity Accuracy                 (1 byte)
    FRN 21 I021/200  Target Status                     (1 byte)
    FRN 22 I021/020  Emitter Category                  (1 byte)
    FRN 24 I021/146  Intermediate Selected Altitude    (2 bytes)
    FRN 27 I021/070  Mode 3/A Code                     (2 bytes)
    FRN 28 I021/131  Signal Amplitude                  (1 byte)

Four record variants are emitted, matching the real feed's FSPECs
(which are always padded to 5 octets with a trailing empty extension):

    AIRBORNE       ffa1db0300  (43-byte record) position + velocity
    AIRBORNE_SEL   ffa35ba700  (48-byte record) + baro rate/emitter/
                               selected altitude/Mode 3A
    GROUND         fb811b0300  (33-byte record) ground position report
    GROUND_IDENT   fb811b8700  (36-byte record) 5-second ident beacon
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from encoder.common import (
    build_record,
    encode_flight_level,
    encode_geometric_height,
    encode_ground_vector,
    encode_icao_address,
    encode_mode3a,
    encode_rate_of_climb_descent,
    encode_sac_sic,
    encode_time_of_day,
    encode_wgs84_position_24,
    pack_ia5_6bit,
    wrap_record,
)

if TYPE_CHECKING:
    from simulation.aircraft import Aircraft

CATEGORY = 21
FSPEC_OCTETS = 5  # the real feed always pads CAT021 FSPEC to 5 octets

# I021/040 Target Report Descriptor values as captured from the real feed
TRD_AIRBORNE = b"\x00\x28"
TRD_AIRBORNE_SEL = b"\x04\x28"
TRD_GROUND = b"\x40\x20"

# Constants observed in every real record
FOM = b"\x00\x07"            # I021/090 Figure of Merit
LINK_TECH = b"\x08"          # I021/210 (normal reports)
LINK_TECH_IDENT = b"\x18"    # I021/210 (5s ident beacon variant)
VELOCITY_ACCURACY = b"\x00"  # I021/095
TARGET_STATUS = b"\x00"      # I021/200
EMITTER_CATEGORY = b"\x03"   # I021/020 (medium airborne aircraft)


def encode_selected_altitude(altitude_ft: float) -> bytes:
    """I021/146: source/validity bits 0xC000 + altitude, LSB 25 ft."""
    raw = max(0, min(0x1FFF, int(round(altitude_ft / 25.0))))
    return (0xC000 | raw).to_bytes(2, "big")


def encode_signal_amplitude(aircraft: "Aircraft") -> bytes:
    """I021/131: 1 byte. Real values range ~0x17-0x3e; derive a stable,
    slightly varying amplitude from the target address and time."""
    base = 0x17 + (int(aircraft.icao_address, 16) % 0x1D)
    wobble = int(aircraft.timestamp_seconds) % 5
    return bytes([base + wobble])


def _selected_altitude_target(aircraft: "Aircraft") -> float:
    """Selected altitude (mode control panel target) for I021/146."""
    if aircraft.vertical_rate_fpm > 100:      # climbing: aiming for cruise
        return max(aircraft.altitude_ft, 20000.0 + (int(aircraft.icao_address, 16) % 8) * 2000.0)
    if aircraft.vertical_rate_fpm < -100:     # descending: aiming for approach
        return 3000.0
    return aircraft.altitude_ft


def encode_cat021_record(
    aircraft: "Aircraft",
    sac: int,
    sic: int,
    variant: str = "auto",
) -> bytes:
    """Build one CAT021 ed 0.26 record.

    variant: "airborne", "airborne_sel", "ground", "ground_ident" or
    "auto" (airborne aircraft -> airborne, ground -> ground).
    """
    if variant == "auto":
        variant = "ground" if aircraft.on_ground else "airborne"

    position = encode_wgs84_position_24(aircraft.latitude, aircraft.longitude)
    common_head = [
        (True, encode_sac_sic(sac, sic)),                       # FRN1 010
    ]

    if variant == "airborne":
        items = common_head + [
            (True, TRD_AIRBORNE),                                # FRN2 040
            (True, encode_time_of_day(aircraft.timestamp_seconds)),  # FRN3 030
            (True, position),                                    # FRN4 130
            (True, encode_icao_address(aircraft.icao_address)),  # FRN5 080
            (True, encode_geometric_height(aircraft.altitude_ft)),  # FRN6 140
            (True, FOM),                                         # FRN7 090
            (True, LINK_TECH),                                   # FRN8 210
            (False, b""),                                        # FRN9 230
            (True, encode_flight_level(aircraft.flight_level)),  # FRN10 145
            (False, b""), (False, b""), (False, b""),            # FRN11-13
            (False, b""),                                        # FRN14 155
            (True, encode_rate_of_climb_descent(aircraft.vertical_rate_fpm)),  # FRN15 157
            (True, encode_ground_vector(aircraft.ground_speed_kt, aircraft.track_deg)),  # FRN16 160
            (False, b""),                                        # FRN17 165
            (True, pack_ia5_6bit(aircraft.callsign, num_chars=8)),  # FRN18 170
            (True, VELOCITY_ACCURACY),                           # FRN19 095
            (False, b""),                                        # FRN20 032
            (True, TARGET_STATUS),                               # FRN21 200
            (False, b""), (False, b""), (False, b""),            # FRN22-24
            (False, b""), (False, b""), (False, b""),            # FRN25-27
            (True, encode_signal_amplitude(aircraft)),           # FRN28 131
        ]
    elif variant == "airborne_sel":
        items = common_head + [
            (True, TRD_AIRBORNE_SEL),                            # FRN2 040
            (True, encode_time_of_day(aircraft.timestamp_seconds)),  # FRN3 030
            (True, position),                                    # FRN4 130
            (True, encode_icao_address(aircraft.icao_address)),  # FRN5 080
            (True, encode_geometric_height(aircraft.altitude_ft)),  # FRN6 140
            (True, FOM),                                         # FRN7 090
            (True, LINK_TECH),                                   # FRN8 210
            (False, b""),                                        # FRN9 230
            (True, encode_flight_level(aircraft.flight_level)),  # FRN10 145
            (False, b""), (False, b""), (False, b""),            # FRN11-13
            (True, encode_rate_of_climb_descent(aircraft.vertical_rate_fpm)),  # FRN14 155
            (False, b""),                                        # FRN15 157
            (True, encode_ground_vector(aircraft.ground_speed_kt, aircraft.track_deg)),  # FRN16 160
            (False, b""),                                        # FRN17 165
            (True, pack_ia5_6bit(aircraft.callsign, num_chars=8)),  # FRN18 170
            (True, VELOCITY_ACCURACY),                           # FRN19 095
            (False, b""),                                        # FRN20 032
            (True, TARGET_STATUS),                               # FRN21 200
            (True, EMITTER_CATEGORY),                            # FRN22 020
            (False, b""),                                        # FRN23 220
            (True, encode_selected_altitude(_selected_altitude_target(aircraft))),  # FRN24 146
            (False, b""), (False, b""),                          # FRN25-26
            (True, encode_mode3a(aircraft.squawk)),              # FRN27 070
            (True, encode_signal_amplitude(aircraft)),           # FRN28 131
        ]
    elif variant == "ground":
        items = common_head + [
            (True, TRD_GROUND),                                  # FRN2 040
            (True, encode_time_of_day(aircraft.timestamp_seconds)),  # FRN3 030
            (True, position),                                    # FRN4 130
            (True, encode_icao_address(aircraft.icao_address)),  # FRN5 080
            (False, b""),                                        # FRN6 140 (no geometric alt on ground)
            (True, FOM),                                         # FRN7 090
            (True, LINK_TECH),                                   # FRN8 210
            (False, b""), (False, b""),                          # FRN9-10
            (False, b""), (False, b""), (False, b""),            # FRN11-13
            (False, b""), (False, b""), (False, b""),            # FRN14-16
            (False, b""),                                        # FRN17
            (True, pack_ia5_6bit(aircraft.callsign, num_chars=8)),  # FRN18 170
            (True, VELOCITY_ACCURACY),                           # FRN19 095
            (False, b""),                                        # FRN20 032
            (True, TARGET_STATUS),                               # FRN21 200
            (False, b""), (False, b""), (False, b""),            # FRN22-24
            (False, b""), (False, b""), (False, b""),            # FRN25-27
            (True, encode_signal_amplitude(aircraft)),           # FRN28 131
        ]
    elif variant == "ground_ident":
        items = common_head + [
            (True, TRD_GROUND),                                  # FRN2 040
            (True, encode_time_of_day(aircraft.timestamp_seconds)),  # FRN3 030
            (True, position),                                    # FRN4 130
            (True, encode_icao_address(aircraft.icao_address)),  # FRN5 080
            (False, b""),                                        # FRN6 140
            (True, FOM),                                         # FRN7 090
            (True, LINK_TECH_IDENT),                             # FRN8 210
            (False, b""), (False, b""),                          # FRN9-10
            (False, b""), (False, b""), (False, b""),            # FRN11-13
            (False, b""), (False, b""), (False, b""),            # FRN14-16
            (False, b""),                                        # FRN17
            (True, pack_ia5_6bit(aircraft.callsign, num_chars=8)),  # FRN18 170
            (True, VELOCITY_ACCURACY),                           # FRN19 095
            (False, b""),                                        # FRN20 032
            (True, TARGET_STATUS),                               # FRN21 200
            (True, EMITTER_CATEGORY),                            # FRN22 020
            (False, b""), (False, b""),                          # FRN23-24
            (False, b""), (False, b""),                          # FRN25-26
            (True, encode_mode3a(aircraft.squawk)),              # FRN27 070
            (True, encode_signal_amplitude(aircraft)),           # FRN28 131
        ]
    else:
        raise ValueError(f"unknown CAT021 variant: {variant!r}")

    body = build_record(items, min_fspec_octets=FSPEC_OCTETS)
    return wrap_record(CATEGORY, body)
