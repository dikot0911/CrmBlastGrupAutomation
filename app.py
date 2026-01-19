import os
import asyncio
import logging
import threading
import json
import time
import httpx
import pytz
import telethon
import csv
import io
import random
from functools import wraps 
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash, send_from_directory, Response, stream_with_context

# --- TELETHON & SUPABASE ---
from telethon import TelegramClient, errors, functions, types, utils
from telethon.sessions import StringSession
from supabase import create_client, Client

# --- CUSTOM BLUEPRINTS (EXISTING) ---
# Kami mempertahankan blueprint lama agar tidak merusak struktur folder yang sudah ada
try:
    from demo_routes import demo_bp
except ImportError:
    # Fallback aman jika file demo_routes.py belum ada di environment baru
    demo_bp = None

# ==============================================================================
# SECTION 1: SYSTEM CONFIGURATION & ENVIRONMENT SETUP
# ==============================================================================

# Initialize Flask Application
app = Flask(__name__)

# Security Configuration
# Gunakan Secret Key yang sangat kuat untuk production environment SaaS
app.secret_key = os.getenv('SECRET_KEY', 'rahasia_negara_baba_parfume_saas_ultimate_key_v99_production_ready')

# Registrasi Blueprint Lama (Jika Ada)
if demo_bp:
    app.register_blueprint(demo_bp)  

# Session Configuration (Agar login user awet dan aman)
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)
app.config['SESSION_COOKIE_SECURE'] = True  # Hanya kirim cookie via HTTPS (Wajib di Production)
app.config['SESSION_COOKIE_HTTPONLY'] = True # Mencegah akses JavaScript ke cookie (Anti-XSS)
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax' # Mencegah CSRF

