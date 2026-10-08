import type { EdgeConfig } from "./config";

export const EDGE_USER_AGENT = "aeroswarm-radar-edge/0.1.0";

export interface Handshake {
  url: string;
  headers: Record<string, string>;
}

/**
 * Build the WSS upgrade request.
 *
 * ASSUMPTION (contract with AeroSwarm `/ws/ingest/radar` is not in the task
 * spec): the source token travels as `Authorization: Bearer <token>` and the
 * feed as `X-Feed-Id`. Headers are used instead of query params on purpose:
 * URLs end up in proxy/access logs, headers do not. If AeroSwarm expects a
 * different shape, this is the only place to change.
 */
export function buildHandshake(
  config: Pick<EdgeConfig, "ingestUrl" | "sourceToken" | "feedId">,
): Handshake {
  return {
    url: config.ingestUrl,
    headers: {
      Authorization: `Bearer ${config.sourceToken}`,
      "X-Feed-Id": config.feedId,
      "User-Agent": EDGE_USER_AGENT,
    },
  };
}
