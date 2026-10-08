import WebSocket from "ws";
import { normalizeError } from "./diagnostics";
import type { Handshake } from "./handshake";

/**
 * Narrow seam over the WebSocket client. The forwarder only depends on this,
 * which keeps the reconnect/backpressure state machine unit-testable with a
 * fake and keeps `ws` details (events, readyState constants) in one file.
 */
export interface WsHandlers {
  onOpen(): void;
  onClose(code: number, reason: string): void;
  onError(err: Error): void;
  onPong(): void;
  onServerMessage(text: string): void;
}

export interface WsConnection {
  readonly isOpen: boolean;
  /** Bytes queued in the socket but not yet written to the network. */
  readonly bufferedAmount: number;
  /** Send exactly these bytes as ONE binary frame. `cb` reports flush success/failure. */
  sendBinary(data: Uint8Array, cb: (err?: Error | null) => void): void;
  ping(): void;
  close(code?: number, reason?: string): void;
  terminate(): void;
}

export type WsConnector = (handshake: Handshake, handlers: WsHandlers) => WsConnection;

export const HANDSHAKE_TIMEOUT_MS = 10_000;

export const connectWs: WsConnector = (handshake, handlers) => {
  const ws = new WebSocket(handshake.url, {
    headers: handshake.headers,
    handshakeTimeout: HANDSHAKE_TIMEOUT_MS,
    // ASTERIX datagrams are small; compression costs CPU/latency for no gain.
    perMessageDeflate: false,
    // rejectUnauthorized stays at its secure default (true): never disable TLS verification.
  });

  ws.on("open", () => handlers.onOpen());
  ws.on("close", (code, reason) => handlers.onClose(code, reason.toString("utf8")));
  ws.on("error", (err: unknown) => handlers.onError(normalizeError(err)));
  ws.on("pong", () => handlers.onPong());
  ws.on("message", (data, isBinary) => {
    if (!isBinary) handlers.onServerMessage(data.toString("utf8"));
  });

  return {
    get isOpen() {
      return ws.readyState === WebSocket.OPEN;
    },
    get bufferedAmount() {
      return ws.bufferedAmount;
    },
    sendBinary(data, cb) {
      ws.send(data, { binary: true }, cb);
    },
    ping() {
      ws.ping();
    },
    close(code, reason) {
      ws.close(code, reason);
    },
    terminate() {
      ws.terminate();
    },
  };
};
