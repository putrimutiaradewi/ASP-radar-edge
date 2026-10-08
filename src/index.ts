import { ConfigError, loadConfig } from "./config";
import { Edge } from "./edge";
import { createLogger } from "./logger";

async function main(): Promise<void> {
  let config: ReturnType<typeof loadConfig>;
  try {
    config = loadConfig(process.env);
  } catch (err) {
    if (err instanceof ConfigError) {
      console.error(err.message);
      console.error("See .env.example for the full list of variables.");
      process.exit(2);
    }
    throw err;
  }

  const logger = createLogger({ level: config.logLevel, secrets: [config.sourceToken] });
  const edge = new Edge(config, { logger });

  let shuttingDown = false;
  const shutdown = async (signal: string): Promise<void> => {
    if (shuttingDown) return;
    shuttingDown = true;
    logger.info("shutting down", { signal });
    try {
      await edge.stop();
    } finally {
      process.exit(0);
    }
  };
  process.on("SIGINT", () => void shutdown("SIGINT"));
  process.on("SIGTERM", () => void shutdown("SIGTERM"));

  // A crash is better than a silently wedged edge: let the service manager restart us.
  process.on("uncaughtException", (err) => {
    logger.error("uncaught exception", { err });
    process.exit(1);
  });
  process.on("unhandledRejection", (reason) => {
    logger.error("unhandled rejection", { err: reason });
    process.exit(1);
  });

  try {
    await edge.start();
  } catch (err) {
    // Most commonly EADDRINUSE / EACCES / EADDRNOTAVAIL on the UDP bind.
    logger.error("failed to start", { err });
    process.exit(1);
  }
}

void main();
