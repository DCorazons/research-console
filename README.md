# research-console

Konsol riset untuk skripsi *Analisis Komparatif Active Record Pattern dan Repository Pattern terhadap Performa Runtime dan Maintainability Kode pada Aplikasi Web Berbasis Laravel*.

Repo ini terpisah dari aplikasi uji ([DCorazons/academico](https://github.com/DCorazons/academico)) agar tidak masuk ke `git diff` antarimplementasi maupun analisis SonarQube. Konsol bukan objek penelitian: ia menjalankan prosedur pengujian dan menyajikan ringkasan hasil.

## Isi

| Folder | Isi | Status |
|---|---|---|
| `monitoring/` | InfluxDB 1.11 (metrik k6), cAdvisor 0.55 (CPU/memori kontainer), Prometheus 3.5 | ada |
| `k6/` | Skrip skenario read, write, dan stress | belum |
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