# Upload Configuration (Untuk fitur Broadcast Gambar)
UPLOAD_FOLDER = 'static/uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Pastikan folder upload tersedia saat startup
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Advanced Logging System (Enterprise Grade)
# Mencatat setiap detil aktivitas sistem dengan timestamp presisi
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] [%(name)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("BabaSaaSCore")

# ==============================================================================
# SECTION 2: DATABASE CONNECTION (SUPABASE)
# ==============================================================================

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    logger.critical("❌ CRITICAL ERROR: Environment Variables Missing (SUPABASE_URL / KEY).")
    logger.critical("   System cannot start properly without database connection.")
    supabase = None
else:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        logger.info("✅ Supabase API Connected Successfully. Database System Online.")
    except Exception as e:
        logger.critical(f"❌ Supabase Connection Failed: {e}")
        supabase = None

# ==============================================================================
# SECTION 3: GLOBAL VARIABLES & STATE MANAGEMENT
# ==============================================================================

# Telegram API Credentials (Master App)
API_ID = int(os.getenv('API_ID', '0')) 
API_HASH = os.getenv('API_HASH', '')

# In-Memory State Storage
# Digunakan untuk rate limiting dan caching sementara objek client saat login.
login_states = {} 

# ==============================================================================
# SECTION 4: BACKGROUND SYSTEMS (WORKERS & UTILITIES)
# ==============================================================================

def start_self_ping():
    """
    Background Worker: Anti-Sleep Mechanism.
    Ping endpoint /ping setiap 14 menit untuk mencegah Render Free Tier tertidur (Spin Down).
    """
    site_url = os.getenv('SITE_URL') or os.getenv('RENDER_EXTERNAL_URL')
    
    if not site_url:
        logger.warning("⚠️ SITE_URL belum diset. Fitur Self-Ping mungkin tidak efektif.")
        return

    # Normalisasi URL (Pastikan ada protocol)
    if not site_url.startswith('http'):
        site_url = f'https://{site_url}'
        
    ping_endpoint = f"{site_url}/ping"
    logger.info(f"🚀 Anti-Sleep Worker Started! Target: {ping_endpoint}")

    def _worker():
        while True:
            try:
                # Tidur 14 menit (840 detik) - Margin aman sebelum timeout 15 menit
                time.sleep(840)
                
                # Kirim Heartbeat
                with httpx.Client(timeout=10) as client:
                    resp = client.get(ping_endpoint)
                    if resp.status_code == 200:
                        logger.info(f"💓 [Heartbeat] Server is Alive | Time: {datetime.utcnow()}")
                    else:
                        logger.warning(f"⚠️ [Heartbeat] Ping returned status: {resp.status_code}")
                        
            except Exception as e:
                logger.error(f"❌ [Heartbeat] Ping Failed: {e}")
                time.sleep(60)

    # Jalankan sebagai Daemon Thread
    threading.Thread(target=_worker, daemon=True, name="PingWorker").start()

def run_async(coroutine):
    """
    Bridge Helper: Menjalankan Asyncio Coroutine di dalam Flask (Synchronous).
    Membuat Event Loop terisolasi untuk setiap eksekusi.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coroutine)
    except Exception as e:
        logger.error(f"Async Bridge Error: {e}")
        raise e
    finally:
        try:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            loop.close()
        except:
            pass

def allowed_file(filename):
    """Cek ekstensi file yang diizinkan untuk upload gambar"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# ==============================================================================
# SECTION 4.5: MESSAGE TEMPLATE MANAGER (NEW FEATURE)
# ==============================================================================
# Class ini ditambahkan untuk mengelola logika Template Pesan tanpa mengganggu
# kode existing. Ini adalah "Add-on" logic.

class MessageTemplateManager:
    """
    Manajer untuk menangani CRUD Template Pesan.
    Sistem ini memungkinkan user menyimpan format pesan yang sering digunakan.
    """
    
    @staticmethod
    def get_templates(user_id):
        """Mengambil semua template milik user tertentu."""
        if not supabase: return []
        try:
            # Mengambil dari tabel 'message_templates'
            # Pastikan table ini dibuat di Supabase (lihat instruksi SQL di dokumentasi)
            res = supabase.table('message_templates').select("*").eq('user_id', user_id).order('created_at', desc=True).execute()
            return res.data if res.data else []
        except Exception as e:
            logger.error(f"Template Fetch Error: {e}")
            return []

    @staticmethod
    def get_template_by_id(template_id):
        """Mengambil satu template spesifik berdasarkan ID."""
        if not supabase or not template_id: return None
        try:
            res = supabase.table('message_templates').select("*").eq('id', template_id).execute()
            return res.data[0] if res.data else None
        except Exception as e:
            logger.error(f"Single Template Fetch Error: {e}")
            return None
            
    @staticmethod
    def create_template(user_id, name, content, source_chat_id=None, source_message_id=None):
        if not supabase: return False
        try:
            data = {
                'user_id': user_id, 
                'name': name, 
                'message_text': content, 
                'source_chat_id': source_chat_id,     # <-- Simpan ID Chat Sumber
                'source_message_id': source_message_id, # <-- Simpan ID Pesan Sumber
                'created_at': datetime.utcnow().isoformat()
            }
            supabase.table('message_templates').insert(data).execute()
            return True
        except Exception as e:
            logger.error(f"Template Create Error: {e}")
            return False

    @staticmethod
    def delete_template(user_id, template_id):
        """
        Menghapus template dengan Validasi Ketat, Cek Jadwal & Anti-Crash.
        Returns: (Success: bool, Message: str)
        """
        # 1. Cek Koneksi Database
        if not supabase: 
            return False, "❌ Database Terputus (Disconnected)"
        
        try:
            # 2. Validasi Tipe Data (Biar gak error konversi)
            try:
                t_id = int(template_id)
            except ValueError:
                return False, "❌ ID Template tidak valid."

            # 3. CEK PENGGUNAAN (Safety Check Level 1)
            # Cek apakah template lagi dipake di jadwal aktif?
            try:
                usage_check = supabase.table('blast_schedules')\
                    .select("run_hour, run_minute")\
                    .eq('template_id', t_id)\
                    .eq('is_active', True)\
                    .execute()
                
                # Kalau ketemu jadwal yang pake template ini -> TOLAK HAPUS
                if usage_check.data and len(usage_check.data) > 0:
                    times = [f"{s['run_hour']:02d}:{s['run_minute']:02d}" for s in usage_check.data]
                    time_str = ", ".join(times)
                    return False, f"⚠️ Gagal Hapus! Template ini sedang AKTIF digunakan pada Jadwal Pukul: {time_str} WIB. Harap hapus atau ganti jadwalnya terlebih dahulu."
            except Exception as e:
                # Kalau gagal cek jadwal (misal tabel belum sync), log aja dan lanjut ke step delete (biar database yang nahan)
                logger.warning(f"Usage Check Warning: {e}")

            # 4. EKSEKUSI HAPUS (Safety Check Level 2)
            # Hapus hanya jika ID cocok DAN User ID cocok (Security Isolation)
            res = supabase.table('message_templates').delete()\
                .eq('id', t_id)\
                .eq('user_id', user_id)\
                .execute()
            
            # 5. VERIFIKASI HASIL
            if res.data and len(res.data) > 0:
                return True, "✅ Template berhasil dihapus permanen."
            else:
                return False, "❌ Template tidak ditemukan atau sudah dihapus."
                
        except Exception as e:
            err_msg = str(e).lower()
            logger.error(f"Template Delete Error: {e}")
            
            # Tangkap Error Constraint (Foreign Key) dari Database
            if "foreign key" in err_msg or "constraint" in err_msg:
                return False, "🔒 Tidak bisa dihapus: Template ini terkunci karena masih terhubung dengan riwayat broadcast atau jadwal."
            
            # Tangkap Error Lainnya
            return False, f"⚠️ System Error: {str(e)}"
# ==============================================================================
# SECTION 4.6: SCHEDULER & AUTO-BLAST WORKER (NEW FEATURE)
# ==============================================================================
# Worker ini akan berjalan di background untuk mengecek jadwal setiap menit.
# Ini melengkapi fitur "Jadwal" yang sebelumnya hanya menyimpan data.

class SchedulerWorker:
    """
    Worker cerdas dengan TIMEZONE WIB (Asia/Jakarta).
    Anti-Drama UTC. Input jam 7, jalan jam 7 WIB.
    """
    
    @staticmethod
    def start():
        threading.Thread(target=SchedulerWorker._loop, daemon=True, name="SchedulerEngine").start()
        logger.info("🕒 Scheduler Engine Started (Timezone: Asia/Jakarta)")

    @staticmethod
    def _loop():
        """Main Loop: Cek setiap detik ke-00"""
        while True:
            try:
                # 1. Ambil Waktu Sekarang tapi PAKSA ke WIB
                tz_indo = pytz.timezone('Asia/Jakarta')
                now_indo = datetime.now(tz_indo)

                # 2. Cek Jadwal
                if supabase:
                    SchedulerWorker._process_schedules(now_indo)
                
                # 3. Logic Sleep Pintar (Biar pas di detik 00 menit berikutnya)
                # Biar CPU gak panas ngecek mulu, tapi akurat
                sleep_time = 60 - datetime.now().second
                time.sleep(sleep_time)
                
            except Exception as e:
                logger.error(f"Scheduler Loop Error: {e}")
                time.sleep(60)

    @staticmethod
    def _process_schedules(current_time_indo):
        """Logika inti pengecekan jadwal"""
        try:
            # Ambil Jam & Menit versi INDONESIA
            current_hour = current_time_indo.hour
            current_minute = current_time_indo.minute
            
            # Debug log (biar lu tau server lagi mikir jam berapa)
            # logger.info(f"Checking Schedule for: {current_hour}:{current_minute} WIB")

            # Query ke Database (Cari yang jam & menitnya SAMA PERSIS)
            res = supabase.table('blast_schedules').select("*")\
                .eq('is_active', True)\
                .eq('run_hour', current_hour)\
                .eq('run_minute', current_minute)\
                .execute()
                
            schedules = res.data
            
            if not schedules:
                return # Gak ada jadwal di menit ini
                
            logger.info(f"🚀 EXECUTE: Ditemukan {len(schedules)} jadwal untuk jam {current_hour}:{current_minute} WIB")
            
            for task in schedules:
                threading.Thread(target=SchedulerWorker._execute_task, args=(task,)).start()
                
        except Exception as e:
            logger.error(f"Scheduler Process Error: {e}")

    @staticmethod
    def _execute_task(task):
        """
        Eksekusi satu task jadwal dengan dukungan Multi-Account & Forum Topic.
        Logika: Cek dulu sender_phone, kalau ada pakai itu. Kalau mati/gak ada, pakai akun default.
        """
        user_id = task['user_id']
        template_id = task.get('template_id') 
        target_group_id = task.get('target_group_id') 
        sender_phone = task.get('sender_phone') # [NEW] Ambil preferensi akun pengirim
        
        # 1. Siapkan Pesan & Media
        message_content = "Halo! Ini pesan terjadwal otomatis."
        source_media = None
        
        if template_id:
            tmpl = MessageTemplateManager.get_template_by_id(template_id)
            if tmpl:
                message_content = tmpl['message_text']
                # Cek Reference Media (Untuk forward media dari cloud)
                if tmpl.get('source_chat_id') and tmpl.get('source_message_id'):
                    source_media = {'chat': int(tmpl['source_chat_id']), 'id': int(tmpl['source_message_id'])}

        # 2. Worker Async (Core Logic)
        async def _async_send():
            client = None
            
            # [LOGIC BARU: PILIH AKUN PENGIRIM]
            # Jika user memilih nomor spesifik di jadwal, kita coba connect pakai nomor itu
            if sender_phone and sender_phone != 'auto':
                try:
                    # Ambil session string khusus akun tersebut dari database
                    res = supabase.table('telegram_accounts').select("session_string")\
                        .eq('user_id', user_id)\
                        .eq('phone_number', sender_phone)\
                        .eq('is_active', True)\
                        .execute()
                    
                    if res.data:
                        # Connect manual pakai session string akun tsb
                        session_str = res.data[0]['session_string']
                        client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
                        await client.connect()
                except Exception as e:
                    print(f"⚠️ Gagal switch ke akun {sender_phone}: {e}")

            # Fallback: Kalau akun spesifik mati atau user pilih 'Auto', pakai akun default (yg pertama aktif)
            if not client or not await client.is_user_authorized():
                client = await get_active_client(user_id)
            
            # Kalau semua akun mati, nyerah.
            if not client: return 
            
            try:
                # Load Media dari Cloud Telegram (jika ada di template)
                media_obj = None
                if source_media:
                    try:
                        src_msg = await client.get_messages(source_media['chat'], ids=source_media['id'])
                        if src_msg and src_msg.media: media_obj = src_msg.media
                    except: pass

                # Ambil Target Audience
                targets_query = supabase.table('blast_targets').select("*").eq('user_id', user_id)
                # Filter jika user memilih grup target spesifik
                if target_group_id: targets_query = targets_query.eq('id', target_group_id)
                targets = targets_query.execute().data
                
                # Loop kirim ke setiap target
                for tg in targets:
                    try:
                        entity = await client.get_entity(int(tg['group_id']))
                        
                        # Handling Topik Forum
                        topic_ids = []
                        if tg.get('topic_ids'):
                            try: topic_ids = [int(x.strip()) for x in str(tg['topic_ids']).split(',') if x.strip().isdigit()]
                            except: pass
                        
                        # Jika ada topik, kirim ke masing-masing topik. Jika tidak, kirim ke General (None)
                        destinations = topic_ids if topic_ids else [None]
                        
                        for top_id in destinations:
                            final_msg = message_content.replace("{name}", tg.get('group_name') or "Kak")
                            
                            # Eksekusi Kirim
                            if media_obj:
                                await client.send_file(entity, media_obj, caption=final_msg, reply_to=top_id)
                            else:
                                await client.send_message(entity, final_msg, reply_to=top_id)
                            
                            # Catat Log Sukses
                            # Info sender dicatat biar user tau "Oh ini dikirim sama akun B"
                            sender_info = f"via {sender_phone}" if sender_phone else "via Default"
                            topik_info = f" (Topic: {top_id})" if top_id else ""
                            
                            supabase.table('blast_logs').insert({
                                "user_id": user_id, 
                                "group_name": f"{tg['group_name']}{topik_info} ({sender_info})",
                                "group_id": tg['group_id'], 
                                "status": "SUCCESS", 
                                "created_at": datetime.utcnow().isoformat()
                            }).execute()
                            
                            # Jeda random biar aman (Anti-Flood)
                            await asyncio.sleep(random.randint(5, 10)) 

                    except Exception as e:
                        # Log Gagal
                        supabase.table('blast_logs').insert({
                            "user_id": user_id, 
                            "group_name": tg.get('group_name', '?'),
                            "status": "FAILED", 
                            "error_message": str(e), 
                            "created_at": datetime.utcnow().isoformat()
                        }).execute()
            finally: 
                await client.disconnect()
        
        # Jalankan worker async di event loop baru
        run_async(_async_send())


# Jalankan Scheduler saat app start
if supabase:
    SchedulerWorker.start()

# ==============================================================================
# SECTION 5: DATA ACCESS LAYER (DAL)
# ==============================================================================

def get_user_data(user_id):
    """
    Mengambil data User lengkap dengan status Telegram Account.
    Menggunakan Wrapper Class agar kompatibel dengan template Jinja2 (dot notation).
    """
    if not supabase: return None
    try:
        # 1. Fetch User Data
        u_res = supabase.table('users').select("*").eq('id', user_id).execute()
        if not u_res.data: return None
        user_raw = u_res.data[0]
        
        # 2. Fetch Telegram Account Data
        t_res = supabase.table('telegram_accounts').select("*").eq('user_id', user_id).execute()
        tele_raw = t_res.data[0] if t_res.data else None
        
        # 3. Create Wrapper Object
        class UserEntity:
            def __init__(self, u_data, t_data):
                self.id = u_data['id']
                self.email = u_data['email']
                self.is_admin = u_data.get('is_admin', False)
                self.is_banned = u_data.get('is_banned', False)
                self.created_at = u_data.get('created_at')
                
                # Nested Object for Telegram Info
                self.telegram_account = None
                if t_data:
                    self.telegram_account = type('TeleInfo', (object,), {
                        'phone_number': t_data.get('phone_number'),
                        'is_active': t_data.get('is_active', False),
                        'session_string': t_data.get('session_string'),
                        'created_at': t_data.get('created_at')
                    })
        
        return UserEntity(user_raw, tele_raw)
    except Exception as e:
        logger.error(f"DAL Error (get_user_data): {e}")
        return None

async def get_active_client(user_id):
    """
    Membangun koneksi Telethon Client aktif dari Database.
    Memeriksa validitas sesi secara otomatis sebelum mengembalikan objek client.
    """
    if not supabase: return None
    try:
        # Hanya ambil akun yang ditandai ACTIVE di database
        res = supabase.table('telegram_accounts').select("session_string").eq('user_id', user_id).eq('is_active', True).execute()
        
        if not res.data:
            logger.warning(f"Client Init: No active session for UserID {user_id}")
            return None
        
        session_str = res.data[0]['session_string']
        
        # Initialize Client dengan Session String dari DB
        client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
        await client.connect()
        
        # Security Check: Apakah sesi masih valid di server Telegram?
        # Jika user logout dari HP, session string ini akan invalid.
        if not await client.is_user_authorized():
            logger.warning(f"Client Init: Session EXPIRED/REVOKED for UserID {user_id}")
            await client.disconnect()
            
            # Auto-update status di DB jadi Inactive agar UI dashboard update
            supabase.table('telegram_accounts').update({'is_active': False}).eq('user_id', user_id).execute()
            return None
            
        return client
    except Exception as e:
        logger.error(f"Client Init Error for UserID {user_id}: {e}")
        return None

# ==============================================================================
# SECTION 6: MIDDLEWARE & DECORATORS
# ==============================================================================

@app.errorhandler(404)
def handle_404(e):
    """Redirect cerdas jika user nyasar ke link mati"""
    if 'user_id' in session:
        return redirect(url_for('dashboard_overview'))
    return redirect(url_for('index'))

def login_required(f):
    """Decorator untuk memproteksi halaman yang butuh login"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        # Extend session lifetime setiap user aktif
        session.permanent = True
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    """Decorator khusus halaman Super Admin"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session: 
            return redirect(url_for('login'))
        
        # Cek hak akses admin dari database
        user = get_user_data(session['user_id'])
        if not user or not user.is_admin:
            flash('⛔ Security Alert: Akses Ditolak. Area ini dipantau.', 'danger')
            return redirect(url_for('dashboard_overview'))
            
        return f(*args, **kwargs)
    return decorated_function

# ==============================================================================
# SECTION 7: PUBLIC ROUTES (LANDING & AUTH)
# ==============================================================================

@app.route('/')
def index():
    return render_template('landing/index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if not supabase:
            flash('System Error: Database Disconnected.', 'danger')
            return render_template('auth/login.html')
        
        email = request.form.get('email')
        password = request.form.get('password')
        
        try:
            res = supabase.table('users').select("*").eq('email', email).execute()
            if res.data:
                user = res.data[0]
                # Verifikasi Password Hash
                if check_password_hash(user['password'], password):
                    # Cek Banned Status
                    if user.get('is_banned'):
                        flash('⛔ Akun Anda telah disuspend karena pelanggaran.', 'danger')
                        return redirect(url_for('login'))
                    
                    # Login Sukses
                    session['user_id'] = user['id']
                    session.permanent = True
                    
                    # Routing berdasarkan Role
                    if user.get('is_admin'):
                        return redirect(url_for('super_admin_dashboard'))
                    return redirect(url_for('dashboard_overview'))
            
            flash('Kombinasi Email atau Password salah.', 'danger')
            
        except Exception as e:
            logger.error(f"Login Exception: {e}")
            flash('Terjadi kesalahan sistem internal.', 'danger')
            
    return render_template('auth/login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        try:
            # Validasi Duplikat Email
            exist = supabase.table('users').select("id").eq('email', email).execute()
            if exist.data:
                flash('Email ini sudah terdaftar. Silakan login.', 'warning')
                return redirect(url_for('register'))
            
            # Create New User
            hashed_pw = generate_password_hash(password)
            new_user = {
                'email': email,
                'password': hashed_pw,
                'created_at': datetime.utcnow().isoformat(),
                'is_admin': False,
                'is_banned': False
            }
            supabase.table('users').insert(new_user).execute()
            
            flash('Pendaftaran Berhasil! Silakan masuk ke akun Anda.', 'success')
            return redirect(url_for('login'))
            
        except Exception as e:
            logger.error(f"Register Exception: {e}")
            flash('Gagal mendaftar. Silakan coba lagi.', 'danger')
            
    return render_template('auth/register.html')

@app.route('/logout')
def logout():
    uid = session.get('user_id')
    # Cleanup memory cache jika ada
    if uid and uid in login_states:
        try: del login_states[uid]
        except: pass
        
    session.pop('user_id', None)
    return redirect(url_for('index'))

# ==============================================================================
# SECTION 8: USER DASHBOARD CONTROLLERS (MODULAR ROUTING)
# ==============================================================================

# Helper untuk memvalidasi user sebelum render dashboard
def get_dashboard_context():
    user = get_user_data(session['user_id'])
    if not user:
        session.pop('user_id', None)
        return None
    if user.is_banned:
        session.pop('user_id', None)
        flash('⛔ Akun Anda dibekukan oleh Administrator.', 'danger')
        return None
    return user

@app.route('/dashboard')
@login_required
def dashboard_overview():
    """Halaman Utama Dashboard: Ringkasan Statistik dengan Pagination"""
    user = get_dashboard_context()
    if not user: return redirect(url_for('login'))
    
    uid = user.id
    
    # --- LOGIC PAGINATION ---
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    
    # Hitung offset untuk database
    start = (page - 1) * per_page
    end = start + per_page - 1
    
    logs = []
    total_logs = 0
    total_pages = 0
    
    schedules = []
    targets = []
    crm_count = 0
    
    if supabase:
        try:
            # 1. Ambil Total Count Logs (Untuk hitung halaman)
            count_res = supabase.table('blast_logs').select("id", count='exact', head=True).eq('user_id', uid).execute()
            total_logs = count_res.count if count_res.count else 0
            
            # Hitung total halaman
            import math
            total_pages = math.ceil(total_logs / per_page)

            # 2. Ambil Data Logs Sesuai Halaman (Range)
            logs = supabase.table('blast_logs').select("*").eq('user_id', uid)\
                .order('created_at', desc=True)\
                .range(start, end)\
                .execute().data
            
            # 3. Data Lainnya
            schedules = supabase.table('blast_schedules').select("*").eq('user_id', uid).execute().data
            targets = supabase.table('blast_targets').select("*").eq('user_id', uid).execute().data
            crm_res = supabase.table('tele_users').select("id", count='exact', head=True).eq('owner_id', uid).execute()
            crm_count = crm_res.count if crm_res.count else 0
            
        except Exception as e:
            logger.error(f"Dashboard Data Error: {e}")
    
    return render_template('dashboard/index.html', 
                           user=user, 
                           logs=logs, 
                           schedules=schedules, 
                           targets=targets, 
                           user_count=crm_count,
                           # Kirim variabel pagination ke HTML
                           current_page=page,
                           total_pages=total_pages,
                           per_page=per_page,
                           total_logs=total_logs,
                           active_page='dashboard')

@app.route('/dashboard/broadcast')
@login_required
def dashboard_broadcast():
    """Halaman Fitur Broadcast & Preview Pesan"""
    user = get_dashboard_context()
    if not user: return redirect(url_for('login'))
    
    crm_count = 0
    templates = []
    selected_ids = ""
    count_selected = 0
    
    try:
        crm_res = supabase.table('tele_users').select("id", count='exact', head=True).eq('owner_id', user.id).execute()
        crm_count = crm_res.count if crm_res.count else 0
        
        # Load Templates
        templates = MessageTemplateManager.get_templates(user.id)
        
        # Tangkap ID dari URL (hasil lemparan dari CRM)
        ids_arg = request.args.get('ids')
        if ids_arg:
            selected_ids = ids_arg
            count_selected = len(ids_arg.split(','))
        # ------------------------------------
    except: pass

    return render_template('dashboard/broadcast.html', 
                           user=user, 
                           user_count=crm_count, 
                           templates=templates, 
                           selected_ids=selected_ids,     # Kirim ke HTML
                           count_selected=count_selected, # Kirim ke HTML
                           active_page='broadcast')

@app.route('/dashboard/targets')
@login_required
def dashboard_targets():
    """Halaman Manajemen Target Grup"""
    user = get_dashboard_context()
    if not user: return redirect(url_for('login'))
    
    targets = []
    try:
        targets = supabase.table('blast_targets').select("*").eq('user_id', user.id).order('created_at', desc=True).execute().data
    except: pass
    
    return render_template('dashboard/targets.html', user=user, targets=targets, active_page='targets')

@app.route('/dashboard/schedule')
@login_required
def dashboard_schedule():
    """Halaman Manajemen Jadwal Blast (Multi-Account Support)"""
    user = get_dashboard_context()
    if not user: return redirect(url_for('login'))
    
    schedules = []
    templates = [] 
    targets = []   
    accounts = []  # List akun aktif
    
    try:
        # Ambil jadwal lama
        schedules = supabase.table('blast_schedules').select("*").eq('user_id', user.id).order('run_hour', desc=False).execute().data
        
        # Fetch Data Pendukung
        templates = MessageTemplateManager.get_templates(user.id)
        targets = supabase.table('blast_targets').select("*").eq('user_id', user.id).execute().data
        
        # [UPGRADE] Ambil akun yang AKTIF saja buat dropdown
        acc_res = supabase.table('telegram_accounts').select("phone_number").eq('user_id', user.id).eq('is_active', True).execute()
        accounts = acc_res.data if acc_res.data else []
        
        # Enrich schedule data with template names
        for s in schedules:
            t_id = s.get('template_id')
            s['template_name'] = 'Custom / No Template'
            if t_id:
                found = next((t for t in templates if t['id'] == t_id), None)
                if found: s['template_name'] = found['name']
        
    except Exception as e:
        logger.error(f"Schedule Page Error: {e}")
    
    return render_template('dashboard/schedule.html', 
                           user=user, 
                           schedules=schedules, 
                           templates=templates, 
                           targets=targets,     
                           accounts=accounts,   # Kirim ke HTML
                           active_page='schedule')

@app.route('/dashboard/templates')
@login_required
def dashboard_templates():
    """
    [FITUR BARU] Halaman Manajemen Template Pesan.
    User bisa CRUD template di sini.
    """
    user = get_dashboard_context()
    if not user: return redirect(url_for('login'))
    
    templates = MessageTemplateManager.get_templates(user.id)
    
    return render_template('dashboard/templates.html', # Pastikan buat file HTML ini nanti
                           user=user, 
                           templates=templates, 
                           active_page='templates')

@app.route('/dashboard/crm')
@login_required
def dashboard_crm():
    """Halaman Database Pelanggan (CRM) dengan Server-Side Pagination"""
    user = get_dashboard_context()
    if not user: return redirect(url_for('login'))
    
    # --- LOGIC PAGINATION & SEARCH (Update Ini!) ---
    # 1. Ambil parameter dari URL (default page 1, 50 baris per halaman)
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)
    search_query = request.args.get('q', '').strip()
    
    # 2. Hitung Offset Database (Start - End)
    start = (page - 1) * per_page
    end = start + per_page - 1
    
    crm_users = []
    total_users = 0
    total_pages = 0
    
    if supabase:
        try:
            # Base Query
            query = supabase.table('tele_users').select("*", count='exact').eq('owner_id', user.id)
            
            # Filter Pencarian (Jika ada)
            if search_query:
                # Cari berdasarkan username (Case Insensitive)
                query = query.ilike('username', f'%{search_query}%') 
            
            # Eksekusi Query dengan Range (Halaman)
            res = query.order('last_interaction', desc=True).range(start, end).execute()
            
            crm_users = res.data if res.data else []
            total_users = res.count if res.count else 0
            
            # Hitung Total Halaman (Matematika)
            import math
            total_pages = math.ceil(total_users / per_page) if per_page > 0 else 0
            
        except Exception as e:
            logger.error(f"CRM Pagination Error: {e}")
    
    # 3. Kirim SEMUA variabel ini ke HTML (PENTING!)
    return render_template('dashboard/crm.html', 
                           user=user, 
                           crm_users=crm_users, 
                           user_count=total_users, # Update variable count
                           # Variabel Pagination Wajib:
                           current_page=page,
                           total_pages=total_pages,
                           per_page=per_page,
                           total_users=total_users,
                           search_query=search_query,
                           active_page='crm')

@app.route('/dashboard/connection')
@login_required
def dashboard_connection():
    user = get_user_data(session['user_id']) # User data basic
    if not user: return redirect(url_for('login'))
    
    # [UPGRADE] Ambil SEMUA akun telegram milik user ini
    accounts = []
    try:
        res = supabase.table('telegram_accounts').select("*").eq('user_id', user.id).order('created_at', desc=True).execute()
        accounts = res.data if res.data else []
    except Exception as e:
        logger.error(f"Fetch Accounts Error: {e}")

    return render_template('dashboard/connection.html', 
                           user=user, 
                           accounts=accounts, # Kirim list akun ke HTML
                           active_page='connection')

@app.route('/api/connect/disconnect', methods=['POST'])
@login_required
def disconnect_account():
    """Putuskan sambungan salah satu akun spesifik"""
    phone = request.json.get('phone')
    user_id = session['user_id']
    
    try:
        # Hapus baris berdasarkan user_id DAN nomor hp
        supabase.table('telegram_accounts').delete().eq('user_id', user_id).eq('phone_number', phone).execute()
        
        # Hapus session file/cache memory jika ada
        # (Opsional: tambahkan logic cleanup telethon session string)
        
        return jsonify({'status': 'success', 'message': f'Akun {phone} berhasil dihapus.'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})

@app.route('/dashboard/profile')
@login_required
def dashboard_profile():
    """Halaman Profile User Lengkap"""
    user = get_user_data(session['user_id'])
    if not user: return redirect(url_for('login'))
    
    # Render template profil yang baru dibuat
    return render_template('dashboard/profile.html', user=user, active_page='profile')

# ==============================================================================
# SECTION 9: TELEGRAM AUTHENTICATION (CORE LOGIC & STATELESS)
# ==============================================================================

@app.route('/api/connect/send_code', methods=['POST'])
@login_required
def send_code():
    phone = request.json.get('phone')
    user_id = session['user_id']
    
    if not phone: return jsonify({'status': 'error', 'message': 'Nomor HP wajib diisi.'})

    # [UPGRADE] CEK LIMIT AKUN (MAX 3)
    try:
        res = supabase.table('telegram_accounts').select("id", count='exact', head=True).eq('user_id', user_id).execute()
        current_count = res.count if res.count else 0
        
        # Cek apakah nomor ini sudah ada (Re-login) atau nomor baru (New Add)
        check_exist = supabase.table('telegram_accounts').select("id").eq('user_id', user_id).eq('phone_number', phone).execute()
        is_existing_number = True if check_exist.data else False
        
        # Logic Limit: Kalau nomor baru DAN jumlah udah 3 -> TOLAK
        if not is_existing_number and current_count >= 3:
            return jsonify({
                'status': 'limit_reached', 
                'message': 'Batas Maksimal 3 Akun Tercapai! Hubungi Admin untuk upgrade.'
            })
            
    except Exception as e:
        logger.error(f"Limit Check Error: {e}")

    # Rate Limiting (Sama kayak sebelumnya)
    current_time = time.time()
    if user_id in login_states:
        last_req = login_states[user_id].get('last_otp_req', 0)
        if current_time - last_req < 60:
            remaining = int(60 - (current_time - last_req))
            return jsonify({'status': 'cooldown', 'message': f'Tunggu {remaining} detik lagi.'})
    
    async def _process_send_code():
        client = TelegramClient(StringSession(), API_ID, API_HASH)
        await client.connect()
        try:
            if not await client.is_user_authorized():
                req = await client.send_code_request(phone)
                temp_session_str = client.session.save()
                
                # [UPGRADE] Simpan Data (Upsert berdasarkan User + Phone)
                # Kita pake trik: Coba delete dulu row "pending" lama kalau ada, lalu insert baru
                # Atau gunakan UPSERT dengan constraint (user_id, phone_number)
                
                data = {
                    'user_id': user_id,
                    'phone_number': phone,
                    'session_string': temp_session_str,
                    'targets': req.phone_code_hash, # Hash OTP sementara
                    'is_active': False, # Belum aktif sampai verifikasi
                    'created_at': datetime.utcnow().isoformat()
                }
                
                # Upsert ke Supabase
                supabase.table('telegram_accounts').upsert(data, on_conflict="user_id, phone_number").execute()
                
                login_states[user_id] = {'last_otp_req': current_time, 'pending_phone': phone} # Simpan phone yg lagi login di RAM
                return jsonify({'status': 'success', 'message': 'Kode OTP terkirim!'})
            else:
                return jsonify({'status': 'error', 'message': 'Nomor ini aneh (Authorized but not local).'})
        except Exception as e:
            return jsonify({'status': 'error', 'message': f'Telegram Error: {str(e)}'})
        finally: await client.disconnect()

    return run_async(_process_send_code())

@app.route('/api/connect/verify_code', methods=['POST'])
@login_required
def verify_code():
    user_id = session['user_id']
    otp = request.json.get('otp')
    pw = request.json.get('password')
    
    # 1. Retrieve Stored Session & Hash
    db_session = None
    db_hash = None
    db_phone = None
    
    try:
        # Ambil sesi pending (yang belum aktif atau yang lagi proses)
        # Prioritaskan cari berdasarkan phone number yang disimpan di RAM/Session jika ada, 
        # tapi karena stateless, kita cari row yang punya hash tapi belum aktif.
        res = supabase.table('telegram_accounts').select("*").eq('user_id', user_id).eq('is_active', False).neq('targets', '[]').limit(1).execute()
        
        if not res.data:
            return jsonify({'status': 'error', 'message': 'Sesi kadaluarsa. Kirim ulang OTP.'})
        
        row = res.data[0]
        db_session = row['session_string']
        db_phone = row['phone_number']
        db_hash = row['targets']
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Database Error: {str(e)}'})

    async def _process_verify():
        client = TelegramClient(StringSession(db_session), API_ID, API_HASH)
        await client.connect()
        
        try:
            # 2. Sign In
            try:
                await client.sign_in(db_phone, otp, phone_code_hash=db_hash)
            except errors.SessionPasswordNeededError:
                if not pw:
                    return jsonify({'status': '2fa', 'message': 'Akun dilindungi 2FA. Masukkan Password.'})
                await client.sign_in(password=pw)
            
            # 3. [BARU] AMBIL DATA PROFIL TELEGRAM
            me = await client.get_me()
            
            # 4. Simpan Session & Profil ke DB
            final_session = client.session.save()
            
            update_data = {
                'session_string': final_session,
                'is_active': True,
                'targets': '[]', # Clear hash
                'created_at': datetime.utcnow().isoformat(),
                # Simpan Info Profil
                'first_name': me.first_name or '',
                'last_name': me.last_name or '',
                'username': me.username or ''
            }
            
            supabase.table('telegram_accounts').update(update_data).eq('user_id', user_id).eq('phone_number', db_phone).execute()
            
            return jsonify({'status': 'success', 'message': f'Berhasil login sebagai {me.first_name}!'})
            
        except errors.PhoneCodeInvalidError:
            return jsonify({'status': 'error', 'message': 'Kode OTP salah.'})
        except Exception as e:
            logger.error(f"Login Failed: {e}")
            return jsonify({'status': 'error', 'message': f'Gagal: {str(e)}'})
        finally:
            await client.disconnect()

    try:
        return run_async(_process_verify())
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})

