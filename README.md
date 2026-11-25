# research-console

Konsol riset untuk skripsi *Analisis Komparatif Active Record Pattern dan Repository Pattern terhadap Performa Runtime dan Maintainability Kode pada Aplikasi Web Berbasis Laravel*.

Repo ini terpisah dari aplikasi uji ([DCorazons/academico](https://github.com/DCorazons/academico)) agar tidak masuk ke `git diff` antarimplementasi maupun analisis SonarQube. Konsol bukan objek penelitian: ia menjalankan prosedur pengujian dan menyajikan ringkasan hasil.

## Isi

| Folder | Isi | Status |
|---|---|---|
| `monitoring/` | InfluxDB 1.11 (metrik k6), cAdvisor 0.55 (CPU/memori kontainer), Prometheus 3.5 | ada |
| `k6/` | Halaman web (utama): `read-web.js`, `write-web.js`, `single-web.js`, `lib/web.js`. API JSON (pembanding): `read.js`, `write.js`, `single.js`. Bersama: `lib/common.js` | ada |
| `app/`, `docker-compose.yml` | Konsol (FastAPI): antrean run, rencana blok, hasil, ekspor CSV | ada |

## Tata letak di VPS

```
/opt/research/
├─ academico-ar/      checkout impl/active-record, port 127.0.0.1:8001
├─ academico-repo/    checkout impl/repository,    port 127.0.0.1:8002
├─ academico-qb/      checkout impl/query-builder, port 127.0.0.1:8003
├─ data/              dump + data k6 dari `php artisan research:export-k6-data` (tidak di-commit)
├─ results/           ringkasan JSON dan log setiap run (<label>.json, <label>.log)
├─ console-data/      basis data SQLite konsol
└─ console/           repo ini
```

Pembagian CPU (Tabel 3.4): CPU 0 untuk k6, monitoring, dan konsol; CPU 1 untuk FrankenPHP dan MariaDB. Semua port hanya di `127.0.0.1`; buka dari laptop lewat SSH tunnel.

## Monitoring

```bash
cd /opt/research/console
docker compose -f monitoring/docker-compose.yml up -d
```

- InfluxDB: `http://127.0.0.1:8086`, database `k6`. k6 menulis ke sana dengan `k6 run --out influxdb=http://127.0.0.1:8086/k6 ...`.
- Prometheus: `http://127.0.0.1:9090`. Hanya menyimpan metrik CPU dan memori kontainer `research-*`, di-scrape setiap 5 detik.
- cAdvisor: `http://127.0.0.1:8080`, hanya untuk pengecekan manual.

Stack monitoring dibiarkan berjalan sepanjang sesi pengujian; yang dinyalakan dan dimatikan per run hanyalah stack aplikasi.

## k6

k6 dijalankan dari image `grafana/k6:1.3.0` di CPU 0, dengan jaringan host agar `127.0.0.1:800x` dan InfluxDB terjangkau, dan dengan user host (`--user`) agar ringkasan JSON bisa ditulis ke folder hasil. Sebelum setiap run: stack implementasi menyala dan dump sudah dipulihkan.

```bash
docker run --rm --network host --cpuset-cpus 0 --user "$(id -u):$(id -g)" \
  -v /opt/research/console/k6:/scripts:ro -v /opt/research/data/k6:/data:ro -v /opt/research/results:/results \
  -e K6_INFLUXDB_TAGS_AS_FIELDS=vu,iter,url \
  grafana/k6:1.3.0 run --quiet --out influxdb=http://127.0.0.1:8086/k6 \
  -e BASE_URL=http://127.0.0.1:8001 -e VUS=50 -e RUN_ID=b01-ar-read-50 -e SUMMARY=/results/b01-ar-read-50.json \
  /scripts/read-web.js
```

| Variabel | Default | Arti |
|---|---|---|
| `BASE_URL` | `http://127.0.0.1:8001` | stack yang diuji (8001 AR, 8002 Repo, 8003 QB) |
| `VUS` | 50 | VU fase terukur (load: tingkat dari uji pilot; stress: naik 50 per tahap) |
| `WARMUP_VUS`, `WARMUP` | 5, 60s | pemanasan beban rendah, tidak dianalisis |
| `RAMP`, `SUSTAIN` | 30s, 60s | ramp-up lalu sustain (Tabel 3.6); hanya sustain yang dianalisis |
| `THINK` | 1 | think time tetap setelah setiap request, dalam detik |
| `SUMMARY`, `RUN_ID` | `summary.json`, kosong | file ringkasan JSON dan labelnya |
| `WARMUP_REQUESTS`, `REPEATS`, `GAP` | 3, 10, 0.5 | khusus `single.js` |

Setiap request diberi tag `endpoint` dan `phase` (`warmup`, `rampup`, `sustain`, `cooldown`). Ringkasan JSON berisi baris `sustain` per endpoint: jumlah request, throughput (request berhasil per detik sustain), error rate, avg/med/p95/max response time, dan rata-rata/maksimum `X-Peak-Memory`. Data per request ada di InfluxDB.

### Skrip halaman web (pengukuran utama)

Mengikuti masukan dosen pembimbing, pengukuran utama memakai halaman `/research/*` yang di-render di server (Blade), sampai seluruh HTML diterima: waktu yang dialami pengguna dari sisi server. Setiap VU berperilaku seperti satu browser: login lewat form (fase `login`, tidak dianalisis) dengan cookie jar sendiri yang dibuat baru setiap login, login ulang di awal setiap skenario, dan tidak mengikuti redirect sehingga POST dan halaman sesudahnya terukur terpisah. Jar bawaan k6 tidak dipakai karena dikosongkan antariterasi dan antarskenario, sehingga session hilang dan login berikutnya mendapat 419.

- `read-web.js`: VU ganjil mahasiswa (dashboard → materi → ujian), VU genap admin (daftar course → enrollment → laporan); keenam halaman mendapat porsi sama.
- `write-web.js`: setiap VU satu mahasiswa yang mengerjakan ujiannya satu per satu: buka halaman soal (token CSRF), kirim jawaban (303), buka dashboard (pesan nilai). Setelah 14 ujian, VU login sebagai mahasiswa berikutnya; tidak ada pasangan yang dipakai dua kali.
- `single-web.js`: satu VU, halaman admin lalu halaman mahasiswa, 3 pemanasan dan 10 terukur per halaman.

Stack harus memakai `SESSION_DRIVER=database` dan `BCRYPT_ROUNDS=10` (lihat `.env.research.example` academico), dan data k6 harus diekspor ulang agar `students.json` berisi `username`.

### Skrip API (pembanding)

Skenario read: setiap VU satu mahasiswa tetap, keenam GET digilir bergantian sehingga porsinya sama. Skenario write: setiap request satu pasangan mahasiswa–ujian dari `write-pairs.json` yang belum dipakai; pemanasan mengambil dari belakang, fase terukur dari depan, dan run dihentikan bila pasangan habis.

## Konsol

Dua kontainer di CPU 0, berbagi satu file SQLite (`/opt/research/console-data/console.db`):

| Kontainer | Tugas | Akses |
|---|---|---|
| `web` | antarmuka di `127.0.0.1:9000` | tanpa Docker socket; hanya menulis rencana dan perintah (jalankan, jeda, batalkan) ke SQLite |
| `runner` | menjalankan antrean satu run demi satu run | Docker socket dan `/opt/research` (path sama dengan host); tanpa port |

```bash
cd /opt/research/console
cp console.example.env .env        # isi VIEW_PASSWORD, CONTROL_PASSWORD, SECRET_KEY, K6_USER=$(id -u):$(id -g)
mkdir -p /opt/research/console-data /opt/research/results
docker compose build && docker compose up -d
```

Dari laptop: `ssh -L 9000:127.0.0.1:9000 ubuntu@<vps>`, lalu buka `http://localhost:9000`. Password **lihat** membuka semua halaman dan ekspor; password **kontrol** juga bisa membuat rencana dan menjalankan antrean.

### Prosedur satu run (runner)

1. Pemeriksaan awal: monitoring siap, `sha256sum -c SHA256SUMS` di folder data, tidak ada stack `research-ar|repo|qb` lain yang berjalan, checkout tepat di tag (`TAG_AR`, `TAG_REPO`, `TAG_QB`) tanpa perubahan, image sudah di-build.
2. `docker compose up -d --no-build --wait`, tunggu `/research/login` 200.
3. Pulihkan dump, `php artisan research:verify-dataset`, jeda `SETTLE_S` detik.
4. k6 (`grafana/k6:1.3.0`, CPU 0, kontainer `research-k6-<id>`, `--tag run_id=<label>`), sambil mencatat `/proc/stat` setiap detik.
5. Jendela sustain diambil dari InfluxDB (request `phase=sustain` pertama dan terakhir dengan `run_id` itu). CPU dan memori `app`, `mariadb`, dan `k6` pada jendela itu diambil dari Prometheus.
6. Validitas: tidak valid bila CPU k6 maksimum ≥ `K6_CPU_LIMIT` (80 % satu CPU) atau steal (semua CPU maupun CPU 1) ≥ `STEAL_LIMIT` (5 %). Error rate tidak menentukan validitas; itu hasil. Run tidak valid langsung diulang sampai `MAX_ATTEMPTS` (3) kali.
7. `docker compose down`. Bila ada langkah yang gagal (bukan tidak valid), run ditandai `failed` dan antrean dijeda.

### Rencana blok

Satu blok berisi semua run ketiga implementasi (41 run, sekitar 2 jam 20 menit):

1. Single dan load, implementasi demi implementasi, urutan diputar per blok: blok 1 AR→Repo→QB, blok 2 Repo→QB→AR, blok 3 QB→AR→Repo, dan seterusnya. Setiap implementasi: single, load read 25/50/75, load write 25/50/75.
2. Stress hanya AR dan Repo, bergantian siapa yang lebih dulu (blok ganjil AR): stress read lalu stress write pada 75/125/175/225/275 VU. Tahapnya tetap untuk kedua implementasi; titik jenuh dibaca dari hasil, run tidak dihentikan di sana.

Suite `web` (halaman, utama) memakai `*-web.js`; suite `api` (pembanding) memakai `single.js`, `read.js`, `write.js` dan dibuat sebagai rencana terpisah.

### Hasil dan ekspor

Halaman Hasil hanya memakai run `done` yang valid: rata-rata ± simpangan baku sampel antarblok per jenis, skenario, VU, implementasi, dan endpoint, dengan grafik SVG (p95, throughput, X-Peak-Memory). Ekspor CSV: `runs.csv` (semua run, termasuk yang tidak valid dan alasannya), `endpoints.csv`, `resources.csv`, `aggregate.csv?suite=web`.

### Pengembangan

```bash
cd app && python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt && .venv/bin/pytest -q
```
