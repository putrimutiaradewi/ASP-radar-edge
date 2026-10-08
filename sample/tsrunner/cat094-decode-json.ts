import { readFileSync, writeFileSync, readdirSync, statSync } from 'node:fs';
import { join, basename } from 'node:path';
import { parseAsterix } from '../../packages/server/codec/dist/index.js';

// Usage:
//   tsx cat094-decode-json.ts .
//   tsx cat094-decode-json.ts data/interface_eth0_29072026.pcap
//   tsx cat094-decode-json.ts data/interface_eth0_29072026.pcap data/interface_eth1_29072026.pcap
//   tsx cat094-decode-json.ts data/interface_eth0_29072026.pcap out.json

function* iterPcap(buf: Buffer) {
  const magic = buf.readUInt32LE(0);
  const swapped = magic === 0xd4c3b2a1;
  if (magic !== 0xa1b2c3d4 && !swapped) return;
  const r32 = swapped
    ? (b: Buffer, o: number) => b.readUInt32BE(o)
    : (b: Buffer, o: number) => b.readUInt32LE(o);
  let off = 24;
  while (off + 16 <= buf.length) {
    const incl = r32(buf, off + 8);
    if (off + 16 + incl > buf.length) break;
    yield buf.slice(off + 16, off + 16 + incl);
    off += 16 + incl;
  }
}

function* iterPcapng(buf: Buffer) {
  if (buf.readUInt32LE(0) !== 0x0a0d0d0a) return;
  let off = 0;
  while (off + 12 <= buf.length) {
    const type = buf.readUInt32LE(off);
    const len = buf.readUInt32LE(off + 4);
    if (len < 12 || off + len > buf.length) break;
    const body = buf.slice(off + 8, off + 8 + len - 12);
    off += len;
    if (type === 0x00000006) {
      if (body.length < 20) continue;
      const capLen = body.readUInt32LE(12);
      if (capLen > body.length - 20) continue;
      yield body.slice(20, 20 + capLen);
    }
  }
}

function extractAsterixPayload(pkt: Buffer) {
  if (pkt.length < 42) return null;
  const ihl = (pkt[14]! & 0x0f) * 4;
  if (ihl < 20) return null;
  if (pkt[23] !== 17) return null;
  const udpStart = 14 + ihl;
  if (pkt.length < udpStart + 8) return null;
  const udpLen = (pkt[udpStart + 4]! << 8) | pkt[udpStart + 5]!;
  return pkt.slice(udpStart + 8, Math.min(pkt.length, udpStart + udpLen));
}

function decodeRecord(cat: number, len: number, block: Buffer) {
  const header = Buffer.from([cat, (len >> 8) & 0xff, len & 0xff]);
  const rec = Buffer.concat([header, block]);
  const parsed = parseAsterix(rec); // auto-switch across all known editions
  return parsed[0] ?? null;
}

const rawArgs = process.argv.slice(2);
let outputFile = 'pcap-output.json';
if (rawArgs.length && rawArgs[rawArgs.length - 1]!.endsWith('.json')) {
  outputFile = rawArgs.pop()!;
}
const inputPaths = rawArgs.length ? rawArgs : ['.'];

const files: string[] = [];
for (const p of inputPaths) {
  try {
    const s = statSync(p);
    if (s.isDirectory()) {
      for (const f of readdirSync(p)) {
        if (/\.(pcap|pcapng)$/i.test(f)) files.push(join(p, f));
      }
    } else if (s.isFile()) {
      files.push(p);
    }
  } catch {
    console.error(`skip: ${p} not found`);
  }
}

const byCat: Record<string, any[]> = {};
const counts: Record<string, number> = {};
let totalRecords = 0;
let decodedRecords = 0;

for (const f of files) {
  const b = readFileSync(f);
  const magic = b.readUInt32LE(0);
  const packets =
    magic === 0xa1b2c3d4 || magic === 0xd4c3b2a1
      ? [...iterPcap(b)]
      : magic === 0x0a0d0d0a
        ? [...iterPcapng(b)]
        : [];

  for (const pkt of packets) {
    const payload = extractAsterixPayload(pkt);
    if (!payload) continue;

    let o = 0;
    while (o + 3 <= payload.length) {
      const cat = payload[o]!;
      const len = (payload[o + 1]! << 8) | payload[o + 2]!;
      if (len < 3 || o + len > payload.length) break;

      const block = payload.slice(o + 3, o + len);
      const rec = decodeRecord(cat, len, block);
      counts[cat] = (counts[cat] ?? 0) + 1;
      if (rec) {
        (rec as any)._sourceFile = basename(f);
        byCat[cat] = byCat[cat] ?? [];
        byCat[cat].push(rec);
        decodedRecords++;
      }
      totalRecords++;
      o += len;
    }
  }
}

const summary = { totalRecords, decodedRecords, counts, records: byCat };
writeFileSync(outputFile, JSON.stringify(summary, null, 2));
console.log(`${outputFile}: ${decodedRecords} decoded / ${totalRecords} total. Per category: ${JSON.stringify(counts)}`);
