"""ASTERIX PCAP Generator for Indonesian Airports.

Orchestrates the simulation engine, the per-category ASTERIX encoders
and the PCAP writer to produce one .pcap file per (airport, category)
combination, replicating the structure, transport and update rates of
the real Juanda (WARR) surveillance feed:

    MLAT stream  (CAT019 + CAT020): 192.168.30.102:55031 -> 239.200.2.2:8600
    ADS-B stream (CAT021 + CAT023 + CAT247): 192.168.30.92:44180 -> 239.200.2.2:8700

Usage (defaults generate all 5 airports x 5 categories, 20 aircraft,
5 minutes each):

    python main.py
    python main.py --airports WIII WARR --categories cat021 cat020 \
        --aircraft 50 --duration 600 --seed 7
"""
from __future__ import annotations

import argparse
import datetime
import os
import time
from typing import Dict, Optional

from config import ALL_AIRPORTS, ALL_CATEGORIES, SELECTABLE_CATEGORIES, SimulationSettings
from encoder.cat019 import encode_cat019_record
from encoder.cat020 import encode_cat020_record
from encoder.cat021 import encode_cat021_record
from encoder.cat023 import (
    encode_cat023_ground_station_status,
    encode_cat023_service_status,
)
from encoder.cat034 import encode_cat034_record
from encoder.cat048 import encode_cat048_record
from encoder.cat062 import encode_cat062_record
from encoder.cat063 import encode_cat063_record
from encoder.cat247 import encode_cat247_record
from encoder.common import bearing_distance_nm, destination_point
from pcap.writer import AsterixPcapSession, CombinedPcapSession, NetworkConfig
from simulation.aircraft import Aircraft
from simulation.airport import Airport, get_airport
from simulation.scenario import Scenario, ScenarioConfig

# Transport parameters captured from the real Juanda feed.
ADSB_NETWORK = NetworkConfig(
    src_ip="192.168.30.92", dst_ip="239.200.2.2",
    src_port=44180, dst_port=8700,
    src_mac="08:cc:a7:e7:9f:42", dst_mac="01:00:5e:48:02:02",
    ttl=63, tos=0x78,
)
MLAT_NETWORK = NetworkConfig(
    src_ip="192.168.30.102", dst_ip="239.200.2.2",
    src_port=55031, dst_port=8600,
    src_mac="08:cc:a7:e7:9f:42", dst_mac="01:00:5e:48:02:02",
    ttl=7, tos=0x00,
)

ADSB_CATEGORIES = {"cat021", "cat023", "cat247"}
MLAT_CATEGORIES = {"cat019", "cat020"}


def _epoch_seconds_for_midnight(start_time: datetime.time) -> int:
    """Compute a Unix epoch timestamp for "today at start_time UTC", so
    PCAP packet timestamps look like a real capture."""
    today = datetime.datetime.now(datetime.timezone.utc).date()
    dt = datetime.datetime.combine(today, start_time, tzinfo=datetime.timezone.utc)
    return int(dt.timestamp())


def _interpolate(prev: Aircraft, cur: Aircraft, f: float) -> Aircraft:
    """Return a snapshot at fraction `f` (0..1) of the second between two
    consecutive simulation states. Sub-second reports interpolate along
    the path the aircraft ACTUALLY flew (as a real 2 Hz ADS-B feed
    observes) — extrapolating ahead instead would overshoot every turn
    and make tracks wobble."""
    snap = prev.snapshot()
    snap.latitude = prev.latitude + (cur.latitude - prev.latitude) * f
    snap.longitude = prev.longitude + (cur.longitude - prev.longitude) * f
    snap.altitude_ft = prev.altitude_ft + (cur.altitude_ft - prev.altitude_ft) * f
    snap.flight_level = snap.altitude_ft / 100.0
    snap.timestamp_seconds = prev.timestamp_seconds + f
    return snap


def _jitter(aircraft: Aircraft, tick: int, salt: int, spread: float) -> float:
    """Deterministic per-aircraft/per-tick sub-second jitter."""
    h = (int(aircraft.icao_address, 16) * 2654435761 + tick * 40503 + salt * 69621) & 0xFFFFFFFF
    return (h % 1000) / 1000.0 * spread


