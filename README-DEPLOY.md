# Snake Live — Deploy VPS + Nginx Proxy Manager (HTTPS)

## Arsitektur

```
Browser / OBS
     │
     ▼
https://domain  ──────────────► Nginx Proxy Manager (SSL / Force HTTPS)
     │                              │
     │                              ├── /          → snake-live:8001 → login.html → home.html
     │                              ├── /engine    → snake-live:8001 → engine.html
     │                              ├── /snake     → snake-live:8001 → snake.html (legacy)
     │                              └── /ws        → snake-live:8766 → WebSocket relay
     │
     ▼
server.py ──► login dari .env + JWT + rate limit
          └─► TikTokLive ──► gift ──► WebSocket ──► engine.html / snake.html
```

- `server.py` adalah satu-satunya backend Python.
- `engine.html` adalah game default, `snake.html` versi legacy.
- Username dan password login **hanya dibaca dari `.env`**.
- Tidak ada password yang dibuat otomatis dan tidak ada `data/auth.json`.
- Jika `ADMIN_USERNAME` kosong, atau `ADMIN_PASSWORD` dan `ADMIN_PASSWORD_HASH`
  dua-duanya kosong, server sengaja berhenti.
- Dashboard, API, dan WebSocket dilindungi login.
- OBS memakai overlay token read-only, bukan password admin.

## 1. Siapkan `.env`

```bash
cp .env.example .env
chmod 600 .env
```

Edit `.env` minimal seperti ini:

```env
ADMIN_USERNAME=admin
ADMIN_PASSWORD=password-panjang-dan-kuat
PUBLIC_ORIGIN=https://snake.domainkamu.com
COOKIE_SECURE=auto
```

Catatan:

- Ganti `password-panjang-dan-kuat` dengan password asli. Minimal 8 karakter,
  disarankan 16+ karakter. Contoh membuat password acak:
  `python -c "import secrets; print(secrets.token_urlsafe(24))"`.
- `PUBLIC_ORIGIN` harus sama persis dengan URL HTTPS yang dibuka di browser,
  tanpa garis miring di akhir.
- Jangan commit `.env`. File ini sudah masuk `.gitignore`.
- Password dengan spasi, `#`, atau `$` sebaiknya dibungkus kutip tunggal,
  misalnya `ADMIN_PASSWORD='password saya #1$'`. Jika memakai kutip ganda atau
  tanpa kutip, karakter `$` perlu ditulis `$$`. Paling aman gunakan
  `ADMIN_PASSWORD_HASH`.
- `server.py` juga memuat `.env` saat dijalankan lokal, jadi tidak perlu export
  environment manual.

## 2. Jalankan dengan Docker

```bash
docker network create app-network   # sekali saja, jika belum ada
docker compose up -d --build
docker compose logs -f --tail=100
```

Log yang benar akan menampilkan:

```
[AUTH] Login memakai ADMIN_PASSWORD dari .env untuk @admin.
```

Kalau muncul:

```
[AUTH] ADMIN_USERNAME wajib diisi di .env.
[AUTH] ADMIN_PASSWORD atau ADMIN_PASSWORD_HASH wajib diisi di .env.
```

Isi `.env`, lalu:

```bash
docker compose up -d --force-recreate snake-live
```

Cek health:

```bash
curl http://127.0.0.1:8001/healthz
docker compose ps
```

Compose hanya mem-publish `127.0.0.1:8001` untuk debug dari host. NPM mengakses
container lewat `app-network`. Port `8766` tidak dipublish.

Jika NPM tidak berada di network yang sama:

```bash
docker network connect app-network <nama-container-npm>
```

## 3. Jalankan lokal (Windows/Linux)

```bash
pip install -r requirements.txt
python server.py
```

Server otomatis membaca `.env` di folder project. Buka
`http://localhost:8001`, lalu login. Di Windows bisa juga double-click
`start_server.bat`.

