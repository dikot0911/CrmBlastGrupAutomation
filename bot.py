import os
import logging
from datetime import datetime
import pytz
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
from supabase import create_client

# --- CONFIG ---
BOT_TOKEN = os.getenv("NOTIF_BOT_TOKEN") 
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- START COMMAND (DEEP LINKING HANDLER) ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    args = context.args # Ini ngambil token dari link (t.me/bot?start=TOKEN)
    
    # 1. Cek apakah user bawa token valid?
    if args and len(args[0]) > 10:
        token = args[0]
        try:
            # Cari user yang punya token ini
            res = supabase.table('users').select("id, email").eq('verification_token', token).execute()
            
            if res.data:
                db_user = res.data[0]
                
                # UPDATE DB: Simpan Chat ID & Hapus Token (One-time use)
                supabase.table('users').update({
                    'notification_chat_id': chat_id,
                    'verification_token': None 
                }).eq('id', db_user['id']).execute()
                
                await update.message.reply_text(
                    f"✅ **KONEKSI BERHASIL!**\n\n"
                    f"Halo **{db_user['email']}**,\n"
                    f"Akun Telegram ini telah terhubung ke Dashboard.\n"
                    f"Anda akan menerima laporan otomatis di sini.",
                    parse_mode='Markdown'
                )
                # Tampilkan Menu
                await show_main_menu(update, db_user['id'])
                return
            else:
                await update.message.reply_text("❌ Token kadaluarsa atau tidak valid. Silakan klik ulang tombol di Dashboard.")
                return
        except Exception as e:
            print(f"Error linking: {e}")
            await update.message.reply_text("❌ Terjadi kesalahan sistem.")
            return

    # 2. Kalau gak bawa token, cek apakah udah terdaftar?
    try:
        res = supabase.table('users').select("id").eq('notification_chat_id', chat_id).execute()
        if res.data:
            await show_main_menu(update, res.data[0]['id'])
        else:
            await update.message.reply_text(
                "👋 **Selamat Datang!**\n\n"
                "Bot ini khusus untuk notifikasi User SaaS.\n"
                "Silakan login ke Web Dashboard > Profil > Klik **'Hubungkan Telegram'**.",
                parse_mode='Markdown'
            )
    except:
        pass

# --- MENU TAMPILAN ---
async def show_main_menu(update: Update, user_id):
    keyboard = [
        [InlineKeyboardButton("📅 Jadwal Mendatang", callback_data=f'schedule_{user_id}')],
        [InlineKeyboardButton("💎 Cek Status Paket", callback_data=f'plan_{user_id}')],
        [InlineKeyboardButton("📞 Bantuan Admin", callback_data='help')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = "🤖 **CONTROL PANEL**\nApa yang ingin Anda cek?"
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')

# --- HANDLER TOMBOL ---
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data.startswith('plan_'):
        user_id = data.split('_')[1]
        res = supabase.table('users').select("plan_tier, subscription_end").eq('id', user_id).execute()
        if res.data:
            u = res.data[0]
            plan = u.get('plan_tier', 'Starter')
            end = u.get('subscription_end', 'Selamanya')
            if end and end != 'Selamanya': end = end[:10]
            
            text = (
                f"💎 **INFO PAKET**\n\n"
                f"📦 Paket: **{plan}**\n"
                f"⏳ Expired: `{end}`\n\n"
                f"Mau upgrade? Buka dashboard web."
            )
            key = [[InlineKeyboardButton("🔙 Menu Utama", callback_data=f'menu_{user_id}')]]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(key), parse_mode='Markdown')

    elif data.startswith('schedule_'):
        user_id = data.split('_')[1]
        res = supabase.table('blast_schedules').select("*").eq('user_id', user_id).eq('is_active', True).execute()
        
        if not res.data:
            text = "📭 Tidak ada jadwal aktif."
        else:
            text = "📅 **JADWAL AKTIF ANDA:**\n\n"
            for s in res.data:
                text += f"⏰ Jam `{s['run_hour']:02d}:{s['run_minute']:02d}` WIB\n"
        
        key = [[InlineKeyboardButton("🔙 Menu Utama", callback_data=f'menu_{user_id}')]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(key), parse_mode='Markdown')

    elif data == 'help':
        text = "📞 Hubungi Admin Support di:\n@UsernameAdminLu"
        # Gak perlu back button biar chat tetep bersih
        await query.edit_message_text(text, parse_mode='Markdown')
        
    elif data.startswith('menu_'):
        user_id = data.split('_')[1]
        await show_main_menu(update, user_id)

# --- RUN BOT ---
if __name__ == '__main__':
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    print("Bot Notifikasi Jalan...")
    app.run_polling()