# ==============================================================================
# SECTION 10: BOT FEATURES API (SCAN, TARGETS, IMPORT)
# ==============================================================================

# Pastikan import functions ada di paling atas (baris 13-an). 
# Kalau belum ada, tambahkan: from telethon import functions

@app.route('/scan_groups_api')
@login_required
def scan_groups_api():
    """Scan Groups & Topics (FINAL HARD IMPORT VERSION)"""
    user_id = session.get('user_id')
    if user_id is None:
        return jsonify({"status": "error", "message": "User not authenticated."}), 401

    async def _scan():
        # 1. Cek Versi (Buat kepastian di log)
        import telethon
        from telethon import utils, types
        
        # --- [CRITICAL FIX] ---
        # Kita import PAKSA sub-modulnya agar Python sadar kalau ini ada.
        # Jangan cuma 'import telethon.tl.functions', tapi harus spesifik ke 'channels'
        try:
            from telethon.tl.functions.channels import GetForumTopicsRequest
            HAS_RAW_API = True
            logger.info(f"✅ [SYSTEM] Class GetForumTopicsRequest BERHASIL di-load dari Telethon {telethon.__version__}")
        except ImportError as e:
            HAS_RAW_API = False
            logger.critical(f"❌ [SYSTEM] FATAL: GetForumTopicsRequest hilang! Error: {e}")

        client = await get_active_client(user_id)
        if not client:
            return jsonify({"status": "error", "message": "Telegram disconnected."})

        groups = []

        try:
            async for dialog in client.iter_dialogs(limit=200):
                # Filter hanya grup
                if not dialog.is_group:
                    continue

                # Cek atribut forum
                is_forum = getattr(dialog.entity, 'forum', False)
                real_id = utils.get_peer_id(dialog.entity)

                g_data = {
                    'id': real_id,
                    'name': dialog.name,
                    'is_forum': is_forum,
                    'topics': []
                }

                if is_forum:
                    logger.info(f"🔍 [SCAN] Forum Detected: {dialog.name}")
                    
                    if HAS_RAW_API:
                        try:
                            input_channel = await client.get_input_entity(real_id)
                            all_topics = []
                            
                            # Pagination Variables
                            offset_id = 0
                            offset_date = 0
                            offset_topic = 0

                            for page in range(10):  # Limit 10 halaman
                                try:
                                    # [CRITICAL FIX] Panggil Class Langsung (Bukan via wrapper functions.channels)
                                    req = GetForumTopicsRequest(
                                        channel=input_channel,
                                        offset_date=offset_date,
                                        offset_id=offset_id,
                                        offset_topic=offset_topic,
                                        limit=100,
                                        q=''
                                    )
                                    res = await client(req)

                                    topics = getattr(res, 'topics', [])
                                    if not topics:
                                        break

                                    for t in topics:
                                        t_id = getattr(t, 'id', None)
                                        t_title = getattr(t, 'title', '')

                                        # Handle Deleted Topics
                                        if isinstance(t, types.ForumTopicDeleted):
                                            t_title = f"(Deleted Topic) #{t_id}"
                                        
                                        # Handle Blank Title
                                        if not t_title:
                                            t_title = f"Topic #{t_id}"

                                        # Normalisasi General
                                        if t_id == 1 or "Topic #1" in t_title:
                                            t_title = "General 📌"

                                        if t_id is not None:
                                            all_topics.append({"id": t_id, "title": t_title})

                                    # Update Offset untuk loop berikutnya
                                    last = topics[-1]
                                    offset_id = getattr(last, 'id', 0)
                                    offset_date = getattr(last, 'date', 0)

                                except Exception as loop_e:
                                    logger.warning(f"   ⚠️ Page {page} error: {loop_e}")
                                    break

                            # Sortir & Pastikan General Ada
                            all_topics.sort(key=lambda x: x['id'])
                            if not any(t['id'] == 1 for t in all_topics):
                                all_topics.insert(0, {'id': 1, 'title': 'General (Topik Utama)'})

                            g_data['topics'] = all_topics
                            logger.info(f"   ✅ Sukses: {len(all_topics)} topik diambil.")

                        except Exception as e:
                            logger.exception(f"   🔥 Gagal Request ke Telegram: {e}")
                            g_data['topics'] = [{'id': 1, 'title': 'General (API Error)'}]
                    else:
                        g_data['topics'] = [{'id': 1, 'title': 'General (Library Import Failed)'}]

                groups.append(g_data)

        except Exception as e:
            logger.exception(f"Global Scan Error: {e}")
            return jsonify({'status': 'error', 'message': str(e)})

        finally:
            try: await client.disconnect()
            except: pass

        return jsonify({'status': 'success', 'data': groups})

    return run_async(_scan())
    