Untuk akses HTTPS di lokal, gunakan reverse proxy/HTTPS sendiri. Jangan set
`COOKIE_SECURE=true` saat mengakses lewat HTTP biasa.

## 4. Nginx Proxy Manager + HTTPS

### Proxy Host

- Domain: `snake.domainkamu.com`
- Scheme: `http`
- Forward Hostname: `snake-live`
- Forward Port: `8001`
- Websockets Support: ON
- Block Common Exploits: ON
- Access List: **None** (login sudah ditangani aplikasi)
- SSL: Request Let's Encrypt
- Force SSL: ON
- HTTP/2 Support: ON
- HSTS: opsional

Jangan aktifkan Access List NPM bersamaan dengan login aplikasi kecuali memang
ingin dua lapis login. Access List akan membuat OBS Browser Source sulit
terhubung.

### Custom Location WebSocket

Di Proxy Host yang sama → **Custom Locations** → Add:

- Location: `/ws`
- Scheme: `http`
- Forward Hostname: `snake-live`
- Forward Port: `8766`
- Websockets Support: ON

Tanpa ini, dashboard bisa terbuka tetapi gift real-time gagal
(`wss://domain/ws`).

### HTTPS dan cookie

- Akses dashboard hanya lewat `https://snake.domainkamu.com`.
- NPM meneruskan `X-Forwarded-Proto: https`; cookie login otomatis diberi
  atribut `Secure` karena `COOKIE_SECURE=auto`.
- Jika memakai reverse proxy lain, pastikan header `X-Forwarded-Proto`,
  `X-Forwarded-Host`, dan `X-Real-IP` diteruskan.
- Jangan buka port `8001`/`8766` ke publik. Cukup port 80/443 untuk NPM.
- Jika memakai Cloudflare, aktifkan WebSockets dan gunakan SSL mode
  **Full (strict)**.

## 5. Kredensial login

### Cara kerja

- `ADMIN_USERNAME` dan `ADMIN_PASSWORD` dibaca dari `.env` saat startup.
- File lama `data/auth.json` (jika ada) tidak dipakai lagi; kredensial sepenuhnya
  dari `.env`.
- Password asli tetap di `.env`. Jika memakai `ADMIN_PASSWORD`, saat startup
  di-hash PBKDF2-SHA256 (260.000 iterasi) di memori dan hash tidak ditulis ke
  disk. Tidak ada `data/auth.json`.
- JWT HS256 dikirim lewat cookie `snake_session`:
  - `HttpOnly`
  - `SameSite=Lax`
  - `Secure` otomatis saat HTTPS
  - berlaku 12 jam (`JWT_EXPIRE_HOURS`)
- Semua `/api/*` (kecuali login/logout) memerlukan JWT.
- WebSocket memerlukan JWT cookie, Bearer token, atau overlay token.
- POST login dan API kontrol memeriksa `Origin` untuk mencegah CSRF. Logout
  selalu menghapus cookie agar tidak terjadi login loop.
- Login dibatasi 5 percobaan / 5 menit per IP dan per IP+username.
- API dibatasi 180 request / menit per IP.
- WebSocket dibatasi 60 koneksi / menit per IP, maksimal 100 koneksi aktif.

### Ganti username/password

Edit `.env`:

```env
ADMIN_USERNAME=admin
ADMIN_PASSWORD=password-baru-yang-kuat
```

Lalu:

```bash
docker compose up -d --force-recreate snake-live
```

Sesi JWT lama tetap valid sampai expiry. Untuk memaksa semua sesi logout,
ganti juga `JWT_SECRET` di `.env`.

### Alternatif hash password

Secara default, password asli ada di `.env` lewat `ADMIN_PASSWORD`. Kalau ingin
`.env` tidak berisi password asli, gunakan hash:

```bash
python server.py --hash-password
```

Atau lewat Docker:

```bash
docker compose run --rm --no-deps snake-live python server.py --hash-password
```

