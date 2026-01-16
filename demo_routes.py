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
            {'id': 3, 'group_name': 'Info Loker Kamboja', 'status': 'failed', 'error_message': 'FloodWait', 'created_at': (now - timedelta(minutes=45)).isoformat()},
            {'id': 4, 'group_name': 'ALFAMART KPS POIPET', 'status': 'success', 'created_at': (now - timedelta(minutes=12)).isoformat()},
            {'id': 5, 'group_name': 'Kaskus Cambodia ( KPS, POIPET, PP, CT, BAVET, DLL )', 'status': 'success', 'created_at': (now - timedelta(minutes=12)).isoformat()},
            {'id': 5, 'group_name': 'KASKUS FJB POIPET', 'status': 'success', 'created_at': (now - timedelta(minutes=12)).isoformat()},
        ],
        'schedules': [
            {'id': 1, 'run_hour': 8, 'run_minute': 0, 'is_active': True},
            {'id': 2, 'run_hour': 12, 'run_minute': 0, 'is_active': True},
            {'id': 3, 'run_hour': 18, 'run_minute': 30, 'is_active': False},
        ],
        'targets': [
            {'id': 1, 'group_name': 'KASKUS KAMBOJA KPS', 'topic_ids': None, 'created_at': now.isoformat()},
            {'id': 2, 'group_name': 'Fjb Kaskus Kps', 'topic_ids': '1,5', 'created_at': now.isoformat()},
            {'id': 3, 'group_name': 'Info Loker Kamboja', 'topic_ids': '1,5', 'created_at': now.isoformat()},
            {'id': 4, 'group_name': 'ALFAMART KPS POIPET', 'topic_ids': '1,5', 'created_at': now.isoformat()},
            {'id': 5, 'group_name': 'KASKUS FJB POIPET', 'topic_ids': '1,5', 'created_at': now.isoformat()},
        ],
        'crm_count': 888,
        'crm_users': [ # Data CRM Palsu
            {'user_id': 111, 'first_name': 'Budi Santoso', 'username': 'budiganteng', 'last_interaction': now.isoformat()},
            {'user_id': 222, 'first_name': 'Siti Aminah', 'username': None, 'last_interaction': (now - timedelta(days=1)).isoformat()},
            {'user_id': 333, 'first_name': 'Dracin Lovers', 'username': 'dracin_indo', 'last_interaction': (now - timedelta(days=2)).isoformat()},
        ]
    }

# --- RUTE DEMO ---
@demo_bp.route('/live-demo/<path:page>')
def live_demo_view(page):
    demo_user = DemoUserEntity()
    data = get_demo_data()

    # Flag 'is_demo_mode' ini kuncinya!
    common_args = {
        'user': demo_user, 
        'user_count': data['crm_count'], 
        'is_demo_mode': True, 
        'selected_ids': None
    }

    try:
        if page == 'dashboard':
            return render_template('dashboard/index.html', **common_args, 
                                   logs=data['logs'], schedules=data['schedules'], targets=data['targets'],
                                   current_page=1, total_pages=1, per_page=5, total_logs=5, active_page='dashboard')
            
        elif page == 'broadcast':
            return render_template('dashboard/broadcast.html', **common_args, count_selected=0, active_page='broadcast')
            
        elif page == 'targets':
            return render_template('dashboard/targets.html', **common_args, targets=data['targets'], active_page='targets')
            
        elif page == 'schedule':
            return render_template('dashboard/schedule.html', **common_args, schedules=data['schedules'], active_page='schedule')
        
        elif page == 'crm':
             return render_template('dashboard/crm.html', **common_args, crm_users=data['crm_users'], active_page='crm')

        elif page == 'connection':
             return render_template('dashboard/connection.html', **common_args, active_page='connection')
             
        elif page == 'profile':
             return render_template('dashboard/profile.html', **common_args, active_page='profile')

        else:
            return redirect('/live-demo/dashboard')
            
    except Exception as e:
        logger.error(f"Demo View Error: {e}")
        return "Demo sedang loading..."

    return "OK"