def generate_airport(icao: str, settings: SimulationSettings) -> Dict[str, str]:
    """Run one airport's scenario and write out every requested
    category's PCAP file. Returns a dict of category -> file path."""
    airport = get_airport(icao)
    airport_dir = os.path.join(settings.output_dir, icao)
    os.makedirs(airport_dir, exist_ok=True)

    scenario = Scenario(
        ScenarioConfig(
            airport=airport,
            num_aircraft=settings.num_aircraft,
            duration_s=settings.duration_s,
            departure_ratio=settings.departure_ratio,
            simulation_radius_nm=settings.simulation_radius_nm,
            seed=settings.seed,
            holding_enabled=settings.holding_enabled,
            missed_approach_enabled=settings.missed_approach_enabled,
            start_time=settings.start_time,
        )
    )

    base_epoch = _epoch_seconds_for_midnight(settings.start_time)
    combined = CombinedPcapSession(os.path.join(airport_dir, "combined.pcap"), base_epoch)

    class _Session:
        """Writes to the per-category file AND the airport-wide
        chronological combined.pcap (single replay timeline)."""

        def __init__(self, path: str, network: NetworkConfig):
            self._inner = AsterixPcapSession(path, network, base_epoch)
            self._network = network

        def add_message(self, payload: bytes, elapsed: float) -> None:
            self._inner.add_message(payload, elapsed)
            combined.add_message(payload, elapsed, self._network)

        def close(self) -> None:
            self._inner.close()

    sessions: Dict[str, _Session] = {}
    paths: Dict[str, str] = {}
    for category in settings.categories:
        if settings.network is not None:
            network = settings.network
        elif category in MLAT_CATEGORIES:
            network = MLAT_NETWORK
        else:
            network = ADSB_NETWORK
        path = os.path.join(airport_dir, f"{category}.pcap")
        sessions[category] = _Session(path, network)
        paths[category] = path
    paths["combined"] = os.path.join(airport_dir, "combined.pcap")

    midnight_offset = settings.start_time.hour * 3600 + settings.start_time.minute * 60
    next_legacy_tick = 0.0
    next_cat247_tick = 0.0

    def emit_target_reports(prev: Aircraft, cur: Aircraft, base_elapsed: float) -> None:
        """Emit one second of target reports for the interval between
        two consecutive simulation states, interpolating sub-second
        positions along the actually-flown path."""
        tick = int(base_elapsed)
        icao24 = int(prev.icao_address, 16)

        if "cat021" in sessions:
            # Real feed: two position reports per second per target;
            # "selected-altitude equipped" airframes alternate the long
            # variant; ground targets add a 5-second ident beacon.
            for slot, base_offset in enumerate((0.0, 0.5)):
                offset = base_offset + _jitter(prev, tick, slot, 0.35)
                snap = _interpolate(prev, cur, offset)
                if prev.on_ground:
                    variant = "ground"
                    if slot == 0 and tick % 5 == icao24 % 5:
                        variant = "ground_ident"
                else:
                    sel_equipped = icao24 % 5 != 0
                    variant = "airborne_sel" if (sel_equipped and slot == 1) else "airborne"
                rec = encode_cat021_record(snap, airport.sac_adsb, airport.sic_adsb, variant=variant)
                sessions["cat021"].add_message(rec, base_elapsed + offset)

        if "cat020" in sessions:
            # MLAT covers the airport surface and the immediate
            # approach; one report per second per target inside
            # coverage, in a per-target time slot.
            dist_nm, _ = bearing_distance_nm(
                airport.latitude, airport.longitude,
                prev.latitude, prev.longitude,
            )
            if dist_nm <= settings.mlat_coverage_nm and prev.altitude_ft < 10000.0:
                offset = (icao24 % 5) * 0.2 + _jitter(prev, tick, 7, 0.15)
                snap = _interpolate(prev, cur, offset)
                if prev.on_ground:
                    # A few airframes have no downlinked callsign yet.
                    variant = "ground_noid" if icao24 % 4 == 0 else "ground"
                else:
                    variant = "airborne"
                rec = encode_cat020_record(
                    snap, airport.sac_mlat, airport.sic_mlat,
                    airport.latitude, airport.longitude, variant=variant,
                )
                sessions["cat020"].add_message(rec, base_elapsed + offset)

        if "cat048" in sessions:
            rec = encode_cat048_record(
                prev, airport.sac, airport.sic_radar, airport.latitude, airport.longitude
            )
            sessions["cat048"].add_message(rec, base_elapsed)
        if "cat062" in sessions:
            rec = encode_cat062_record(prev, airport.sac, airport.sic_tracker)
            sessions["cat062"].add_message(rec, base_elapsed)

    prev_states: Dict[str, Aircraft] = {}
    last_elapsed = 0.0

    try:
        for elapsed, active_aircraft in scenario.run():
            last_elapsed = elapsed
            current = {ac.icao_address: ac for ac in active_aircraft}

            # --- aircraft-driven target report categories ---
            # Reports for second N are emitted once second N+1 is known,
            # so sub-second positions interpolate the real flown path.
            for icao, prev in prev_states.items():
                cur = current.get(icao, prev)
                emit_target_reports(prev, cur, elapsed - 1.0)
            prev_states = current

            tod = midnight_offset + elapsed

            # --- periodic system status categories (real feed rates) ---
            if "cat019" in sessions:  # 1 Hz MLAT system status
                sessions["cat019"].add_message(
                    encode_cat019_record(
                        airport.sac_mlat, airport.sic_mlat, tod,
                        airport.latitude, airport.longitude,
                    ),
                    elapsed + 0.85,
                )

            if "cat023" in sessions and elapsed % settings.cat023_interval_s < 1.0:
                sessions["cat023"].add_message(
                    encode_cat023_ground_station_status(airport.sac_adsb, airport.sic_adsb, tod),
                    elapsed,
                )
                sessions["cat023"].add_message(
                    encode_cat023_service_status(airport.sac_adsb, airport.sic_adsb, tod),
                    elapsed + 0.02,
                )

            if "cat247" in sessions and elapsed >= next_cat247_tick:
                sessions["cat247"].add_message(
                    encode_cat247_record(airport.sac_adsb, airport.sic_adsb, tod),
                    elapsed,
                )
                next_cat247_tick = elapsed + settings.cat247_interval_s

            # --- legacy radar/tracker status categories ---
            if elapsed >= next_legacy_tick:
                sector_deg = (elapsed * 90.0) % 360.0  # simulate a rotating radar antenna
                if "cat034" in sessions:
                    rec = encode_cat034_record(
                        airport.sac, airport.sic_radar, tod,
                        sector_deg=sector_deg, rotation_period_s=4.0, operational=True,
                    )
                    sessions["cat034"].add_message(rec, elapsed)
                if "cat063" in sessions:
                    rec = encode_cat063_record(
                        airport.sac, airport.sic_tracker, tod,
                        sensor_sac=airport.sac, sensor_sic=airport.sic_radar, service_id=1,
                    )
                    sessions["cat063"].add_message(rec, elapsed)
                next_legacy_tick = elapsed + settings.ground_status_interval_s
    finally:
        # Flush the final simulation second (no successor state to
        # interpolate towards — emit it as-is).
        for prev in prev_states.values():
            emit_target_reports(prev, prev, last_elapsed)
        for session in sessions.values():
            session.close()
        combined.close()

    return paths