@app.route('/save_bulk_targets', methods=['POST'])
@login_required
def save_bulk_targets():
    """Bulk Save Targets to DB"""
    user_id = session['user_id']
    data = request.json
    selected = data.get('targets', [])
    
    try:
        count = 0
        for item in selected:
            # Serialize topic IDs to string
            t_ids = ",".join(map(str, item.get('topic_ids', [])))
            
            payload = {
                "user_id": user_id,
                "group_name": item['group_name'],
                "group_id": int(item['group_id']),
                "topic_ids": t_ids,
                "is_active": True,
                "created_at": datetime.utcnow().isoformat()
            }
            
            # Upsert Logic
            ex = supabase.table('blast_targets').select('id').eq('user_id', user_id).eq('group_id', item['group_id']).execute()
            
            if ex.data:
                supabase.table('blast_targets').update(payload).eq('id', ex.data[0]['id']).execute()
            else:
                supabase.table('blast_targets').insert(payload).execute()
            count += 1
            
        return jsonify({"status": "success", "message": f"{count} target berhasil disimpan!"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/import_crm_api', methods=['POST'])
@login_required
def import_crm_api():
    """Scan Private Chats for CRM"""
    user_id = session['user_id']
    
    async def _import():
        client = await get_active_client(user_id)
        if not client: return jsonify({"status": "error", "message": "Telegram tidak terhubung."})
        
        count = 0
        try:
            async for dialog in client.iter_dialogs(limit=500):
                if dialog.is_user and not dialog.entity.bot:
                    u = dialog.entity
                    data = {
                        "owner_id": user_id,
                        "user_id": u.id,
                        "username": u.username,
                        "first_name": u.first_name,
                        "last_interaction": datetime.utcnow().isoformat(),
                        "created_at": datetime.utcnow().isoformat()
                    }
                    # Upsert Safe Logic
                    try:
                        supabase.table('tele_users').upsert(data, on_conflict="owner_id, user_id").execute()
                        count += 1
                    except: pass
                    
            await client.disconnect()
            return jsonify({"status": "success", "message": f"Berhasil mengimpor {count} kontak baru."})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)})
            
    return run_async(_import())
    
