export interface StatsSnapshot {
  received: number;
  forwarded: number;
  dropped: number;
  reconnects: number;
}

/**
 * Plain monotonic counters (single-threaded, so no atomics needed).
 *
 * - received:   UDP datagrams read from the socket
 * - forwarded:  datagrams successfully handed to the WS socket as a binary frame
 * - dropped:    datagrams discarded (WSS down / backpressure / send error)
 * - reconnects: reconnect attempts started after a lost or failed connection
 *
 * Invariant (once in-flight sends settle): received = forwarded + dropped.
 */
export class Stats {
  received = 0;
  forwarded = 0;
  dropped = 0;
  reconnects = 0;

  snapshot(): StatsSnapshot {
    return {
      received: this.received,
      forwarded: this.forwarded,
      dropped: this.dropped,
      reconnects: this.reconnects,
    };
  }
}
