import os
import asyncio
import logging
import threading
import json
from functools import wraps 
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
from werkzeug.security import generate_password_hash, check_password_hash
from telethon import TelegramClient, errors, functions
from telethon.sessions import StringSession
from supabase import create_client, Client

# --- CONFIGURATION ---
app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'rahasia_negara_baba_parfume_saas')

# --- SUPABASE CONNECTION ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("❌ SUPABASE_URL dan SUPABASE_KEY wajib diset di Environment!")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- GLOBAL VARS ---
API_ID = int(os.getenv('API_ID', '0')) 
API_HASH = os.getenv('API_HASH', '')
login_states = {} 

# --- HELPER FUNCTIONS ---

def get_user_data(user_id):
    """Ambil data user + telegram account secara aman"""
    try:
        u_res = supabase.table('users').select("*").eq('id', user_id).execute()
        if not u_res.data: return None
        user = u_res.data[0]
        
        t_res = supabase.table('telegram_accounts').select("*").eq('user_id', user_id).execute()
        tele = t_res.data[0] if t_res.data else None
        
        # Bungkus jadi object biar template HTML ga error akses properti
        class Wrapper:
            def __init__(self, d, t):
                self.id = d['id']
                self.email = d['email']
                self.is_admin = d.get('is_admin', False)
                self.is_banned = d.get('is_banned', False)
                self.telegram_account = type('obj', (object,), t) if t else None
        
        return Wrapper(user, tele)
    except Exception as e:
        print(f"Err UserData: {e}")
        return None

async def get_user_client(user_id):
    """Bikin Client Telethon on-the-fly pakai session user"""
    try:
        res = supabase.table('telegram_accounts').select("session_string").eq('user_id', user_id).execute()
        if not res.data: return None
        
        session_str = res.data[0]['session_string']
        client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            return None
        return client
    except:
        return None

# --- DECORATORS & ERROR HANDLERS ---

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
            flash('Akses ditolak.', 'danger')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function

# --- AUTH ROUTES ---

@app.route('/')
def index(): return render_template('landing.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        try:
            res = supabase.table('users').select("*").eq('email', email).execute()
            if res.data:
                user = res.data[0]
                if check_password_hash(user['password'], password):
                    if user.get('is_banned'):
                        flash('Akun disuspend.', 'danger')
                        return redirect(url_for('login'))
                    session['user_id'] = user['id']
                    return redirect(url_for('super_admin_dashboard' if user.get('is_admin') else 'dashboard'))
            flash('Email/Password salah.', 'danger')
        except Exception as e:
            flash(f'Error System: {e}', 'danger')
    return render_template('auth.html', mode='login')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        try:
            # Cek exist
            exist = supabase.table('users').select("id").eq('email', email).execute()
            if exist.data:
                flash('Email sudah terdaftar.', 'warning')
                return redirect(url_for('register'))
            
            hashed = generate_password_hash(password)
            supabase.table('users').insert({'email': email, 'password': hashed}).execute()
            flash('Daftar berhasil, silakan login.', 'success')
            return redirect(url_for('login'))
        except Exception as e:
            flash(f'Gagal daftar: {e}', 'danger')
    return render_template('auth.html', mode='register')

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('index'))

# --- DASHBOARD & FEATURES ROUTES ---

@app.route('/dashboard')
@login_required
def dashboard():
    user = get_user_data(session['user_id'])
    if not user: 
        session.pop('user_id', None)
        return redirect(url_for('login'))
    
    # Ambil Data Dashboard User dari Supabase
    uid = user.id
    
    # 1. Logs
    logs = supabase.table('blast_logs').select("*").eq('user_id', uid).order('created_at', desc=True).limit(50).execute().data
    
    # 2. Schedules
    schedules = supabase.table('blast_schedules').select("*").eq('user_id', uid).execute().data
    
    # 3. Targets
    targets = supabase.table('blast_targets').select("*").eq('user_id', uid).execute().data
    
    # 4. User Count (CRM)
    crm_count = supabase.table('tele_users').select("id", count='exact').eq('owner_id', uid).execute().count
    
    return render_template('dashboard.html', user=user, logs=logs, schedules=schedules, targets=targets, user_count=crm_count or 0)

# --- TELETHON FEATURES (API ENDPOINTS) ---

