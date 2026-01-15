import os
import asyncio
import logging
import threading
import json
import time
import httpx
from functools import wraps 
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash, send_from_directory

# --- TELETHON & SUPABASE ---
from telethon import TelegramClient, errors, functions, utils
from telethon.sessions import StringSession
from supabase import create_client, Client

# ==============================================================================
# SECTION 1: SYSTEM CONFIGURATION & ENVIRONMENT SETUP
# ==============================================================================

# Initialize Flask Application
app = Flask(__name__)

# Security Configuration
# Gunakan Secret Key yang sangat kuat untuk production environment
app.secret_key = os.getenv('SECRET_KEY', 'rahasia_negara_baba_parfume_saas_ultimate_key_v99_production_ready')

# Session Configuration (Agar login user awet dan aman)
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)
app.config['SESSION_COOKIE_SECURE'] = True  # Hanya kirim cookie via HTTPS (Wajib di Production)
app.config['SESSION_COOKIE_HTTPONLY'] = True # Mencegah akses JavaScript ke cookie (Anti-XSS)
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax' # Mencegah CSRF

# Upload Configuration (Untuk fitur Broadcast Gambar masa depan)
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
    # Kita set None, tapi aplikasi akan tetap jalan (dengan fitur terbatas/error saat akses DB)
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
# Wajib ada di Environment Variables Render
API_ID = int(os.getenv('API_ID', '0')) 
API_HASH = os.getenv('API_HASH', '')

# In-Memory State Storage
# Digunakan untuk rate limiting dan caching sementara objek client saat login.
# Data kritis (seperti Hash OTP) tetap disimpan di Database agar Stateless (aman saat restart).
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
                # Tunggu sebentar jika error sebelum coba lagi agar tidak spam log
                time.sleep(60)

    # Jalankan sebagai Daemon Thread (Mati otomatis jika main app mati)
    threading.Thread(target=_worker, daemon=True, name="PingWorker").start()

