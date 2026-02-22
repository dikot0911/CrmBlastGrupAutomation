 (cd "$(git rev-parse --show-toplevel)" && git apply --3way <<'EOF' 
diff --git a/README.MD b/README.MD
index 7a4f800ea50eab89c997e4404b564f3116a8196a..b53a992dc73100920b22d879a0c21c672e76442c 100644
--- a/README.MD
+++ b/README.MD
@@ -1,266 +1,381 @@
-# 🚀 BlastPro – Ultimate Telegram Automation SaaS
+# BlastPro / CrmBlastGrupAutomation
 
-**BlastPro** adalah platform **Software as a Service (SaaS)** berbasis web yang memungkinkan **UMKM dan pebisnis** mengelola pemasaran Telegram secara **otomatis, aman, dan terukur**.
+Platform **SaaS otomasi Telegram** berbasis Flask + Telethon untuk kebutuhan broadcast grup, pengelolaan target, CRM kontak Telegram, auto-reply, penjadwalan kampanye, dan notifikasi via Telegram Bot.
 
-Platform ini menggunakan arsitektur **Multi-Tenant**, memungkinkan **satu server menangani ribuan akun Telegram pengguna** tanpa perlu menyewa VPS masing-masing.
+README ini dibuat untuk dua audiens sekaligus:
+- **User/Owner bisnis** → supaya cepat paham fungsi aplikasi dan alur pakainya.
+- **Developer** → supaya cepat paham arsitektur, modul, dependensi, konfigurasi, dan cara pengembangan.
 
 ---
 
-## 🔥 Fitur Unggulan
+## 1) Gambaran Proyek (Versi Mudah Dipahami)
 
-### 🤖 1. Multi-Tenant Userbot Core
-Sistem inti yang memungkinkan satu server menangani banyak akun Telegram secara bersamaan.
+BlastPro adalah aplikasi web untuk membantu pengguna:
+1. Login akun Telegram mereka dari dashboard.
+2. Memindai grup/topik yang diikuti.
+3. Menyimpan target campaign.
+4. Mengirim pesan blast (teks/gambar) ke banyak target.
+5. Menjalankan blast terjadwal.
+6. Mengelola data kontak (CRM) dari interaksi Telegram.
+7. Menerima laporan/notifikasi di Telegram bot pendamping.
 
-- **Web-Based Login**  
-  Login Telegram menggunakan nomor HP & OTP langsung melalui dashboard web yang aman.
+### Cocok untuk siapa?
+- Tim sales/marketing yang promosi rutin lewat Telegram.
+- UMKM atau agency yang butuh repeat campaign.
+- Operator yang butuh panel admin dan monitoring user.
 
-- **Stateless Authentication**  
-  Sesi Telegram disimpan di database, tidak terputus walaupun server restart, redeploy, atau ganti worker.
+---
 
-- **2FA Support**  
-  Mendukung akun Telegram dengan Cloud Password (verifikasi dua langkah).
+## 2) Fitur Utama
+
+### A. Manajemen Akun & Autentikasi
+- Register, login, logout.
+- Verifikasi email.
+- Lupa password + reset password via email.
+- Session web dengan pengamanan cookie.
+- Proteksi keamanan tambahan (sanitizer, anti spam, token manager, dsb).
+
+### B. Koneksi Telegram Userbot
+- Login akun Telegram via OTP (send code + verify code).
+- Dukungan akun dengan 2FA (cloud password).
+- Dukungan QR login.
+- Session Telegram disimpan agar tetap aktif lintas restart (berbasis data DB).
+- Fitur disconnect akun Telegram dari dashboard.
+
+### C. Targeting & Broadcast
+- Scan grup yang diikuti akun Telegram.
+- Simpan target grup secara bulk.
+- Dukungan topik/forum (topic IDs).
+- Broadcast teks dan media (gambar).
+- Fitur stop broadcast.
+- Template pesan dan personalisasi/spintax.
+- Logging hasil blast per target.
+
+### D. Scheduler & Campaign Management
+- Buat/edit/hapus jadwal blast.
+- Set waktu kirim (jam/menit).
+- Aktif/nonaktif jadwal campaign.
+
+### E. CRM Telegram
+- Ambil data user Telegram dari interaksi/chats.
+- Lihat daftar CRM user.
+- Import CRM (API/CSV).
+- Export CRM ke CSV.
+- Hapus CRM satuan/massal.
+
+### F. Auto Reply
+- Pengaturan auto-reply per user.
+- CRUD keyword rule balasan otomatis.
+- Toggle aktif/nonaktif auto-reply.
+- Log balasan otomatis.
+
+### G. Billing / Paket / Wallet
+- Halaman payment + checkout.
+- Pengelolaan transaksi.
+- Admin bank & approval transaksi.
+- Pricing plan dan variant.
+
+### H. Super Admin
+- Dashboard admin global.
+- Monitoring user.
+- Detail user.
+- Ubah plan user.
+- Reset session user.
+- Reset password user.
+- Ban/unban user.
+- Kelola pricing.
+- Monitoring finance & approval transaksi.
+
+### I. Telegram Notification Bot (Pendamping)
+- Bot Telegram terpisah untuk notifikasi dan mini-dashboard.
+- Deep-link koneksi bot ke akun web user.
+- Menu laporan blast, akun, jadwal, wallet.
+- Pagination laporan/log.
+
+### J. Reliability Worker
+- Worker self-ping (`/ping`) untuk mengurangi risiko app “tidur” di free-tier hosting.
 
 ---
 
-### 🎯 2. Advanced Targeting & Scanner
-- **Group Scanner**  
-  Memindai otomatis semua grup dan forum yang diikuti akun Telegram pengguna.
-
-- **Forum Topic Support (Premium)**  
-  Mendeteksi dan mengirim pesan ke topic spesifik dalam grup forum.
+## 3) Arsitektur Singkat
 
-- **Bulk Save**  
-  Menyimpan ratusan target grup ke database dalam satu klik.
+### Komponen inti
+- **Web App**: `Flask` (`app.py`) sebagai API + server template HTML.
+- **Telegram Userbot Engine**: `Telethon` untuk operasi akun Telegram user.
+- **Database/API Layer**: `Supabase` client (PostgreSQL via API).
+- **Notification Bot**: `python-telegram-bot` (`bot.py`) untuk pelaporan ke Telegram.
+- **Mail Service**: util SMTP (`utils/mailer.py`) untuk verifikasi/reset password.
+- **Security Layer**: util keamanan (`utils/security.py`).
 
-- **Isolasi Data**  
-  Data target grup setiap user terpisah dan aman (multi-tenant ready).
+### Alur data sederhana
+1. User akses dashboard web.
+2. User koneksikan akun Telegram.
+3. Data akun/target/template/jadwal disimpan ke DB.
+4. Proses blast membaca konfigurasi user + target.
+5. Hasil blast disimpan ke log.
+6. Notifikasi/laporan bisa dikirim ke Telegram bot.
 
 ---
 
-### 📢 3. Smart Broadcast System
-- **Text & Image**  
-  Kirim pesan teks dan gambar promo (upload supported).
+## 4) Struktur Direktori Proyek
+
+```text
+.
+├── app.py                  # Entry utama Flask + route web/API + core logic blast
+├── bot.py                  # Bot Telegram notifikasi/mini-dashboard
+├── demo_routes.py          # Blueprint demo route (mock data untuk demo UI)
+├── requirements.txt        # Dependensi Python
+├── README.MD               # Dokumentasi proyek
+├── LICENSE
+├── BlastPro.db             # File DB lokal (legacy/dev)
+├── saas_database.db        # File DB lokal (legacy/dev)
+│
+├── utils/
+│   ├── security.py         # Password vault, token, anti spam, session defense
+│   └── mailer.py           # SMTP mailer (verifikasi/reset password)
+│
+├── static/
+│   └── css/
+│       ├── landing.css
+│       ├── auth.css
+│       ├── dashboard.css
+│       └── admin.css
+│
+└── templates/
+    ├── landing/            # Landing page publik
+    ├── auth/               # Login / Register
+    ├── dashboard/          # UI user dashboard
+    └── admin/              # UI super admin
+```
+
+---
 
-- **Personalized Message**  
-  Variable replacement seperti:
-Menyesuaikan nama dari database CRM.
+## 5) Halaman & Endpoint Penting (Ringkasan)
+
+> Catatan: daftar ini ringkasan endpoint utama untuk orientasi cepat.
+
+### Publik & Auth
+- `/` landing.
+- `/login`, `/register`, `/logout`.
+- `/forgot-password`, `/reset-password/<token>`.
+- `/verify/<token>`.
+
+### Dashboard User
+- `/dashboard` (overview).
+- `/dashboard/broadcast`.
+- `/dashboard/targets`.
+- `/dashboard/schedule`.
+- `/dashboard/templates`.
+- `/dashboard/crm`.
+- `/dashboard/connection`.
+- `/dashboard/profile`.
+- `/dashboard/payment`.
+- `/dashboard/auto-reply`.
+
+### API Operasional
+- `/api/connect/send_code`, `/api/connect/verify_code`.
+- `/api/connect/get_qr`, `/api/connect/check_qr`, `/api/connect/submit_2fa`.
+- `/scan_groups_api`, `/save_bulk_targets`.
+- `/start_broadcast`, `/api/broadcast/stop`.
+- `/api/get_crm_users`, `/import_crm_api`, `/import_crm_csv`, `/export_crm_csv`.
+- endpoint pengelolaan template, target, jadwal, keyword, dsb.
+
+### Super Admin
+- `/super-admin` + sub-pages user/pricing/finance.
+- endpoint aksi admin: update plan, reset session/password, ban user, approve transaksi.
+
+### Demo
+- `/live-demo/<page>` dari blueprint `demo_routes.py` untuk menampilkan demo halaman.
 
-- **Anti-Flood Mechanism**  
-Delay cerdas + auto handle `FloodWaitError` untuk mencegah banned.
+---
 
-- **Background Processing**  
-Blast berjalan di background thread, UI dashboard tetap responsif.
+## 6) Skema Data (Supabase) yang Digunakan Aplikasi
+
+Tabel yang dipakai di kode:
+- `users`
+- `telegram_accounts`
+- `blast_targets`
+- `blast_schedules`
+- `blast_logs`
+- `tele_users`
+- `message_templates`
+- `auto_reply_settings`
+- `keyword_rules`
+- `reply_logs`
+- `transactions`
+- `admin_banks`
+- `pricing_plans`
+- `pricing_variants`
+
+Relasi umum:
+- `users` sebagai entitas utama tenant.
+- Setiap user dapat punya 1 akun Telegram aktif (`telegram_accounts`) dan banyak data turunan (targets, schedules, logs, CRM, template, transaksi, dsb).
+
+> Disarankan membuat migration/SQL schema terpisah (`schema.sql`) agar setup lingkungan baru lebih cepat dan konsisten.
 
 ---
 
-### 👥 4. CRM Otomatis
-- **Chat Mining**  
-Memindai riwayat chat pribadi (PM) untuk mengumpulkan:
-- Username
-- Nama
-- ID Telegram
-- Waktu interaksi terakhir
+## 7) Konfigurasi Environment Variable
+
+Minimal variabel penting:
+
+### Core app
+- `SECRET_KEY`
+- `FLASK_ENV`
+- `SUPABASE_URL`
+- `SUPABASE_KEY`
+- `API_ID` (Telegram API ID)
+- `API_HASH` (Telegram API HASH)
+- `SITE_URL` atau `RENDER_EXTERNAL_URL` (untuk self-ping)
+
+### Notification bot
+- `NOTIF_BOT_TOKEN`
+- `NOTIF_BOT_USERNAME` (opsional)
+- `ENABLE_BOT_POLLING` (`true/false`, opsional)
+
+### Email (SMTP)
+- `SMTP_SERVER` (default `smtp.gmail.com`)
+- `SMTP_PORT` (default `587`)
+- `SENDER_EMAIL`
+- `SENDER_PASSWORD` (gunakan app password)
+
+### Bootstrap super admin
+- `SUPER_ADMIN`
+- `PASS_ADMIN`
+
+Contoh `.env`:
+
+```env
+SECRET_KEY=change-this-secret
+FLASK_ENV=development
+
+SUPABASE_URL=https://your-project.supabase.co
+SUPABASE_KEY=your-service-or-anon-key
+
+API_ID=123456
+API_HASH=xxxxxxxxxxxxxxxxxxxxxxxxxxxx
+
+SITE_URL=http://127.0.0.1:5000
 
-- **Database Pelanggan**  
-Data tersimpan rapi per user dan siap untuk follow-up massal.
+NOTIF_BOT_TOKEN=123456:ABCDEF...
+NOTIF_BOT_USERNAME=your_bot_username
+ENABLE_BOT_POLLING=false
+
+SMTP_SERVER=smtp.gmail.com
+SMTP_PORT=587
+SENDER_EMAIL=your@email.com
+SENDER_PASSWORD=your-app-password
+
+SUPER_ADMIN=admin@example.com
+PASS_ADMIN=strong-password
+```
 
 ---
 
-### 🛡️ 5. Super Admin (God Mode)
-Panel khusus pemilik platform untuk kontrol penuh.
+## 8) Instalasi & Menjalankan Proyek (Lokal)
+
+### Prasyarat
+- Python 3.10+ (disarankan 3.11)
+- Pip
+- Akun Supabase + schema siap
+- API Telegram (`api_id`, `api_hash`)
+
+### Langkah
+```bash
+# 1) Clone
+git clone <repo-url>
+cd CrmBlastGrupAutomation
+
+# 2) Virtual env
+python -m venv .venv
+source .venv/bin/activate   # Linux/macOS
+# .venv\Scripts\activate    # Windows
 
-- **User Management**  
-Monitor seluruh user dan status bot mereka.
+# 3) Install dependency
+pip install -r requirements.txt
 
-- **Kill Switch**  
-Suspend/Ban bot user secara instan.
+# 4) Siapkan env
+cp .env.example .env   # jika belum ada, buat manual
 
-- **Realtime Statistics**  
-Statistik global user, bot aktif, dan kesehatan server.
+# 5) Jalankan app
+python app.py
+```
+
+Akses aplikasi di browser (umumnya):
+- `http://127.0.0.1:5000`
+
+Jika ingin menjalankan bot notifikasi terpisah:
+```bash
+python bot.py
+```
 
 ---
 
-### ⚡ 6. Stabilitas Server (Anti-Tidur)
-- **Self-Ping Worker**  
-Server melakukan ping ke dirinya sendiri setiap 14 menit menggunakan `httpx`.
+## 9) Deployment (Ringkas)
+
+Aplikasi ini cocok untuk deployment model PaaS (mis. Render) dengan `gunicorn`.
 
-- **Manfaat**  
-Mencegah layanan Free Tier (seperti Render) masuk mode tidur (spin down), bot selalu aktif 24/7 tanpa cold start.
+Checklist produksi:
+- Set semua env var penting.
+- Gunakan `FLASK_ENV=production`.
+- Pastikan `SECRET_KEY` kuat.
+- Pastikan cookie secure aktif di HTTPS.
+- Validasi koneksi Supabase dan Telegram API.
+- Jalankan bot polling bila diperlukan (`ENABLE_BOT_POLLING=true`) atau gunakan proses terpisah.
 
 ---
 
-## 🏗️ Arsitektur & Teknologi
+## 10) Catatan Keamanan & Operasional
 
-### Backend Stack
-- **Python Flask** – Web framework utama
-- **Telethon** – Telegram MTProto async client
-- **Supabase (PostgreSQL API)** – Database stateless via REST
-- **Asyncio Bridge** – Menjalankan async Telethon di Flask/Gunicorn
-- **Httpx** – HTTP client untuk self-ping & background task
+- Jangan commit kredensial API/token/password ke repo.
+- Gunakan app password untuk SMTP, bukan password email utama.
+- Batasi akses key Supabase sesuai kebutuhan environment.
+- Audit input user (sudah ada util sanitizer, tetap perlu review berkala).
+- Monitoring log blast sangat penting untuk mendeteksi flood/error Telegram.
+- Pertimbangkan queue worker dedicated jika trafik blast bertambah besar.
 
 ---
 
-## 🗄️ Database Schema (PostgreSQL)
+## 11) FAQ Singkat
 
-Relasi **One-to-Many**  
-Satu User → Satu Akun Telegram → Banyak Target, Jadwal, dan Log
+### Q: Ini aplikasi auto kirim spam?
+A: Tujuan utamanya automasi komunikasi/campaign. Gunakan secara etis, sesuai kebijakan Telegram dan regulasi setempat.
 
-### Tabel
-- `users` – Akun web & role (admin/banned)
-- `telegram_accounts` – Session String Telethon
-- `blast_targets` – Target grup & topic
-- `blast_schedules` – Jadwal blast harian
-- `blast_logs` – Log pengiriman
-- `tele_users` – CRM (kontak Telegram)
+### Q: Bisa multi-user?
+A: Ya, arsitektur aplikasi dirancang multi-tenant (isolasi data per user).
 
-> **Note:**  
-> Kolom `targets` pada `telegram_accounts` digunakan sementara untuk menyimpan Hash OTP saat proses login (Stateless Auth Flow).
+### Q: Kalau server restart, akun Telegram putus?
+A: Secara desain session Telegram disimpan di database agar bisa dipulihkan.
+
+### Q: Kenapa ada `demo_routes.py`?
+A: Untuk mode demo halaman dengan data dummy (membantu presentasi/preview).
 
 ---
 
-## 📂 Struktur Folder Proyek
-/
-├── app.py # OTAK UTAMA: Flask, Bot Logic, Auth & Workers
-├── requirements.txt # Dependency Python
-├── Procfile # Konfigurasi Render (Gunicorn)
-│
-├── static/
-│ ├── css/
-│ │ ├── admin.css # Dark Mode Super Admin
-│ │ ├── auth.css # Login/Register (Glassmorphism)
-│ │ ├── dashboard.css # Dashboard User
-│ │ └── landing.css # Landing Page
-│ └── uploads/ # Image broadcast sementara
-│
-└── templates/
-  ├── landing/
-  ├── auth/
-  ├── dashboard/
-  └── admin/
+## 12) Roadmap Pengembangan yang Direkomendasikan
 
+- Tambahkan `schema.sql` + migrasi versioning.
+- Pisahkan service layer (auth, blast, billing, crm) dari `app.py` agar modular.
+- Tambahkan test otomatis (unit + integration).
+- Tambahkan observability (structured log, metrics, alerting).
+- Gunakan task queue (Celery/RQ) untuk broadcast skala besar.
+- Tambahkan rate limiting API publik dan audit log keamanan.
 
 ---
 
-## 🚀 Panduan Instalasi & Deploy
-
-### 1️⃣ Persiapan Database (Supabase)
-
-Buat project di Supabase → SQL Editor → Jalankan script berikut:
-
-```sql
--- USER WEB
-create table public.users (
-  id bigint generated by default as identity primary key,
-  email text unique not null,
-  password text not null,
-  is_admin boolean default false,
-  is_banned boolean default false,
-  created_at timestamp with time zone default timezone('utc'::text, now()) not null
-);
-
--- SESI TELEGRAM
-create table public.telegram_accounts (
-  id bigint generated by default as identity primary key,
-  user_id bigint references public.users(id) on delete cascade not null unique,
-  phone_number text,
-  session_string text,
-  is_active boolean default true,
-  targets text default '[]',
-  created_at timestamp with time zone default timezone('utc'::text, now())
-);
-
--- TARGET GRUP
-create table public.blast_targets (
-  id bigint generated by default as identity primary key,
-  user_id bigint references public.users(id) on delete cascade not null,
-  group_id bigint not null,
-  group_name text,
-  topic_ids text,
-  is_active boolean default true,
-  created_at timestamp with time zone default timezone('utc'::text, now())
-);
-
--- JADWAL
-create table public.blast_schedules (
-  id bigint generated by default as identity primary key,
-  user_id bigint references public.users(id) on delete cascade not null,
-  run_hour int not null,
-  run_minute int not null,
-  is_active boolean default true,
-  created_at timestamp with time zone default timezone('utc'::text, now())
-);
-
--- LOG
-create table public.blast_logs (
-  id bigint generated by default as identity primary key,
-  user_id bigint references public.users(id) on delete cascade not null,
-  group_name text,
-  group_id bigint,
-  topic_id int,
-  status text,
-  error_message text,
-  created_at timestamp with time zone default timezone('utc'::text, now())
-);
-
--- CRM
-create table public.tele_users (
-  id bigint generated by default as identity primary key,
-  owner_id bigint references public.users(id) on delete cascade not null,
-  user_id bigint not null,
-  username text,
-  first_name text,
-  last_interaction timestamp with time zone,
-  created_at timestamp with time zone default timezone('utc'::text, now()),
-  unique(owner_id, user_id)
-);
-
-2️⃣ Deploy ke Render.com
-
-Upload project ke GitHub (Public / Private)
-
-Buat Web Service baru di Render
-
-Konfigurasi:
-
-Environment: Python 3
-
-Build Command: pip install -r requirements.txt
-
-Start Command: gunicorn app:app
-
-Environment Variables
-Key	Deskripsi
-SUPABASE_URL	URL Supabase
-SUPABASE_KEY	API Key
-API_ID	Telegram API ID
-API_HASH	Telegram API Hash
-SECRET_KEY	Flask Secret
-SUPER_ADMIN	Email admin
-PASS_ADMIN	Password admin
-PYTHON_VERSION	3.9.0
-3️⃣ Akses & Penggunaan
-
-Admin Panel: /super-admin
-
-Register User: /register
-
-Login Telegram via dashboard (OTP + 2FA jika ada)
-
-Scan grup → atur jadwal → mulai blast
-
-🔒 Keamanan & Disclaimer
-
-Session Telegram disimpan di database Anda sendiri
-
-Jangan bocorkan SUPABASE_KEY
-
-Blast berlebihan berisiko limit / banned Telegram
-
-Gunakan interval & konten yang relevan
-
-📜 Lisensi & Tanggung Jawab
-
-Proyek ini dibuat untuk edukasi & produktivitas bisnis.
-Developer tidak bertanggung jawab atas penyalahgunaan atau pelanggaran TOS Telegram.
-
-Developed with ❤️ and Coffee ☕
+## 13) Lisensi
+
+Lihat file [LICENSE](LICENSE) untuk detail lisensi penggunaan.
+
+---
+
+## 14) Ringkasan Super Singkat (untuk non-teknis)
+
+BlastPro adalah “**dashboard kirim campaign Telegram**” yang membantu bisnis:
+- mengelola target,
+- mengirim pesan massal,
+- mengatur jadwal,
+- menyimpan data prospek,
+- dan memantau hasil secara rapi.
+
+Developer mendapat fondasi Flask + Telethon + Supabase yang sudah mencakup auth, admin, CRM, billing, dan integrasi bot notifikasi.
 
EOF
)
