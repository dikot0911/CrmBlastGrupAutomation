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
            {'id': 5, 'group_name': 'Kaskus Cambodia ( KPS, POIPET, PP, CT, BAVET, DLL )', 'status': 'success', 'created_at': (now - timedelta(minutes=12)).isoformat()},
            {'id': 5, 'group_name': 'KASKUS FJB POIPET', 'status': 'success', 'created_at': (now - timedelta(minutes=12)).isoformat()},
        ],
        'schedules': [
            {'id': 1, 'run_hour': 8, 'run_minute': 0, 'is_active': True},
            {'id': 2, 'run_hour': 12, 'run_minute': 0, 'is_active': True},
            {'id': 3, 'run_hour': 18, 'run_minute': 30, 'is_active': True},
        ],
        'targets': [
            {'id': 1, 'group_name': 'KASKUS KAMBOJA KPS', 'topic_ids': None, 'created_at': now.isoformat()},
            {'id': 2, 'group_name': 'Fjb Kaskus Kps', 'topic_ids': '1,5', 'created_at': now.isoformat()},
            {'id': 3, 'group_name': 'Info Loker Kamboja', 'topic_ids': '1,8', 'created_at': now.isoformat()},
            {'id': 4, 'group_name': 'ALFAMART KPS POIPET', 'topic_ids': '1,3', 'created_at': now.isoformat()},
            {'id': 5, 'group_name': 'KASKUS FJB POIPET', 'topic_ids': '1,7', 'created_at': now.isoformat()},
        ],
        'crm_count': 888,
        'crm_users': [ # Data CRM Palsu
            {'user_id': 113211, 'first_name': 'Budi Santoso', 'username': 'bud1g4nt3n9999999', 'last_interaction': now.isoformat()},
            {'user_id': 221122, 'first_name': 'Siti Aminah', 'username': None, 'last_interaction': (now - timedelta(days=1)).isoformat()},
            {'user_id': 337783, 'first_name': 'Dracin Lovers', 'username': 'dr4mamu_b0t321', 'last_interaction': (now - timedelta(days=2)).isoformat()},
        ]
    }

# --- RUTE DEMO ---
@demo_bp.route('/live-demo/<path:page>')
def live_demo_view(page):
    try:
        data = get_demo_data()
        user = DemoUserEntity()
        
        # Inject variable wajib biar dashboard.html ga error
        common = {
            'user': user,
            'user_count': 888,
            'is_demo_mode': True, # Flag demo
            'selected_ids': None
        }

        if page == 'dashboard':
            return render_template('dashboard/index.html', **common, 
                                   logs=data['logs'], schedules=data['schedules'], targets=data['targets'],
                                   current_page=1, total_pages=1, per_page=5, total_logs=5, active_page='dashboard')
        # ... (Sisa route lain biarin sama kayak punya lu) ...
        elif page == 'broadcast': return render_template('dashboard/broadcast.html', **common, active_page='broadcast', count_selected=0)
        elif page == 'targets': return render_template('dashboard/targets.html', **common, targets=data['targets'], active_page='targets')
        elif page == 'schedule': return render_template('dashboard/schedule.html', **common, schedules=data['schedules'], active_page='schedule')
        elif page == 'crm': return render_template('dashboard/crm.html', **common, crm_users=data['crm_users'], active_page='crm')
        elif page == 'connection': return render_template('dashboard/connection.html', **common, active_page='connection')
        elif page == 'profile': return render_template('dashboard/profile.html', **common, active_page='profile')
        else: return redirect('/live-demo/dashboard')

    except Exception as e:
        logger.error(f"Demo Error: {e}")
        return "Loading Demo..."
