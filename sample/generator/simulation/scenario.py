"""Scenario generation: creates a population of aircraft around an
airport and steps them forward second-by-second, yielding realistic
target snapshots for the ASTERIX encoders to consume.

Spawn rules
-----------
* Departures: parking stand → apron → taxiway → runway threshold
  (chosen from airport.spawn_points filtered by role).
* Arrivals:
  - 50 % chance: STAR entry fix (cruise level, high speed).
  - 30 % chance: holding fix (below cruise, holding speed).
  - 20 % chance: final approach fix (3 000 ft AGL, 160 kt on ILS course).
* Minimum 2 NM spacing between any two airborne aircraft at spawn time
  (up to 20 retry attempts per aircraft).
* All spawn-state fields (altitude, speed, heading, phase, vertical_rate)
  are consistent with the chosen spawn role.
"""
from __future__ import annotations

import datetime
import random
import warnings
from dataclasses import dataclass, field
from typing import Iterator, List, Optional

from encoder.common import bearing_distance_nm, destination_point
from simulation.aircraft import (
    Aircraft,
    FlightPhase,
    OperationType,
    random_callsign,
    random_icao_address,
    random_squawk,
)
from simulation.airport import Airport, SpawnPoint, SpawnRole
from simulation.movement import MovementModel
from simulation.validation import SimulationIntegrityError, validate_snapshot

# Minimum NM separation enforced between any two *airborne* aircraft at
# the moment of spawn.  Ground aircraft are exempt (they are on the
# airport surface where separation is handled by ground procedures).
_SPAWN_SEP_NM = 2.0

# Maximum retries when placing an aircraft that would violate the
# minimum-separation rule.
_MAX_SPAWN_RETRIES = 20


@dataclass
class ScenarioConfig:
    airport: Airport
    num_aircraft: int = 20
    duration_s: int = 300
    departure_ratio: float = 0.5
    # Arrivals whose random STAR entry has no matching spawn_point will
    # fall back to this radius (NM) from the airport — always relative
    # to the airport's actual lat/lon, never a hardcoded/global coordinate.
    simulation_radius_nm: float = 30.0
    seed: int | None = None
    holding_enabled: bool = True
    missed_approach_enabled: bool = True
    start_time: datetime.time = field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).time()
    )
    # Set False to suppress SimulationIntegrityError and merely warn.
    strict_validation: bool = True