def run_async(coroutine):
    """
    Bridge Helper: Menjalankan Asyncio Coroutine di dalam Flask (Synchronous).
    Membuat Event Loop terisolasi untuk setiap eksekusi guna mencegah blocking dan thread safety issue.
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
            # Clean up pending tasks generator
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
            hashed_pw = generate_password_hash(password, method='sha256')
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
    """Halaman Utama Dashboard: Ringkasan Statistik"""
    user = get_dashboard_context()
    if not user: return redirect(url_for('login'))
    
    uid = user.id
    # Default values
    logs, schedules, targets, crm_count = [], [], [], 0
    
    if supabase:
        try:
            # Fetch essential data for overview
            logs = supabase.table('blast_logs').select("*").eq('user_id', uid).order('created_at', desc=True).limit(20).execute().data
            schedules = supabase.table('blast_schedules').select("*").eq('user_id', uid).execute().data
            targets = supabase.table('blast_targets').select("*").eq('user_id', uid).execute().data
            # Efficient counting
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
                           active_page='dashboard')

@app.route('/dashboard/broadcast')
@login_required
def dashboard_broadcast():
    """Halaman Fitur Broadcast & Preview Pesan"""
    user = get_dashboard_context()
    if not user: return redirect(url_for('login'))
    
    # Ambil jumlah CRM user untuk info
    crm_count = 0
    try:
        crm_res = supabase.table('tele_users').select("id", count='exact', head=True).eq('owner_id', user.id).execute()
        crm_count = crm_res.count if crm_res.count else 0
    except: pass

    return render_template('dashboard/broadcast.html', user=user, user_count=crm_count, active_page='broadcast')

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
    try:
        schedules = supabase.table('blast_schedules').select("*").eq('user_id', user.id).order('run_hour', desc=False).execute().data
    except: pass
    
    return render_template('dashboard/schedule.html', user=user, schedules=schedules, active_page='schedule')

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

@app.route('/scan_groups_api')
@login_required
def scan_groups_api():
    """Scan Groups/Channels/Forums"""
    user_id = session['user_id']
    
    async def _scan():
        client = await get_active_client(user_id)
        if not client: return jsonify({"status": "error", "message": "Telegram tidak terhubung."})
        
        groups = []
        try:
            # Scan limit 300 dialogs
            async for dialog in client.iter_dialogs(limit=300):
                if dialog.is_group:
                    is_forum = getattr(dialog.entity, 'forum', False)
                    real_id = utils.get_peer_id(dialog.entity)
                    
                    g_data = {
                        'id': real_id, 
                        'name': dialog.name, 
                        'is_forum': is_forum, 
                        'topics': []
                    }
                    
                    # Scan Topics if Forum
                    if is_forum:
                        try:
                            topics = await client.get_forum_topics(dialog.entity, limit=10)
                            if topics and topics.topics:
                                for t in topics.topics:
                                    g_data['topics'].append({'id': t.id, 'title': t.title})
                        except: pass
                    
                    groups.append(g_data)
        except Exception as e:
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

# ==============================================================================
# SECTION 11: BROADCAST SYSTEM (TEXT + IMAGE)
# ==============================================================================

# ... Bagian atas app.py tetap sama ...

@app.route('/start_broadcast', methods=['POST'])
@login_required
def start_broadcast():
    """
    Handle Broadcast Request (Text + Image Support).
    Uses Background Thread for sending to prevent blocking.
    """
    user_id = session['user_id']
    message = request.form.get('message')
    image_file = request.files.get('image') # Support Image Upload
    
    if not message:
        return jsonify({"status": "error", "message": "Pesan wajib diisi."})

    # Handle Image Upload
    image_path = None
    if image_file and allowed_file(image_file.filename):
        filename = secure_filename(f"broadcast_{user_id}_{int(time.time())}_{image_file.filename}")
        image_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        image_file.save(image_path)

    # Define Background Worker
    def _broadcast_worker(uid, msg, img_path):
        async def _logic():
            client = await get_active_client(uid)
            if not client: 
                # Cleanup Image if failed
                if img_path and os.path.exists(img_path): os.remove(img_path)
                return
            
            try:
                # Fetch CRM Users
                crm_users = supabase.table('tele_users').select("*").eq('owner_id', uid).execute().data
                
                for u in crm_users:
                    try:
                        target_peer = None
                        user_tele_id = int(u['user_id'])

                        # CRITICAL FIX: Robust Entity Resolving
                        # 1. Coba ambil dari cache
                        try:
                            target_peer = await client.get_input_entity(user_tele_id)
                        except ValueError:
                            # 2. Jika gagal, coba fetch paksa dari network (Slow but works)
                            try:
                                target_peer = await client.get_entity(user_tele_id)
                            except Exception as e:
                                logger.warning(f"Entity not found for {user_tele_id}: {e}")
                                continue

                        # Personalize Message
                        final_msg = msg.replace("{name}", u.get('first_name') or "Kak")
                        
                        # Send with or without Image
                        if img_path:
                            await client.send_file(target_peer, img_path, caption=final_msg)
                        else:
                            await client.send_message(target_peer, final_msg)
                            
                        # Anti-Flood Delay
                        await asyncio.sleep(3) 
                    except Exception as e:
                        logger.warning(f"Broadcast Fail to {u['user_id']}: {e}")
            except Exception as e:
                logger.error(f"Broadcast System Error: {e}")
            finally:
                await client.disconnect()
                # Cleanup Image after broadcast finishes
                if img_path and os.path.exists(img_path):
                    try: os.remove(img_path)
                    except: pass

        # Run Async Logic in New Event Loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_logic())
        finally:
            loop.close()

    # Launch Thread
    threading.Thread(target=_broadcast_worker, args=(user_id, message, image_path)).start()
    
    return jsonify({"status": "success", "message": "Broadcast sedang diproses di latar belakang!"})

# ... Bagian bawah app.py tetap sama ...

### 3. Update `templates/dashboard/index.html` (Perbaikan Paginasi Log)
Update sedikit di `templates/dashboard/index.html` untuk memastikan tombol paginasi bekerja dan dropdown baris berfungsi.


http://googleusercontent.com/immersive_entry_chip/1

Update kedua file template ini dan logika `start_broadcast` di `app.py`. Sekarang login Telegram sudah ada halamannya lagi, dan broadcast akan mencoba mencari entitas pengguna lebih keras sebelum menyerah, mengurangi kemungkinan error "Could not find input entity".

# ==============================================================================
# SECTION 12: CRUD ROUTES (SCHEDULE & TARGETS)
# ==============================================================================

@app.route('/add_schedule', methods=['POST'])
@login_required
def add_schedule():
    h = request.form.get('hour')
    m = request.form.get('minute')
    
    if h and m:
        try:
            supabase.table('blast_schedules').insert({
                "user_id": session['user_id'],
                "run_hour": int(h),
                "run_minute": int(m),
                "is_active": True,
                "created_at": datetime.utcnow().isoformat()
            }).execute()
            flash('Jadwal berhasil ditambahkan.', 'success')
        except Exception as e:
            flash(f'Gagal menambah jadwal: {e}', 'danger')
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

if __name__ == '__main__':
    # Initialize System
    init_system_check()
    # Start Background Pinger
    start_self_ping()
    # Run App
    app.run(debug=True, port=5000, use_reloader=False)
