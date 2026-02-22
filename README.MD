# 🚀 BlastPro – Ultimate Telegram Automation SaaS

**BlastPro** adalah platform **Software as a Service (SaaS)** berbasis web yang memungkinkan **UMKM dan pebisnis** mengelola pemasaran Telegram secara **otomatis, aman, dan terukur**.
Platform **SaaS otomasi Telegram** berbasis Flask + Telethon untuk kebutuhan broadcast grup, pengelolaan target, CRM kontak Telegram, auto-reply, penjadwalan kampanye, dan notifikasi via Telegram Bot.

README ini dibuat untuk dua audiens sekaligus:
- **User/Owner bisnis** → supaya cepat paham fungsi aplikasi dan alur pakainya.
- **Developer** → supaya cepat paham arsitektur, modul, dependensi, konfigurasi, dan cara pengembangan.
---

## 1) Gambaran Proyek

BlastPro adalah aplikasi web untuk membantu pengguna:
1. Login akun Telegram mereka dari dashboard.
2. Memindai grup/topik yang diikuti.
3. Menyimpan target campaign.
4. Mengirim pesan blast (teks/gambar) ke banyak target.
5. Menjalankan blast terjadwal.
6. Mengelola data kontak (CRM) dari interaksi Telegram.
7. Menerima laporan/notifikasi di Telegram bot pendamping.

### Cocok untuk siapa?
- Tim sales/marketing yang promosi rutin lewat Telegram.
- UMKM atau agency yang butuh repeat campaign.
- Operator yang butuh panel admin dan monitoring user.

---

## 2) Fitur Utama

### A. Manajemen Akun & Autentikasi
- Register, login, logout.
- Verifikasi email.
- Lupa password + reset password via email.
- Session web dengan pengamanan cookie.
- Proteksi keamanan tambahan (sanitizer, anti spam, token manager, dsb).

### B. Koneksi Telegram Userbot
- Login akun Telegram via OTP (send code + verify code).
- Dukungan akun dengan 2FA (cloud password).
- Dukungan QR login.
- Session Telegram disimpan agar tetap aktif lintas restart (berbasis data DB).
- Fitur disconnect akun Telegram dari dashboard.

### C. Targeting & Broadcast
- Scan grup yang diikuti akun Telegram.
- Simpan target grup secara bulk.
- Dukungan topik/forum (topic IDs).
- Broadcast teks dan media (gambar).
- Fitur stop broadcast.
- Template pesan dan personalisasi/spintax.
- Logging hasil blast per target.

### D. Scheduler & Campaign Management
- Buat/edit/hapus jadwal blast.
- Set waktu kirim (jam/menit).
- Aktif/nonaktif jadwal campaign.

### E. CRM Telegram
- Ambil data user Telegram dari interaksi/chats.
- Lihat daftar CRM user.
- Import CRM (API/CSV).
- Export CRM ke CSV.
- Hapus CRM satuan/massal.

### F. Auto Reply
- Pengaturan auto-reply per user.
- CRUD keyword rule balasan otomatis.
- Toggle aktif/nonaktif auto-reply.
- Log balasan otomatis.

### G. Billing / Paket / Wallet
- Halaman payment + checkout.
- Pengelolaan transaksi.
- Admin bank & approval transaksi.
- Pricing plan dan variant.

### H. Super Admin
- Dashboard admin global.
- Monitoring user.
- Detail user.
- Ubah plan user.
- Reset session user.
- Reset password user.
- Ban/unban user.
- Kelola pricing.
- Monitoring finance & approval transaksi.

### I. Telegram Notification Bot (Pendamping)
- Bot Telegram terpisah untuk notifikasi dan mini-dashboard.
- Deep-link koneksi bot ke akun web user.
- Menu laporan blast, akun, jadwal, wallet.
- Pagination laporan/log.

### J. Reliability Worker
- Worker self-ping (`/ping`) untuk mengurangi risiko app “tidur” di free-tier hosting.

---

## 3) Arsitektur Singkat

### Komponen inti
- **Web App**: `Flask` (`app.py`) sebagai API + server template HTML.
- **Telegram Userbot Engine**: `Telethon` untuk operasi akun Telegram user.
- **Database/API Layer**: `Supabase` client (PostgreSQL via API).
- **Notification Bot**: `python-telegram-bot` (`bot.py`) untuk pelaporan ke Telegram.
- **Mail Service**: util SMTP (`utils/mailer.py`) untuk verifikasi/reset password.
- **Security Layer**: util keamanan (`utils/security.py`).

