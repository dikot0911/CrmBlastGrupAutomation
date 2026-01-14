import os
import asyncio
import threading
import logging
from functools import wraps 
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from telethon import TelegramClient, errors
from telethon.sessions import StringSession
import psycopg2 
from supabase import create_client, Client

# --- CONFIGURATION ---
app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'rahasia_negara_baba_parfume_saas')

# --- DATABASE CONFIGURATION ---
# Prioritaskan Environment Variable DATABASE_URL
database_url = os.getenv('DATABASE_URL')

if not database_url:
    # Fallback hanya untuk local development jika tidak ada env var
    print("⚠️ WARNING: DATABASE_URL not found, using local SQLite.")
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///saas_database.db'
else:
    # Fix postgres prefix untuk SQLAlchemy
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# --- SUPABASE CLIENT CONFIGURATION ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = None

if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        print("✅ Supabase Client Connected via API")
    except Exception as e:
        print(f"⚠️ Gagal connect Supabase API: {e}")

# --- GLOBAL VARS ---
API_ID = int(os.getenv('API_ID', '0')) 
API_HASH = os.getenv('API_HASH', '')
login_states = {} 

# --- DATABASE MODELS ---
# Pastikan nama tabel di SQL sama dengan nama model (SQLAlchemy defaultnya lowercase nama class)
class User(db.Model):
    __tablename__ = 'users' # Explicit table name agar match dengan schema.sql
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(150), nullable=False)
    is_admin = db.Column(db.Boolean, default=False) 
    is_banned = db.Column(db.Boolean, default=False) 
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    telegram_account = db.relationship('TelegramAccount', backref='user', uselist=False)

class TelegramAccount(db.Model):
    __tablename__ = 'telegram_accounts' # Explicit table name
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), unique=True)
    phone_number = db.Column(db.String(20))
    session_string = db.Column(db.Text)
    is_active = db.Column(db.Boolean, default=True)
    targets = db.Column(db.Text, default="[]") 

# --- ERROR HANDLER (REDIRECT 404 TO HOME) ---
@app.errorhandler(404)
def page_not_found(e):
    # Jika user salah ketik url, lempar ke dashboard kalau login, atau home kalau belum
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('index'))

# --- ADMIN DECORATOR ---
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session: 
            return redirect(url_for('login'))
        
        user = User.query.get(session['user_id'])
        if not user or not user.is_admin:
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
        
        try:
            user = User.query.filter_by(email=email).first()
            
            if user and check_password_hash(user.password, password):
                if user.is_banned:
                    flash('⛔ Akun Anda telah disuspend oleh Admin.', 'danger')
                    return redirect(url_for('login'))
                    
                session['user_id'] = user.id
                if user.is_admin:
                    return redirect(url_for('super_admin_dashboard'))
                return redirect(url_for('dashboard'))
                
            flash('Email atau password salah!', 'danger')
        except Exception as e:
            # Catch error database biar gak crash 500
            print(f"Login Error: {e}")
            flash('Terjadi kesalahan sistem. Coba lagi nanti.', 'danger')
            
    return render_template('auth.html', mode='login')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        try:
            if User.query.filter_by(email=email).first():
                flash('Email sudah terdaftar!', 'warning')
                return redirect(url_for('register'))
                
            new_user = User(email=email, password=generate_password_hash(password, method='sha256'))
            db.session.add(new_user)
            db.session.commit()
            flash('Registrasi berhasil! Silakan login.', 'success')
            return redirect(url_for('login'))
        except Exception as e:
            print(f"Register Error: {e}")
            flash('Gagal mendaftar. Coba lagi.', 'danger')
            
    return render_template('auth.html', mode='register')

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session: return redirect(url_for('login'))
    user = User.query.get(session['user_id'])
    if not user:
        session.pop('user_id', None)
        return redirect(url_for('login'))
    if user.is_banned: 
        session.pop('user_id', None)
        flash('⛔ Akun Anda dibekukan.', 'danger')
        return redirect(url_for('login'))
    return render_template('dashboard.html', user=user)

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('index'))

# --- SUPER ADMIN ROUTES ---

@app.route('/super-admin')
@admin_required
def super_admin_dashboard():
    users = User.query.order_by(User.created_at.desc()).all()
    stats = {
        'total_users': User.query.count(),
        'active_bots': TelegramAccount.query.filter_by(is_active=True).count(),
        'banned_users': User.query.filter_by(is_banned=True).count()
    }
    return render_template('super_admin.html', users=users, stats=stats)

@app.route('/super-admin/ban/<int:user_id>', methods=['POST'])
@admin_required
def ban_user(user_id):
    user = User.query.get(user_id)
    if user:
        if user.is_admin:
            flash('Tidak bisa ban sesama Admin!', 'warning')
        else:
            user.is_banned = not user.is_banned
            if user.is_banned and user.telegram_account:
                user.telegram_account.is_active = False
            db.session.commit()
            status = "Banned" if user.is_banned else "Active"
            flash(f'User {user.email} status changed to {status}.', 'success')
    return redirect(url_for('super_admin_dashboard'))

# --- TELETHON AUTH FLOW ---

@app.route('/api/connect/send_code', methods=['POST'])
async def send_code():
    if 'user_id' not in session: return jsonify({'status': 'error', 'message': 'Unauthorized'}), 401
    user = User.query.get(session['user_id'])
    if user.is_banned: return jsonify({'status': 'error', 'message': 'Akun disuspend.'}), 403

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
        
        user = User.query.get(user_id)
        if user.telegram_account:
            user.telegram_account.session_string = string_sess
            user.telegram_account.phone_number = phone
            user.telegram_account.is_active = True
        else:
            new_tele = TelegramAccount(user_id=user_id, phone_number=phone, session_string=string_sess)
            db.session.add(new_tele)
        
        db.session.commit()
        await client.disconnect()
        del login_states[user_id]
        return jsonify({'status': 'success', 'message': 'Telegram Berhasil Terhubung!'})
        
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})

# --- HELPER: INITIALIZE DB ---
def init_db():
    with app.app_context():
        # Coba create table kalau belum ada (Safe operation)
        try:
            db.create_all()
            print("✅ Database Tables Ready")
        except Exception as e:
            print(f"⚠️ DB Warning (Mungkin sudah ada): {e}")
        
        # Auto Admin Creation
        super_email = os.getenv('SUPER_ADMIN', 'admin@baba.com')
        super_pass = os.getenv('PASS_ADMIN', 'admin123')

        try:
            if not User.query.filter_by(email=super_email).first():
                admin = User(
                    email=super_email, 
                    password=generate_password_hash(super_pass, method='sha256'),
                    is_admin=True
                )
                db.session.add(admin)
                db.session.commit()
                print(f"👑 Super Admin Created: {super_email}")
        except Exception as e:
            print(f"⚠️ Gagal cek/buat admin (Cek koneksi DB): {e}")

if __name__ == '__main__':
    init_db()
    app.run(debug=True, port=5000, use_reloader=False)
