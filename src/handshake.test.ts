import { expect, test } from "bun:test";
import { buildHandshake } from "./handshake";

test("token goes in the Authorization header, never in the URL", () => {
  const h = buildHandshake({
    ingestUrl: "wss://aeroswarm.example.com/ws/ingest/radar",
    sourceToken: "tok_123",
    feedId: "radar-b",
  });
  expect(h.url).toBe("wss://aeroswarm.example.com/ws/ingest/radar");
  expect(h.url).not.toContain("tok_123");
  expect(h.headers.Authorization).toBe("Bearer tok_123");
  expect(h.headers["X-Feed-Id"]).toBe("radar-b");
});
