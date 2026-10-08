import { beforeEach, describe, expect, test } from "bun:test";
import { Stats } from "./stats";
import { captureLogger, createFakeConnector, FakeClock, type FakeConn } from "./test-utils";
import { WssForwarder } from "./wss-forwarder";

const TOKEN = "srctk_unit_test_token";

function setup(overrides: Partial<{ maxBufferedBytes: number; pingIntervalMs: number; reconnectMaxMs: number }> = {}) {
  const clock = new FakeClock();
  const stats = new Stats();
  const logger = captureLogger([TOKEN]);
  const { connector, conns } = createFakeConnector();
  const fwd = new WssForwarder({
    config: {
      ingestUrl: "wss://aeroswarm.example.com/ws/ingest/radar",
      sourceToken: TOKEN,
      feedId: "radar-b",
      reconnectMaxMs: overrides.reconnectMaxMs ?? 30_000,
      maxBufferedBytes: overrides.maxBufferedBytes ?? 1024,
      pingIntervalMs: overrides.pingIntervalMs ?? 15_000,
    },
    stats,
    logger,
    clock,
    connector,
  });
  const conn = (i: number): FakeConn => {
    const c = conns[i];
    if (!c) throw new Error(`no connection #${i}`);
    return c;
  };
  return { clock, stats, logger, fwd, conns, conn };
}

describe("UDP datagram -> WSS binary frame", () => {
  test("forwards exactly one frame per datagram with identical bytes", () => {
    const { fwd, stats, conn } = setup();
    fwd.start();
    conn(0).open();

    const a = Buffer.from([0x30, 0x00, 0x0a, 0xff, 0x00, 0x01]);
    const b = Buffer.from([0xff, 0xff]);
    expect(fwd.forward(a)).toBe(true);
    expect(fwd.forward(b)).toBe(true);

    expect(conn(0).frames.length).toBe(2);
    expect(Buffer.from(conn(0).frames[0] as Uint8Array).equals(a)).toBe(true);
    expect(Buffer.from(conn(0).frames[1] as Uint8Array).equals(b)).toBe(true);
    expect(stats.forwarded).toBe(2);
    expect(stats.dropped).toBe(0);
  });

  test("hands over the original bytes, not a transformed copy", () => {
    const { fwd, conn } = setup();
    fwd.start();
    conn(0).open();
    const payload = Buffer.from("ASTERIX-CAT048-\x00\xff\x7f\x80", "latin1");
    const snapshot = Buffer.from(payload);
    fwd.forward(payload);
    expect(Buffer.from(conn(0).frames[0] as Uint8Array).equals(snapshot)).toBe(true);
  });

  test("sends the auth headers on connect", () => {
    const { fwd, conn } = setup();
    fwd.start();
    expect(conn(0).handshake.headers.Authorization).toBe(`Bearer ${TOKEN}`);
    expect(conn(0).handshake.headers["X-Feed-Id"]).toBe("radar-b");
  });
});

