import os
import asyncio
import logging
import threading
import json
import time
import httpx # Pastikan ada di requirements.txt
from functools import wraps 
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
from werkzeug.security import generate_password_hash, check_password_hash
from telethon import TelegramClient, errors, functions, utils
from telethon.sessions import StringSession
from supabase import create_client, Client

# ==========================================
# 1. CONFIGURATION & SETUP
# ==========================================

app = Flask(__name__)
# Gunakan secret key yang kuat
app.secret_key = os.getenv('SECRET_KEY', 'rahasia_negara_baba_parfume_saas_ultimate_key_v99')

# Logging Setup (Professional Logging)
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("BabaSaaS")

# --- SUPABASE CONNECTION ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    logger.critical("❌ CRITICAL: SUPABASE_URL or SUPABASE_KEY missing! Database features will fail.")
    supabase = None
else:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        logger.info("✅ Supabase API Connected Successfully")
    except Exception as e:
        logger.critical(f"❌ Supabase Init Error: {e}")
        supabase = None

# --- GLOBAL VARIABLES ---
# API ID/HASH Master Aplikasi (Wajib ada di Env Render)
API_ID = int(os.getenv('API_ID', '0')) 
API_HASH = os.getenv('API_HASH', '')

# Login States (Hanya untuk Rate Limiting, tidak simpan client object lagi)
login_states = {} 

# ==========================================
# 2. HELPER SYSTEMS (ASYNC & BACKGROUND TASKS)
# ==========================================

# --- AUTO PING / ANTI SLEEP MECHANISM ---
def start_self_ping():
    """Anti-Sleep Feature: Pings itself every 14 minutes."""
    site_url = os.getenv('SITE_URL') or os.getenv('RENDER_EXTERNAL_URL')
    
    if not site_url:
        logger.warning("⚠️ SITE_URL/RENDER_EXTERNAL_URL not set. Self-ping might not work.")
        return

    if not site_url.startswith('http'):
        site_url = f'https://{site_url}'
        
    ping_url = f"{site_url}/ping"
    logger.info(f"🚀 Anti-Sleep (Self-Ping) Active! Target: {ping_url}")

    def run_pinger():
        while True:
            try:
                time.sleep(840) # 14 menit
                r = httpx.get(ping_url, timeout=10)
                logger.info(f"💓 [Keep-Alive] Ping Status: {r.status_code}")
            except Exception as e:
                logger.error(f"⚠️ [Keep-Alive] Ping Failed: {e}")

    threading.Thread(target=run_pinger, daemon=True).start()

