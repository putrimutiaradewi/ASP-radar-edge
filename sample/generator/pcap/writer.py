"""Ethernet + IPv4 + UDP packet framing and a standard (libpcap format)
PCAP file writer, so ASTERIX data blocks can be captured as if they
had been sniffed off the wire and opened directly in Wireshark."""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import BinaryIO, List, Optional, Tuple

# --------------------------------------------------------------------------
# PCAP global/packet header constants
# --------------------------------------------------------------------------

PCAP_MAGIC = 0xA1B2C3D4
PCAP_VERSION_MAJOR = 2
PCAP_VERSION_MINOR = 4
LINKTYPE_ETHERNET = 1


def _mac_to_bytes(mac: str) -> bytes:
    return bytes(int(part, 16) for part in mac.split(":"))


@dataclass
class NetworkConfig:
    """Configurable network parameters used to encapsulate ASTERIX
    payloads for transport simulation. Defaults replicate the real
    Juanda ADS-B multicast stream."""

    src_ip: str = "192.168.30.92"
    dst_ip: str = "239.200.2.2"
    src_port: int = 44180
    dst_port: int = 8700
    src_mac: str = "08:cc:a7:e7:9f:42"
    dst_mac: str = "01:00:5e:48:02:02"  # IPv4 multicast MAC for 239.200.2.2
    ttl: int = 63
    tos: int = 0x78
    # The real feed sends IP ident = 0 on every packet; set to None to
    # use an incrementing ident instead.
    fixed_ident: Optional[int] = 0


def _ipv4_checksum(header: bytes) -> int:
    if len(header) % 2:
        header += b"\x00"
    total = 0
    for i in range(0, len(header), 2):
        total += (header[i] << 8) + header[i + 1]
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return (~total) & 0xFFFF


def _ip_to_bytes(ip: str) -> bytes:
    return bytes(int(part) for part in ip.split("."))


def build_ipv4_header(
    payload_len: int, src_ip: str, dst_ip: str, ident: int,
    protocol: int = 17, ttl: int = 64, tos: int = 0,
) -> bytes:
    version_ihl = (4 << 4) | 5
    total_length = 20 + payload_len
    flags_fragment = 0x4000  # Don't Fragment
    checksum = 0
    src = _ip_to_bytes(src_ip)
    dst = _ip_to_bytes(dst_ip)

    header = struct.pack(
        "!BBHHHBBH4s4s",
        version_ihl,
        tos,
        total_length,
        ident & 0xFFFF,
        flags_fragment,
        ttl,
        protocol,
        checksum,
        src,
        dst,
    )
    checksum = _ipv4_checksum(header)
    header = struct.pack(
        "!BBHHHBBH4s4s",
        version_ihl,
        tos,
        total_length,
        ident & 0xFFFF,
        flags_fragment,
        ttl,
        protocol,
        checksum,
        src,
        dst,
    )
    return header


def _udp_checksum(src_ip: str, dst_ip: str, udp_segment: bytes) -> int:
    pseudo_header = _ip_to_bytes(src_ip) + _ip_to_bytes(dst_ip) + struct.pack("!BBH", 0, 17, len(udp_segment))
    data = pseudo_header + udp_segment
    if len(data) % 2:
        data += b"\x00"
    total = 0
    for i in range(0, len(data), 2):
        total += (data[i] << 8) + data[i + 1]
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    checksum = (~total) & 0xFFFF
    return checksum if checksum != 0 else 0xFFFF


def build_udp_segment(payload: bytes, src_ip: str, dst_ip: str, src_port: int, dst_port: int) -> bytes:
    length = 8 + len(payload)
    header_no_checksum = struct.pack("!HHHH", src_port, dst_port, length, 0)
    segment = header_no_checksum + payload
    checksum = _udp_checksum(src_ip, dst_ip, segment)
    header = struct.pack("!HHHH", src_port, dst_port, length, checksum)
    return header + payload


