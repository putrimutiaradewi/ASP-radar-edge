import random
import unittest

from simulation.aircraft import Aircraft, FlightPhase, OperationType
from simulation.airport import get_airport
from simulation.movement import MovementModel
from simulation.scenario import Scenario, ScenarioConfig


class TestMovementModel(unittest.TestCase):
    def setUp(self):
        self.airport = get_airport("WIII")
        self.rng = random.Random(1234)
        self.model = MovementModel(airport=self.airport, rng=self.rng)

    def _departure_aircraft(self) -> Aircraft:
        ac = Aircraft(
            icao_address="8A0001",
            callsign="GIA100",
            airline="Garuda Indonesia",
            operation=OperationType.DEPARTURE,
            track_number=1,
            latitude=self.airport.latitude,
            longitude=self.airport.longitude,
            altitude_ft=0.0,
            ground_speed_kt=0.0,
            track_deg=self.airport.runway_heading_deg,
            heading_deg=self.airport.runway_heading_deg,
            vertical_rate_fpm=0.0,
            squawk="1000",
            flight_level=0.0,
            timestamp_seconds=0.0,
            phase=FlightPhase.TAXI_OUT,
            on_ground=True,
        )
        self.model.initialize_phase_duration(ac)
        return ac

    def test_taxi_out_has_zero_altitude_and_low_speed(self):
        ac = self._departure_aircraft()
        self.model.step(ac, dt=1.0)
        self.assertEqual(ac.altitude_ft, 0.0)
        self.assertLess(ac.ground_speed_kt, 20.0)
        self.assertTrue(ac.on_ground)

    def test_departure_eventually_climbs(self):
        ac = self._departure_aircraft()
        max_altitude = 0.0
        for _ in range(1800):  # 30 simulated minutes
            self.model.step(ac, dt=1.0)
            max_altitude = max(max_altitude, ac.altitude_ft)
        self.assertGreater(max_altitude, 10000.0)

    def test_departure_phase_sequence_progresses(self):
        ac = self._departure_aircraft()
        seen_phases = {ac.phase}
        for _ in range(1800):
            self.model.step(ac, dt=1.0)
            seen_phases.add(ac.phase)
        self.assertIn(FlightPhase.TAKEOFF, seen_phases)
        self.assertIn(FlightPhase.CLIMB, seen_phases)

    def test_timestamp_advances_monotonically(self):
        ac = self._departure_aircraft()
        prev = ac.timestamp_seconds
        for _ in range(10):
            self.model.step(ac, dt=1.0)
            self.assertGreater(ac.timestamp_seconds, prev)
            prev = ac.timestamp_seconds

    def test_position_changes_when_moving(self):
        ac = self._departure_aircraft()
        for _ in range(200):  # get it moving down the runway / airborne
            self.model.step(ac, dt=1.0)
        self.assertNotEqual((ac.latitude, ac.longitude), (self.airport.latitude, self.airport.longitude))


class TestScenario(unittest.TestCase):
    def test_scenario_generates_requested_aircraft_count(self):
        airport = get_airport("WADD")
        scenario = Scenario(ScenarioConfig(airport=airport, num_aircraft=10, duration_s=5, seed=99))
        self.assertEqual(len(scenario.aircraft), 10)

    def test_scenario_run_yields_duration_ticks(self):
        airport = get_airport("WARR")
        scenario = Scenario(ScenarioConfig(airport=airport, num_aircraft=3, duration_s=15, seed=1))
        ticks = list(scenario.run())
        self.assertEqual(len(ticks), 15)

    def test_unique_icao_addresses(self):
        airport = get_airport("WAHI")
        scenario = Scenario(ScenarioConfig(airport=airport, num_aircraft=25, duration_s=2, seed=5))
        addresses = [ac.icao_address for ac in scenario.aircraft]
        self.assertEqual(len(addresses), len(set(addresses)))

    def test_seeded_scenarios_are_reproducible(self):
        airport = get_airport("WAHS")
        s1 = Scenario(ScenarioConfig(airport=airport, num_aircraft=5, duration_s=10, seed=777))
        s2 = Scenario(ScenarioConfig(airport=airport, num_aircraft=5, duration_s=10, seed=777))
        callsigns1 = [ac.callsign for ac in s1.aircraft]
        callsigns2 = [ac.callsign for ac in s2.aircraft]
        self.assertEqual(callsigns1, callsigns2)


if __name__ == "__main__":
    unittest.main()