class Scenario:
    """Owns the aircraft population for one airport and advances the
    whole simulation one second at a time."""

    def __init__(self, config: ScenarioConfig):
        self.config   = config
        self.rng      = random.Random(config.seed)
        self.airport  = config.airport
        self.movement = MovementModel(
            airport=self.airport,
            rng=self.rng,
            holding_enabled=config.holding_enabled,
            missed_approach_probability=0.06 if config.missed_approach_enabled else 0.0,
        )
        self.aircraft: List[Aircraft] = []
        self._start_offsets: dict[str, float] = {}
        # Departure stand rotation: group taxi routes by their stand
        # (first waypoint) and hand stands out round-robin, so several
        # departures never spawn stacked on the same stand.
        stands: dict[tuple, list] = {}
        for route in self.airport.taxi_routes:
            stands.setdefault(route[0], []).append(route)
        self._stand_routes = list(stands.values())
        self.rng.shuffle(self._stand_routes)
        self._stand_cursor = 0
        self._midnight_seconds = (
            config.start_time.hour * 3600
            + config.start_time.minute * 60
            + config.start_time.second
        )
        self._generate_population()

    # ---- helpers for spawn-point selection ----------------------------------

    def _spawns_by_role(self, role: SpawnRole) -> List[SpawnPoint]:
        return [sp for sp in self.airport.spawn_points if sp.role == role]

    def _pick_spawn(self, role: SpawnRole) -> Optional[SpawnPoint]:
        candidates = self._spawns_by_role(role)
        return self.rng.choice(candidates) if candidates else None

    # ---- population setup ---------------------------------------------------

    def _generate_population(self) -> None:
        used_icao: set[str] = set()
        # Track airborne positions for minimum-separation check.
        airborne_positions: List[tuple[float, float]] = []

        num_departures = round(self.config.num_aircraft * self.config.departure_ratio)
        for i in range(self.config.num_aircraft):
            operation = (
                OperationType.DEPARTURE if i < num_departures else OperationType.ARRIVAL
            )
            aircraft = self._create_aircraft(
                track_number=i + 1,
                operation=operation,
                used_icao=used_icao,
                airborne_positions=airborne_positions,
            )
            self.aircraft.append(aircraft)

            # Track airborne spawn coordinates for spacing checks.
            if not aircraft.on_ground:
                airborne_positions.append((aircraft.latitude, aircraft.longitude))

            # Spread aircraft entry times so traffic density is realistic
            # rather than one burst at t=0.
            self._start_offsets[aircraft.icao_address] = self.rng.uniform(
                0, max(1.0, self.config.duration_s * 0.6)
            )

    def _airborne_too_close(
        self,
        lat: float,
        lon: float,
        airborne_positions: List[tuple[float, float]],
    ) -> bool:
        """Return True if (lat, lon) is within _SPAWN_SEP_NM of any
        existing airborne position."""
        for alat, alon in airborne_positions:
            dist_nm, _ = bearing_distance_nm(alat, alon, lat, lon)
            if dist_nm < _SPAWN_SEP_NM:
                return True
        return False

    def _create_aircraft(
        self,
        track_number: int,
        operation: OperationType,
        used_icao: set[str],
        airborne_positions: List[tuple[float, float]],
    ) -> Aircraft:
        rng       = self.rng
        icao      = random_icao_address(rng)
        while icao in used_icao:
            icao  = random_icao_address(rng)
        used_icao.add(icao)

        airline, callsign = random_callsign(rng)
        squawk            = random_squawk(rng)
        timestamp         = float(self._midnight_seconds)

        route = None
        if operation == OperationType.DEPARTURE:
            if self.airport.taxi_routes:
                # Spawn at a real parking stand (round-robin over stands
                # so departures don't stack) and taxi the real route.
                stand_routes = self._stand_routes[self._stand_cursor % len(self._stand_routes)]
                self._stand_cursor += 1
                route = self.rng.choice(stand_routes)
                _, heading = bearing_distance_nm(
                    route[0][0], route[0][1], route[1][0], route[1][1]
                )
                lat, lon = route[0]
                altitude, ground_speed, vertical_rate = self.airport.elevation_ft, 0.0, 0.0
                phase = FlightPhase.TAXI_OUT
            else:
                lat, lon, heading, altitude, ground_speed, phase, vertical_rate = (
                    self._spawn_departure()
                )
        else:
            lat, lon, heading, altitude, ground_speed, phase, vertical_rate = (
                self._spawn_arrival(airborne_positions)
            )

        aircraft = Aircraft(
            icao_address=icao,
            callsign=callsign,
            airline=airline,
            operation=operation,
            track_number=track_number,
            latitude=lat,
            longitude=lon,
            altitude_ft=altitude,
            ground_speed_kt=ground_speed,
            track_deg=heading,
            heading_deg=heading,
            vertical_rate_fpm=vertical_rate,
            squawk=squawk,
            flight_level=altitude / 100.0,
            timestamp_seconds=timestamp,
            phase=phase,
            on_ground=(operation == OperationType.DEPARTURE),
            prev_latitude=lat,
            prev_longitude=lon,
        )
        if route is not None:
            aircraft.route = list(route)
            aircraft.route_index = 1  # waypoint 0 is the spawn stand itself
        self.movement.initialize_phase_duration(aircraft)
        return aircraft

    def _spawn_departure(self) -> tuple:
        """Choose a ground spawn (parking → taxiway → threshold) for a
        departure.  Returns (lat, lon, heading, altitude, speed, phase, vrate).
        """
        airport  = self.airport
        elev     = airport.elevation_ft
        rwy_hdg  = airport.runway_heading_deg

        # Priority order: parking stand → apron → taxiway → rwy threshold.
        for role in (
            SpawnRole.PARKING,
            SpawnRole.APRON,
            SpawnRole.TAXIWAY,
            SpawnRole.RWY_THRESHOLD,
        ):
            sp = self._pick_spawn(role)
            if sp is not None:
                return (
                    sp.latitude, sp.longitude, sp.heading_deg,
                    elev, 0.0, FlightPhase.TAXI_OUT, 0.0,
                )

        # Fallback: airport centroid if no spawn points defined.
        return (
            airport.latitude, airport.longitude, rwy_hdg,
            elev, 0.0, FlightPhase.TAXI_OUT, 0.0,
        )

    def _spawn_arrival(self, airborne_positions: List[tuple[float, float]]) -> tuple:
        """Choose a spawn location for an arriving aircraft.

        50 % → STAR entry (cruise level)
        30 % → holding fix
        20 % → final approach fix

        Retries up to _MAX_SPAWN_RETRIES times to satisfy the 2 NM
        minimum separation rule.
        """
        airport = self.airport
        roll    = self.rng.random()

        for _attempt in range(_MAX_SPAWN_RETRIES):
            if roll < 0.50:
                result = self._spawn_star_entry()
            elif roll < 0.80:
                result = self._spawn_holding()
            else:
                result = self._spawn_final()

            lat, lon = result[0], result[1]
            if not self._airborne_too_close(lat, lon, airborne_positions):
                return result
            # Nudge the dice slightly and retry.
            roll = self.rng.random()

        # Last resort: place at a random far-out position (warn only).
        warnings.warn(
            f"Could not place arrival aircraft with >= {_SPAWN_SEP_NM} NM "
            f"separation after {_MAX_SPAWN_RETRIES} retries; using fallback.",
            stacklevel=4,
        )
        return self._spawn_star_entry()

    def _jitter(self, lat: float, lon: float, max_nm: float = 1.0) -> tuple[float, float]:
        """Apply a random offset (up to max_nm NM in a random direction)
        to an airborne fix position so two aircraft on the same named fix
        can never share an identical coordinate."""
        dist = self.rng.uniform(0.05, max_nm)
        brg  = self.rng.uniform(0, 360)
        return destination_point(lat, lon, brg, dist)

    def _spawn_star_entry(self) -> tuple:
        """Spawn at a STAR entry fix or a random far-out position."""
        airport = self.airport
        sp      = self._pick_spawn(SpawnRole.STAR_ENTRY)
        if sp is not None:
            altitude   = float(self.rng.choice([31000, 33000, 35000, 37000, 38000]))
            ground_speed = self.rng.uniform(420, 470)
            lat, lon     = self._jitter(sp.latitude, sp.longitude)
            return (
                lat, lon, sp.heading_deg,
                altitude, ground_speed, FlightPhase.CRUISE, 0.0,
            )

        # No spawn point defined — generate a random far-out position.
        radius      = self.config.simulation_radius_nm
        dist_nm     = self.rng.uniform(radius * 0.5, radius)
        inbound_brg = self.rng.uniform(0, 360)
        lat, lon    = destination_point(
            airport.latitude, airport.longitude, inbound_brg, dist_nm
        )
        track = (inbound_brg + 180.0) % 360.0
        altitude     = float(self.rng.choice([31000, 33000, 35000, 37000, 38000]))
        ground_speed = self.rng.uniform(420, 470)
        return (lat, lon, track, altitude, ground_speed, FlightPhase.CRUISE, 0.0)

    def _spawn_holding(self) -> tuple:
        """Spawn at a holding fix, descending and slowing."""
        airport = self.airport
        sp      = self._pick_spawn(SpawnRole.HOLDING_FIX)
        if sp is not None:
            altitude   = float(self.rng.choice([9000, 11000, 13000, 15000]))
            ground_speed = self.rng.uniform(220, 250)
            lat, lon     = self._jitter(sp.latitude, sp.longitude)
            return (
                lat, lon, sp.heading_deg,
                altitude, ground_speed, FlightPhase.HOLDING, 0.0,
            )

        # Fallback: offset from airport.
        dist_nm  = self.rng.uniform(15, 25)
        brg      = self.rng.uniform(0, 360)
        lat, lon = destination_point(airport.latitude, airport.longitude, brg, dist_nm)
        track    = (brg + 180.0) % 360.0
        return (lat, lon, track, 12000.0, 230.0, FlightPhase.HOLDING, 0.0)

    def _spawn_final(self) -> tuple:
        """Spawn on the ILS final approach fix, ~8 NM from threshold."""
        airport = self.airport

        active = airport.active_runway()
        if active:
            # Real extended centreline: 8 NM before the active threshold.
            thr, far = active
            _, rwy_brg = bearing_distance_nm(thr[0], thr[1], far[0], far[1])
            lat, lon = destination_point(thr[0], thr[1], (rwy_brg + 180.0) % 360.0, 8.0)
            lat, lon = self._jitter(lat, lon, max_nm=0.15)
            return (
                lat, lon, rwy_brg,
                airport.elevation_ft + 3000.0, self.rng.uniform(150.0, 170.0),
                FlightPhase.APPROACH, -700.0,
            )

        sp      = self._pick_spawn(SpawnRole.FINAL_APPROACH)
        if sp is not None:
            altitude     = airport.elevation_ft + 3000.0
            ground_speed = self.rng.uniform(150.0, 170.0)
            vertical_rate = -700.0
            lat, lon      = self._jitter(sp.latitude, sp.longitude, max_nm=0.15)
            return (
                lat, lon, sp.heading_deg,
                altitude, ground_speed, FlightPhase.APPROACH, vertical_rate,
            )

        # Fallback: compute a point 8 NM on the ILS centreline.
        ilshdg   = (airport.runway_heading_deg + 180.0) % 360.0
        lat, lon = destination_point(airport.latitude, airport.longitude, ilshdg, 8.0)
        altitude     = airport.elevation_ft + 3000.0
        ground_speed = self.rng.uniform(150.0, 170.0)
        return (lat, lon, airport.runway_heading_deg, altitude, ground_speed, FlightPhase.APPROACH, -700.0)

    # ---- simulation loop ----------------------------------------------------

    def run(self) -> Iterator[tuple[float, List[Aircraft]]]:
        """Advance the simulation second by second for `duration_s`
        seconds, yielding (elapsed_seconds, active_aircraft_snapshots)
        for every tick.

        The validation layer is called each tick; SimulationIntegrityError
        is raised immediately if a fatal rule fires (controllable via
        config.strict_validation).
        """
        # Validation state: maps ICAO → (lat, lon, gs) from last tick.
        prev_states: dict = {}

        for tick in range(self.config.duration_s):
            active_snapshots: List[Aircraft] = []

            for aircraft in self.aircraft:
                if tick < self._start_offsets[aircraft.icao_address]:
                    continue

                if (
                    aircraft.phase == FlightPhase.ARRIVAL
                    and aircraft.ground_speed_kt <= 0.05
                    and aircraft.phase_elapsed_s > 5
                ):
                    # Parked: keep emitting the static last state.
                    active_snapshots.append(aircraft.snapshot())
                    continue

                self.movement.step(aircraft, dt=1.0)
                active_snapshots.append(aircraft.snapshot())

            # ---- validation -------------------------------------------------
            if active_snapshots:
                try:
                    validate_snapshot(
                        active_snapshots,
                        prev_states,
                        raise_on_fatal=self.config.strict_validation,
                    )
                except SimulationIntegrityError:
                    if self.config.strict_validation:
                        raise
                    # Non-strict: warn and continue.
                    import warnings as _w
                    _w.warn(
                        f"tick {tick}: simulation integrity errors detected",
                        RuntimeWarning,
                        stacklevel=2,
                    )

            yield float(tick), active_snapshots
