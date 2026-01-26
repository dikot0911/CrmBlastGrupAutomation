import os
import asyncio
import logging
import math
from datetime import datetime, timedelta
import pytz
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, 
    CommandHandler, 
    CallbackQueryHandler, 
    ContextTypes, 
    MessageHandler, 
    filters
)
from telegram.error import BadRequest, Forbidden
from supabase import create_client

# ==============================================================================
# CONFIGURATION & SETUP
# ==============================================================================

# Load Environment Variables
BOT_TOKEN = os.getenv("NOTIF_BOT_TOKEN") 
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Fail-safe mechanism
if not BOT_TOKEN:
    print("❌ FATAL: NOTIF_BOT_TOKEN is missing!")
    BOT_TOKEN = "DUMMY_TOKEN_TO_PREVENT_CRASH"

# Initialize Database
try:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    print(f"❌ Database Connection Failed in Bot: {e}")
    supabase = None

# Logging Configuration
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Constants
ITEMS_PER_PAGE = 5  # Jumlah item per halaman untuk pagination

# ==============================================================================
# HELPER FUNCTIONS
# ==============================================================================

def get_user_by_chat_id(chat_id):
    """Mencari user ID database berdasarkan Chat ID Telegram"""
    if not supabase: return None
    try:
        res = supabase.table('users').select("id, email, plan_tier, is_admin").eq('notification_chat_id', chat_id).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"Error fetching user: {e}")
        return None

def format_date(iso_str):
    """Format tanggal ISO ke format Indonesia yang manusiawi"""
    if not iso_str: return "-"
    try:
        dt = datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
        dt = dt.astimezone(pytz.timezone('Asia/Jakarta'))
        return dt.strftime("%d %b %Y, %H:%M WIB")
    except:
        return iso_str

def get_pagination_markup(current_page, total_pages, prefix, extra_data=""):
    """
    Membuat tombol navigasi (Previous | 1/5 | Next)
    prefix: identitas menu (misal: 'logs_', 'acc_')
    extra_data: data tambahan id (misal user_id)
    """
    buttons = []
    nav_row = []
    
    if current_page > 1:
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"{prefix}_page_{current_page-1}_{extra_data}"))
    
    nav_row.append(InlineKeyboardButton(f"📄 {current_page}/{total_pages}", callback_data="noop"))
    
    if current_page < total_pages:
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"{prefix}_page_{current_page+1}_{extra_data}"))
        
    buttons.append(nav_row)
    return buttons

# ==============================================================================
# MAIN HANDLERS (START & MENUS)
# ==============================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler perintah /start"""
    chat_id = update.effective_chat.id
    args = context.args
    
    # --- SCENARIO 1: DEEP LINKING (VERIFIKASI WEB) ---
    if args and len(args[0]) > 10:
        token = args[0]
        try:
            # Cari user dengan token verifikasi ini
            res = supabase.table('users').select("id, email").eq('verification_token', token).execute()
            
            if res.data:
                db_user = res.data[0]
                # Update DB: Link Chat ID & Hapus Token
                supabase.table('users').update({
                    'notification_chat_id': chat_id,
                    'verification_token': None 
                }).eq('id', db_user['id']).execute()
                
                await update.message.reply_text(
                    f"✅ **KONEKSI SUKSES!**\n\n"
                    f"Selamat datang **{db_user['email']}**,\n"
                    f"Bot ini sekarang terhubung ke Dashboard Anda.\n"
                    f"Anda akan menerima laporan Blast dan notifikasi sistem di sini.",
                    parse_mode='Markdown'
                )
                await show_dashboard(update, db_user['id'])
                return
            else:
                await update.message.reply_text("❌ **Link Kadaluarsa!**\nSilakan kembali ke dashboard web dan klik tombol 'Hubungkan Telegram' lagi.")
                return
        except Exception as e:
            logger.error(f"Linking Error: {e}")
            await update.message.reply_text("❌ Terjadi kesalahan sistem.")
            return

    # --- SCENARIO 2: NORMAL START ---
    user = get_user_by_chat_id(chat_id)
    if user:
        await show_dashboard(update, user['id'])
    else:
        await update.message.reply_text(
            "👋 **Selamat Datang!**\n\n"
            "Bot ini adalah asisten notifikasi untuk **BlastPro SaaS**.\n"
            "Untuk menggunakan bot ini, Anda harus menghubungkannya melalui Dashboard Web.\n\n"
            "1. Login ke Web Dashboard\n"
            "2. Masuk ke menu **Profil**\n"
            "3. Klik **Hubungkan Telegram**",
            parse_mode='Markdown'
        )