#telegram API Routes fot tamplates
def parse_telegram_link(link):
    """
    Parser Link Telegram Cerdas.
    Support:
    - Public: t.me/username/123
    - Private: t.me/c/1234567890/123
    """
    try:
        # Bersihin link dari https:// atau t.me/
        clean_link = link.replace("https://t.me/", "").replace("t.me/", "").strip()
        parts = clean_link.split('/')
        
        # Validasi dasar
        if len(parts) < 2: return None, None
        
        # Ambil Message ID (pasti yang terakhir)
        try:
            msg_id = int(parts[-1])
        except:
            return None, None # Kalau bukan angka berarti salah link

        # Cek tipe channel (Private 'c' atau Public 'username')
        if parts[0] == 'c':
            # Private Channel ID di link biasanya tanpa -100, jadi kita tambah manual
            # Contoh: t.me/c/1791234567/100 -> ID asli: -1001791234567
            chat_id_raw = parts[1]
            chat_id = int(f"-100{chat_id_raw}")
        else:
            # Public Channel (Username)
            chat_id = parts[0]
            
        return chat_id, msg_id
    except Exception as e:
        logger.error(f"Link Parse Error: {e}")
        return None, None

# --- UPDATE API FETCH ---
@app.route('/api/fetch_message', methods=['POST'])
@login_required
def fetch_telegram_message():
    user_id = session['user_id']
    link = request.json.get('link')
    if not link: return jsonify({'status': 'error', 'message': 'Link kosong.'})

    async def _fetch():
        client = await get_active_client(user_id)
        if not client: return jsonify({'status': 'error', 'message': 'Telegram disconnected.'})
        try:
            # Parsing Link
            entity, msg_id = parse_telegram_link(link)
            if not entity or not msg_id:
                return jsonify({'status': 'error', 'message': 'Link tidak valid.'})

            # Cek Pesan
            msg = await client.get_messages(entity, ids=msg_id)
            if not msg: return jsonify({'status': 'error', 'message': 'Pesan tidak ditemukan.'})

            # Kita balikin ID-nya ke Frontend biar disimpen pas user klik Save
            return jsonify({
                'status': 'success', 
                'text': msg.text or "", 
                'has_media': True if msg.media else False,
                'source_chat_id': str(utils.get_peer_id(msg.peer_id)), # ID Chat
                'source_message_id': msg.id # ID Pesan
            })
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)})
        finally: await client.disconnect()
        
    return run_async(_fetch())

