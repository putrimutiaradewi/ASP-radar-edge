/**
 * Minimal pcap / pcapng reader that extracts UDP payloads (IPv4 only).
 * Dev/E2E tooling - not part of the edge runtime.
 *
 * Packets that cannot be reproduced byte-for-byte (snaplen-truncated, IP-fragmented)
 * are skipped and counted rather than replayed in altered form.
 */

export interface UdpPacket {
  tsMs: number;
  srcPort: number;
  dstPort: number;
  payload: Buffer;
}

export interface ParseResult {
  packets: UdpPacket[];
  skipped: { nonUdp: number; fragmented: number; truncated: number; unsupportedLink: number };
}

interface Reader {
  u16(o: number): number;
  u32(o: number): number;
}

const reader = (buf: Buffer, le: boolean): Reader => ({
  u16: (o) => (le ? buf.readUInt16LE(o) : buf.readUInt16BE(o)),
  u32: (o) => (le ? buf.readUInt32LE(o) : buf.readUInt32BE(o)),
});

const LINK_ETHERNET = 1;
const LINK_RAW = 101;
const LINK_LINUX_SLL = 113;

export function parseCapture(buf: Buffer, opts: { dstPort?: number } = {}): ParseResult {
  const result: ParseResult = {
    packets: [],
    skipped: { nonUdp: 0, fragmented: 0, truncated: 0, unsupportedLink: 0 },
  };

  const handle = (frame: Buffer, link: number, tsMs: number, truncated: boolean): void => {
    if (truncated) {
      result.skipped.truncated++;
      return;
    }
    let ipOff: number;
    if (link === LINK_ETHERNET) {
      if (frame.length < 14) return void result.skipped.nonUdp++;
      let type = frame.readUInt16BE(12);
      let off = 14;
      while ((type === 0x8100 || type === 0x88a8) && frame.length >= off + 4) {
        type = frame.readUInt16BE(off + 2);
        off += 4;
      }
      if (type !== 0x0800) return void result.skipped.nonUdp++;
      ipOff = off;
    } else if (link === LINK_LINUX_SLL) {
      if (frame.length < 16 || frame.readUInt16BE(14) !== 0x0800) return void result.skipped.nonUdp++;
      ipOff = 16;
    } else if (link === LINK_RAW) {
      ipOff = 0;
    } else {
      result.skipped.unsupportedLink++;
      return;
    }

    if (frame.length < ipOff + 20 || frame[ipOff]! >> 4 !== 4) return void result.skipped.nonUdp++;
    const ihl = (frame[ipOff]! & 0x0f) * 4;
    const proto = frame[ipOff + 9];
    if (proto !== 17) return void result.skipped.nonUdp++;
    const fragField = frame.readUInt16BE(ipOff + 6);
    if ((fragField & 0x1fff) !== 0 || (fragField & 0x2000) !== 0) return void result.skipped.fragmented++;

    const udpOff = ipOff + ihl;
    if (frame.length < udpOff + 8) return void result.skipped.truncated++;
    const srcPort = frame.readUInt16BE(udpOff);
    const dstPort = frame.readUInt16BE(udpOff + 2);
    const udpLen = frame.readUInt16BE(udpOff + 4);
    const payloadLen = udpLen - 8;
    if (payloadLen < 0 || frame.length < udpOff + 8 + payloadLen) return void result.skipped.truncated++;
    if (opts.dstPort !== undefined && dstPort !== opts.dstPort) return void result.skipped.nonUdp++;

    // Copy: the payload must not alias the capture buffer.
    result.packets.push({
      tsMs,
      srcPort,
      dstPort,
      payload: Buffer.from(frame.subarray(udpOff + 8, udpOff + 8 + payloadLen)),
    });
  };

  if (buf.length < 12) throw new Error("File too short to be a capture");
  const magicBE = buf.readUInt32BE(0);

  if (magicBE === 0x0a0d0d0a) {
    parsePcapng(buf, handle);
  } else if ([0xa1b2c3d4, 0xd4c3b2a1, 0xa1b23c4d, 0x4d3cb2a1].includes(magicBE)) {
    const le = magicBE === 0xd4c3b2a1 || magicBE === 0x4d3cb2a1;
    const nano = magicBE === 0xa1b23c4d || magicBE === 0x4d3cb2a1;
    const r = reader(buf, le);
    const link = r.u32(20);
    let off = 24;
    while (off + 16 <= buf.length) {
      const sec = r.u32(off);
      const frac = r.u32(off + 4);
      const incl = r.u32(off + 8);
      const orig = r.u32(off + 12);
      off += 16;
      if (off + incl > buf.length) break; // truncated file
      handle(buf.subarray(off, off + incl), link, sec * 1000 + (nano ? frac / 1e6 : frac / 1e3), incl < orig);
      off += incl;
    }
  } else {
    throw new Error("Unrecognized capture format (expected pcap or pcapng)");
  }
  return result;
}

function parsePcapng(
  buf: Buffer,
  handle: (frame: Buffer, link: number, tsMs: number, truncated: boolean) => void,
): void {
  let le = true;
  const ifaces: Array<{ link: number; msPerTick: number }> = [];
  let off = 0;
  while (off + 12 <= buf.length) {
    if (buf.readUInt32BE(off) === 0x0a0d0d0a) {
      // Section header: byte-order magic decides endianness for this section.
      le = buf.readUInt32LE(off + 8) === 0x1a2b3c4d;
      ifaces.length = 0;
    }
    const r = reader(buf, le);
    const type = r.u32(off);
    const total = r.u32(off + 4);
    if (total < 12 || off + total > buf.length) break;

    if (type === 1) {
      // Interface Description Block
      const link = r.u16(off + 8);
      let msPerTick = 1e-3; // default resolution: microseconds
      let o = off + 16;
      const end = off + total - 4;
      while (o + 4 <= end) {
        const code = r.u16(o);
        const len = r.u16(o + 2);
        if (code === 0) break;
        if (code === 9 && len >= 1) {
          const v = buf[o + 4]!;
          msPerTick = (v & 0x80 ? 2 ** -(v & 0x7f) : 10 ** -v) * 1000;
        }
        o += 4 + Math.ceil(len / 4) * 4;
      }
      ifaces.push({ link, msPerTick });
    } else if (type === 6) {
      // Enhanced Packet Block
      const iface = ifaces[r.u32(off + 8)];
      const ticks = r.u32(off + 12) * 2 ** 32 + r.u32(off + 16);
      const cap = r.u32(off + 20);
      const orig = r.u32(off + 24);
      if (iface && off + 28 + cap <= off + total) {
        handle(buf.subarray(off + 28, off + 28 + cap), iface.link, ticks * iface.msPerTick, cap < orig);
      }
    }
    off += total;
  }
}
