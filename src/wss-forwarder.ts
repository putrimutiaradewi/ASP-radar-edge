import { BACKOFF_BASE_MS, backoffDelayMs } from "./backoff";
import { type Clock, systemClock } from "./clock";
import type { EdgeConfig } from "./config";
import { connectionHint } from "./diagnostics";
import { buildHandshake } from "./handshake";
import type { Logger } from "./logger";
import type { Stats } from "./stats";
import { connectWs, type WsConnection, type WsConnector } from "./ws-connection";

/** A connection that stayed up this long is considered healthy -> backoff resets. */
export const STABLE_CONNECTION_MS = 10_000;
const STOP_GRACE_MS = 1_000;

export type ForwarderState = "idle" | "connecting" | "open" | "backoff" | "stopped";

export interface ForwarderOptions {
  config: Pick<
    EdgeConfig,
    "ingestUrl" | "sourceToken" | "feedId" | "reconnectMaxMs" | "maxBufferedBytes" | "pingIntervalMs"
  >;
  stats: Stats;
  logger: Logger;
  clock?: Clock;
  connector?: WsConnector;
  /** First reconnect delay (spec: 1s). Overridable for tests only. */
  baseDelayMs?: number;
  stableConnectionMs?: number;
}

/**
 * Owns the single outbound WSS connection.
 *
 * Design rules:
 *  - 1 UDP datagram -> exactly 1 binary frame, bytes untouched.
 *  - NO application-level queue. If the socket is not OPEN, or its own send
 *    buffer is already above `maxBufferedBytes`, the datagram is dropped and
 *    counted. Memory is therefore bounded by (maxBufferedBytes + one frame).
 *  - Radar tracks are real-time data: a stale replay after an outage is
 *    worse than a gap, so nothing is ever replayed.
 */
export class WssForwarder {
  private readonly cfg: ForwarderOptions["config"];
  private readonly stats: Stats;
  private readonly log: Logger;
  private readonly clock: Clock;
  private readonly connector: WsConnector;
  private readonly baseDelayMs: number;
  private readonly stableMs: number;

  private state: ForwarderState = "idle";
  private conn: WsConnection | null = null;
  /** Bumped per connection so late events from a dead socket are ignored. */
  private generation = 0;
  private attempt = 0;
  private openedAt = 0;
  private reconnectTimer: unknown = null;
  private pingTimer: unknown = null;
  private awaitingPong = false;
  private stopWaiter: (() => void) | null = null;

  constructor(opts: ForwarderOptions) {
    this.cfg = opts.config;
    this.stats = opts.stats;
    this.log = opts.logger;
    this.clock = opts.clock ?? systemClock;
    this.connector = opts.connector ?? connectWs;
    this.baseDelayMs = opts.baseDelayMs ?? BACKOFF_BASE_MS;
    this.stableMs = opts.stableConnectionMs ?? STABLE_CONNECTION_MS;
  }

  get currentState(): ForwarderState {
    return this.state;
  }

  get connected(): boolean {
    return this.state === "open";
  }

  start(): void {
    if (this.state !== "idle") return;
    this.connect();
  }

  /**
   * Forward one datagram. Never throws, never queues.
   * @returns true if handed to the socket, false if dropped.
   */
  forward(datagram: Uint8Array): boolean {
    const conn = this.conn;
    if (this.state !== "open" || !conn || !conn.isOpen) {
      this.stats.dropped++;
      return false;
    }
    if (conn.bufferedAmount > this.cfg.maxBufferedBytes) {
      this.stats.dropped++;
      return false;
    }
    try {
      conn.sendBinary(datagram, (err) => {
        if (err) this.stats.dropped++;
        else this.stats.forwarded++;
      });
      return true;
    } catch {
      this.stats.dropped++;
      return false;
    }
  }