async def show_dashboard(update: Update, user_id):
    """Menampilkan Menu Utama Dashboard"""
    # Fetch Data Ringkas
    try:
        # Cek Paket
        u_res = supabase.table('users').select("plan_tier, wallet_balance").eq('id', user_id).execute()
        user_data = u_res.data[0]
        
        # Cek Akun Aktif
        acc_res = supabase.table('telegram_accounts').select("id", count='exact').eq('user_id', user_id).eq('is_active', True).execute()
        active_acc = acc_res.count or 0
        
        # Cek Jadwal Hari Ini
        # (Simplified query for demo)
        sched_res = supabase.table('blast_schedules').select("id", count='exact').eq('user_id', user_id).eq('is_active', True).execute()
        active_sched = sched_res.count or 0

    except:
        user_data = {'plan_tier': 'Unknown', 'wallet_balance': 0}
        active_acc = 0
        active_sched = 0

    text = (
        f"🤖 **DASHBOARD CONTROL**\n"
        f"─────────────────\n"
        f"👑 **Paket:** {user_data['plan_tier']}\n"
        f"📱 **Akun Aktif:** {active_acc}\n"
        f"📅 **Jadwal Aktif:** {active_sched}\n"
        f"💰 **Saldo:** Rp {user_data['wallet_balance']:,}\n"
        f"─────────────────\n"
        f"Pilih menu di bawah untuk detail:"
    )

    keyboard = [
        [
            InlineKeyboardButton("📊 Laporan Blast", callback_data=f"menu_reports_{user_id}"),
            InlineKeyboardButton("📱 Akun Saya", callback_data=f"menu_accounts_{user_id}")
        ],
        [
            InlineKeyboardButton("📅 Cek Jadwal", callback_data=f"menu_schedules_{user_id}"),
            InlineKeyboardButton("💰 Wallet & Ref", callback_data=f"menu_wallet_{user_id}")
        ],
        [InlineKeyboardButton("🔄 Refresh Dashboard", callback_data=f"dashboard_refresh_{user_id}")]
    ]

    # Handle update via Message or Callback
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

# ==============================================================================
# FEATURE: REPORT & LOGS SYSTEM (THE REQUESTED UPGRADE)
# ==============================================================================

async def show_blast_reports(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id, page=1):
    """Menampilkan List Riwayat Blast Terakhir"""
    query = update.callback_query
    
    try:
        # Ambil total logs
        count_res = supabase.table('blast_logs').select("id", count='exact').eq('user_id', user_id).execute()
        total_items = count_res.count or 0
        total_pages = math.ceil(total_items / ITEMS_PER_PAGE)
        
        # Pagination Database
        start = (page - 1) * ITEMS_PER_PAGE
        end = start + ITEMS_PER_PAGE - 1
        
        logs = supabase.table('blast_logs').select("*").eq('user_id', user_id)\
            .order('created_at', desc=True).range(start, end).execute().data
            
        if not logs:
            await query.edit_message_text(
                "📭 **Belum ada riwayat blast.**\nMulailah mengirim pesan dari dashboard web.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kembali", callback_data=f"dashboard_refresh_{user_id}")]])
            )
            return

        text = f"📊 **RIWAYAT AKTIVITAS BLAST**\nPage {page}/{total_pages}\n\n"
        keyboard = []

        for log in logs:
            status_icon = "✅" if log['status'] == 'SUCCESS' else "❌"
            time_str = format_date(log['created_at'])
            # Potong nama grup kalo kepanjangan
            grp_name = (log['group_name'][:20] + '..') if len(log.get('group_name', '')) > 20 else log.get('group_name', 'Unknown')
            
            text += f"{status_icon} **{grp_name}**\n"
            text += f"   └ 🕒 {time_str}\n"
            
            # Kalau GAGAL, kasih tombol cek error
            if log['status'] != 'SUCCESS':
                # Callback data: view_error_LOGID
                keyboard.append([InlineKeyboardButton(f"🔍 Cek Error: {grp_name}", callback_data=f"err_detail_{log['id']}")])
        
        text += "\n_Klik tombol di bawah untuk navigasi._"
        
        # Tambah tombol navigasi
        nav_buttons = get_pagination_markup(page, total_pages, "report", user_id)
        for row in nav_buttons: keyboard.append(row)
        
        keyboard.append([InlineKeyboardButton("🔙 Kembali ke Menu", callback_data=f"dashboard_refresh_{user_id}")])
        
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    except Exception as e:
        logger.error(f"Report Error: {e}")
        await query.answer("Gagal memuat laporan.", show_alert=True)