@app.route('/scan_groups_api')
@login_required
async def scan_groups_api():
    """Scan Grup user yang sedang login"""
    user_id = session['user_id']
    client = await get_user_client(user_id)
    
    if not client:
        return jsonify({"status": "error", "message": "Telegram belum terkoneksi/expired."})
    
    try:
        groups_data = []
        async for dialog in client.iter_dialogs(limit=300):
            if dialog.is_group:
                entity = dialog.entity
                is_forum = getattr(entity, 'forum', False)
                # Ambil ID Asli
                real_id = dialog.id 
                
                g_data = {'id': real_id, 'name': dialog.name, 'is_forum': is_forum, 'topics': []}
                
                if is_forum:
                    try:
                        topics = await client.get_forum_topics(entity, limit=20)
                        if topics and topics.topics:
                            for t in topics.topics:
                                g_data['topics'].append({'id': t.id, 'title': t.title})
                    except: pass
                
                groups_data.append(g_data)
        
        await client.disconnect()
        return jsonify({"status": "success", "data": groups_data})
        
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/save_bulk_targets', methods=['POST'])
@login_required
def save_bulk_targets():
    """Simpan target blast ke Supabase"""
    user_id = session['user_id']
    data = request.json
    selected = data.get('targets', [])
    
    try:
        for item in selected:
            raw_topics = item.get('topic_ids', [])
            topics_str = ", ".join(map(str, raw_topics)) if isinstance(raw_topics, list) else ""
            
            payload = {
                "user_id": user_id,
                "group_name": item['group_name'],
                "group_id": int(item['group_id']),
                "topic_ids": topics_str,
                "is_active": True
            }
            # Cek duplikat target untuk user ini
            exist = supabase.table('blast_targets').select('id').eq('user_id', user_id).eq('group_id', item['group_id']).execute()
            
            if exist.data:
                supabase.table('blast_targets').update(payload).eq('id', exist.data[0]['id']).execute()
            else:
                supabase.table('blast_targets').insert(payload).execute()
                
        return jsonify({"status": "success", "message": f"{len(selected)} target disimpan!"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/import_crm_api', methods=['POST'])
@login_required
async def import_crm_api():
    """Import chat history ke tabel tele_users"""
    user_id = session['user_id']
    client = await get_user_client(user_id)
    if not client: return jsonify({"status": "error", "message": "Tele disconnected"})
    
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
                    "last_interaction": datetime.utcnow().isoformat()
                }
                # Upsert manual (hapus dulu kalau ada biar update, atau ignore)
                # Supabase upsert butuh unique constraint yang pas
                try:
                    supabase.table('tele_users').upsert(data, on_conflict="owner_id, user_id").execute()
                    count += 1
                except: pass
                
        await client.disconnect()
        return jsonify({"status": "success", "message": f"Berhasil import {count} user."})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/start_broadcast', methods=['POST'])
@login_required
async def start_broadcast():
    """Kirim Broadcast (Sederhana via Request, idealnya via Worker)"""
    user_id = session['user_id']
    message = request.form.get('message')
    
    client = await get_user_client(user_id)
    if not client: return jsonify({"status": "error", "message": "Tele disconnected"})
    
    # Ambil user CRM milik owner ini
    crm_users = supabase.table('tele_users').select("*").eq('owner_id', user_id).execute().data
    
    # Run async background task (fire and forget simulation)
    # Note: Di production Render free tier, process ini bisa kepotong kalau lama.
    # Idealnya pakai Celery/Redis queue. Ini versi simple.
    asyncio.create_task(background_broadcast(client, crm_users, message))
    
    return jsonify({"status": "success", "message": "Broadcast berjalan di background!"})

async def background_broadcast(client, users, text):
    try:
        for u in users:
            try:
                final_msg = text.replace("{name}", u.get('first_name') or "Kak")
                await client.send_message(int(u['user_id']), final_msg)
                await asyncio.sleep(2)
            except: pass
    except: pass
    finally:
        await client.disconnect()

# --- CRUD JADWAL & TARGET ---

@app.route('/add_schedule', methods=['POST'])
@login_required
def add_schedule():
    user_id = session['user_id']
    h = request.form.get('hour')
    m = request.form.get('minute')
    supabase.table('blast_schedules').insert({
        "user_id": user_id, "run_hour": int(h), "run_minute": int(m)
    }).execute()
    return redirect(url_for('dashboard'))

@app.route('/delete_schedule/<int:id>')
@login_required
def delete_schedule(id):
    # Pastikan hapus punya sendiri
    user_id = session['user_id']
    supabase.table('blast_schedules').delete().eq('id', id).eq('user_id', user_id).execute()
    return redirect(url_for('dashboard'))

@app.route('/delete_target/<int:id>')
@login_required
def delete_target(id):
    user_id = session['user_id']
    supabase.table('blast_targets').delete().eq('id', id).eq('user_id', user_id).execute()
    return redirect(url_for('dashboard'))

# --- SUPER ADMIN ---

@app.route('/super-admin')
@admin_required
def super_admin_dashboard():
    # Ambil semua user untuk admin
    users = supabase.table('users').select("*").order('created_at', desc=True).execute().data
    
    # Data enrichment manual
    final_users = []
    for u in users:
        # Cek telegram
        tele = supabase.table('telegram_accounts').select("*").eq('user_id', u['id']).execute().data
        
        class UserWrapper:
            def __init__(self, d, t):
                self.id = d['id']
                self.email = d['email']
                self.is_admin = d.get('is_admin')
                self.is_banned = d.get('is_banned')
                self.created_at = datetime.fromisoformat(d['created_at'].replace('Z', '+00:00')) if d.get('created_at') else datetime.now()
                self.telegram_account = type('obj', (object,), t[0]) if t else None
                
        final_users.append(UserWrapper(u, tele))
        
    stats = {
        'total_users': len(users),
        'active_bots': 0, # Hitung manual jika perlu
        'banned_users': sum(1 for x in users if x.get('is_banned'))
    }
    return render_template('super_admin.html', users=final_users, stats=stats)

@app.route('/super-admin/ban/<int:user_id>', methods=['POST'])
@admin_required
def ban_user(user_id):
    # Logic ban user via API
    try:
        user = supabase.table('users').select("is_banned").eq('id', user_id).execute().data[0]
        new_status = not user['is_banned']
        supabase.table('users').update({'is_banned': new_status}).eq('id', user_id).execute()
        
        if new_status: # Matikan bot
            supabase.table('telegram_accounts').update({'is_active': False}).eq('user_id', user_id).execute()
            
        flash(f"Status user {user_id} diupdate.", 'success')
    except Exception as e:
        flash(f"Error: {e}", 'danger')
    return redirect(url_for('super_admin_dashboard'))

# --- AUTH TELEGRAM ---
@app.route('/api/connect/send_code', methods=['POST'])
@login_required
async def send_code():
    phone = request.json.get('phone')
    user_id = session['user_id']
    try:
        client = TelegramClient(StringSession(), API_ID, API_HASH)
        await client.connect()
        if not await client.is_user_authorized():
            req = await client.send_code_request(phone)
            login_states[user_id] = {'client': client, 'phone': phone, 'hash': req.phone_code_hash}
            return jsonify({'status': 'success', 'message': 'OTP Sent!'})
        return jsonify({'status': 'error', 'message': 'Logged in.'})
    except Exception as e: return jsonify({'status': 'error', 'message': str(e)})

@app.route('/api/connect/verify_code', methods=['POST'])
@login_required
async def verify_code():
    user_id = session['user_id']
    state = login_states.get(user_id)
    if not state: return jsonify({'status': 'error', 'message': 'Expired'})
    
    otp = request.json.get('otp')
    pw = request.json.get('password')
    client = state['client']
    
    try:
        try: await client.sign_in(state['phone'], otp, phone_code_hash=state['hash'])
        except errors.SessionPasswordNeededError: await client.sign_in(password=pw)
        
        sess = client.session.save()
        # Upsert Tele Account
        data = {'user_id': user_id, 'phone_number': state['phone'], 'session_string': sess, 'is_active': True}
        supabase.table('telegram_accounts').upsert(data, on_conflict="user_id").execute()
        
        await client.disconnect()
        return jsonify({'status': 'success'})
    except Exception as e: return jsonify({'status': 'error', 'message': str(e)})

# --- STARTUP CHECK ---
def init_check():
    # Cek Admin
    adm = os.getenv('SUPER_ADMIN', 'admin@baba.com')
    pwd = os.getenv('PASS_ADMIN', 'admin123')
    try:
        res = supabase.table('users').select("*").eq('email', adm).execute()
        if not res.data:
            supabase.table('users').insert({
                'email': adm, 'password': generate_password_hash(pwd), 'is_admin': True
            }).execute()
            print(f"👑 Admin Created: {adm}")
    except: pass

# --- KEEP ALIVE ROUTE ---
@app.route('/ping')
def ping():
    """Endpoint ringan buat dipancing UptimeRobot biar server gak tidur"""
    return jsonify({"status": "alive", "time": datetime.utcnow().isoformat()}), 200

if __name__ == '__main__':
    init_check()
    app.run(debug=True, port=5000, use_reloader=False)
