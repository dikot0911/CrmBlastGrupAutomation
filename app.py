import os
import asyncio
import threading
import logging
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from telethon import TelegramClient, errors
from telethon.sessions import StringSession

# --- CONFIGURATION ---
app = Flask(__name__)
app.secret_key = 'rahasia_negara_baba_parfume_saas'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///saas_database.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# --- GLOBAL VARS FOR TELEGRAM AUTH ---
# Kita butuh API ID/HASH "Master" punya kamu untuk menjembatani login user
API_ID = 12345678  # GANTI DENGAN API ID KAMU
API_HASH = 'ganti_dengan_api_hash_kamu' 
# Dictionary untuk menyimpan state login sementara
login_states = {} 

# --- DATABASE MODELS ---
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(150), nullable=False)
    # Relasi ke Akun Telegram
    telegram_account = db.relationship('TelegramAccount', backref='user', uselist=False)

class TelegramAccount(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), unique=True)
    phone_number = db.Column(db.String(20))
    session_string = db.Column(db.Text) # KUNCI UTAMA: Ini yang bikin bot jalan 24 jam
    is_active = db.Column(db.Boolean, default=True)
    targets = db.Column(db.Text, default="[]") # Simpan JSON string target grup

# --- ROUTES ---

@app.route('/')
def index():
    return render_template('landing.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password, password):
            session['user_id'] = user.id
            return redirect(url_for('dashboard'))
        flash('Email atau password salah!', 'danger')
    return render_template('auth.html', mode='login')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        if User.query.filter_by(email=email).first():
            flash('Email sudah terdaftar!', 'warning')
            return redirect(url_for('register'))
            
        new_user = User(email=email, password=generate_password_hash(password, method='sha256'))
        db.session.add(new_user)
        db.session.commit()
        flash('Registrasi berhasil! Silakan login.', 'success')
        return redirect(url_for('login'))
    return render_template('auth.html', mode='register')

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session: return redirect(url_for('login'))
    user = User.query.get(session['user_id'])
    return render_template('dashboard.html', user=user)

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('index'))

# --- TELETHON AUTH FLOW (THE MAGIC) ---

@app.route('/api/connect/send_code', methods=['POST'])
async def send_code():
    """Langkah 1: User input No HP -> Server minta OTP ke Telegram"""
    if 'user_id' not in session: return jsonify({'status': 'error', 'message': 'Unauthorized'}), 401
    
    phone = request.json.get('phone')
    user_id = session['user_id']
    
    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.connect()
    
    try:
        if not await client.is_user_authorized():
            phone_code_hash = await client.send_code_request(phone)
            # Simpan state sementara di memory (bisa diganti Redis untuk production)
            login_states[user_id] = {
                'client': client,
                'phone': phone,
                'phone_code_hash': phone_code_hash.phone_code_hash
            }
            return jsonify({'status': 'success', 'message': 'OTP terkirim ke Telegram/SMS anda.'})
        else:
            return jsonify({'status': 'error', 'message': 'Nomor ini sudah login.'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})

@app.route('/api/connect/verify_code', methods=['POST'])
async def verify_code():
    """Langkah 2: User input OTP -> Server generate Session String"""
    if 'user_id' not in session: return jsonify({'status': 'error', 'message': 'Unauthorized'}), 401
    
    user_id = session['user_id']
    otp = request.json.get('otp')
    password = request.json.get('password') # 2FA password jika ada
    
    state = login_states.get(user_id)
    if not state: return jsonify({'status': 'error', 'message': 'Session expired. Ulangi input nomor.'})
    
    client = state['client']
    phone = state['phone']
    hash_code = state['phone_code_hash']
    
    try:
        try:
            await client.sign_in(phone, otp, phone_code_hash=hash_code)
        except errors.SessionPasswordNeededError:
            if not password:
                return jsonify({'status': '2fa_required', 'message': 'Akun ini pakai Password 2FA. Mohon input.'})
            await client.sign_in(password=password)
            
        # SUKSES LOGIN! AMBIL SESSION STRING
        string_sess = client.session.save()
        
        # Simpan ke Database
        user = User.query.get(user_id)
        if user.telegram_account:
            user.telegram_account.session_string = string_sess
            user.telegram_account.phone_number = phone
        else:
            new_tele = TelegramAccount(user_id=user_id, phone_number=phone, session_string=string_sess)
            db.session.add(new_tele)
        
        db.session.commit()
        
        # Bersihkan memory
        await client.disconnect()
        del login_states[user_id]
        
        return jsonify({'status': 'success', 'message': 'Telegram Berhasil Terhubung!'})
        
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})

# --- HELPER: INITIALIZE DB ---
def init_db():
    with app.app_context():
        db.create_all()

if __name__ == '__main__':
    init_db()
    # Gunakan loop asyncio untuk Flask (Diperlukan karena Telethon async)
    app.run(debug=True, port=5000, use_reloader=False)