describe("drop instead of buffer", () => {
  test("drops while the first connection is still being established", () => {
    const { fwd, stats, conn } = setup();
    fwd.start();
    expect(fwd.forward(Buffer.from([1]))).toBe(false);
    expect(stats.dropped).toBe(1);
    expect(conn(0).frames.length).toBe(0);
  });

  test("WSS disconnect -> packets dropped, and never replayed after reconnect", () => {
    const { fwd, stats, clock, conn } = setup();
    fwd.start();
    conn(0).open();
    fwd.forward(Buffer.from([1]));

    conn(0).serverClose(1006);
    expect(fwd.connected).toBe(false);

    for (let i = 0; i < 50; i++) fwd.forward(Buffer.from([2, i]));
    expect(stats.dropped).toBe(50);
    expect(stats.forwarded).toBe(1);

    clock.advance(1000);
    conn(1).open();
    // Nothing from the outage window may show up on the new socket.
    expect(conn(1).frames.length).toBe(0);

    fwd.forward(Buffer.from([3]));
    expect(conn(1).frames.length).toBe(1);
    expect(Array.from(conn(1).frames[0] as Uint8Array)).toEqual([3]);
  });

  test("backpressure: drops when the socket send buffer is over the limit", () => {
    const { fwd, stats, conn } = setup({ maxBufferedBytes: 100 });
    fwd.start();
    conn(0).open();

    conn(0).bufferedAmount = 100; // at limit: still accepted
    expect(fwd.forward(Buffer.from([1]))).toBe(true);
    conn(0).bufferedAmount = 101; // slow link, buffer growing
    expect(fwd.forward(Buffer.from([2]))).toBe(false);
    expect(stats.dropped).toBe(1);
    expect(stats.forwarded).toBe(1);

    conn(0).bufferedAmount = 0; // drained -> flows again
    expect(fwd.forward(Buffer.from([3]))).toBe(true);
  });

  test("a failed send callback counts as dropped, not forwarded", () => {
    const { fwd, stats, conn } = setup();
    fwd.start();
    conn(0).open();
    conn(0).sendError = new Error("EPIPE");
    fwd.forward(Buffer.from([1]));
    expect(stats.forwarded).toBe(0);
    expect(stats.dropped).toBe(1);
  });

  test("a synchronous send() throw is contained and counted as dropped", () => {
    const { fwd, stats, conn } = setup();
    fwd.start();
    conn(0).open();
    conn(0).sendThrows = true;
    expect(() => fwd.forward(Buffer.from([1]))).not.toThrow();
    expect(stats.dropped).toBe(1);
  });
});

describe("reconnect with backoff", () => {
  test("repeated failures retry after 1s, 2s, 4s, 8s, 16s, 30s, 30s", () => {
    const { fwd, clock, conns, conn } = setup();
    fwd.start();
    expect(conns.length).toBe(1);

    const expected = [1000, 2000, 4000, 8000, 16000, 30000, 30000];
    let i = 0;
    for (const delay of expected) {
      conn(i).failConnect();
      // Not yet one ms before the delay elapses...
      clock.advance(delay - 1);
      expect(conns.length).toBe(i + 1);
      // ...and exactly at the delay.
      clock.advance(1);
      expect(conns.length).toBe(i + 2);
      i++;
    }
  });

  test("RECONNECT_MAX_MS is honoured", () => {
    const { fwd, clock, conns, conn } = setup({ reconnectMaxMs: 4000 });
    fwd.start();
    for (const delay of [1000, 2000, 4000, 4000]) {
      const n = conns.length;
      conn(n - 1).failConnect();
      clock.advance(delay - 1);
      expect(conns.length).toBe(n);
      clock.advance(1);
      expect(conns.length).toBe(n + 1);
    }
  });

  test("backoff resets after a connection that stayed up long enough", () => {
    const { fwd, clock, conns, conn } = setup();
    fwd.start();
    conn(0).failConnect();
    clock.advance(1000);
    conn(1).failConnect();
    clock.advance(2000); // third attempt now in flight
    conn(2).open();
    for (let i = 0; i < 4; i++) {
      clock.advance(15_000); // healthy for a minute: server answers every ping
      conn(2).pong();
    }
    conn(2).serverClose(1006);

    clock.advance(999);
    expect(conns.length).toBe(3);
    clock.advance(1); // back to 1s, not 4s
    expect(conns.length).toBe(4);
  });

  test("a flapping connection (accepts then drops immediately) does NOT reset backoff", () => {
    const { fwd, clock, conns, conn } = setup();
    fwd.start();
    conn(0).open();
    clock.advance(500);
    conn(0).serverClose(1008, "policy violation"); // e.g. auth rejected after upgrade
    clock.advance(1000);
    conn(1).open();
    clock.advance(500);
    conn(1).serverClose(1008);

    clock.advance(1999);
    expect(conns.length).toBe(2);
    clock.advance(1); // 2s, proving no hammering at 1s
    expect(conns.length).toBe(3);
  });

  test("reconnects counter counts reconnect attempts, not the initial connect", () => {
    const { fwd, stats, clock, conn } = setup();
    fwd.start();
    expect(stats.reconnects).toBe(0);
    conn(0).open();
    conn(0).serverClose();
    clock.advance(1000);
    expect(stats.reconnects).toBe(1);
    conn(1).failConnect();
    clock.advance(2000);
    expect(stats.reconnects).toBe(2);
  });

  test("late events from a dead socket are ignored (no double reconnect)", () => {
    const { fwd, clock, conns, conn } = setup();
    fwd.start();
    conn(0).open();
    conn(0).serverClose();
    clock.advance(1000);
    expect(conns.length).toBe(2);
    conn(0).serverClose(); // stale duplicate
    clock.advance(60_000);
    expect(conns.length).toBe(2);
  });
});

