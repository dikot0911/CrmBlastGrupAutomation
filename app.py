import os
import asyncio
import logging
from functools import wraps 
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
from werkzeug.security import generate_password_hash, check_password_hash
from telethon import TelegramClient, errors
from telethon.sessions import StringSession
from supabase import create_client, Client

# --- CONFIGURATION ---
app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'rahasia_negara_baba_parfume_saas')

# --- SUPABASE CONNECTION (WAJIB ADA DI RENDER ENV) ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("\n" + "="*50)
    print("❌ CRITICAL ERROR: SUPABASE_URL atau SUPABASE_KEY belum diset!")
    print("👉 Aplikasi tidak bisa jalan tanpa API Key ini.")
    print("="*50 + "\n")
    # Kita biarkan lanjut tapi nanti bakal error kalau dipake
    supabase = None
else:
    try:
        supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
        print("✅ TERHUBUNG KE SUPABASE VIA API")
    except Exception as e:
        print(f"❌ Gagal inisialisasi Supabase: {e}")
        supabase = None

# --- GLOBAL VARS ---
API_ID = int(os.getenv('API_ID', '0')) 
API_HASH = os.getenv('API_HASH', '')
login_states = {} 

# --- HELPER FUNCTIONS (PENGGANTI DATABASE LOKAL) ---

def get_user_by_email(email):
    """Ambil data user dari Supabase berdasarkan email"""
    try:
        response = supabase.table('users').select("*").eq('email', email).execute()
        if response.data and len(response.data) > 0:
            return response.data[0]
        return None
    except Exception as e:
        print(f"Err Get User: {e}")
        return None

def get_user_by_id(user_id):
    """Ambil data user dari Supabase berdasarkan ID"""
    try:
        response = supabase.table('users').select("*").eq('id', user_id).execute()
        if response.data and len(response.data) > 0:
            return response.data[0]
        return None
    except Exception as e:
        print(f"Err Get ID: {e}")
        return None

def get_telegram_account(user_id):
    """Ambil data telegram account milik user"""
    try:
        response = supabase.table('telegram_accounts').select("*").eq('user_id', user_id).execute()
        if response.data and len(response.data) > 0:
            return response.data[0]
        return None
    except:
        return None

# --- ERROR HANDLER ---
@app.errorhandler(404)
def page_not_found(e):
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('index'))

# --- ADMIN DECORATOR ---
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session: 
            return redirect(url_for('login'))
        
        user = get_user_by_id(session['user_id'])
        if not user or not user.get('is_admin'):
            flash('⚠️ Akses Ditolak! Halaman ini area terlarang.', 'danger')
            return redirect(url_for('dashboard'))
        
        return f(*args, **kwargs)
    return decorated_function

# --- ROUTES ---

