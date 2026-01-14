import os
import asyncio
import logging
import threading
import json
import time
from functools import wraps 
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
from werkzeug.security import generate_password_hash, check_password_hash
from telethon import TelegramClient, errors, functions, utils
from telethon.sessions import StringSession
from supabase import create_client, Client

# --- CONFIGURATION ---
app = Flask(__name__)
# Gunakan secret key yang kuat di production
app.secret_key = os.getenv('SECRET_KEY', 'rahasia_negara_baba_parfume_saas_ultimate_key_v99')

# --- SUPABASE CONNECTION ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("⚠️ WARNING: Supabase Credentials Missing! Fitur database tidak akan berfungsi.")
    supabase = None
else:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        print("✅ Supabase API Connected Successfully")
    except Exception as e:
        print(f"❌ Supabase Init Error: {e}")
        supabase = None

# --- GLOBAL VARS ---
# API ID/HASH Master Aplikasi (Wajib ada di Env Render)
API_ID = int(os.getenv('API_ID', '0')) 
API_HASH = os.getenv('API_HASH', '')

# Penyimpanan sementara untuk Login State (bisa diganti Redis untuk production skala besar)
# Format: {user_id: {'client': client_obj, 'phone': str, 'hash': str, 'last_otp_req': timestamp}}
login_states = {} 