describe("heartbeat", () => {
  test("missed pong terminates the socket and triggers reconnect", () => {
    const { fwd, clock, conns, conn } = setup({ pingIntervalMs: 1000 });
    fwd.start();
    conn(0).open();

    clock.advance(1000); // ping #1 sent
    expect(conn(0).pings).toBe(1);
    clock.advance(1000); // no pong -> terminate
    expect(conn(0).terminated).toBe(true);
    expect(fwd.connected).toBe(false);

    clock.advance(1000);
    expect(conns.length).toBe(2);
  });

  test("answered pings keep the connection alive", () => {
    const { fwd, clock, conn } = setup({ pingIntervalMs: 1000 });
    fwd.start();
    conn(0).open();
    for (let i = 0; i < 5; i++) {
      clock.advance(1000);
      conn(0).pong();
    }
    expect(conn(0).terminated).toBe(false);
    expect(conn(0).pings).toBe(5);
    expect(fwd.connected).toBe(true);
  });
});

describe("stop()", () => {
  test("cancels pending reconnect and does not reconnect afterwards", async () => {
    const { fwd, clock, conns, conn } = setup();
    fwd.start();
    conn(0).failConnect();
    await fwd.stop();
    clock.advance(120_000);
    expect(conns.length).toBe(1);
    expect(fwd.currentState).toBe("stopped");
  });

  test("closes an open socket and resolves once the peer confirms", async () => {
    const { fwd, conn } = setup();
    fwd.start();
    conn(0).open();
    const p = fwd.stop();
    expect(conn(0).closed).toBe(true);
    conn(0).serverClose(1001);
    await p;
    expect(conn(0).terminated).toBe(false);
  });

  test("force-terminates if the peer never completes the close handshake", async () => {
    const { fwd, clock, conn } = setup();
    fwd.start();
    conn(0).open();
    const p = fwd.stop();
    clock.advance(1000);
    await p;
    expect(conn(0).terminated).toBe(true);
  });

  test("datagrams after stop are dropped", async () => {
    const { fwd, stats, conn } = setup();
    fwd.start();
    conn(0).open();
    const p = fwd.stop();
    conn(0).serverClose(1001);
    await p;
    expect(fwd.forward(Buffer.from([1]))).toBe(false);
    expect(stats.dropped).toBe(1);
  });
});

describe("secrets", () => {
  let ctx: ReturnType<typeof setup>;
  beforeEach(() => {
    ctx = setup();
  });

  test("the token never appears in any log line across a full connect/fail/reconnect cycle", () => {
    const { fwd, clock, conn, logger } = ctx;
    fwd.start();
    conn(0).failConnect(`handshake failed: Authorization: Bearer ${TOKEN}`);
    clock.advance(1000);
    conn(1).open();
    conn(1).serverClose(1008, `bad token ${TOKEN}`);
    expect(logger.lines.length).toBeGreaterThan(3);
    for (const line of logger.lines) expect(line).not.toContain(TOKEN);
  });
});
