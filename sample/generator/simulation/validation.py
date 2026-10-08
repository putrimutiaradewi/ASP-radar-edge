"""Simulation integrity validation.

Called inside scenario.run() after every tick.  Checks that all kinematic
fields are internally consistent and physically plausible.  Any failure that
is marked *fatal* raises SimulationIntegrityError immediately so bugs are
caught early during development rather than silently producing bad PCAP data.

Non-fatal issues are collected and returned for logging / inspection without
aborting the run.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from encoder.common import bearing_distance_nm

# One knot expressed as NM/s — used for "teleport" detection.
_NM_PER_S_AT_1KT = 1.0 / 3600.0

# Maximum realistic ground-speed in knots (supersonic buffer for safety).
_MAX_SPEED_KT = 700.0

# Maximum position jump in one 1-second tick before we call it a teleport.
_TELEPORT_NM = 10.0

# Minimum distance (NM) that counts as "the aircraft actually moved".
_MIN_MOVEMENT_NM = 1e-4   # ≈ 0.6 m

# Maximum speed delta between consecutive ticks (knots/second).
# Real jet accel < 10 kt/s; we allow 50 kt/s to accommodate the first tick
# of a new phase before values settle.
_MAX_ACCEL_KT_S = 50.0

# Minimum separation between airborne aircraft (NM).
# This is the hard-overlap alarm threshold (≈37 m); the spawn-time rule
# of 2 NM is enforced separately in scenario.py.
_MIN_SEP_NM = 0.02


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

@dataclass
class ValidationError:
    icao: str
    rule: str
    detail: str
    fatal: bool


class SimulationIntegrityError(RuntimeError):
    """Raised when one or more fatal validation rules fire."""

    def __init__(self, errors: List[ValidationError]):
        self.errors = errors
        lines = [f"  [{e.icao}] {e.rule}: {e.detail}" for e in errors]
        super().__init__("Simulation integrity failure(s):\n" + "\n".join(lines))


# ---------------------------------------------------------------------------
# Internal state tracked across ticks
# ---------------------------------------------------------------------------

# Keyed by ICAO hex string → (lat, lon, ground_speed_kt) from last tick.
_PrevState = Dict[str, Tuple[float, float, float]]


# ---------------------------------------------------------------------------
# Per-aircraft checks
# ---------------------------------------------------------------------------

def _check_aircraft(
    icao: str,
    lat: float,
    lon: float,
    gs: float,
    hdg: float,
    trk: float,
    prev: Optional[Tuple[float, float, float]],
    errors: List[ValidationError],
) -> None:
    """Run all per-aircraft scalar checks."""

    def err(rule: str, detail: str, fatal: bool = True) -> None:
        errors.append(ValidationError(icao=icao, rule=rule, detail=detail, fatal=fatal))

    # --- NaN / Inf heading / track ------------------------------------------
    if math.isnan(hdg) or math.isinf(hdg):
        err("HEADING_NAN", f"heading_deg={hdg!r}")
    if math.isnan(trk) or math.isinf(trk):
        err("TRACK_NAN", f"track_deg={trk!r}")

    # --- NaN / Inf position --------------------------------------------------
    if math.isnan(lat) or math.isinf(lat):
        err("LAT_NAN", f"latitude={lat!r}")
    if math.isnan(lon) or math.isinf(lon):
        err("LON_NAN", f"longitude={lon!r}")

    if prev is None:
        # First tick for this aircraft — no inter-tick checks yet.
        return

    prev_lat, prev_lon, prev_gs = prev

    # Distance actually covered this tick.
    dist_nm, _ = bearing_distance_nm(prev_lat, prev_lon, lat, lon)
    moved = dist_nm > _MIN_MOVEMENT_NM

    # --- Ground speed zero while moving -------------------------------------
    if moved and gs < 0.5:
        err(
            "GS_ZERO_WHILE_MOVING",
            f"moved {dist_nm:.4f} NM but ground_speed_kt={gs:.2f}",
        )

    # --- Teleport -----------------------------------------------------------
    if dist_nm > _TELEPORT_NM:
        err(
            "TELEPORT",
            f"jumped {dist_nm:.2f} NM in 1 s (max allowed {_TELEPORT_NM} NM)",
        )

    # --- Unrealistic acceleration -------------------------------------------
    accel = abs(gs - prev_gs)
    if accel > _MAX_ACCEL_KT_S:
        err(
            "EXCESS_ACCELERATION",
            f"speed change {accel:.1f} kt/s (max {_MAX_ACCEL_KT_S} kt/s)",
            fatal=False,   # warn only — first tick of a new phase can spike
        )


# ---------------------------------------------------------------------------
# Inter-aircraft separation check
# ---------------------------------------------------------------------------

def _check_separation(
    snapshots: list,    # List[Aircraft] — avoid circular import
    errors: List[ValidationError],
) -> None:
    """Check that no two airborne aircraft are extremely close.

    This is a non-fatal warning: during approach and holding, legitimate
    traffic can fly within a few NM of each other.  The 2 NM spawn-time
    rule is enforced separately in scenario.py.  Here we only flag
    near-identical positions (< MIN_SEP_NM) which would indicate a bug.
    """
    airborne = [ac for ac in snapshots if not ac.on_ground]
    for i, a in enumerate(airborne):
        for b in airborne[i + 1:]:
            dist_nm, _ = bearing_distance_nm(a.latitude, a.longitude, b.latitude, b.longitude)
            if dist_nm < _MIN_SEP_NM:
                errors.append(ValidationError(
                    icao=f"{a.icao_address}&{b.icao_address}",
                    rule="OVERLAP",
                    detail=(
                        f"{a.icao_address} and {b.icao_address} are "
                        f"{dist_nm:.4f} NM apart (min {_MIN_SEP_NM} NM)"
                    ),
                    fatal=False,   # warn-only: close approaches are expected
                ))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_snapshot(
    snapshots: list,            # List[Aircraft]
    prev_states: _PrevState,
    raise_on_fatal: bool = True,
) -> List[ValidationError]:
    """Validate one simulation tick.

    Args:
        snapshots:      Aircraft snapshots produced this tick.
        prev_states:    Mutable dict mapping ICAO → (lat, lon, gs) from the
                        previous tick; updated in-place by this function.
        raise_on_fatal: If True (default) raise SimulationIntegrityError when
                        any fatal error is found.

    Returns:
        List of all ValidationError objects (fatal and non-fatal).
    """
    errors: List[ValidationError] = []

    for ac in snapshots:
        icao = ac.icao_address
        prev = prev_states.get(icao)
        _check_aircraft(
            icao=icao,
            lat=ac.latitude,
            lon=ac.longitude,
            gs=ac.ground_speed_kt,
            hdg=ac.heading_deg,
            trk=ac.track_deg,
            prev=prev,
            errors=errors,
        )
        # Update state for next tick.
        prev_states[icao] = (ac.latitude, ac.longitude, ac.ground_speed_kt)

    _check_separation(snapshots, errors)

    if raise_on_fatal:
        fatal = [e for e in errors if e.fatal]
        if fatal:
            raise SimulationIntegrityError(fatal)

    return errors
