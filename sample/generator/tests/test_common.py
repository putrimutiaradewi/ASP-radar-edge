import math
import unittest

from encoder.common import (
    bearing_distance_nm,
    build_record,
    destination_point,
    encode_fspec,
    encode_icao_address,
    encode_mode3a,
    encode_sac_sic,
    encode_time_of_day,
    encode_wgs84_position_24,
    encode_wgs84_position_32,
    pack_ia5_6bit,
    wrap_record,
)


class TestFspec(unittest.TestCase):
    def test_single_octet_no_extension(self):
        presence = [True, False, True, False, False, False, False]
        fspec = encode_fspec(presence)
        self.assertEqual(len(fspec), 1)
        # bit8=1 (item1), bit6=1 (item3), bit1(FX)=0
        self.assertEqual(fspec[0], 0b10100000)

    def test_extension_bit_set_when_more_than_7_items(self):
        presence = [True] * 8  # needs 2 octets
        fspec = encode_fspec(presence)
        self.assertEqual(len(fspec), 2)
        self.assertEqual(fspec[0] & 0x01, 1)  # FX set on first octet
        self.assertEqual(fspec[1] & 0x01, 0)  # FX clear on last octet
        self.assertEqual(fspec[1] & 0x80, 0x80)  # 8th item = bit8 of 2nd octet

    def test_empty_presence_raises(self):
        with self.assertRaises(ValueError):
            encode_fspec([])


class TestBuildRecordAndWrap(unittest.TestCase):
    def test_build_record_skips_absent_items(self):
        items = [(True, b"\xAA"), (False, b"\xBB\xBB"), (True, b"\xCC")]
        body = build_record(items)
        # fspec(1 byte) + item1(1) + item3(1) = 3 bytes; item2 skipped
        self.assertEqual(len(body), 3)
        self.assertEqual(body[1:], b"\xAA\xCC")

    def test_wrap_record_length_field_matches_actual_length(self):
        body = b"\x01\x02\x03\x04\x05"
        record = wrap_record(20, body)
        cat = record[0]
        length = int.from_bytes(record[1:3], "big")
        self.assertEqual(cat, 20)
        self.assertEqual(length, len(record))


class TestTimeOfDay(unittest.TestCase):
    def test_round_trip_precision(self):
        seconds = 12345.5
        encoded = encode_time_of_day(seconds)
        self.assertEqual(len(encoded), 3)
        raw = int.from_bytes(encoded, "big")
        decoded = raw / 128.0
        self.assertAlmostEqual(decoded, seconds, delta=1 / 128.0)

    def test_midnight_zero(self):
        self.assertEqual(encode_time_of_day(0), b"\x00\x00\x00")


class TestPositions(unittest.TestCase):
    def test_wgs84_32_round_trip(self):
        lat, lon = -6.1256, 106.6559
        encoded = encode_wgs84_position_32(lat, lon)
        self.assertEqual(len(encoded), 8)
        lsb = 180.0 / (2 ** 25)
        lat_raw = int.from_bytes(encoded[0:4], "big", signed=True)
        lon_raw = int.from_bytes(encoded[4:8], "big", signed=True)
        self.assertAlmostEqual(lat_raw * lsb, lat, delta=lsb)
        self.assertAlmostEqual(lon_raw * lsb, lon, delta=lsb)

    def test_wgs84_24_round_trip(self):
        lat, lon = -8.7482, 115.1670
        encoded = encode_wgs84_position_24(lat, lon)
        self.assertEqual(len(encoded), 6)
        lsb = 180.0 / (2 ** 23)
        lat_raw = int.from_bytes(encoded[0:3], "big", signed=True)
        lon_raw = int.from_bytes(encoded[3:6], "big", signed=True)
        self.assertAlmostEqual(lat_raw * lsb, lat, delta=lsb)
        self.assertAlmostEqual(lon_raw * lsb, lon, delta=lsb)

    def test_negative_longitude_encodes_correctly(self):
        encoded = encode_wgs84_position_32(0.0, -179.9)
        lsb = 180.0 / (2 ** 25)
        lon_raw = int.from_bytes(encoded[4:8], "big", signed=True)
        self.assertAlmostEqual(lon_raw * lsb, -179.9, delta=lsb)


class TestBearingAndDestination(unittest.TestCase):
    def test_destination_point_matches_bearing_distance_inverse(self):
        lat0, lon0 = -6.1256, 106.6559
        bearing = 45.0
        distance = 50.0
        lat1, lon1 = destination_point(lat0, lon0, bearing, distance)
        back_distance, back_bearing = bearing_distance_nm(lat0, lon0, lat1, lon1)
        self.assertAlmostEqual(back_distance, distance, delta=0.05)
        self.assertAlmostEqual(back_bearing, bearing, delta=0.5)

    def test_zero_distance_stays_put(self):
        lat0, lon0 = -7.3798, 112.7869
        lat1, lon1 = destination_point(lat0, lon0, 90.0, 0.0)
        self.assertAlmostEqual(lat0, lat1, places=6)
        self.assertAlmostEqual(lon0, lon1, places=6)


class TestMode3A(unittest.TestCase):
    def test_encode_mode3a_digits(self):
        encoded = encode_mode3a("1200")
        value = int.from_bytes(encoded, "big")
        # bits 12-1 hold four 3-bit octal digits A4A3A2A1
        d1 = (value >> 9) & 0x7
        d2 = (value >> 6) & 0x7
        d3 = (value >> 3) & 0x7
        d4 = value & 0x7
        self.assertEqual((d1, d2, d3, d4), (1, 2, 0, 0))

    def test_all_zero_squawk(self):
        encoded = encode_mode3a("0000")
        self.assertEqual(encoded, b"\x00\x00")


class TestIcaoAndSacSic(unittest.TestCase):
    def test_icao_address_is_3_bytes(self):
        encoded = encode_icao_address("8A0F21")
        self.assertEqual(encoded, bytes.fromhex("8A0F21"))

    def test_sac_sic(self):
        self.assertEqual(encode_sac_sic(202, 3), bytes([202, 3]))


class TestIa5Packing(unittest.TestCase):
    def test_pack_length(self):
        encoded = pack_ia5_6bit("GIA123", num_chars=8)
        self.assertEqual(len(encoded), 6)  # 8 chars * 6 bits = 48 bits = 6 bytes

    def test_pack_unpack_round_trip(self):
        text = "GIA123"
        encoded = pack_ia5_6bit(text, num_chars=8)
        # manually unpack using the inverse of the ASTERIX IA5 6-bit
        # table: codes 1-26 are letters (A=1..Z=26), 48-57 are digits
        # '0'-'9' (their ASCII value already fits in 6 bits), and 32 is
        # space.
        bits = int.from_bytes(encoded, "big")
        chars = []
        for i in range(8):
            shift = (7 - i) * 6
            code = (bits >> shift) & 0x3F
            if code == 32:
                chars.append(" ")
            elif 1 <= code <= 26:
                chars.append(chr(code + 0x40))
            elif 48 <= code <= 57:
                chars.append(chr(code))
            else:
                chars.append(" ")
        decoded = "".join(chars).rstrip()
        self.assertEqual(decoded, text)

    def test_padding_is_space(self):
        encoded = pack_ia5_6bit("AB", num_chars=8)
        bits = int.from_bytes(encoded, "big")
        last_char_code = bits & 0x3F
        self.assertEqual(last_char_code, ord(" ") & 0x3F)


if __name__ == "__main__":
    unittest.main()
