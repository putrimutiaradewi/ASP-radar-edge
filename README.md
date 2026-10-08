# radar-edge

Standalone edge service yang berjalan di PC yang terhubung ke radar. Service ini menerima **raw UDP datagram** dari radar dan meneruskannya ke AeroSwarm melalui **outbound authenticated WSS**.

Radar Edge **tidak melakukan decoding, parsing, transformasi, atau modifikasi ASTERIX**. Payload yang diterima dari UDP diteruskan sebagai binary WebSocket frame.

```text
Radar B
   │
   │ UDP
   ▼
Radar Edge
   │
   │ WSS / TLS (outbound)
   ▼
AeroSwarm VPS
   │
   ▼
AsterixUdpService
   │
   ▼
FusedTrackEvent
```

## Jaminan Perilaku

| Perilaku                        | Implementasi                                                                                                                                          |
| ------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1 datagram → 1 binary frame     | Setiap UDP datagram diteruskan sebagai satu binary WebSocket frame. Payload dikirim sebagai `Buffer` tanpa decoding atau transformasi.                |
| Raw data tetap unchanged        | Radar Edge tidak melakukan decoding atau modifikasi ASTERIX.                                                                                          |
| Tidak ada unlimited buffering   | Jika WSS belum `OPEN`, packet langsung di-drop. Saat koneksi lambat, packet juga dapat di-drop ketika `bufferedAmount` melebihi `MAX_BUFFERED_BYTES`. |
| Tidak ada replay setelah outage | Packet yang sudah di-drop tidak dikirim ulang setelah koneksi kembali.                                                                                |
| Reconnect                       | Backoff: `1s → 2s → 4s → 8s → 16s → 30s`, dengan batas `RECONNECT_MAX_MS`.                                                                            |
| Authentication                  | Source token dan feed ID dikirim saat WebSocket handshake.                                                                                            |
| Token tidak di-log              | Token tidak dicetak ke log dan logger melakukan sanitasi terhadap nilai sensitif.                                                                     |
| Statistik                       | Service mencatat `received`, `forwarded`, `dropped`, `reconnects`, dan status koneksi.                                                                |

## Menjalankan