# ==============================================================================
# SECTION 11: BROADCAST SYSTEM (REAL-TIME STREAMING & HUMAN MODE)
# ==============================================================================

@app.route('/start_broadcast', methods=['POST'])
@login_required
def start_broadcast():
    """
    Broadcast dengan Real-time Streaming Response + Human Behavior Logic.
    Fitur Anti-Ban: Random Delay & Batch Pausing.
    """
    user_id = session['user_id']
    message = request.form.get('message')
    template_id = request.form.get('template_id')
    selected_ids_str = request.form.get('selected_ids') 
    target_option = request.form.get('target_option')
    image_file = request.files.get('image')
    
    # 1. Logic Content (Template vs Manual)
    source_media = None
    if template_id:
        tmpl = MessageTemplateManager.get_template_by_id(template_id)
        if tmpl:
            if not message: message = tmpl['message_text']
            # Cek Media dari "Database Telegram"
            if tmpl.get('source_chat_id') and tmpl.get('source_message_id'):
                source_media = {'chat': int(tmpl['source_chat_id']), 'id': int(tmpl['source_message_id'])}

    if not message:
        return jsonify({"error": "Pesan konten wajib diisi."})

    # 2. Handle Upload Gambar Manual (Lokal)
    manual_image_path = None
    if image_file and allowed_file(image_file.filename):
        filename = secure_filename(f"broadcast_{user_id}_{int(time.time())}_{image_file.filename}")
        manual_image_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        image_file.save(manual_image_path)

    # 3. Tentukan Target Audience
    targets = []
    if selected_ids_str and target_option == 'selected':
        target_ids = [int(x) for x in selected_ids_str.split(',') if x.isdigit()]
        if target_ids:
            res = supabase.table('tele_users').select("*").in_('user_id', target_ids).eq('owner_id', user_id).execute()
            targets = res.data
    else:
        # Kirim ke SEMUA (Safety Limit 5000 dulu biar aman)
        res = supabase.table('tele_users').select("*").eq('owner_id', user_id).limit(5000).execute()
        targets = res.data

    if not targets:
        return jsonify({"error": "Tidak ada target penerima."})

    # 4. GENERATOR FUNCTION (Logika Inti)
    def generate():
        yield json.dumps({"type": "start", "total": len(targets)}) + "\n"
        
        async def _process():
            client = await get_active_client(user_id)
            if not client:
                yield json.dumps({"type": "error", "msg": "Telegram Disconnected"}) + "\n"
                return

            # Load Cloud Media (Sekali di awal biar hemat request)
            cloud_media_obj = None
            if source_media:
                try:
                    src_msg = await client.get_messages(source_media['chat'], ids=source_media['id'])
                    if src_msg and src_msg.media: cloud_media_obj = src_msg.media
                except: pass

            success_count = 0
            fail_count = 0

            # --- LOOP PENGIRIMAN ---
            for idx, user in enumerate(targets):
                
                # [SAFETY LOGIC 1]: Batch Pause (Istirahat Panjang)
                # Setiap kelipatan 25 pesan, istirahat 3-5 menit
                if idx > 0 and idx % 25 == 0:
                    long_pause = random.randint(180, 300) # 3 s.d 5 Menit
                    yield json.dumps({
                        "type": "progress",
                        "current": idx,
                        "total": len(targets),
                        "status": "warning", # Warna kuning di log
                        "log": f"☕ SAFETY BREAK: Istirahat {long_pause} detik (Anti-Ban)...",
                        "success": success_count,
                        "failed": fail_count
                    }) + "\n"
                    await asyncio.sleep(long_pause)

                # [PROSES KIRIM]
                try:
                    final_msg = message.replace("{name}", user.get('first_name') or "Kak")
                    entity = await client.get_input_entity(int(user['user_id']))
                    
                    if cloud_media_obj:
                        await client.send_file(entity, cloud_media_obj, caption=final_msg)
                    elif manual_image_path:
                        await client.send_file(entity, manual_image_path, caption=final_msg)
                    else:
                        await client.send_message(entity, final_msg)
                    
                    success_count += 1
                    status = "success"
                    log_msg = f"Terkirim ke {user.get('first_name') or user.get('user_id')}"

                except Exception as e:
                    fail_count += 1
                    status = "failed"
                    log_msg = f"Gagal ke {user.get('user_id')}: {str(e)[:15]}..."
                
                # Update Progress UI
                yield json.dumps({
                    "type": "progress",
                    "current": idx + 1,
                    "total": len(targets),
                    "status": status,
                    "log": log_msg,
                    "success": success_count,
                    "failed": fail_count
                }) + "\n"
                
                # [SAFETY LOGIC 2]: Random Delay Antar Pesan
                # Acak antara 1.5 sampai 3.0 detik (step 0.1)
                step_delay = random.randrange(15, 31) / 10.0
                await asyncio.sleep(step_delay)

            await client.disconnect()
            
            if manual_image_path and os.path.exists(manual_image_path):
                os.remove(manual_image_path)
                
            yield json.dumps({"type": "done", "success": success_count, "failed": fail_count}) + "\n"

        # Bridge Async
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            runner = _process()
            while True:
                try:
                    yield loop.run_until_complete(runner.__anext__())
                except StopAsyncIteration:
                    break
        finally:
            loop.close()

    return Response(stream_with_context(generate()), mimetype='application/json')

