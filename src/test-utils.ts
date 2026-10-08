import type { Clock } from "./clock";
import type { Handshake } from "./handshake";
import { createLogger, type Logger } from "./logger";
import type { WsConnection, WsConnector, WsHandlers } from "./ws-connection";

/** Deterministic clock: time only moves when the test calls advance(). */
export class FakeClock implements Clock {
  private t = 0;
  private nextId = 1;
  private timers = new Map<number, { at: number; fn: () => void; every?: number }>();

  now(): number {
    return this.t;
  }
  setTimeout(fn: () => void, ms: number): unknown {
    const id = this.nextId++;
    this.timers.set(id, { at: this.t + ms, fn });
    return id;
  }
  clearTimeout(h: unknown): void {
    this.timers.delete(h as number);
  }
  setInterval(fn: () => void, ms: number): unknown {
    const id = this.nextId++;
    this.timers.set(id, { at: this.t + ms, fn, every: ms });
    return id;
  }
  clearInterval(h: unknown): void {
    this.timers.delete(h as number);
  }
  get pendingTimers(): number {
    return this.timers.size;
  }

  advance(ms: number): void {
    const target = this.t + ms;
    for (;;) {
      let nextId: number | null = null;
      let nextAt = Number.POSITIVE_INFINITY;
      for (const [id, tm] of this.timers) {
        if (tm.at <= target && tm.at < nextAt) {
          nextAt = tm.at;
          nextId = id;
        }
      }
      if (nextId === null) break;
      const tm = this.timers.get(nextId);
      if (!tm) break;
      this.t = tm.at;
      if (tm.every) tm.at += tm.every;
      else this.timers.delete(nextId);
      tm.fn();
    }
    this.t = target;
  }
}

export class FakeConn implements WsConnection {
  isOpen = false;
  bufferedAmount = 0;
  frames: Uint8Array[] = [];
  pings = 0;
  closed = false;
  terminated = false;
  /** When set, sendBinary reports this error instead of success. */
  sendError: Error | null = null;
  /** When true, sendBinary throws synchronously (socket in a bad state). */
  sendThrows = false;

  constructor(
    readonly handshake: Handshake,
    readonly handlers: WsHandlers,
  ) {}

  sendBinary(data: Uint8Array, cb: (err?: Error | null) => void): void {
    if (this.sendThrows) throw new Error("socket not open");
    this.frames.push(data);
    cb(this.sendError);
  }
  ping(): void {
    this.pings++;
  }
  close(code = 1000, reason = ""): void {
    this.closed = true;
    this.isOpen = false;
    // Peer acknowledges the close handshake asynchronously in real life; tests trigger it explicitly.
    this.lastCloseArgs = { code, reason };
  }
  lastCloseArgs: { code: number; reason: string } | null = null;
  terminate(): void {
    this.terminated = true;
    this.isOpen = false;
    this.handlers.onClose(1006, "");
  }

  // ---- test controls (simulate network / server) ----
  open(): void {
    this.isOpen = true;
    this.handlers.onOpen();
  }
  serverClose(code = 1006, reason = ""): void {
    this.isOpen = false;
    this.handlers.onClose(code, reason);
  }
  /** Connection attempt fails: ws emits 'error' then 'close'. */
  failConnect(message = "ECONNREFUSED"): void {
    this.handlers.onError(new Error(message));
    this.handlers.onClose(1006, "");
  }
  pong(): void {
    this.handlers.onPong();
  }
}

export function createFakeConnector(): { connector: WsConnector; conns: FakeConn[] } {
  const conns: FakeConn[] = [];
  const connector: WsConnector = (handshake, handlers) => {
    const c = new FakeConn(handshake, handlers);
    conns.push(c);
    return c;
  };
  return { connector, conns };
}

export interface CapturedLogger extends Logger {
  lines: string[];
  /** Parsed JSON log records, in order. */
  records(): Array<Record<string, unknown>>;
}

/** Logger that records every serialized line (after redaction) for assertions. */
export function captureLogger(secrets: string[] = []): CapturedLogger {
  const lines: string[] = [];
  const logger = createLogger({ write: (l) => lines.push(l), secrets, level: "debug" });
  return Object.assign(logger, {
    lines,
    records: () => lines.map((l) => JSON.parse(l) as Record<string, unknown>),
  });
}
