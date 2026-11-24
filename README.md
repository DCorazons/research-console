# research-console

Konsol riset untuk skripsi *Analisis Komparatif Active Record Pattern dan Repository Pattern terhadap Performa Runtime dan Maintainability Kode pada Aplikasi Web Berbasis Laravel*.

Repo ini terpisah dari aplikasi uji ([DCorazons/academico](https://github.com/DCorazons/academico)) agar tidak masuk ke `git diff` antarimplementasi maupun analisis SonarQube. Konsol bukan objek penelitian: ia menjalankan prosedur pengujian dan menyajikan ringkasan hasil.

## Isi

| Folder | Isi | Status |
|---|---|---|
| `monitoring/` | InfluxDB 1.11 (metrik k6), cAdvisor 0.55 (CPU/memori kontainer), Prometheus 3.5 | ada |
| `k6/` | Halaman web (utama): `read-web.js`, `write-web.js`, `single-web.js`, `lib/web.js`. API JSON (pembanding): `read.js`, `write.js`, `single.js`. Bersama: `lib/common.js` | ada |
| `app/` | FastAPI + Chart.js: antrean run, blok, ekspor CSV | belum |

## Tata letak di VPS

```
/opt/research/
├─ academico-ar/      checkout impl/active-record, port 127.0.0.1:8001
├─ academico-repo/    checkout impl/repository,    port 127.0.0.1:8002
├─ academico-qb/      checkout impl/query-builder, port 127.0.0.1:8003
├─ data/              dump + data k6 dari `php artisan research:export-k6-data` (tidak di-commit)
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
