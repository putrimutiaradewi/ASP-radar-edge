"""Indonesian airport reference data used as the geographic origin for
each simulated scenario (gate/runway location, radar/MLAT/ADS-B ground
station site, etc.)."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

LatLon = Tuple[float, float]


class SpawnRole(str, Enum):
    """Semantic category for an aircraft spawn point."""
    PARKING     = "parking"     # gate / stand
    APRON       = "apron"       # apron taxiway
    TAXIWAY     = "taxiway"     # pre-departure hold / intersection
    RWY_THRESHOLD = "rwy_threshold"  # beginning of takeoff roll
    FINAL_APPROACH = "final_approach"  # ILS intercept (~8 NM final)
    HOLDING_FIX  = "holding_fix"      # holding stack fix
    STAR_ENTRY   = "star_entry"       # STAR / arrival fix (cruise level)


@dataclass(frozen=True)
class SpawnPoint:
    """A named spawn location on or near an airport."""
    name: str
    role: SpawnRole
    latitude: float
    longitude: float
    # Heading the aircraft should face when it first appears (deg true).
    # For airborne fixes this is the initial track toward the airport.
    heading_deg: float


@dataclass(frozen=True)
class Airport:
    """A simulated airport / surveillance site."""

    icao: str
    name: str
    latitude: float
    longitude: float
    elevation_ft: float = 0.0
    runway_heading_deg: float = 250.0  # approximate main runway heading

    # Data source identifiers (SAC/SIC) used by the surveillance
    # sensors located at / serving this airport. Defaults replicate the
    # real Juanda feed: ADS-B ground station reports as SAC 0 / SIC 181
    # (0xB5), the MLAT system as SAC 1 / SIC 1.
    sac: int = 202  # legacy SAC used by the cat034/048/062/063 encoders
    sac_adsb: int = 0
    sic_adsb: int = 181
    sac_mlat: int = 1
    sic_mlat: int = 1
    sic_radar: int = 3
    sic_tracker: int = 4

    # Named spawn points around / on the airport.
    spawn_points: List[SpawnPoint] = field(default_factory=list)

    # Real ground geometry extracted from OpenStreetMap (the same data
    # the map UI renders), so ground tracks stay on actual pavement:
    #   runway_line: the two runway endpoint coordinates.
    #   taxi_routes: polylines from a parking stand (first point, on a
    #     real apron) along real taxiways to a runway entry (last point,
    #     a runway endpoint). Departures follow them forwards; arrivals
    #     follow a reversed route back to a stand.
    # When None/empty the movement model falls back to the legacy
    # straight-line taxi behaviour.
    runway_line: Optional[Tuple[LatLon, LatLon]] = None
    taxi_routes: List[List[LatLon]] = field(default_factory=list)

    def active_runway(self) -> Optional[Tuple[LatLon, LatLon]]:
        """(threshold, far_end) for the active landing/takeoff direction:
        the runway end from which the runway bearing best matches
        runway_heading_deg."""
        if not self.runway_line:
            return None
        a, b = self.runway_line
        dy = math.radians(b[0] - a[0])
        dx = math.radians(b[1] - a[1]) * math.cos(math.radians((a[0] + b[0]) / 2))
        brg_ab = (math.degrees(math.atan2(dx, dy)) + 360.0) % 360.0
        diff = abs((brg_ab - self.runway_heading_deg + 180.0) % 360.0 - 180.0)
        return (a, b) if diff <= 90.0 else (b, a)


# ---------------------------------------------------------------------------
# Helper: compute a lat/lon offset for a spawn point
# ---------------------------------------------------------------------------

_R_NM = 3440.065


def _offset(lat: float, lon: float, bearing_deg: float, dist_nm: float) -> tuple[float, float]:
    """Destination lat/lon given a start, bearing and distance."""
    delta = dist_nm / _R_NM
    theta = math.radians(bearing_deg)
    phi1  = math.radians(lat)
    lam1  = math.radians(lon)
    phi2  = math.asin(math.sin(phi1) * math.cos(delta)
                      + math.cos(phi1) * math.sin(delta) * math.cos(theta))
    lam2  = lam1 + math.atan2(
        math.sin(theta) * math.sin(delta) * math.cos(phi1),
        math.cos(delta) - math.sin(phi1) * math.sin(phi2),
    )
    return math.degrees(phi2), (math.degrees(lam2) + 540.0) % 360.0 - 180.0


# ---------------------------------------------------------------------------
# WIII — Soekarno-Hatta International Airport
#   Runway 07L/25R:  heading ~250°  (primary direction)
#   Runway 07R/25L:  heading ~250°
#   ILS 25R final course: inbound ~070°, so aircraft face ~070° on final
# ---------------------------------------------------------------------------

def _wiii_spawns() -> List[SpawnPoint]:
    lat, lon, rwy = -6.1256, 106.6559, 250.0
    ilshdg = (rwy + 180.0) % 360.0        # 070° — facing the runway
    star_hdg = (rwy + 180.0) % 360.0      # inbound toward airport

    p, t, s = SpawnRole.PARKING, SpawnRole.RWY_THRESHOLD, SpawnRole.STAR_ENTRY
    fa, hf, ap = SpawnRole.FINAL_APPROACH, SpawnRole.HOLDING_FIX, SpawnRole.APRON
    tw = SpawnRole.TAXIWAY

    return [
        SpawnPoint("WIII-STAND-A1",   p,  -6.1270, 106.6520, rwy),
        SpawnPoint("WIII-STAND-B3",   p,  -6.1248, 106.6499, rwy),
        SpawnPoint("WIII-APRON-E",    ap, -6.1280, 106.6505, rwy),
        SpawnPoint("WIII-TWY-A",      tw, -6.1261, 106.6540, rwy),
        # Runway thresholds (07L and 25R)
        SpawnPoint("WIII-THR-07L",    t,  *_offset(lat, lon, 70.0,  1.3), rwy),
        SpawnPoint("WIII-THR-25R",    t,  *_offset(lat, lon, 250.0, 1.3), (rwy+180)%360),
        # Final approach fix on ILS 25R centreline, ~8 NM out
        SpawnPoint("WIII-FAF-25R",    fa, *_offset(lat, lon, ilshdg, 8.5), ilshdg),
        # Holding fix (PARDI hold, ~20 NM NE)
        SpawnPoint("WIII-HOLD-PARDI", hf, *_offset(lat, lon, 60.0, 20.0), star_hdg),
        # STAR entry fixes
        SpawnPoint("WIII-STAR-N",     s,  *_offset(lat, lon, 0.0,  35.0), 180.0),
        SpawnPoint("WIII-STAR-E",     s,  *_offset(lat, lon, 80.0, 40.0), 260.0),
        SpawnPoint("WIII-STAR-S",     s,  *_offset(lat, lon, 175.0,38.0), 355.0),
    ]


# ---------------------------------------------------------------------------
# WARR — Juanda International Airport, Surabaya
#   Runway 10/28:  primary heading ~100°
# ---------------------------------------------------------------------------

def _warr_spawns() -> List[SpawnPoint]:
    lat, lon, rwy = -7.3798, 112.7869, 100.0
    ilshdg = (rwy + 180.0) % 360.0   # 280° — aircraft facing runway on final
    star_hdg = ilshdg

    p, t, s = SpawnRole.PARKING, SpawnRole.RWY_THRESHOLD, SpawnRole.STAR_ENTRY
    fa, hf, ap = SpawnRole.FINAL_APPROACH, SpawnRole.HOLDING_FIX, SpawnRole.APRON
    tw = SpawnRole.TAXIWAY

    return [
        SpawnPoint("WARR-STAND-A",    p,  -7.3815, 112.7830, rwy),
        SpawnPoint("WARR-STAND-B",    p,  -7.3800, 112.7845, rwy),
        SpawnPoint("WARR-APRON",      ap, -7.3820, 112.7820, rwy),
        SpawnPoint("WARR-TWY-B",      tw, -7.3808, 112.7860, rwy),
        SpawnPoint("WARR-THR-10",     t,  *_offset(lat, lon, 100.0, 1.2), rwy),
        SpawnPoint("WARR-THR-28",     t,  *_offset(lat, lon, 280.0, 1.2), (rwy+180)%360),
        SpawnPoint("WARR-FAF-28",     fa, *_offset(lat, lon, ilshdg, 8.0), ilshdg),
        SpawnPoint("WARR-HOLD-SULUR", hf, *_offset(lat, lon, 330.0, 18.0), star_hdg),
        SpawnPoint("WARR-STAR-W",     s,  *_offset(lat, lon, 280.0, 40.0), 100.0),
        SpawnPoint("WARR-STAR-N",     s,  *_offset(lat, lon, 10.0,  35.0), 190.0),
        SpawnPoint("WARR-STAR-SE",    s,  *_offset(lat, lon, 130.0, 38.0), 310.0),
    ]


# ---------------------------------------------------------------------------
# WAHS — Jenderal Ahmad Yani International Airport, Semarang
#   Runway 13/31:  primary heading ~130°
# ---------------------------------------------------------------------------

def _wahs_spawns() -> List[SpawnPoint]:
    lat, lon, rwy = -6.9727, 110.3751, 130.0
    ilshdg = (rwy + 180.0) % 360.0   # 310°

    p, t, s = SpawnRole.PARKING, SpawnRole.RWY_THRESHOLD, SpawnRole.STAR_ENTRY
    fa, hf, ap = SpawnRole.FINAL_APPROACH, SpawnRole.HOLDING_FIX, SpawnRole.APRON
    tw = SpawnRole.TAXIWAY

    return [
        SpawnPoint("WAHS-STAND-A",    p,  -6.9740, 110.3730, rwy),
        SpawnPoint("WAHS-STAND-B",    p,  -6.9735, 110.3745, rwy),
        SpawnPoint("WAHS-APRON",      ap, -6.9745, 110.3720, rwy),
        SpawnPoint("WAHS-TWY-A",      tw, -6.9730, 110.3758, rwy),
        SpawnPoint("WAHS-THR-13",     t,  *_offset(lat, lon, 130.0, 1.1), rwy),
        SpawnPoint("WAHS-THR-31",     t,  *_offset(lat, lon, 310.0, 1.1), (rwy+180)%360),
        SpawnPoint("WAHS-FAF-31",     fa, *_offset(lat, lon, ilshdg, 8.0), ilshdg),
        SpawnPoint("WAHS-HOLD-BENGA", hf, *_offset(lat, lon, 350.0, 16.0), 170.0),
        SpawnPoint("WAHS-STAR-W",     s,  *_offset(lat, lon, 275.0, 38.0), 95.0),
        SpawnPoint("WAHS-STAR-N",     s,  *_offset(lat, lon, 355.0, 35.0), 175.0),
        SpawnPoint("WAHS-STAR-E",     s,  *_offset(lat, lon, 85.0,  36.0), 265.0),
    ]


# ---------------------------------------------------------------------------
# WAHI — Yogyakarta International Airport
#   Runway 08/26 (new airport):  primary heading ~080°
# ---------------------------------------------------------------------------

def _wahi_spawns() -> List[SpawnPoint]:
    lat, lon, rwy = -7.9053, 110.0575, 80.0
    ilshdg = (rwy + 180.0) % 360.0   # 260°

    p, t, s = SpawnRole.PARKING, SpawnRole.RWY_THRESHOLD, SpawnRole.STAR_ENTRY
    fa, hf, ap = SpawnRole.FINAL_APPROACH, SpawnRole.HOLDING_FIX, SpawnRole.APRON
    tw = SpawnRole.TAXIWAY

    return [
        SpawnPoint("WAHI-STAND-A",    p,  -7.9062, 110.0545, rwy),
        SpawnPoint("WAHI-STAND-B",    p,  -7.9058, 110.0560, rwy),
        SpawnPoint("WAHI-APRON",      ap, -7.9065, 110.0540, rwy),
        SpawnPoint("WAHI-TWY-A",      tw, -7.9055, 110.0570, rwy),
        SpawnPoint("WAHI-THR-08",     t,  *_offset(lat, lon, 80.0,  1.0), rwy),
        SpawnPoint("WAHI-THR-26",     t,  *_offset(lat, lon, 260.0, 1.0), (rwy+180)%360),
        SpawnPoint("WAHI-FAF-26",     fa, *_offset(lat, lon, ilshdg, 7.5), ilshdg),
        SpawnPoint("WAHI-HOLD-JOGJA", hf, *_offset(lat, lon, 200.0, 15.0), 20.0),
        SpawnPoint("WAHI-STAR-W",     s,  *_offset(lat, lon, 270.0, 36.0), 90.0),
        SpawnPoint("WAHI-STAR-S",     s,  *_offset(lat, lon, 190.0, 35.0), 10.0),
        SpawnPoint("WAHI-STAR-E",     s,  *_offset(lat, lon, 80.0,  34.0), 260.0),
    ]


# ---------------------------------------------------------------------------
# WADD — I Gusti Ngurah Rai International Airport, Bali
#   Runway 09/27:  primary heading ~090°
# ---------------------------------------------------------------------------

def _wadd_spawns() -> List[SpawnPoint]:
    lat, lon, rwy = -8.7482, 115.1670, 90.0
    ilshdg = (rwy + 180.0) % 360.0   # 270°

    p, t, s = SpawnRole.PARKING, SpawnRole.RWY_THRESHOLD, SpawnRole.STAR_ENTRY
    fa, hf, ap = SpawnRole.FINAL_APPROACH, SpawnRole.HOLDING_FIX, SpawnRole.APRON
    tw = SpawnRole.TAXIWAY

    return [
        SpawnPoint("WADD-STAND-A",    p,  -8.7490, 115.1630, rwy),
        SpawnPoint("WADD-STAND-B",    p,  -8.7485, 115.1645, rwy),
        SpawnPoint("WADD-APRON",      ap, -8.7495, 115.1620, rwy),
        SpawnPoint("WADD-TWY-A",      tw, -8.7483, 115.1660, rwy),
        SpawnPoint("WADD-THR-09",     t,  *_offset(lat, lon, 90.0,  1.2), rwy),
        SpawnPoint("WADD-THR-27",     t,  *_offset(lat, lon, 270.0, 1.2), (rwy+180)%360),
        SpawnPoint("WADD-FAF-27",     fa, *_offset(lat, lon, ilshdg, 8.0), ilshdg),
        SpawnPoint("WADD-HOLD-TUBAN", hf, *_offset(lat, lon, 310.0, 15.0), 130.0),
        SpawnPoint("WADD-STAR-N",     s,  *_offset(lat, lon, 355.0, 38.0), 175.0),
        SpawnPoint("WADD-STAR-W",     s,  *_offset(lat, lon, 270.0, 40.0), 90.0),
        SpawnPoint("WADD-STAR-E",     s,  *_offset(lat, lon, 85.0,  36.0), 265.0),
    ]


# ---------------------------------------------------------------------------
# Real ground geometry (extracted from OpenStreetMap aeroway data).
# Runway lines are the actual runway endpoints; taxi routes run from a
# parking stand on a real apron, along real taxiway centrelines, to a
# runway entry point. WIII / WADD have no extracted geometry yet and
# fall back to the legacy synthetic spawn points.
# ---------------------------------------------------------------------------

# WARR Juanda — runway 10/28
_WARR_RUNWAY = ((-7.382111, 112.800902), (-7.377543, 112.772637))
_WARR_ROUTES = [
    [(-7.382283, 112.779417), (-7.380832, 112.779775), (-7.380693, 112.780194), (-7.381651, 112.786161), (-7.382758, 112.792989), (-7.383625, 112.798313), (-7.383891, 112.800043), (-7.383619, 112.800464), (-7.382470, 112.800228), (-7.382111, 112.800902)],
    [(-7.382283, 112.779417), (-7.382518, 112.778972), (-7.382256, 112.777354), (-7.381280, 112.775885), (-7.380231, 112.775962), (-7.379935, 112.775621), (-7.379552, 112.773255), (-7.379270, 112.772947), (-7.378640, 112.773011), (-7.378253, 112.773289), (-7.377543, 112.772637)],
    [(-7.383069, 112.781998), (-7.383881, 112.785595), (-7.383722, 112.786166), (-7.382218, 112.786542), (-7.381805, 112.787104), (-7.382758, 112.792989), (-7.383625, 112.798313), (-7.383891, 112.800043), (-7.383619, 112.800464), (-7.382470, 112.800228), (-7.382111, 112.800902)],
    [(-7.383069, 112.781998), (-7.382256, 112.777354), (-7.381280, 112.775885), (-7.380231, 112.775962), (-7.379935, 112.775621), (-7.379552, 112.773255), (-7.379270, 112.772947), (-7.378640, 112.773011), (-7.378253, 112.773289), (-7.377543, 112.772637)],
    [(-7.382802, 112.780827), (-7.383881, 112.785595), (-7.383722, 112.786166), (-7.382218, 112.786542), (-7.381805, 112.787104), (-7.382758, 112.792989), (-7.383625, 112.798313), (-7.383891, 112.800043), (-7.383619, 112.800464), (-7.382470, 112.800228), (-7.382111, 112.800902)],
    [(-7.382802, 112.780827), (-7.382256, 112.777354), (-7.381280, 112.775885), (-7.380231, 112.775962), (-7.379935, 112.775621), (-7.379552, 112.773255), (-7.379270, 112.772947), (-7.378640, 112.773011), (-7.378253, 112.773289), (-7.377543, 112.772637)],
    [(-7.382427, 112.778293), (-7.382518, 112.778972), (-7.382283, 112.779417), (-7.380832, 112.779775), (-7.380693, 112.780194), (-7.381651, 112.786161), (-7.382758, 112.792989), (-7.383625, 112.798313), (-7.383891, 112.800043), (-7.383619, 112.800464), (-7.382470, 112.800228), (-7.382111, 112.800902)],
    [(-7.382427, 112.778293), (-7.381448, 112.775919), (-7.380231, 112.775962), (-7.379935, 112.775621), (-7.379552, 112.773255), (-7.379270, 112.772947), (-7.378640, 112.773011), (-7.378253, 112.773289), (-7.377543, 112.772637)],
    [(-7.382256, 112.777354), (-7.382518, 112.778972), (-7.382283, 112.779417), (-7.380832, 112.779775), (-7.380693, 112.780194), (-7.381651, 112.786161), (-7.382758, 112.792989), (-7.383625, 112.798313), (-7.383891, 112.800043), (-7.383619, 112.800464), (-7.382470, 112.800228), (-7.382111, 112.800902)],
    [(-7.382256, 112.777354), (-7.381280, 112.775885), (-7.380231, 112.775962), (-7.379935, 112.775621), (-7.379552, 112.773255), (-7.379270, 112.772947), (-7.378640, 112.773011), (-7.378253, 112.773289), (-7.377543, 112.772637)],
    [(-7.381737, 112.776613), (-7.382326, 112.777621), (-7.382518, 112.778972), (-7.382283, 112.779417), (-7.380832, 112.779775), (-7.380693, 112.780194), (-7.381651, 112.786161), (-7.382758, 112.792989), (-7.383625, 112.798313), (-7.383891, 112.800043), (-7.383619, 112.800464), (-7.382470, 112.800228), (-7.382111, 112.800902)],
    [(-7.381737, 112.776613), (-7.381280, 112.775885), (-7.380231, 112.775962), (-7.379935, 112.775621), (-7.379552, 112.773255), (-7.379270, 112.772947), (-7.378640, 112.773011), (-7.378253, 112.773289), (-7.377543, 112.772637)],
]

# WAHS Ahmad Yani — runway 13/31 (taxi network reaches the 31 end)
_WAHS_RUNWAY = ((-6.977284, 110.381072), (-6.962341, 110.363371))
_WAHS_ROUTES = [
    [(-6.975077, 110.376042), (-6.976407, 110.377465), (-6.976299, 110.379105), (-6.977965, 110.379410), (-6.977284, 110.381072)],
    [(-6.973894, 110.374216), (-6.976407, 110.377465), (-6.976299, 110.379105), (-6.977965, 110.379410), (-6.977284, 110.381072)],
    [(-6.976356, 110.377338), (-6.976299, 110.379105), (-6.977965, 110.379410), (-6.977284, 110.381072)],
    [(-6.977806, 110.379051), (-6.977965, 110.379410), (-6.977284, 110.381072)],
]

# WAHI Yogyakarta International — runway 11/29
_WAHI_RUNWAY = ((-7.910306, 110.071045), (-7.900261, 110.043424))
_WAHI_ROUTES = [
    [(-7.901734, 110.060359), (-7.902613, 110.060048), (-7.904452, 110.064935), (-7.904924, 110.065155), (-7.905879, 110.064798), (-7.906200, 110.064935), (-7.908459, 110.070973), (-7.909000, 110.072225), (-7.909621, 110.072032), (-7.910046, 110.070916), (-7.910306, 110.071045)],
    [(-7.901734, 110.060359), (-7.901767, 110.059922), (-7.902473, 110.059675), (-7.902031, 110.058500), (-7.902188, 110.057961), (-7.903176, 110.057586), (-7.903413, 110.057162), (-7.901294, 110.051361), (-7.898911, 110.044742), (-7.898506, 110.043621), (-7.898670, 110.043148), (-7.899065, 110.043124), (-7.900029, 110.043566), (-7.900261, 110.043424)],
    [(-7.901482, 110.059171), (-7.902187, 110.058916), (-7.904452, 110.064935), (-7.904924, 110.065155), (-7.905879, 110.064798), (-7.906200, 110.064935), (-7.908459, 110.070973), (-7.909000, 110.072225), (-7.909621, 110.072032), (-7.910046, 110.070916), (-7.910306, 110.071045)],
    [(-7.901482, 110.059171), (-7.902187, 110.058916), (-7.902188, 110.057961), (-7.903176, 110.057586), (-7.903413, 110.057162), (-7.901294, 110.051361), (-7.898911, 110.044742), (-7.898506, 110.043621), (-7.898670, 110.043148), (-7.899065, 110.043124), (-7.900029, 110.043566), (-7.900261, 110.043424)],
    [(-7.900856, 110.058060), (-7.901752, 110.057736), (-7.904452, 110.064935), (-7.904924, 110.065155), (-7.905879, 110.064798), (-7.906200, 110.064935), (-7.908459, 110.070973), (-7.909000, 110.072225), (-7.909621, 110.072032), (-7.910046, 110.070916), (-7.910306, 110.071045)],
    [(-7.900856, 110.058060), (-7.900926, 110.057613), (-7.901616, 110.057364), (-7.899627, 110.051932), (-7.899786, 110.051546), (-7.900890, 110.051142), (-7.901053, 110.050702), (-7.898911, 110.044742), (-7.898506, 110.043621), (-7.898670, 110.043148), (-7.899065, 110.043124), (-7.900029, 110.043566), (-7.900261, 110.043424)],
    [(-7.900671, 110.056853), (-7.901339, 110.056608), (-7.904452, 110.064935), (-7.904924, 110.065155), (-7.905879, 110.064798), (-7.906200, 110.064935), (-7.908459, 110.070973), (-7.909000, 110.072225), (-7.909621, 110.072032), (-7.910046, 110.070916), (-7.910306, 110.071045)],
    [(-7.900671, 110.056853), (-7.901339, 110.056608), (-7.899627, 110.051932), (-7.899786, 110.051546), (-7.900890, 110.051142), (-7.901053, 110.050702), (-7.898911, 110.044742), (-7.898506, 110.043621), (-7.898670, 110.043148), (-7.899065, 110.043124), (-7.900029, 110.043566), (-7.900261, 110.043424)],
]

# ---------------------------------------------------------------------------
# Airport registry
# ---------------------------------------------------------------------------

AIRPORTS: Dict[str, Airport] = {
    "WIII": Airport(
        icao="WIII",
        name="Soekarno-Hatta International Airport",
        latitude=-6.1256,
        longitude=106.6559,
        elevation_ft=34,
        runway_heading_deg=250,
        spawn_points=_wiii_spawns(),
    ),
    "WARR": Airport(
        icao="WARR",
        name="Juanda International Airport",
        latitude=-7.3798,
        longitude=112.7869,
        elevation_ft=9,
        runway_heading_deg=100,
        spawn_points=_warr_spawns(),
        runway_line=_WARR_RUNWAY,
        taxi_routes=_WARR_ROUTES,
    ),
    "WAHS": Airport(
        icao="WAHS",
        name="Jenderal Ahmad Yani International Airport",
        latitude=-6.9727,
        longitude=110.3751,
        elevation_ft=10,
        runway_heading_deg=130,
        spawn_points=_wahs_spawns(),
        runway_line=_WAHS_RUNWAY,
        taxi_routes=_WAHS_ROUTES,
    ),
    "WAHI": Airport(
        icao="WAHI",
        name="Yogyakarta International Airport",
        latitude=-7.9053,
        longitude=110.0575,
        elevation_ft=39,
        runway_heading_deg=110,  # real runway is 11/29 (OSM), not 08/26
        spawn_points=_wahi_spawns(),
        runway_line=_WAHI_RUNWAY,
        taxi_routes=_WAHI_ROUTES,
    ),
    "WADD": Airport(
        icao="WADD",
        name="I Gusti Ngurah Rai International Airport",
        latitude=-8.7482,
        longitude=115.1670,
        elevation_ft=14,
        runway_heading_deg=90,
        spawn_points=_wadd_spawns(),
    ),
}


def get_airport(icao: str) -> Airport:
    try:
        return AIRPORTS[icao.upper()]
    except KeyError as exc:
        raise KeyError(f"Unknown airport ICAO code: {icao!r}") from exc