# ==============================================================================
# SECTION 12: CRUD ROUTES (SCHEDULE, TARGETS, & TEMPLATES)
# ==============================================================================

@app.route('/add_schedule', methods=['POST'])
@login_required
def add_schedule():
    """
    [UPGRADE] Menambah jadwal dengan dukungan Template & Target Group
    """
    h = request.form.get('hour')
    m = request.form.get('minute')
    
    # New Fields
    template_id = request.form.get('template_id')
    target_group_id = request.form.get('target_group_id')
    
    if h and m:
        try:
            payload = {
                "user_id": session['user_id'],
                "run_hour": int(h),
                "run_minute": int(m),
                "is_active": True,
                "created_at": datetime.utcnow().isoformat()
            }
            
            # Jika user memilih template, simpan ID-nya
            if template_id and template_id != "":
                payload['template_id'] = int(template_id)
                
            # Jika user memilih target group spesifik
            if target_group_id and target_group_id != "":
                payload['target_group_id'] = int(target_group_id)

            supabase.table('blast_schedules').insert(payload).execute()
            flash('Jadwal berhasil ditambahkan dengan konfigurasi baru.', 'success')
        except Exception as e:
            flash(f'Gagal menambah jadwal: {e}', 'danger')
            logger.error(f"Add Schedule Error: {e}")
            
    return redirect(url_for('dashboard_schedule'))

@app.route('/delete_schedule/<int:id>')
@login_required
def delete_schedule(id):
    try:
        supabase.table('blast_schedules').delete().eq('id', id).eq('user_id', session['user_id']).execute()
        flash('Jadwal dihapus.', 'success')
    except:
        flash('Gagal menghapus jadwal.', 'danger')
    return redirect(url_for('dashboard_schedule'))

@app.route('/delete_target/<int:id>')
@login_required
def delete_target(id):
    try:
        supabase.table('blast_targets').delete().eq('id', id).eq('user_id', session['user_id']).execute()
        flash('Target grup dihapus.', 'success')
    except:
        flash('Gagal menghapus target.', 'danger')
    return redirect(url_for('dashboard_targets'))

# --- NEW ROUTES FOR TEMPLATES ---

@app.route('/save_template', methods=['POST'])
@login_required
def save_template():
    user_id = session['user_id']
    name = request.form.get('name')
    msg = request.form.get('message')
    
    # Tangkap ID Sumber (Hidden Input di HTML)
    src_chat = request.form.get('source_chat_id')
    src_msg = request.form.get('source_message_id')
    
    # Konversi ke int/bigint kalau ada datanya
    final_chat = int(src_chat) if src_chat and src_chat != 'null' else None
    final_msg = int(src_msg) if src_msg and src_msg != 'null' else None

    if not name:
        flash('Nama Template wajib diisi.', 'warning')
        return redirect(url_for('dashboard_templates'))
    
    if MessageTemplateManager.create_template(user_id, name, msg, final_chat, final_msg):
        flash('Template tersimpan (Mode Cloud Reference)!', 'success')
    else: 
        flash('Gagal simpan.', 'danger')
    
    return redirect(url_for('dashboard_templates'))

@app.route('/delete_template/<int:id>')
@login_required
def delete_template(id):
    # Panggil fungsi manager baru (terima 2 output)
    success, msg = MessageTemplateManager.delete_template(session['user_id'], id)
    
    if success:
        flash(msg, 'success')
    else:
        # Tampilkan pesan error spesifik (misal: "Dipake di jadwal 08:00")
        flash(msg, 'danger')
        
    return redirect(url_for('dashboard_templates'))

