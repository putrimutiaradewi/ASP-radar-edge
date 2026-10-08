import unittest

from encoder.cat019 import encode_cat019_record
from encoder.cat020 import encode_cat020_record
from encoder.cat021 import encode_cat021_record
from encoder.cat023 import (
    encode_cat023_ground_station_status,
    encode_cat023_record,
    encode_cat023_service_status,
)
from encoder.cat034 import encode_cat034_record
from encoder.cat048 import encode_cat048_record
from encoder.cat062 import encode_cat062_record
from encoder.cat063 import encode_cat063_record
from encoder.cat247 import encode_cat247_record
from simulation.aircraft import Aircraft, FlightPhase, OperationType


def _make_aircraft(**overrides) -> Aircraft:
    defaults = dict(
        icao_address="8A0F21",
        callsign="GIA123",
        airline="Garuda Indonesia",
        operation=OperationType.DEPARTURE,
        track_number=1,
        latitude=-6.1256,
        longitude=106.6559,
        altitude_ft=35000.0,
        ground_speed_kt=450.0,
        track_deg=270.0,
        heading_deg=270.0,
        vertical_rate_fpm=0.0,
        squawk="1200",
        flight_level=350.0,
        timestamp_seconds=7200.0,
        phase=FlightPhase.CRUISE,
        on_ground=False,
    )
    defaults.update(overrides)
    return Aircraft(**defaults)


def _decode_record_header(record: bytes):
    cat = record[0]
    length = int.from_bytes(record[1:3], "big")
    return cat, length


def _decode_fspec(data: bytes, offset: int = 3):
    presence = []
    while True:
        byte = data[offset]
        offset += 1
        for i in range(7):
            presence.append(bool(byte & (1 << (7 - i))))
        if not (byte & 0x01):
            break
    return presence, offset


class EncoderRecordStructureMixin:
    """Shared structural assertions applicable to every category."""

    def assert_well_formed(self, record: bytes, expected_cat: int):
        cat, length = _decode_record_header(record)
        self.assertEqual(cat, expected_cat)
        self.assertEqual(length, len(record), "declared LEN must match actual record length")
        self.assertGreaterEqual(length, 4, "record must contain at least CAT+LEN+1 FSPEC octet")
        presence, item_offset = _decode_fspec(record)
        # FSPEC correctly terminates before running past the record
        self.assertLessEqual(item_offset, length)


class TestCat019(unittest.TestCase, EncoderRecordStructureMixin):
    def test_matches_real_feed_structure(self):
        record = encode_cat019_record(1, 1, timestamp_seconds=27862.0, ref_lat=-7.3798, ref_lon=112.7869)
        self.assert_well_formed(record, 19)
        self.assertEqual(len(record), 60)                    # real feed: 60-byte records
        self.assertEqual(record[3:5].hex(), "ffe0")          # real feed FSPEC


class TestCat020(unittest.TestCase, EncoderRecordStructureMixin):
    REF = (-7.3798, 112.7869)

    def test_ground_record_matches_real_fspec_and_length(self):
        ac = _make_aircraft(on_ground=True, altitude_ft=0.0, flight_level=0.0, ground_speed_kt=10.0)
        record = encode_cat020_record(ac, 1, 1, *self.REF, variant="ground")
        self.assert_well_formed(record, 20)
        self.assertEqual(record[3:7].hex(), "ff4d4584")
        self.assertEqual(len(record), 96)

    def test_ground_noid_record_matches_real_fspec_and_length(self):
        ac = _make_aircraft(on_ground=True, altitude_ft=0.0, flight_level=0.0, ground_speed_kt=10.0)
        record = encode_cat020_record(ac, 1, 1, *self.REF, variant="ground_noid")
        self.assert_well_formed(record, 20)
        self.assertEqual(record[3:7].hex(), "ff494584")
        self.assertEqual(len(record), 89)

    def test_airborne_record_matches_real_fspec_and_length(self):
        ac = _make_aircraft(on_ground=False, altitude_ft=500.0, flight_level=5.0)
        record = encode_cat020_record(ac, 1, 1, *self.REF, variant="airborne")
        self.assert_well_formed(record, 20)
        self.assertEqual(record[3:7].hex(), "ff6d4584")
        self.assertEqual(len(record), 98)

    def test_vehicle_record_carries_fleet_id(self):
        ac = _make_aircraft(on_ground=True, altitude_ft=0.0, flight_level=0.0, ground_speed_kt=15.0)
        record = encode_cat020_record(ac, 1, 1, *self.REF, variant="vehicle", vehicle_fleet_id=6)
        self.assert_well_formed(record, 20)
        self.assertEqual(record[3:7].hex(), "ff4d6584")  # ground FSPEC + FRN17 (I020/300)
        self.assertEqual(len(record), 97)                # ground variant + 1 fleet-id byte


