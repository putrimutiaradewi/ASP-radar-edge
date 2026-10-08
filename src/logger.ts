/**
 * Minimal JSON-lines logger with defense-in-depth secret redaction.
 *
 * The token is never *meant* to be logged, but redaction is applied to the
 * final serialized line so an accidental `logger.error("...", { err })` or a
 * library error message that echoes a header still cannot leak it.
 */

export type LogLevel = "debug" | "info" | "warn" | "error";

export interface Logger {
  debug(msg: string, fields?: Record<string, unknown>): void;
  info(msg: string, fields?: Record<string, unknown>): void;
  warn(msg: string, fields?: Record<string, unknown>): void;
  error(msg: string, fields?: Record<string, unknown>): void;
}

export interface LoggerOptions {
  level?: LogLevel;
  /** Strings that must never appear in output. */
  secrets?: readonly string[];
  /** Output sink; defaults to stdout (stderr for error). */
  write?: (line: string, level: LogLevel) => void;
  now?: () => Date;
}

const ORDER: Record<LogLevel, number> = { debug: 10, info: 20, warn: 30, error: 40 };
export const REDACTED = "[REDACTED]";

function serializeValue(_key: string, value: unknown): unknown {
  if (value instanceof Error) return { name: value.name, message: value.message };
  if (typeof value === "bigint") return value.toString();
  return value;
}

export function createLogger(opts: LoggerOptions = {}): Logger {
  const min = ORDER[opts.level ?? "info"];
  const now = opts.now ?? (() => new Date());
  const write =
    opts.write ??
    ((line: string, level: LogLevel) => {
      (level === "error" ? console.error : console.log)(line);
    });

  // Redact both the raw secret and its JSON-escaped form (quotes, backslashes...).
  const needles = new Set<string>();
  for (const s of opts.secrets ?? []) {
    if (!s) continue;
    needles.add(s);
    needles.add(JSON.stringify(s).slice(1, -1));
  }

  const redact = (line: string): string => {
    let out = line;
    for (const n of needles) out = out.split(n).join(REDACTED);
    return out;
  };

  const log = (level: LogLevel, msg: string, fields?: Record<string, unknown>): void => {
    if (ORDER[level] < min) return;
    const line = JSON.stringify(
      { time: now().toISOString(), level, msg, ...fields },
      serializeValue,
    );
    write(redact(line), level);
  };

  return {
    debug: (m, f) => log("debug", m, f),
    info: (m, f) => log("info", m, f),
    warn: (m, f) => log("warn", m, f),
    error: (m, f) => log("error", m, f),
  };
}
