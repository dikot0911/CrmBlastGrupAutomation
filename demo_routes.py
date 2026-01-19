from flask import Blueprint, render_template, redirect, url_for
from datetime import datetime, timedelta
import logging

# Blueprint Setup
demo_bp = Blueprint('demo', __name__)
logger = logging.getLogger("BabaSaaSCore")

# --- DATA PALSU (DUMMY) ---
class DemoUserEntity:
    def __init__(self):
        self.id = 12345
        self.email = "demo.user@blastpro.id"
        self.is_admin = False
        self.is_banned = False
        self.created_at = datetime.utcnow()
        self.telegram_account = type('TeleInfo', (object,), {
            'phone_number': '+6281299998888', # Nomor Palsu
            'is_active': True,
            'created_at': datetime.utcnow(),
            'tele_users_count': 888
        })

def get_demo_data():
    now = datetime.utcnow()
    return {
        'logs': [
            {'id': 1, 'group_name': 'KASKUS KAMBOJA KPS', 'status': 'success', 'created_at': (now).isoformat()},
            {'id': 2, 'group_name': 'Fjb Kaskus Kps', 'status': 'success', 'created_at': (now - timedelta(minutes=12)).isoformat()},
            {'id': 3, 'group_name': 'Info Loker Kamboja', 'status': 'success', 'created_at': (now - timedelta(minutes=45)).isoformat()},
            {'id': 4, 'group_name': 'ALFAMART KPS POIPET', 'status': 'success', 'created_at': (now - timedelta(minutes=12)).isoformat()},
            {'id': 5, 'group_name': 'Kaskus Cambodia', 'status': 'success', 'created_at': (now - timedelta(minutes=12)).isoformat()},
        ],
        # [FIX] Tambahkan Dummy Template (Biar Dropdown gak error)
        'templates': [
            {'id': 1, 'name': 'Promo Diskon 50%', 'message_text': 'Halo kak, dapatkan diskon 50% khusus hari ini!'},
            {'id': 2, 'name': 'Restock Barang', 'message_text': 'Barang sudah ready stok lagi ya kak, silahkan order.'},
            {'id': 3, 'name': 'Ucapan Pagi', 'message_text': 'Selamat pagi kak, semangat beraktivitas!'}
        ],
        'schedules': [
            {'id': 1, 'run_hour': 8, 'run_minute': 0, 'is_active': True, 'template_id': 3, 'template_name': 'Ucapan Pagi'},
            {'id': 2, 'run_hour': 12, 'run_minute': 0, 'is_active': True, 'template_id': 1, 'template_name': 'Promo Diskon 50%'},
            {'id': 3, 'run_hour': 18, 'run_minute': 30, 'is_active': True, 'template_id': 2, 'template_name': 'Restock Barang'},
        ],
        'targets': [
            {'id': 1, 'group_name': 'KASKUS KAMBOJA KPS', 'topic_ids': None, 'created_at': now.isoformat()},
            {'id': 2, 'group_name': 'Fjb Kaskus Kps', 'topic_ids': '1,5', 'created_at': now.isoformat()},
            {'id': 3, 'group_name': 'Info Loker Kamboja', 'topic_ids': '1,8', 'created_at': now.isoformat()},
            {'id': 4, 'group_name': 'ALFAMART KPS POIPET', 'topic_ids': '1,3', 'created_at': now.isoformat()},
            {'id': 5, 'group_name': 'KASKUS FJB POIPET', 'topic_ids': '1,7', 'created_at': now.isoformat()},
        ],
        'crm_count': 888,
        'crm_users': [
            {'user_id': 113211, 'first_name': 'Budi Santoso', 'username': 'bud1g4nt3n9', 'last_interaction': now.isoformat()},
            {'user_id': 221122, 'first_name': 'Siti Aminah', 'username': None, 'last_interaction': (now - timedelta(days=1)).isoformat()},
            {'user_id': 337783, 'first_name': 'Dracin Lovers', 'username': 'dr4mamu_b0t', 'last_interaction': (now - timedelta(days=2)).isoformat()},
        ]
    }

# --- RUTE DEMO ---
@demo_bp.route('/live-demo/<path:page>')
def live_demo_view(page):
    try:
        data = get_demo_data()
        user = DemoUserEntity()
        
        # Inject variable wajib
        common = {
            'user': user, 
            'user_count': 888, 
            'is_demo_mode': True, 
            'selected_ids': None
        }

        if page == 'dashboard':
            return render_template('dashboard/index.html', **common, 
                                   logs=data['logs'], schedules=data['schedules'], targets=data['targets'],
                                   current_page=1, total_pages=1, per_page=5, total_logs=5, active_page='dashboard')
            
        elif page == 'broadcast':
            # [FIX] Kirim templates ke broadcast juga
            return render_template('dashboard/broadcast.html', **common, 
                                   templates=data['templates'], # <-- PENTING
                                   active_page='broadcast', count_selected=0)
            
        elif page == 'targets':
            return render_template('dashboard/targets.html', **common, 
                                   targets=data['targets'], active_page='targets')
            
        elif page == 'schedule':
            # [FIX UTAMA] Kirim templates & targets ke halaman jadwal
            return render_template('dashboard/schedule.html', **common, 
                                   schedules=data['schedules'], 
                                   templates=data['templates'], # <-- INI YANG BIKIN ERROR KEMARIN (MISSING)
                                   targets=data['targets'],     # <-- INI JUGA DIBUTUHKAN MODAL
                                   active_page='schedule')
        
        elif page == 'crm':
             return render_template('dashboard/crm.html', **common, 
                                    crm_users=data['crm_users'], 
                                    active_page='crm',
                                    # --- TAMBAHAN WAJIB (Biar Gak Error Merah) ---
                                    current_page=1, 
                                    total_pages=1, 
                                    per_page=10, 
                                    total_logs=len(data['crm_users']))

        elif page == 'connection':
             return render_template('dashboard/connection.html', **common, active_page='connection')
             
        elif page == 'profile':
             return render_template('dashboard/profile.html', **common, active_page='profile')
             
        elif page == 'templates': # [FIX] Tambah route templates demo
             return render_template('dashboard/templates.html', **common, 
                                    templates=data['templates'], active_page='templates')

        else:
            return redirect('/live-demo/dashboard')
            
    except Exception as e:
        logger.error(f"Demo View Error: {e}")
        # Return error di layar biar ketahuan kalo ada yang salah
        return f"<h2 style='color:red; text-align:center; margin-top:50px;'>Demo Error: {str(e)}</h2>"

    return "OK"
