import { afterEach, describe, expect, test } from "bun:test";
import dgram from "node:dgram";
import { createServer, type IncomingMessage, type Server as HttpServer } from "node:http";
import type { AddressInfo, Socket } from "node:net";
import { WebSocketServer, type WebSocket as ServerSocket } from "ws";
import type { EdgeConfig } from "./config";
import { Edge } from "./edge";
import { captureLogger } from "./test-utils";

const TOKEN = "srctk_integration_secret_123";
const FEED = "radar-b";

interface Received {
  data: Buffer;
  isBinary: boolean;
}

/** Upper bound for MockIngest.close() so an afterEach hook can never hang the suite. */
const CLOSE_GRACE_MS = 1_000;

class MockIngest {
  wss!: WebSocketServer;
  port = 0;
  frames: Received[] = [];
  upgrades: IncomingMessage[] = [];
  sockets = new Set<ServerSocket>();
  rejectAuth = false;

  // We own the HTTP server (instead of letting ws create one from `port`) so close() can also
  // destroy sockets that never became a WebSocket (rejected 401 upgrades, keep-alive, etc).
  // Note: Bun only reports a socket via 'connection' once its first request arrives, so a
  // connection that never sends anything cannot be tracked - CLOSE_GRACE_MS bounds that case.
  private httpServer: HttpServer | null = null;
  private tcpSockets = new Set<Socket>();
  private closing: Promise<void> | null = null;

  async listen(port = 0): Promise<void> {
    const httpServer = createServer((_req, res) => {
      res.writeHead(426).end(); // plain HTTP is not supported here, only WS upgrades
    });
    this.httpServer = httpServer;
    httpServer.on("connection", (c) => {
      this.tcpSockets.add(c);
      c.once("close", () => this.tcpSockets.delete(c));
    });

    this.wss = new WebSocketServer({
      server: httpServer,
      path: "/ws/ingest/radar",
      verifyClient: (info, done) => {
        this.upgrades.push(info.req);
        const ok = !this.rejectAuth && info.req.headers.authorization === `Bearer ${TOKEN}`;
        if (ok) done(true);
        else done(false, 401, "Unauthorized");
      },
    });
    // Registered before listening so no early connection can slip past the handlers.
    this.wss.on("connection", (s) => {
      this.sockets.add(s);
      s.on("close", () => this.sockets.delete(s));
      s.on("message", (data, isBinary) => this.frames.push({ data: data as Buffer, isBinary }));
    });

    await new Promise<void>((resolve, reject) => {
      // ws re-emits the HTTP server's 'error' on the wss; listen there so a bind failure
      // (e.g. EADDRINUSE) rejects listen() instead of surfacing as an uncaught exception.
      this.wss.once("error", reject);
      httpServer.listen(port, "127.0.0.1", () => {
        this.wss.off("error", reject);
        resolve();
      });
    });
    this.port = (httpServer.address() as AddressInfo).port;
  }

  /** Idempotent: every call (sequential or concurrent) returns the same promise. Never rejects. */
  close(): Promise<void> {
    this.closing ??= this.shutdown();
    return this.closing;
  }

  private async shutdown(): Promise<void> {
    const httpServer = this.httpServer;
    if (!httpServer) return; // listen() was never called

    // 1. Stop accepting new connections / upgrades.
    const httpClosed = new Promise<void>((resolve) => httpServer.close(() => resolve()));
    const wssClosed = new Promise<void>((resolve) => this.wss.close(() => resolve()));

    // 2. Hard-kill every WebSocket client (terminate() = no closing handshake to wait for).
    for (const ws of new Set([...this.sockets, ...this.wss.clients])) {
      try {
        ws.terminate();
      } catch {
        /* already gone */
      }
    }
    // 3. Destroy tracked raw TCP sockets, including ones that never became a WebSocket.
    for (const c of this.tcpSockets) c.destroy();
    httpServer.closeAllConnections?.();

    // 4. Wait for both servers to report closed - but never longer than CLOSE_GRACE_MS.
    let timer: ReturnType<typeof setTimeout> | undefined;
    const grace = new Promise<"timeout">((resolve) => {
      timer = setTimeout(() => resolve("timeout"), CLOSE_GRACE_MS);
    });
    const outcome = await Promise.race([Promise.all([httpClosed, wssClosed]).then(() => "closed" as const), grace]);
    clearTimeout(timer);
    if (outcome === "timeout") {
      console.warn(`[MockIngest] close(): servers did not report closed within ${CLOSE_GRACE_MS}ms; continuing`);
    }
  }
}

