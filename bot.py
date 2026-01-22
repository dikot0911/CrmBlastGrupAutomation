import os
import asyncio
import logging
from datetime import datetime
import pytz
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
from supabase import create_client

# --- CONFIG ---
# Pastikan lu udah masukin ini di .env lu ya!
BOT_TOKEN = os.getenv("NOTIF_BOT_TOKEN") 
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Cek token dulu biar ga error diem-diem
if not BOT_TOKEN:
    print("❌ FATAL: NOTIF_BOT_TOKEN belum diisi di .env")
    # Biar ga crash pas import, kita kasih dummy kalau kosong (tapi nanti error pas run)
    BOT_TOKEN = "DUMMY_TOKEN"

# --- SUPABASE INIT ---
try:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
except:
    print("❌ Warning: Database connection failed in bot.py")

# --- LOGGING ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# --- START COMMAND (DEEP LINKING HANDLER) ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    args = context.args # Ini ngambil token dari link (t.me/bot?start=TOKEN)
    
    # 1. Cek apakah user klik link dari Website? (Bawa Token)
    if args and len(args[0]) > 5:
        token = args[0]
        try:
            # Cari user di database yang punya token ini
            res = supabase.table('users').select("id, email").eq('verification_token', token).execute()
            
            if res.data:
                db_user = res.data[0]
                
                # UPDATE DB: Simpan Chat ID & Hapus Token (Biar tokennya gak bisa dipake orang lain)
                supabase.table('users').update({
                    'notification_chat_id': chat_id,
                    'verification_token': None 
                }).eq('id', db_user['id']).execute()
                
                await update.message.reply_text(
                    f"✅ **KONEKSI SUKSES!**\n\n"
                    f"Halo **{db_user['email']}**,\n"
                    f"Akun Telegram ini telah terhubung ke Dashboard SaaS.\n"
                    f"Anda akan menerima notifikasi otomatis (Jadwal, Status, Billing) di sini.",
                    parse_mode='Markdown'
                )
                # Langsung tampilin menu
                await show_main_menu(update, db_user['id'])
                return
            else:
                await update.message.reply_text("❌ Token kadaluarsa atau tidak valid. Silakan klik tombol 'Hubungkan' lagi di Web.")
                return
        except Exception as e:
            print(f"Error linking: {e}")
            await update.message.reply_text("❌ Terjadi kesalahan sistem saat verifikasi.")
            return

    # 2. Kalau user cuma iseng chat /start (Gak bawa token)
    try:
        # Cek dulu dia udah terdaftar belum?
        res = supabase.table('users').select("id").eq('notification_chat_id', chat_id).execute()
        if res.data:
            # Udah terdaftar, kasih menu
            await show_main_menu(update, res.data[0]['id'])
        else:
            # Belum terdaftar, suruh ke web
            await update.message.reply_text(
                "👋 **Selamat Datang di Bot Notifikasi!**\n\n"
                "Bot ini khusus untuk user SaaS.\n"
                "Silakan login ke **Dashboard Web > Profil**, lalu klik tombol **'Hubungkan Telegram'**.",
                parse_mode='Markdown'
            )
    except:
        pass

# --- TAMPILAN MENU UTAMA ---
async def show_main_menu(update: Update, user_id):
    keyboard = [
        [InlineKeyboardButton("📅 Jadwal Berikutnya", callback_data=f'schedule_{user_id}')],
        [InlineKeyboardButton("💎 Cek Paket Saya", callback_data=f'plan_{user_id}')],
        [InlineKeyboardButton("📞 Hubungi Admin", callback_data='help')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = "🤖 **CONTROL PANEL**\nApa yang ingin Anda cek hari ini?"
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')

# --- HANDLER TOMBOL KETIKA DIKLIK ---
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer() # Wajib biar loading di tombol ilang
    data = query.data
    
    # 1. Cek Paket
    if data.startswith('plan_'):
        user_id = data.split('_')[1]
        res = supabase.table('users').select("plan_tier, subscription_end").eq('id', user_id).execute()
        if res.data:
            u = res.data[0]
            plan = u.get('plan_tier', 'Starter')
            # Format tanggal biar enak dibaca
            end_raw = u.get('subscription_end')
            end = end_raw[:10] if end_raw else 'Selamanya (Free)'
            
            text = (
                f"💎 **INFO PAKET ANDA**\n\n"
                f"📦 Level: **{plan}**\n"
                f"⏳ Expired: `{end}`\n\n"
                f"_Ingin upgrade? Silakan buka menu Billing di Web._"
            )
            key = [[InlineKeyboardButton("🔙 Kembali", callback_data=f'menu_{user_id}')]]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(key), parse_mode='Markdown')

    # 2. Cek Jadwal
    elif data.startswith('schedule_'):
        user_id = data.split('_')[1]
        # Ambil jadwal aktif
        res = supabase.table('blast_schedules').select("*").eq('user_id', user_id).eq('is_active', True).order('run_hour').execute()
        
        if not res.data:
            text = "📭 **Tidak ada jadwal aktif.**\nJadwal blast Anda kosong."
        else:
            text = "📅 **JADWAL AKTIF:**\n"
            for s in res.data:
                menit = f"{s['run_minute']:02d}"
                text += f"• ⏰ `{s['run_hour']}:{menit}` WIB\n"
        
        key = [[InlineKeyboardButton("🔙 Kembali", callback_data=f'menu_{user_id}')]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(key), parse_mode='Markdown')

    # 3. Help Admin
    elif data == 'help':
        text = "📞 **BANTUAN ADMIN**\n\nSilakan hubungi:\n@dramamu_admin"
        key = [[InlineKeyboardButton("🔙 Kembali", callback_data=f'menu_0')]] # ID 0 dummy, nanti di-override logic start
        # Disini kita gak kasih tombol back ke menu user krn user_id ga kebawa di data 'help'
        # Edit text aja cukup
        await query.edit_message_text(text, parse_mode='Markdown')
        
    # 4. Tombol Back
    elif data.startswith('menu_'):
        user_id = data.split('_')[1]
        await show_main_menu(update, user_id)

# --- FUNGSI PENGGERAK UTAMA ---
def run_bot_process():
    """
    Fungsi ini akan dipanggil oleh app.py biar jalan di background.
    """
    if not BOT_TOKEN or BOT_TOKEN == "DUMMY_TOKEN":
        print("❌ BOT STOP: Token belum diisi.")
        return

    # Buat loop baru khusus untuk thread ini
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    print("🚀 Bot Telegram Berhasil Dinyalakan (Background Mode)...")
    
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Daftarkan Handler
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("connect", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    
    # [FIX PENTING] Matikan stop_signals agar jalan mulus di Thread
    application.run_polling(stop_signals=None) 

if __name__ == '__main__':
    run_bot_process()
