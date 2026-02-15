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
import qrcode
import base64
import uuid
import string
from io import BytesIO
from functools import wraps 
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash, send_from_directory, Response, stream_with_context

# --- TELETHON & SUPABASE ---
from telethon import TelegramClient, errors, functions, types, utils, events
from telethon.sessions import StringSession
from supabase import create_client, Client

# ==============================================================================
# SECTION 1: SYSTEM CONFIGURATION & ENVIRONMENT SETUP
# ==============================================================================

# [FIX 1: LOGGER WAJIB PALING ATAS]
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] [%(name)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("BlastProSAAS")

# Initialize Flask Application
app = Flask(__name__)

# Security Configuration
app.secret_key = os.getenv('SECRET_KEY', 'rahasia_Blast_Pro_Saas_ultimate_key_v99_production_ready')

# [FIX 2: IMPORT MODUL LAIN SETELAH LOGGER JADI]
try:
    from demo_routes import demo_bp
    if demo_bp:
        app.register_blueprint(demo_bp)  
except ImportError:
    demo_bp = None

try:
    from bot import run_bot_process
except ImportError as e:
    # Sekarang aman panggil logger disini
    logger.warning(f"⚠️ Modul bot.py tidak ditemukan atau error: {e}")
    run_bot_process = None

# Session Configuration
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)
app.config['SESSION_COOKIE_SECURE'] = True  
app.config['SESSION_COOKIE_HTTPONLY'] = True 
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax' 

# Upload Configuration
UPLOAD_FOLDER = 'static/uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

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
        # [FIX] Perhatikan spasi di baris bawah ini! (Harus menjorok ke dalam)
        from supabase.lib.client_options import ClientOptions
        
        # Inisialisasi Client
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        
        logger.info("✅ Supabase API Connected Successfully.")
    except Exception as e:
        logger.critical(f"❌ Supabase Failed: {e}")
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
# In-Memory Storage untuk QR Login (Client Object disimpan sementara disini)
qr_sessions = {}

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

# --- "Trigger BOT TELEGRAM

def send_telegram_alert(user_id, message, show_report_btn=False):
    """
    Kirim notif ke Telegram User.
    Param show_report_btn: Jika True, akan menampilkan tombol '🔍 Lihat Detail'.
    """
    # [FIX SAFETY] Cek koneksi DB dulu biar gak crash
    if not supabase:
        logger.warning(f"⚠️ Skip notif user {user_id}: Database Disconnected")
        return

    try:
        res = supabase.table('users').select("notification_chat_id").eq('id', user_id).execute()
        if not res.data or not res.data[0]['notification_chat_id']: return 
        
        chat_id = res.data[0]['notification_chat_id']
        notif_token = os.getenv("NOTIF_BOT_TOKEN")
        if not notif_token: return
        
        url = f"https://api.telegram.org/bot{notif_token}/sendMessage"
        
        payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "Markdown"
        }

        # [NEW] Jika ini laporan blast, tambahkan tombol
        if show_report_btn:
            payload["reply_markup"] = {
                "inline_keyboard": [[
                    {
                        "text": "🔍 Lihat Detail & Error", 
                        "callback_data": f"menu_reports_{user_id}"
                    }
                ]]
            }

        httpx.post(url, json=payload, timeout=5)
    except Exception as e:
        # [FIX LOGGING] Pake logger biar seragam sama yang lain
        logger.error(f"⚠️ Gagal kirim notif: {e}")

def generate_ref_code():
    """Bikin kode unik 6 karakter, contoh: X7Y9Z1"""
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

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
# SECTION 4.5.5: AUTO REPLY & KEYWORD ENGINE (NEW FEATURE)
# ==============================================================================

class AutoReplyManager:
    """
    Mengelola Data Pengaturan Auto Reply & Keyword Rules dari Database.
    Fokus pada CRUD dan Logika Data.
    """

    @staticmethod
    def normalize_phone(phone):
        """
        Membersihkan format nomor HP agar konsisten (+62812...)
        Hapus spasi, strip, dan pastikan ada tanda plus.
        """
        if not phone: return 'all'
        if phone == 'all': return 'all'
        clean = str(phone).replace(" ", "").replace("-", "").strip()
        if not clean.startswith("+"): clean = "+" + clean
        return clean

    @staticmethod
    def get_settings(user_id, raw_target='all'):
        """
        Mengambil settingan untuk akun tertentu.
        PRIORITAS: 
        1. Cari settingan KHUSUS nomor ini.
        2. Kalau gak ada, cari settingan GLOBAL ('all').
        3. Kalau gak ada juga, return Default (Mati).
        """
        target = AutoReplyManager.normalize_phone(raw_target)
        
        if not supabase: return None
        
        # 1. Coba cari settingan spesifik
        res = supabase.table('auto_reply_settings').select("*")\
            .eq('user_id', user_id).eq('target_phone', target).execute()
        
        if res.data:
            return res.data[0]
            
        # 2. Fallback ke Global ('all') jika yang dicari bukan 'all'
        if target != 'all':
            res_glob = supabase.table('auto_reply_settings').select("*")\
                .eq('user_id', user_id).eq('target_phone', 'all').execute()
            if res_glob.data:
                return res_glob.data[0]

        # 3. Default (Fitur Dianggap Mati)
        return {
            'is_active': False, 
            'cooldown_minutes': 60, 
            'welcome_message': '', 
            'target_phone': target
        }

    @staticmethod
    def update_settings(user_id, data):
        """
        Simpan atau Update settingan.
        """
        # Normalize dulu target-nya biar gak double
        data['target_phone'] = AutoReplyManager.normalize_phone(data.get('target_phone', 'all'))
        
        # Cek existing
        existing = supabase.table('auto_reply_settings').select("id")\
            .eq('user_id', user_id).eq('target_phone', data['target_phone']).execute()
            
        if existing.data:
            # Update
            supabase.table('auto_reply_settings').update(data)\
                .eq('id', existing.data[0]['id']).execute()
        else:
            # Insert
            data['user_id'] = user_id
            supabase.table('auto_reply_settings').insert(data).execute()

    @staticmethod
    def get_keywords(user_id):
        """Ambil semua keyword milik user ini."""
        res = supabase.table('keyword_rules').select("*")\
            .eq('user_id', user_id).order('created_at', desc=True).execute()
        return res.data if res.data else []

    @staticmethod
    def add_keyword(user_id, keyword, response, raw_target='all'):
        """Tambah keyword baru ke database."""
        target = AutoReplyManager.normalize_phone(raw_target)
        data = {
            'user_id': user_id, 
            'keyword': keyword.lower(), 
            'response': response, 
            'target_phone': target
        }
        supabase.table('keyword_rules').insert(data).execute()

    @staticmethod
    def delete_keyword(id):
        """Hapus keyword berdasarkan ID."""
        supabase.table('keyword_rules').delete().eq('id', id).execute()

class ReplyEngine:
    """
    Worker Cerdas untuk Auto-Reply.
    Fitur: Multi-Account Isolation, Priority Logic (Specific > Global), Cooldown.
    """
    active_listeners = {} 

    @staticmethod
    def start_listener(user_id, client):
        client_key = f"{user_id}_{id(client)}"
        if client_key in ReplyEngine.active_listeners: return 

        settings = AutoReplyManager.get_settings(user_id)
        if not settings or not settings.get('is_active'): return

        target_phone_setting = settings.get('target_phone', 'all')
        
        async def _attach():
            try:
                me = await client.get_me()
                my_phone = f"+{me.phone}" if not str(me.phone).startswith('+') else str(me.phone)
                
                # Filter Level 1: Apakah akun ini diizinkan aktif?
                if target_phone_setting != 'all' and target_phone_setting != my_phone:
                    return

                # Load Resources
                keywords = AutoReplyManager.get_keywords(user_id)
                welcome_msg = settings.get('welcome_message')
                cooldown = settings.get('cooldown_minutes', 60)

                @client.on(events.NewMessage(incoming=True))
                async def handler(event):
                    try:
                        # Filter Dasar: Jangan respon diri sendiri, bot lain, atau grup/channel
                        if event.sender_id == me.id or event.message.via_bot_id: return
                        if event.is_group or event.is_channel: return

                        sender_id = event.sender_id
                        chat_text = event.raw_text.lower().strip()
                        response_text = None

                        # --- LOGIC PINTAR PEMILIHAN KEYWORD ---
                        # 1. Ambil keyword yang SPESIFIK buat akun ini
                        specific_rules = [r for r in keywords if r.get('target_phone') == my_phone]
                        # 2. Ambil keyword GLOBAL (all)
                        global_rules = [r for r in keywords if r.get('target_phone') == 'all']
                        
                        # Gabung: Prioritaskan Spesifik dulu, baru Global
                        # Jadi kalau ada keyword sama di Spesifik & Global, yang Spesifik yang menang
                        active_rules = specific_rules + global_rules
                        
                        for rule in active_rules:
                            # Support Partial Match (mengandung kata)
                            if rule['keyword'] in chat_text:
                                response_text = rule['response']
                                logger.info(f"🤖 [AutoReply] {my_phone} reply to {sender_id} | Rule: {rule['keyword']}")
                                break 

                        # --- LOGIC WELCOME MESSAGE (JIKA GAK ADA KEYWORD) ---
                        if not response_text and welcome_msg:
                            # Cek Cooldown
                            log_res = supabase.table('reply_logs').select("last_reply_at")\
                                .eq('user_id', user_id).eq('sender_id', sender_id).execute()
                            
                            should_reply = True
                            if log_res.data:
                                last_time = datetime.fromisoformat(log_res.data[0]['last_reply_at'].replace('Z', '+00:00'))
                                diff_min = (datetime.now(pytz.utc) - last_time).total_seconds() / 60
                                if diff_min < cooldown: should_reply = False
                            
                            if should_reply: 
                                response_text = welcome_msg
                                logger.info(f"🤖 [AutoReply] {my_phone} send WELCOME to {sender_id}")

                        # --- EKSEKUSI BALASAN ---
                        if response_text:
                            # Simulasi Ngetik (Humanis)
                            async with client.action(event.chat_id, 'typing'):
                                await asyncio.sleep(random.uniform(2.0, 4.5))
                            
                            await event.reply(response_text)
                            
                            # Catat Log biar gak spam welcome message
                            log_data = {'user_id': user_id, 'sender_id': sender_id, 'last_reply_at': datetime.utcnow().isoformat()}
                            supabase.table('reply_logs').upsert(log_data, on_conflict="user_id, sender_id").execute()

                    except Exception as e:
                        logger.error(f"ReplyHandler Error ({my_phone}): {e}")

                ReplyEngine.active_listeners[client_key] = True
                logger.info(f"👂 Auto-Reply Active on: {my_phone}")

            except Exception as e:
                logger.error(f"Listener Attach Error: {e}")

        run_async(_attach())
            
# ==============================================================================
# SECTION 4.6: SCHEDULER & AUTO-BLAST WORKER (HUMAN + SMART RETRY) - FIXED
# ==============================================================================

