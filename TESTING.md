# Radar Edge Testing

## Environment

* Service: `radar-edge`
* UDP: `0.0.0.0:13217`
* Mock WSS: `ws://127.0.0.1:9000/ws/ingest/radar`
* Test data: `sample/data/130320261451.pcapng`
* Replay speed: `max`

## 1. Build & Automated Tests

* `bun test` → PASS
* `bun run typecheck` → PASS
* `bun run build` → PASS

## 2. UDP → WSS Forwarding

PCAP replay:

```text
parsed 11554 UDP packets
round 1: sent 11554 datagrams
```

Edge stats:

```text
received: 11554
forwarded: 11554
dropped: 0
reconnects: 0
connected: true
```

Mock WSS received binary frames up to:

```text
#11554 binary
```

Result: **PASS**

## 3. WSS Disconnect & Reconnect

Mock WSS was stopped while Edge was running.

Observed reconnect backoff:

```text
1s → 2s → 4s → 8s → 16s → 30s
```

Edge remained running while WSS was unavailable.

Result: **PASS**

## 4. Packet Drop While WSS Is Down

PCAP replay while WSS was unavailable:

```text
received: 23108
forwarded: 11554
dropped: 11554
connected: false
```

The second batch of 11,554 packets was dropped immediately instead of being buffered.

Result: **PASS**

## 5. Reconnect Recovery

Mock WSS was started again.

Observed:

```text
connected to AeroSwarm ingest
```

Result: **PASS**

## Local Test Summary

| Test                       | Result |
| -------------------------- | ------ |
| UDP receiver               | PASS   |
| UDP → WSS forwarding       | PASS   |
| Binary WebSocket frame     | PASS   |
| Raw payload forwarding     | PASS   |
| WSS disconnect handling    | PASS   |
| Reconnect backoff          | PASS   |
| Packet drop while WSS down | PASS   |
| No unlimited buffering     | PASS   |
| Reconnect recovery         | PASS   |
| Statistics                 | PASS   |

## E2E Test

Not yet performed against the real AeroSwarm VPS/Radar B environment.

Expected E2E flow:

```text
Radar B / PCAP
    ↓ UDP
radar-edge
    ↓ WSS
AeroSwarm VPS
    ↓ UDP Feed
AsterixUdpService
    ↓
FusedTrackEvent
    ↓
Redpanda / Connector
```

E2E verification must confirm that Radar B data produces the correct `airportId` and does not affect Radar A/WARR data.
