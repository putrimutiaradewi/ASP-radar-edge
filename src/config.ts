/**
 * Environment -> typed config.
 *
 * Validation collects *all* problems before failing so the operator fixes the
 * .env in one pass. Error messages never echo the value of SOURCE_TOKEN (or
 * any URL), only the variable name.
 */

export interface EdgeConfig {
  udpListenHost: string;
  udpListenPort: number;
  ingestUrl: string;
  sourceToken: string;
  feedId: string;
  reconnectMaxMs: number;
  statsIntervalMs: number;
  /** Drop (instead of queue) when the WS send buffer already holds this many bytes. */
  maxBufferedBytes: number;
  /** WS ping interval; a missed pong terminates the socket so we reconnect. */
  pingIntervalMs: number;
  /** Requested SO_RCVBUF for the UDP socket (headroom against event-loop stalls). */
  udpRecvBufferBytes: number;
  logLevel: "debug" | "info" | "warn" | "error";
}

export class ConfigError extends Error {
  readonly problems: string[];
  constructor(problems: string[]) {
    super(`Invalid configuration:\n${problems.map((p) => `  - ${p}`).join("\n")}`);
    this.name = "ConfigError";
    this.problems = problems;
  }
}

type Env = Record<string, string | undefined>;

const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]", "::1"]);
const LOG_LEVELS = ["debug", "info", "warn", "error"] as const;

function readInt(
  env: Env,
  name: string,
  fallback: number,
  min: number,
  max: number,
  problems: string[],
): number {
  const raw = env[name]?.trim();
  if (raw === undefined || raw === "") return fallback;
  if (!/^\d+$/.test(raw)) {
    problems.push(`${name} must be an integer`);
    return fallback;
  }
  const n = Number(raw);
  if (n < min || n > max) {
    problems.push(`${name} must be between ${min} and ${max}`);
    return fallback;
  }
  return n;
}

export function loadConfig(env: Env): EdgeConfig {
  const problems: string[] = [];

  const udpListenHost = env.UDP_LISTEN_HOST?.trim() || "0.0.0.0";
  const udpListenPort = readInt(env, "UDP_LISTEN_PORT", 13217, 1, 65535, problems);

  const ingestUrl = env.AEROSWARM_INGEST_URL?.trim() ?? "";
  if (!ingestUrl) {
    problems.push("AEROSWARM_INGEST_URL is required (wss://<aeroswarm-domain>/ws/ingest/radar)");
  } else {
    try {
      const u = new URL(ingestUrl);
      if (u.username || u.password) {
        problems.push("AEROSWARM_INGEST_URL must not contain credentials; use SOURCE_TOKEN");
      }
      if (u.protocol === "ws:") {
        // Plain ws is only tolerated for local development/testing.
        if (!LOOPBACK_HOSTS.has(u.hostname)) {
          problems.push("AEROSWARM_INGEST_URL must use wss:// (ws:// is only allowed for localhost)");
        }
      } else if (u.protocol !== "wss:") {
        problems.push("AEROSWARM_INGEST_URL must use the wss:// scheme");
      }
    } catch {
      problems.push("AEROSWARM_INGEST_URL is not a valid URL");
    }
  }

  const sourceToken = env.SOURCE_TOKEN?.trim() ?? "";
  if (!sourceToken) problems.push("SOURCE_TOKEN is required");

  const feedId = env.FEED_ID?.trim() ?? "";
  if (!feedId) problems.push("FEED_ID is required");

  const reconnectMaxMs = readInt(env, "RECONNECT_MAX_MS", 30_000, 1_000, 3_600_000, problems);
  const statsIntervalMs = readInt(env, "STATS_INTERVAL_MS", 30_000, 100, 3_600_000, problems);
  const maxBufferedBytes = readInt(env, "MAX_BUFFERED_BYTES", 1_048_576, 1, 1_073_741_824, problems);
  const pingIntervalMs = readInt(env, "PING_INTERVAL_MS", 15_000, 1_000, 600_000, problems);

  const udpRecvBufferBytes = readInt(env, "UDP_RCVBUF_BYTES", 4_194_304, 4_096, 268_435_456, problems);

  const rawLevel = (env.LOG_LEVEL?.trim().toLowerCase() || "info") as EdgeConfig["logLevel"];
  if (!LOG_LEVELS.includes(rawLevel)) {
    problems.push(`LOG_LEVEL must be one of ${LOG_LEVELS.join(", ")}`);
  }

  if (problems.length > 0) throw new ConfigError(problems);

  return {
    udpListenHost,
    udpListenPort,
    ingestUrl,
    sourceToken,
    feedId,
    reconnectMaxMs,
    statsIntervalMs,
    maxBufferedBytes,
    pingIntervalMs,
    udpRecvBufferBytes,
    logLevel: rawLevel,
  };
}

/** Log-safe form of a URL: origin + path only (drops query string / userinfo / hash). */
export function describeUrl(url: string): string {
  try {
    const u = new URL(url);
    return `${u.protocol}//${u.host}${u.pathname}`;
  } catch {
    return "<invalid-url>";
  }
}
