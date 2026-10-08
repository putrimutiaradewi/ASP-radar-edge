/**
 * Turn runtime-specific WebSocket failures into something an operator can act on.
 *
 * Why this exists: Bun's `ws` emits an `ErrorEvent` (message lives on a prototype
 * getter, so naive JSON logging prints only `{"isTrusted":true}`), while Node emits
 * a real `Error`. Bun also reports a rejected upgrade (e.g. 401) only as
 * "Expected 101 status code", without the status code.
 */

export function normalizeError(e: unknown): Error {
  if (e instanceof Error) return e;
  const o = (e ?? {}) as { message?: unknown; error?: unknown; type?: unknown };
  if (o.error instanceof Error) return o.error;
  if (typeof o.message === "string" && o.message) return new Error(o.message);
  if (typeof o.type === "string") return new Error(`websocket ${o.type} event`);
  return new Error("unknown websocket error");
}

export function connectionHint(message: string): string | undefined {
  if (/expected 101|unexpected server response|\b40[13]\b/i.test(message)) {
    return "server rejected the WebSocket upgrade: verify SOURCE_TOKEN, FEED_ID and the URL path";
  }
  if (/certificate|\bTLS\b|\bSSL\b|self[- ]signed|CERT_|UNABLE_TO_VERIFY/i.test(message)) {
    return "TLS verification failed: check the PC clock and CA trust (verification is never disabled)";
  }
  if (/ECONNREFUSED|ENOTFOUND|EAI_AGAIN|ETIMEDOUT|EHOSTUNREACH|failed to connect|timed out/i.test(message)) {
    return "cannot reach the server: check the URL, DNS, firewall and outbound port 443";
  }
  return undefined;
}
