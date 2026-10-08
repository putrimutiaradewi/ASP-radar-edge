# Cara Jalankan PcapReplayer di Server

Tool ini dipakai untuk replay data radar ASTERIX (dari file `.pcap`) ke
`simulator-service` AeroSwarm lewat UDP multicast, seolah-olah radar
beneran sedang mengirim data live.

## Prasyarat

- Sudah di-`scp` ke server (mis. `~/PcapReplayer` atau `~/sample`)
- `simulator-service` AeroSwarm sudah jalan dan listen di `224.0.0.1:30000`
  (network_mode: host, lihat `docker-compose.kantor.yml`)

## 1. Install dependency (sekali saja)

`requirements.txt` pin `scapy==2.4.3` (rilis 2019) yang **tidak kompatibel**
dengan Python 3.10+ di Linux — akan error `FileNotFoundError: liblibc.a`
saat import (bug deteksi arsitektur BPF, cuma relevan di BSD/macOS, salah
ke-trigger di Linux). **Jangan pakai `requirements.txt` langsung** — install
scapy versi terbaru saja:

```bash
cd ~/sample   # sesuaikan path tempat kamu taruh foldernya
pip3 install --user --upgrade scapy progressbar33
```

## 2. Jalankan replay

Sekali jalan (berhenti setelah file habis):

```bash
python3 pcapreplay.py --asterix-replay -f em1_2025-09-29.pcap -t 224.0.0.1 -p 30000 -s 2
```

Loop terus-menerus (`pcapreplay.py` sendiri tidak punya flag loop bawaan, gunakan `while true`):

```bash
while true; do
  python3 pcapreplay.py --asterix-replay -f em1_2025-09-29.pcap -t 224.0.0.1 -p 30000 -s 2
  sleep 1
done
```

Jalan di background, tidak putus walau SSH ditutup:

```bash
nohup bash -c '
while true; do
  python3 pcapreplay.py --asterix-replay -f em1_2025-09-29.pcap -t 224.0.0.1 -p 30000 -s 2
  sleep 1
done
' > replay.log 2>&1 &
disown
```

### Parameter

| Flag               | Arti                                                            |
| ------------------ | --------------------------------------------------------------- |
| `--asterix-replay` | Mode replay ASTERIX (bukan mode client/server pcapreplay biasa) |
| `-f`               | File `.pcap`/`.pcapng` yang mau di-replay                       |
| `-t 224.0.0.1`     | Target — multicast group yang didengar `simulator-service`      |
| `-p 30000`         | Port UDP tujuan (`UDP_PORT` di `simulator-service`)             |
| `-s 2`             | Speed multiplier (2x lebih cepat dari timestamp asli di pcap)   |

### Data sample per bandara (`sample/data/<ICAO>/catXXX.pcap`)

Hasil generate `asterix-generator` — 7 file kategori per bandara (WADD,
WAHI, WAHS, WARR, WIII). Karena `simulator-service` auto-deteksi kategori
per paket, semua file untuk 1 bandara bisa dikirim ke port yang sama
sekaligus (paralel).

Sekali jalan (tidak loop):

```bash
cd ./sample
for cat in data/*.pcap; do
  python3 pcapreplay.py --asterix-replay -f "$cat" -t 224.0.0.1 -p 30000 -s 2 &
done
wait
```

Ganti `WARR` sesuai bandara yang mau di-replay.

Loop terus-menerus semua kategori 1 bandara, di background (tidak putus walau SSH ditutup, tanpa log):

```bash
nohup bash -c '
while true; do
  for cat in data/*.pcap; do
    python3 pcapreplay.py --asterix-replay -f "$cat" -t 224.0.0.1 -p 30000 -s 2 &
  done
  wait
  sleep 1
done
' > /dev/null 2>&1 &
disown
```

Catat PID yang muncul, misalnya `[1] 1492657`, untuk menghentikan wrapper-nya
nanti. Kalau mau menyimpan log, ganti `> /dev/null` menjadi
`> replay-warr.log`.

## 3. Verifikasi data masuk

Di terminal lain (biarkan replay tetap jalan):

```bash
curl -s http://localhost:8086/feeds | python3 -m json.tool
```

Cari `stats.packetsReceived` di feed yang bersangkutan — harus mulai naik
dari `0`. Kalau tetap `0` setelah beberapa detik, cek:

```bash
sudo tshark -i ens160 -f "udp port 30000 or host 224.0.0.1"
```

Harus muncul traffic — kalau tidak, replay-nya tidak benar-benar mengirim
(cek log `pip3 install` sukses & file `.pcap` valid).

## 4. Hentikan replay background

```bash
pkill -f pcapreplay.py
```

Kalau menjalankan versi `while true` dengan `nohup`, hentikan juga proses
wrapper-nya pakai PID yang muncul saat `nohup` dijalankan (misalnya `1492657`):

```bash
kill 1492657
```
