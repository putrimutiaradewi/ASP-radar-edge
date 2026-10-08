"""Aircraft state representation and flight-phase modelling."""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class FlightPhase(str, Enum):
    """Phases of flight modelled by the simulator."""

    TAXI_OUT = "taxi_out"
    DEPARTURE = "departure"
    TAKEOFF = "takeoff"
    CLIMB = "climb"
    CRUISE = "cruise"
    DESCENT = "descent"
    HOLDING = "holding"
    APPROACH = "approach"
    LANDING = "landing"
    ARRIVAL = "arrival"
    TAXI_IN = "taxi_in"
    MISSED_APPROACH = "missed_approach"


class OperationType(str, Enum):
    DEPARTURE = "departure"
    ARRIVAL = "arrival"


@dataclass
class Aircraft:
    """Full kinematic + identification state of one simulated aircraft
    at a single point in time (one "tick" == one second)."""

    icao_address: str          # 24-bit ICAO address, hex string e.g. "8A0F21"
    callsign: str              # e.g. "GIA123"
    airline: str
    operation: OperationType
    track_number: int

    latitude: float
    longitude: float
    altitude_ft: float
    ground_speed_kt: float
    track_deg: float
    heading_deg: float
    vertical_rate_fpm: float
    squawk: str                # 4-digit octal string, e.g. "1200"
    flight_level: float        # in FL units (e.g. 350 == FL350 == 35000ft)
    timestamp_seconds: float   # seconds since UTC midnight

    phase: FlightPhase = FlightPhase.CRUISE
    on_ground: bool = False

    # internal simulation bookkeeping (not ASTERIX fields)
    phase_elapsed_s: float = 0.0
    phase_duration_s: float = 0.0
    holding_turn_deg: float = 0.0
    missed_approach_used: bool = False

    # Ground route following: a polyline of real taxiway coordinates
    # the aircraft tracks during TAXI_OUT / TAXI_IN, and the index of
    # the next waypoint. None = no route (legacy straight-line taxi).
    route: Optional[list] = None
    route_index: int = 0

    # Previous-tick position — written by movement.step() before updating
    # lat/lon so that heading and ground speed can be derived from the
    # actual displacement vector rather than being randomly assigned.
    prev_latitude: float = 0.0
    prev_longitude: float = 0.0

    def snapshot(self) -> "Aircraft":
        """Return a shallow copy representing the current state,
        suitable for handing to an encoder without risk of the
        simulator later mutating the same object."""
        return Aircraft(
            icao_address=self.icao_address,
            callsign=self.callsign,
            airline=self.airline,
            operation=self.operation,
            track_number=self.track_number,
            latitude=self.latitude,
            longitude=self.longitude,
            altitude_ft=self.altitude_ft,
            ground_speed_kt=self.ground_speed_kt,
            track_deg=self.track_deg,
            heading_deg=self.heading_deg,
            vertical_rate_fpm=self.vertical_rate_fpm,
            squawk=self.squawk,
            flight_level=self.flight_level,
            timestamp_seconds=self.timestamp_seconds,
            phase=self.phase,
            on_ground=self.on_ground,
            prev_latitude=self.prev_latitude,
            prev_longitude=self.prev_longitude,
        )


# --------------------------------------------------------------------------
# Indonesian airline / callsign generation
# --------------------------------------------------------------------------

INDONESIAN_AIRLINES = [
    ("Garuda Indonesia", "GIA"),
    ("Citilink", "CTV"),
    ("Lion Air", "LNI"),
    ("Batik Air", "BTK"),
    ("Super Air Jet", "SJV"),
    ("Pelita Air", "PAS"),
    ("AirAsia Indonesia", "AWQ"),
]


def random_icao_address(rng: random.Random) -> str:
    """Generate a random, plausible 24-bit ICAO aircraft address in the
    Indonesian allocation block (8A0000 - 8A7FFF is illustrative only,
    used for simulation purposes)."""
    value = rng.randint(0x8A0000, 0x8A7FFF)
    return f"{value:06X}"


def random_callsign(rng: random.Random) -> tuple[str, str]:
    """Return (airline_name, callsign) using a real Indonesian airline
    ICAO prefix and a random 2-4 digit flight number."""
    name, prefix = rng.choice(INDONESIAN_AIRLINES)
    number = rng.randint(100, 8999)
    return name, f"{prefix}{number}"


def random_squawk(rng: random.Random) -> str:
    digits = [str(rng.randint(0, 7)) for _ in range(4)]
    return "".join(digits)