async def show_error_detail(update: Update, log_id):
    """Menampilkan detail kenapa blast gagal (Inline Detail)"""
    query = update.callback_query
    
    try:
        res = supabase.table('blast_logs').select("*").eq('id', log_id).single().execute()
        if not res.data:
            await query.answer("Data log tidak ditemukan.", show_alert=True)
            return
            
        log = res.data
        
        text = (
            f"❌ **DETAIL KEGAGALAN**\n"
            f"─────────────────\n"
            f"📂 **Target:** {log.get('group_name')}\n"
            f"🆔 **ID Grup:** `{log.get('group_id')}`\n"
            f"🕒 **Waktu:** {format_date(log['created_at'])}\n"
            f"─────────────────\n"
            f"⚠️ **Pesan Error:**\n"
            f"`{log.get('error_message') or 'Unknown Error'}`\n\n"
            f"_Saran: Jika error FloodWait, tunggu beberapa saat. Jika PeerIdInvalid, pastikan bot sudah join grup._"
        )
        
        # Tombol Back ke list report
        key = [[InlineKeyboardButton("🔙 Kembali ke List", callback_data=f"menu_reports_{log['user_id']}")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(key), parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"Error detail: {e}")
        await query.answer("Error system.", show_alert=True)

# ==============================================================================
# FEATURE: ACCOUNT MANAGER
# ==============================================================================

async def show_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id):
    """Menampilkan status akun Telegram yang terhubung"""
    query = update.callback_query
    
    try:
        accs = supabase.table('telegram_accounts').select("*").eq('user_id', user_id).execute().data
        
        if not accs:
            text = "📱 **AKUN TELEGRAM**\n\nBelum ada akun yang terhubung."
        else:
            text = f"📱 **AKUN TERHUBUNG ({len(accs)}/3)**\n\n"
            for acc in accs:
                status = "🟢 Aktif" if acc['is_active'] else "🔴 Mati (Relogin)"
                phone = acc['phone_number']
                name = acc.get('first_name', 'Unknown')
                text += f"👤 **{name}**\n   └ 📞 `{phone}` • {status}\n\n"
        
        keyboard = [
            [InlineKeyboardButton("➕ Tambah Akun (Web)", url="https://crmblastgrupautomation.onrender.com/dashboard/connection")],
            [InlineKeyboardButton("🔙 Kembali", callback_data=f"dashboard_refresh_{user_id}")]
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"Acc manager error: {e}")

# ==============================================================================
# FEATURE: WALLET & REFERRAL
# ==============================================================================

