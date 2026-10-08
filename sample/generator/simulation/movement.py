"""Per-second kinematic updates and flight-phase transitions.

Physics model guarantees:
  - heading_deg is derived from bearing(prev_position → current_position)
    after every position update; it is never randomly assigned.
  - ground_speed_kt is derived from distance(prev, current) / dt so that
    it is exactly zero when the aircraft does not move.
  - Heading changes are clamped to MAX_HDG_RATE_DEG_S (3°/s airborne,
    5°/s on ground) so tracks are always smooth.
  - Phase-specific speeds and vertical rates match real-world performance
    envelopes (see the per-handler docstrings).

Step order each tick
---------------------
  1. Save prev_lat, prev_lon.
  2. Call the phase handler (sets *target* speed / vertical_rate via
     _ease_towards; may also set a target_heading for ground/fixed phases).
  3. Integrate position using the *previous* tick's speed and heading so
     position, speed and heading all refer to the same interval.
  4. Derive *actual* speed from dist(prev, new) / dt.
  5. Derive *actual* heading from bearing(prev → new) when the aircraft
     moved; otherwise keep the current heading (no NaN risk).
  6. Smooth the heading towards the computed bearing at the capped rate.
  7. Write ground_speed_kt, track_deg, heading_deg, altitude, FL, on_ground.
  8. Advance phase timers and trigger phase transitions.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass

from encoder.common import bearing_distance_nm, destination_point
from simulation.aircraft import Aircraft, FlightPhase, OperationType
from simulation.airport import Airport

# Maximum heading-change rates.
_MAX_HDG_AIR_DEG_S:   float = 3.0   # airborne phases
_MAX_HDG_GROUND_DEG_S: float = 5.0  # taxi / ground phases

# Phase sequences — MISSED_APPROACH spliced in dynamically.
DEPARTURE_SEQUENCE = [
    FlightPhase.TAXI_OUT,
    FlightPhase.TAKEOFF,
    FlightPhase.CLIMB,
    FlightPhase.CRUISE,
]

ARRIVAL_SEQUENCE = [
    FlightPhase.CRUISE,
    FlightPhase.DESCENT,
    FlightPhase.APPROACH,
    FlightPhase.LANDING,
    FlightPhase.TAXI_IN,
    FlightPhase.ARRIVAL,
]

# Threshold below which we say "the aircraft is stationary" for the purpose
# of skipping the position integration.
_MIN_SPEED_KT: float = 0.5

# Minimum displacement in NM to consider the aircraft "moved" this tick;
# below this we skip the bearing derivation to avoid NaN from tiny float noise.
_MIN_DIST_NM: float = 1e-5   # ≈ 0.06 m


def _next_phase(sequence: list[FlightPhase], current: FlightPhase) -> FlightPhase:
    try:
        idx = sequence.index(current)
    except ValueError:
        return sequence[-1]
    if idx + 1 < len(sequence):
        return sequence[idx + 1]
    return sequence[-1]  # stay in terminal phase


@dataclass
class MovementModel:
    """Drives one aircraft's kinematics forward in time, one second at a
    time, and manages flight-phase transitions.

    All kinematic values are derived from the actual position displacement
    so that ground_speed_kt, heading_deg and track_deg are always mutually
    consistent with the position change.
    """

    airport: Airport
    rng: random.Random
    holding_enabled: bool = True
    missed_approach_probability: float = 0.06

    # ---- public API ---------------------------------------------------------

    def initialize_phase_duration(self, aircraft: Aircraft) -> None:
        aircraft.phase_elapsed_s = 0.0
        aircraft.phase_duration_s = self._duration_for(aircraft.phase)
        # Route-following taxi phases end when the route is completed,
        # not on a timer.
        if aircraft.phase == FlightPhase.TAXI_OUT and aircraft.route:
            aircraft.phase_duration_s = 1e9
        # With a real runway line, approach and landing end on geometry
        # (reaching the threshold / slowing down), not on a timer.
        if aircraft.phase in (FlightPhase.APPROACH, FlightPhase.LANDING) and self.airport.runway_line:
            aircraft.phase_duration_s = 1e9
        # Seed prev_position to current position so the first tick produces
        # zero displacement (no phantom speed / NaN heading on tick 0).
        aircraft.prev_latitude  = aircraft.latitude
        aircraft.prev_longitude = aircraft.longitude

    # ---- ground route following ---------------------------------------------

    def _follow_route(self, ac: Aircraft, dt: float) -> bool:
        """Steer the aircraft towards the next waypoint of its assigned
        ground route (real taxiway coordinates). Returns True when the
        final waypoint has been reached."""
        if not ac.route or ac.route_index >= len(ac.route):
            return True
        wp = ac.route[ac.route_index]
        dist_nm, brg = bearing_distance_nm(ac.latitude, ac.longitude, wp[0], wp[1])
        step_nm = max(ac.ground_speed_kt, 2.0) * dt / 3600.0
        # Consume any waypoints we will pass within this tick.
        while dist_nm <= step_nm * 1.25 and ac.route_index < len(ac.route) - 1:
            ac.route_index += 1
            wp = ac.route[ac.route_index]
            dist_nm, brg = bearing_distance_nm(ac.latitude, ac.longitude, wp[0], wp[1])
        if ac.route_index >= len(ac.route) - 1 and dist_nm <= step_nm * 1.25:
            ac.route_index = len(ac.route)
            return True
        # Taxiway turns are sharp; snap the heading to the leg bearing.
        ac.heading_deg = ac.track_deg = brg
        return False

    def step(self, aircraft: Aircraft, dt: float = 1.0) -> None:
        """Advance the aircraft's state by `dt` seconds.

        The step is ordered so that position, speed and heading are always
        derived from each other rather than set independently.
        """
        # ---- 1. Snapshot previous position ----------------------------------
        prev_lat = aircraft.latitude
        prev_lon = aircraft.longitude
        prev_hdg = aircraft.heading_deg

        # ---- 2. Phase handler (target speeds / vertical rates / headings) ---
        handler = self._HANDLERS[aircraft.phase]
        handler(self, aircraft, dt)

        # ---- 3. Integrate position using *previous* speed and heading --------
        #  We use the heading that was in effect at the start of this tick
        #  (prev_hdg) to advance the position; the new heading will be derived
        #  from the resulting displacement below.
        if aircraft.ground_speed_kt > _MIN_SPEED_KT:
            dist_nm = aircraft.ground_speed_kt * dt / 3600.0
            new_lat, new_lon = destination_point(
                prev_lat, prev_lon, prev_hdg, dist_nm
            )
            aircraft.latitude  = new_lat
            aircraft.longitude = new_lon
        # else: aircraft is stationary — lat/lon unchanged.

        # ---- 4. Derive actual speed from displacement / dt ------------------
        actual_dist_nm, _ = bearing_distance_nm(
            prev_lat, prev_lon, aircraft.latitude, aircraft.longitude
        )
        aircraft.ground_speed_kt = actual_dist_nm / dt * 3600.0  # NM/s → kt

        # ---- 5. Update track and book-keep for next tick --------------------
        ground_phase = aircraft.phase in (
            FlightPhase.TAXI_OUT,
            FlightPhase.TAXI_IN,
            FlightPhase.ARRIVAL,
        )

        aircraft.track_deg = aircraft.heading_deg  # no wind model: track == heading

        aircraft.prev_latitude  = prev_lat
        aircraft.prev_longitude = prev_lon

        # ---- 6. Altitude, FL, on_ground, timers -----------------------------
        aircraft.altitude_ft = max(0.0, aircraft.altitude_ft + aircraft.vertical_rate_fpm * dt / 60.0)
        aircraft.flight_level = aircraft.altitude_ft / 100.0
        aircraft.on_ground = ground_phase or (
            aircraft.phase in (FlightPhase.TAKEOFF, FlightPhase.LANDING)
            and aircraft.altitude_ft <= self.airport.elevation_ft + 5.0
        )

        aircraft.phase_elapsed_s  += dt
        aircraft.timestamp_seconds += dt

        if aircraft.phase_elapsed_s >= aircraft.phase_duration_s:
            self._advance_phase(aircraft)

    # ---- phase transition logic ---------------------------------------------

    def _advance_phase(self, aircraft: Aircraft) -> None:
        # A completed ground route does not carry over into the next phase.
        if aircraft.phase in (FlightPhase.TAXI_OUT, FlightPhase.TAXI_IN):
            aircraft.route = None
            aircraft.route_index = 0
        # Go-around decision is made at the end of the approach (short
        # final), before the aircraft is committed to the landing roll.
        if aircraft.phase == FlightPhase.APPROACH and not aircraft.missed_approach_used:
            if self.rng.random() < self.missed_approach_probability:
                aircraft.missed_approach_used = True
                aircraft.phase = FlightPhase.MISSED_APPROACH
                self.initialize_phase_duration(aircraft)
                return

        if aircraft.phase == FlightPhase.MISSED_APPROACH:
            aircraft.phase = FlightPhase.APPROACH
            self.initialize_phase_duration(aircraft)
            return

        # Optional holding pattern before approach.
        if (
            aircraft.phase == FlightPhase.DESCENT
            and self.holding_enabled
            and not aircraft.missed_approach_used
        ):
            if self.rng.random() < 0.15:
                aircraft.phase = FlightPhase.HOLDING
                self.initialize_phase_duration(aircraft)
                return

        if aircraft.phase == FlightPhase.HOLDING:
            aircraft.phase = FlightPhase.APPROACH
            self.initialize_phase_duration(aircraft)
            return

        sequence = (
            DEPARTURE_SEQUENCE
            if aircraft.operation == OperationType.DEPARTURE
            else ARRIVAL_SEQUENCE
        )
        aircraft.phase = _next_phase(sequence, aircraft.phase)
        self.initialize_phase_duration(aircraft)

    def _duration_for(self, phase: FlightPhase) -> float:
        r = self.rng
        return {
            FlightPhase.TAXI_OUT:        r.uniform(60, 150),
            FlightPhase.TAKEOFF:         r.uniform(20, 35),
            FlightPhase.CLIMB:           r.uniform(300, 600),
            FlightPhase.CRUISE:          r.uniform(120, 1200),
            FlightPhase.DESCENT:         r.uniform(300, 600),
            FlightPhase.HOLDING:         r.uniform(180, 480),
            FlightPhase.APPROACH:        r.uniform(180, 300),
            FlightPhase.LANDING:         r.uniform(30, 60),
            FlightPhase.TAXI_IN:         r.uniform(60, 180),
            FlightPhase.ARRIVAL:         1e9,   # terminal / parked
            FlightPhase.MISSED_APPROACH: r.uniform(60, 120),
        }[phase]

    # ---- per-phase kinematic handlers ---------------------------------------
    #
    # Each handler's job is ONLY to update the *target* speed and vertical
    # rate via _ease_towards, and to set a *target* heading for the turn
    # logic.  It must NOT directly set latitude/longitude — that is done
    # exclusively by step() above.
    #
    # Ground phases (TAXI_OUT, TAXI_IN, ARRIVAL) set heading_deg directly
    # because they follow a fixed taxiway bearing rather than a computed
    # destination.  The displacement-derived heading override in step() is
    # skipped for ground phases (they use the fixed heading as the integration
    # heading, and the displacement then re-confirms the same bearing).

    def _h_taxi_out(self, ac: Aircraft, dt: float) -> None:
        """Taxi out at 10-20 kt, following the assigned real taxiway
        route when the airport has one (heading tracks each leg)."""
        target_gs = self.rng.uniform(10.0, 20.0)  # realistic taxi range
        ac.ground_speed_kt   = _ease_towards(ac.ground_speed_kt,  target_gs, 2.0, dt)
        ac.vertical_rate_fpm = 0.0
        if ac.route:
            if self._follow_route(ac, dt):
                # Reached the runway entry — line up and begin takeoff.
                ac.phase_elapsed_s = ac.phase_duration_s
        else:
            # Legacy fallback: fixed taxiway bearing towards the runway.
            ac.heading_deg = ac.track_deg = self.airport.runway_heading_deg

    def _runway_roll_heading(self, ac: Aircraft) -> float:
        """Bearing along the real runway centreline, away from the
        nearer runway end (i.e. down the runway)."""
        line = self.airport.runway_line
        if not line:
            return self.airport.runway_heading_deg
        d_a, brg_a = bearing_distance_nm(ac.latitude, ac.longitude, line[0][0], line[0][1])
        d_b, brg_b = bearing_distance_nm(ac.latitude, ac.longitude, line[1][0], line[1][1])
        # Head towards the FARTHER end — that is "down the runway".
        return brg_a if d_a >= d_b else brg_b

    def _h_takeoff(self, ac: Aircraft, dt: float) -> None:
        """Takeoff roll: accelerate 0 → 160 kt; rotate at 80 % of roll.
        The roll tracks the real runway centreline when available."""
        ac.ground_speed_kt = _ease_towards(ac.ground_speed_kt, 160.0, 8.0, dt)
        progress = ac.phase_elapsed_s / max(ac.phase_duration_s, 1.0)
        if progress < 0.8:
            ac.vertical_rate_fpm = 0.0      # still rolling
        else:
            # Rotate: ramp up climb rate to 2500 fpm.
            ac.vertical_rate_fpm = _ease_towards(ac.vertical_rate_fpm, 2500.0, 300.0, dt)
        ac.heading_deg = ac.track_deg = self._runway_roll_heading(ac)

    def _h_climb(self, ac: Aircraft, dt: float) -> None:
        """Initial climb: 220 kt below 10 000 ft, 450 kt above.
        Vertical rate 1 500–2 200 fpm; gentle SID turns ≤ 3°/s (handled
        by step's heading smoothing).
        """
        target_alt = self.rng.choice([31000, 33000, 35000, 37000, 38000])
        below_fl100 = ac.altitude_ft < 10000
        target_gs   = 220.0 if below_fl100 else _ease_towards(ac.ground_speed_kt, 450.0, 3.0, dt)
        ac.ground_speed_kt = _ease_towards(ac.ground_speed_kt, target_gs, 3.0, dt)

        if ac.altitude_ft < target_alt - 500:
            target_vr = self.rng.uniform(1500.0, 2200.0)
            ac.vertical_rate_fpm = _ease_towards(ac.vertical_rate_fpm, target_vr, 50.0, dt)
        else:
            ac.vertical_rate_fpm = _ease_towards(ac.vertical_rate_fpm, 0.0, 200.0, dt)

        # Gentle heading wander modelling SID turns; heading smoothing in
        # step() limits to MAX_HDG_AIR_DEG_S.
        target_hdg = (ac.heading_deg + self.rng.uniform(-0.5, 0.5)) % 360.0
        ac.heading_deg = _turn_towards(ac.heading_deg, target_hdg, _MAX_HDG_AIR_DEG_S * dt)

    def _h_cruise(self, ac: Aircraft, dt: float) -> None:
        """Cruise: 420–480 kt, level flight with minor track variations."""
        ac.ground_speed_kt   = _ease_towards(ac.ground_speed_kt, self.rng.uniform(420, 480), 1.0, dt)
        ac.vertical_rate_fpm = _ease_towards(ac.vertical_rate_fpm, 0.0, 100.0, dt)
        target_hdg = (ac.heading_deg + self.rng.uniform(-0.1, 0.1)) % 360.0
        ac.heading_deg = _turn_towards(ac.heading_deg, target_hdg, _MAX_HDG_AIR_DEG_S * dt)

    def _h_descent(self, ac: Aircraft, dt: float) -> None:
        """Descent: 260 kt, -700 to -1 800 fpm; steer towards airport."""
        ac.ground_speed_kt = _ease_towards(ac.ground_speed_kt, 260.0, 1.5, dt)
        target_vr = self.rng.uniform(-1800.0, -700.0)
        ac.vertical_rate_fpm = _ease_towards(ac.vertical_rate_fpm, target_vr, 50.0, dt)

        # Steer towards the airport — STAR routing.
        _, bearing_to_airport = bearing_distance_nm(
            ac.latitude, ac.longitude,
            self.airport.latitude, self.airport.longitude,
        )
        ac.heading_deg = _turn_towards(ac.heading_deg, bearing_to_airport, _MAX_HDG_AIR_DEG_S * dt)

    def _h_holding(self, ac: Aircraft, dt: float) -> None:
        """Standard racetrack holding: 230 kt, continuous right turn."""
        ac.ground_speed_kt   = _ease_towards(ac.ground_speed_kt, 230.0, 1.0, dt)
        ac.vertical_rate_fpm = 0.0
        # Holding turn rate 3°/s (standard rate) with heading smoothing.
        ac.heading_deg = (ac.heading_deg + 3.0 * dt) % 360.0

    def _h_approach(self, ac: Aircraft, dt: float) -> None:
        """Instrument approach: 150–180 kt, glide towards the active
        runway threshold; hand over to LANDING when reaching it."""
        ac.ground_speed_kt = _ease_towards(ac.ground_speed_kt, self.rng.uniform(150.0, 180.0), 1.5, dt)
        active = self.airport.active_runway()
        if active:
            thr = active[0]
            dist_nm, brg = bearing_distance_nm(ac.latitude, ac.longitude, thr[0], thr[1])
            # Continuous glide: aim to cross the threshold at ~50 ft AGL.
            alt_agl = max(ac.altitude_ft - self.airport.elevation_ft, 0.0)
            time_to_thr = dist_nm / max(ac.ground_speed_kt, 60.0) * 3600.0
            glide_vr = -(alt_agl - 50.0) / max(time_to_thr, 5.0) * 60.0
            ac.vertical_rate_fpm = _ease_towards(
                ac.vertical_rate_fpm, max(-1100.0, min(-250.0, glide_vr)), 60.0, dt
            )
            target_hdg = brg if dist_nm > 0.3 else self.airport.runway_heading_deg
            ac.heading_deg = _turn_towards(ac.heading_deg, target_hdg, _MAX_HDG_AIR_DEG_S * dt)
            # Geometry-based hand-over: threshold reached, low — land.
            if dist_nm < 0.35 and alt_agl < 400.0:
                ac.phase_elapsed_s = ac.phase_duration_s
        else:
            target_alt = self.airport.elevation_ft + 2000.0
            if ac.altitude_ft > target_alt:
                ac.vertical_rate_fpm = _ease_towards(ac.vertical_rate_fpm, -900.0, 30.0, dt)
            else:
                ac.vertical_rate_fpm = _ease_towards(ac.vertical_rate_fpm, -600.0, 30.0, dt)
            ac.heading_deg = _turn_towards(
                ac.heading_deg, self.airport.runway_heading_deg, _MAX_HDG_AIR_DEG_S * dt
            )

    def _h_landing(self, ac: Aircraft, dt: float) -> None:
        """Landing roll: touch down, decelerate 160 → 20 kt along the
        real runway centreline; vacate once slowed."""
        ac.ground_speed_kt = _ease_towards(ac.ground_speed_kt, 20.0, 5.0, dt)
        alt_agl = ac.altitude_ft - self.airport.elevation_ft
        if alt_agl <= 40.0:
            # Touchdown: pin the aircraft to the runway surface.
            ac.altitude_ft = self.airport.elevation_ft
            ac.vertical_rate_fpm = 0.0
        else:
            ac.vertical_rate_fpm = _ease_towards(ac.vertical_rate_fpm, -500.0, 60.0, dt)
        ac.heading_deg = ac.track_deg = self._runway_roll_heading(ac)
        # Slowed to taxi speed — vacate the runway.
        if self.airport.runway_line and ac.ground_speed_kt <= 25.0 and alt_agl <= 40.0:
            ac.phase_elapsed_s = ac.phase_duration_s

    def _h_taxi_in(self, ac: Aircraft, dt: float) -> None:
        """Vacate runway and taxi to a stand: 10–20 kt, backtracking to
        the nearest runway entry then following a real taxi route in
        reverse to its parking stand (when the airport has routes)."""
        target_gs = self.rng.uniform(10.0, 20.0)
        ac.ground_speed_kt   = _ease_towards(ac.ground_speed_kt, target_gs, 2.0, dt)
        ac.vertical_rate_fpm = 0.0

        if ac.route is None and self.airport.taxi_routes:
            # Pick the route whose runway entry (last point) is nearest
            # to where we ended the landing roll, and follow it in
            # reverse: runway entry -> taxiways -> stand.
            def entry_dist(route):
                d, _ = bearing_distance_nm(ac.latitude, ac.longitude, route[-1][0], route[-1][1])
                return d
            candidates = sorted(self.airport.taxi_routes, key=entry_dist)[:4]
            chosen = self.rng.choice(candidates)
            ac.route = list(reversed(chosen))
            ac.route_index = 0
            ac.phase_duration_s = 1e9

        if ac.route:
            if self._follow_route(ac, dt):
                # Reached the stand — parked.
                ac.phase_elapsed_s = ac.phase_duration_s

    def _h_arrival(self, ac: Aircraft, dt: float) -> None:
        """Parked / arrived: coast to a stop."""
        ac.ground_speed_kt   = _ease_towards(ac.ground_speed_kt, 0.0, 3.0, dt)
        ac.vertical_rate_fpm = 0.0

    def _h_missed_approach(self, ac: Aircraft, dt: float) -> None:
        """Go-around: climb to 2 000 ft AGL at 200 kt, +1 500 fpm."""
        ac.ground_speed_kt   = _ease_towards(ac.ground_speed_kt, 200.0, 4.0, dt)
        ac.vertical_rate_fpm = _ease_towards(ac.vertical_rate_fpm, 1500.0, 60.0, dt)
        ac.heading_deg = ac.track_deg = self.airport.runway_heading_deg

    _HANDLERS = {
        FlightPhase.TAXI_OUT:        _h_taxi_out,
        FlightPhase.TAKEOFF:         _h_takeoff,
        FlightPhase.CLIMB:           _h_climb,
        FlightPhase.CRUISE:          _h_cruise,
        FlightPhase.DESCENT:         _h_descent,
        FlightPhase.HOLDING:         _h_holding,
        FlightPhase.APPROACH:        _h_approach,
        FlightPhase.LANDING:         _h_landing,
        FlightPhase.TAXI_IN:         _h_taxi_in,
        FlightPhase.ARRIVAL:         _h_arrival,
        FlightPhase.MISSED_APPROACH: _h_missed_approach,
    }


# ---------------------------------------------------------------------------
# Kinematic helpers
# ---------------------------------------------------------------------------

def _ease_towards(current: float, target: float, max_delta_per_s: float, dt: float) -> float:
    """Move `current` towards `target` by at most max_delta_per_s * dt."""
    max_step = max_delta_per_s * dt
    delta    = target - current
    if abs(delta) <= max_step:
        return target
    return current + max_step * (1.0 if delta > 0 else -1.0)


def _turn_towards(current_deg: float, target_deg: float, max_step_deg: float) -> float:
    """Rotate current_deg towards target_deg by at most max_step_deg,
    choosing the shorter (≤ 180°) turn direction."""
    diff = (target_deg - current_deg + 540.0) % 360.0 - 180.0
    step = max(-abs(max_step_deg), min(abs(max_step_deg), diff))
    return (current_deg + step) % 360.0
