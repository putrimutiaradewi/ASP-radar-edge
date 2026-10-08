#!/bin/bash
# Replay an airport's combined.pcap to the ASTERIX UDP listener forever,
# restarting automatically each time pcapreplay.py finishes one pass.
#
# Usage:
#   ./replay-loop.sh [AIRPORT] [SPEED] [TARGET] [PORT]
#   ./replay-loop.sh                        # WARR, speed 2, 224.0.0.1:30000
#   ./replay-loop.sh WIII 4                 # WIII, speed 4
#
# Run in the background with nohup + disown so it survives logout:
#   nohup ./replay-loop.sh > replay-loop.log 2>&1 &
#   disown
#
# Stop it:
#   pkill -f replay-loop.sh

set -u
cd "$(dirname "$0")"

AIRPORT="${1:-WARR}"
SPEED="${2:-2}"
TARGET="${3:-224.0.0.1}"
PORT="${4:-30000}"
PCAP="generator/output/${AIRPORT}/combined.pcap"

if [ ! -f "$PCAP" ]; then
  echo "[replay-loop] $PCAP not found — generate it first:"
  echo "  cd generator && python3 main.py --airports $AIRPORT --aircraft 30 --duration 600"
  exit 1
fi

echo "[replay-loop] looping $PCAP -> $TARGET:$PORT (speed ${SPEED}x). Ctrl+C or pkill -f replay-loop.sh to stop."
while true; do
  python3 pcapreplay.py --asterix-replay -f "$PCAP" -t "$TARGET" -p "$PORT" -s "$SPEED"
  echo "[replay-loop] $(date '+%Y-%m-%d %H:%M:%S') cycle ended, restarting..."
  sleep 1
done
