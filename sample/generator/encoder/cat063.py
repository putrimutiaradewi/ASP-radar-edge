"""CAT063 - Sensor Status Messages.

Implements a practical subset of SUR.ET1.ST05.2000-STD-14-01 (CAT063).
Reports the operational status of a sensor contributing to the
surveillance system (radar, MLAT or ADS-B ground station). Like
CAT023/034 this is a periodic, non-target-related message.

    FRN 1  I063/010  Data Source Identifier              (2 bytes)
    FRN 2  I063/015  Service Identification                (1 byte)
    FRN 3  I063/030  Time of Message                        (3 bytes)
    FRN 4  I063/050  Sensor Identifier (SAC/SIC of sensor)   (2 bytes)
    FRN 5  I063/060  Sensor Configuration and Status         (1 byte)
    FRN 6  I063/080  Time Stamping Bias                      (2 bytes)
"""
from __future__ import annotations

from encoder.common import build_record, encode_sac_sic, encode_time_of_day, wrap_record

CATEGORY = 63


def encode_service_identification(service_id: int) -> bytes:
    return bytes([service_id & 0xFF])


def encode_sensor_identifier(sensor_sac: int, sensor_sic: int) -> bytes:
    return encode_sac_sic(sensor_sac, sensor_sic)


def encode_sensor_status(
    con: bool = False,
    psr_nogo: bool = False,
    ssr_nogo: bool = False,
    mds_nogo: bool = False,
    ads_nogo: bool = False,
    mlt_nogo: bool = False,
) -> bytes:
    """I063/060: CON, PSR, SSR, MDS, ADS, MLT status bits + spare/FX."""
    byte = 0
    if con:
        byte |= 0b10000000
    if psr_nogo:
        byte |= 0b01000000
    if ssr_nogo:
        byte |= 0b00100000
    if mds_nogo:
        byte |= 0b00010000
    if ads_nogo:
        byte |= 0b00001000
    if mlt_nogo:
        byte |= 0b00000100
    return bytes([byte])


def encode_time_stamping_bias(bias_ms: float) -> bytes:
    """2 bytes signed, LSB = 1 ms."""
    raw = int(round(bias_ms))
    raw = max(-32768, min(32767, raw))
    if raw < 0:
        raw += 1 << 16
    return raw.to_bytes(2, "big")


def encode_cat063_record(
    sac: int,
    sic: int,
    timestamp_seconds: float,
    sensor_sac: int,
    sensor_sic: int,
    service_id: int = 1,
    degraded: bool = False,
    time_bias_ms: float = 0.0,
) -> bytes:
    """Build one full CAT063 sensor status record."""
    items = [
        (True, encode_sac_sic(sac, sic)),                                          # FRN1 010
        (True, encode_service_identification(service_id)),                        # FRN2 015
        (True, encode_time_of_day(timestamp_seconds)),                            # FRN3 030
        (True, encode_sensor_identifier(sensor_sac, sensor_sic)),                 # FRN4 050
        (True, encode_sensor_status(psr_nogo=degraded, ssr_nogo=degraded)),        # FRN5 060
        (True, encode_time_stamping_bias(time_bias_ms)),                          # FRN6 080
    ]
    body = build_record(items)
    return wrap_record(CATEGORY, body)