  async stop(): Promise<void> {
    if (this.state === "stopped") return;
    const conn = this.conn;
    this.state = "stopped";
    this.clearTimers();
    this.conn = null;
    if (!conn) return;

    // Resolve as soon as the socket reports closed; otherwise force-terminate
    // after a short grace so shutdown never hangs on an unresponsive peer.
    await new Promise<void>((resolve) => {
      const grace = this.clock.setTimeout(() => {
        this.stopWaiter = null;
        try {
          conn.terminate();
        } catch {
          /* ignore */
        }
        resolve();
      }, STOP_GRACE_MS);
      this.stopWaiter = () => {
        this.clock.clearTimeout(grace);
        this.stopWaiter = null;
        resolve();
      };
      try {
        conn.close(1001, "edge shutting down");
      } catch {
        this.stopWaiter();
      }
    });
  }

  // ---------------------------------------------------------------- internals

  private connect(): void {
    this.state = "connecting";
    const gen = ++this.generation;
    const handshake = buildHandshake(this.cfg);
    this.log.info("connecting to AeroSwarm ingest", { attempt: this.attempt });

    let conn: WsConnection;
    try {
      conn = this.connector(handshake, {
        onOpen: () => gen === this.generation && this.handleOpen(),
        onClose: (code, reason) => gen === this.generation && this.handleClose(code, reason),
        onError: (err) => gen === this.generation && this.handleError(err),
        onPong: () => {
          if (gen === this.generation) this.awaitingPong = false;
        },
        onServerMessage: (text) => {
          if (gen === this.generation) this.log.debug("server message", { text: text.slice(0, 200) });
        },
      });
    } catch (err) {
      // Synchronous constructor failure (e.g. malformed URL) -> treat as a failed attempt.
      this.log.error("failed to create WebSocket", { err });
      this.scheduleReconnect();
      return;
    }
    this.conn = conn;
  }

  private handleOpen(): void {
    this.state = "open";
    this.openedAt = this.clock.now();
    this.awaitingPong = false;
    this.log.info("connected to AeroSwarm ingest", { feedId: this.cfg.feedId });
    this.pingTimer = this.clock.setInterval(() => this.heartbeat(), this.cfg.pingIntervalMs);
  }

  private handleError(err: Error): void {
    if (this.state === "stopped") return;
    // 'close' always follows 'error' in ws; reconnect is scheduled there.
    this.log.warn("websocket error", { err, hint: connectionHint(err.message) });
  }

  private handleClose(code: number, reason: string): void {
    const wasOpen = this.state === "open";
    const uptime = wasOpen ? this.clock.now() - this.openedAt : 0;
    this.clearPing();
    if (this.state === "stopped") {
      this.stopWaiter?.();
      return;
    }
    this.conn = null;

    if (wasOpen && uptime >= this.stableMs) this.attempt = 0;
    this.log.warn("disconnected from AeroSwarm ingest", {
      code,
      reason: reason.slice(0, 120),
      wasConnected: wasOpen,
      uptimeMs: wasOpen ? uptime : undefined,
    });
    this.scheduleReconnect();
  }

  private scheduleReconnect(): void {
    if (this.state === "stopped") return;
    const delay = backoffDelayMs(this.attempt, this.cfg.reconnectMaxMs, this.baseDelayMs);
    this.attempt++;
    this.state = "backoff";
    this.log.info("reconnecting", { inMs: delay });
    this.reconnectTimer = this.clock.setTimeout(() => {
      this.reconnectTimer = null;
      if (this.state !== "backoff") return;
      this.stats.reconnects++;
      this.connect();
    }, delay);
  }

  /** Detect half-open connections (NAT/idle-timeout) that TCP alone would hide for minutes. */
  private heartbeat(): void {
    const conn = this.conn;
    if (this.state !== "open" || !conn) return;
    if (this.awaitingPong) {
      this.log.warn("no pong from server; terminating connection", {
        pingIntervalMs: this.cfg.pingIntervalMs,
      });
      conn.terminate();
      return;
    }
    this.awaitingPong = true;
    try {
      conn.ping();
    } catch {
      conn.terminate();
    }
  }

  private clearPing(): void {
    if (this.pingTimer !== null) {
      this.clock.clearInterval(this.pingTimer);
      this.pingTimer = null;
    }
  }

  private clearTimers(): void {
    this.clearPing();
    if (this.reconnectTimer !== null) {
      this.clock.clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }
}