@app.route('/')
def index():
    return render_template('landing.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        if not supabase:
            flash("Koneksi Database Putus. Cek Server.", 'danger')
            return render_template('auth.html', mode='login')

        user = get_user_by_email(email)
        
        if user and check_password_hash(user['password'], password):
            if user.get('is_banned'):
                flash('⛔ Akun Anda telah disuspend oleh Admin.', 'danger')
                return redirect(url_for('login'))
                
            session['user_id'] = user['id']
            
            if user.get('is_admin'):
                return redirect(url_for('super_admin_dashboard'))
            return redirect(url_for('dashboard'))
            
        flash('Email atau password salah!', 'danger')
            
    return render_template('auth.html', mode='login')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        if not supabase:
            flash("Server Error: No DB Connection", 'danger')
            return redirect(url_for('register'))

        # Cek email duplikat
        existing = get_user_by_email(email)
        if existing:
            flash('Email sudah terdaftar!', 'warning')
            return redirect(url_for('register'))
            
        # Simpan user baru via API
        try:
            hashed_pw = generate_password_hash(password, method='sha256')
            data = {
                'email': email, 
                'password': hashed_pw,
                'created_at': datetime.utcnow().isoformat()
            }
            supabase.table('users').insert(data).execute()
            
            flash('Registrasi berhasil! Silakan login.', 'success')
            return redirect(url_for('login'))
        except Exception as e:
            print(f"Register API Error: {e}")
            flash('Gagal mendaftar. Terjadi kesalahan sistem.', 'danger')
            
    return render_template('auth.html', mode='register')

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session: return redirect(url_for('login'))
    
    user = get_user_by_id(session['user_id'])
    if not user:
        session.pop('user_id', None)
        return redirect(url_for('login'))
        
    if user.get('is_banned'): 
        session.pop('user_id', None)
        flash('⛔ Akun Anda dibekukan.', 'danger')
        return redirect(url_for('login'))
    
    # Inject data telegram account ke object user biar template dashboard.html gak error
    # Di template biasanya akses user.telegram_account.phone_number
    # Kita akali dengan Dictionary Access di template atau modif object di sini
    
    # Ambil data telegram terpisah
    tele_account = get_telegram_account(user['id'])
    
    # Kita bungkus user dictionary ke object sederhana biar kompatibel sama template lama
    # Atau kita kirim variable terpisah ke template
    # NOTE: Pastikan di dashboard.html aksesnya kompatibel. 
    # Kalau dashboard.html pake user.email (dot notation), code di bawah ini penting:
    
    class UserObj:
        def __init__(self, data, tele):
            self.id = data['id']
            self.email = data['email']
            self.is_admin = data.get('is_admin')
            self.telegram_account = None
            if tele:
                self.telegram_account = type('obj', (object,), {
                    'phone_number': tele['phone_number'],
                    'is_active': tele['is_active']
                })
    
    user_obj = UserObj(user, tele_account)
    return render_template('dashboard.html', user=user_obj)

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('index'))

# --- SUPER ADMIN ROUTES ---

@app.route('/super-admin')
@admin_required
def super_admin_dashboard():
    try:
        # Ambil semua users
        resp_users = supabase.table('users').select("*").order('created_at', desc=True).execute()
        users_list = resp_users.data
        
        # Hitung statistik manual karena API count terbatas
        total_users = len(users_list)
        
        # Hitung active bot
        resp_bots = supabase.table('telegram_accounts').select("id", count='exact').eq('is_active', True).execute()
        active_bots = resp_bots.count if resp_bots.count else 0
        
        banned_users = sum(1 for u in users_list if u.get('is_banned'))
        
        stats = {
            'total_users': total_users,
            'active_bots': active_bots,
            'banned_users': banned_users
        }
        
        # Perbaiki struktur users list agar punya properti 'telegram_account' untuk template
        # Ini agak berat kalau user ribuan, tapi untuk awal ok
        final_users = []
        for u in users_list:
            tele = get_telegram_account(u['id'])
            # Bikin dummy object biar template user.email bisa jalan
            class UserWrapper:
                def __init__(self, d, t):
                    self.id = d['id']
                    self.email = d['email']
                    self.is_admin = d.get('is_admin')
                    self.is_banned = d.get('is_banned')
                    self.created_at = datetime.fromisoformat(d['created_at'].replace('Z', '+00:00')) if d.get('created_at') else datetime.now()
                    self.telegram_account = None
                    if t:
                        self.telegram_account = type('obj', (object,), {
                            'phone_number': t['phone_number'],
                            'is_active': t['is_active']
                        })
            final_users.append(UserWrapper(u, tele))

        return render_template('super_admin.html', users=final_users, stats=stats)
    except Exception as e:
        print(f"❌ ADMIN DASHBOARD ERROR: {e}")
        return "Database Error di Admin Panel (API)"

@app.route('/super-admin/ban/<int:user_id>', methods=['POST'])
@admin_required
def ban_user(user_id):
    try:
        target_user = get_user_by_id(user_id)
        if target_user:
            if target_user.get('is_admin'):
                flash('Tidak bisa ban sesama Admin!', 'warning')
            else:
                new_status = not target_user.get('is_banned', False)
                
                # Update User
                supabase.table('users').update({'is_banned': new_status}).eq('id', user_id).execute()
                
                # Update Bot Active Status
                if new_status: # Jika di ban, matikan bot
                    supabase.table('telegram_accounts').update({'is_active': False}).eq('user_id', user_id).execute()
                
                status_text = "Banned" if new_status else "Active"
                flash(f'User {target_user["email"]} status changed to {status_text}.', 'success')
    except Exception as e:
        print(f"❌ BAN ACTION ERROR: {e}")
        flash(f"Gagal update status: {e}", 'danger')
        
    return redirect(url_for('super_admin_dashboard'))