Project membutuhkan [Bun](https://bun.sh). Development dan testing dilakukan menggunakan Bun + TypeScript.

Install dependency:

```bash
bun install
```

Buat file `.env` dari template:

```bash
cp .env.example .env
```

Kemudian isi:

```env
UDP_LISTEN_HOST=0.0.0.0
UDP_LISTEN_PORT=13217

AEROSWARM_INGEST_URL=wss://<aeroswarm-domain>/ws/ingest/radar
SOURCE_TOKEN=
FEED_ID=

RECONNECT_MAX_MS=30000
STATS_INTERVAL_MS=30000
```

Jalankan service:

```bash
bun run start
```

Development mode:

```bash
bun run dev
```

Bun membaca konfigurasi `.env` dari working directory.

## Executable Tunggal

Radar Edge juga dapat dibuat menjadi executable menggunakan Bun:

```bash
bun run build:exe
```

Output:

```text
dist/radar-edge
```

Pada Windows:

```text
dist/radar-edge.exe
```

Executable dapat dijalankan pada PC target bersama file `.env`.

Build dilakukan untuk OS tempat command tersebut dijalankan. Cross-platform build belum menjadi bagian dari testing project ini.

## Sebagai Service

Radar Edge dirancang untuk berjalan sebagai process yang dapat direstart oleh service manager ketika terjadi error.

Contoh menggunakan `systemd` di Linux:

```ini
[Unit]
Description=Radar Edge
After=network.target

[Service]
WorkingDirectory=/opt/radar-edge
ExecStart=/opt/radar-edge/radar-edge
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Di Windows dapat menggunakan NSSM atau Task Scheduler dengan konfigurasi restart ketika process berhenti.

## Konfigurasi

| Variabel               |   Default | Keterangan                                                     |
| ---------------------- | --------: | -------------------------------------------------------------- |
| `UDP_LISTEN_HOST`      | `0.0.0.0` | Alamat bind UDP receiver.                                      |
| `UDP_LISTEN_PORT`      |   `13217` | Port UDP yang menerima data radar.                             |
| `AEROSWARM_INGEST_URL` |     wajib | URL ingest AeroSwarm, contoh `wss://<domain>/ws/ingest/radar`. |
| `SOURCE_TOKEN`         |     wajib | Authentication token untuk source radar.                       |
| `FEED_ID`              |     wajib | Identitas feed, misalnya Radar B.                              |
| `RECONNECT_MAX_MS`     |   `30000` | Batas maksimum reconnect backoff.                              |
| `STATS_INTERVAL_MS`    |   `30000` | Interval log statistik.                                        |
| `MAX_BUFFERED_BYTES`   | `1048576` | Batas buffer WebSocket sebelum packet di-drop.                 |
| `PING_INTERVAL_MS`     |   `15000` | Interval heartbeat/ping WebSocket.                             |
| `UDP_RCVBUF_BYTES`     | `4194304` | Ukuran `SO_RCVBUF` yang diminta untuk UDP socket.              |
| `LOG_LEVEL`            |    `info` | Level log: `debug`, `info`, `warn`, atau `error`.              |

Konfigurasi divalidasi ketika service dijalankan. Nilai `SOURCE_TOKEN` tidak ditampilkan pada log.

## Kontrak WSS dengan AeroSwarm

Spesifikasi task menyediakan `SOURCE_TOKEN` dan `FEED_ID`, tetapi bentuk authentication pada endpoint AeroSwarm perlu dikonfirmasi dengan environment actual.

Implementasi saat ini menggunakan header:

```http
GET /ws/ingest/radar

Authorization: Bearer <SOURCE_TOKEN>
X-Feed-Id: <FEED_ID>
```

Authentication menggunakan header agar credential tidak ditempatkan pada query string atau URL.

Implementasi handshake dipusatkan pada:

```text
src/handshake.ts
```

Jika kontrak authentication AeroSwarm berbeda, bagian handshake dapat disesuaikan tanpa mengubah flow utama UDP → WSS.

## Statistik & Log

Service mencatat ringkasan statistik secara periodik berdasarkan `STATS_INTERVAL_MS`.

Contoh:

```json
{
  "time": "...",
  "level": "info",
  "msg": "stats",
  "received": 6,
  "forwarded": 6,
  "dropped": 0,
  "reconnects": 0,
  "connected": true,
  "interval": {
    "received": 6,
    "forwarded": 6,
    "dropped": 0,
    "reconnects": 0
  }
}
```

### Counter

| Counter      | Arti                                                                               |
| ------------ | ---------------------------------------------------------------------------------- |
| `received`   | Datagram UDP yang berhasil diterima oleh aplikasi.                                 |
| `forwarded`  | Datagram yang berhasil dikirim sebagai binary WebSocket frame.                     |
| `dropped`    | Datagram yang dibuang karena WSS tidak tersedia, backpressure, atau gagal dikirim. |
| `reconnects` | Percobaan reconnect setelah koneksi gagal atau terputus.                           |

Dalam kondisi normal, setiap datagram yang sudah diterima aplikasi akan berakhir sebagai `forwarded` atau `dropped`.

Counter `interval` menunjukkan jumlah perubahan sejak log statistik sebelumnya.

### Catatan UDP Receive Buffer

Counter `received` hanya menghitung packet yang berhasil sampai ke aplikasi. Packet yang dibuang oleh kernel karena UDP receive buffer penuh tidak dapat dihitung oleh application-level counter.

Radar Edge meminta ukuran `SO_RCVBUF` sebesar `UDP_RCVBUF_BYTES`.

Pada Linux, ukuran aktual dapat dibatasi oleh konfigurasi OS seperti:

```text
net.core.rmem_max
```

Jika OS memberikan buffer lebih kecil dari yang diminta, service akan mencatat warning.

## Testing

### Automated Test

Jalankan:

```bash
bun run typecheck
bun test
bun run build
```

Ketiga command tersebut digunakan untuk memvalidasi:

* TypeScript type checking
* Unit/integration tests
* Production build

### Local Functional Test

Untuk melakukan functional test tanpa Radar B fisik, project menyediakan:

* Sample PCAP sebagai simulasi traffic radar
* PCAP replay untuk mengirim packet menjadi UDP datagram
* Mock WSS server sebagai simulasi AeroSwarm

Flow:

```text
Sample PCAP
    │
    ▼
PCAP Replay
    │
    │ UDP
    ▼
Radar Edge :13217
    │
    │ WSS
    ▼
Mock WSS Server
```

### Menjalankan Mock WSS

```bash
bun run scripts/mock-ingest-server.ts
```

Expected output:

```text
mock ingest listening on ws://127.0.0.1:9000/ws/ingest/radar
```

Untuk local testing, konfigurasi `.env` dapat menggunakan:

```env
AEROSWARM_INGEST_URL=ws://127.0.0.1:9000/ws/ingest/radar
SOURCE_TOKEN=test-token
FEED_ID=radar-b-test
```

`ws://` digunakan hanya untuk local mock server.

### Menjalankan Radar Edge

```bash
bun run src/index.ts
```

Expected flow:

```text
UDP receiver listening
        ↓
Edge starting
        ↓
Connecting to AeroSwarm ingest
        ↓
Connected to AeroSwarm ingest
```

### Replay PCAP

Contoh menggunakan sample PCAP:

```bash
bun run scripts/pcap-replay.ts .\sample\data\130320261451.pcapng --port 13217 --speed max
```

PCAP replay akan membaca UDP packet dari capture dan mengirim payload ke UDP receiver Radar Edge.

Contoh hasil:

```text
parsed 11554 UDP packets
{
  nonUdp: 2997,
  fragmented: 0,
  truncated: 0,
  unsupportedLink: 0
}

round 1: sent 11554 datagrams to 127.0.0.1:13217
```

## Hasil Local Functional Test

### 1. UDP → WSS Forwarding

Sample PCAP:

```text
130320261451.pcapng
```

Hasil:

| Metric             | Result |
| ------------------ | -----: |
| UDP packets parsed | 11,554 |
| UDP packets sent   | 11,554 |
| Edge received      | 11,554 |
| Edge forwarded     | 11,554 |
| Dropped            |      0 |
| Reconnects         |      0 |

Mock WSS juga menerima seluruh frame binary sampai packet terakhir.

**Result: PASS**

### 2. WSS Disconnect & Reconnect

Mock WSS dihentikan ketika Edge sedang berjalan.

Edge melakukan reconnect menggunakan backoff:

```text
1s
2s
4s
8s
16s
30s
30s
...
```

Backoff dibatasi pada `RECONNECT_MAX_MS=30000`.

**Result: PASS**

### 3. Packet Drop Saat WSS Down

Ketika Mock WSS masih tidak tersedia, PCAP yang sama direplay kembali.

Hasil counter kumulatif:

```text
received:   23108
forwarded:  11554
dropped:    11554
```

Artinya:

* 11.554 packet pertama berhasil diteruskan ketika WSS tersedia.
* 11.554 packet berikutnya masuk ketika WSS down.
* Packet tersebut langsung di-drop.
* Tidak ada unlimited buffering.

**Result: PASS**

### 4. Reconnect Recovery

Mock WSS kemudian dijalankan kembali.

Edge berhasil melakukan reconnect dan menghasilkan log:

```text
connected to AeroSwarm ingest
```

Setelah reconnect, Edge kembali dapat meneruskan packet baru.

**Result: PASS**

## Ringkasan Testing

| Test                      | Status    |
| ------------------------- | --------- |
| TypeScript typecheck      | ✅ PASS    |
| Automated tests           | ✅ PASS    |
| Production build          | ✅ PASS    |
| UDP → WSS forwarding      | ✅ PASS    |
| Raw binary forwarding     | ✅ PASS    |
| WSS disconnect handling   | ✅ PASS    |
| Packet drop saat WSS down | ✅ PASS    |
| Reconnect backoff         | ✅ PASS    |
| Reconnect recovery        | ✅ PASS    |
| Real Radar B              | ⏳ PENDING |
| Real AeroSwarm VPS        | ⏳ PENDING |
| Real E2E                  | ⏳ PENDING |
| `airportId` validation    | ⏳ PENDING |
| Radar A/WARR regression   | ⏳ PENDING |

## E2E & Regresi

**Real E2E belum dijalankan.**

Local testing menggunakan PCAP dan Mock WSS sudah berhasil, tetapi acceptance test penuh membutuhkan:

* Radar B sebenarnya
* AeroSwarm VPS sebenarnya
* `AsterixUdpService`
* `FusedTrackEvent`
* Environment downstream yang sesuai

Target E2E:

```text
Radar B
   │
   │ UDP
   ▼
Radar Edge
   │
   │ WSS
   ▼
AeroSwarm VPS
   │
   ▼
AsterixUdpService
   │
   ▼
FusedTrackEvent
```

### Checklist E2E

* [ ] Radar Edge berhasil connect ke AeroSwarm actual.
* [ ] Data Radar B diterima oleh Radar Edge melalui UDP.
* [ ] Data Radar B diteruskan ke AeroSwarm sebagai binary frame.
* [ ] `AsterixUdpService` menerima feed Radar B.
* [ ] `FusedTrackEvent` berhasil dihasilkan.
* [ ] `airportId` pada event sesuai dengan Radar B.
* [ ] Radar A/WARR tetap menghasilkan track secara normal.
* [ ] Tidak ada data Radar B yang masuk ke pipeline WARR.
* [ ] Ketika WSS down, packet baru di-drop dan tidak direplay setelah reconnect.
* [ ] Setelah koneksi kembali, packet baru dapat diteruskan kembali.

## Troubleshooting

### WebSocket tidak dapat connect

Jika muncul error seperti:

```text
server rejected the WebSocket upgrade
```

Periksa:

* `AEROSWARM_INGEST_URL`
* `SOURCE_TOKEN`
* `FEED_ID`
* path `/ws/ingest/radar`
* kontrak authentication AeroSwarm

### Server tidak dapat dijangkau

Jika muncul:

```text
cannot reach the server
```

Periksa:

* URL
* DNS
* koneksi jaringan
* firewall
* akses outbound ke port 443

### TLS Error

Jika muncul:

```text
TLS verification failed
```

Periksa:

* waktu/tanggal PC
* certificate chain
* CA yang dipercaya OS

TLS verification tidak dimatikan oleh Radar Edge.

### UDP Port Sudah Digunakan

Jika muncul:

```text
EADDRINUSE
```

Berarti port UDP yang digunakan sudah dipakai process lain.

Periksa process yang menggunakan `UDP_LISTEN_PORT`, atau ubah konfigurasi port.

### UDP Receive Buffer Warning

Pada beberapa OS, ukuran buffer yang diberikan dapat lebih kecil dari ukuran yang diminta.

Contoh:

```text
OS granted a smaller UDP receive buffer than requested
requested=4194304 granted=524288
```

Warning ini tidak menghentikan service.

Pada Linux, ukuran buffer dapat dipengaruhi oleh:

```text
net.core.rmem_max
```

## Keputusan Desain

### Drop, bukan unlimited buffer

Radar data bersifat real-time. Menyimpan packet dalam jumlah tidak terbatas ketika WSS down dapat menyebabkan:

* penggunaan memory terus meningkat
* packet lama dikirim setelah koneksi pulih
* data track menjadi stale

Karena itu packet yang masuk ketika WSS tidak tersedia langsung di-drop.

### Backpressure

Selain kondisi WSS down, koneksi yang lambat juga dapat menyebabkan WebSocket memiliki data yang belum terkirim.

`MAX_BUFFERED_BYTES` digunakan sebagai batas untuk mencegah buffer aplikasi/socket berkembang tanpa batas.

### Reconnect Backoff

Reconnect menggunakan exponential backoff:

```text
1s → 2s → 4s → 8s → 16s → 30s
```

Tujuannya menghindari reconnect terus-menerus dengan interval sangat pendek ketika AeroSwarm sedang unavailable.

### Heartbeat

Ping/pong digunakan untuk membantu mendeteksi koneksi yang secara jaringan sudah tidak aktif tetapi belum terdeteksi sebagai disconnect.

Ketika koneksi dianggap tidak sehat, Edge akan memutus koneksi dan menjalankan reconnect.

### UDP Receive Buffer

Radar Edge meminta UDP receive buffer yang lebih besar melalui `SO_RCVBUF` untuk mengurangi kemungkinan packet loss pada kondisi traffic tinggi atau event-loop stall singkat.

## Di Luar Scope

Radar Edge **tidak** menangani:

* ASTERIX decoding
* ASTERIX transformation
* perubahan decoder AeroSwarm
* `FusedTrackEvent` processing
* Redpanda/Kafka
* konfigurasi Radar B
* konfigurasi router/NAT
* VPN/Tailscale/WireGuard
* HA/failover edge

Scope utama Radar Edge adalah:

```text
UDP Receive
     ↓
Raw Binary Forwarding
     ↓
Authenticated WSS
     ↓
Reconnect & Failure Handling
     ↓
Statistics / Logging
```

## Struktur Project

```text
radar-edge/
├── sample/
│   ├── data/
│   │   ├── 130320261451.pcapng
│   │   ├── em1_2025-09-29.pcap
│   │   ├── interface_eth0_29072026.pcap
│   │   ├── interface_eth1_29072026.pcap
│   │   └── interface_eth1.pcap
│   ├── generator/
│   │   ├── encoder/
│   │   └── output/
│   ├── tsrunner/
│   ├── pcapreplay.py
│   └── README.md
│
├── scripts/
│   ├── mock-ingest-server.ts
│   └── pcap-replay.ts
│
├── src/
│   ├── index.ts
│   ├── edge.ts
│   ├── udp-receiver.ts
│   ├── wss-forwarder.ts
│   ├── ws-connection.ts
│   ├── handshake.ts
│   ├── diagnostics.ts
│   ├── config.ts
│   ├── backoff.ts
│   ├── logger.ts
│   ├── stats.ts
│   ├── clock.ts
│   │
│   ├── edge.test.ts
│   ├── udp-receiver.test.ts
│   ├── wss-forwarder.test.ts
│   ├── handshake.test.ts
│   ├── backoff.test.ts
│   ├── config.test.ts
│   ├── diagnostics.test.ts
│   └── logger.test.ts
│
├── .env
├── .env.example
├── .gitignore
├── package.json
├── bun.lock
├── tsconfig.json
└── README.md
```

### File Utama

| File                            | Fungsi                                                            |
| ------------------------------- | ----------------------------------------------------------------- |
| `src/index.ts`                  | Entrypoint service, konfigurasi, signal handling, dan shutdown.   |
| `src/edge.ts`                   | Menghubungkan UDP receiver, WSS forwarder, dan statistik.         |
| `src/udp-receiver.ts`           | Menerima UDP datagram dan mengatur UDP socket.                    |
| `src/wss-forwarder.ts`          | Forward binary frame, reconnect, backoff, drop, dan backpressure. |
| `src/ws-connection.ts`          | Abstraksi koneksi WebSocket.                                      |
| `src/handshake.ts`              | Menyiapkan authentication header untuk WSS.                       |
| `src/config.ts`                 | Membaca dan memvalidasi environment configuration.                |
| `src/backoff.ts`                | Mengatur reconnect backoff.                                       |
| `src/stats.ts`                  | Counter dan statistik runtime.                                    |
| `src/logger.ts`                 | Logging dan sanitasi informasi sensitif.                          |
| `src/diagnostics.ts`            | Normalisasi error dan diagnostic hint.                            |
| `scripts/mock-ingest-server.ts` | Mock WSS server untuk local testing.                              |
| `scripts/pcap-replay.ts`        | Replay packet dari PCAP ke UDP receiver.                          |

## Current Status

```text
Development                    ✅ DONE
Automated Testing              ✅ PASS
Local Functional Testing       ✅ PASS
UDP → WSS Forwarding           ✅ PASS
WSS Failure Handling           ✅ PASS
Reconnect Recovery             ✅ PASS

Real Radar B Testing           ⏳ PENDING
Real AeroSwarm Testing         ⏳ PENDING
Real E2E Testing               ⏳ PENDING
airportId Validation           ⏳ PENDING
Radar A/WARR Regression        ⏳ PENDING
```

**Current conclusion:** Radar Edge sudah berhasil diimplementasikan dan divalidasi melalui local functional testing. Validasi terakhir yang masih diperlukan adalah pengujian pada environment actual menggunakan Radar B dan AeroSwarm VPS sampai downstream `FusedTrackEvent`.
