import { describe, expect, test } from "bun:test";
import { createLogger, REDACTED } from "./logger";

const TOKEN = 'srctk_ab"cd\\ef_123';

function make(level: "debug" | "info" | "warn" | "error" = "debug") {
  const lines: string[] = [];
  const log = createLogger({ level, secrets: [TOKEN], write: (l) => lines.push(l) });
  return { log, lines };
}

describe("logger redaction", () => {
  test("redacts the token in the message", () => {
    const { log, lines } = make();
    log.info(`connecting with ${TOKEN}`);
    expect(lines[0]).not.toContain(TOKEN);
    expect(lines[0]).toContain(REDACTED);
  });

  test("redacts the token in fields, including JSON-escaped form", () => {
    const { log, lines } = make();
    log.info("hdr", { authorization: `Bearer ${TOKEN}` });
    expect(lines[0]).not.toContain("srctk_ab");
    expect(lines[0]).toContain(REDACTED);
  });

  test("redacts the token inside Error messages", () => {
    const { log, lines } = make();
    log.error("boom", { err: new Error(`bad header Bearer ${TOKEN}`) });
    expect(lines[0]).not.toContain("srctk_ab");
  });

  test("honours level filtering", () => {
    const { log, lines } = make("warn");
    log.debug("d");
    log.info("i");
    log.warn("w");
    log.error("e");
    expect(lines.length).toBe(2);
  });

  test("emits valid JSON lines", () => {
    const { log, lines } = make();
    log.info("hello", { n: 1 });
    const parsed = JSON.parse(lines[0] as string);
    expect(parsed).toMatchObject({ level: "info", msg: "hello", n: 1 });
    expect(typeof parsed.time).toBe("string");
  });
});