# --- TELETHON AUTH FLOW (API VERSION) ---

@app.route('/api/connect/send_code', methods=['POST'])
async def send_code():
    if 'user_id' not in session: return jsonify({'status': 'error', 'message': 'Unauthorized'}), 401
    
    user = get_user_by_id(session['user_id'])
    if user.get('is_banned'): return jsonify({'status': 'error', 'message': 'Akun disuspend.'}), 403

    phone = request.json.get('phone')
    user_id = session['user_id']
    
    if API_ID == 0 or not API_HASH:
        return jsonify({'status': 'error', 'message': 'Server Config Error: API_ID/HASH not set.'})

    try:
        client = TelegramClient(StringSession(), API_ID, API_HASH)
        await client.connect()
        
        if not await client.is_user_authorized():
            phone_code_hash = await client.send_code_request(phone)
            login_states[user_id] = {
                'client': client,
                'phone': phone,
                'phone_code_hash': phone_code_hash.phone_code_hash
            }
            return jsonify({'status': 'success', 'message': 'OTP terkirim ke Telegram/SMS anda.'})
        else:
            return jsonify({'status': 'error', 'message': 'Nomor ini sudah login.'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': f"Telegram Error: {str(e)}"})

@app.route('/api/connect/verify_code', methods=['POST'])
async def verify_code():
    if 'user_id' not in session: return jsonify({'status': 'error', 'message': 'Unauthorized'}), 401
    
    user_id = session['user_id']
    otp = request.json.get('otp')
    password = request.json.get('password')
    state = login_states.get(user_id)
    
    if not state: return jsonify({'status': 'error', 'message': 'Session expired.'})
    
    client = state['client']
    phone = state['phone']
    hash_code = state['phone_code_hash']
    
    try:
        try:
            await client.sign_in(phone, otp, phone_code_hash=hash_code)
        except errors.SessionPasswordNeededError:
            if not password:
                return jsonify({'status': '2fa_required', 'message': 'Password 2FA diperlukan.'})
            await client.sign_in(password=password)
            
        string_sess = client.session.save()
        
        # Simpan Session String ke Supabase via API
        # Cek ada akun lama gak
        existing = get_telegram_account(user_id)
        
        data = {
            'user_id': user_id,
            'phone_number': phone,
            'session_string': string_sess,
            'is_active': True,
            'created_at': datetime.utcnow().isoformat()
        }
        
        if existing:
            supabase.table('telegram_accounts').update(data).eq('user_id', user_id).execute()
        else:
            supabase.table('telegram_accounts').insert(data).execute()
        
        await client.disconnect()
        del login_states[user_id]
        return jsonify({'status': 'success', 'message': 'Telegram Berhasil Terhubung!'})
        
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})

# --- HELPER: AUTO CREATE ADMIN ---
# Dijalankan saat start, cek via API
def init_admin_check():
    if not supabase: return
    
    super_email = os.getenv('SUPER_ADMIN', 'admin@baba.com')
    super_pass = os.getenv('PASS_ADMIN', 'admin123')
    
    try:
        exist = get_user_by_email(super_email)
        if not exist:
            hashed = generate_password_hash(super_pass, method='sha256')
            data = {
                'email': super_email,
                'password': hashed,
                'is_admin': True,
                'created_at': datetime.utcnow().isoformat()
            }
            supabase.table('users').insert(data).execute()
            print(f"👑 Super Admin Created via API: {super_email}")
        else:
            print(f"✅ Admin exists: {super_email}")
    except Exception as e:
        print(f"⚠️ Init Admin Error: {e}")

if __name__ == '__main__':
    # Init admin
    init_admin_check()
    app.run(debug=True, port=5000, use_reloader=False)