# ==============================================================================
# SECTION 13: SUPER ADMIN PANEL
# ==============================================================================

@app.route('/super-admin')
@admin_required
def super_admin_dashboard():
    try:
        # Stats Logic
        users_res = supabase.table('users').select("id, is_banned", count='exact').execute()
        bots_res = supabase.table('telegram_accounts').select("id", count='exact').eq('is_active', True).execute()
        
        users_data = supabase.table('users').select("*").order('created_at', desc=True).execute().data
        
        stats = {
            'total_users': users_res.count or 0,
            'active_bots': bots_res.count or 0,
            'banned_users': sum(1 for u in users_data if u.get('is_banned'))
        }
        
        return render_template('admin/index.html', stats=stats, active_page='dashboard')
    except Exception as e:
        return f"Admin Dashboard Error: {e}"

@app.route('/super-admin/users')
@admin_required
def super_admin_users():
    try:
        users = supabase.table('users').select("*").order('created_at', desc=True).execute().data
        final_list = []
        
        for u in users:
            tele = supabase.table('telegram_accounts').select("*").eq('user_id', u['id']).execute().data
            
            # Wrapper Class
            class UserW:
                def __init__(self, d, t):
                    self.id = d['id']
                    self.email = d['email']
                    self.is_admin = d.get('is_admin')
                    self.is_banned = d.get('is_banned')
                    
                    raw_date = d.get('created_at')
                    try:
                        self.created_at = datetime.fromisoformat(raw_date.replace('Z', '+00:00')) if raw_date else datetime.now()
                    except:
                        self.created_at = datetime.now()
                    
                    self.telegram_account = None
                    if t:
                        self.telegram_account = type('o',(object,),t[0])
            
            final_list.append(UserW(u, tele))
            
        return render_template('admin/users.html', users=final_list, active_page='users')
    except Exception as e:
        return f"User List Error: {e}"

@app.route('/super-admin/ban/<int:user_id>', methods=['POST'])
@admin_required
def ban_user(user_id):
    try:
        u_data = supabase.table('users').select("is_banned").eq('id', user_id).execute().data
        if not u_data: return redirect(url_for('super_admin_users'))
        
        current_val = u_data[0].get('is_banned', False)
        new_val = not current_val
        
        supabase.table('users').update({'is_banned': new_val}).eq('id', user_id).execute()
        
        # If banned, kill the bot session
        if new_val:
            supabase.table('telegram_accounts').update({'is_active': False}).eq('user_id', user_id).execute()
            
        flash(f"Status User #{user_id} berhasil diubah.", 'success')
    except Exception as e:
        flash(f"Gagal update status: {e}", 'danger')
        
    return redirect(url_for('super_admin_users'))

# --- PING ENDPOINT ---
@app.route('/ping')
def ping():
    return jsonify({"status": "alive", "timestamp": datetime.utcnow().isoformat()}), 200

# ==========================================
# SECTION 14 : IMPORT & EXPORT CSV
# ==========================================

@app.route('/import_crm_csv', methods=['POST'])
@login_required
def import_crm_csv():
    """
    Import Database Pelanggan via CSV.
    Validasi: Header wajib 'user_id', 'username' (opsional), 'first_name' (opsional).
    """
    user_id = session['user_id']
    
    if 'file' not in request.files:
        return jsonify({"status": "error", "message": "Tidak ada file yang diunggah."})
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"status": "error", "message": "Nama file kosong."})
    
    if not file.filename.endswith('.csv'):
        return jsonify({"status": "error", "message": "Format file harus .csv"})

    try:
        # Baca file di memori (Stream) biar cepet
        stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
        csv_input = csv.DictReader(stream)
        
        # Validasi Header Minimal
        if 'user_id' not in csv_input.fieldnames:
            return jsonify({
                "status": "error", 
                "message": "Format CSV Salah! Wajib ada kolom header: 'user_id'."
            })

        valid_rows = []
        errors = 0
        
        for row in csv_input:
            try:
                # Validasi User ID harus Angka
                uid_raw = row.get('user_id', '').strip()
                if not uid_raw.isdigit():
                    errors += 1
                    continue 
                
                # Rapihkan data
                clean_data = {
                    "owner_id": user_id,
                    "user_id": int(uid_raw),
                    "username": row.get('username', '').strip() or None,
                    "first_name": row.get('first_name', 'Imported Contact').strip(),
                    "last_interaction": datetime.utcnow().isoformat(),
                    "created_at": datetime.utcnow().isoformat()
                }
                valid_rows.append(clean_data)
                
            except Exception:
                errors += 1
                continue

        if not valid_rows:
            return jsonify({"status": "error", "message": "File kosong atau data user_id tidak valid."})

        # Batch Insert ke Supabase (Biar aman kalau data ribuan)
        batch_size = 1000
        for i in range(0, len(valid_rows), batch_size):
            batch = valid_rows[i:i + batch_size]
            supabase.table('tele_users').upsert(batch, on_conflict="owner_id, user_id").execute()

        msg = f"Sukses import {len(valid_rows)} kontak."
        if errors > 0:
            msg += f" ({errors} baris dilewati karena format salah)"
            
        return jsonify({"status": "success", "message": msg})

    except Exception as e:
        return jsonify({"status": "error", "message": f"Gagal proses file: {str(e)}"})

@app.route('/export_crm_csv')
@login_required
def export_crm_csv():
    """Download Database Pelanggan jadi file CSV"""
    user_id = session['user_id']
    
    try:
        # Ambil data user dari DB
        res = supabase.table('tele_users').select("user_id, username, first_name, last_interaction")\
            .eq('owner_id', user_id).execute()
        
        data = res.data if res.data else []
        
        # Bikin CSV di memori
        si = io.StringIO()
        cw = csv.writer(si)
        
        # Header CSV
        cw.writerow(['user_id', 'username', 'first_name', 'last_interaction'])
        
        # Isi Data
        for row in data:
            cw.writerow([
                row.get('user_id'),
                row.get('username') or '',
                row.get('first_name') or '',
                row.get('last_interaction')
            ])
            
        output = si.getvalue()
        
        return Response(
            output,
            mimetype="text/csv",
            headers={"Content-disposition": f"attachment; filename=database_pelanggan_{datetime.now().strftime('%Y%m%d')}.csv"}
        )
        
    except Exception as e:
        flash(f"Gagal export: {e}", "danger")
        return redirect(url_for('dashboard_crm'))


# ==============================================================================
# SECTION 15: INITIALIZATION ROUTINE
# ==============================================================================

def init_system_check():
    """Runs once on startup to ensure admin exists & environment is healthy"""
    adm_email = os.getenv('SUPER_ADMIN', 'admin@baba.com')
    adm_pass = os.getenv('PASS_ADMIN', 'admin123')
    
    if supabase:
        try:
            logger.info(f"⚙️ System Startup: Checking Admin ({adm_email})...")
            res = supabase.table('users').select("id").eq('email', adm_email).execute()
            
            new_hash = generate_password_hash(adm_pass)
            
            if not res.data:
                # Create Admin
                data = {
                    'email': adm_email, 
                    'password': new_hash, 
                    'is_admin': True, 
                    'created_at': datetime.utcnow().isoformat()
                }
                supabase.table('users').insert(data).execute()
                logger.info("👑 Super Admin Account Created Successfully")
            else:
                # Sync Admin Password from Env
                uid = res.data[0]['id']
                supabase.table('users').update({
                    'password': new_hash, 
                    'is_admin': True
                }).eq('id', uid).execute()
                logger.info("🔄 Admin Password Synced with Environment")
                
        except Exception as e:
            logger.warning(f"⚠️ Admin Init Warning: {e}")

# --- [PERBAIKAN DISINI] ---
if supabase:
    print("⚙️ Executing System Check...", flush=True) # Debug log
    init_system_check()
    
# Start Background Pinger
start_self_ping()
    
# --- [BATAS SUCI] --
if __name__ == '__main__':
    # Run App
    app.run(debug=True, port=5000, use_reloader=False)