class TestCat021(unittest.TestCase, EncoderRecordStructureMixin):
    def test_airborne_record_matches_real_fspec_and_length(self):
        ac = _make_aircraft(on_ground=False)
        record = encode_cat021_record(ac, 0, 181, variant="airborne")
        self.assert_well_formed(record, 21)
        self.assertEqual(record[3:8].hex(), "ffa1db0300")
        self.assertEqual(len(record), 46)

    def test_airborne_sel_record_matches_real_fspec_and_length(self):
        ac = _make_aircraft(on_ground=False)
        record = encode_cat021_record(ac, 0, 181, variant="airborne_sel")
        self.assert_well_formed(record, 21)
        self.assertEqual(record[3:8].hex(), "ffa35ba700")
        self.assertEqual(len(record), 51)

    def test_ground_record_matches_real_fspec_and_length(self):
        ac = _make_aircraft(on_ground=True, ground_speed_kt=0.0)
        record = encode_cat021_record(ac, 0, 181, variant="ground")
        self.assert_well_formed(record, 21)
        self.assertEqual(record[3:8].hex(), "fb811b0300")
        self.assertEqual(len(record), 36)

    def test_ground_ident_record_matches_real_fspec_and_length(self):
        ac = _make_aircraft(on_ground=True, ground_speed_kt=0.0)
        record = encode_cat021_record(ac, 0, 181, variant="ground_ident")
        self.assert_well_formed(record, 21)
        self.assertEqual(record[3:8].hex(), "fb811b8700")
        self.assertEqual(len(record), 39)

    def test_auto_variant_follows_ground_state(self):
        rec_ground = encode_cat021_record(_make_aircraft(on_ground=True), 0, 181)
        rec_air = encode_cat021_record(_make_aircraft(on_ground=False), 0, 181)
        self.assertLess(len(rec_ground), len(rec_air))


class TestCat023(unittest.TestCase, EncoderRecordStructureMixin):
    def test_ground_station_status_matches_real_feed(self):
        record = encode_cat023_ground_station_status(0, 181, timestamp_seconds=3600)
        self.assert_well_formed(record, 23)
        self.assertEqual(record[3:5].hex(), "d900")
        self.assertEqual(len(record), 13)

    def test_service_status_matches_real_feed(self):
        record = encode_cat023_service_status(0, 181, timestamp_seconds=3600)
        self.assert_well_formed(record, 23)
        self.assertEqual(record[3:5].hex(), "f580")
        self.assertEqual(len(record), 16)

    def test_legacy_entry_point_still_encodes(self):
        record = encode_cat023_record(0, 181, timestamp_seconds=3600, service_id=1)
        self.assert_well_formed(record, 23)


class TestCat247(unittest.TestCase, EncoderRecordStructureMixin):
    def test_matches_real_feed_structure(self):
        record = encode_cat247_record(0, 181, timestamp_seconds=3600)
        self.assert_well_formed(record, 247)
        self.assertEqual(record[3], 0xF0)                    # real feed FSPEC
        self.assertEqual(len(record), 20)
        self.assertIn(bytes.fromhex("15001a"), record)       # announces CAT021 v0.26


class TestCat034(unittest.TestCase, EncoderRecordStructureMixin):
    def test_sector_crossing_message(self):
        record = encode_cat034_record(202, 3, timestamp_seconds=100.0, sector_deg=45.0)
        self.assert_well_formed(record, 34)


class TestCat048(unittest.TestCase, EncoderRecordStructureMixin):
    def test_mode_s_record_includes_address_and_id(self):
        ac = _make_aircraft(on_ground=False)
        record = encode_cat048_record(ac, sac=202, sic=3, radar_lat=-6.1256, radar_lon=106.6559, mode_s=True)
        self.assert_well_formed(record, 48)
        presence, _ = _decode_fspec(record)
        self.assertTrue(presence[6])  # target address present
        self.assertTrue(presence[7])  # aircraft id present

    def test_primary_only_record_omits_mode_s_items(self):
        ac = _make_aircraft(on_ground=False)
        record = encode_cat048_record(ac, sac=202, sic=3, radar_lat=-6.1256, radar_lon=106.6559, mode_s=False)
        self.assert_well_formed(record, 48)
        presence, _ = _decode_fspec(record)
        self.assertFalse(presence[6])
        self.assertFalse(presence[7])


class TestCat062(unittest.TestCase, EncoderRecordStructureMixin):
    def test_system_track_record(self):
        ac = _make_aircraft(on_ground=False)
        record = encode_cat062_record(ac, sac=202, sic=4)
        self.assert_well_formed(record, 62)


class TestCat063(unittest.TestCase, EncoderRecordStructureMixin):
    def test_sensor_status_record(self):
        record = encode_cat063_record(202, 4, timestamp_seconds=50.0, sensor_sac=202, sensor_sic=3)
        self.assert_well_formed(record, 63)


if __name__ == "__main__":
    unittest.main()