# --- HELPER: RUN ASYNC IN SYNC ---
def run_async(coro):
    """
    Wrapper ajaib untuk menjalankan fungsi async (Telethon) di dalam route Flask (Sync).
    Membuat event loop baru untuk setiap eksekusi agar tidak bentrok.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()

# --- HELPER FUNCTIONS (DATABASE ABSTRACTION) ---

def get_user_data(user_id):
    """
    Mengambil data User beserta status akun Telegram-nya.
    Mengembalikan objek wrapper agar kompatibel dengan template Jinja2 (user.email, user.telegram_account.phone_number).
    """
    if not supabase: return None
    try:
        # Ambil data User
        u_res = supabase.table('users').select("*").eq('id', user_id).execute()
        if not u_res.data: return None
        user_data = u_res.data[0]
        
        # Ambil data Telegram Account terkait
        t_res = supabase.table('telegram_accounts').select("*").eq('user_id', user_id).execute()
        tele_data = t_res.data[0] if t_res.data else None
        
        # Wrapper Class untuk mempermudah akses di template
        class UserWrapper:
            def __init__(self, d, t):
                self.id = d['id']
                self.email = d['email']
                self.is_admin = d.get('is_admin', False)
                self.is_banned = d.get('is_banned', False)
                
                # Handle properti telegram_account secara aman
                self.telegram_account = None
                if t:
                    self.telegram_account = type('TeleObj', (object,), {
                        'phone_number': t.get('phone_number'),
                        'is_active': t.get('is_active', False),
                        'created_at': t.get('created_at')
                    })
        
        return UserWrapper(user_data, tele_data)
    except Exception as e:
        print(f"❌ Error fetching user data: {e}")
        return None

async def get_user_client(user_id):
    """
    Membuat Client Telethon aktif berdasarkan session string yang tersimpan di database.
    Hanya mengembalikan client jika session valid dan user terautentikasi.
    """
    if not supabase: return None
    try:
        # Hanya ambil akun yang statusnya active
        res = supabase.table('telegram_accounts').select("session_string").eq('user_id', user_id).eq('is_active', True).execute()
        if not res.data: return None
        
        session_str = res.data[0]['session_string']
        
        # Inisialisasi Client dengan Session String
        client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
        await client.connect()
        
        # Validasi apakah session masih valid (tidak logout dari HP)
        if not await client.is_user_authorized():
            print(f"⚠️ Session expired for user {user_id}")
            await client.disconnect()
            # Opsional: Update status jadi inactive di DB
            supabase.table('telegram_accounts').update({'is_active': False}).eq('user_id', user_id).execute()
            return None
            
        return client
    except Exception as e:
        print(f"❌ Error creating client for user {user_id}: {e}")
        return None

# --- DECORATORS & ERROR HANDLERS ---

@app.errorhandler(404)
def page_not_found(e):
    # Redirect cerdas: Login -> Dashboard, Belum Login -> Login/Home
    return redirect(url_for('dashboard')) if 'user_id' in session else redirect(url_for('login'))

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session: 
            return redirect(url_for('login'))
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

# --- AUTH ROUTES (WEB LOGIN/REGISTER) ---

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
                    # Cek Status Banned
                    if user.get('is_banned'):
                        flash('⛔ Akun Anda telah disuspend oleh Admin.', 'danger')
                        return redirect(url_for('login'))
                    
                    # Login Berhasil
                    session['user_id'] = user['id']
                    
                    # Redirect sesuai Role
                    if user.get('is_admin'):
                        return redirect(url_for('super_admin_dashboard'))
                    return redirect(url_for('dashboard'))
            
            flash('Email atau Password salah.', 'danger')
        except Exception as e:
            flash(f'System Error: {str(e)}', 'danger')
            
    return render_template('auth.html', mode='login')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        try:
            # Cek Email Duplikat
            exist = supabase.table('users').select("id").eq('email', email).execute()
            if exist.data:
                flash('Email sudah terdaftar. Silakan login.', 'warning')
                return redirect(url_for('register'))
            
            # Hash Password & Simpan
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
            flash(f'Gagal mendaftar: {str(e)}', 'danger')
            
    return render_template('auth.html', mode='register')

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    # Bersihkan state login jika ada
    return redirect(url_for('index'))

# --- USER DASHBOARD ---

@app.route('/dashboard')
@login_required
def dashboard():
    user = get_user_data(session['user_id'])
    
    # Validasi User (jika dihapus saat sesi aktif)
    if not user: 
        session.pop('user_id', None)
        return redirect(url_for('login'))
    
    # Jika kena banned saat sesi aktif
    if user.is_banned:
        session.pop('user_id', None)
        flash('⛔ Akun Anda telah disuspend.', 'danger')
        return redirect(url_for('login'))
    
    uid = user.id
    
    # Data Default
    logs = []
    schedules = []
    targets = []
    crm_count = 0

    # Fetch Data Dashboard dari Supabase
    if supabase:
        try:
            # 1. Logs (Limit 50 terbaru)
            logs = supabase.table('blast_logs').select("*").eq('user_id', uid).order('created_at', desc=True).limit(50).execute().data
            
            # 2. Schedules
            schedules = supabase.table('blast_schedules').select("*").eq('user_id', uid).execute().data
            
            # 3. Targets
            targets = supabase.table('blast_targets').select("*").eq('user_id', uid).execute().data
            
            # 4. User Count (CRM)
            # Menggunakan count='exact' head=True untuk performa
            crm_res = supabase.table('tele_users').select("id", count='exact', head=True).eq('owner_id', uid).execute()
            crm_count = crm_res.count if crm_res.count else 0
        except Exception as e:
            print(f"Dashboard Data Error: {e}")
            # Tidak crash, hanya data kosong
    
    return render_template('dashboard.html', user=user, logs=logs, schedules=schedules, targets=targets, user_count=crm_count)

# --- TELEGRAM AUTHENTICATION (PROFESSIONAL OTP) ---

@app.route('/api/connect/send_code', methods=['POST'])
@login_required
def send_code():
    phone = request.json.get('phone')
    user_id = session['user_id']
    
    # 1. Rate Limiting / Cooldown Check
    current_time = time.time()
    if user_id in login_states:
        last_req = login_states[user_id].get('last_otp_req', 0)
        # Cooldown 60 detik
        if current_time - last_req < 60:
            remaining = int(60 - (current_time - last_req))
            return jsonify({
                'status': 'cooldown', 
                'message': f'Tunggu {remaining} detik sebelum kirim ulang OTP.',
                'remaining': remaining
            })
    
    # 2. Proses Kirim OTP (Async Wrapper)
    async def _send():
        client = TelegramClient(StringSession(), API_ID, API_HASH)
        await client.connect()
        
        try:
            # Cek apakah nomor valid/terdaftar
            if not await client.is_user_authorized():
                req = await client.send_code_request(phone)
                
                # Simpan HASH ke Database (State Persistence)
                # Gunakan tabel telegram_accounts, status active=False
                data = {
                    'user_id': user_id, 
                    'phone_number': phone,
                    'session_string': req.phone_code_hash, # Hash OTP disimpan sementara di sini
                    'is_active': False,
                    'created_at': datetime.utcnow().isoformat()
                }
                # Upsert agar menimpa data lama jika ada
                supabase.table('telegram_accounts').upsert(data, on_conflict="user_id").execute()
                
                # Update Login State di Memory (Untuk rate limiting & client object sementara)
                # Note: Client object tidak bisa disimpan di DB, jadi simpan di RAM sementara
                # Jika server restart, user harus request OTP ulang.
                login_states[user_id] = {
                    'client': client, # Keep connection alive
                    'phone': phone,
                    'hash': req.phone_code_hash,
                    'last_otp_req': current_time
                }
                
                return jsonify({'status': 'success', 'message': 'Kode OTP telah dikirim ke Telegram Anda.'})
            else:
                return jsonify({'status': 'error', 'message': 'Nomor ini sudah login di sesi lain.'})
        except errors.FloodWaitError as e:
            return jsonify({'status': 'error', 'message': f'Terlalu banyak percobaan. Tunggu {e.seconds} detik.'})
        except errors.PhoneNumberInvalidError:
            return jsonify({'status': 'error', 'message': 'Nomor HP tidak valid. Gunakan format internasional (contoh: +62...)'})
        except Exception as e:
            return jsonify({'status': 'error', 'message': f'Telegram Error: {str(e)}'})
        
        # Jangan disconnect jika sukses, karena client dipakai verify

    try:
        return run_async(_send())
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Server Error: {str(e)}'})

@app.route('/api/connect/verify_code', methods=['POST'])
@login_required
def verify_code():
    user_id = session['user_id']
    otp = request.json.get('otp')
    pw = request.json.get('password')
    
    # Ambil State dari Memory (Prioritas) atau Database
    client = None
    phone_hash = None
    phone = None
    
    if user_id in login_states:
        client = login_states[user_id].get('client')
        phone = login_states[user_id].get('phone')
        phone_hash = login_states[user_id].get('hash')
    else:
        # Fallback ke Database jika memory hilang (misal restart)
        # Tapi client object harus bikin baru, which means butuh connect ulang
        # Ini tricky karena verify butuh client yang sama atau flow yang valid.
        # Jika client mati, biasanya harus req OTP ulang.
        return jsonify({
            'status': 'error', 
            'message': 'Sesi habis atau server restart. Silakan kirim ulang OTP.'
        })

    async def _verify():
        try:
            # Jika client terputus, coba connect lagi
            if not client.is_connected():
                await client.connect()
                
            try: 
                await client.sign_in(phone, otp, phone_code_hash=phone_hash)
            except errors.SessionPasswordNeededError: 
                if not pw: 
                    return jsonify({'status': '2fa', 'message': 'Akun dilindungi 2FA. Masukkan Password.'})
                await client.sign_in(password=pw)
            except errors.PhoneCodeInvalidError:
                return jsonify({'status': 'error', 'message': 'Kode OTP salah.'})
            except errors.PhoneCodeExpiredError:
                return jsonify({'status': 'error', 'message': 'Kode OTP kadaluarsa.'})
            
            # Login SUKSES -> Dapet Session String Asli
            real_session = client.session.save()
            
            # Update Database dengan Session String ASLI & Active True
            supabase.table('telegram_accounts').update({
                'session_string': real_session,
                'is_active': True,
                'created_at': datetime.utcnow().isoformat()
            }).eq('user_id', user_id).execute()
            
            # Bersihkan state
            await client.disconnect()
            if user_id in login_states:
                del login_states[user_id]
                
            return jsonify({'status': 'success', 'message': 'Berhasil terhubung! Halaman akan dimuat ulang.'})
            
        except Exception as e:
            return jsonify({'status': 'error', 'message': f'Verifikasi Gagal: {str(e)}'})

    try:
        return run_async(_verify())
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})

# --- TELETHON FEATURES (SCAN, IMPORT, BROADCAST) ---

@app.route('/scan_groups_api')
@login_required
def scan_groups_api():
    """Fitur Scan Grup & Forum"""
    user_id = session['user_id']
    
    async def _scan():
        client = await get_user_client(user_id)
        if not client: return jsonify({"status": "error", "message": "Telegram belum terkoneksi."})
        
        groups = []
        try:
            # Limit 300 dialog
            async for dialog in client.iter_dialogs(limit=300):
                if dialog.is_group:
                    is_forum = getattr(dialog.entity, 'forum', False)
                    # Ambil ID Asli (Utils Peer ID lebih aman)
                    real_id = utils.get_peer_id(dialog.entity)
                    
                    g_data = {
                        'id': real_id, 
                        'name': dialog.name, 
                        'is_forum': is_forum, 
                        'topics': []
                    }
                    
                    # Scan Topik (Jika Forum)
                    if is_forum:
                        try:
                            # Limit 15 topik terbaru biar gak timeout
                            topics = await client.get_forum_topics(dialog.entity, limit=15)
                            if topics and topics.topics:
                                for t in topics.topics:
                                    g_data['topics'].append({'id': t.id, 'title': t.title})
                        except: pass
                    
                    groups.append(g_data)
        except Exception as e:
            return jsonify({'status': 'error', 'message': f"Scan Error: {str(e)}"})
        finally:
            await client.disconnect()
            
        return jsonify({'status': 'success', 'data': groups})

    try:
        return run_async(_scan())
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})

@app.route('/save_bulk_targets', methods=['POST'])
@login_required
def save_bulk_targets():
    """Simpan target terpilih ke DB"""
    user_id = session['user_id']
    data = request.json
    selected = data.get('targets', [])
    
    try:
        count = 0
        for item in selected:
            # Convert list topic IDs ke string "1, 2, 3"
            t_ids = ",".join(map(str, item.get('topic_ids', [])))
            
            payload = {
                "user_id": user_id,
                "group_name": item['group_name'],
                "group_id": int(item['group_id']),
                "topic_ids": t_ids,
                "is_active": True,
                "created_at": datetime.utcnow().isoformat()
            }
            
            # Cek duplikat (Upsert logic manual)
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
    """Scan history chat personal untuk database CRM"""
    user_id = session['user_id']
    
    async def _import():
        client = await get_user_client(user_id)
        if not client: return jsonify({"status": "error", "message": "Tele disconnected"})
        
        count = 0
        try:
            # Scan 500 dialog terakhir
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
                    # Upsert (Ignore duplicate error)
                    try:
                        supabase.table('tele_users').upsert(data, on_conflict="owner_id, user_id").execute()
                        count += 1
                    except: pass
                    
            await client.disconnect()
            return jsonify({"status": "success", "message": f"Sukses! {count} kontak baru ditambahkan ke CRM."})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)})
            
    return run_async(_import())

@app.route('/start_broadcast', methods=['POST'])
@login_required
def start_broadcast():
    """Broadcast Pesan ke CRM"""
    user_id = session['user_id']
    message = request.form.get('message')
    
    if not message:
        return jsonify({"status": "error", "message": "Pesan tidak boleh kosong."})

    # Background Task Logic
    def _run_bg(uid, msg):
        async def _broadcast_logic():
            client = await get_user_client(uid)
            if not client: return
            
            try:
                # Ambil target broadcast
                crm_users = supabase.table('tele_users').select("*").eq('owner_id', uid).execute().data
                
                for u in crm_users:
                    try:
                        # Personalisasi pesan
                        final_msg = msg.replace("{name}", u.get('first_name') or "Kak")
                        await client.send_message(int(u['user_id']), final_msg)
                        # Jeda anti-flood
                        await asyncio.sleep(2) 
                    except Exception as e:
                        print(f"Fail Broadcast {u['user_id']}: {e}")
            except Exception as e:
                print(f"Broadcast Error: {e}")
            finally:
                await client.disconnect()

        # Execute in separate event loop & thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_broadcast_logic())
        loop.close()

    # Fire and Forget Thread
    threading.Thread(target=_run_bg, args=(user_id, message)).start()
    
    return jsonify({"status": "success", "message": "Broadcast sedang berjalan di latar belakang!"})

# --- CRUD JADWAL & HAPUS TARGET ---

@app.route('/add_schedule', methods=['POST'])
@login_required
def add_schedule():
    h = request.form.get('hour')
    m = request.form.get('minute')
    if h and m:
        supabase.table('blast_schedules').insert({
            "user_id": session['user_id'], 
            "run_hour": int(h), 
            "run_minute": int(m), 
            "is_active": True,
            "created_at": datetime.utcnow().isoformat()
        }).execute()
        flash('Jadwal berhasil ditambahkan.', 'success')
    else:
        flash('Jam/Menit tidak valid.', 'danger')
    return redirect(url_for('dashboard'))

@app.route('/delete_schedule/<int:id>')
@login_required
def delete_schedule(id):
    supabase.table('blast_schedules').delete().eq('id', id).eq('user_id', session['user_id']).execute()
    flash('Jadwal dihapus.', 'success')
    return redirect(url_for('dashboard'))

@app.route('/delete_target/<int:id>')
@login_required
def delete_target(id):
    supabase.table('blast_targets').delete().eq('id', id).eq('user_id', session['user_id']).execute()
    flash('Target dihapus.', 'success')
    return redirect(url_for('dashboard'))

# --- SUPER ADMIN PANEL ---

@app.route('/super-admin')
@admin_required
def super_admin_dashboard():
    try:
        # Fetch Users
        users = supabase.table('users').select("*").order('created_at', desc=True).execute().data
        
        final_list = []
        stats = {'total_users': len(users), 'active_bots': 0, 'banned_users': 0}
        
        for u in users:
            # Cek status ban
            if u.get('is_banned'): stats['banned_users'] += 1
            
            # Cek Telegram
            tele = supabase.table('telegram_accounts').select("*").eq('user_id', u['id']).execute().data
            if tele and tele[0].get('is_active'): stats['active_bots'] += 1
            
            # Wrapper Class
            class UserW:
                def __init__(self, d, t):
                    self.id = d['id']
                    self.email = d['email']
                    self.is_admin = d.get('is_admin')
                    self.is_banned = d.get('is_banned')
                    
                    # Parse Date
                    raw_date = d.get('created_at')
                    self.created_at = datetime.fromisoformat(raw_date.replace('Z', '+00:00')) if raw_date else datetime.now()
                    
                    self.telegram_account = type('o',(object,),t[0]) if t else None
            
            final_list.append(UserW(u, tele))
            
        return render_template('super_admin.html', users=final_list, stats=stats)
    except Exception as e:
        return f"Error Admin Panel: {e}"

@app.route('/super-admin/ban/<int:user_id>', methods=['POST'])
@admin_required
def ban_user(user_id):
    try:
        # Get current status
        u_data = supabase.table('users').select("is_banned").eq('id', user_id).execute().data
        if not u_data: return redirect(url_for('super_admin_dashboard'))
        
        current_status = u_data[0].get('is_banned', False)
        new_status = not current_status
        
        # Update User
        supabase.table('users').update({'is_banned': new_status}).eq('id', user_id).execute()
        
        # Jika Banned -> Matikan Bot
        if new_status:
            supabase.table('telegram_accounts').update({'is_active': False}).eq('user_id', user_id).execute()
            
        flash(f"Status User #{user_id} berhasil diubah.", 'success')
    except Exception as e:
        flash(f"Gagal update status: {e}", 'danger')
        
    return redirect(url_for('super_admin_dashboard'))

# --- PING (KEEP ALIVE) ---
@app.route('/ping')
def ping():
    return jsonify({"status": "alive", "server_time": datetime.utcnow().isoformat()}), 200

# --- INITIALIZATION ---
def init_check():
    # Auto Create Super Admin jika belum ada
    adm_email = os.getenv('SUPER_ADMIN', 'admin@baba.com')
    adm_pass = os.getenv('PASS_ADMIN', 'admin123')
    
    if supabase:
        try:
            res = supabase.table('users').select("id").eq('email', adm_email).execute()
            if not res.data:
                hashed = generate_password_hash(adm_pass)
                data = {
                    'email': adm_email, 
                    'password': hashed, 
                    'is_admin': True,
                    'created_at': datetime.utcnow().isoformat()
                }
                supabase.table('users').insert(data).execute()
                print(f"👑 Super Admin Created: {adm_email}")
        except Exception as e:
            print(f"⚠️ Init Admin Warning: {e}")

if __name__ == '__main__':
    init_check()
    # Debug=True hanya untuk local dev, Render pakai Gunicorn
    app.run(debug=True, port=5000, use_reloader=False)
