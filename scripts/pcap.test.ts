import { describe, expect, test } from "bun:test";
import { parseCapture } from "./pcap";

/** Build an Ethernet/IPv4/UDP frame. */
function frame(payload: Buffer, dstPort = 13217, srcPort = 5000, opts: { vlan?: boolean; proto?: number; frag?: number } = {}): Buffer {
  const eth = Buffer.alloc(opts.vlan ? 18 : 14);
  if (opts.vlan) {
    eth.writeUInt16BE(0x8100, 12);
    eth.writeUInt16BE(0x0800, 16);
  } else eth.writeUInt16BE(0x0800, 12);
  const ip = Buffer.alloc(20);
  ip[0] = 0x45;
  ip.writeUInt16BE(20 + 8 + payload.length, 2);
  ip.writeUInt16BE(opts.frag ?? 0, 6);
  ip[9] = opts.proto ?? 17;
  const udp = Buffer.alloc(8);
  udp.writeUInt16BE(srcPort, 0);
  udp.writeUInt16BE(dstPort, 2);
  udp.writeUInt16BE(8 + payload.length, 4);
  return Buffer.concat([eth, ip, udp, payload]);
}

function pcap(frames: Array<{ f: Buffer; sec: number; usec: number; snap?: number }>, le = true): Buffer {
  const w32 = (n: number) => { const b = Buffer.alloc(4); le ? b.writeUInt32LE(n) : b.writeUInt32BE(n); return b; };
  const w16 = (n: number) => { const b = Buffer.alloc(2); le ? b.writeUInt16LE(n) : b.writeUInt16BE(n); return b; };
  const hdr = Buffer.concat([w32(0xa1b2c3d4), w16(2), w16(4), w32(0), w32(0), w32(65535), w32(1)]);
  // writing magic in file byte order: LE file stores d4 c3 b2 a1
  if (le) hdr.writeUInt32LE(0xa1b2c3d4, 0); else hdr.writeUInt32BE(0xa1b2c3d4, 0);
  const recs = frames.map(({ f, sec, usec, snap }) => {
    const incl = snap ?? f.length;
    return Buffer.concat([w32(sec), w32(usec), w32(incl), w32(f.length), f.subarray(0, incl)]);
  });
  return Buffer.concat([hdr, ...recs]);
}

function pcapng(frames: Buffer[], tsticks: number[]): Buffer {
  const pad = (b: Buffer) => Buffer.concat([b, Buffer.alloc((4 - (b.length % 4)) % 4)]);
  const blk = (type: number, body: Buffer) => {
    const total = 12 + body.length;
    const h = Buffer.alloc(8);
    h.writeUInt32LE(type, 0);
    h.writeUInt32LE(total, 4);
    const t = Buffer.alloc(4);
    t.writeUInt32LE(total, 0);
    return Buffer.concat([h, body, t]);
  };
  const shb = Buffer.alloc(16);
  shb.writeUInt32LE(0x1a2b3c4d, 0);
  shb.writeUInt16LE(1, 4);
  shb.writeBigInt64LE(-1n, 8);
  const idb = Buffer.alloc(8);
  idb.writeUInt16LE(1, 0); // ethernet
  idb.writeUInt32LE(65535, 4);
  const opt = Buffer.from([9, 0, 1, 0, 6, 0, 0, 0, 0, 0, 0, 0]); // if_tsresol=6 (usec) + end
  const epbs = frames.map((f, i) => {
    const b = Buffer.alloc(20);
    b.writeUInt32LE(0, 0);
    b.writeUInt32LE(Math.floor(tsticks[i]! / 2 ** 32), 4);
    b.writeUInt32LE(tsticks[i]! >>> 0, 8);
    b.writeUInt32LE(f.length, 12);
    b.writeUInt32LE(f.length, 16);
    return blk(6, Buffer.concat([b, pad(f)]));
  });
  const shbBlock = Buffer.concat([Buffer.from([0x0a, 0x0d, 0x0d, 0x0a]), Buffer.alloc(4), shb, Buffer.alloc(0)]);
  shbBlock.writeUInt32LE(28, 4);
  const shbFull = Buffer.concat([shbBlock, Buffer.from([28, 0, 0, 0])]);
  return Buffer.concat([shbFull, blk(1, Buffer.concat([idb, opt])), ...epbs]);
}

const A = Buffer.from([0x30, 0x00, 0x0b, 0xff, 0x00]);
const B = Buffer.alloc(300, 0xab);

describe("parseCapture (pcap)", () => {
  test("extracts UDP payloads byte-for-byte with timestamps (little-endian file)", () => {
    const r = parseCapture(pcap([{ f: frame(A), sec: 10, usec: 0 }, { f: frame(B), sec: 10, usec: 500_000 }]));
    expect(r.packets.length).toBe(2);
    expect(r.packets[0]!.payload.equals(A)).toBe(true);
    expect(r.packets[1]!.payload.equals(B)).toBe(true);
    expect(r.packets[1]!.tsMs - r.packets[0]!.tsMs).toBeCloseTo(500, 3);
    expect(r.packets[0]!.dstPort).toBe(13217);
  });

  test("handles big-endian files and VLAN tags", () => {
    const r = parseCapture(pcap([{ f: frame(A, 13217, 1, { vlan: true }), sec: 1, usec: 0 }], false));
    expect(r.packets[0]!.payload.equals(A)).toBe(true);
  });

  test("filters by destination port", () => {
    const r = parseCapture(pcap([{ f: frame(A, 13217), sec: 1, usec: 0 }, { f: frame(B, 9999), sec: 1, usec: 1 }]), { dstPort: 13217 });
    expect(r.packets.length).toBe(1);
  });

  test("skips non-UDP, fragmented and snaplen-truncated packets instead of altering them", () => {
    const r = parseCapture(
      pcap([
        { f: frame(A, 13217, 1, { proto: 6 }), sec: 1, usec: 0 },
        { f: frame(A, 13217, 1, { frag: 0x2000 }), sec: 1, usec: 1 },
        { f: frame(B), sec: 1, usec: 2, snap: 100 },
        { f: frame(A), sec: 1, usec: 3 },
      ]),
    );
    expect(r.packets.length).toBe(1);
    expect(r.skipped).toMatchObject({ nonUdp: 1, fragmented: 1, truncated: 1 });
  });

  test("rejects non-capture input", () => {
    expect(() => parseCapture(Buffer.from("this is not a capture file"))).toThrow();
  });
});

describe("parseCapture (pcapng)", () => {
  test("extracts payloads and converts timestamps using if_tsresol", () => {
    const r = parseCapture(pcapng([frame(A), frame(B)], [1_000_000, 1_250_000])); // microseconds
    expect(r.packets.length).toBe(2);
    expect(r.packets[0]!.payload.equals(A)).toBe(true);
    expect(r.packets[1]!.payload.equals(B)).toBe(true);
    expect(r.packets[1]!.tsMs - r.packets[0]!.tsMs).toBeCloseTo(250, 3);
  });
});