def generate_all(settings: SimulationSettings) -> Dict[str, Dict[str, str]]:
    results: Dict[str, Dict[str, str]] = {}
    for icao in settings.airports:
        results[icao] = generate_airport(icao, settings)
    return results


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ASTERIX PCAP Generator for Indonesian Airports")
    parser.add_argument("--airports", nargs="+", default=ALL_AIRPORTS, choices=ALL_AIRPORTS)
    parser.add_argument("--categories", nargs="+", default=ALL_CATEGORIES, choices=SELECTABLE_CATEGORIES)
    parser.add_argument("--aircraft", type=int, default=20, help="Number of simulated aircraft per airport")
    parser.add_argument("--duration", type=int, default=300, help="Simulation duration in seconds")
    parser.add_argument("--departure-ratio", type=float, default=0.5)
    parser.add_argument(
        "--radius-nm", type=float, default=30.0,
        help="Arrivals spawn within this radius (NM) of the selected airport; departures start at the airport",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output-dir", default="output")
    parser.add_argument("--src-ip", default=None, help="Override source IP for all streams")
    parser.add_argument("--dst-ip", default=None, help="Override destination IP for all streams")
    parser.add_argument("--src-port", type=int, default=None, help="Override source port for all streams")
    parser.add_argument("--dst-port", type=int, default=None, help="Override destination port for all streams")
    parser.add_argument("--no-holding", action="store_true")
    parser.add_argument("--no-missed-approach", action="store_true")
    return parser


def main() -> None:
    args = _build_arg_parser().parse_args()

    network: Optional[NetworkConfig] = None
    if any(v is not None for v in (args.src_ip, args.dst_ip, args.src_port, args.dst_port)):
        network = NetworkConfig(
            src_ip=args.src_ip or ADSB_NETWORK.src_ip,
            dst_ip=args.dst_ip or ADSB_NETWORK.dst_ip,
            src_port=args.src_port or ADSB_NETWORK.src_port,
            dst_port=args.dst_port or ADSB_NETWORK.dst_port,
        )

    settings = SimulationSettings(
        airports=args.airports,
        categories=args.categories,
        num_aircraft=args.aircraft,
        duration_s=args.duration,
        departure_ratio=args.departure_ratio,
        simulation_radius_nm=args.radius_nm,
        seed=args.seed,
        output_dir=args.output_dir,
        network=network,
        holding_enabled=not args.no_holding,
        missed_approach_enabled=not args.no_missed_approach,
    )

    start = time.time()
    results = generate_all(settings)
    elapsed = time.time() - start

    total_files = sum(len(cats) for cats in results.values())
    print(f"Generated {total_files} PCAP file(s) for {len(results)} airport(s) in {elapsed:.2f}s")
    for icao, cats in results.items():
        print(f"  {icao}:")
        for cat, path in cats.items():
            size = os.path.getsize(path)
            print(f"    {cat}: {path} ({size} bytes)")


if __name__ == "__main__":
    main()
