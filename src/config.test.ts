import { describe, expect, test } from "bun:test";
import { ConfigError, describeUrl, loadConfig } from "./config";

const valid = {
  AEROSWARM_INGEST_URL: "wss://aeroswarm.example.com/ws/ingest/radar",
  SOURCE_TOKEN: "srctk_super_secret_value",
  FEED_ID: "radar-b",
};

function problemsOf(env: Record<string, string | undefined>): string[] {
  try {
    loadConfig(env);
  } catch (e) {
    if (e instanceof ConfigError) return e.problems;
    throw e;
  }
  return [];
}

describe("loadConfig", () => {
  test("applies spec defaults", () => {
    const c = loadConfig(valid);
    expect(c.udpListenHost).toBe("0.0.0.0");
    expect(c.udpListenPort).toBe(13217);
    expect(c.reconnectMaxMs).toBe(30_000);
    expect(c.statsIntervalMs).toBe(30_000);
    expect(c.feedId).toBe("radar-b");
  });

  test("reads overrides", () => {
    const c = loadConfig({
      ...valid,
      UDP_LISTEN_HOST: "10.0.0.5",
      UDP_LISTEN_PORT: "4000",
      RECONNECT_MAX_MS: "10000",
      STATS_INTERVAL_MS: "5000",
    });
    expect(c).toMatchObject({
      udpListenHost: "10.0.0.5",
      udpListenPort: 4000,
      reconnectMaxMs: 10_000,
      statsIntervalMs: 5000,
    });
  });

  test("requires URL, token and feed id, and reports them all at once", () => {
    const p = problemsOf({});
    expect(p.some((x) => x.includes("AEROSWARM_INGEST_URL"))).toBe(true);
    expect(p.some((x) => x.includes("SOURCE_TOKEN"))).toBe(true);
    expect(p.some((x) => x.includes("FEED_ID"))).toBe(true);
  });

  test("rejects non-TLS URLs except on loopback", () => {
    expect(problemsOf({ ...valid, AEROSWARM_INGEST_URL: "ws://aeroswarm.example.com/x" })).not.toEqual([]);
    expect(problemsOf({ ...valid, AEROSWARM_INGEST_URL: "https://aeroswarm.example.com/x" })).not.toEqual([]);
    expect(problemsOf({ ...valid, AEROSWARM_INGEST_URL: "ws://127.0.0.1:9000/x" })).toEqual([]);
    expect(problemsOf({ ...valid, AEROSWARM_INGEST_URL: "ws://localhost:9000/x" })).toEqual([]);
  });

  test("rejects credentials embedded in the URL", () => {
    expect(problemsOf({ ...valid, AEROSWARM_INGEST_URL: "wss://u:p@aeroswarm.example.com/x" })).not.toEqual([]);
  });

  test("validates numeric ranges", () => {
    expect(problemsOf({ ...valid, UDP_LISTEN_PORT: "0" })).not.toEqual([]);
    expect(problemsOf({ ...valid, UDP_LISTEN_PORT: "70000" })).not.toEqual([]);
    expect(problemsOf({ ...valid, UDP_LISTEN_PORT: "abc" })).not.toEqual([]);
    expect(problemsOf({ ...valid, RECONNECT_MAX_MS: "5" })).not.toEqual([]);
  });

  test("error text never contains the token or full URL", () => {
    const err = (() => {
      try {
        loadConfig({ ...valid, UDP_LISTEN_PORT: "nope", AEROSWARM_INGEST_URL: "ws://evil.example.com/?t=1" });
      } catch (e) {
        return e as Error;
      }
      return new Error("did not throw");
    })();
    expect(err.message).not.toContain(valid.SOURCE_TOKEN);
    expect(err.message).not.toContain("evil.example.com");
  });
});

describe("describeUrl", () => {
  test("strips query string, hash and userinfo", () => {
    expect(describeUrl("wss://user:pw@host.example.com:8443/ws/ingest/radar?token=SECRET#frag")).toBe(
      "wss://host.example.com:8443/ws/ingest/radar",
    );
  });
  test("handles garbage", () => {
    expect(describeUrl("not a url")).toBe("<invalid-url>");
  });
});

describe("udp receive buffer", () => {
  test("defaults to 4 MiB and is overridable/validated", () => {
    expect(loadConfig(valid).udpRecvBufferBytes).toBe(4_194_304);
    expect(loadConfig({ ...valid, UDP_RCVBUF_BYTES: "1048576" }).udpRecvBufferBytes).toBe(1_048_576);
    expect(problemsOf({ ...valid, UDP_RCVBUF_BYTES: "10" })).not.toEqual([]);
  });
});