### Alur data sederhana
1. User akses dashboard web.
2. User koneksikan akun Telegram.
3. Data akun/target/template/jadwal disimpan ke DB.
4. Proses blast membaca konfigurasi user + target.
5. Hasil blast disimpan ke log.
6. Notifikasi/laporan bisa dikirim ke Telegram bot.

---

## 4) Struktur Direktori Proyek

```text
.
├── app.py                  # Entry utama Flask + route web/API + core logic blast
├── bot.py                  # Bot Telegram notifikasi/mini-dashboard
├── demo_routes.py          # Blueprint demo route (mock data untuk demo UI)
├── requirements.txt        # Dependensi Python
├── README.MD               # Dokumentasi proyek
├── LICENSE
├── BlastPro.db             # File DB lokal (legacy/dev)
├── saas_database.db        # File DB lokal (legacy/dev)
│
├── utils/
│   ├── security.py         # Password vault, token, anti spam, session defense
│   └── mailer.py           # SMTP mailer (verifikasi/reset password)
│
├── static/
│   └── css/
│       ├── landing.css
│       ├── auth.css
│       ├── dashboard.css
│       └── admin.css
│
└── templates/
    ├── landing/            # Landing page publik
    ├── auth/               # Login / Register
    ├── dashboard/          # UI user dashboard
    └── admin/              # UI super admin
```

---

## 5) Halaman & Endpoint Penting (Ringkasan)

> Catatan: daftar ini ringkasan endpoint utama untuk orientasi cepat.

### Publik & Auth
- `/` landing.
- `/login`, `/register`, `/logout`.
- `/forgot-password`, `/reset-password/<token>`.
- `/verify/<token>`.

### Dashboard User
- `/dashboard` (overview).
- `/dashboard/broadcast`.
- `/dashboard/targets`.
- `/dashboard/schedule`.
- `/dashboard/templates`.
- `/dashboard/crm`.
- `/dashboard/connection`.
- `/dashboard/profile`.
- `/dashboard/payment`.
- `/dashboard/auto-reply`.

### API Operasional
- `/api/connect/send_code`, `/api/connect/verify_code`.
- `/api/connect/get_qr`, `/api/connect/check_qr`, `/api/connect/submit_2fa`.
- `/scan_groups_api`, `/save_bulk_targets`.
- `/start_broadcast`, `/api/broadcast/stop`.
- `/api/get_crm_users`, `/import_crm_api`, `/import_crm_csv`, `/export_crm_csv`.
- endpoint pengelolaan template, target, jadwal, keyword, dsb.

### Super Admin
- `/super-admin` + sub-pages user/pricing/finance.
- endpoint aksi admin: update plan, reset session/password, ban user, approve transaksi.

### Demo
- `/live-demo/<page>` dari blueprint `demo_routes.py` untuk menampilkan demo halaman.

---

## 6) Skema Data (Supabase) yang Digunakan Aplikasi

Tabel yang dipakai di kode:
- `users`
- `telegram_accounts`
- `blast_targets`
- `blast_schedules`
- `blast_logs`
- `tele_users`
- `message_templates`
- `auto_reply_settings`
- `keyword_rules`
- `reply_logs`
- `transactions`
- `admin_banks`
- `pricing_plans`
- `pricing_variants`

Relasi umum:
- `users` sebagai entitas utama tenant.
- Setiap user dapat punya 1 akun Telegram aktif (`telegram_accounts`) dan banyak data turunan (targets, schedules, logs, CRM, template, transaksi, dsb).

> Disarankan membuat migration/SQL schema terpisah (`schema.sql`) agar setup lingkungan baru lebih cepat dan konsisten.

---

## 7) Konfigurasi Environment Variable

Minimal variabel penting:

### Core app
- `SECRET_KEY`
- `FLASK_ENV`
- `SUPABASE_URL`
- `SUPABASE_KEY`
- `API_ID` (Telegram API ID)
- `API_HASH` (Telegram API HASH)
- `SITE_URL` atau `RENDER_EXTERNAL_URL` (untuk self-ping)

### Notification bot
- `NOTIF_BOT_TOKEN`
- `NOTIF_BOT_USERNAME` (opsional)
- `ENABLE_BOT_POLLING` (`true/false`, opsional)