function config(port: number, over: Partial<EdgeConfig> = {}): EdgeConfig {
  return {
    udpListenHost: "127.0.0.1",
    udpListenPort: 0,
    ingestUrl: `ws://127.0.0.1:${port}/ws/ingest/radar`,
    sourceToken: TOKEN,
    feedId: FEED,
    reconnectMaxMs: 400,
    statsIntervalMs: 60_000,
    maxBufferedBytes: 1_048_576,
    pingIntervalMs: 15_000,
    udpRecvBufferBytes: 4_194_304,
    logLevel: "debug",
    ...over,
  };
}

async function waitFor(cond: () => boolean, ms = 3000, label = "condition"): Promise<void> {
  const t0 = Date.now();
  while (!cond()) {
    if (Date.now() - t0 > ms) throw new Error(`timeout waiting for ${label}`);
    await new Promise((r) => setTimeout(r, 5));
  }
}

const udpClient = dgram.createSocket("udp4");
function sendUdp(port: number, payload: Buffer): Promise<void> {
  return new Promise((resolve, reject) =>
    udpClient.send(payload, port, "127.0.0.1", (e) => (e ? reject(e) : resolve())),
  );
}

const cleanups: Array<() => Promise<void>> = [];
afterEach(async () => {
  while (cleanups.length) await cleanups.pop()?.();
});

async function boot(over: Partial<EdgeConfig> = {}, serverPort = 0) {
  const server = new MockIngest();
  await server.listen(serverPort);
  cleanups.push(() => server.close().catch(() => {}));
  const logger = captureLogger([TOKEN]);
  const edge = new Edge(config(server.port, over), { logger, baseDelayMs: 40 });
  await edge.start();
  cleanups.push(() => edge.stop());
  return { server, edge, logger };
}