def build_ethernet_frame(payload: bytes, network: NetworkConfig, ident: int) -> bytes:
    """Wrap an ASTERIX payload in UDP -> IPv4 -> Ethernet headers."""
    if network.fixed_ident is not None:
        ident = network.fixed_ident
    udp_segment = build_udp_segment(payload, network.src_ip, network.dst_ip, network.src_port, network.dst_port)
    ip_header = build_ipv4_header(
        len(udp_segment), network.src_ip, network.dst_ip, ident,
        ttl=network.ttl, tos=network.tos,
    )
    ip_packet = ip_header + udp_segment

    eth_header = _mac_to_bytes(network.dst_mac) + _mac_to_bytes(network.src_mac) + struct.pack("!H", 0x0800)
    return eth_header + ip_packet


# --------------------------------------------------------------------------
# PCAP file writer
# --------------------------------------------------------------------------


class PcapWriter:
    """Writes packets to a classic (libpcap) format .pcap file, one
    packet at a time, so files stream well even for large scenarios."""

    def __init__(self, file_obj: BinaryIO):
        self._f = file_obj
        self._write_global_header()

    def _write_global_header(self) -> None:
        header = struct.pack(
            "<IHHiIII",
            PCAP_MAGIC,
            PCAP_VERSION_MAJOR,
            PCAP_VERSION_MINOR,
            0,       # thiszone
            0,       # sigfigs
            65535,   # snaplen
            LINKTYPE_ETHERNET,
        )
        self._f.write(header)

    def write_packet(self, data: bytes, ts_sec: int, ts_usec: int = 0) -> None:
        packet_header = struct.pack("<IIII", ts_sec, ts_usec, len(data), len(data))
        self._f.write(packet_header)
        self._f.write(data)

    def close(self) -> None:
        self._f.close()

    def __enter__(self) -> "PcapWriter":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


class AsterixPcapSession:
    """Convenience wrapper: turns a sequence of ASTERIX data-block
    payloads plus timestamps into a fully framed PCAP file."""

    def __init__(self, path: str, network: NetworkConfig, base_epoch_seconds: int):
        self._path = path
        self._network = network
        self._base_epoch_seconds = base_epoch_seconds
        self._file = open(path, "wb")
        self._writer = PcapWriter(self._file)
        self._ident = 1

    def add_message(self, asterix_payload: bytes, elapsed_seconds: float) -> None:
        frame = build_ethernet_frame(asterix_payload, self._network, self._ident)
        self._ident += 1
        ts_sec = self._base_epoch_seconds + int(elapsed_seconds)
        ts_usec = int((elapsed_seconds % 1.0) * 1_000_000)
        self._writer.write_packet(frame, ts_sec, ts_usec)

    def close(self) -> None:
        self._writer.close()

    def __enter__(self) -> "AsterixPcapSession":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


class CombinedPcapSession:
    """Writes every category of an airport into ONE chronologically
    ordered PCAP file — like a real multi-stream capture — so a single
    replay keeps all categories on the same timeline. Messages may
    arrive slightly out of order across categories within a second, so
    they are buffered and sorted before writing."""

    def __init__(self, path: str, base_epoch_seconds: int):
        self._path = path
        self._base_epoch_seconds = base_epoch_seconds
        self._messages: List[Tuple[float, bytes]] = []

    def add_message(self, asterix_payload: bytes, elapsed_seconds: float, network: NetworkConfig) -> None:
        frame = build_ethernet_frame(asterix_payload, network, 0)
        self._messages.append((elapsed_seconds, frame))

    def close(self) -> None:
        self._messages.sort(key=lambda m: m[0])
        with open(self._path, "wb") as f:
            writer = PcapWriter(f)
            for elapsed, frame in self._messages:
                ts_sec = self._base_epoch_seconds + int(elapsed)
                ts_usec = int((elapsed % 1.0) * 1_000_000)
                writer.write_packet(frame, ts_sec, ts_usec)
            f.flush()

    def __enter__(self) -> "CombinedPcapSession":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
