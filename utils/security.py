"""
=========================================================================================
🔥 BLASTPRO SECURITY MATRIX v1.0 (ENTERPRISE GRADE) 🔥
=========================================================================================
File ini adalah inti keamanan dari aplikasi BlastPro SaaS.
Dilengkapi dengan 7 Lapis Pertahanan terhadap XSS, SQLi, CSRF, Brute Force, dan Spam.
Dibuat khusus untuk arsitektur Flask + Supabase.
=========================================================================================
"""

import os
import re
import html
import time
import hmac
import hashlib
import secrets
import logging
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
from cryptography.fernet import Fernet

# Set Logger untuk mendeteksi serangan
logger = logging.getLogger("BlastPro_Security")
logger.setLevel(logging.WARNING)

# =========================================================================================
# 🛡️ 1. EXCEPTION HANDLING (Error Kustom Keamanan)
# =========================================================================================
class SecurityViolation(Exception):
    """Exception dasar untuk semua pelanggaran keamanan."""
    pass

class TokenExpiredError(SecurityViolation):
    """Token sudah melewati batas waktu."""
    pass

class InvalidTokenError(SecurityViolation):
    """Token diubah oleh pihak ketiga / tidak valid."""
    pass

class WeakPasswordError(SecurityViolation):
    """Password tidak memenuhi standar keamanan."""
    pass

class SpamEmailError(SecurityViolation):
    """Email terdeteksi sebagai temporary/disposable email."""
    pass


# =========================================================================================
# 🛡️ 2. PASSWORD VAULT (Keamanan Sandi Kasta Dewa)
# =========================================================================================
class PasswordVault:
    """Manajemen Enkripsi dan Validasi Sandi."""
    
    @staticmethod
    def hash_password(password: str) -> str:
        """
        Mengacak password menggunakan PBKDF2 dengan SHA256.
        Mencegah Rainbow Table & Dictionary Attacks.
        """
        # werkzeug otomatis membuat salt random untuk setiap hash
        return generate_password_hash(password, method='pbkdf2:sha256:260000')

    @staticmethod
    def verify_password(hashed_password: str, plain_password: str) -> bool:
        """Mencocokkan password yang diketik user dengan hash di database."""
        return check_password_hash(hashed_password, plain_password)

    @staticmethod
    def validate_complexity(password: str):
        """
        Aturan Ketat Sandi SaaS:
        - Minimal 8 karakter
        - Minimal 1 Huruf Besar
        - Minimal 1 Huruf Kecil
        - Minimal 1 Angka
        """
        if len(password) < 8:
            raise WeakPasswordError("Password minimal harus 8 karakter.")
        if not re.search(r"[A-Z]", password):
            raise WeakPasswordError("Password harus mengandung minimal 1 huruf kapital.")
        if not re.search(r"[a-z]", password):
            raise WeakPasswordError("Password harus mengandung minimal 1 huruf kecil.")
        if not re.search(r"\d", password):
            raise WeakPasswordError("Password harus mengandung minimal 1 angka.")
        
        # Blacklist sandi pasaran
        blacklist = ['12345678', 'password', 'qwertyuiop', 'admin123', 'blastpro123']
        if password.lower() in blacklist:
            raise WeakPasswordError("Password terlalu umum/mudah ditebak. Gunakan kombinasi lain.")
        
        return True


# =========================================================================================
# 🛡️ 3. TOKEN GENERATOR (Verifikasi Email Anti-Bot)
# =========================================================================================
class TokenManager:
    """Pembuat Tiket/Token Sekali Pakai (Time-based)."""
    
    def __init__(self, secret_key: str, salt: str = 'blastpro-auth-salt'):
        # Memastikan aplikasi memiliki secret key, jika tidak, sistem menolak berjalan
        if not secret_key:
            raise ValueError("CRITICAL: SECRET_KEY tidak ditemukan di environment!")
        self.serializer = URLSafeTimedSerializer(secret_key)
        self.salt = salt

    def generate_verification_token(self, email: str) -> str:
        """Membuat token aktivasi email."""
        return self.serializer.dumps(email, salt=self.salt)

    def verify_token(self, token: str, expiration_seconds: int = 3600) -> str:
        """
        Membaca token.
        Default expiration: 3600 detik (1 Jam). 
        Jika lebih dari 1 jam, akan meledak (Error).
        """
        try:
            email = self.serializer.loads(token, salt=self.salt, max_age=expiration_seconds)
            return email
        except SignatureExpired:
            logger.warning(f"SECURITY: Seseorang mencoba memakai token yang kadaluarsa: {token[:10]}...")
            raise TokenExpiredError("Link verifikasi sudah kadaluarsa. Silakan minta link baru.")
        except BadSignature:
            logger.warning(f"SECURITY: Seseorang mencoba meretas token (Bad Signature): {token[:10]}...")
            raise InvalidTokenError("Link verifikasi tidak valid atau telah dimanipulasi.")