# --- ASYNCIO RUNNER (FLASK <-> TELETHON BRIDGE) ---
def run_async(coro):
    """
    Menjalankan coroutine asyncio dalam konteks synchronous Flask.
    Membuat loop baru setiap eksekusi untuk thread safety.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    except Exception as e:
        logger.error(f"Async Runner Error: {e}")
        raise e
    finally:
        try:
            loop.close()
        except:
            pass

# --- DATABASE ABSTRACTION HELPERS ---

def get_user_data(user_id):
    """Retrieves full user data along with their telegram account info."""
    if not supabase: return None
    try:
        u_res = supabase.table('users').select("*").eq('id', user_id).execute()
        if not u_res.data: return None
        user_data = u_res.data[0]
        
        t_res = supabase.table('telegram_accounts').select("*").eq('user_id', user_id).execute()
        tele_data = t_res.data[0] if t_res.data else None
        
        class UserWrapper:
            def __init__(self, d, t):
                self.id = d['id']
                self.email = d['email']
                self.is_admin = d.get('is_admin', False)
                self.is_banned = d.get('is_banned', False)
                self.created_at = d.get('created_at')
                self.telegram_account = None
                if t:
                    self.telegram_account = type('TeleObj', (object,), {
                        'phone_number': t.get('phone_number'),
                        'is_active': t.get('is_active', False),
                        'created_at': t.get('created_at')
                    })
        return UserWrapper(user_data, tele_data)
    except Exception as e:
        logger.error(f"Error fetching user data: {e}")
        return None

async def get_user_client(user_id):
    """Creates an active Telethon Client using the session string from DB."""
    if not supabase: return None
    try:
        res = supabase.table('telegram_accounts').select("session_string").eq('user_id', user_id).eq('is_active', True).execute()
        if not res.data: return None
        session_str = res.data[0]['session_string']
        
        # Inisialisasi Klien Baru (Fresh Connection)
        client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
        await client.connect()
        
        if not await client.is_user_authorized():
            logger.warning(f"Session expired for user {user_id}")
            await client.disconnect()
            supabase.table('telegram_accounts').update({'is_active': False}).eq('user_id', user_id).execute()
            return None
        return client
    except Exception as e:
        logger.error(f"Error creating client for user {user_id}: {e}")
        return None

# ==========================================
# 3. DECORATORS & SECURITY
# ==========================================

@app.errorhandler(404)
def page_not_found(e):
    return redirect(url_for('dashboard')) if 'user_id' in session else redirect(url_for('login'))

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session: return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session: return redirect(url_for('login'))
        user = get_user_data(session['user_id'])
        if not user or not user.is_admin:
            flash('⛔ Akses Ditolak! Halaman ini khusus Super Admin.', 'danger')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function

# ==========================================
# 4. AUTH ROUTES (WEB)
# ==========================================

@app.route('/')
def index():
    return render_template('landing.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if not supabase:
            flash('Database Error: Environment Variables Missing', 'danger')
            return render_template('auth.html', mode='login')
        
        email = request.form.get('email')
        password = request.form.get('password')
        
        try:
            res = supabase.table('users').select("*").eq('email', email).execute()
            if res.data:
                user = res.data[0]
                if check_password_hash(user['password'], password):
                    if user.get('is_banned'):
                        flash('⛔ Akun Anda telah disuspend oleh Admin.', 'danger')
                        return redirect(url_for('login'))
                    
                    session['user_id'] = user['id']
                    if user.get('is_admin'): 
                        return redirect(url_for('super_admin_dashboard'))
                    return redirect(url_for('dashboard'))
            
            flash('Email atau Password salah.', 'danger')
        except Exception as e:
            logger.error(f"Login Error: {e}")
            flash('Terjadi kesalahan sistem saat login.', 'danger')
            
    return render_template('auth.html', mode='login')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        try:
            exist = supabase.table('users').select("id").eq('email', email).execute()
            if exist.data:
                flash('Email sudah terdaftar.', 'warning')
                return redirect(url_for('register'))
            
            hashed = generate_password_hash(password)
            data = {
                'email': email, 
                'password': hashed, 
                'created_at': datetime.utcnow().isoformat()
            }
            supabase.table('users').insert(data).execute()
            flash('Pendaftaran berhasil! Silakan login.', 'success')
            return redirect(url_for('login'))
        except Exception as e:
            logger.error(f"Register Error: {e}")
            flash('Gagal mendaftar.', 'danger')
    return render_template('auth.html', mode='register')

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('index'))

# ==========================================
# 5. USER DASHBOARD
# ==========================================

@app.route('/dashboard')
@login_required
def dashboard():
    user = get_user_data(session['user_id'])
    
    if not user: 
        session.pop('user_id', None)
        return redirect(url_for('login'))
    
    if user.is_banned: 
        session.pop('user_id', None)
        flash('⛔ Akun Anda disuspend.', 'danger')
        return redirect(url_for('login'))
    
    uid = user.id
    logs, schedules, targets, crm_count = [], [], [], 0
    
    if supabase:
        try:
            logs = supabase.table('blast_logs').select("*").eq('user_id', uid).order('created_at', desc=True).limit(50).execute().data
            schedules = supabase.table('blast_schedules').select("*").eq('user_id', uid).execute().data
            targets = supabase.table('blast_targets').select("*").eq('user_id', uid).execute().data
            crm_res = supabase.table('tele_users').select("id", count='exact', head=True).eq('owner_id', uid).execute()
            crm_count = crm_res.count if crm_res.count else 0
        except Exception as e: 
            logger.error(f"Dashboard Fetch Error: {e}")
    
    return render_template('dashboard.html', user=user, logs=logs, schedules=schedules, targets=targets, user_count=crm_count)

# ==========================================
# 6. TELEGRAM AUTHENTICATION (FIXED EVENT LOOP)
# ==========================================

@app.route('/api/connect/send_code', methods=['POST'])
@login_required
def send_code():
    phone = request.json.get('phone')
    user_id = session['user_id']
    
    if not phone: 
        return jsonify({'status': 'error', 'message': 'Nomor HP wajib diisi.'})

    # Rate Limit Check
    current_time = time.time()
    if user_id in login_states:
        last_req = login_states[user_id].get('last_otp_req', 0)
        if current_time - last_req < 45: # 45 detik cooldown
            remaining = int(45 - (current_time - last_req))
            return jsonify({'status': 'cooldown', 'message': f'Tunggu {remaining}s...', 'remaining': remaining})
    
    async def _send_otp_logic():
        # Bikin client BARU, connect, request, save hash, disconnect.
        client = TelegramClient(StringSession(), API_ID, API_HASH)
        await client.connect()
        try:
            if not await client.is_user_authorized():
                req = await client.send_code_request(phone)
                
                # SIMPAN HASH DI DATABASE (Bukan RAM Client Object)
                # Ini kunci biar gak kena error event loop!
                data = {
                    'user_id': user_id, 
                    'phone_number': phone,
                    'session_string': req.phone_code_hash, # Simpan HASH sementara disini
                    'is_active': False,
                    'created_at': datetime.utcnow().isoformat()
                }
                supabase.table('telegram_accounts').upsert(data, on_conflict="user_id").execute()
                
                # Update timestamp di RAM buat rate limit
                login_states[user_id] = {'last_otp_req': current_time}
                
                return jsonify({'status': 'success', 'message': 'Kode OTP terkirim!'})
            else:
                return jsonify({'status': 'error', 'message': 'Nomor ini sudah login.'})
        except errors.FloodWaitError as e:
            return jsonify({'status': 'error', 'message': f'Terlalu sering. Tunggu {e.seconds}s.'})
        except Exception as e:
            logger.error(f"OTP Error: {e}")
            return jsonify({'status': 'error', 'message': f'Error: {str(e)}'})
        finally:
            # PENTING: Selalu disconnect biar event loop bersih
            await client.disconnect()

    try: return run_async(_send_otp_logic())
    except Exception as e: return jsonify({'status': 'error', 'message': str(e)})

@app.route('/api/connect/verify_code', methods=['POST'])
@login_required
def verify_code():
    user_id = session['user_id']
    otp = request.json.get('otp')
    pw = request.json.get('password')
    
    # Ambil HASH dari Database (yang disimpen pas send_code)
    try:
        res = supabase.table('telegram_accounts').select("session_string, phone_number").eq('user_id', user_id).execute()
        if not res.data:
            return jsonify({'status': 'error', 'message': 'Data sesi hilang. Kirim ulang OTP.'})
        
        db_hash = res.data[0]['session_string']
        db_phone = res.data[0]['phone_number']
    except: 
        return jsonify({'status': 'error', 'message': 'Database Error'})

    async def _verify_logic():
        # Bikin client BARU lagi (Fresh Session)
        client = TelegramClient(StringSession(), API_ID, API_HASH)
        await client.connect()
        
        try:
            # Login pake HASH yang dari DB
            try: 
                await client.sign_in(db_phone, otp, phone_code_hash=db_hash)
            except errors.SessionPasswordNeededError: 
                if not pw: return jsonify({'status': '2fa', 'message': 'Butuh Password 2FA.'})
                await client.sign_in(password=pw)
            
            # Kalau sukses, simpan SESSION STRING ASLI (Permanen)
            real_session = client.session.save()
            
            supabase.table('telegram_accounts').update({
                'session_string': real_session, 
                'is_active': True, 
                'created_at': datetime.utcnow().isoformat()
            }).eq('user_id', user_id).execute()
            
            return jsonify({'status': 'success', 'message': 'Login Berhasil!'})
            
        except errors.PhoneCodeInvalidError:
            return jsonify({'status': 'error', 'message': 'Kode OTP salah.'})
        except errors.PhoneCodeExpiredError:
            return jsonify({'status': 'error', 'message': 'Kode OTP kadaluarsa.'})
        except Exception as e:
            logger.error(f"Verify Error: {e}")
            return jsonify({'status': 'error', 'message': f'Gagal: {str(e)}'})
        finally:
            await client.disconnect()

    try: return run_async(_verify_logic())
    except Exception as e: return jsonify({'status': 'error', 'message': str(e)})

# ==========================================
# 7. TELETHON FEATURES (BOT LOGIC)
# ==========================================

@app.route('/scan_groups_api')
@login_required
def scan_groups_api():
    user_id = session['user_id']
    async def _scan():
        client = await get_user_client(user_id)
        if not client: return jsonify({"status": "error", "message": "Telegram belum terkoneksi."})
        groups = []
        try:
            # Limit 300 dialogs
            async for dialog in client.iter_dialogs(limit=300):
                if dialog.is_group:
                    is_forum = getattr(dialog.entity, 'forum', False)
                    real_id = utils.get_peer_id(dialog.entity)
                    g_data = {'id': real_id, 'name': dialog.name, 'is_forum': is_forum, 'topics': []}
                    
                    if is_forum:
                        try:
                            topics = await client.get_forum_topics(dialog.entity, limit=10)
                            if topics and topics.topics:
                                for t in topics.topics: g_data['topics'].append({'id': t.id, 'title': t.title})
                        except: pass
                    groups.append(g_data)
        except Exception as e: 
            return jsonify({'status': 'error', 'message': str(e)})
        finally: 
            await client.disconnect()
        return jsonify({'status': 'success', 'data': groups})
    return run_async(_scan())

@app.route('/save_bulk_targets', methods=['POST'])
@login_required
def save_bulk_targets():
    user_id = session['user_id']
    data = request.json
    selected = data.get('targets', [])
    try:
        count = 0
        for item in selected:
            t_ids = ",".join(map(str, item.get('topic_ids', [])))
            payload = {
                "user_id": user_id, "group_name": item['group_name'], 
                "group_id": int(item['group_id']), "topic_ids": t_ids, 
                "is_active": True, "created_at": datetime.utcnow().isoformat()
            }
            # Upsert Logic
            ex = supabase.table('blast_targets').select('id').eq('user_id', user_id).eq('group_id', item['group_id']).execute()
            if ex.data: supabase.table('blast_targets').update(payload).eq('id', ex.data[0]['id']).execute()
            else: supabase.table('blast_targets').insert(payload).execute()
            count += 1
        return jsonify({"status": "success", "message": f"{count} target disimpan!"})
    except Exception as e: return jsonify({"status": "error", "message": str(e)})

@app.route('/import_crm_api', methods=['POST'])
@login_required
def import_crm_api():
    user_id = session['user_id']
    async def _import():
        client = await get_user_client(user_id)
        if not client: return jsonify({"status": "error", "message": "Tele disconnected"})
        count = 0
        try:
            async for dialog in client.iter_dialogs(limit=500):
                if dialog.is_user and not dialog.entity.bot:
                    u = dialog.entity
                    data = {
                        "owner_id": user_id, "user_id": u.id, 
                        "username": u.username, "first_name": u.first_name, 
                        "last_interaction": datetime.utcnow().isoformat(), 
                        "created_at": datetime.utcnow().isoformat()
                    }
                    try: supabase.table('tele_users').upsert(data, on_conflict="owner_id, user_id").execute(); count += 1
                    except: pass
            await client.disconnect()
            return jsonify({"status": "success", "message": f"Sukses! {count} kontak ditambahkan."})
        except Exception as e: return jsonify({"status": "error", "message": str(e)})
    return run_async(_import())

@app.route('/start_broadcast', methods=['POST'])
@login_required
def start_broadcast():
    user_id = session['user_id']
    message = request.form.get('message')
    if not message: return jsonify({"status": "error", "message": "Pesan kosong."})

    def _run_bg(uid, msg):
        async def _broadcast_logic():
            client = await get_user_client(uid)
            if not client: return
            try:
                crm_users = supabase.table('tele_users').select("*").eq('owner_id', uid).execute().data
                for u in crm_users:
                    try:
                        final_msg = msg.replace("{name}", u.get('first_name') or "Kak")
                        await client.send_message(int(u['user_id']), final_msg)
                        await asyncio.sleep(2) 
                    except: pass
            finally: await client.disconnect()
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_broadcast_logic())
        loop.close()

    threading.Thread(target=_run_bg, args=(user_id, message)).start()
    return jsonify({"status": "success", "message": "Broadcast berjalan di background!"})

# --- CRUD JADWAL & ADMIN ROUTES ---

@app.route('/add_schedule', methods=['POST'])
@login_required
def add_schedule():
    h, m = request.form.get('hour'), request.form.get('minute')
    if h and m:
        try:
            supabase.table('blast_schedules').insert({
                "user_id": session['user_id'], "run_hour": int(h), "run_minute": int(m), 
                "is_active": True, "created_at": datetime.utcnow().isoformat()
            }).execute()
            flash('Jadwal ditambahkan.', 'success')
        except: flash('Gagal tambah jadwal.', 'danger')
    return redirect(url_for('dashboard'))

@app.route('/delete_schedule/<int:id>')
@login_required
def delete_schedule(id):
    supabase.table('blast_schedules').delete().eq('id', id).eq('user_id', session['user_id']).execute()
    return redirect(url_for('dashboard'))

@app.route('/delete_target/<int:id>')
@login_required
def delete_target(id):
    supabase.table('blast_targets').delete().eq('id', id).eq('user_id', session['user_id']).execute()
    return redirect(url_for('dashboard'))

@app.route('/super-admin')
@admin_required
def super_admin_dashboard():
    try:
        users = supabase.table('users').select("*").order('created_at', desc=True).execute().data
        final_list = []
        stats = {'total_users': len(users), 'active_bots': 0, 'banned_users': 0}
        for u in users:
            if u.get('is_banned'): stats['banned_users'] += 1
            tele = supabase.table('telegram_accounts').select("*").eq('user_id', u['id']).execute().data
            if tele and tele[0].get('is_active'): stats['active_bots'] += 1
            class UserW:
                def __init__(self, d, t):
                    self.id = d['id']; self.email = d['email']; self.is_admin = d.get('is_admin'); self.is_banned = d.get('is_banned')
                    self.telegram_account = type('o',(object,),t[0]) if t else None
            final_list.append(UserW(u, tele))
        return render_template('super_admin.html', users=final_list, stats=stats)
    except: return "Admin Error"

@app.route('/super-admin/ban/<int:user_id>', methods=['POST'])
@admin_required
def ban_user(user_id):
    u = supabase.table('users').select("is_banned").eq('id', user_id).execute().data[0]
    new = not u['is_banned']
    supabase.table('users').update({'is_banned': new}).eq('id', user_id).execute()
    if new: supabase.table('telegram_accounts').update({'is_active': False}).eq('user_id', user_id).execute()
    return redirect(url_for('super_admin_dashboard'))

# --- PING (KEEP ALIVE) ---
@app.route('/ping')
def ping(): return jsonify({"status": "alive", "server_time": datetime.utcnow().isoformat()}), 200

# ==========================================
# 8. INITIALIZATION
# ==========================================

def init_check():
    """Admin Init & Password Sync"""
    adm_email = os.getenv('SUPER_ADMIN', 'admin@baba.com')
    adm_pass = os.getenv('PASS_ADMIN', 'admin123')
    
    if supabase:
        try:
            logger.info(f"Checking Admin Account: {adm_email}...")
            res = supabase.table('users').select("*").eq('email', adm_email).execute()
            new_hash = generate_password_hash(adm_pass)
            
            if not res.data:
                data = {'email': adm_email, 'password': new_hash, 'is_admin': True, 'created_at': datetime.utcnow().isoformat()}
                supabase.table('users').insert(data).execute()
                logger.info("👑 Super Admin Created")
            else:
                uid = res.data[0]['id']
                supabase.table('users').update({'password': new_hash, 'is_admin': True}).eq('id', uid).execute()
                logger.info("🔄 Admin Password Synced")
        except Exception as e:
            logger.warning(f"⚠️ Init Admin Warning: {e}")

if __name__ == '__main__':
    init_check()
    start_self_ping() 
    app.run(debug=True, port=5000, use_reloader=False)
