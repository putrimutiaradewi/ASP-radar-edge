"""Top level, user-facing simulation configuration."""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import List, Optional

from pcap.writer import NetworkConfig

ALL_AIRPORTS = ["WIII", "WARR", "WAHS", "WAHI", "WADD"]
# Categories present in the real Juanda surveillance feed (the default
# generation profile). The legacy categories remain selectable.
ALL_CATEGORIES = ["cat019", "cat020", "cat021", "cat023", "cat247"]
LEGACY_CATEGORIES = ["cat034", "cat048", "cat062", "cat063"]
SELECTABLE_CATEGORIES = ALL_CATEGORIES + LEGACY_CATEGORIES


@dataclass
class SimulationSettings:
    """User-configurable knobs for a generation run.

    Attributes:
        airports: list of ICAO codes to generate scenarios for.
        categories: list of category module names (e.g. "cat048") to
            generate; defaults to all supported categories.
        num_aircraft: number of simulated aircraft per airport.
        duration_s: scenario length in seconds (e.g. 60, 300, 600, 1800).
        departure_ratio: fraction of aircraft that are departures
            (rest are arrivals).
        simulation_radius_nm: arrivals spawn within this radius (NM) of
            the selected airport's lat/lon — never at a hardcoded or
            global coordinate. Departures always start at the airport.
        seed: optional RNG seed for reproducible scenarios.
        output_dir: root directory PCAPs are written under
            (output/<ICAO>/<category>.pcap).
        network: source/destination IP/port/MAC configuration shared by
            all generated packets.
        ground_status_interval_s: how often (seconds) CAT023/034/063
            ground/sensor status messages are emitted, independent of
            aircraft activity.
        holding_enabled / missed_approach_enabled: toggle optional
            flight-phase behaviours.
    """

    airports: List[str] = field(default_factory=lambda: list(ALL_AIRPORTS))
    categories: List[str] = field(default_factory=lambda: list(ALL_CATEGORIES))
    num_aircraft: int = 20
    duration_s: int = 300
    departure_ratio: float = 0.5
    # Arrivals spawn within this radius of the airport (NM); departures
    # always start AT the airport (0 NM). Never a hardcoded/global distance —
    # everything is relative to the selected airport's lat/lon.
    simulation_radius_nm: float = 30.0
    seed: Optional[int] = 42
    output_dir: str = "output"
    # When None, each stream uses the transport captured from the real
    # feed (ADS-B: 192.168.30.92:44180 -> 239.200.2.2:8700, MLAT:
    # 192.168.30.102:55031 -> 239.200.2.2:8600). Setting an explicit
    # NetworkConfig overrides both streams.
    network: Optional[NetworkConfig] = None
    ground_status_interval_s: int = 10  # legacy cat034/063 interval
    cat023_interval_s: float = 2.0      # real feed: each variant every 2 s
    cat247_interval_s: float = 60.0     # real feed: every 60 s
    mlat_coverage_nm: float = 15.0      # CAT020 emitted within this range
    holding_enabled: bool = True
    missed_approach_enabled: bool = True
    start_time: datetime.time = field(default_factory=lambda: datetime.time(2, 0, 0))
