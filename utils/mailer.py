"""
=========================================================================================
📧 BLASTPRO MAILER ENGINE v1.0 (ENTERPRISE GRADE) 📧
=========================================================================================
Mesin pengirim email asinkron menggunakan Background Threading.
Mencegah UI nge-freeze saat menunggu respon dari server SMTP (Gmail/SendGrid/dll).
Dilengkapi dengan Template HTML Email yang modern dan responsif.
=========================================================================================
"""

import os
import smtplib
import threading
import logging
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

logger = logging.getLogger("BlastPro_Mailer")
logger.setLevel(logging.INFO)

class BlastProMailer:
    def __init__(self):
        # Mengambil kredensial dari Environment Variables (.env / Render Dashboard)
        # Disarankan pakai Gmail App Password untuk startup awal
        self.smtp_server = os.environ.get('SMTP_SERVER', 'smtp.gmail.com')
        self.smtp_port = int(os.environ.get('SMTP_PORT', 587))
        self.sender_email = os.environ.get('SENDER_EMAIL')
        self.sender_password = os.environ.get('SENDER_PASSWORD') # Gunakan App Password, bukan pass email asli
        self.app_name = "BlastPro SaaS"

        if not self.sender_email or not self.sender_password:
            logger.warning("⚠️ MAILER WARNING: SENDER_EMAIL atau SENDER_PASSWORD belum disetting di environment!")

    def _get_verification_template(self, verify_url: str, user_name: str) -> str:
        """Template HTML Email Kasta Dewa untuk Verifikasi."""
        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f3f4f6; margin: 0; padding: 40px 20px; }}
                .container {{ max-width: 600px; margin: 0 auto; background-color: #ffffff; border-radius: 16px; overflow: hidden; box-shadow: 0 10px 25px rgba(0,0,0,0.05); }}
                .header {{ background: linear-gradient(135deg, #4f46e5, #9333ea); padding: 40px 20px; text-align: center; }}
                .header h1 {{ color: #ffffff; margin: 0; font-size: 28px; letter-spacing: 1px; }}
                .content {{ padding: 40px 30px; text-align: center; }}
                .content h2 {{ color: #1f2937; margin-top: 0; font-size: 22px; }}
                .content p {{ color: #4b5563; line-height: 1.6; font-size: 15px; margin-bottom: 30px; }}
                .btn {{ display: inline-block; background: linear-gradient(to right, #4f46e5, #7c3aed); color: #ffffff !important; text-decoration: none; padding: 14px 32px; border-radius: 12px; font-weight: bold; font-size: 16px; box-shadow: 0 4px 6px rgba(79, 70, 229, 0.25); }}
                .footer {{ background-color: #f9fafb; padding: 20px; text-align: center; border-top: 1px solid #e5e7eb; }}
                .footer p {{ color: #9ca3af; font-size: 12px; margin: 0; }}
                .warning {{ color: #ef4444; font-size: 13px; margin-top: 20px; font-weight: 500; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>⚡ {self.app_name}</h1>
                </div>
                <div class="content">
                    <h2>Halo, {user_name}! 👋</h2>
                    <p>Terima kasih telah mendaftar di <b>{self.app_name}</b>. Selangkah lagi untuk mengotomatisasi marketing Anda. Silakan klik tombol di bawah ini untuk memverifikasi alamat email Anda.</p>
                    
                    <a href="{verify_url}" class="btn">Verifikasi Akun Saya</a>
                    
                    <p class="warning">⚠️ Link ini akan hangus secara otomatis dalam 1 Jam.</p>
                    
                    <p style="font-size: 13px; color: #6b7280; margin-top: 30px; text-align: left;">
                        Atau copy-paste link berikut ke browser Anda:<br>
                        <a href="{verify_url}" style="color: #4f46e5; word-break: break-all;">{verify_url}</a>
                    </p>
                </div>
                <div class="footer">
                    <p>&copy; {datetime.now().year} {self.app_name}. All rights reserved.</p>
                    <p>Jika Anda tidak merasa mendaftar, abaikan email ini.</p>
                </div>
            </div>
        </body>
        </html>
        """

    def _send_email_sync(self, to_email: str, subject: str, html_content: str):
        """Fungsi inti pengirim email (Berjalan di background)."""
        if not self.sender_email or not self.sender_password:
            logger.error("❌ MAILER ERROR: Kredensial email kosong, pengiriman dibatalkan.")
            return

        msg = MIMEMultipart('alternative')
        msg['From'] = f"{self.app_name} <{self.sender_email}>"
        msg['To'] = to_email
        msg['Subject'] = subject

        # Pasang konten HTML
        msg.attach(MIMEText(html_content, 'html'))

        try:
            # Setup koneksi SMTP
            server = smtplib.SMTP(self.smtp_server, self.smtp_port)
            server.ehlo() # Nyapa server
            server.starttls() # Aktifin enkripsi jaringan
            server.login(self.sender_email, self.sender_password)
            
            # Eksekusi Kirim
            server.send_message(msg)
            server.quit()
            logger.info(f"✅ MAILER: Email verifikasi berhasil dikirim ke {to_email}")
            
        except Exception as e:
            logger.error(f"❌ MAILER ERROR: Gagal mengirim email ke {to_email}. Error: {str(e)}")

    def send_verification_email(self, to_email: str, user_name: str, verify_url: str):
        """
        Fungsi yang dipanggil dari app.py.
        Akan melemparkan tugas kirim email ke Background Thread.
        """
        html_content = self._get_verification_template(verify_url, user_name)
        subject = f"Verifikasi Akun {self.app_name} Anda"
        
        # Lempar ke "Kurir Bayangan" (Thread baru) biar server web gak nungguin
        thread = threading.Thread(
            target=self._send_email_sync, 
            args=(to_email, subject, html_content)
        )
        thread.start()

# Instansiasi objek mailer agar siap di-import oleh app.py
mailer = BlastProMailer()