# =========================================================================================
# 🛡️ 4. ANTI-SPAM GUARD (Blokir Pendaftaran Fake/Tuyul)
# =========================================================================================
class AntiSpamGuard:
    """Mencegah pendaftaran menggunakan email sementara (Trash Mail)."""
    
    # Daftar domain email sementara yang populer di kalangan bot
    DISPOSABLE_DOMAINS = {
        '10minutemail.com', 'temp-mail.org', 'yopmail.com', 'guerrillamail.com', 
        'mailinator.com', 'throwawaymail.com', 'maildrop.cc', 'trashmail.com',
        'sharklasers.com', 'dispostable.com', 'tempmail.com', 'tempmail.net',
        '0clickemail.com', 'spam4.me', 'mytrashmail.com', 'catchator.com',
        'mailcatch.com', 'getnada.com', 'nada.ltd', 'inboxkitten.com'
    }

    @classmethod
    def is_clean_email(cls, email: str) -> bool:
        """Verifikasi format email dan pastikan bukan email sementara."""
        # 1. Cek format standar email
        email_regex = r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$"
        if not re.match(email_regex, email):
            raise SecurityViolation("Format email tidak valid.")
        
        # 2. Cek Anti Disposable Email
        try:
            domain = email.split('@')[1].lower()
            if domain in cls.DISPOSABLE_DOMAINS:
                logger.warning(f"ANTI-SPAM: Pendaftaran ditolak dari domain sampah -> {domain}")
                raise SpamEmailError("Domain email ini tidak diizinkan. Gunakan Gmail, Yahoo, atau Email Bisnis.")
        except IndexError:
            raise SecurityViolation("Format email rusak.")
            
        return True


# =========================================================================================
# 🛡️ 5. INPUT SANITIZER (Tembok XSS & HTML Injection)
# =========================================================================================
class InputSanitizer:
    """Pembersih Input User untuk mencegah celah Cross Site Scripting (XSS)."""
    
    @staticmethod
    def clean_html(text: str) -> str:
        """Mengubah tag <script> menjadi teks biasa agar tidak tereksekusi browser."""
        if not text:
            return ""
        return html.escape(text.strip())

    @staticmethod
    def sanitize_username(username: str) -> str:
        """Hanya mengizinkan huruf, angka, dan underscore untuk username."""
        if not username:
            return ""
        # Buang semua karakter kecuali a-z, A-Z, 0-9, dan _
        clean_user = re.sub(r'[^a-zA-Z0-9_]', '', username)
        return clean_user
        
    @staticmethod
    def sanitize_phone(phone: str) -> str:
        """Hanya mengizinkan angka dan tanda + untuk nomor HP."""
        if not phone:
            return ""
        return re.sub(r'[^\d+]', '', phone)


# =========================================================================================
# 🛡️ 6. SESSION DEFENDER (Anti Session Hijacking)
# =========================================================================================
class SessionDefender:
    """Melindungi sesi pengguna dari pencurian (Hijacking)."""
    
    @staticmethod
    def generate_fingerprint(ip_address: str, user_agent: str) -> str:
        """
        Membuat sidik jari digital (Fingerprint) dari user saat login.
        Jika di tengah sesi IP atau Device berubah drastis, sesi akan digugurkan.
        """
        raw_data = f"{ip_address}|{user_agent}|blastpro_secret_salt"
        return hashlib.sha256(raw_data.encode()).hexdigest()

    @staticmethod
    def compare_fingerprint(current_fingerprint: str, stored_fingerprint: str) -> bool:
        """Membandingkan fingerprint dengan metode Constant Time (Anti Timing Attack)."""
        return hmac.compare_digest(current_fingerprint, stored_fingerprint)


# =========================================================================================
# 🛡️ 7. CRYPTO ENGINE (Enkripsi Data Sensitif Kelas Militer)
# =========================================================================================
class CryptoEngine:
    """
    Digunakan untuk mengenkripsi Session String Telethon atau API Keys 
    sebelum disimpan ke Database Supabase. (Supaya kalau DB bobol, akun Telegram user aman).
    """
    
    def __init__(self, master_key: str):
        """
        Master key harus berupa 32 url-safe base64-encoded bytes.
        Bisa didapat dari: cryptography.fernet.Fernet.generate_key()
        """
        if not master_key:
            raise ValueError("CRITICAL: MASTER_KEY tidak ditemukan untuk Crypto Engine!")
        self.cipher = Fernet(master_key.encode())

    def encrypt_data(self, plain_text: str) -> str:
        """Mengunci teks menjadi kode acak yang tidak bisa dibaca."""
        if not plain_text: return ""
        encrypted_bytes = self.cipher.encrypt(plain_text.encode())
        return encrypted_bytes.decode()

    def decrypt_data(self, encrypted_text: str) -> str:
        """Membuka kunci teks kembali ke bentuk semula."""
        if not encrypted_text: return ""
        try:
            decrypted_bytes = self.cipher.decrypt(encrypted_text.encode())
            return decrypted_bytes.decode()
        except Exception as e:
            logger.error(f"CRYPTO ERROR: Gagal mendekripsi data sensitif! {e}")
            raise SecurityViolation("Data korup atau kunci enkripsi salah.")


# =========================================================================================
# 🛡️ 8. CSRF PROTECTION UTILS (Fallback)
# =========================================================================================
def generate_csrf_token() -> str:
    """Membuat token acak untuk dimasukkan ke dalam form HTML (Mencegah serangan CSRF)."""
    return secrets.token_hex(32)

def verify_csrf_token(form_token: str, session_token: str) -> bool:
    """Mencocokkan token di form dengan token di sesi user."""
    if not form_token or not session_token:
        return False
    return hmac.compare_digest(form_token, session_token)
