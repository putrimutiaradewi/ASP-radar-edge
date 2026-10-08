/**
 * Replay UDP payloads from a pcap/pcapng into the edge's UDP port (E2E tooling).
 *
 *   bun run scripts/pcap-replay.ts capture.pcapng --port 13217 --dst-port 13217 --speed 1
 *
 * Options:
 *   --host <ip>       edge UDP host           (default 127.0.0.1)
 *   --port <n>        edge UDP port           (default 13217)
 *   --dst-port <n>    only replay packets captured with this destination port
 *   --speed <x|max>   timing factor vs. capture; "max" = no delay (default 1)
 *   --loop            replay forever
 */
import dgram from "node:dgram";
import { readFileSync } from "node:fs";
import { parseCapture } from "./pcap";

function arg(name: string, fallback?: string): string | undefined {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 ? process.argv[i + 1] : fallback;
}

const file = process.argv[2];
if (!file || file.startsWith("--")) {
  console.error("usage: bun run scripts/pcap-replay.ts <capture.pcap|pcapng> [--host H] [--port P] [--dst-port P] [--speed X|max] [--loop]");
  process.exit(2);
}

const host = arg("host", "127.0.0.1") as string;
const port = Number(arg("port", "13217"));
const dstPort = arg("dst-port") ? Number(arg("dst-port")) : undefined;
const speedArg = arg("speed", "1") as string;
const speed = speedArg === "max" ? Number.POSITIVE_INFINITY : Number(speedArg);
const loop = process.argv.includes("--loop");

const { packets, skipped } = parseCapture(readFileSync(file), { dstPort });
console.log(`parsed ${packets.length} UDP packets`, skipped);
if (packets.length === 0) process.exit(1);

const sock = dgram.createSocket("udp4");
const send = (p: Buffer): Promise<void> =>
  new Promise((res, rej) => sock.send(p, port, host, (e) => (e ? rej(e) : res())));
const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms));

let round = 0;
do {
  round++;
  let sent = 0;
  let prev = (packets[0] as (typeof packets)[number]).tsMs;
  for (const p of packets) {
    if (Number.isFinite(speed)) {
      const gap = (p.tsMs - prev) / speed;
      if (gap >= 1) await sleep(gap);
    } else if (sent % 50 === 0) {
      await sleep(0); // let the receiver drain; avoids same-host kernel buffer overflow
    }
    prev = p.tsMs;
    await send(p.payload);
    sent++;
  }
  console.log(`round ${round}: sent ${sent} datagrams to ${host}:${port}`);
} while (loop);
sock.close();
