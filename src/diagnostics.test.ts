import { describe, expect, test } from "bun:test";
import { connectionHint, normalizeError } from "./diagnostics";

describe("normalizeError", () => {
  test("passes real Errors through untouched", () => {
    const e = new Error("connect ECONNREFUSED 127.0.0.1:9");
    expect(normalizeError(e)).toBe(e);
  });

  test("regression: Bun ErrorEvent keeps its message (was logged as {isTrusted:true})", () => {
    // Mimics Bun: `message` is a prototype getter, only `isTrusted` is an own property.
    class ErrorEventLike {
      isTrusted = true;
      get message() {
        return "WebSocket connection to 'wss://x/y' failed: Expected 101 status code";
      }
      get type() {
        return "error";
      }
    }
    const err = normalizeError(new ErrorEventLike());
    expect(err).toBeInstanceOf(Error);
    expect(err.message).toContain("Expected 101");
    expect(JSON.stringify({ err: { message: err.message } })).toContain("Expected 101");
  });

  test("unwraps an inner error and degrades gracefully on junk", () => {
    const inner = new Error("inner");
    expect(normalizeError({ error: inner })).toBe(inner);
    expect(normalizeError({ type: "error" }).message).toBe("websocket error event");
    expect(normalizeError(undefined).message).toBe("unknown websocket error");
    expect(normalizeError("boom").message).toBe("unknown websocket error");
  });
});

describe("connectionHint", () => {
  test("flags a rejected upgrade (Bun and Node phrasings)", () => {
    expect(connectionHint("failed: Expected 101 status code")).toContain("SOURCE_TOKEN");
    expect(connectionHint("Unexpected server response: 401")).toContain("SOURCE_TOKEN");
  });
  test("flags unreachable server", () => {
    expect(connectionHint("connect ECONNREFUSED 10.0.0.1:443")).toContain("cannot reach");
    expect(connectionHint("getaddrinfo ENOTFOUND host.example")).toContain("cannot reach");
    expect(connectionHint("WebSocket connection failed: Failed to connect")).toContain("cannot reach");
  });
  test("flags TLS problems", () => {
    expect(connectionHint("unable to verify the first certificate")).toContain("TLS");
    expect(connectionHint("self-signed certificate")).toContain("TLS");
  });
  test("returns nothing for unknown failures", () => {
    expect(connectionHint("something else entirely")).toBeUndefined();
  });
});
