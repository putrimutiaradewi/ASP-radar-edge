import os
import struct
import tempfile
import unittest

from pcap.writer import (
    AsterixPcapSession,
    NetworkConfig,
    PCAP_MAGIC,
    build_ethernet_frame,
    build_ipv4_header,
    build_udp_segment,
)


class TestChecksums(unittest.TestCase):
    def test_udp_segment_length_field(self):
        payload = b"\x30\x00\x1c" + b"\x00" * 25
        segment = build_udp_segment(payload, "10.0.0.1", "10.0.0.2", 8600, 8600)
        length = int.from_bytes(segment[4:6], "big")
        self.assertEqual(length, 8 + len(payload))

    def test_ipv4_header_checksum_is_zero_sum(self):
        header = build_ipv4_header(28, "10.0.0.1", "10.0.0.2", ident=1)
        # Summing a correctly-checksummed header (with checksum field
        # included) over 16-bit words must fold to 0xFFFF.
        total = 0
        for i in range(0, len(header), 2):
            total += (header[i] << 8) + header[i + 1]
        while total >> 16:
            total = (total & 0xFFFF) + (total >> 16)
        self.assertEqual(total, 0xFFFF)

    def test_ipv4_header_length_is_20_bytes(self):
        header = build_ipv4_header(10, "1.2.3.4", "5.6.7.8", ident=1)
        self.assertEqual(len(header), 20)


class TestEthernetFrame(unittest.TestCase):
    def test_frame_structure_and_ethertype(self):
        network = NetworkConfig(src_ip="10.1.1.1", dst_ip="10.1.1.2", src_port=8600, dst_port=8600)
        payload = b"\x30\x00\x08\xAA\xAA\xAA\xAA\xAA"
        frame = build_ethernet_frame(payload, network, ident=1)
        # dst mac (6) + src mac (6) + ethertype (2) = 14 byte eth header
        ethertype = struct.unpack("!H", frame[12:14])[0]
        self.assertEqual(ethertype, 0x0800)  # IPv4
        # payload should appear at the tail of the frame
        self.assertTrue(frame.endswith(payload))

    def test_frame_total_length(self):
        network = NetworkConfig()
        payload = b"\x00" * 16
        frame = build_ethernet_frame(payload, network, ident=1)
        # 14 (eth) + 20 (ip) + 8 (udp) + payload
        self.assertEqual(len(frame), 14 + 20 + 8 + len(payload))


class TestPcapFile(unittest.TestCase):
    def test_global_header_magic_and_linktype(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "test.pcap")
            network = NetworkConfig()
            with AsterixPcapSession(path, network, base_epoch_seconds=1700000000) as session:
                session.add_message(b"\x30\x00\x04\xFF", elapsed_seconds=0.0)
                session.add_message(b"\x30\x00\x04\xEE", elapsed_seconds=1.5)

            with open(path, "rb") as f:
                data = f.read()

            magic = struct.unpack("<I", data[0:4])[0]
            self.assertEqual(magic, PCAP_MAGIC)
            linktype = struct.unpack("<I", data[20:24])[0]
            self.assertEqual(linktype, 1)  # Ethernet

    def test_two_messages_produce_two_packet_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "test.pcap")
            network = NetworkConfig()
            with AsterixPcapSession(path, network, base_epoch_seconds=1700000000) as session:
                session.add_message(b"\x30\x00\x04\xFF", elapsed_seconds=0.0)
                session.add_message(b"\x30\x00\x04\xEE", elapsed_seconds=1.0)

            file_size = os.path.getsize(path)
            global_header_size = 24
            packet_record_size = 16 + 4 + (14 + 20 + 8)  # hdr + asterix payload
            expected = global_header_size + 2 * packet_record_size
            self.assertEqual(file_size, expected)

    def test_timestamps_increase_with_elapsed_seconds(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "test.pcap")
            network = NetworkConfig()
            base = 1_700_000_000
            with AsterixPcapSession(path, network, base_epoch_seconds=base) as session:
                session.add_message(b"\x30\x00\x04\xFF", elapsed_seconds=0.0)
                session.add_message(b"\x30\x00\x04\xEE", elapsed_seconds=5.0)

            with open(path, "rb") as f:
                data = f.read()
            offset = 24
            ts1_sec = struct.unpack("<I", data[offset:offset + 4])[0]
            rec1_len = struct.unpack("<I", data[offset + 8:offset + 12])[0]
            offset2 = offset + 16 + rec1_len
            ts2_sec = struct.unpack("<I", data[offset2:offset2 + 4])[0]
            self.assertEqual(ts1_sec, base)
            self.assertEqual(ts2_sec, base + 5)


if __name__ == "__main__":
    unittest.main()