async def show_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id):
    query = update.callback_query
    
    try:
        res = supabase.table('users').select("wallet_balance, referral_code, plan_tier").eq('id', user_id).single().execute()
        u = res.data
        
        # Link Referral
        bot_username = context.bot.username
        ref_link = f"https://crmblastgrupautomation.onrender.com/register?ref={u['referral_code']}"
        
        text = (
            f"💰 **DOMPET & AFILIASI**\n"
            f"─────────────────\n"
            f"💵 **Saldo:** Rp {u['wallet_balance']:,}\n"
            f"🎫 **Kode Ref:** `{u['referral_code']}`\n"
            f"─────────────────\n"
            f"🔗 **Link Referral Anda:**\n"
            f"`{ref_link}`\n\n"
            f"Bagikan link ini dan dapatkan komisi dari setiap pendaftar baru!"
        )
        
        keyboard = [[InlineKeyboardButton("🔙 Kembali", callback_data=f"dashboard_refresh_{user_id}")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"Wallet error: {e}")

# ==============================================================================
# MAIN CALLBACK HANDLER (ROUTER)
# ==============================================================================

async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Pusat Logika Tombol Inline"""
    query = update.callback_query
    data = query.data
    
    # Jangan lupa answer() biar loading muter2 ilang
    try:
        await query.answer()
    except: pass # Ignore if already answered

    # Parsing Data (Format: action_userid_param)
    parts = data.split('_')
    action = parts[0]
    
    # 1. REFRESH DASHBOARD
    if data.startswith("dashboard_refresh_"):
        user_id = parts[2]
        await show_dashboard(update, user_id)

    # 2. MENU: REPORTS (Pagination)
    elif data.startswith("menu_reports_") or data.startswith("report_page_"):
        # Format: menu_reports_USERID atau report_page_PAGE_USERID
        if action == "menu":
            user_id = parts[2]
            page = 1
        else: # report_page_2_USERID
            page = int(parts[2])
            user_id = parts[3]
            
        await show_blast_reports(update, context, user_id, page)

    # 3. DETAIL ERROR
    elif data.startswith("err_detail_"):
        log_id = parts[2]
        await show_error_detail(update, log_id)

    # 4. MENU: ACCOUNTS
    elif data.startswith("menu_accounts_"):
        user_id = parts[2]
        await show_accounts(update, context, user_id)

    # 5. MENU: WALLET
    elif data.startswith("menu_wallet_"):
        user_id = parts[2]
        await show_wallet(update, context, user_id)
        
    # 6. MENU: SCHEDULES
    elif data.startswith("menu_schedules_"):
        user_id = parts[2]
        # Logic simple check schedule
        res = supabase.table('blast_schedules').select("*").eq('user_id', user_id).eq('is_active', True).execute()
        text = "📅 **JADWAL AKTIF ANDA:**\n\n"
        if not res.data:
            text += "_Tidak ada jadwal aktif._"
        else:
            for s in res.data:
                text += f"⏰ **{s['run_hour']:02d}:{s['run_minute']:02d} WIB**\n"
        
        key = [[InlineKeyboardButton("🔙 Kembali", callback_data=f"dashboard_refresh_{user_id}")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(key), parse_mode='Markdown')

    # 7. HELP ADMIN
    elif data == 'help_admin':
        await query.edit_message_text(
            "📞 **BANTUAN ADMIN**\n\nSilakan hubungi: @dramamu_admin\nJam Operasional: 09:00 - 21:00 WIB",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kembali", callback_data=f"dashboard_refresh_{user_id}")]])
        )

# ==============================================================================
# EXTERNAL TRIGGER (DIPANGGIL DARI APP.PY)
# ==============================================================================
# Fungsi ini tidak dipanggil langsung oleh bot, tapi oleh Flask App
# saat ada event (misal: Broadcast Selesai)

async def send_blast_report_card(app_context, user_id, success, failed):
    """
    Fungsi Spesial: Mengirim Kartu Laporan Cantik setelah Broadcast selesai.
    Harus dipanggil secara async dari app.py.
    """
    try:
        res = supabase.table('users').select("notification_chat_id").eq('id', user_id).execute()
        if not res.data or not res.data[0]['notification_chat_id']: return
        
        chat_id = res.data[0]['notification_chat_id']
        
        text = (
            f"🚀 **BROADCAST SELESAI!**\n"
            f"─────────────────\n"
            f"✅ **Berhasil:** {success}\n"
            f"❌ **Gagal:** {failed}\n"
            f"📊 **Total:** {success + failed}\n"
            f"─────────────────\n"
            f"_Klik tombol di bawah untuk melihat detail error (jika ada)._"
        )
        
        # Tombol langsung ke Menu Report
        keyboard = [[InlineKeyboardButton("🔍 Lihat Detail & Error", callback_data=f"menu_reports_{user_id}")]]
        
        await app_context.bot.send_message(
            chat_id=chat_id, 
            text=text, 
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Failed sending report card: {e}")

# ==============================================================================
# MAIN EXECUTION
# ==============================================================================

def run_bot_process():
    """Entry Point untuk Thread di Flask"""
    if not BOT_TOKEN or BOT_TOKEN == "DUMMY_TOKEN":
        print("❌ BOT STOP: Token belum diisi.")
        return

    # Create Loop Baru (Wajib untuk Threading)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    print("🚀 Enterprise Bot Started (Background Mode)...")
    
    # Build Application
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Add Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(callback_router))
    
    # Run Polling (Blocking di Thread ini)
    app.run_polling(stop_signals=None, drop_pending_updates=True)

if __name__ == '__main__':
    run_bot_process()