Isi hasilnya:

```env
ADMIN_USERNAME=admin
ADMIN_PASSWORD=
ADMIN_PASSWORD_HASH=pbkdf2_sha256:260000:xxxxxxxx:xxxxxxxx
```

`ADMIN_PASSWORD_HASH` menang atas `ADMIN_PASSWORD`. Format memakai pemisah `:`
agar aman dari interpolasi `$` di Compose.

### Reset

Karena kredensial hanya ada di `.env`:

```bash
nano .env
docker compose up -d --force-recreate snake-live
```

Tidak perlu menghapus volume. Volume `snake-data` hanya menyimpan JWT secret
dan overlay token jika keduanya tidak diisi di `.env`.

## 6. OBS Browser Source

1. Login ke dashboard lewat HTTPS.
2. Klik **Salin Link OBS** di kartu Snake atau dock bawah.
3. Tempel URL ke OBS Browser Source.
4. Ukuran: **1080 x 1920** (portrait TikTok Live).

URL akan berbentuk:

```
https://snake.domainkamu.com/engine.html?token=<OVERLAY_TOKEN>
```

- Overlay token hanya memberi akses baca ke halaman game dan WebSocket gift.
- Token tidak bisa dipakai membuka dashboard atau mengontrol bot.
- Jangan bagikan URL OBS karena token ada di query string dan bisa tercatat di
  log akses NPM.
- Token dibuat otomatis dan disimpan di `/app/data/overlay_token`, atau isi
  `OVERLAY_TOKEN` di `.env` jika ingin mengelola sendiri.
- Jika `OVERLAY_TOKEN` diganti, link OBS lama tidak berlaku.

## 7. Environment

| Variabel | Default | Keterangan |
|---|---|---|
| `ADMIN_USERNAME` | wajib | Username login dari `.env` |
| `ADMIN_PASSWORD` | wajib* | Password login dari `.env` |
| `ADMIN_PASSWORD_HASH` | kosong | Alternatif hash; menang atas `ADMIN_PASSWORD` |
| `PUBLIC_ORIGIN` | kosong | Origin HTTPS untuk validasi CSRF |
| `COOKIE_SECURE` | `auto` | `auto`, `true`, atau `false` |
| `JWT_SECRET` | otomatis | Minimal 32 karakter; kosong = disimpan di volume data |
| `JWT_EXPIRE_HOURS` | `12` | Umur sesi login |
| `OVERLAY_TOKEN` | otomatis | Minimal 16 karakter; kosong = disimpan di volume data |
| `PORT` | `8001` | Port HTTP internal |
| `WS_PORT` | `8766` | Port WebSocket internal |
| `HOST` | `0.0.0.0` | Bind address |
| `DATA_DIR` | `/app/data` | Lokasi `jwt_secret`/`overlay_token` |
| `TRUST_PROXY` | `true` | Percaya header dari NPM |
| `LOGIN_MAX_ATTEMPTS` | `5` | Percobaan login per window |
| `LOGIN_WINDOW_SECONDS` | `300` | Window rate limit login |
| `API_RATE_LIMIT` | `180` | Request API per window |
| `API_RATE_WINDOW_SECONDS` | `60` | Window rate limit API |
| `WS_RATE_LIMIT` | `60` | Koneksi WS per window |
| `MAX_WS_CLIENTS` | `100` | Koneksi WS bersamaan |
| `DEFAULT_USERNAME` | kosong | Auto-start bot TikTok |

\* Wajib jika `ADMIN_PASSWORD_HASH` kosong.

## 8. Endpoint

