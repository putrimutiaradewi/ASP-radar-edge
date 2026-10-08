import { type Clock, systemClock } from "./clock";
import { type EdgeConfig, describeUrl } from "./config";
import type { Logger } from "./logger";
import { Stats, type StatsSnapshot } from "./stats";
import { UdpReceiver } from "./udp-receiver";
import type { WsConnector } from "./ws-connection";
import { WssForwarder } from "./wss-forwarder";

export interface EdgeDeps {
  logger: Logger;
  clock?: Clock;
  connector?: WsConnector;
  baseDelayMs?: number;
  stableConnectionMs?: number;
}

/** Wires UDP receiver -> WSS forwarder and owns stats + periodic summary. */
export class Edge {
  readonly stats = new Stats();
  private readonly receiver: UdpReceiver;
  private readonly forwarder: WssForwarder;
  private readonly clock: Clock;
  private readonly log: Logger;
  private summaryTimer: unknown = null;
  private lastSummary: StatsSnapshot = this.stats.snapshot();
  private boundPort: number | null = null;

  constructor(
    private readonly config: EdgeConfig,
    deps: EdgeDeps,
  ) {
    this.log = deps.logger;
    this.clock = deps.clock ?? systemClock;

    this.forwarder = new WssForwarder({
      config,
      stats: this.stats,
      logger: deps.logger,
      clock: this.clock,
      connector: deps.connector,
      baseDelayMs: deps.baseDelayMs,
      stableConnectionMs: deps.stableConnectionMs,
    });

    this.receiver = new UdpReceiver({
      host: config.udpListenHost,
      port: config.udpListenPort,
      recvBufferBytes: config.udpRecvBufferBytes,
      logger: deps.logger,
      onDatagram: (msg) => {
        this.stats.received++;
        this.forwarder.forward(msg);
      },
    });
  }

  /** Actual bound UDP port (differs from config when port 0 is used in tests). */
  get udpPort(): number | null {
    return this.boundPort;
  }

  get connected(): boolean {
    return this.forwarder.connected;
  }

  async start(): Promise<void> {
    const bound = await this.receiver.start();
    this.boundPort = bound.port;
    this.log.info("udp receiver listening", {
      host: bound.address,
      port: bound.port,
      recvBufferBytes: bound.recvBufferBytes,
    });
    this.log.info("edge starting", {
      ingest: describeUrl(this.config.ingestUrl),
      feedId: this.config.feedId,
      statsIntervalMs: this.config.statsIntervalMs,
      reconnectMaxMs: this.config.reconnectMaxMs,
    });
    this.forwarder.start();
    this.summaryTimer = this.clock.setInterval(() => this.logSummary(), this.config.statsIntervalMs);
  }

  async stop(): Promise<void> {
    if (this.summaryTimer !== null) {
      this.clock.clearInterval(this.summaryTimer);
      this.summaryTimer = null;
    }
    // Stop intake first so no new datagrams arrive while we close the socket.
    await this.receiver.stop();
    await this.forwarder.stop();
    this.logSummary("final");
  }

  private logSummary(kind: "periodic" | "final" = "periodic"): void {
    const now = this.stats.snapshot();
    const prev = this.lastSummary;
    this.lastSummary = now;
    this.log.info(kind === "final" ? "stats (final)" : "stats", {
      ...now,
      connected: this.forwarder.connected,
      interval: {
        received: now.received - prev.received,
        forwarded: now.forwarded - prev.forwarded,
        dropped: now.dropped - prev.dropped,
        reconnects: now.reconnects - prev.reconnects,
      },
    });
  }
}