### Email (SMTP)
- `SMTP_SERVER` (default `smtp.gmail.com`)
- `SMTP_PORT` (default `587`)
- `SENDER_EMAIL`
- `SENDER_PASSWORD` (gunakan app password)

### Bootstrap super admin
- `SUPER_ADMIN`
- `PASS_ADMIN`

Contoh `.env`:

```env
SECRET_KEY=change-this-secret
FLASK_ENV=development

SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-service-or-anon-key

API_ID=123456
API_HASH=xxxxxxxxxxxxxxxxxxxxxxxxxxxx

SITE_URL=http://127.0.0.1:5000

NOTIF_BOT_TOKEN=123456:ABCDEF...
NOTIF_BOT_USERNAME=your_bot_username
ENABLE_BOT_POLLING=false

SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SENDER_EMAIL=your@email.com
SENDER_PASSWORD=your-app-password

SUPER_ADMIN=admin@example.com
PASS_ADMIN=strong-password
```

---

## 8) Instalasi & Menjalankan Proyek (Lokal)

### Prasyarat
- Python 3.10+ (disarankan 3.11)
- Pip
- Akun Supabase + schema siap
- API Telegram (`api_id`, `api_hash`)

### Langkah
```bash
# 1) Clone
git clone <repo-url>
cd CrmBlastGrupAutomation

# 2) Virtual env
python -m venv .venv
source .venv/bin/activate   # Linux/macOS
# .venv\Scripts\activate    # Windows

# 3) Install dependency
pip install -r requirements.txt

# 4) Siapkan env
cp .env.example .env   # jika belum ada, buat manual

# 5) Jalankan app
python app.py
```

Akses aplikasi di browser (umumnya):
- `http://127.0.0.1:5000`

Jika ingin menjalankan bot notifikasi terpisah:
```bash
python bot.py
```

---

## 9) Deployment (Ringkas)

Aplikasi ini cocok untuk deployment model PaaS (mis. Render) dengan `gunicorn`.

Checklist produksi:
- Set semua env var penting.
- Gunakan `FLASK_ENV=production`.
- Pastikan `SECRET_KEY` kuat.
- Pastikan cookie secure aktif di HTTPS.
- Validasi koneksi Supabase dan Telegram API.
- Jalankan bot polling bila diperlukan (`ENABLE_BOT_POLLING=true`) atau gunakan proses terpisah.

---

## 10) Catatan Keamanan & Operasional

- Jangan commit kredensial API/token/password ke repo.
- Gunakan app password untuk SMTP, bukan password email utama.
- Batasi akses key Supabase sesuai kebutuhan environment.
- Audit input user (sudah ada util sanitizer, tetap perlu review berkala).
- Monitoring log blast sangat penting untuk mendeteksi flood/error Telegram.
- Pertimbangkan queue worker dedicated jika trafik blast bertambah besar.

---

## 11) FAQ Singkat

### Q: Ini aplikasi auto kirim spam?
A: Tujuan utamanya automasi komunikasi/campaign. Gunakan secara etis, sesuai kebijakan Telegram dan regulasi setempat.

### Q: Bisa multi-user?
A: Ya, arsitektur aplikasi dirancang multi-tenant (isolasi data per user).

### Q: Kalau server restart, akun Telegram putus?
A: Secara desain session Telegram disimpan di database agar bisa dipulihkan.

### Q: Kenapa ada `demo_routes.py`?
A: Untuk mode demo halaman dengan data dummy (membantu presentasi/preview).

---

## 12) Roadmap Pengembangan yang Direkomendasikan

- Tambahkan `schema.sql` + migrasi versioning.
- Pisahkan service layer (auth, blast, billing, crm) dari `app.py` agar modular.
- Tambahkan test otomatis (unit + integration).
- Tambahkan observability (structured log, metrics, alerting).
- Gunakan task queue (Celery/RQ) untuk broadcast skala besar.
- Tambahkan rate limiting API publik dan audit log keamanan.

---

## 13) Lisensi

Lihat file [LICENSE](LICENSE) untuk detail lisensi penggunaan.

---

## 14) Ringkasan Super Singkat (untuk non-teknis)

BlastPro adalah “**dashboard kirim campaign Telegram**” yang membantu bisnis:
- mengelola target,
- mengirim pesan massal,
- mengatur jadwal,
- menyimpan data prospek,
- dan memantau hasil secara rapi.

Developer mendapat fondasi Flask + Telethon + Supabase yang sudah mencakup auth, admin, CRM, billing, dan integrasi bot notifikasi.