| Endpoint | Auth | Fungsi |
|---|---|---|
| `GET /login.html` | publik | Halaman login |
| `POST /api/login` | publik | Login, set cookie JWT |
| `POST /api/logout` | publik | Hapus cookie sesi |
| `GET /api/me` | JWT | Info user dan expiry sesi |
| `GET /` | JWT | Dashboard `home.html` |
| `GET /engine.html` | JWT / overlay token | Game default |
| `GET /snake.html` | JWT / overlay token | Game legacy |
| `GET /api/status` | JWT | Status bot + statistik |
| `GET /api/config` | JWT | Path game, port WS, overlay token |
| `GET /api/songs` | JWT / overlay token | Daftar backsound di folder `mp3/` |
| `GET /sfx/*.mp3` | JWT / overlay token | Sound belok `right/left/up/down.mp3` |
| `GET /healthz` | publik | Health check Docker |
| `POST /api/start` | JWT + CSRF | Mulai koneksi TikTok Live |
| `POST /api/stop` | JWT + CSRF | Hentikan koneksi |
| `POST /api/test-gift` | JWT + CSRF | Simulasi gift |
| `WS /ws` | JWT / overlay token | Relay gift real-time |

## 9. Troubleshooting

### Container restart terus / tidak mau start

Cek log:

```bash
docker compose logs --tail=50 snake-live
```

Penyebab paling umum: `ADMIN_USERNAME` kosong, atau `ADMIN_PASSWORD` dan
`ADMIN_PASSWORD_HASH` dua-duanya kosong di `.env`. Isi kredensial, lalu recreate
container.

### Login selalu gagal / 401

- Pastikan username/password sama dengan `.env`.
- Setelah mengubah `.env`, jalankan
  `docker compose up -d --force-recreate snake-live`.
- Jika memakai `ADMIN_PASSWORD_HASH`, pastikan formatnya
  `pbkdf2_sha256:iterasi:salt:hash` dan tidak terpotong.

### Terlalu banyak percobaan (429)

Tunggu sesuai `Retry-After` (default 5 menit), atau restart container untuk
menghapus rate limiter in-memory:

```bash
docker compose restart snake-live
```

### Login berhasil tapi langsung logout

- Pastikan akses lewat HTTPS dan NPM meneruskan `X-Forwarded-Proto: https`.
- Jika akses lewat HTTP langsung, gunakan `COOKIE_SECURE=auto` atau `false`.
- Pastikan `PUBLIC_ORIGIN` sama dengan origin di browser.

### Dashboard 502 dari NPM

- Pastikan container hidup: `docker compose ps`
- Pastikan NPM berada di network `app-network`
- Pastikan forward hostname `snake-live` dan port `8001`

### Log gift tidak muncul / WebSocket disconnect

- Pastikan custom location `/ws` ada dan Websockets Support ON
- DevTools → Network → WS, cek `wss://domain/ws` status `101`
- Jangan arahkan browser ke `:8766` langsung saat memakai HTTPS
- Close code `1008` = sesi login tidak valid atau overlay token salah

### OBS menampilkan halaman login

- Gunakan link dari tombol **Salin Link OBS** yang berisi `?token=`.
- Pastikan `OVERLAY_TOKEN` di server sama dengan token di URL.
- Pastikan NPM Access List tidak menghalangi OBS.

### Bot tidak connect ke TikTok

- Pastikan akun sedang live
- Cek log: `docker compose logs -f`
- TikTok kadang memblokir IP datacenter/VPS
- Versi `TikTokLive` sudah di-pin di `requirements.txt`

### Audio/BGM

- Backsound diputar oleh **browser/OBS** dari file di folder `mp3/`.
- Jika `mp3/` kosong, game berjalan tanpa backsound (tidak ada error).
- Lagu diputar berurutan lalu diulang otomatis. Format yang didukung:
  `.mp3`, `.ogg`, `.wav`, `.flac`, `.m4a`.
- Sound belok memakai `sfx/right.mp3`, `sfx/left.mp3`, `sfx/up.mp3`, dan
  `sfx/down.mp3`. Bunyi hanya saat arah ular berubah.
- Volume default ada di `engine.html` dan `snake.html`:
  `VOL_BGM=.10` (10%) dan `VOL_SFX=.25` (25%).