class SchedulerWorker:
    """
    Worker cerdas dengan TIMEZONE WIB (Asia/Jakarta).
    Fitur: Human Behavior, Smart Retry (3 Phase), & Anti-Flood.
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
                # 1. Ambil Waktu Sekarang (WIB)
                tz_indo = pytz.timezone('Asia/Jakarta')
                now_indo = datetime.now(tz_indo)

                # 2. Cek Jadwal di DB
                if supabase:
                    SchedulerWorker._process_schedules(now_indo)
                
                # 3. Sleep Pintar (Sync ke detik 00)
                sleep_time = 60 - datetime.now().second
                if sleep_time < 0: sleep_time = 1
                time.sleep(sleep_time)
                
            except Exception as e:
                logger.error(f"Scheduler Loop Error: {e}")
                time.sleep(60)

    @staticmethod
    def _process_schedules(current_time_indo):
        try:
            # --- 1. NOTIFIKASI 5 MENIT SEBELUM ---
            future_time = current_time_indo + timedelta(minutes=5)
            f_hour = future_time.hour
            f_minute = future_time.minute
            
            # Cek jadwal 5 menit ke depan
            upcoming = supabase.table('blast_schedules').select("user_id")\
                .eq('is_active', True)\
                .eq('run_hour', f_hour)\
                .eq('run_minute', f_minute)\
                .execute().data
                
            for job in upcoming:
                msg = (
                    "⏳ **PENGINGAT JADWAL**\n\n"
                    f"Jadwal Blast akan berjalan dalam **5 menit lagi** "
                    f"(Pukul {f_hour}:{f_minute:02d} WIB).\n\n"
                    "Pastikan akun Telegram pengirim (Sender) Anda aktif/online agar proses lancar."
                )
                threading.Thread(target=send_telegram_alert, args=(job['user_id'], msg)).start()
            
            # --- 2. EKSEKUSI JADWAL SEKARANG (INI YANG KEMAREN ILANG) ---
            current_hour = current_time_indo.hour
            current_minute = current_time_indo.minute

            # [FIX UTAMA] Definisikan variabel 'res' disini!
            res = supabase.table('blast_schedules').select("*")\
                .eq('is_active', True)\
                .eq('run_hour', current_hour)\
                .eq('run_minute', current_minute)\
                .execute()
                
            schedules = res.data
            if not schedules: return
                
            logger.info(f"🚀 EXECUTE: Ditemukan {len(schedules)} jadwal induk.")
            
            for task in schedules:
                # [LOGIC BARU: EXPAND TEMPLATE SAAT RUNTIME]
                # Cek apakah jadwal ini pakai Template Koleksi?
                if task.get('target_template_name'):
                    # Ambil nama template
                    tmpl_name = task['target_template_name']
                    user_id = task['user_id']
                    
                    # Cari semua grup yang ada di template ini (REAL-TIME FETCH)
                    targets = supabase.table('blast_targets').select("*")\
                        .eq('user_id', user_id)\
                        .eq('template_name', tmpl_name)\
                        .execute().data
                        
                    if targets:
                        logger.info(f"📂 Expanding Collection '{tmpl_name}': {len(targets)} groups found.")
                        
                        # Loop bikin task virtual buat setiap grup dalam template
                        for t in targets:
                            # Bikin copy task biar gak ngerusak data asli
                            sub_task = task.copy()
                            # Override target dengan ID Grup spesifik dari template
                            sub_task['target_group_id'] = t['id'] 
                            # Pastikan sender kebawa
                            sub_task['sender_phone'] = task.get('sender_phone') 
                            
                            # Jalankan Eksekusi
                            threading.Thread(target=SchedulerWorker._execute_task, args=(sub_task,)).start()
                    else:
                        logger.warning(f"⚠️ Collection '{tmpl_name}' kosong pas mau jalan.")
                
                else:
                    # Jadwal Biasa (Single Group) - Jalankan langsung
                    threading.Thread(target=SchedulerWorker._execute_task, args=(task,)).start()
                
        except Exception as e:
            logger.error(f"Scheduler Process Error: {e}")
            
    @staticmethod
    def _execute_task(task):
        """
        [STRICT MODE] Eksekusi Task dengan Keamanan Niche Akun.
        """
        user_id = task['user_id']
        template_id = task.get('template_id') 
        target_group_id = task.get('target_group_id') 
        sender_phone = task.get('sender_phone') # Ini nomor HP pengirim yang dipilih
        
        # 1. Siapkan Konten
        message_content = "Halo! Ini pesan terjadwal otomatis."
        source_media = None
        
        if template_id:
            tmpl = MessageTemplateManager.get_template_by_id(template_id)
            if tmpl:
                message_content = tmpl['message_text']
                if tmpl.get('source_chat_id'):
                    source_media = {'chat': int(tmpl['source_chat_id']), 'id': int(tmpl['source_message_id'])}

        # 2. Worker Async Utama
        async def _async_send():
            client = None
            conn_error = None
            
            # --- A. LOGIC KONEKSI "STRICT" ---
            try:
                # Cek apakah user milih akun SPESIFIK atau AUTO?
                is_specific_sender = (sender_phone and sender_phone != 'auto')

                if is_specific_sender:
                    # KASUS 1: USER MILIH AKUN SPESIFIK
                    res = supabase.table('telegram_accounts').select("session_string")\
                        .eq('user_id', user_id).eq('phone_number', sender_phone).eq('is_active', True).execute()
                    
                    if res.data:
                        client = TelegramClient(StringSession(res.data[0]['session_string']), API_ID, API_HASH)
                        await client.connect()
                    else:
                        # JIKA AKUN SPESIFIK MATI -> LANGSUNG STOP
                        conn_error = f"⛔ Akun {sender_phone} mati/logout. Task dibatalkan demi keamanan branding."
                
                else:
                    # KASUS 2: USER MILIH "AUTO"
                    client = await get_active_client(user_id)
                    if not client:
                        conn_error = "Tidak ada akun Telegram yang aktif sama sekali."
                
                # JIKA GAGAL KONEK
                if not client or not await client.is_user_authorized():
                    # Catat Log Gagal
                    supabase.table('blast_logs').insert({
                        "user_id": user_id, "group_name": "SYSTEM", "group_id": 0,
                        "status": "FAILED", "error_message": conn_error or "Auth Failed",
                        "created_at": datetime.utcnow().isoformat()
                    }).execute()
                    
                    # Lapor Bot
                    send_telegram_alert(user_id, f"❌ **Jadwal Gagal!**\n{conn_error}")
                    return 

            except Exception as e:
                logger.error(f"Scheduler Connect Error: {e}")
                return

            try:
                # --- B. PERSIAPAN DATA ---
                send_telegram_alert(user_id, f"🚀 **Jadwal Dimulai!**\nPengirim: {sender_phone if is_specific_sender else 'Auto'}")

                # Load Media
                media_obj = None
                if source_media:
                    try:
                        src_msg = await client.get_messages(source_media['chat'], ids=source_media['id'])
                        if src_msg and src_msg.media: media_obj = src_msg.media
                    except: pass

                # Ambil Target
                targets_query = supabase.table('blast_targets').select("*").eq('user_id', user_id)
                if target_group_id: targets_query = targets_query.eq('id', target_group_id)
                raw_targets = targets_query.execute().data
                
                if not raw_targets:
                    send_telegram_alert(user_id, "⚠️ Target grup kosong.")
                    return

                # FLATTEN TARGETS
                send_queue = []
                for tg in raw_targets:
                    topic_ids = []
                    if tg.get('topic_ids'):
                        try: topic_ids = [int(x.strip()) for x in str(tg['topic_ids']).split(',') if x.strip().isdigit()]
                        except: pass
                    
                    destinations = topic_ids if topic_ids else [None]
                    for top_id in destinations:
                        send_queue.append({
                            'group_id': int(tg['group_id']),
                            'topic_id': top_id,
                            'group_name': tg.get('group_name', 'Unknown')
                        })

                # --- C. PROCESS QUEUE ---
                async def process_queue(queue_list, attempt_phase):
                    next_retry_queue = []
                    success_count = 0
                    
                    for idx, item in enumerate(queue_list):
                        if idx > 0 and idx % 20 == 0: await asyncio.sleep(random.randint(60, 120))

                        try:
                            entity = await client.get_entity(item['group_id'])
                            await client.send_read_acknowledge(entity)
                            async with client.action(entity, 'typing'): await asyncio.sleep(random.uniform(2, 5))

                            final_msg = message_content.replace("{name}", item['group_name'])
                            
                            if media_obj: await client.send_file(entity, media_obj, caption=final_msg, reply_to=item['topic_id'])
                            else: await client.send_message(entity, final_msg, reply_to=item['topic_id'])
                            
                            supabase.table('blast_logs').insert({
                                "user_id": user_id, "group_name": item['group_name'], "group_id": item['group_id'], 
                                "status": "SUCCESS", "created_at": datetime.utcnow().isoformat()
                            }).execute()
                            success_count += 1
                            await asyncio.sleep(random.randint(3, 8))

                        except Exception as e:
                            err = str(e)
                            if "FloodWait" in err or "SlowMode" in err: next_retry_queue.append(item)
                            else:
                                supabase.table('blast_logs').insert({
                                    "user_id": user_id, "group_name": item['group_name'], "status": "FAILED", 
                                    "error_message": err, "created_at": datetime.utcnow().isoformat()
                                }).execute()
                            await asyncio.sleep(2)

                    return next_retry_queue, success_count
                
                # --- D. EKSEKUSI ---
                total_success = 0
                retry_1, s1 = await process_queue(send_queue, 1)
                total_success += s1
                
                if retry_1:
                    await asyncio.sleep(30)
                    retry_2, s2 = await process_queue(retry_1, 2)
                    total_success += s2
                    
                    if retry_2:
                        await asyncio.sleep(60)
                        _, s3 = await process_queue(retry_2, 3)
                        total_success += s3

                send_telegram_alert(user_id, f"✅ **Jadwal Selesai!**\nTotal Terkirim: {total_success}")

            finally: 
                if client: await client.disconnect()
        
        run_async(_async_send())

# Jalankan Scheduler saat app start
if supabase:
    SchedulerWorker.start()


# ==============================================================================
# SECTION 4.7: AUTO REPLY BACKGROUND SERVICE (SATPAM 24 JAM)
# ==============================================================================

class AutoReplyService:
    """
    Worker yang berjalan di background (Daemon Thread).
    Tugas: Menjaga koneksi MTProto tetap hidup untuk mendengarkan pesan masuk.
    """
    _loop = None
    _clients = {} # Database koneksi aktif di memori: { 'UserID_NoHP': ClientObject }

    @classmethod
    def start(cls):
        """Fungsi Pemicu Utama (Dipanggil di paling bawah app.py)"""
        threading.Thread(target=cls._background_process, daemon=True, name="AutoReplySatpam").start()
        logger.info("👮‍♂️ [SATPAM] AutoReply Service BERHASIL DINYALAKAN!")

    @classmethod
    def _background_process(cls):
        """Membuat Event Loop khusus untuk Thread Satpam"""
        cls._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(cls._loop)
        cls._loop.run_until_complete(cls._main_supervisor())

    @classmethod
    async def _main_supervisor(cls):
        logger.info("👀 [SATPAM] Mulai patroli hemat RAM...")
        while True:
            try:
                if supabase:
                    # 1. Ambil list akun Telegram yang terdaftar & aktif sesinya
                    acc_res = supabase.table('telegram_accounts').select("*").eq('is_active', True).execute()
                    all_accounts = acc_res.data or []
                    
                    # 2. Ambil settingan Auto Reply yang statusnya ON (True)
                    # Kita cuma mau akun yang DI-IZINKAN NYALA
                    settings_res = supabase.table('auto_reply_settings').select("target_phone").eq('is_active', True).execute()
                    allowed_phones = [s['target_phone'] for s in settings_res.data]
                    
                    # Tambahan: Kalau ada setting 'all' yang aktif, berarti semua akun boleh nyala?
                    # Strategi Hemat RAM: TIDAK. Kita paksa user nyalain per akun biar sadar resource.
                    # Tapi kalau lu mau 'all' ngetrigger semua, uncomment baris bawah:
                    # if 'all' in allowed_phones: allowed_phones = [a['phone_number'] for a in all_accounts]

                    active_keys = []
                    
                    for acc in all_accounts:
                        phone = AutoReplyManager.normalize_phone(acc['phone_number'])
                        
                        # SYARAT LOGIN: 
                        # 1. Nomor HP ada di daftar allowed_phones (Status ON)
                        if phone in allowed_phones:
                            key = f"{acc['user_id']}_{acc['phone_number']}"
                            active_keys.append(key)
                            
                            if key not in cls._clients:
                                await cls._start_client(acc, key)
                        else:
                            # Kalau status OFF tapi masih connect, matikan (Hemat RAM)
                            key = f"{acc['user_id']}_{acc['phone_number']}"
                            if key in cls._clients:
                                await cls._stop_client(key)
                    
                    # 3. Cleanup sisa
                    for existing_key in list(cls._clients.keys()):
                        if existing_key not in active_keys:
                            await cls._stop_client(existing_key)
                            
            except Exception as e:
                logger.error(f"⚠️ [SATPAM] Supervisor Error: {e}")
            
            await asyncio.sleep(25) # Cek tiap 25 detik

    @classmethod
    async def _start_client(cls, acc_data, key):
        """Menghidupkan 1 Klien Telegram untuk 1 Akun"""
        try:
            # 1. Login pakai Session String
            client = TelegramClient(StringSession(acc_data['session_string']), API_ID, API_HASH)
            await client.connect()
            
            # 2. Cek apakah sesi masih valid?
            if not await client.is_user_authorized():
                logger.warning(f"❌ [SATPAM] Sesi Invalid/Expired: {key}")
                await client.disconnect()
                return

            # Data Penting
            user_id = acc_data['user_id']
            # Normalize nomor HP dari DB biar cocok sama settingan
            my_phone = AutoReplyManager.normalize_phone(acc_data['phone_number'])

            # --- 3. PASANG EVENT HANDLER (INTI LOGIC) ---
            @client.on(events.NewMessage(incoming=True))
            async def incoming_handler(event):
                try:
                    # A. FILTER AWAL (Anti Spam Grup/Channel)
                    me_obj = await client.get_me()
                    if event.sender_id == me_obj.id or event.message.via_bot_id: return
                    if event.is_group or event.is_channel: return # STOP KALAU DARI GRUP!

                    sender_id = event.sender_id
                    chat_text = event.raw_text.lower().strip()
                    
                    # B. AMBIL SETTINGAN (Realtime dari DB)
                    # Panggil Manager: "Eh, akun nomor HP ini settingannya apa?"
                    settings = AutoReplyManager.get_settings(user_id, my_phone)
                    
                    # Kalau fitur dimatikan, cuekin aja
                    if not settings or not settings.get('is_active'): return

                    # C. LOGIC PENCARIAN KEYWORD
                    keywords = AutoReplyManager.get_keywords(user_id)
                    response_text = None

                    # Prioritas: 
                    # 1. Cari keyword yang TARGETNYA == NOMOR INI
                    specific_rules = [r for r in keywords if AutoReplyManager.normalize_phone(r.get('target_phone')) == my_phone]
                    # 2. Cari keyword yang TARGETNYA == ALL
                    global_rules = [r for r in keywords if r.get('target_phone') == 'all']
                    
                    # Gabung (Specific duluan biar menang)
                    all_rules = specific_rules + global_rules

                    for rule in all_rules:
                        # Cek apakah keyword ada di dalam chat?
                        if rule['keyword'] in chat_text:
                            response_text = rule['response']
                            logger.info(f"✅ [SATPAM] {my_phone} menjawab '{rule['keyword']}' ke {sender_id}")
                            break
                    
                    # D. LOGIC WELCOME MESSAGE (Jika gak ada keyword)
                    if not response_text and settings.get('welcome_message'):
                        # Cek Cooldown (Jeda Spam)
                        cooldown_min = settings.get('cooldown_minutes', 60)
                        log_res = supabase.table('reply_logs').select("last_reply_at")\
                            .eq('user_id', user_id).eq('sender_id', sender_id).execute()
                        
                        should_reply = True
                        if log_res.data:
                            last_time = datetime.fromisoformat(log_res.data[0]['last_reply_at'].replace('Z', '+00:00'))
                            diff_min = (datetime.now(pytz.utc) - last_time).total_seconds() / 60
                            # Kalau masih dalam masa cooldown, jangan bales
                            if diff_min < cooldown_min: should_reply = False
                        
                        if should_reply:
                            response_text = settings.get('welcome_message')
                            logger.info(f"👋 [SATPAM] {my_phone} kirim Welcome Message ke {sender_id}")

                    # E. EKSEKUSI KIRIM PESAN
                    if response_text:
                        # Akting ngetik dulu (Typing...)
                        async with client.action(event.chat_id, 'typing'):
                            await asyncio.sleep(random.uniform(2.0, 4.0)) # Jeda humanis
                        
                        # Kirim!
                        await event.reply(response_text)
                        
                        # Catat log biar cooldown jalan
                        log_data = {
                            'user_id': user_id, 
                            'sender_id': sender_id, 
                            'last_reply_at': datetime.utcnow().isoformat()
                        }
                        supabase.table('reply_logs').upsert(log_data, on_conflict="user_id, sender_id").execute()

                except Exception as handler_e:
                    logger.error(f"Handler Error {my_phone}: {handler_e}")

            # Simpan client ini ke memori biar gak mati
            cls._clients[key] = client
            logger.info(f"👂 [SATPAM] Aktif Mendengarkan: {my_phone}")
            
        except Exception as e:
            logger.error(f"Gagal start client {key}: {e}")

    @classmethod
    async def _stop_client(cls, key):
        """Mematikan 1 Klien (Misal user logout atau hapus akun)"""
        client = cls._clients.pop(key, None)
        if client:
            await client.disconnect()
            logger.info(f"💤 [SATPAM] Stop Listening: {key}")
    
# ==============================================================================
# SECTION 5: DATA ACCESS LAYER (DAL)
# ==============================================================================

def get_user_data(user_id):
    """
    Mengambil data User lengkap dengan status Subscription & Telegram.
    """
    if not supabase: return None
    try:
        # 1. Fetch User Data
        u_res = supabase.table('users').select("*").eq('id', user_id).execute()
        if not u_res.data: return None
        user_raw = u_res.data[0]
        
        # 2. Fetch Telegram Account
        t_res = supabase.table('telegram_accounts').select("*").eq('user_id', user_id).execute()
        tele_raw = t_res.data[0] if t_res.data else None
        
        # 3. Create Wrapper Object
        class UserEntity:
            def __init__(self, u_data, t_data):
                self.id = u_data['id']
                self.email = u_data['email']
                self.is_admin = u_data.get('is_admin', False)
                self.is_banned = u_data.get('is_banned', False)
                
                # ... (Kode lama parsing tanggal dll biarin aja) ...
        
                # --- [TAMBAHAN BARU] REFERRAL & WALLET ---
                self.referral_code = u_data.get('referral_code', '-')
                self.wallet_balance = u_data.get('wallet_balance', 0)
                self.notification_chat_id = u_data.get('notification_chat_id') # Buat cek status bot
                
                # Parsing Tanggal Join
                raw_created = u_data.get('created_at')
                try:
                    self.created_at = datetime.fromisoformat(raw_created.replace('Z', '+00:00')) if raw_created else datetime.now()
                except:
                    self.created_at = datetime.now()

                # --- LOGIC BARU: SUBSCRIPTION ---
                self.plan_tier = u_data.get('plan_tier', 'Starter') # Default Starter
                
                # Hitung Sisa Hari
                raw_sub_end = u_data.get('subscription_end')
                self.days_remaining = 0
                self.subscription_status = 'Expired'
                self.sub_end_date = None

                if raw_sub_end:
                    try:
                        # Parsing tanggal expire
                        end_date = datetime.fromisoformat(raw_sub_end.replace('Z', '+00:00'))
                        self.sub_end_date = end_date
                        
                        # Hitung selisih hari dari SEKARANG (UTC)
                        now = datetime.now(pytz.utc)
                        delta = end_date - now
                        
                        if delta.days >= 0:
                            self.days_remaining = delta.days
                            self.subscription_status = 'Active'
                        else:
                            self.days_remaining = 0
                            self.plan_tier = 'Starter' # Downgrade otomatis visualnya
                    except Exception as e:
                        logger.error(f"Date Parse Error: {e}")

                # Nested Object for Telegram Info
                self.telegram_account = None
                if t_data:
                    self.telegram_account = type('TeleInfo', (object,), {
                        'phone_number': t_data.get('phone_number'),
                        'is_active': t_data.get('is_active', False),
                        'session_string': t_data.get('session_string')
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
        if not await client.is_user_authorized():
            logger.warning(f"Client Init: Session EXPIRED/REVOKED for UserID {user_id}")
            await client.disconnect()
            
            # Auto-update status di DB jadi Inactive agar UI dashboard update
            supabase.table('telegram_accounts').update({'is_active': False}).eq('user_id', user_id).execute()
            return None

        # --- [INI YANG BIKIN ERROR TADI - SEKARANG UDAH RAPI] ---
        try:
            # Pasang kuping Auto Reply di sini
            if 'ReplyEngine' in globals() and ReplyEngine:
                ReplyEngine.start_listener(user_id, client)
        except Exception as e:
            # Kalau listener gagal, jangan bikin aplikasi crash. Log aja warning.
            logger.warning(f"AutoReply Listener Error: {e}")
            
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
    # Ambil data pricing struktur JSON tapi dikonversi balik ke Dict Python
    # Supaya bisa di-looping pakai Jinja2 di HTML
    pricing_data = {}
    if supabase:
        try:
            raw_json = FinanceManager.get_plans_json() # Reuse fungsi yang udah ada
            pricing_data = json.loads(raw_json)
        except: pass
        
    return render_template('landing/index.html', pricing=pricing_data)

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
    # Tangkap kode referral dari URL (misal: /register?ref=BABA123)
    ref_param = request.args.get('ref')
    
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        # Tangkap ref code dari form hidden input juga (kalau ada)
        ref_code_input = request.form.get('referral_code') or ref_param
        
        try:
            # 1. Validasi Email Duplikat (Sama kayak lama)
            exist = supabase.table('users').select("id").eq('email', email).execute()
            if exist.data:
                flash('Email sudah terdaftar.', 'warning')
                return redirect(url_for('register'))
            
            # 2. Cek Upline (Siapa yang ngajak?)
            upline_id = None
            if ref_code_input:
                upline_res = supabase.table('users').select("id").eq('referral_code', ref_code_input).execute()
                if upline_res.data:
                    upline_id = upline_res.data[0]['id']
            
            # 3. Create User Baru (+ Referral Code & Upline)
            hashed_pw = generate_password_hash(password)
            new_ref_code = generate_ref_code() # Bikin kode unik buat dia
            
            new_user = {
                'email': email,
                'password': hashed_pw,
                'created_at': datetime.utcnow().isoformat(),
                'is_admin': False,
                'is_banned': False,
                'plan_tier': 'Starter',
                'referral_code': new_ref_code, # <--- INI BARU
                'referred_by': upline_id,      # <--- INI BARU (Disimpan biar tau harus bagi komisi ke siapa)
                'wallet_balance': 0
            }
            supabase.table('users').insert(new_user).execute()
            
            flash('Pendaftaran Berhasil! Silakan login.', 'success')
            return redirect(url_for('login'))
            
        except Exception as e:
            logger.error(f"Register Error: {e}")
            flash('Gagal mendaftar.', 'danger')
            
    return render_template('auth/register.html', ref=ref_param)

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
    """
    Halaman Utama Dashboard User:
    - Redirect Admin ke Super Admin Panel
    - Statistik Ringkas
    - Log Aktivitas dengan Pagination
    """
    user = get_dashboard_context()
    if not user: return redirect(url_for('login'))
    
    # [FIX 1] Redirect Super Admin supaya gak masuk sini
    if user.is_admin:
        return redirect(url_for('super_admin_dashboard'))
    
    uid = user.id
    
    # --- LOGIC PAGINATION ---
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    start = (page - 1) * per_page
    end = start + per_page - 1
    
    logs = []
    total_logs = 0
    total_pages = 0
    stats = {} # [FIX 2] Container untuk statistik kartu
    
    if supabase:
        try:
            # A. Pagination Logs
            count_res = supabase.table('blast_logs').select("id", count='exact', head=True).eq('user_id', uid).execute()
            total_logs = count_res.count if count_res.count else 0
            import math
            total_pages = math.ceil(total_logs / per_page)

            logs = supabase.table('blast_logs').select("*").eq('user_id', uid)\
                .order('created_at', desc=True).range(start, end).execute().data
            
            # B. Ambil Data Jadwal & Target
            schedules = supabase.table('blast_schedules').select("*").eq('user_id', uid).execute().data
            targets = supabase.table('blast_targets').select("*").eq('user_id', uid).execute().data
            
            # C. Hitung Statistik Ringkas (Buat Kartu Atas)
            acc_count = supabase.table('telegram_accounts').select("id", count='exact', head=True).eq('user_id', uid).eq('is_active', True).execute().count
            success_blast = supabase.table('blast_logs').select("id", count='exact', head=True).eq('user_id', uid).eq('status', 'SUCCESS').execute().count
            
            stats = {
                'connected_accounts': acc_count or 0,
                'total_blast': total_logs,
                'success_rate': int((success_blast/total_logs)*100) if total_logs > 0 else 0,
                'active_schedules': len([s for s in schedules if s.get('is_active')])
            }
            
        except Exception as e:
            logger.error(f"Dashboard Data Error: {e}")
            stats = {'connected_accounts': 0, 'total_blast': 0, 'success_rate': 0, 'active_schedules': 0}
    
    return render_template('dashboard/index.html', 
                           user=user, 
                           stats=stats, # Kirim stats biar gak error
                           logs=logs, 
                           schedules=schedules, 
                           targets=targets,
                           # Pagination Data
                           current_page=page,
                           total_pages=total_pages,
                           total_logs=total_logs,
                           active_page='home') # Ganti 'dashboard' jadi 'home' sesuai base.html

# Variable Global buat kontrol Broadcast
# Format: {'user_id': 'running' | 'stopped'}
broadcast_states = {}

@app.route('/dashboard/broadcast')
@login_required
def dashboard_broadcast():
    """Halaman Fitur Broadcast"""
    user = get_dashboard_context()
    if not user: return redirect(url_for('login'))
    
    crm_count = 0
    templates = []
    accounts = [] # <--- INI WAJIB ADA
    selected_ids = ""
    count_selected = 0
    
    try:
        # Fetch CRM Count
        crm_res = supabase.table('tele_users').select("id", count='exact', head=True).eq('owner_id', user.id).execute()
        crm_count = crm_res.count if crm_res.count else 0
        
        # Load Templates
        templates = MessageTemplateManager.get_templates(user.id)
        
        # [FIX] Load Active Accounts (Biar Muncul di Tab Pengirim)
        acc_res = supabase.table('telegram_accounts').select("*").eq('user_id', user.id).eq('is_active', True).execute()
        accounts = acc_res.data if acc_res.data else []

        # Tangkap ID dari URL (lemparan dari CRM)
        ids_arg = request.args.get('ids')
        if ids_arg:
            selected_ids = ids_arg
            count_selected = len(ids_arg.split(','))
            
    except Exception as e:
        logger.error(f"Broadcast Page Error: {e}")

    return render_template('dashboard/broadcast.html', 
                           user=user, 
                           user_count=crm_count, 
                           templates=templates,
                           accounts=accounts,       # <--- KIRIM KE HTML
                           selected_ids=selected_ids,     
                           count_selected=count_selected, 
                           active_page='broadcast')

# API Buat Stop Broadcast
@app.route('/api/broadcast/stop', methods=['POST'])
@login_required
def stop_broadcast_api():
    user_id = session['user_id']
    broadcast_states[user_id] = 'stopped' # Set Flag Stop
    return jsonify({'status': 'success', 'message': 'Broadcast stopping...'})

@app.route('/dashboard/targets')
@login_required
def dashboard_targets():
    """Halaman Manajemen Target Grup (Mode Template / Collection)"""
    user = get_dashboard_context()
    if not user: return redirect(url_for('login'))
    
    # Struktur Data: { 'No_HP_Akun': { 'Nama_Template': [List Grup] } }
    grouped_targets = {}
    
    # List Akun Aktif (Untuk Dropdown Scan & Import)
    accounts = []
    
    try:
        # 1. Ambil Akun Aktif
        acc_res = supabase.table('telegram_accounts').select("*").eq('user_id', user.id).eq('is_active', True).execute()
        accounts = acc_res.data if acc_res.data else []

        # 2. Ambil Semua Target
        all_targets = supabase.table('blast_targets').select("*").eq('user_id', user.id).order('created_at', desc=True).execute().data
        
        # 3. Logic Grouping (Python Side biar fleksibel)
        for t in all_targets:
            # Key 1: Source Phone (Akun)
            src = t.get('source_phone') or 'Unknown Account'
            # Key 2: Template Name (Koleksi)
            tmpl = t.get('template_name') or 'Tanpa Nama'
            
            if src not in grouped_targets:
                grouped_targets[src] = {}
            
            if tmpl not in grouped_targets[src]:
                grouped_targets[src][tmpl] = []
                
            grouped_targets[src][tmpl].append(t)
            
    except Exception as e:
        logger.error(f"Targets Page Error: {e}")
    
    return render_template('dashboard/targets.html', 
                           user=user, 
                           grouped_targets=grouped_targets, # Data struktur baru
                           accounts=accounts, 
                           active_page='targets')

# TAMBAHAN UTK TARGET MANAGER (RENAME & EDIT)
@app.route('/api/target/rename_template', methods=['POST'])
@login_required
def rename_target_template():
    user_id = session['user_id']
    data = request.json
    
    old_name = data.get('old_name')
    new_name = data.get('new_name')
    source_phone = data.get('source_phone')
    
    if not old_name or not new_name or not source_phone:
        return jsonify({'status': 'error', 'message': 'Data tidak lengkap'})
        
    try:
        # Update semua baris yang punya nama template lama & akun yg sama
        supabase.table('blast_targets').update({'template_name': new_name})\
            .eq('user_id', user_id)\
            .eq('source_phone', source_phone)\
            .eq('template_name', old_name)\
            .execute()
            
        return jsonify({'status': 'success', 'message': 'Template berhasil diubah!'})
    except Exception as e:
        logger.error(f"Rename Template Error: {e}")
        return jsonify({'status': 'error', 'message': str(e)})

@app.route('/api/target/update_group', methods=['POST'])
@login_required
def update_target_group():
    user_id = session['user_id']
    data = request.json
    
    target_id = data.get('id')
    new_name = data.get('group_name')
    new_topics = data.get('topic_ids') # String "123, 456" atau None
    
    try:
        update_payload = {
            'group_name': new_name,
            'topic_ids': new_topics
        }
        
        supabase.table('blast_targets').update(update_payload)\
            .eq('id', target_id)\
            .eq('user_id', user_id)\
            .execute()
            
        return jsonify({'status': 'success', 'message': 'Data grup berhasil diperbarui.'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})

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
    """Halaman Database Pelanggan dengan Filter Folder Akun"""
    user = get_dashboard_context()
    if not user: return redirect(url_for('login'))
    
    # 1. Ambil List Akun Yang AKTIF Saja (Untuk Navigasi Folder)
    accounts = []
    active_phones = []
    try:
        acc_res = supabase.table('telegram_accounts').select("*")\
            .eq('user_id', user.id).eq('is_active', True)\
            .order('created_at', desc=True).execute()
        accounts = acc_res.data if acc_res.data else []
        active_phones = [acc['phone_number'] for acc in accounts]
    except Exception as e:
        logger.error(f"Fetch Accounts Error: {e}")

    # 2. Logic Pagination & Search
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)
    search_query = request.args.get('q', '').strip()
    
    # Tangkap parameter folder/tab yang dipilih user (Default: all)
    current_source = request.args.get('source', 'all') 

    start = (page - 1) * per_page
    end = start + per_page - 1
    
    crm_users = []
    total_users = 0
    total_pages = 0
    
    if supabase:
        try:
            # Base Query
            query = supabase.table('tele_users').select("*", count='exact').eq('owner_id', user.id)
            
            # --- LOGIC FOLDER ---
            if current_source != 'all':
                # Filter hanya data milik akun tertentu
                if current_source in active_phones:
                    query = query.eq('source_phone', current_source)
                else:
                    # Kalau user iseng ganti URL ke akun yg gak aktif/gak ada -> Kosongkan hasil
                    query = query.eq('id', -1) 
            else:
                # Tab "All Database": Tampilkan semua data TAPI HANYA dari akun yang masih aktif
                if active_phones:
                    query = query.in_('source_phone', active_phones)
                else:
                    query = query.eq('id', -1)

            # Filter Pencarian
            if search_query:
                query = query.ilike('username', f'%{search_query}%') 
            
            # Eksekusi
            res = query.order('last_interaction', desc=True).range(start, end).execute()
            
            crm_users = res.data if res.data else []
            total_users = res.count if res.count else 0
            
            import math
            total_pages = math.ceil(total_users / per_page) if per_page > 0 else 0
            
        except Exception as e:
            logger.error(f"CRM Data Error: {e}")
            # Jangan crash, kirim data kosong aja biar halaman tetep kebuka
            crm_users = []
    
    # Render Template dengan semua variabel yang dibutuhkan
    return render_template('dashboard/crm.html', 
                           user=user, 
                           crm_users=crm_users, 
                           user_count=total_users,
                           current_page=page,
                           total_pages=total_pages,
                           per_page=per_page,
                           search_query=search_query,
                           active_page='crm',
                           accounts=accounts,       # <--- PENTING BUAT FOLDER
                           current_source=current_source) # <--- PENTING BUAT NAVIGASI

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
    """Halaman Profile dengan Logic Deep Linking Bot"""
    user = get_user_data(session['user_id'])
    if not user: return redirect(url_for('login'))
    
    # 1. Generate Token Unik buat Deep Linking
    import uuid
    verify_token = str(uuid.uuid4())
    
    # 2. Simpan Token ke DB
    supabase.table('users').update({'verification_token': verify_token}).eq('id', user.id).execute()
    
    # 3. Bikin Link Bot (Ambil username bot dari env)
    bot_username = os.getenv('NOTIF_BOT_USERNAME', 'NamaBotLu_bot') 
    bot_link = f"https://t.me/{bot_username}?start={verify_token}"
    
    # 4. Cek Status Koneksi Notif (User udah connect bot belum?)
    # Kita cek manual field notification_chat_id dari database raw karena di wrapper get_user_data mungkin belum ada
    raw_user = supabase.table('users').select("notification_chat_id").eq('id', user.id).execute()
    is_notif_connected = False
    if raw_user.data and raw_user.data[0]['notification_chat_id']:
        is_notif_connected = True

    return render_template('dashboard/profile.html', 
                           user=user, 
                           active_page='profile',
                           bot_link=bot_link,
                           is_notif_connected=is_notif_connected)
    
@app.route('/dashboard/payment')
@login_required
def dashboard_payment():
    user = get_dashboard_context()
    # Ambil data dinamis dari DB
    plans_data = FinanceManager.get_plans_structure()
    banks = supabase.table('admin_banks').select("*").eq('is_active', True).execute().data
    
    return render_template('dashboard/payment.html', 
                           user=user, 
                           active_page='payment',
                           plans_json=json.dumps(plans_data), # Kirim JSON ke JS
                           banks=banks)

# --- [TAMBAHAN WAJIB] API CHECKOUT USER ---
@app.route('/api/payment/checkout', methods=['POST'])
@login_required
def api_checkout():
    user_id = session['user_id']
    variant_id = request.form.get('variant_id')
    method = request.form.get('payment_method')
    proof = request.files.get('proof_file')
    
    # Validasi input
    if not variant_id or not method:
        flash('Data pembayaran tidak lengkap.', 'danger')
        return redirect(url_for('dashboard_payment'))

    # Panggil Manager untuk simpan transaksi
    success, msg = FinanceManager.create_transaction(user_id, variant_id, method, proof)
    
    if success:
        # Kirim notif ke Admin (Optional) atau ke User
        flash('✅ Invoice berhasil dibuat! Mohon tunggu konfirmasi admin 1x24 jam.', 'success')
    else:
        flash(f'❌ Gagal: {msg}', 'danger')
        
    return redirect(url_for('dashboard_payment'))
    
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
# SECTION 9.5: QR CODE LOGIN HANDLER (2FA SUPPORT)
# ==============================================================================

# Global Memory untuk komunikasi antar-thread (Scan QR & Input Password)
qr_states = {}

def qr_worker(user_id, session_uuid):
    print(f"THREAD [{session_uuid}]: Worker Started", flush=True)
    
    async def _process():
        # Bikin koneksi baru khusus sesi ini
        client = TelegramClient(StringSession(), API_ID, API_HASH)
        await client.connect()
        
        try:
            if not await client.is_user_authorized():
                # 1. Request QR Token
                qr_login = await client.qr_login()
                qr_states[session_uuid]['qr_url'] = qr_login.url
                qr_states[session_uuid]['status'] = 'waiting'
                
                try:
                    # 2. TUNGGU USER SCAN DI HP
                    # Timeout 120 detik biar user gak buru-buru
                    print(f"THREAD [{session_uuid}]: Waiting scan...", flush=True)
                    await qr_login.wait(timeout=120)
                    
                    # 3. SCAN BERHASIL -> Cek apakah butuh password 2FA?
                    # Kita coba panggil get_me(). 
                    # Jika user pake 2FA, method ini bakal gagal & melempar error.
                    
                    try:
                        me = await client.get_me()
                        
                        # --- FLOW A: LOGIN LANGSUNG (TANPA 2FA) ---
                        final_session = client.session.save()
                        qr_states[session_uuid]['user_data'] = {
                            'session': final_session,
                            'phone': f"+{me.phone}",
                            'first_name': me.first_name,
                            'last_name': me.last_name,
                            'username': me.username
                        }
                        qr_states[session_uuid]['status'] = 'success'
                        print(f"THREAD [{session_uuid}]: Login Success (No 2FA)", flush=True)
                        
                    except errors.SessionPasswordNeededError:
                        # --- FLOW B: BUTUH PASSWORD (2FA DETECTED) ---
                        print(f"THREAD [{session_uuid}]: 2FA REQUIRED!", flush=True)
                        
                        # Update status biar frontend tau harus minta password
                        qr_states[session_uuid]['status'] = '2fa_required'
                        
                        # TUNGGU PASSWORD DARI FRONTEND (Maks 120 detik)
                        password_received = False
                        for _ in range(240): # 240 x 0.5s = 120 detik
                            # Cek apakah password udah dikirim via API /submit_2fa
                            if 'password_input' in qr_states[session_uuid]:
                                pw = qr_states[session_uuid]['password_input']
                                try:
                                    # Coba login pake password
                                    await client.sign_in(password=pw)
                                    
                                    # Kalau tembus sini, berarti password BENAR!
                                    me = await client.get_me()
                                    final_session = client.session.save()
                                    
                                    qr_states[session_uuid]['user_data'] = {
                                        'session': final_session,
                                        'phone': f"+{me.phone}",
                                        'first_name': me.first_name,
                                        'last_name': me.last_name,
                                        'username': me.username
                                    }
                                    qr_states[session_uuid]['status'] = 'success'
                                    password_received = True
                                    print(f"THREAD [{session_uuid}]: Login Success (With 2FA)", flush=True)
                                    break
                                    
                                except Exception as pw_e:
                                    # Password Salah
                                    print(f"THREAD [{session_uuid}]: Wrong Password: {pw_e}", flush=True)
                                    qr_states[session_uuid]['status'] = 'error'
                                    qr_states[session_uuid]['error_msg'] = "Password Salah!"
                                    password_received = True # Biar loop berhenti
                                    break
                                    
                            await asyncio.sleep(0.5)
                        
                        if not password_received:
                            qr_states[session_uuid]['status'] = 'expired'
                            
                except asyncio.TimeoutError:
                    qr_states[session_uuid]['status'] = 'expired'
            else:
                qr_states[session_uuid]['status'] = 'error'
                qr_states[session_uuid]['error_msg'] = "Client already auth?"
                
        except Exception as e:
            # Handle error umum
            err = str(e)
            print(f"THREAD [{session_uuid}]: CRITICAL ERROR: {err}", flush=True)
            qr_states[session_uuid]['status'] = 'error'
            qr_states[session_uuid]['error_msg'] = err
        finally:
            await client.disconnect()

    # Jalankan Loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(_process())
    loop.close()

# --- ROUTE 1: MINTA QR (SAMA KAYAK SEBELUMNYA) ---
@app.route('/api/connect/get_qr', methods=['POST'])
@login_required
def get_qr_code():
    user_id = session['user_id']
    
    # Limit Check
    try:
        res = supabase.table('telegram_accounts').select("id", count='exact', head=True).eq('user_id', user_id).execute()
        if (res.count or 0) >= 3:
            return jsonify({'status': 'limit_reached', 'message': 'Limit 3 Akun Tercapai!'})
    except: pass

    session_uuid = str(uuid.uuid4())
    qr_states[session_uuid] = {'status': 'initializing', 'qr_url': None}
    
    # Start Background Thread
    t = threading.Thread(target=qr_worker, args=(user_id, session_uuid))
    t.daemon = True
    t.start()
    
    # Tunggu sebentar
    import time
    for _ in range(50):
        if qr_states[session_uuid].get('qr_url'): break
        time.sleep(0.1)
        
    if not qr_states[session_uuid].get('qr_url'):
        return jsonify({'status': 'error', 'message': 'Timeout koneksi Telegram.'})
        
    # Generate Image
    url = qr_states[session_uuid]['qr_url']
    img = qrcode.make(url)
    buffered = BytesIO()
    img.save(buffered, format="PNG")
    img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")
    
    return jsonify({
        'status': 'success', 
        'qr_image': f"data:image/png;base64,{img_str}",
        'session_uuid': session_uuid
    })

# --- ROUTE 2: KIRIM PASSWORD 2FA (BARU!) ---
@app.route('/api/connect/submit_2fa', methods=['POST'])
@login_required
def submit_2fa_qr():
    session_uuid = request.json.get('session_uuid')
    password = request.json.get('password')
    
    if session_uuid in qr_states:
        # Masukkan password ke memory agar diambil oleh Thread Worker
        qr_states[session_uuid]['password_input'] = password
        return jsonify({'status': 'success'})
    
    return jsonify({'status': 'error', 'message': 'Sesi QR hilang/kadaluarsa'})

# --- ROUTE 3: CEK STATUS (POLLING) ---
@app.route('/api/connect/check_qr', methods=['POST'])
@login_required
def check_qr_status():
    session_uuid = request.json.get('session_uuid')
    user_id = session['user_id']
    
    if not session_uuid or session_uuid not in qr_states:
        return jsonify({'status': 'expired', 'message': 'QR Expired.'})
    
    state = qr_states[session_uuid]
    status = state.get('status')
    
    if status == 'success':
        # Login Sukses -> Simpan DB
        u_data = state['user_data']
        db_data = {
            'user_id': user_id,
            'phone_number': u_data['phone'],
            'session_string': u_data['session'],
            'first_name': u_data['first_name'] or '',
            'last_name': u_data['last_name'] or '',
            'username': u_data['username'] or '',
            'is_active': True,
            'targets': '[]',
            'created_at': datetime.utcnow().isoformat()
        }
        supabase.table('telegram_accounts').upsert(db_data, on_conflict="user_id, phone_number").execute()
        del qr_states[session_uuid]
        return jsonify({'status': 'success', 'message': f"Login Berhasil: {u_data['first_name']}"})
        
    elif status == '2fa_required':
        # Kasih tau frontend buat munculin prompt password
        return jsonify({'status': '2fa'})
        
    elif status == 'expired':
        return jsonify({'status': 'expired'})
    elif status == 'error':
        return jsonify({'status': 'error', 'message': state.get('error_msg', 'Unknown Error')})
    else:
        return jsonify({'status': 'waiting'})

# ==============================================================================
# SECTION 10: BOT FEATURES API (SCAN, TARGETS, IMPORT)
# ==============================================================================

# Pastikan import functions ada di paling atas file app.py. 
# Kalau belum, tambahkan di Section 1: from telethon import functions

@app.route('/scan_groups_api')
@login_required
def scan_groups_api():
    """
    Advanced Group Scanner API (DYNAMIC IMPORT FIX).
    Fitur: Mencari fungsi Forum API secara dinamis tanpa peduli versi Telethon.
    """
    user_id = session.get('user_id')
    target_phone = request.args.get('phone') 

    if user_id is None:
        return jsonify({"status": "error", "message": "User not authenticated."}), 401

    async def _scan():
        # --- 1. SETUP LIBRARIES (DYNAMIC MODE) ---
        import telethon
        from telethon import utils, types, functions
        from telethon.tl.types import InputPeerChannel
        
        logger.info(f"🧐 [DEBUG] Telethon Version: {telethon.__version__}")

        # [MAGIC FIX] Cari fungsi Forum Topic secara dinamis
        # Kita cek di 'channels' atau 'messages' namespace
        GetForumTopicsRequest = None
        
        # Coba cari di functions.channels
        if hasattr(functions.channels, 'GetForumTopicsRequest'):
            GetForumTopicsRequest = functions.channels.GetForumTopicsRequest
            logger.info("✅ API Found: functions.channels.GetForumTopicsRequest")
        # Coba cari di functions.messages (kadadang disini)
        elif hasattr(functions.messages, 'GetForumTopicsRequest'):
            GetForumTopicsRequest = functions.messages.GetForumTopicsRequest
            logger.info("✅ API Found: functions.messages.GetForumTopicsRequest")
        # Coba nama lain (kadang namanya cuma GetForumTopics)
        elif hasattr(functions.channels, 'GetForumTopics'):
            GetForumTopicsRequest = functions.channels.GetForumTopics
            logger.info("✅ API Found: functions.channels.GetForumTopics")
            
        HAS_RAW_API = GetForumTopicsRequest is not None
        
        if not HAS_RAW_API:
            logger.warning("⚠️ [FATAL] Forum API beneran gak ketemu di library ini. Cek dokumentasi Telethon terbaru.")

        # --- 2. CONNECT TO TELEGRAM ---
        client = None
        conn_info = "Default Account"
        
        if target_phone:
            try:
                res = supabase.table('telegram_accounts').select("session_string")\
                    .eq('user_id', user_id).eq('phone_number', target_phone).eq('is_active', True).execute()
                if res.data:
                    client = TelegramClient(StringSession(res.data[0]['session_string']), API_ID, API_HASH)
                    await client.connect()
                    conn_info = f"Specific: {target_phone}"
            except Exception as e:
                logger.error(f"Connect Error: {e}")

        if not client:
            client = await get_active_client(user_id)
            conn_info = "Auto-Default"

        if not client: 
            return jsonify({"status": "error", "message": "Tidak ada akun Telegram yang terhubung."})

        logger.info(f"🚀 Starting Scan Process via {conn_info}...")
        
        groups = []
        stats = {'groups': 0, 'forums': 0, 'errors': 0, 'skipped': 0, 'topics_found': 0}

        try:
            # --- 3. SCANNING LOOP ---
            async for dialog in client.iter_dialogs(limit=500):
                try:
                    if not dialog.is_group:
                        stats['skipped'] += 1
                        continue 

                    entity = dialog.entity
                    real_id = utils.get_peer_id(entity)
                    
                    member_count = getattr(entity, 'participants_count', 0)
                    username = getattr(entity, 'username', None)
                    is_forum = getattr(entity, 'forum', False)
                    
                    all_topics = []

                    # --- 4. DEEP SCAN FOR FORUMS ---
                    if is_forum:
                        stats['forums'] += 1
                        
                        if HAS_RAW_API:
                            try:
                                # Input Channel Preparation
                                access_hash = getattr(entity, 'access_hash', None)
                                if access_hash:
                                    input_channel = InputPeerChannel(channel_id=entity.id, access_hash=access_hash)
                                else:
                                    input_channel = await client.get_input_entity(real_id)

                                offset_id, offset_date, offset_topic = 0, 0, 0
                                
                                # Scan 5 Pages
                                for page in range(5): 
                                    req = GetForumTopicsRequest(
                                        input_channel,           # <--- Perhatikan ini! Gak pake channel=
                                        q='',                    # Query search kosong
                                        offset_date=offset_date,
                                        offset_id=offset_id,
                                        offset_topic=offset_topic,
                                        limit=100
                                    )
                                    res = await client(req)
                                    if not res.topics: break
                                    
                                    for t in res.topics:
                                        t_id = getattr(t, 'id', None)
                                        if t_id:
                                            t_title = getattr(t, 'title', '')
                                            # Filter Deleted/Closed
                                            if isinstance(t, types.ForumTopicDeleted): t_title = f"(Deleted) #{t_id}"
                                            elif not t_title: t_title = f"Topic #{t_id}"
                                            
                                            # Normalize General
                                            if t_id == 1 and ("Topic #1" in t_title or not t_title): 
                                                t_title = "General 📌"
                                                
                                            all_topics.append({'id': t_id, 'title': t_title})
                                            stats['topics_found'] += 1
                                    
                                    last = res.topics[-1]
                                    offset_id = getattr(last, 'id', 0)
                                    offset_date = getattr(last, 'date', 0)
                                    await asyncio.sleep(0.2) # Anti Flood

                                # Sort & Fallback
                                all_topics.sort(key=lambda x: x['id'])
                                if not any(t['id'] == 1 for t in all_topics):
                                    all_topics.insert(0, {'id': 1, 'title': 'General (Topik Utama) 📌'})

                            except Exception as forum_e:
                                logger.error(f"Forum Scan Error {dialog.name}: {forum_e}")
                                all_topics = [{'id': 1, 'title': 'General (Fallback - Scan Error)'}]
                        else:
                            # Kalau API beneran gak ketemu
                            all_topics = [{'id': 1, 'title': 'General (Fallback - API Missing)'}]
                    else:
                        stats['groups'] += 1

                    # --- 5. CONSTRUCT DATA ---
                    g_data = {
                        'id': real_id, 
                        'name': dialog.name, 
                        'is_forum': is_forum,
                        'username': f"@{username}" if username else None,
                        'members': member_count,
                        'topics': all_topics
                    }
                    groups.append(g_data)

                except Exception as group_e:
                    logger.warning(f"Skip Group Error: {group_e}")
                    stats['errors'] += 1
                    continue

        except Exception as e:
            logger.critical(f"FATAL SCAN ERROR: {e}")
            return jsonify({'status': 'error', 'message': str(e)})
        finally:
            await client.disconnect()
            
        logger.info(f"✅ Scan Result: {stats}")
        return jsonify({
            'status': 'success', 
            'data': groups,
            'meta': stats
        })
    
    return run_async(_scan())

@app.route('/save_bulk_targets', methods=['POST'])
@login_required
def save_bulk_targets():
    user = session['user_id']
    data = request.json
    targets = data.get('targets', [])
    source_phone = data.get('source_phone')
    template_name = data.get('template_name', 'Scan Result ' + datetime.now().strftime('%d/%m'))

    if not targets:
        return jsonify({'status': 'error', 'message': 'Tidak ada grup yang dipilih'})

    try:
        source_name = None
        if source_phone:
            acc_data = supabase.table('telegram_accounts').select("first_name").eq('phone_number', source_phone).execute()
            if acc_data.data:
                source_name = acc_data.data[0]['first_name']

        final_data = []
        for t in targets:
            final_data.append({
                'user_id': user,
                'group_name': t['group_name'],
                'group_id': str(t['group_id']),
                'topic_ids': ",".join(map(str, t['topic_ids'])) if t.get('topic_ids') else None,
                'created_at': datetime.now().isoformat(),
                'source_phone': source_phone,
                'source_name': source_name,
                'template_name': template_name
            })

        supabase.table('blast_targets').insert(final_data).execute()
        return jsonify({'status': 'success', 'message': 'Database berhasil disimpan!'})

    except Exception as e:
        logger.error(f"Error saving targets: {e}")
        return jsonify({'status': 'error', 'message': str(e)})

@app.route('/api/get_crm_users', methods=['GET'])
@login_required
def api_get_crm_users():
    user_id = session['user_id']
    source = request.args.get('source', 'all')
    
    query = supabase.table('tele_users').select("user_id, first_name, username").eq('owner_id', user_id)
    
    if source != 'all' and source != 'auto':
        query = query.eq('source_phone', source)
        
    res = query.limit(1000).execute()
    return jsonify(res.data)

@app.route('/import_crm_api', methods=['POST'])
@login_required
def import_crm_api():
    user_id = session['user_id']
    data = request.json
    source_phone = data.get('source_phone')

    if not source_phone:
        return jsonify({"status": "error", "message": "Target akun belum dipilih."})
    
    async def _import():
        try:
            acc_res = supabase.table('telegram_accounts').select("session_string")\
                .eq('user_id', user_id).eq('phone_number', source_phone).eq('is_active', True).execute()
            
            if not acc_res.data:
                return jsonify({"status": "error", "message": "Akun tidak aktif/ditemukan."})
                
            session_str = acc_res.data[0]['session_string']
            client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
            await client.connect()
        except Exception as e:
            return jsonify({"status": "error", "message": f"Koneksi gagal: {e}"})
        
        count = 0
        try:
            final_source_label = source_phone 
            async for dialog in client.iter_dialogs(limit=1000):
                if dialog.is_user and not dialog.entity.bot:
                    u = dialog.entity
                    if u.self: continue
                    data_payload = {
                        "owner_id": user_id, "user_id": u.id, "username": u.username,
                        "first_name": u.first_name, "source_phone": final_source_label,
                        "last_interaction": datetime.utcnow().isoformat(), "created_at": datetime.utcnow().isoformat()
                    }
                    try:
                        supabase.table('tele_users').upsert(data_payload, on_conflict="owner_id, user_id").execute()
                        count += 1
                    except: pass
            await client.disconnect()
            return jsonify({"status": "success", "message": f"Berhasil sinkronisasi {count} kontak ke folder {final_source_label}."})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)})
            
    return run_async(_import())
    
def parse_telegram_link(link):
    try:
        clean_link = link.replace("https://t.me/", "").replace("t.me/", "").strip()
        parts = clean_link.split('/')
        if len(parts) < 2: return None, None
        try: msg_id = int(parts[-1])
        except: return None, None
        if parts[0] == 'c': chat_id = int(f"-100{parts[1]}")
        else: chat_id = parts[0]
        return chat_id, msg_id
    except Exception as e:
        logger.error(f"Link Parse Error: {e}")
        return None, None

@app.route('/delete_target_template', methods=['POST'])
@login_required
def delete_target_template():
    user_id = session['user_id']
    template_name = request.json.get('template_name')
    source_phone = request.json.get('source_phone')
    try:
        supabase.table('blast_targets').delete().eq('user_id', user_id).eq('template_name', template_name).eq('source_phone', source_phone).execute()
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/import_targets_csv', methods=['POST'])
@login_required
def import_targets_csv():
    user_id = session['user_id']
    file = request.files.get('file')
    source_phone = request.form.get('source_phone')
    template_name = request.form.get('template_name')

    if not file or not source_phone or not template_name:
        return jsonify({"status": "error", "message": "Data tidak lengkap."})

    try:
        stream = io.StringIO(file.stream.read().decode("utf-8-sig"), newline=None)
        csv_input = csv.DictReader(stream)
        valid_rows = []
        for row in csv_input:
            gid = row.get('group_id') or row.get('id')
            gname = row.get('group_name') or row.get('name') or 'Imported Group'
            topics = row.get('topics') or row.get('topic_ids')
            if gid:
                valid_rows.append({
                    "user_id": user_id, "group_id": str(gid).strip(), "group_name": gname.strip(),
                    "topic_ids": topics.strip() if topics else None, "source_phone": source_phone,
                    "template_name": template_name, "created_at": datetime.utcnow().isoformat()
                })
        if valid_rows:
            supabase.table('blast_targets').insert(valid_rows).execute()
            return jsonify({"status": "success", "message": f"Berhasil import {len(valid_rows)} grup."})
        else:
            return jsonify({"status": "error", "message": "File CSV kosong atau format salah."})
    except Exception as e:
        return jsonify({"status": "error", "message": f"Error: {str(e)}"})

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
            entity, msg_id = parse_telegram_link(link)
            if not entity or not msg_id: return jsonify({'status': 'error', 'message': 'Link tidak valid.'})
            msg = await client.get_messages(entity, ids=msg_id)
            if not msg: return jsonify({'status': 'error', 'message': 'Pesan tidak ditemukan.'})
            return jsonify({
                'status': 'success', 'text': msg.text or "", 
                'has_media': True if msg.media else False,
                'source_chat_id': str(utils.get_peer_id(msg.peer_id)), 'source_message_id': msg.id
            })
        except Exception as e: return jsonify({'status': 'error', 'message': str(e)})
        finally: await client.disconnect()
        
    return run_async(_fetch())

# ==============================================================================
# SECTION 11: BROADCAST SYSTEM (REAL-TIME STREAMING & HUMAN MODE)
# ==============================================================================

# Global State buat kontrol Stop/Pause
broadcast_states = {}

def process_spintax(text):
    import re
    if not text: return ""
    pattern = r'\{([^{}]+)\}'
    while True:
        match = re.search(pattern, text)
        if not match: break
        options = match.group(1).split('|')
        choice = random.choice(options)
        text = text[:match.start()] + choice + text[match.end():]
    return text

@app.route('/start_broadcast', methods=['POST'])
@login_required
def start_broadcast():
    """
    Broadcast Engine v3.0 (Human Ultimate).
    Fitur: Typing Indicator, Random Delay, Spintax, Stop Signal.
    """
    user_id = session['user_id']
    broadcast_states[user_id] = 'running'

    # Tangkap Input
    message_raw = request.form.get('message')
    template_id = request.form.get('template_id')
    selected_ids_str = request.form.get('selected_ids') 
    target_option = request.form.get('target_option')
    sender_phone_req = request.form.get('sender_phone') 
    image_file = request.files.get('image')
    
    # Logic Content
    source_media = None
    final_message_template = message_raw

    if template_id:
        tmpl = MessageTemplateManager.get_template_by_id(template_id)
        if tmpl:
            if not final_message_template: final_message_template = tmpl['message_text']
            if tmpl.get('source_chat_id') and tmpl.get('source_message_id'):
                source_media = {'chat': int(tmpl['source_chat_id']), 'id': int(tmpl['source_message_id'])}

    if not final_message_template:
        return jsonify({"error": "Konten pesan tidak boleh kosong."})

    # Handle Image Upload
    manual_image_path = None
    if image_file and allowed_file(image_file.filename):
        filename = secure_filename(f"blast_{user_id}_{int(time.time())}_{image_file.filename}")
        manual_image_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        image_file.save(manual_image_path)

    # Tentukan Target
    targets = []
    if target_option == 'selected' and selected_ids_str:
        target_ids = [int(x) for x in selected_ids_str.split(',') if x.strip().isdigit()]
        if target_ids:
            res = supabase.table('tele_users').select("*").in_('user_id', target_ids).eq('owner_id', user_id).execute()
            targets = res.data
    else:
        res = supabase.table('tele_users').select("*").eq('owner_id', user_id).limit(5000).execute()
        targets = res.data

    if not targets:
        return jsonify({"error": "Target audiens kosong."})

    # GENERATOR FUNCTION
    def generate():
        yield json.dumps({"type": "start", "total": len(targets)}) + "\n"
        
        async def _engine():
            client = None
            try:
                # Koneksi Telegram
                if sender_phone_req and sender_phone_req != 'auto':
                    acc_res = supabase.table('telegram_accounts').select("session_string")\
                        .eq('user_id', user_id).eq('phone_number', sender_phone_req).eq('is_active', True).execute()
                    if acc_res.data:
                        client = TelegramClient(StringSession(acc_res.data[0]['session_string']), API_ID, API_HASH)
                        await client.connect()
                    else:
                        yield json.dumps({"type": "error", "msg": f"Akun {sender_phone_req} mati."}) + "\n"
                        return
                else:
                    client = await get_active_client(user_id)

                if not client or not await client.is_user_authorized():
                    yield json.dumps({"type": "error", "msg": "Gagal koneksi ke Telegram."}) + "\n"
                    return

                # Media Load
                cloud_media_obj = None
                if source_media:
                    try:
                        src_msg = await client.get_messages(source_media['chat'], ids=source_media['id'])
                        if src_msg and src_msg.media: cloud_media_obj = src_msg.media
                    except: pass

                # --- LOOPING PENGIRIMAN HUMANIS ---
                success_count = 0
                fail_count = 0
                
                for idx, user in enumerate(targets):
                    
                    # 1. CEK SIGNAL STOP
                    if broadcast_states.get(user_id) == 'stopped':
                        yield json.dumps({"type": "error", "msg": "⛔ Broadcast Dihentikan Paksa."}) + "\n"
                        break

                    # 2. SAFETY BREAK (Istirahat Panjang)
                    if idx > 0 and idx % 40 == 0:
                        rest_time = random.randint(120, 240)
                        yield json.dumps({
                            "type": "progress", "current": idx, "total": len(targets),
                            "status": "warning", "log": f"☕ Mode Humanis: Istirahat {rest_time}s...",
                            "success": success_count, "failed": fail_count
                        }) + "\n"
                        await asyncio.sleep(rest_time)

                    u_name = user.get('first_name') or "Kak"
                    personalized_msg = final_message_template.replace("{name}", u_name)
                    personalized_msg = process_spintax(personalized_msg) 

                    # 3. PROSES KIRIM
                    log_status = "FAILED"
                    error_msg = None
                    ui_log = ""
                    ui_status = "failed"
                    
                    try:
                        entity = await client.get_input_entity(int(user['user_id']))
                        
                        # [HUMAN TOUCH] Simulasi Ngetik
                        async with client.action(entity, 'typing'):
                            await asyncio.sleep(random.uniform(1.5, 4.5))

                        # Kirim Pesan
                        if cloud_media_obj:
                            await client.send_file(entity, cloud_media_obj, caption=personalized_msg)
                        elif manual_image_path:
                            await client.send_file(entity, manual_image_path, caption=personalized_msg)
                        else:
                            await client.send_message(entity, personalized_msg)
                        
                        success_count += 1
                        log_status = "SUCCESS"
                        ui_log = f"Terkirim ke {u_name}"
                        ui_status = "success"

                    except Exception as e:
                        fail_count += 1
                        error_msg = str(e)
                        ui_log = f"Gagal: {error_msg[:15]}..."
                        
                        if "FloodWait" in error_msg:
                            import re
                            wait_sec = int(re.search(r'\d+', error_msg).group()) if re.search(r'\d+', error_msg) else 60
                            yield json.dumps({"type": "progress", "log": f"⏳ Telegram minta istirahat {wait_sec}s...", "status": "warning"}) + "\n"
                            await asyncio.sleep(wait_sec)

                    # 4. LOGGING & UPDATE UI
                    try:
                        supabase.table('blast_logs').insert({
                            "user_id": user_id,
                            "group_name": f"{u_name} (User)",
                            "group_id": user['user_id'],
                            "status": log_status,
                            "error_message": error_msg,
                            "created_at": datetime.utcnow().isoformat()
                        }).execute()
                    except: pass 

                    yield json.dumps({
                        "type": "progress",
                        "current": idx + 1,
                        "total": len(targets),
                        "status": ui_status,
                        "log": ui_log,
                        "success": success_count,
                        "failed": fail_count
                    }) + "\n"

                    # 5. JEDA ANTAR PESAN (Human Interval)
                    await asyncio.sleep(random.uniform(4.0, 12.0))

            except Exception as e:
                yield json.dumps({"type": "error", "msg": f"System Error: {str(e)}"}) + "\n"
            
            finally:
                if client: await client.disconnect()
                if manual_image_path and os.path.exists(manual_image_path):
                    os.remove(manual_image_path)
                
                # --- [TAMBAHAN BARU: LAPORAN KE BOT] ---
                # Kirim notif "Selesai" lengkap dengan statistik & tombol cek error
                if success_count > 0 or fail_count > 0:
                    report_msg = (
                        f"🚀 **BROADCAST SELESAI!**\n"
                        f"─────────────────\n"
                        f"✅ **Berhasil:** {success_count}\n"
                        f"❌ **Gagal:** {fail_count}\n"
                        f"📊 **Total:** {success_count + fail_count}\n"
                        f"─────────────────\n"
                        f"_Klik tombol di bawah untuk melihat detail._"
                    )
                    # Jalankan di background thread biar gak bikin loading web lama
                    threading.Thread(
                        target=send_telegram_alert, 
                        args=(user_id, report_msg, True) # True = Munculin Tombol
                    ).start()

                yield json.dumps({"type": "done", "success": success_count, "failed": fail_count}) + "\n"

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            runner = _engine()
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

@app.route('/update_schedule', methods=['POST'])
@login_required
def update_schedule():
    s_id = request.form.get('schedule_id')
    user_id = session['user_id']
    
    try:
        data = {
            'run_hour': int(request.form.get('hour')),
            'run_minute': int(request.form.get('minute')),
            'sender_phone': request.form.get('sender_phone'),
            'target_group_id': request.form.get('target_group_id') or None,
            'template_id': int(request.form.get('template_id')) if request.form.get('template_id') else None
        }
        
        supabase.table('blast_schedules').update(data).eq('id', s_id).eq('user_id', user_id).execute()
        flash('Jadwal berhasil diperbarui!', 'success')
    except Exception as e:
        flash(f'Gagal update jadwal: {str(e)}', 'danger')
        
    return redirect(url_for('dashboard_schedule'))

@app.route('/add_schedule', methods=['POST'])
@login_required
def add_schedule():
    user = get_dashboard_context()
    
    # Tangkap Data Form
    hour = request.form.get('hour')
    minute = request.form.get('minute')
    template_id = request.form.get('template_id')
    target_input = request.form.get('target_group_id') # Bisa ID Angka atau "TEMPLATE:Nama"
    sender_phone = request.form.get('sender_phone') 
    
    if not sender_phone or sender_phone == 'auto':
        sender_phone = 'auto'

    try:
        # Data Dasar (Default)
        data = {
            'user_id': user.id,
            'run_hour': int(hour),
            'run_minute': int(minute),
            'template_id': int(template_id) if template_id else None,
            'sender_phone': sender_phone,
            'status': 'active',
            'created_at': datetime.now().isoformat(),
            'target_group_id': None,        # Default Kosong
            'target_template_name': None    # Default Kosong
        }

        # Logic Penentuan Target
        if target_input and target_input.startswith("TEMPLATE:"):
            # KASUS A: User milih TEMPLATE (Koleksi)
            # Kita simpan NAMA TEMPLATE-nya aja, jadi cuma 1 Baris di Database
            tmpl_name = target_input.replace("TEMPLATE:", "")
            data['target_template_name'] = tmpl_name
            
            # Validasi tipis: Cek template ada isinya gak?
            check = supabase.table('blast_targets').select("id").eq('template_name', tmpl_name).limit(1).execute()
            if not check.data:
                flash('Template target kosong/tidak ditemukan.', 'warning')
                return redirect(url_for('dashboard_schedule'))

        else:
            # KASUS B: User milih Grup Satuan (Single)
            if target_input:
                # Disini kita simpan ID Database (Primary Key), bukan ID Telegram
                data['target_group_id'] = int(target_input)

        # Simpan ke DB (Cuma 1 baris, gak bakal double!)
        supabase.table('blast_schedules').insert(data).execute()
        flash('Jadwal berhasil disimpan!', 'success')
        
    except Exception as e:
        logger.error(f"Error add schedule: {e}")
        flash(f'Gagal membuat jadwal: {str(e)}', 'danger')

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

@app.route('/update_template', methods=['POST'])
@login_required
def update_template():
    user_id = session['user_id']
    t_id = request.form.get('id') # Tangkap ID template
    name = request.form.get('name')
    msg = request.form.get('message')
    
    # Tangkap ID Sumber Cloud Media
    src_chat = request.form.get('source_chat_id')
    src_msg = request.form.get('source_message_id')
    
    # Konversi ke int/bigint kalau ada datanya
    final_chat = int(src_chat) if src_chat and src_chat != 'null' and src_chat != '' else None
    final_msg = int(src_msg) if src_msg and src_msg != 'null' and src_msg != '' else None

    if not t_id:
        flash('ID Template tidak valid.', 'danger')
        return redirect(url_for('dashboard_templates'))

    if not name:
        flash('Nama Template wajib diisi.', 'warning')
        return redirect(url_for('dashboard_templates'))
    
    try:
        data = {
            'name': name, 
            'message_text': msg, 
            'source_chat_id': final_chat, 
            'source_message_id': final_msg,
            'updated_at': datetime.utcnow().isoformat()
        }
        
        # Eksekusi Update ke Database
        supabase.table('message_templates').update(data).eq('id', t_id).eq('user_id', user_id).execute()
        
        flash('Template berhasil diperbarui!', 'success')
    except Exception as e:
        flash(f'Gagal update: {str(e)}', 'danger')
        logger.error(f"Template Update Error: {e}")
    
    return redirect(url_for('dashboard_templates'))

# AUTO REPLY-
@app.route('/dashboard/auto-reply', methods=['GET', 'POST'])
@login_required
def dashboard_auto_reply():
    user = get_dashboard_context()
    active_tab = request.args.get('tab', 'all') 

    # --- HANDLE SAVE SETTINGS (Sama kayak sebelumnya) ---
    if request.method == 'POST':
        try:
            # ... (kode save settings sama persis kayak sebelumnya) ...
            # Copy paste logic POST sebelumnya di sini
            is_active = request.form.get('is_active') == 'on'
            target = request.form.get('target_phone')
            
            hours = int(request.form.get('cooldown_hours', 0))
            minutes = int(request.form.get('cooldown_minutes', 0))
            total_minutes = (hours * 60) + minutes
            if total_minutes < 1: total_minutes = 1
            
            data = {
                'is_active': is_active,
                'target_phone': target, 
                'cooldown_minutes': total_minutes,
                'welcome_message': request.form.get('welcome_message'),
                'updated_at': datetime.utcnow().isoformat()
            }
            
            AutoReplyManager.update_settings(user.id, data)
            flash('Pengaturan berhasil disimpan!', 'success')
            return redirect(url_for('dashboard_auto_reply', tab=target))
        except Exception as e:
            logger.error(f"Save Error: {e}")
            flash(f"Gagal: {e}", 'danger')

    # --- GET DATA ---
    accounts = []
    try:
        res = supabase.table('telegram_accounts').select("*").eq('user_id', user.id).eq('is_active', True).execute()
        accounts = res.data or []
    except: pass

    all_keywords = AutoReplyManager.get_keywords(user.id)
    grouped = {'all': []}
    
    # Init folder
    for acc in accounts:
        grouped[acc['phone_number']] = []
        
    for k in all_keywords:
        raw_target = k.get('target_phone', 'all')
        # Logic grouping yang aman (Normalize dulu)
        matched_key = 'all'
        if raw_target != 'all':
            norm_target = AutoReplyManager.normalize_phone(raw_target)
            for acc_phone in grouped.keys():
                if AutoReplyManager.normalize_phone(acc_phone) == norm_target:
                    matched_key = acc_phone
                    break
        
        if matched_key not in grouped: grouped[matched_key] = []
        grouped[matched_key].append(k)
        
    # [FIX] Ambil list active phones & Normalize biar match sama frontend
    active_settings = supabase.table('auto_reply_settings').select("target_phone").eq('user_id', user.id).eq('is_active', True).execute()
    
    # Kita bikin list nomor HP yang udah dibersihin (tanpa spasi/dash)
    active_phones_normalized = []
    if active_settings.data:
        for s in active_settings.data:
            active_phones_normalized.append(AutoReplyManager.normalize_phone(s['target_phone']))

    current_settings = AutoReplyManager.get_settings(user.id, active_tab)
    
    return render_template('dashboard/auto_reply.html', 
                           user=user, 
                           settings=current_settings, 
                           accounts=accounts,
                           grouped_keywords=grouped,
                           active_tab=active_tab,
                           active_phones=active_phones_normalized, # Kirim data yg udah bersih
                           active_page='autoreply')

@app.route('/api/toggle_auto_reply', methods=['POST'])
@login_required
def api_toggle_auto_reply():
    user_id = session['user_id']
    data = request.json
    target_phone = data.get('target_phone')
    desired_state = data.get('state') # True (Mau ON) atau False (Mau OFF)

    try:
        # 1. Kalau mau ON, Cek Syarat: Harus punya minimal 1 keyword
        if desired_state:
            rules = supabase.table('keyword_rules').select("id", count='exact')\
                .eq('user_id', user_id).eq('target_phone', target_phone).execute()
            
            # Cek juga Global Rules
            global_rules = supabase.table('keyword_rules').select("id", count='exact')\
                .eq('user_id', user_id).eq('target_phone', 'all').execute()
            
            total_rules = (rules.count or 0) + (global_rules.count or 0)
            
            if total_rules == 0:
                return jsonify({
                    "status": "error", 
                    "message": "⚠️ Minimal buat 1 Keyword (Rule) dulu sebelum mengaktifkan bot ini!"
                }), 400

        # 2. Update Setting
        # Cek dulu row-nya ada gak
        current = AutoReplyManager.get_settings(user_id, target_phone)
        
        # Siapkan data update
        update_data = {
            'user_id': user_id,
            'target_phone': target_phone,
            'is_active': desired_state,
            'updated_at': datetime.utcnow().isoformat()
        }
        
        # Simpan (Pakai fungsi manager yang udah kita buat)
        AutoReplyManager.update_settings(user_id, update_data)
        
        status_msg = "✅ Bot Aktif" if desired_state else "zzz Bot Istirahat"
        return jsonify({"status": "success", "message": status_msg})

    except Exception as e:
        logger.error(f"Toggle Error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/add_keyword', methods=['POST'])
@login_required
def add_keyword_route():
    key = request.form.get('keyword')
    resp = request.form.get('response')
    target = request.form.get('target_phone', 'all') # <--- PENTING: Tangkap target akun
    
    if key and resp:
        AutoReplyManager.add_keyword(session['user_id'], key, resp, target)
        flash(f'Keyword "{key}" berhasil ditambahkan untuk {target if target != "all" else "Semua Akun"}.', 'success')
        
    return redirect(url_for('dashboard_auto_reply', tab=target)) # Balik ke tab yang sama

@app.route('/delete_keyword/<int:id>')
@login_required
def delete_keyword_route(id):
    AutoReplyManager.delete_keyword(id)
    flash('Keyword dihapus.', 'success')
    return redirect(url_for('dashboard_auto_reply'))
# ==============================================================================
# SECTION 13: SUPER ADMIN PANEL
# ==============================================================================

@app.route('/super-admin')
@app.route('/super-admin/dashboard')
@admin_required
def super_admin_dashboard():
    """
    Halaman Utama Admin (God Mode):
    - Statistik User & Bot
    - Statistik Keuangan (Revenue & Pending)
    - 5 Transaksi Terakhir
    """
    try:
        # 1. Stats User & Bot
        users_res = supabase.table('users').select("id, is_banned, plan_tier", count='exact').execute()
        bots_res = supabase.table('telegram_accounts').select("id", count='exact').eq('is_active', True).execute()
        
        users_data = users_res.data
        
        # 2. Stats Keuangan (INI YANG TADINYA KURANG)
        # Hitung Transaksi Pending
        pending_trx = supabase.table('transactions').select("id", count='exact').eq('status', 'pending').execute().count
        
        # Hitung Total Revenue (Paid Only)
        revenue_data = supabase.table('transactions').select("amount").eq('status', 'paid').execute().data
        total_revenue = sum(item['amount'] for item in revenue_data) if revenue_data else 0

        # Hitung User Aktif (Non-Starter & Belum Expired)
        now_iso = datetime.utcnow().isoformat()
        active_subs = supabase.table('users').select("id", count='exact')\
            .neq('plan_tier', 'Starter').gt('subscription_end', now_iso).execute().count

        # Rangkum Data Stats
        stats = {
            'total_users': users_res.count or 0,
            'active_bots': bots_res.count or 0,
            'active_subs': active_subs or 0,
            'pending_trx': pending_trx or 0,
            'revenue': total_revenue,
            'plans': {
                'agency': sum(1 for u in users_data if u.get('plan_tier') == 'Agency'),
                'pro': sum(1 for u in users_data if u.get('plan_tier') == 'UMKM Pro') # Sesuaikan string DB
            }
        }

        # 3. Ambil 5 Transaksi Terakhir (Buat Tabel Dashboard)
        recent_trx = supabase.table('transactions').select("*, users(email), pricing_variants(pricing_plans(code_name))")\
            .order('created_at', desc=True).limit(5).execute().data

        # 4. Waktu Server
        now_wib = (datetime.utcnow() + timedelta(hours=7)).strftime("%H:%M WIB")
        
        return render_template('admin/index.html', 
                               stats=stats, 
                               recent_trx=recent_trx, 
                               now_wib=now_wib,
                               active_page='dashboard')
                               
    except Exception as e:
        logger.error(f"Admin Dashboard Error: {e}")
        # Return fallback biar gak crash
        return render_template('admin/index.html', 
                               stats={'total_users':0, 'revenue':0, 'pending_trx':0, 'active_subs':0, 'active_bots':0, 'plans':{'agency':0, 'pro':0}}, 
                               recent_trx=[], 
                               now_wib="Error",
                               active_page='dashboard')

@app.route('/super-admin/users')
@admin_required
def super_admin_users():
    try:
        # Fetch Users dengan sorting terbaru
        users = supabase.table('users').select("*").order('created_at', desc=True).execute().data
        final_list = []
        
        for u in users:
            # Fetch Telegram Info
            tele = supabase.table('telegram_accounts').select("*").eq('user_id', u['id']).execute().data
            
            # Wrapper Class biar enak di HTML
            class UserW:
                def __init__(self, d, t):
                    self.id = d['id']
                    self.email = d['email']
                    self.is_admin = d.get('is_admin')
                    self.is_banned = d.get('is_banned')
                    self.plan_tier = d.get('plan_tier', 'Starter')
                    self.sub_end = d.get('subscription_end')
                    
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

# --- [FITUR BARU] DETAIL USER (GOD VIEW) ---
@app.route('/super-admin/user/<int:user_id>')
@admin_required
def super_admin_user_detail(user_id):
    """Halaman detail untuk kontrol penuh satu user"""
    try:
        # Ambil Data User
        u_res = supabase.table('users').select("*").eq('id', user_id).execute()
        if not u_res.data: return "User not found"
        user = u_res.data[0]
        
        # Ambil Data Telegram
        t_res = supabase.table('telegram_accounts').select("*").eq('user_id', user_id).execute()
        tele = t_res.data[0] if t_res.data else None
        
        # Ambil Statistik Blast
        logs_res = supabase.table('blast_logs').select("*").eq('user_id', user_id).order('created_at', desc=True).limit(20).execute()
        logs = logs_res.data if logs_res.data else []
        
        # Ambil Statistik Jadwal
        sched_res = supabase.table('blast_schedules').select("id", count='exact').eq('user_id', user_id).eq('is_active', True).execute()
        active_schedules = sched_res.count or 0

        return render_template('admin/user_detail.html', 
                               user=user, 
                               tele=tele, 
                               logs=logs,
                               active_schedules=active_schedules,
                               active_page='users')
    except Exception as e:
        return f"Detail Error: {e}"

# --- [FITUR BARU] MANAGE PLAN MANUAL ---
@app.route('/super-admin/update-plan/<int:user_id>', methods=['POST'])
@admin_required
def super_admin_update_plan(user_id):
    """Admin bisa tembak paket langsung (Upgrade/Downgrade)"""
    plan = request.form.get('plan') # Starter, UMKM PRO, Agency
    days = int(request.form.get('days', 30))
    
    try:
        new_expiry = (datetime.now() + timedelta(days=days)).isoformat()
        
        supabase.table('users').update({
            'plan_tier': plan,
            'subscription_end': new_expiry
        }).eq('id', user_id).execute()
        
        flash(f"Berhasil update user #{user_id} ke paket {plan} ({days} hari).", 'success')
    except Exception as e:
        flash(f"Gagal update plan: {e}", 'danger')
        
    return redirect(url_for('super_admin_user_detail', user_id=user_id))

# --- [FITUR BARU] RESET SESI TELEGRAM ---
@app.route('/super-admin/reset-session/<int:user_id>', methods=['POST'])
@admin_required
def super_admin_reset_session(user_id):
    """Paksa logout bot user kalau nyangkut"""
    try:
        # Set is_active = False di database
        supabase.table('telegram_accounts').update({
            'is_active': False,
            'session_string': None # Opsional: Hapus session string biar bersih total
        }).eq('user_id', user_id).execute()
        
        flash(f"Sesi Telegram User #{user_id} berhasil di-reset paksa.", 'warning')
    except Exception as e:
        flash(f"Gagal reset sesi: {e}", 'danger')
        
    return redirect(url_for('super_admin_user_detail', user_id=user_id))

# --- [FITUR LAMA] BAN USER ---
@app.route('/super-admin/ban/<int:user_id>', methods=['POST'])
@admin_required
def ban_user(user_id):
    # ... (Code lama lu biarin aja, udah oke) ...
    # Cuma pastikan return-nya redirect ke halaman detail atau list yang sesuai
    # ...
    try:
        u_data = supabase.table('users').select("is_banned").eq('id', user_id).execute().data
        if not u_data: return redirect(url_for('super_admin_users'))
        
        new_val = not u_data[0].get('is_banned', False)
        supabase.table('users').update({'is_banned': new_val}).eq('id', user_id).execute()
        
        if new_val:
            supabase.table('telegram_accounts').update({'is_active': False}).eq('user_id', user_id).execute()
            
        flash(f"Status User #{user_id} berhasil diubah.", 'success')
    except Exception as e:
        flash(f"Gagal update status: {e}", 'danger')
        
    # Redirect balik ke halaman detail user biar enak
    return redirect(url_for('super_admin_user_detail', user_id=user_id))

@app.route('/super-admin/pricing', methods=['GET', 'POST'])
@admin_required
def super_admin_pricing():
    try:
        # Logic Update Harga
        if request.method == 'POST':
            var_id = request.form.get('id')
            price_raw = request.form.get('price_raw')
            price_disp = request.form.get('price_display')
            
            # Update ke DB
            supabase.table('pricing_variants').update({
                'price_raw': price_raw,
                'price_display': price_disp
            }).eq('id', var_id).execute()
            
            flash('Harga berhasil diupdate!', 'success')
            return redirect(url_for('super_admin_pricing'))

        # Fetch Data (Dengan Error Handling)
        try:
            plans = supabase.table('pricing_plans').select("*, pricing_variants(*)").order('id').execute().data
        except Exception as db_e:
            logger.error(f"DB Error Pricing: {db_e}")
            flash("Gagal ambil data harga. Cek tabel database.", "danger")
            plans = []

        return render_template('admin/pricing.html', plans=plans, active_page='pricing')
    except Exception as e:
        logger.error(f"Page Error Pricing: {e}")
        return f"System Error: {e}"

@app.route('/super-admin/finance')
@admin_required
def super_admin_finance():
    status = request.args.get('status', 'all')
    trx = []
    
    try:
        # Query Kompleks: Transaksi + User + Paket
        query = supabase.table('transactions').select(
            "*, users(email), pricing_variants(price_display, duration_days, pricing_plans(display_name))"
        )
        
        if status != 'all':
            query = query.eq('status', status)
            
        res = query.order('created_at', desc=True).execute()
        trx = res.data if res.data else []
        
    except Exception as e:
        logger.error(f"Finance Query Error: {e}")
        # Jangan return error 500, tapi kasih flash message & list kosong
        flash(f"Gagal memuat transaksi: {str(e)}", "warning")
        
    return render_template('admin/finance.html', transactions=trx, current_filter=status, active_page='finance')

@app.route('/super-admin/finance/approve/<uuid:trx_id>')
@admin_required
def approve_trx(trx_id):
    success, msg = FinanceManager.approve_transaction(str(trx_id), session['user_id'])
    if success: flash(msg, 'success')
    else: flash(msg, 'danger')
    return redirect(url_for('super_admin_finance'))

# ==============================================================================
# SECTION 13.5: FINANCE & PRICING MANAGER
# ==============================================================================
class FinanceManager:
    @staticmethod
    def get_plans_structure():
        """Mengambil struktur lengkap Plan + Varian untuk Frontend"""
        if not supabase: return {}
        
        # Ambil Plans
        plans = supabase.table('pricing_plans').select("*").order('id').execute().data
        
        structured_data = {}
        for p in plans:
            # Ambil Varian untuk plan ini
            variants = supabase.table('pricing_variants').select("*").eq('plan_id', p['id']).order('duration_days').execute().data
            
            structured_data[p['code_name']] = []
            for v in variants:
                structured_data[p['code_name']].append({
                    'id': v['id'],
                    'title': _get_duration_title(v['duration_days']), # Helper function
                    'duration': f"{v['duration_days']} Hari",
                    'price': v['price_display'],
                    'rawPrice': v['price_raw'],
                    'coret': v['price_strike'] or "",
                    'hemat': v['save_badge'] or "",
                    'features': p['features'],
                    'btnText': "Pilih Paket",
                    'bestValue': v['is_best_value']
                })
        return structured_data

    @staticmethod
    def create_transaction(user_id, variant_id, method, proof_file=None):
        """Buat invoice baru"""
        # Ambil harga asli dari DB biar gak dimanipulasi frontend
        var_res = supabase.table('pricing_variants').select("price_raw").eq('id', variant_id).execute()
        if not var_res.data: return False, "Paket tidak valid"
        
        amount = var_res.data[0]['price_raw']
        
        # Upload Bukti (Jika ada)
        proof_path = None
        if proof_file:
            # Logic upload gambar bukti ke Storage / Static folder
            filename = secure_filename(f"proof_{user_id}_{int(time.time())}_{proof_file.filename}")
            proof_path = f"/static/uploads/proofs/{filename}"
            proof_file.save(os.path.join('static/uploads/proofs', filename))

        data = {
            'user_id': user_id,
            'plan_variant_id': variant_id,
            'amount': amount,
            'payment_method': method,
            'proof_url': proof_path,
            'status': 'pending'
        }
        res = supabase.table('transactions').insert(data).execute()
        return True, "Invoice berhasil dibuat"

    @staticmethod
    def approve_transaction(trx_id, admin_id):
        """Admin Acc Pembayaran -> Otomatis perpanjang masa aktif user"""
        try:
            # 1. Ambil Data Transaksi & Varian Paket
            trx = supabase.table('transactions').select("*, pricing_variants(*, pricing_plans(display_name))")\
                .eq('id', trx_id).single().execute().data
            
            if not trx: return False, "Transaksi tidak ditemukan"
            
            user_id = trx['user_id']
            duration = trx['pricing_variants']['duration_days']
            plan_name = trx['pricing_variants']['pricing_plans']['display_name']
            
            # 2. Hitung Expired Baru
            # Cek dulu user sekarang expired kapan
            u_res = supabase.table('users').select("subscription_end").eq('id', user_id).single().execute()
            current_end = u_res.data.get('subscription_end')
            
            now = datetime.utcnow()
            if current_end:
                current_date = datetime.fromisoformat(current_end.replace('Z', ''))
                # Kalau masih aktif, tambah hari dari tanggal expired lama
                start_date = current_date if current_date > now else now
            else:
                start_date = now
                
            new_expiry = (start_date + timedelta(days=duration)).isoformat()
            
            # 3. Update User
            supabase.table('users').update({
                'plan_tier': plan_name,
                'subscription_end': new_expiry
            }).eq('id', user_id).execute()
            
            # 4. Update Transaksi jadi PAID
            supabase.table('transactions').update({
                'status': 'paid',
                'admin_note': f"Approved by Admin #{admin_id} at {now}"
            }).eq('id', trx_id).execute()
            
            # 5. Kirim Notif ke User
            send_telegram_alert(user_id, f"✅ **Pembayaran Diterima!**\nPaket {plan_name} aktif sampai {new_expiry[:10]}.")
            
            return True, "Sukses Approve"
        except Exception as e:
            logger.error(f"Approval Error: {e}")
            return False, str(e)

def _get_duration_title(days):
    if days <= 3: return "Trial"
    if days <= 35: return "Bulanan"
    if days <= 100: return "Quarterly"
    return "Semester"

# ==========================================
# SECTION 14 : IMPORT & EXPORT CSV
# ==========================================

@app.route('/import_crm_csv', methods=['POST'])
@login_required
def import_crm_csv():
    """
    [UPGRADE] Import Database Pelanggan via CSV (Smart Handling).
    Fitur:
    - Support UTF-8 BOM (Excel Friendly)
    - Auto-Normalize Headers (Case insensitive)
    - Integrasi Source Phone (Folder System)
    - Batch Processing Anti-Timeout
    """
    user_id = session['user_id']
    
    # 1. Validasi Request Dasar
    if 'file' not in request.files:
        return jsonify({"status": "error", "message": "Tidak ada file yang diunggah."})
    
    file = request.files['file']
    source_phone = request.form.get('source_phone') # Tangkap pilihan folder akun

    if file.filename == '':
        return jsonify({"status": "error", "message": "Nama file kosong."})
    
    if not source_phone:
        return jsonify({"status": "error", "message": "Harap pilih Database Akun Tujuan terlebih dahulu."})

    if not file.filename.lower().endswith('.csv'):
        return jsonify({"status": "error", "message": "Format file harus .csv"})

    try:
        # 2. Smart Encoding Reader (Handle Excel BOM issues)
        # File CSV dari Excel seringkali punya karakter 'BOM' di awal yang bikin error
        # Kita baca binary dulu, lalu decode paksa
        file_content = file.stream.read()
        
        try:
            decoded_content = file_content.decode('utf-8-sig') # Best for Excel
        except UnicodeDecodeError:
            try:
                decoded_content = file_content.decode('latin-1') # Fallback
            except:
                return jsonify({"status": "error", "message": "Encoding file tidak dikenali. Gunakan UTF-8."})

        # Siapkan Stream IO
        stream = io.StringIO(decoded_content, newline=None)
        csv_input = csv.DictReader(stream)

        # 3. Normalisasi Header (Biar gak sensitif huruf besar/kecil)
        # Kita bikin map key standar: 'user_id', 'username', 'first_name'
        # Jadi user upload header 'User ID' atau 'USER_ID' tetap masuk
        normalized_map = {}
        if csv_input.fieldnames:
            for field in csv_input.fieldnames:
                clean_field = field.strip().lower().replace(" ", "_")
                if "user" in clean_field and "id" in clean_field:
                    normalized_map['user_id'] = field
                elif "user" in clean_field and "name" in clean_field:
                    normalized_map['username'] = field
                elif "name" in clean_field or "nama" in clean_field:
                    normalized_map['first_name'] = field

        # Cek Header Wajib
        if 'user_id' not in normalized_map:
            return jsonify({
                "status": "error", 
                "message": "Format CSV Tidak Valid! Tidak ditemukan kolom 'user_id' atau 'User ID'."
            })

        valid_rows = []
        errors = 0
        
        # 4. Iterasi Data
        for row in csv_input:
            try:
                # Ambil data pake map yang sudah dinormalisasi
                raw_uid = row.get(normalized_map['user_id'], '').strip()
                
                # Validasi ID (Harus Angka)
                if not raw_uid.isdigit():
                    errors += 1
                    continue 
                
                # Ambil Username (Optional)
                raw_username = None
                if 'username' in normalized_map:
                    val = row.get(normalized_map['username'], '').strip()
                    # Bersihkan '@' atau link t.me/ jika user iseng masukin itu
                    raw_username = val.replace("@", "").replace("https://t.me/", "") if val else None

                # Ambil Nama (Optional)
                raw_name = "Imported Contact"
                if 'first_name' in normalized_map:
                    val = row.get(normalized_map['first_name'], '').strip()
                    if val: raw_name = val

                # Susun Data Bersih
                clean_data = {
                    "owner_id": user_id,
                    "user_id": int(raw_uid),
                    "username": raw_username,
                    "first_name": raw_name,
                    "source_phone": source_phone, # <--- PENTING: Masuk ke folder akun ini
                    "last_interaction": datetime.utcnow().isoformat(),
                    "created_at": datetime.utcnow().isoformat()
                }
                valid_rows.append(clean_data)
                
            except Exception:
                errors += 1
                continue

        if not valid_rows:
            return jsonify({"status": "error", "message": "File terbaca kosong atau semua User ID tidak valid."})

        # 5. Batch Upsert ke Database (Supabase)
        # Insert per 1000 baris biar server gak timeout
        batch_size = 1000
        total_inserted = 0
        
        for i in range(0, len(valid_rows), batch_size):
            batch = valid_rows[i:i + batch_size]
            # Upsert: Update jika ID sudah ada, Insert jika belum
            supabase.table('tele_users').upsert(batch, on_conflict="owner_id, user_id").execute()
            total_inserted += len(batch)

        # 6. Response Sukses
        msg = f"Sukses import {total_inserted} kontak ke database {source_phone}."
        if errors > 0:
            msg += f" ({errors} baris diabaikan karena format salah)"
            
        return jsonify({"status": "success", "message": msg})

    except Exception as e:
        logger.error(f"CSV Import Critical Error: {e}")
        return jsonify({"status": "error", "message": f"Server Error: {str(e)}"})

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
    
    #NYALAKAN SATPAM
    AutoReplyService.start()
    
# Start Background Pinger
start_self_ping()
    
# --- [BATAS SUCI] --
BOT_POLLING_ENABLED = os.getenv("ENABLE_BOT_POLLING", "false").lower() in {"1", "true", "yes", "on"}
if BOT_POLLING_ENABLED and os.getenv("NOTIF_BOT_TOKEN"):
    try:
        # Jalankan bot di thread terpisah biar ga ganggu website
        bot_thread = threading.Thread(target=run_bot_process, name="TelegramBot", daemon=True)
        bot_thread.start()
        print("✅ [BOOT] Sinyal Start Bot Terkirim.", flush=True)
    except Exception as e:
        print(f"❌ [BOOT] Gagal Start Bot: {e}", flush=True)
else:
    logger.info("ℹ️ Telegram polling bot nonaktif. Set ENABLE_BOT_POLLING=true jika ingin menyalakan polling di proses web ini.")

# --- DEBUG ROUTE (HAPUS NANTI KALO UDAH FIX) ---
@app.route('/debug-pricing')
def debug_pricing():
    try:
        # 1. Cek Koneksi DB
        if not supabase: return "Supabase Offline"
        
        # 2. Cek Data Raw dari DB
        plans = supabase.table('pricing_plans').select("*").execute().data
        variants = supabase.table('pricing_variants').select("*").execute().data
        
        # 3. Cek Output FinanceManager
        manager_output = FinanceManager.get_plans_structure()
        
        return jsonify({
            "status": "Check",
            "db_plans_count": len(plans),
            "db_variants_count": len(variants),
            "manager_output": manager_output,
            "raw_plans": plans
        })
    except Exception as e:
        # INI YANG KITA CARI: ERROR ASLINYA
        import traceback
        return f"<h1>🔥 ERROR KETEMU:</h1><pre>{traceback.format_exc()}</pre>"
        

# ... (Baru masuk ke app.run) ...
if __name__ == '__main__':
    app.run(debug=True, port=5000, use_reloader=False) 
    # use_reloader=False PENTING biar bot ga jalan 2x (double process)
