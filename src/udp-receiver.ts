import dgram from "node:dgram";
import type { Logger } from "./logger";

export interface UdpReceiverOptions {
  host: string;
  port: number;
  /** Requested SO_RCVBUF in bytes (best effort; the OS may clamp it). */
  recvBufferBytes: number;
  /** Called synchronously per datagram. `msg` is a fresh Buffer owned by the callee. */
  onDatagram(msg: Buffer): void;
  logger: Logger;
}

export interface BoundAddress {
  address: string;
  port: number;
  /** Receive buffer actually granted by the OS (undefined if it could not be read). */
  recvBufferBytes?: number;
}

/** Listens for raw radar datagrams. Does no parsing: bytes go out exactly as they came in. */
export class UdpReceiver {
  private socket: dgram.Socket | null = null;

  constructor(private readonly opts: UdpReceiverOptions) {}

  start(): Promise<BoundAddress> {
    const { host, port, logger } = this.opts;
    const socket = dgram.createSocket({ type: host.includes(":") ? "udp6" : "udp4" });
    this.socket = socket;

    socket.on("message", (msg) => {
      try {
        this.opts.onDatagram(msg);
      } catch (err) {
        // A bug downstream must never take the radar feed down.
        logger.error("datagram handler threw", { err });
      }
    });

    return new Promise<BoundAddress>((resolve, reject) => {
      const onStartupError = (err: Error): void => reject(err);
      socket.once("error", onStartupError);
      socket.bind(port, host, () => {
        socket.off("error", onStartupError);
        // After binding, socket errors are logged but non-fatal.
        socket.on("error", (err) => logger.error("udp socket error", { err }));
        const recvBufferBytes = this.tuneRecvBuffer(socket);
        const addr = socket.address();
        resolve({ address: addr.address, port: addr.port, recvBufferBytes });
      });
    });
  }

  /**
   * The default receive buffer (~208 KiB on Linux) holds only ~250 small datagrams.
   * If the event loop stalls (GC, TLS handshake on reconnect, a busy PC) the kernel
   * drops excess packets silently - they never reach our counters. A bigger buffer
   * is cheap insurance. Failure here is non-fatal.
   */
  private tuneRecvBuffer(socket: dgram.Socket): number | undefined {
    const { recvBufferBytes: wanted, logger } = this.opts;
    try {
      socket.setRecvBufferSize(wanted);
    } catch (err) {
      logger.warn("could not set UDP receive buffer; using OS default", { err });
    }
    try {
      const actual = socket.getRecvBufferSize();
      if (actual < wanted) {
        logger.warn("OS granted a smaller UDP receive buffer than requested", {
          requested: wanted,
          granted: actual,
          hint: "on Linux raise net.core.rmem_max",
        });
      }
      return actual;
    } catch {
      return undefined;
    }
  }

  stop(): Promise<void> {
    const socket = this.socket;
    this.socket = null;
    if (!socket) return Promise.resolve();
    return new Promise((resolve) => {
      try {
        socket.close(() => resolve());
      } catch {
        resolve(); // already closed
      }
    });
  }
}
