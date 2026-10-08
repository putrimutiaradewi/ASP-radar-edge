/**
 * Stand-in for AeroSwarm's /ws/ingest/radar, for local smoke tests.
 *
 *   MOCK_TOKEN=secret bun run scripts/mock-ingest-server.ts [port]
 *
 * Accepts upgrades at /ws/ingest/radar. If MOCK_TOKEN is set, requires
 * `Authorization: Bearer <MOCK_TOKEN>`. Prints one line per binary frame.
 */
import { WebSocketServer } from "ws";

const port = Number(process.argv[2] ?? 9000);
const token = process.env.MOCK_TOKEN;
let frames = 0;
let bytes = 0;

const wss = new WebSocketServer({
  host: "127.0.0.1",
  port,
  path: "/ws/ingest/radar",
  verifyClient: (info, done) => {
    const ok = !token || info.req.headers.authorization === `Bearer ${token}`;
    console.log(`upgrade feed=${info.req.headers["x-feed-id"] ?? "-"} auth=${ok ? "ok" : "REJECTED"}`);
    if (ok) done(true);
    else done(false, 401, "Unauthorized");
  },
});

wss.on("connection", (ws) => {
  console.log("client connected");
  ws.on("message", (data, isBinary) => {
    const buf = data as Buffer;
    frames++;
    bytes += buf.length;
    console.log(
      `#${frames} ${isBinary ? "binary" : "TEXT(!)"} len=${buf.length} head=${buf.subarray(0, 12).toString("hex")}`,
    );
  });
  ws.on("close", (code) => console.log(`client closed code=${code} (total frames=${frames} bytes=${bytes})`));
});
wss.on("listening", () => console.log(`mock ingest listening on ws://127.0.0.1:${port}/ws/ingest/radar`));
