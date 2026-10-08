import { afterEach, expect, test } from "bun:test";
import dgram from "node:dgram";
import { captureLogger } from "./test-utils";
import { UdpReceiver } from "./udp-receiver";

const receivers: UdpReceiver[] = [];
afterEach(async () => {
  while (receivers.length) await receivers.pop()?.stop();
});

function make(over: { recvBufferBytes?: number; onDatagram?: (m: Buffer) => void; port?: number } = {}) {
  const logger = captureLogger();
  const r = new UdpReceiver({
    host: "127.0.0.1",
    port: over.port ?? 0,
    recvBufferBytes: over.recvBufferBytes ?? 1_048_576,
    onDatagram: over.onDatagram ?? (() => {}),
    logger,
  });
  receivers.push(r);
  return { r, logger };
}

test("receives raw datagrams byte-for-byte", async () => {
  const got: Buffer[] = [];
  const { r } = make({ onDatagram: (m) => got.push(m) });
  const { port } = await r.start();
  const tx = dgram.createSocket("udp4");
  const payload = Buffer.from([0x00, 0xff, 0x30, 0x80, 0x00]);
  await new Promise<void>((res) => tx.send(payload, port, "127.0.0.1", () => res()));
  const t0 = Date.now();
  while (got.length < 1 && Date.now() - t0 < 2000) await new Promise((r) => setTimeout(r, 5));
  tx.close();
  expect(got.length).toBe(1);
  expect((got[0] as Buffer).equals(payload)).toBe(true);
});

test("a throwing handler does not kill the receiver", async () => {
  let calls = 0;
  const { r, logger } = make({
    onDatagram: () => {
      calls++;
      throw new Error("bug");
    },
  });
  const { port } = await r.start();
  const tx = dgram.createSocket("udp4");
  for (let i = 0; i < 3; i++) {
    await new Promise<void>((res) => tx.send(Buffer.from([i]), port, "127.0.0.1", () => res()));
    await new Promise((res) => setTimeout(res, 10));
  }
  tx.close();
  expect(calls).toBe(3);
  expect(logger.records().filter((x) => x.msg === "datagram handler threw").length).toBe(3);
});

test("start() rejects when the port is already in use", async () => {
  const { r: first } = make();
  const { port } = await first.start();
  const { r: second } = make({ port });
  await expect(second.start()).rejects.toThrow();
});

test("reports the granted receive buffer", async () => {
  const { r } = make({ recvBufferBytes: 1_048_576 });
  const bound = await r.start();
  expect(typeof bound.recvBufferBytes).toBe("number");
  expect(bound.recvBufferBytes as number).toBeGreaterThan(0);
});

test.skipIf(process.platform !== "linux")("warns when the OS clamps the requested buffer", async () => {
  const { r, logger } = make({ recvBufferBytes: 1_073_741_824 });
  await r.start();
  expect(logger.records().some((x) => x.msg === "OS granted a smaller UDP receive buffer than requested")).toBe(true);
});
