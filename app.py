import os
import asyncio
import logging
import threading
import json
import time
import httpx
import pytz
from functools import wraps 
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash, send_from_directory

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
    Worker cerdas yang berjalan di thread terpisah.
    Tugasnya mengecek database 'blast_schedules' setiap menit dan
    mengeksekusi pengiriman pesan jika waktunya cocok.
    """
    
    @staticmethod
    def start():
        threading.Thread(target=SchedulerWorker._loop, daemon=True, name="SchedulerEngine").start()
        logger.info("🕒 Scheduler Engine Started (Background Mode)")

    @staticmethod
    def _loop():
        """Main Loop untuk pengecekan jadwal"""
        while True:
            try:
                # Cek setiap awal menit (detik 00)
                now = datetime.utcnow() # Gunakan UTC sebagai standar server
                
                # Kita bisa menambahkan logika konversi timezone user di sini nantinya.
                # Untuk saat ini, kita asumsikan server time (UTC) atau disesuaikan +7 manual oleh user.
                
                if supabase:
                    SchedulerWorker._process_schedules(now)
                
                # Sleep selama 60 detik agar tidak spam DB
                # Hitung sisa detik menuju menit berikutnya agar presisi
                sleep_time = 60 - datetime.now().second
                time.sleep(sleep_time)
                
            except Exception as e:
                logger.error(f"Scheduler Loop Error: {e}")
                time.sleep(60) # Sleep aman jika error

    @staticmethod
    def _process_schedules(current_time):
        """Logika inti pengecekan dan eksekusi jadwal"""
        try:
            current_hour = current_time.hour
            current_minute = current_time.minute
            
            # Ambil semua jadwal yang AKTIF dan cocok dengan JAM & MENIT sekarang
            # NOTE: Di production, perlu query yang lebih efisien atau batch processing.
            res = supabase.table('blast_schedules').select("*")\
                .eq('is_active', True)\
                .eq('run_hour', current_hour)\
                .eq('run_minute', current_minute)\
                .execute()
                
            schedules = res.data
            
            if not schedules:
                return # Tidak ada jadwal saat ini
                
            logger.info(f"🕒 Scheduler: Found {len(schedules)} tasks to run at {current_hour}:{current_minute} UTC")
            
            for task in schedules:
                # Jalankan blast di thread terpisah agar tidak memblokir loop scheduler
                threading.Thread(target=SchedulerWorker._execute_task, args=(task,)).start()
                
        except Exception as e:
            logger.error(f"Scheduler Process Error: {e}")

    @staticmethod
    def _execute_task(task):
        """Eksekusi satu task jadwal (Support Forum Topic)"""
        user_id = task['user_id']
        template_id = task.get('template_id') 
        target_group_id = task.get('target_group_id') 
        
        # 1. Siapkan Pesan
        message_content = "Halo! Ini pesan terjadwal otomatis."
        source_media = None
        
        # Logic Template & Media Source (Copy Media)
        if template_id:
            tmpl = MessageTemplateManager.get_template_by_id(template_id)
            if tmpl:
                message_content = tmpl['message_text']
                # Cek Reference Media
                if tmpl.get('source_chat_id') and tmpl.get('source_message_id'):
                    source_media = {'chat': int(tmpl['source_chat_id']), 'id': int(tmpl['source_message_id'])}

        # 2. Worker Async
        async def _async_send():
            client = await get_active_client(user_id)
            if not client: return
            
            try:
                # Load Media jika ada
                media_obj = None
                if source_media:
                    try:
                        src_msg = await client.get_messages(source_media['chat'], ids=source_media['id'])
                        if src_msg and src_msg.media: media_obj = src_msg.media
                    except: pass

                # Ambil Target
                targets_query = supabase.table('blast_targets').select("*").eq('user_id', user_id)
                if target_group_id: targets_query = targets_query.eq('id', target_group_id)
                targets = targets_query.execute().data
                
                for tg in targets:
                    try:
                        entity = await client.get_entity(int(tg['group_id']))
                        
                        # --- LOGIC BARU: HANDLING TOPIK ---
                        # Cek apakah ada topic_ids yang disimpan
                        topic_ids = []
                        if tg.get('topic_ids'):
                            # Convert string "123,456" jadi list [123, 456]
                            try: topic_ids = [int(x.strip()) for x in str(tg['topic_ids']).split(',') if x.strip().isdigit()]
                            except: pass
                        
                        # Tentukan Destinasi (List of 'reply_to')
                        # Kalau gak ada topik, kirim sekali (reply_to=None)
                        destinations = topic_ids if topic_ids else [None]
                        
                        for top_id in destinations:
                            final_msg = message_content.replace("{name}", tg.get('group_name') or "Kak")
                            
                            # Kirim (Pake reply_to buat nembak Topik)
                            if media_obj:
                                await client.send_file(entity, media_obj, caption=final_msg, reply_to=top_id)
                            else:
                                await client.send_message(entity, final_msg, reply_to=top_id)
                            
                            # Log Sukses
                            topik_info = f" (Topic: {top_id})" if top_id else ""
                            supabase.table('blast_logs').insert({
                                "user_id": user_id, "group_name": f"{tg['group_name']}{topik_info}",
                                "group_id": tg['group_id'], "status": "SUCCESS", 
                                "created_at": datetime.utcnow().isoformat()
                            }).execute()
                            
                            await asyncio.sleep(3) # Delay antar topik/grup

                    except Exception as e:
                        supabase.table('blast_logs').insert({
                            "user_id": user_id, "group_name": tg.get('group_name', '?'),
                            "status": "FAILED", "error_message": str(e), 
                            "created_at": datetime.utcnow().isoformat()
                        }).execute()
            finally: await client.disconnect()
        
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
    """Halaman Manajemen Jadwal Blast"""
    user = get_dashboard_context()
    if not user: return redirect(url_for('login'))
    
    schedules = []
    templates = [] # Variable baru untuk dropdown
    targets = []   # Variable baru untuk dropdown target grup
    
    try:
        # Ambil jadwal lama
        schedules = supabase.table('blast_schedules').select("*").eq('user_id', user.id).order('run_hour', desc=False).execute().data
        
        # UPGRADE: Fetch Templates & Targets untuk form "Add Schedule"
        templates = MessageTemplateManager.get_templates(user.id)
        targets = supabase.table('blast_targets').select("*").eq('user_id', user.id).execute().data
        
        # Enrich schedule data with template names (Manual Join)
        # Agar di UI tampil nama templatenya, bukan cuma ID
        for s in schedules:
            t_id = s.get('template_id')
            s['template_name'] = 'Custom / No Template'
            if t_id:
                # Cari nama template di list templates (in-memory search biar cepat)
                found = next((t for t in templates if t['id'] == t_id), None)
                if found:
                    s['template_name'] = found['name']
        
    except Exception as e:
        logger.error(f"Schedule Page Error: {e}")
    
    return render_template('dashboard/schedule.html', 
                           user=user, 
                           schedules=schedules, 
                           templates=templates, # Kirim ke UI
                           targets=targets,     # Kirim ke UI
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
    """Halaman Database Pelanggan (CRM)"""
    user = get_dashboard_context()
    if not user: return redirect(url_for('login'))
    
    crm_count = 0
    crm_users = [] # Optional: Load some users if needed
    try:
        # Get Count
        crm_res = supabase.table('tele_users').select("id", count='exact', head=True).eq('owner_id', user.id).execute()
        crm_count = crm_res.count if crm_res.count else 0
        # Get latest 50 contacts
        crm_users = supabase.table('tele_users').select("*").eq('owner_id', user.id).order('last_interaction', desc=True).limit(50).execute().data
    except: pass
    
    return render_template('dashboard/crm.html', user=user, user_count=crm_count, crm_users=crm_users, active_page='crm')

@app.route('/dashboard/connection')
@login_required
def dashboard_connection():
    user = get_user_data(session['user_id'])
    if not user: return redirect(url_for('login'))
    return render_template('dashboard/connection.html', user=user, active_page='connection')

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
    
    if not phone:
        return jsonify({'status': 'error', 'message': 'Nomor HP wajib diisi.'})

    # Rate Limiting Logic (60s Cooldown)
    current_time = time.time()
    if user_id in login_states:
        last_req = login_states[user_id].get('last_otp_req', 0)
        if current_time - last_req < 60:
            remaining = int(60 - (current_time - last_req))
            return jsonify({'status': 'cooldown', 'message': f'Mohon tunggu {remaining} detik lagi.', 'remaining': remaining})
    
    async def _process_send_code():
        # Create FRESH Session for OTP Request
        client = TelegramClient(StringSession(), API_ID, API_HASH)
        await client.connect()
        
        try:
            # Check if phone is authorized (should be false for login flow)
            if not await client.is_user_authorized():
                req = await client.send_code_request(phone)
                
                # --- STATELESS MECHANISM START ---
                # Capture session state containing the Auth Key (Session A)
                temp_session_str = client.session.save()
                
                # Save to DB (Temporary Storage)
                # 'session_string' stores the session state
                # 'targets' column borrows the phone_code_hash
                data = {
                    'user_id': user_id,
                    'phone_number': phone,
                    'session_string': temp_session_str,
                    'targets': req.phone_code_hash, # Using targets col for Hash
                    'is_active': False,
                    'created_at': datetime.utcnow().isoformat()
                }
                supabase.table('telegram_accounts').upsert(data, on_conflict="user_id").execute()
                # --- STATELESS MECHANISM END ---
                
                # Update RAM State for Rate Limiting Only
                login_states[user_id] = {'last_otp_req': current_time}
                
                return jsonify({'status': 'success', 'message': 'Kode OTP terkirim ke Telegram Anda!'})
            else:
                return jsonify({'status': 'error', 'message': 'Nomor ini sudah login aktif.'})
                
        except errors.FloodWaitError as e:
            return jsonify({'status': 'error', 'message': f'Terlalu banyak percobaan. Tunggu {e.seconds} detik.'})
        except errors.PhoneNumberInvalidError:
            return jsonify({'status': 'error', 'message': 'Format nomor salah. Gunakan +62...'})
        except Exception as e:
            logger.error(f"OTP System Error: {e}")
            return jsonify({'status': 'error', 'message': f'Telegram Error: {str(e)}'})
        finally:
            await client.disconnect()

    try:
        return run_async(_process_send_code())
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Internal Server Error: {str(e)}'})

@app.route('/api/connect/verify_code', methods=['POST'])
@login_required
def verify_code():
    user_id = session['user_id']
    otp = request.json.get('otp')
    pw = request.json.get('password')
    
    # 1. Retrieve Stored Session & Hash from DB
    db_session = None
    db_hash = None
    db_phone = None
    
    try:
        res = supabase.table('telegram_accounts').select("session_string, phone_number, targets").eq('user_id', user_id).execute()
        if not res.data:
            return jsonify({'status': 'error', 'message': 'Sesi tidak ditemukan. Silakan kirim ulang OTP.'})
        
        db_session = res.data[0]['session_string']
        db_phone = res.data[0]['phone_number']
        db_hash = res.data[0]['targets'] # Hash OTP disimpan disini
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Database Read Error: {str(e)}'})

    async def _process_verify():
        # 2. Resume Session from DB String (Session A)
        client = TelegramClient(StringSession(db_session), API_ID, API_HASH)
        await client.connect()
        
        try:
            # 3. Perform Sign In using existing session context
            try:
                await client.sign_in(db_phone, otp, phone_code_hash=db_hash)
            except errors.SessionPasswordNeededError:
                if not pw:
                    return jsonify({'status': '2fa', 'message': 'Akun dilindungi 2FA. Masukkan Password.'})
                await client.sign_in(password=pw)
            
            # 4. Success Handling
            # Save the Final Authenticated Session
            final_session = client.session.save()
            
            # Update DB to Active State
            supabase.table('telegram_accounts').update({
                'session_string': final_session,
                'is_active': True,
                'targets': '[]', # Clear temp hash
                'created_at': datetime.utcnow().isoformat()
            }).eq('user_id', user_id).execute()
            
            return jsonify({'status': 'success', 'message': 'Login Berhasil! Mengalihkan...'})
            
        except errors.PhoneCodeInvalidError:
            return jsonify({'status': 'error', 'message': 'Kode OTP salah.'})
        except errors.PhoneCodeExpiredError:
            return jsonify({'status': 'error', 'message': 'Kode OTP kadaluarsa/Sesi berubah. Kirim ulang.'})
        except Exception as e:
            logger.error(f"Verification Logic Error: {e}")
            return jsonify({'status': 'error', 'message': f'Gagal Login: {str(e)}'})
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
    """Scan Groups/Channels/Forums (ULTIMATE VERSION: AUTO-DETECT + GENERAL TOPIC)"""
    user_id = session['user_id']
    
    async def _scan():
        client = await get_active_client(user_id)
        if not client: return jsonify({"status": "error", "message": "Telegram tidak terhubung."})
        
        groups = []
        try:
            # Scan limit 200 dialogs
            async for dialog in client.iter_dialogs(limit=200):
                if dialog.is_group:
                    is_forum = getattr(dialog.entity, 'forum', False)
                    real_id = utils.get_peer_id(dialog.entity)
                    
                    g_data = {
                        'id': real_id, 
                        'name': dialog.name, 
                        'is_forum': is_forum, 
                        'topics': []
                    }
                    
                    # --- LOGIC KHUSUS FORUM ---
                    if is_forum:
                        logger.info(f"🔍 Scanning topics for: {dialog.name} ({real_id})")
                        found_topics = []
                        
                        try:
                            # STEP 1: Coba cara halus (High Level API)
                            # iter_forum_topics kadang lebih stabil daripada GetForumTopicsRequest langsung
                            async for t in client.iter_forum_topics(dialog.entity, limit=50):
                                t_title = getattr(t, 'title', '') or f"Topic #{t.id}"
                                found_topics.append({'id': t.id, 'title': t_title})
                                
                        except Exception as e:
                            logger.warning(f"⚠️ Scan Method 1 failed for {dialog.name}: {e}")
                            
                            # STEP 2: Coba cara kasar (RAW API) jika cara halus gagal
                            try:
                                input_peer = utils.get_input_channel(dialog.entity)
                                res = await client(functions.channels.GetForumTopicsRequest(
                                    channel=input_peer,
                                    offset_date=0, offset_id=0, offset_topic=0, limit=50
                                ))
                                if res.topics:
                                    for t in res.topics:
                                        t_title = getattr(t, 'title', '') or f"Topic #{t.id}"
                                        found_topics.append({'id': t.id, 'title': t_title})
                            except Exception as e2:
                                logger.error(f"❌ Scan Method 2 also failed: {e2}")

                        # STEP 3: Pastikan General Topic selalu ada
                        # Cek apakah ID 1 (General) udah keambil? Kalau belum, masukin manual.
                        has_general = any(t['id'] == 1 for t in found_topics)
                        if not has_general:
                            # Masukkan Topik General di paling atas
                            found_topics.insert(0, {'id': 1, 'title': 'General / Topik Utama 📌'})

                        g_data['topics'] = found_topics
                    
                    groups.append(g_data)
                    
        except Exception as e:
            logger.error(f"Global Scan Error: {e}")
            return jsonify({'status': 'error', 'message': f"Scan Failed: {str(e)}"})
        finally:
            await client.disconnect()
            
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
# SECTION 11: BROADCAST SYSTEM (TEXT + IMAGE)
# ==============================================================================

@app.route('/start_broadcast', methods=['POST'])
@login_required
def start_broadcast():
        user_id = session['user_id']
        message = request.form.get('message')
        template_id = request.form.get('template_id')
        selected_ids_str = request.form.get('selected_ids') # Tangkap ID
        image_file = request.files.get('image')
        
        # Logic Template
        if not message and template_id:
            tmpl = MessageTemplateManager.get_template_by_id(template_id)
            if tmpl: message = tmpl['content']
                
        if not message:
            return jsonify({"status": "error", "message": "Pesan wajib diisi."})
    
        # Upload Image
        image_path = None
        if image_file and allowed_file(image_file.filename):
            filename = secure_filename(f"broadcast_{user_id}_{int(time.time())}_{image_file.filename}")
            image_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            image_file.save(image_path)

# --- WORKER BARU (SUPPORT CLOUD MEDIA) ---
def _broadcast_worker(uid, msg, img_path, target_ids, template_id=None):
    async def _logic():
        client = await get_active_client(uid)
        if not client: return
            
        # Pemanasan
        try: await client.get_dialogs(limit=None)
        except: pass

            # Cek Template Source (Kalau pakai template)
        source_media = None
        if template_id:
            tmpl = MessageTemplateManager.get_template_by_id(template_id)
            # Cek apakah template punya referensi media (Database Mandiri)
            if tmpl and tmpl.get('source_chat_id') and tmpl.get('source_message_id'):
                try:
                    # Ambil pesan aslinya dari "Database Mandiri" user
                    src_chat = int(tmpl['source_chat_id'])
                    src_id = int(tmpl['source_message_id'])
                    original_msg = await client.get_messages(src_chat, ids=src_id)
                    if original_msg and original_msg.media:
                        source_media = original_msg.media
                except: 
                    print("Gagal load media source")

        try:
            # ... (query user crm sama kayak sebelumnya) ...
                
            for u in crm_users:
                try:
                    user_tele_id = int(u['user_id'])
                    target = await client.get_input_entity(user_tele_id)
                    final_msg = msg.replace("{name}", u.get('first_name') or "Kak")
                        
                    # LOGIC PENGIRIMAN SAKTI
                    if source_media:
                        # 1. Prioritas: Kirim Media dari Telegram Database (TANPA FORWARD LABEL)
                        await client.send_file(target, source_media, caption=final_msg)
                    elif img_path: 
                        # 2. Upload manual (file lokal)
                        await client.send_file(target, img_path, caption=final_msg)
                    else: 
                        # 3. Teks doang
                        await client.send_message(target, final_msg)
                        
                    await asyncio.sleep(2)
                except: pass
        finally:
            await client.disconnect()
            if img_path: os.remove(img_path)
        
    run_async(_logic())

    # Panggil worker (tambah parameter template_id)
    # Pastikan template_id di-pass ke worker
    t_id_int = int(template_id) if template_id else None
    threading.Thread(target=_broadcast_worker, args=(user_id, message, image_path, selected_ids_str, t_id_int)).start()

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

# ==============================================================================
# SECTION 14: INITIALIZATION ROUTINE
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