- Link OBS dengan `?token=` otomatis ikut memuat BGM/SFX.
- BGM pygame server sekarang **opt-in**: default mati karena backsound sudah
  diputar browser/OBS. Aktifkan hanya jika memang ingin audio keluar dari
  mesin server dengan `BGM=true` (bisa dobel jika dibuka di PC yang sama).

## 10. Catatan keamanan

- Jangan commit `.env`; file ini berisi password login.
- Set permission `.env` ke `600` di VPS.
- Gunakan HTTPS (NPM + Let's Encrypt) dan Force SSL.
- Jangan publish port `8001`/`8766` ke publik.
- Jangan gunakan overlay token sebagai password admin.
- Backup volume `snake-data` jika ingin menyimpan JWT/overlay secret.
- `server.py` hanya menyajikan `login.html`, `home.html`, `engine.html`,
  `snake.html`, file `mp3/`, dan file `sfx/`; source code dan directory
  listing diblokir.

## Parameter performa HP (engine.html)

Tambahkan di akhir URL, contoh: `https://domain/engine.html?res=720&fps=30&fit=0.85`

| Parameter | Nilai | Keterangan |
|---|---|---|
| `res` | `480` / `540` / `720` / `1080` / `auto` | Lebar buffer render. Tinggi mengikuti rasio layar (layar 9:16: 720 = 720x1280, 1080 = 1080x1920). `auto` = **pas penuh layar tanpa celah hitam** (ukuran tampilan otomatis 100%, buffer mengikuti piksel layar; di HP sentuh layar sekali untuk masuk layar penuh). Default: HP `720`, desktop/OBS `1080` |
| `fps` | `30` / `60` | Batas FPS render. Default HP `30`, desktop bebas |
| `fit` | `0.4` - `1` (40% - 100%) | Ukuran tampilan di layar. Default HP `0.85`, desktop `1`. Kecilkan jika terasa kebesaran |
| `lite` | `1` / `0` | `1` = mode ringan (tanpa blur/bayangan CSS berat, geometri lebih sederhana). Default aktif otomatis di HP |

Rekomendasi: HP menengah `?res=540&fps=30`, HP lemah `?res=480&fps=30`, HP kuat `?res=1080&fps=60`.

Semua pilihan di atas juga ada di dashboard (`home.html`) pada kartu bot: **Resolusi**, **FPS**, dan **Ukuran tampilan**. Pilihan tersimpan di browser dan dipakai oleh tombol "Jalankan & Buka Game", "Buka Layar Game Saja", dan "Salin Link OBS".

## Logic AI ular (engine.html & snake.html)

Prioritas: **bertahan hidup > menjaga ruang gerak > mengejar apel.**

- **Keselamatan:** ular hanya memilih jalur yang terbukti aman. Posisi ekor dihitung pada setiap tick (ekor bergeser, tumbuh saat makan). Jalur tidak pernah memotong ekor/badan sendiri.
- **Ruang gerak:** setiap rencana disimulasikan sampai ular selesai makan. Jalan pintas yang membuat kepala terkurung di kantong sempit atau meninggalkan banyak sel kosong terisolasi ditolak/dihukum, makin ketat ketika papan makin penuh.
- **Apel:** dari beberapa kandidat, ular menyimulasikan makan apel itu lalu menilai apel berikutnya (lookahead 2 apel). Target dipertahankan (tidak plin-plan).
- **Gerakan natural:** jalur lurus diutamakan; belok dihitung mahal bila terlalu dini, sehingga ular baru bermanuver mendekati apel.
- **Fallback:** jika tidak ada apel atau apel tidak aman dikejar, ular mengikuti jalur siklus (mengekor jalur sendiri) sampai ada peluang lebih baik.

Bobot AI bisa disetel lewat konstanta `AI_TUNE` di blok `//LOGIC_START` pada `engine.html`.