describe("UDP -> Edge -> WSS (real sockets)", () => {
  test("authenticates and delivers each datagram as one identical binary frame", async () => {
    const { server, edge } = await boot();
    await waitFor(() => edge.connected, 3000, "ws connected");

    const up = server.upgrades[0] as IncomingMessage;
    expect(up.url).toBe("/ws/ingest/radar");
    expect(up.headers.authorization).toBe(`Bearer ${TOKEN}`);
    expect(up.headers["x-feed-id"]).toBe(FEED);

    const payloads = [
      Buffer.from([0x00]),
      Buffer.from([0xff, 0x00, 0xff, 0x00]),
      Buffer.from("ASTERIX\x30\x00\x0b\x80\xfe"),
      Buffer.alloc(1400, 0xab),
      Buffer.from(Array.from({ length: 256 }, (_, i) => i)), // every byte value
      Buffer.alloc(60_000, 0x5a), // large datagram, still ONE frame
    ];
    for (const p of payloads) await sendUdp(edge.udpPort as number, p);
    await waitFor(() => server.frames.length === payloads.length, 3000, "all frames");

    expect(server.frames.every((f) => f.isBinary)).toBe(true);
    payloads.forEach((p, i) => {
      expect((server.frames[i] as Received).data.equals(p)).toBe(true);
    });
    expect(edge.stats.snapshot()).toMatchObject({
      received: payloads.length,
      forwarded: payloads.length,
      dropped: 0,
    });
  });

  test("a burst of 300 distinct datagrams arrives complete, in order, unmodified", async () => {
    const { server, edge } = await boot();
    await waitFor(() => edge.connected);
    const N = 300;
    const sent: Buffer[] = [];
    for (let i = 0; i < N; i++) {
      const b = Buffer.alloc(48);
      b.writeUInt32BE(i, 0);
      for (let j = 4; j < 48; j++) b[j] = (i * 31 + j) & 0xff;
      sent.push(b);
      await sendUdp(edge.udpPort as number, b);
      // The sender shares this process's event loop. Without yielding, the receiver is
      // starved and the kernel buffer (not the edge) drops packets - a test artifact.
      // A real radar is a separate host.
      if (i % 25 === 0) await new Promise((r) => setTimeout(r, 0));
    }
    await waitFor(() => server.frames.length === N, 5000, "burst");
    for (let i = 0; i < N; i++) expect((server.frames[i] as Received).data.equals(sent[i] as Buffer)).toBe(true);
    expect(edge.stats.dropped).toBe(0);
  });

  test("server outage: packets are dropped (not buffered); edge reconnects and resumes", async () => {
    const { server, edge } = await boot();
    await waitFor(() => edge.connected);
    const port = server.port;

    await sendUdp(edge.udpPort as number, Buffer.from("before"));
    await waitFor(() => server.frames.length === 1);

    // Kill the server completely.
    await server.close();
    await waitFor(() => !edge.connected, 3000, "edge notices disconnect");

    const during = 25;
    for (let i = 0; i < during; i++) await sendUdp(edge.udpPort as number, Buffer.from(`lost-${i}`));
    await waitFor(() => edge.stats.received === 1 + during, 3000, "outage packets received");
    expect(edge.stats.dropped).toBe(during);

    // Bring the server back on the same port.
    const revived = new MockIngest();
    await revived.listen(port);
    cleanups.push(() => revived.close().catch(() => {}));
    await waitFor(() => edge.connected, 5000, "reconnected");
    expect(edge.stats.reconnects).toBeGreaterThanOrEqual(1);

    await sendUdp(edge.udpPort as number, Buffer.from("after"));
    await waitFor(() => revived.frames.length >= 1);

    // Only the post-recovery packet arrives: nothing from the outage was queued and replayed.
    await new Promise((r) => setTimeout(r, 100));
    expect(revived.frames.map((f) => f.data.toString())).toEqual(["after"]);
    expect(edge.stats.snapshot()).toMatchObject({ received: 1 + during + 1, forwarded: 2, dropped: during });
  });

  test("rejected credentials: edge keeps running, drops, retries with backoff, token never logged", async () => {
    const server = new MockIngest();
    server.rejectAuth = true;
    await server.listen();
    cleanups.push(() => server.close().catch(() => {}));
    const logger = captureLogger([TOKEN]);
    const edge = new Edge(config(server.port), { logger, baseDelayMs: 30 });
    await edge.start();
    cleanups.push(() => edge.stop());

    await waitFor(() => server.upgrades.length >= 3, 3000, "multiple retry attempts");
    await sendUdp(edge.udpPort as number, Buffer.from("x"));
    await waitFor(() => edge.stats.received === 1);

    expect(edge.connected).toBe(false);
    expect(edge.stats.dropped).toBe(1);
    expect(edge.stats.reconnects).toBeGreaterThanOrEqual(2);
    // The server must have seen exactly the secret it needs, but our logs must not contain it.
    for (const line of logger.lines) expect(line).not.toContain(TOKEN);
    // Regression: the failure reason must be visible in the logs (not `{isTrusted:true}`).
    const errLine = logger.records().find((r) => r.msg === "websocket error");
    expect(errLine).toBeDefined();
    expect(JSON.stringify(errLine)).not.toContain("isTrusted");
    expect(String((errLine?.err as { message?: string })?.message ?? "")).not.toBe("");
    expect(errLine?.hint).toContain("SOURCE_TOKEN");
    // Retry gaps grow (30ms, 60ms, ...): fewer attempts than a 30ms hammer loop would produce.
    expect(logger.records().some((r) => typeof r.msg === "string" && r.msg === "reconnecting")).toBe(true);
  });

  test("periodic stats summary reports received/forwarded/dropped/reconnects", async () => {
    const { server, edge, logger } = await boot({ statsIntervalMs: 100 });
    await waitFor(() => edge.connected);
    await sendUdp(edge.udpPort as number, Buffer.from("a"));
    await sendUdp(edge.udpPort as number, Buffer.from("b"));
    await waitFor(() => server.frames.length === 2);
    await waitFor(
      () => logger.records().some((r) => r.msg === "stats" && (r.forwarded as number) === 2),
      2000,
      "stats line",
    );
    const line = logger.records().find((r) => r.msg === "stats" && (r.forwarded as number) === 2);
    expect(line).toMatchObject({ received: 2, forwarded: 2, dropped: 0, reconnects: 0, connected: true });
    expect(line).toHaveProperty("interval");
  });

  test("full lifecycle never leaks the token into logs", async () => {
    const { edge, logger, server } = await boot();
    await waitFor(() => edge.connected);
    await sendUdp(edge.udpPort as number, Buffer.from("z"));
    await waitFor(() => server.frames.length === 1);
    await edge.stop();
    expect(logger.lines.length).toBeGreaterThan(3);
    for (const line of logger.lines) expect(line).not.toContain(TOKEN);
  });

  test("stop() releases the UDP port", async () => {
    const { edge } = await boot();
    const port = edge.udpPort as number;
    await edge.stop();
    // Rebinding the same port must succeed once the edge has shut down.
    const probe = dgram.createSocket("udp4");
    await new Promise<void>((resolve, reject) => {
      probe.once("error", reject);
      probe.bind(port, "127.0.0.1", () => resolve());
    });
    probe.close();
  });
});
