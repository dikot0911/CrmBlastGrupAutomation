from flask import Blueprint, render_template, redirect
from datetime import datetime, timedelta
import logging

# 1. Bikin Blueprint (Ini kayak "Cabang" dari aplikasi utama)
demo_bp = Blueprint('demo', __name__)

logger = logging.getLogger("BabaSaaSCore")

# 2. Masukin Class & Data Palsu tadi kesini
class DemoUserEntity:
    def __init__(self):
        self.id = 99999
        self.email = "demo.umkm@blastpro.id"
        self.is_admin = False
        self.is_banned = False
        self.created_at = datetime.utcnow()
        self.telegram_account = type('TeleInfo', (object,), {
            'phone_number': '+6281234567890',
            'is_active': True,
            'created_at': datetime.utcnow()
        })

def get_demo_data():
    now = datetime.utcnow()
    return {
        'logs': [
            {'id': 1, 'target_name': 'FJB WNI Kamboja 🇰🇭', 'message_preview': 'Halo kak, ready stok...', 'status': 'success', 'created_at': (now).isoformat()},
            {'id': 2, 'target_name': 'Komunitas Kuliner Phnom Penh', 'message_preview': 'Promo diskon 20%...', 'status': 'success', 'created_at': (now - timedelta(minutes=15)).isoformat()},
            {'id': 3, 'target_name': 'Info Loker Kamboja', 'message_preview': 'Dicari reseller...', 'status': 'failed', 'error_msg': 'FloodWait', 'created_at': (now - timedelta(minutes=30)).isoformat()},
        ],
        'schedules': [
            {'id': 1, 'run_hour': 9, 'run_minute': 0, 'is_active': True},
            {'id': 2, 'run_hour': 12, 'run_minute': 30, 'is_active': True},
        ],
        'targets': [
            {'id': 1, 'group_name': 'FJB WNI Kamboja 🇰🇭', 'topic_ids': None, 'created_at': now.isoformat()},
            {'id': 2, 'group_name': 'Kuliner Nusantara PP', 'topic_ids': '1,5,9', 'created_at': now.isoformat()},
        ],
        'crm_count': 154
    }

# 3. Rute Demo (Ganti @app jadi @demo_bp)
@demo_bp.route('/live-demo/<path:page>')
def live_demo_view(page):
    response = None
    demo_user = DemoUserEntity()
    data = get_demo_data()

    common_args = {
        'user': demo_user, 
        'user_count': data['crm_count'], 
        'is_demo_mode': True
    }

    try:
        if page == 'dashboard':
            response = render_template('dashboard/index.html', 
                                    **common_args,
                                    logs=data['logs'], 
                                    schedules=data['schedules'], 
                                    targets=data['targets'],
                                    current_page=1, total_pages=5, per_page=5, total_logs=25,
                                    active_page='dashboard')
            
        elif page == 'broadcast':
            response = render_template('dashboard/broadcast.html', **common_args, active_page='broadcast')
            
        elif page == 'targets':
            response = render_template('dashboard/targets.html', **common_args, targets=data['targets'], active_page='targets')
            
        elif page == 'schedule':
            response = render_template('dashboard/schedule.html', **common_args, schedules=data['schedules'], active_page='schedule')
        
        elif page == 'connection':
             response = render_template('dashboard/connection.html', **common_args, active_page='connection')

        else:
            return redirect('/live-demo/dashboard')
            
    except Exception as e:
        logger.error(f"Demo View Error: {e}")
        return "Demo sedang gangguan."

    from flask import make_response
    resp = make_response(response)
    resp.headers.pop('X-Frame-Options', None) 
    return resp
