cd sample/tsrunner

# 1. Input direktori
bun run cat094-decode-json.ts ../data pcap-output.json

# 2. Input satu file
bun run cat094-decode-json.ts ../data/interface_eth0_29072026.pcap out2.json

# 3. Input array beberapa file
bun run cat094-decode-json.ts ../data/interface_eth0_29072026.pcap ../data/interface_eth1_29072026.pcap out3.json