"""
UNI HELP - "Your campus. Your community. Someone can help."
A university-verified student-to-student campus assistance platform.

Single-file Streamlit application. Run with:
    streamlit run app.py
"""

import os
import sqlite3
import secrets
import string
import smtplib
import ssl
import io
from email.mime.text import MIMEText
from datetime import datetime, timedelta
import time

import streamlit as st
from werkzeug.security import generate_password_hash, check_password_hash
import qrcode
from dotenv import load_dotenv

# =============================================================================
# 0. CONFIG / ENVIRONMENT & CAMPUS DIRECTORY
# =============================================================================

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "database.db")
UPLOADS_DIR = os.path.join(BASE_DIR, "uploads")
QR_DIR = os.path.join(UPLOADS_DIR, "qr")
PHOTOS_DIR = os.path.join(UPLOADS_DIR, "photos")

OTP_EXPIRY_MINUTES = 5
OTP_MAX_ATTEMPTS = int(os.getenv("OTP_MAX_ATTEMPTS", "5"))
QR_EXPIRY_MINUTES = 30
PASSWORD_RESET_EXPIRY_HOURS = 2

ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@unihelp.local")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "AdminUniHelp123!")

CAMPUS_LOCATION_MAP = {
    "Block 1A": ["Room 101", "Room 203"],
    "Block 4": ["Room 108", "Room 208"],
    "Block 6": ["Room 403", "Room 507", "Room 603", "Room 604", "Room 608"],
    "Block 13": ["Room 105", "Room 107", "Room 109", "Room 110", "Room 304", "Room 306", "Room 308"],
    "Block 14": [
        "Room 107", "Room 110", "Room 301", "Room 301L", "Room 304", "Room 402", "Room 403", 
        "Room 414", "Room 415", "Room 501", "Room 505", "Room 507", "Room 511", "Room 512", 
        "Room 513", "Room 601", "Room 605", "Room 608", "Room 801", "Room 802", "Room 803", 
        "Room 805", "Room 806", "Room 813"
    ],
    "Block 18": ["Room 106", "Room 108", "Room 202", "Room 205", "Room 406", "Room 512"],
    "Block 20": ["Room 102", "Room 103", "Room 204", "Room 301", "Room 403", "Room 405", "Room 406", "Room 503", "Room 504"],
    "Block 21": ["Room 105", "Room 107", "Room 108", "Room 205", "Room 209", "Room 301", "Room 302", "Room 303", "Room 405", "Room 407", "Room 408", "Room 409", "Room 506"],
    "Block 25": ["Room 101", "Room 110A", "Room 404"],
    "Block 26": [
        "Room 102", "Room 102A", "Room 104", "Room 105A", "Room 108", "Room 209", "Room 301", 
        "Room 302", "Room 303", "Room 304", "Room 305", "Room 306X", "Room 307", "Room 308B", 
        "Room 401", "Room 404", "Room 405", "Room 406", "Room 504", "Room 504X", "Room 506", 
        "Room 506Y", "Room 507A"
    ],
    "Block 27": ["Room 102A", "Room 404", "Room 407"],
    "Block 28": ["Room 103", "Room 203", "Room 302", "Room 407", "Room 502A"],
    "Block 29": ["Room 303", "Room 405"],
    "Block 33": ["Room 303", "Room 306", "Room 403", "Room 405X", "Room 501", "Room 505"],
    "Block 34": [
        "Room 301", "Room 301A", "Room 303", "Room 306", "Room 402", "Room 403", "Room 404X", 
        "Room 408", "Room 409", "Room 410", "Room 501", "Room 502", "Room 506X", "Room 602", 
        "Room 604", "Room 605", "Room 607", "Room 608", "Room 701", "Room 705", "Room 707", "Room 809"
    ],
    "Block 36": [
        "Room 103", "Room 203", "Room 208", "Room 402", "Room 407", "Room 409", "Room 501", 
        "Room 502", "Room 608", "Room 701", "Room 702", "Room 703", "Room 704", "Room 802", 
        "Room 802A", "Room 805", "Room 905"
    ],
    "Block 37": [
        "Room 601", "Room 604", "Room 605", "Room 607", "Room 608", "Room 609", "Room 701", 
        "Room 702", "Room 703", "Room 704", "Room 705", "Room 706", "Room 707", "Room 708", 
        "Room 711", "Room 801", "Room 805", "Room 806", "Room 807", "Room 809", "Room 810", 
        "Room 901", "Room 904", "Room 906", "Room 907"
    ],
    "Block 38": [
        "Room 305", "Room 501", "Room 502", "Room 605C", "Room 605D", "Room 611", "Room 613", 
        "Room 702", "Room 706", "Room 707", "Room 714", "Room 716", "Room 718", "Room 806", 
        "Room 812", "Room 814", "Room 906A", "Room 911", "Room 913", "Room 915"
    ],
    "Block 55": [
        "Room 204", "Room 301", "Room 305", "Room 307", "Room 308", "Room 402", "Room 403", 
        "Room 504", "Room 509", "Room 701", "Room 703", "Room 710", "Room 802", "Room 804", "Room 805"
    ],
    "Block 55A": ["Room 502", "Level 1"],
    "Block 56": [
        "Room 308", "Room 402", "Room 403", "Room 404", "Room 406", "Room 407", "Room 408", 
        "Room 501", "Room 504", "Room 506", "Room 601A", "Room 604", "Room 605", "Room 606", 
        "Room 607", "Room 608", "Room 701A", "Room 703", "Room 705"
    ],
    "Block 57": [
        "Room 103", "Room 104", "Room 207", "Room 303", "Room 304", "Room 306", "Room 307", 
        "Room 308", "Room 309", "Room 406", "Room 409", "Room 501", "Room 506", "Room 708", "Room 804"
    ],
    "Block 57A": ["Room 304", "Room 402A", "Room 502"]
}

def estimate_campus_distance(loc_str):
    try:
        if not loc_str or "-" not in loc_str:
            return "📍 ~300m (3 mins walk)"
        block_part = loc_str.split("-")[0].strip()
        num_str = "".join(filter(str.isdigit, block_part))
        if not num_str:
            return "📍 ~400m (4 mins walk)"
        b_num = int(num_str)
        meters = 150 + (b_num * 15) % 450
        mins = max(2, round(meters / 100))
        return f"📍 ~{meters}m ({mins} mins walk)"
    except Exception:
        return "📍 ~350m (4 mins walk)"

def get_config_val(key, default=""):
    try:
        val = st.secrets.get(key, os.getenv(key, default))
        return str(val).strip()
    except Exception:
        return str(os.getenv(key, default)).strip()

SMTP_HOST = get_config_val("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(get_config_val("SMTP_PORT", "587"))
SMTP_USERNAME = get_config_val("SMTP_USERNAME", "")
SMTP_PASSWORD = get_config_val("SMTP_PASSWORD", "")
APP_URL = get_config_val("APP_URL", "http://localhost:8501").rstrip("/")

EMAIL_CONFIGURED = bool(SMTP_HOST and SMTP_PORT and SMTP_USERNAME and SMTP_PASSWORD)

for d in (UPLOADS_DIR, QR_DIR, PHOTOS_DIR):
    os.makedirs(d, exist_ok=True)

def task_code(entity_id):
    return f"UNIH{int(entity_id):04d}"

def parse_task_id(query_str):
    if not query_str:
        return None
    clean = str(query_str).strip().upper()
    if clean.startswith("UNIH"):
        digits = clean.replace("UNIH", "")
        return int(digits) if digits.isdigit() else None
    return int(clean) if clean.isdigit() else None

# =============================================================================
# 1. DATABASE SETUP
# =============================================================================

def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=15)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn

SCHEMA = """
CREATE TABLE IF NOT EXISTS system_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    phone TEXT,
    student_id TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'student',
    verified INTEGER NOT NULL DEFAULT 0,
    is_suspended INTEGER NOT NULL DEFAULT 0,
    trust_score INTEGER NOT NULL DEFAULT 50,
    rating_sum INTEGER NOT NULL DEFAULT 0,
    rating_count INTEGER NOT NULL DEFAULT 0,
    unicoins INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS password_reset_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT UNIQUE NOT NULL,
    raw_token_preview TEXT,
    expires_at TEXT NOT NULL,
    used INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS otp_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    purpose TEXT NOT NULL,
    reference_id INTEGER,
    otp_hash TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 5,
    used INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS qr_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reference_type TEXT NOT NULL,
    reference_id INTEGER NOT NULL,
    purpose TEXT NOT NULL,
    token TEXT UNIQUE NOT NULL,
    used INTEGER NOT NULL DEFAULT 0,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    requester_id INTEGER NOT NULL REFERENCES users(id),
    helper_id INTEGER REFERENCES users(id),
    item_name TEXT NOT NULL,
    description TEXT,
    pickup_location TEXT NOT NULL,
    destination TEXT NOT NULL,
    reward REAL NOT NULL,
    preferred_time TEXT,
    status TEXT NOT NULL DEFAULT 'CREATED',
    created_at TEXT NOT NULL,
    accepted_at TEXT,
    pickup_verified_at TEXT,
    delivered_at TEXT,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS borrow_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    borrower_id INTEGER NOT NULL REFERENCES users(id),
    lender_id INTEGER REFERENCES users(id),
    item_name TEXT NOT NULL,
    category TEXT NOT NULL,
    location TEXT NOT NULL,
    description TEXT,
    duration TEXT NOT NULL,
    deposit REAL NOT NULL DEFAULT 0,
    reward REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'REQUESTED',
    created_at TEXT NOT NULL,
    accepted_at TEXT,
    pickup_verified_at TEXT,
    returned_at TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    creator_id INTEGER NOT NULL REFERENCES users(id),
    helper_id INTEGER REFERENCES users(id),
    title TEXT NOT NULL,
    description TEXT,
    pickup TEXT,
    destination TEXT,
    reward REAL NOT NULL,
    deadline TEXT,
    category TEXT,
    status TEXT NOT NULL DEFAULT 'CREATED',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    payer_id INTEGER REFERENCES users(id),
    payee_id INTEGER REFERENCES users(id),
    related_type TEXT NOT NULL,
    related_id INTEGER NOT NULL,
    amount REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'HELD',
    created_at TEXT NOT NULL,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    message TEXT NOT NULL,
    is_read INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS admin_notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,
    reference_id TEXT,
    message TEXT NOT NULL,
    is_read INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS disputes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_type TEXT NOT NULL,
    transaction_id INTEGER NOT NULL,
    reporter_id INTEGER NOT NULL REFERENCES users(id),
    category TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'OPEN',
    created_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS unicoin_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    amount INTEGER NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS admin_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id INTEGER NOT NULL REFERENCES users(id),
    action TEXT NOT NULL,
    target_id INTEGER,
    details TEXT,
    created_at TEXT NOT NULL
);
"""

def now_iso():
    return datetime.utcnow().isoformat()

def parse_iso(s):
    return datetime.fromisoformat(s)

def init_db():
    conn = get_conn()
    conn.executescript(SCHEMA)

    cols = {row[1] for row in conn.execute("PRAGMA table_info(borrow_requests)").fetchall()}
    if "location" not in cols:
        try:
            conn.execute("ALTER TABLE borrow_requests ADD COLUMN location TEXT DEFAULT '34 - 301'")
        except Exception:
            pass

    cur = conn.execute("SELECT id FROM users WHERE role = 'admin' LIMIT 1")
    if cur.fetchone() is None:
        conn.execute(
            """INSERT INTO users (full_name, email, phone, student_id, password_hash,
                role, verified, trust_score, unicoins, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                "Platform Admin",
                ADMIN_EMAIL,
                "",
                "0",
                generate_password_hash(ADMIN_PASSWORD),
                "admin",
                1,
                100,
                0,
                now_iso(),
            ),
        )
    st_row = conn.execute("SELECT value FROM system_settings WHERE key = 'platform_paused'").fetchone()
    if st_row is None:
        conn.execute("INSERT INTO system_settings (key, value) VALUES ('platform_paused', 'false')")
    conn.commit()
    conn.close()

def is_platform_paused():
    conn = get_conn()
    row = conn.execute("SELECT value FROM system_settings WHERE key = 'platform_paused'").fetchone()
    conn.close()
    return bool(row and row["value"].lower() == "true")

def set_platform_paused(paused: bool):
    conn = get_conn()
    conn.execute("UPDATE system_settings SET value = ? WHERE key = 'platform_paused'", ('true' if paused else 'false',))
    conn.commit()
    conn.close()

# =============================================================================
# 2. QR CODES & SECURITY
# =============================================================================

def create_qr_token(reference_type, reference_id, purpose):
    conn = get_conn()
    conn.execute(
        """UPDATE qr_tokens SET used = 1
           WHERE reference_type = ? AND reference_id = ? AND purpose = ? AND used = 0""",
        (reference_type, reference_id, purpose),
    )
    token = f"UNIH|{reference_type}|{reference_id}|{secrets.token_urlsafe(8)}"
    expires_at = (datetime.utcnow() + timedelta(minutes=QR_EXPIRY_MINUTES)).isoformat()
    conn.execute(
        """INSERT INTO qr_tokens (reference_type, reference_id, purpose, token, used, expires_at, created_at)
           VALUES (?, ?, ?, ?, 0, ?, ?)""",
        (reference_type, reference_id, purpose, token, expires_at, now_iso()),
    )
    conn.commit()
    conn.close()
    return token

def verify_qr_token(submitted_token, reference_type, reference_id, purpose):
    clean_token = submitted_token.strip()
    conn = get_conn()
    row = conn.execute(
        """SELECT * FROM qr_tokens 
           WHERE token = ? AND reference_type = ? AND reference_id = ? AND purpose = ?""",
        (clean_token, reference_type, reference_id, purpose),
    ).fetchone()

    if row is None:
        conn.close()
        return False, "Invalid QR code for this transaction."
    if row["used"]:
        conn.close()
        return False, "This QR code has already been scanned/used."
    if datetime.utcnow() > parse_iso(row["expires_at"]):
        conn.close()
        return False, "This QR code has expired. Request a new one."

    conn.execute("UPDATE qr_tokens SET used = 1 WHERE id = ?", (row["id"],))
    conn.commit()
    conn.close()
    return True, "QR verified successfully."

def generate_qr_bytes(token):
    img = qrcode.make(token)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf

def send_realtime_email(to_email, subject, body):
    if not EMAIL_CONFIGURED:
        st.session_state["_last_email_simulated"] = (to_email, subject, body)
        return False, "SMTP is not fully configured. A simulated preview has been provided below."

    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = f"UNI HELP <{SMTP_USERNAME}>"
        msg["To"] = to_email

        if SMTP_PORT == 465:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context, timeout=12) as server:
                server.login(SMTP_USERNAME, SMTP_PASSWORD)
                server.sendmail(SMTP_USERNAME, [to_email], msg.as_string())
        else:
            context = ssl.create_default_context()
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=12) as server:
                server.ehlo()
                server.starttls(context=context)
                server.ehlo()
                server.login(SMTP_USERNAME, SMTP_PASSWORD)
                server.sendmail(SMTP_USERNAME, [to_email], msg.as_string())

        return True, "Email successfully dispatched."
    except Exception as e:
        err_msg = str(e)
        st.session_state["_last_email_error"] = err_msg
        st.session_state["_last_email_simulated"] = (to_email, subject, body)
        return False, f"SMTP Dispatch Error: {err_msg}"

def create_otp(user_id, purpose, reference_id=None):
    conn = get_conn()
    conn.execute(
        """UPDATE otp_records SET used = 1
           WHERE user_id = ? AND purpose = ? AND
                 (reference_id = ? OR (reference_id IS NULL AND ? IS NULL)) AND used = 0""",
        (user_id, purpose, reference_id, reference_id),
    )
    otp_plain = "".join(secrets.choice(string.digits) for _ in range(6))
    expires_at = (datetime.utcnow() + timedelta(minutes=OTP_EXPIRY_MINUTES)).isoformat()
    conn.execute(
        """INSERT INTO otp_records (user_id, purpose, reference_id, otp_hash,
            expires_at, attempts, max_attempts, used, created_at)
           VALUES (?,?,?,?,?,0,?,0,?)""",
        (user_id, purpose, reference_id, generate_password_hash(otp_plain), expires_at, OTP_MAX_ATTEMPTS, now_iso()),
    )
    conn.commit()
    conn.close()
    return otp_plain

def verify_otp(user_id, purpose, reference_id, submitted_otp):
    conn = get_conn()
    row = conn.execute(
        """SELECT * FROM otp_records
           WHERE user_id = ? AND purpose = ? AND
                 (reference_id = ? OR (reference_id IS NULL AND ? IS NULL)) AND used = 0
           ORDER BY id DESC LIMIT 1""",
        (user_id, purpose, reference_id, reference_id),
    ).fetchone()

    if row is None:
        conn.close()
        return False, "No active OTP found. Please request a new one."
    if row["attempts"] >= row["max_attempts"]:
        conn.close()
        return False, "Too many incorrect attempts. Request a new OTP."
    if datetime.utcnow() > parse_iso(row["expires_at"]):
        conn.close()
        return False, "OTP has expired. Request a new one."
    if not check_password_hash(row["otp_hash"], submitted_otp.strip()):
        conn.execute("UPDATE otp_records SET attempts = attempts + 1 WHERE id = ?", (row["id"],))
        conn.commit()
        remaining = row["max_attempts"] - (row["attempts"] + 1)
        conn.close()
        return False, f"Invalid OTP code. {max(remaining, 0)} attempt(s) remaining."

    conn.execute("UPDATE otp_records SET used = 1 WHERE id = ?", (row["id"],))
    conn.commit()
    conn.close()
    return True, "Verified successfully."

def create_password_reset_token(user_id):
    raw_token = secrets.token_urlsafe(32)
    token_hash = generate_password_hash(raw_token)
    expires_at = (datetime.utcnow() + timedelta(hours=PASSWORD_RESET_EXPIRY_HOURS)).isoformat()
    conn = get_conn()
    conn.execute("UPDATE password_reset_tokens SET used = 1 WHERE user_id = ? AND used = 0", (user_id,))
    conn.execute(
        """INSERT INTO password_reset_tokens (user_id, token_hash, raw_token_preview, expires_at, used, created_at)
           VALUES (?, ?, ?, ?, 0, ?)""",
        (user_id, token_hash, raw_token[:8], expires_at, now_iso()),
    )
    conn.commit()
    conn.close()
    return raw_token

def verify_and_consume_password_reset_token(raw_token):
    raw_token = (raw_token or "").strip()
    if not raw_token:
        return None, "Empty reset token provided."
    conn = get_conn()
    rows = conn.execute("SELECT * FROM password_reset_tokens WHERE used = 0 ORDER BY id DESC").fetchall()
    matched = None
    for r in rows:
        if check_password_hash(r["token_hash"], raw_token):
            matched = r
            break
    if not matched:
        conn.close()
        return None, "Invalid or already used password reset link."
    if datetime.utcnow() > parse_iso(matched["expires_at"]):
        conn.close()
        return None, "This password reset link has expired."

    user_id = matched["user_id"]
    conn.execute("UPDATE password_reset_tokens SET used = 1 WHERE id = ?", (matched["id"],))
    conn.commit()
    conn.close()
    return user_id, "Token valid."

def send_login_otp_email(user_row):
    otp = create_otp(user_row["id"], "STUDENT_LOGIN_OTP")
    subject = "UNI HELP — Your Login Verification Code"
    body = (
        f"Hello {user_row['full_name']},\n\n"
        f"Your login verification code for UNI HELP is: {otp}\n\n"
        f"This code will expire in {OTP_EXPIRY_MINUTES} minutes. If you did not request this, "
        f"please secure your account.\n\n"
        f"— UNI HELP Security"
    )
    return send_realtime_email(user_row["email"], subject, body)

def send_password_reset_email(user_row):
    raw_token = create_password_reset_token(user_row["id"])
    reset_link = f"{APP_URL}/?reset_token={raw_token}"
    subject = "UNI HELP — Password Reset Request"
    body = (
        f"Hello {user_row['full_name']},\n\n"
        f"Click the link below to set a new password:\n{reset_link}\n\n"
        f"Or enter your reset token directly into the app:\n{raw_token}\n\n"
        f"This link is valid for {PASSWORD_RESET_EXPIRY_HOURS} hours.\n\n"
        f"— UNI HELP Security Team"
    )
    sent, msg = send_realtime_email(user_row["email"], subject, body)
    return sent, msg, raw_token

# =============================================================================
# 3. QUERIES & SEED DATA (DEMO STUDENTS REMOVED)
# =============================================================================

def seed_demo_data():
    # No demo students seeded as requested
    pass

def user_by_id(user_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return row

def user_by_student_id_or_email(identifier):
    clean = identifier.strip().lower()
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM users WHERE (lower(student_id) = ? OR lower(email) = ?) AND role = 'student'",
        (clean, clean),
    ).fetchone()
    conn.close()
    return row

def notify(user_id, message):
    conn = get_conn()
    conn.execute(
        "INSERT INTO notifications (user_id, message, is_read, created_at) VALUES (?,?,0,?)",
        (user_id, message, now_iso()),
    )
    conn.commit()
    conn.close()

def notify_admin(category, reference_id, message):
    conn = get_conn()
    conn.execute(
        "INSERT INTO admin_notifications (category, reference_id, message, is_read, created_at) VALUES (?, ?, ?, 0, ?)",
        (category, str(reference_id), message, now_iso())
    )
    conn.commit()
    conn.close()

def log_admin_action(admin_id, action, target_id, details=""):
    conn = get_conn()
    conn.execute(
        "INSERT INTO admin_actions (admin_id, action, target_id, details, created_at) VALUES (?, ?, ?, ?, ?)",
        (admin_id, action, target_id, details, now_iso())
    )
    conn.commit()
    conn.close()

def add_unicoins(user_id, amount, reason):
    conn = get_conn()
    conn.execute("UPDATE users SET unicoins = unicoins + ? WHERE id = ?", (amount, user_id))
    conn.execute(
        "INSERT INTO unicoin_transactions (user_id, amount, reason, created_at) VALUES (?,?,?,?)",
        (user_id, amount, reason, now_iso()),
    )
    conn.commit()
    conn.close()
    notify(user_id, f"🪙 {amount:+d} UniCoins — {reason}")

def create_transaction(payer_id, payee_id, related_type, related_id, amount, status="HELD"):
    conn = get_conn()
    conn.execute(
        """INSERT INTO transactions (payer_id, payee_id, related_type, related_id, amount, status, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (payer_id, payee_id, related_type, related_id, amount, status, now_iso(), now_iso()),
    )
    conn.commit()
    conn.close()

def update_transaction_status(related_type, related_id, status):
    conn = get_conn()
    conn.execute(
        "UPDATE transactions SET status = ?, updated_at = ? WHERE related_type = ? AND related_id = ?",
        (status, now_iso(), related_type, related_id),
    )
    conn.commit()
    conn.close()

def delete_order(order_type, order_id):
    conn = get_conn()
    if order_type == "DELIVERY":
        conn.execute("DELETE FROM requests WHERE id = ?", (order_id,))
    elif order_type == "BORROWING":
        conn.execute("DELETE FROM borrow_requests WHERE id = ?", (order_id,))
    elif order_type == "TASK":
        conn.execute("DELETE FROM tasks WHERE id = ?", (order_id,))
    conn.execute("DELETE FROM transactions WHERE related_type = ? AND related_id = ?", (order_type, order_id))
    conn.execute("DELETE FROM disputes WHERE transaction_type = ? AND transaction_id = ?", (order_type, order_id))
    conn.commit()
    conn.close()

def delete_student_account(student_user_id):
    conn = get_conn()
    conn.execute("DELETE FROM requests WHERE requester_id = ? OR helper_id = ?", (student_user_id, student_user_id))
    conn.execute("DELETE FROM borrow_requests WHERE borrower_id = ? OR lender_id = ?", (student_user_id, student_user_id))
    conn.execute("DELETE FROM tasks WHERE creator_id = ? OR helper_id = ?", (student_user_id, student_user_id))
    conn.execute("DELETE FROM transactions WHERE payer_id = ? OR payee_id = ?", (student_user_id, student_user_id))
    conn.execute("DELETE FROM notifications WHERE user_id = ?", (student_user_id,))
    conn.execute("DELETE FROM disputes WHERE reporter_id = ?", (student_user_id,))
    conn.execute("DELETE FROM unicoin_transactions WHERE user_id = ?", (student_user_id,))
    conn.execute("DELETE FROM users WHERE id = ?", (student_user_id,))
    conn.commit()
    conn.close()

# =============================================================================
# 4. CUSTOM HANDSHAKE LOADING ANIMATION & PREMIUM TYPOGRAPHY
# =============================================================================

st.set_page_config(page_title="UNI HELP — Campus Services", page_icon="🎓", layout="centered")

CUSTOM_GLOSSY_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800&display=swap');

html, body, [class*="st-"] {
    font-family: 'Plus Jakarta Sans', sans-serif !important;
    letter-spacing: -0.01em !important;
}

h1, h2, h3, h4, h5, h6 {
    font-family: 'Plus Jakarta Sans', sans-serif !important;
    font-weight: 800 !important;
    letter-spacing: -0.03em !important;
}

.stApp {
    background: radial-gradient(circle at 50% 0%, #1e293b 0%, #090d16 100%) !important;
    color: #f8fafc !important;
}

.main .block-container {
    max-width: 520px !important;
    padding-top: 1.5rem !important;
    padding-bottom: 5rem !important;
    padding-left: 0.8rem !important;
    padding-right: 0.8rem !important;
}

[data-testid="stVerticalBlockBorderWrapper"], .stContainer {
    background: rgba(30, 41, 59, 0.45) !important;
    backdrop-filter: blur(24px) saturate(190%) !important;
    -webkit-backdrop-filter: blur(24px) saturate(190%) !important;
    border: 1px solid rgba(255, 255, 255, 0.08) !important;
    border-radius: 22px !important;
    box-shadow: 0 16px 40px rgba(0, 0, 0, 0.5) !important;
    padding: 1.25rem !important;
    margin-bottom: 1rem !important;
}

.stButton > button {
    background: linear-gradient(135deg, #3b82f6 0%, #6366f1 100%) !important;
    color: #ffffff !important;
    font-family: 'Plus Jakarta Sans', sans-serif !important;
    font-weight: 700 !important;
    font-size: 0.95rem !important;
    border: 1px solid rgba(255, 255, 255, 0.25) !important;
    border-radius: 16px !important;
    padding: 0.65rem 1.25rem !important;
    box-shadow: inset 0 1px 1px rgba(255, 255, 255, 0.4), 0 8px 22px rgba(59, 130, 246, 0.4) !important;
    transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1) !important;
    width: 100% !important;
}

.stButton > button:hover {
    transform: translateY(-2px) scale(1.01) !important;
    box-shadow: inset 0 1px 2px rgba(255, 255, 255, 0.6), 0 12px 28px rgba(99, 102, 241, 0.6) !important;
    border-color: rgba(255, 255, 255, 0.6) !important;
}

.stButton > button:active {
    transform: translateY(1px) scale(0.99) !important;
}

.stButton > button[kind="secondary"] {
    background: rgba(15, 23, 42, 0.75) !important;
    border: 1px solid rgba(255, 255, 255, 0.12) !important;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.25) !important;
}

.stTextInput > div > div > input,
.stTextArea > div > div > textarea,
.stSelectbox > div > div {
    background: rgba(15, 23, 42, 0.7) !important;
    color: #f1f5f9 !important;
    font-family: 'Plus Jakarta Sans', sans-serif !important;
    border: 1px solid rgba(255, 255, 255, 0.1) !important;
    border-radius: 14px !important;
    backdrop-filter: blur(10px) !important;
    box-shadow: inset 0 2px 4px rgba(0, 0, 0, 0.4) !important;
    padding: 0.75rem !important;
}

.stTextInput > div > div > input:focus,
.stTextArea > div > div > textarea:focus {
    border-color: #60a5fa !important;
    box-shadow: 0 0 0 3px rgba(96, 165, 250, 0.25), inset 0 2px 4px rgba(0, 0, 0, 0.4) !important;
}

.stTabs [data-baseweb="tab-list"] {
    background: rgba(15, 23, 42, 0.85) !important;
    border-radius: 18px !important;
    padding: 6px !important;
    border: 1px solid rgba(255, 255, 255, 0.08) !important;
    gap: 6px !important;
}

.stTabs [data-baseweb="tab"] {
    border-radius: 14px !important;
    padding: 8px 12px !important;
    font-family: 'Plus Jakarta Sans', sans-serif !important;
    font-weight: 700 !important;
    color: #94a3b8 !important;
    border: none !important;
}

.stTabs [aria-selected="true"] {
    background: linear-gradient(135deg, #3b82f6 0%, #6366f1 100%) !important;
    color: #ffffff !important;
    box-shadow: 0 4px 15px rgba(59, 130, 246, 0.4) !important;
}

[data-testid="stMetricValue"] {
    font-size: 1.5rem !important;
    font-weight: 800 !important;
    background: linear-gradient(135deg, #60a5fa, #c084fc);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
}

/* --- TWO-PERSON HANDSHAKE LOADING ANIMATION --- */
.handshake-loader-container {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 2.5rem;
    background: rgba(15, 23, 42, 0.6);
    backdrop-filter: blur(16px);
    border-radius: 20px;
    border: 1px solid rgba(255, 255, 255, 0.08);
    margin: 1.5rem 0;
}

.handshake-wrapper {
    display: flex;
    align-items: center;
    justify-content: center;
    position: relative;
    width: 140px;
    height: 70px;
}

.person {
    width: 22px;
    height: 22px;
    background: #60a5fa;
    border-radius: 50%;
    position: absolute;
    box-shadow: 0 0 15px rgba(96, 165, 250, 0.6);
}

.person.left {
    animation: walkLeft 1.6s infinite ease-in-out;
}

.person.right {
    animation: walkRight 1.6s infinite ease-in-out;
}

.handshake-spark {
    position: absolute;
    width: 12px;
    height: 12px;
    background: #34d399;
    border-radius: 50%;
    opacity: 0;
    box-shadow: 0 0 15px #34d399;
    animation: sparkPulse 1.6s infinite ease-in-out;
}

@keyframes walkLeft {
    0% { left: 0px; transform: translateY(0); }
    40% { left: 45px; transform: translateY(-6px); }
    50%, 75% { left: 52px; transform: translateY(0); }
    90% { left: 0px; transform: translateY(0); }
    100% { left: 0px; transform: translateY(0); }
}

@keyframes walkRight {
    0% { right: 0px; transform: translateY(0); }
    40% { right: 45px; transform: translateY(-6px); }
    50%, 75% { right: 52px; transform: translateY(0); }
    90% { right: 0px; transform: translateY(0); }
    100% { right: 0px; transform: translateY(0); }
}

@keyframes sparkPulse {
    0%, 45% { transform: scale(0); opacity: 0; }
    55%, 70% { transform: scale(1.5); opacity: 1; }
    80%, 100% { transform: scale(0); opacity: 0; }
}

.loading-text {
    margin-top: 1rem;
    font-size: 0.95rem;
    font-weight: 700;
    color: #94a3b8;
    letter-spacing: 0.05em;
    text-transform: uppercase;
}
</style>
"""

st.markdown(CUSTOM_GLOSSY_CSS, unsafe_allow_html=True)

def show_handshake_loader(text="Connecting Campus Peers..."):
    loader_html = f"""
    <div class="handshake-loader-container">
        <div class="handshake-wrapper">
            <div class="person left"></div>
            <div class="handshake-spark"></div>
            <div class="person right"></div>
        </div>
        <div class="loading-text">{text}</div>
    </div>
    """
    st.markdown(loader_html, unsafe_allow_html=True)
    time.sleep(0.4)

init_db()
seed_demo_data()

try:
    url_reset_token = st.query_params.get("reset_token")
    if url_reset_token:
        st.session_state["auth_mode"] = "reset_password"
        st.session_state["active_reset_token"] = url_reset_token
except Exception:
    pass

if "user" not in st.session_state:
    st.session_state["user"] = None
if "auth_mode" not in st.session_state:
    st.session_state["auth_mode"] = "student_login"
if "admin_selected_student_id" not in st.session_state:
    st.session_state["admin_selected_student_id"] = None

def show_simulated_dispatch_box():
    if "_last_email_simulated" in st.session_state:
        to_addr, subj, body = st.session_state["_last_email_simulated"]
        with st.expander("📬 Real-time Dispatch Preview (System Log)", expanded=True):
            st.caption(f"**Recipient:** {to_addr} | **Subject:** {subj}")
            st.code(body, language="text")

# =============================================================================
# 5. AUTHENTICATION SCREENS
# =============================================================================

def render_student_login():
    col1, col2, col3 = st.columns([0.1, 2, 0.1])
    with col2:
        st.markdown("<h1 style='text-align:center;'>🎓 UNI HELP</h1>", unsafe_allow_html=True)
        st.markdown("<p style='text-align:center; color:#94a3b8; font-size: 0.95rem;'>Your Campus. Your Community. Someone Can Help.</p>", unsafe_allow_html=True)
        st.write("")

        with st.container(border=True):
            st.markdown("##### 🔐 Student Sign In")
            sid = st.text_input("Student ID (Numbers Only)", key="login_sid")
            pwd = st.text_input("Password", type="password", key="login_pwd")

            c_btn1, c_btn2 = st.columns([1.5, 1])
            with c_btn1:
                send_otp_btn = st.button("Send OTP →", use_container_width=True, type="primary")
            with c_btn2:
                if st.button("Forgot?", use_container_width=True):
                    show_handshake_loader("Loading Recovery...")
                    st.session_state["auth_mode"] = "forgot_password"
                    st.rerun()

            if send_otp_btn:
                if not sid.strip() or not pwd:
                    st.error("Please provide both Student ID and Password.")
                elif not sid.strip().isdigit():
                    st.error("Student ID must contain numbers only.")
                else:
                    show_handshake_loader("Verifying Credentials...")
                    user = user_by_student_id_or_email(sid)
                    if user and check_password_hash(user["password_hash"], pwd):
                        if user["is_suspended"]:
                            st.error("Account suspended. Contact proctor.")
                        elif not user["verified"]:
                            st.warning("Account is pending admin approval.")
                        else:
                            st.session_state["pending_student_user"] = dict(user)
                            sent, err_desc = send_login_otp_email(user)
                            st.session_state["auth_mode"] = "student_otp"
                            if not sent:
                                st.warning(err_desc)
                            st.rerun()
                    else:
                        st.error("Invalid credentials.")

            st.divider()
            if st.button("Register New Account", use_container_width=True):
                show_handshake_loader("Opening Registration...")
                st.session_state["auth_mode"] = "register"
                st.rerun()

        st.write("")
        if st.button("🛡️ Admin Portal", use_container_width=True):
            show_handshake_loader("Entering Secure Portal...")
            st.session_state["auth_mode"] = "admin_login"
            st.rerun()

def render_student_otp():
    user = st.session_state.get("pending_student_user")
    if not user:
        st.session_state["auth_mode"] = "student_login"
        st.rerun()

    col1, col2, col3 = st.columns([0.1, 2, 0.1])
    with col2:
        st.markdown("### 📩 Enter Email OTP")
        st.caption(f"Verification code sent to **{user['email']}**")

        show_simulated_dispatch_box()

        otp_val = st.text_input("6-Digit OTP", max_chars=6, key="login_otp_input")

        if st.button("Verify & Log In", use_container_width=True, type="primary"):
            show_handshake_loader("Handshaking Secure Session...")
            ok, msg = verify_otp(user["id"], "STUDENT_LOGIN_OTP", None, otp_val)
            if ok:
                st.session_state["user"] = user
                st.session_state.pop("pending_student_user", None)
                st.session_state.pop("_last_email_simulated", None)
                st.session_state["auth_mode"] = "student_login"
                st.success("Welcome back!")
                st.rerun()
            else:
                st.error(msg)

        if st.button("← Back to Sign In", use_container_width=True):
            show_handshake_loader("Returning...")
            st.session_state.pop("pending_student_user", None)
            st.session_state["auth_mode"] = "student_login"
            st.rerun()

def render_forgot_password():
    col1, col2, col3 = st.columns([0.1, 2, 0.1])
    with col2:
        st.markdown("### 🔑 Recover Password")
        identifier = st.text_input("Student ID (Numbers Only) or Email")

        if st.button("Send Reset Link", use_container_width=True, type="primary"):
            if not identifier.strip():
                st.error("Please enter ID or email.")
            else:
                show_handshake_loader("Dispatched Recovery Link...")
                user = user_by_student_id_or_email(identifier)
                if user:
                    sent, msg, raw_token = send_password_reset_email(user)
                    st.session_state["recent_generated_reset_token"] = raw_token
                    if sent:
                        st.success(f"Reset link sent to **{user['email']}**.")
                    else:
                        st.warning("Simulated token generated.")
                else:
                    st.info("If registered, reset link dispatched.")

        show_simulated_dispatch_box()

        recent_tok = st.session_state.get("recent_generated_reset_token", "")
        token_input = st.text_input("Reset Token", value=recent_tok)

        if st.button("Proceed to Reset →", use_container_width=True):
            if token_input.strip():
                show_handshake_loader("Loading...")
                st.session_state["active_reset_token"] = token_input.strip()
                st.session_state["auth_mode"] = "reset_password"
                st.rerun()
            else:
                st.error("Enter reset token.")

        if st.button("← Back", use_container_width=True):
            show_handshake_loader("Returning...")
            st.session_state["auth_mode"] = "student_login"
            st.rerun()

def render_reset_password():
    token = st.session_state.get("active_reset_token", "").strip()
    col1, col2, col3 = st.columns([0.1, 2, 0.1])
    with col2:
        st.markdown("### 🔒 New Password")
        if not token:
            st.error("No token provided.")
            if st.button("Return"):
                st.session_state["auth_mode"] = "student_login"
                st.rerun()
            return

        new_pw = st.text_input("New Password", type="password")
        confirm_pw = st.text_input("Confirm Password", type="password")

        if st.button("Update Password", use_container_width=True, type="primary"):
            if len(new_pw) < 6:
                st.error("Password too short.")
            elif new_pw != confirm_pw:
                st.error("Passwords do not match.")
            else:
                show_handshake_loader("Updating Credentials...")
                user_id, msg = verify_and_consume_password_reset_token(token)
                if user_id:
                    conn = get_conn()
                    conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(new_pw), user_id))
                    conn.commit()
                    conn.close()
                    st.success("Password updated! Please login.")
                    st.session_state.pop("active_reset_token", None)
                    st.session_state["auth_mode"] = "student_login"
                    st.rerun()
                else:
                    st.error(msg)

def render_registration():
    col1, col2, col3 = st.columns([0.1, 2, 0.1])
    with col2:
        st.markdown("### 🎓 Student Registration")
        with st.form("reg_form"):
            name = st.text_input("Full Name")
            sid = st.text_input("Student ID (Numbers Only)")
            email = st.text_input("Email Address")
            phone = st.text_input("Phone Number")
            pw1 = st.text_input("Password", type="password")
            pw2 = st.text_input("Confirm Password", type="password")
            submitted = st.form_submit_button("Submit for Approval", use_container_width=True, type="primary")

        if submitted:
            if not sid.strip().isdigit():
                st.error("Student ID must be numeric.")
            elif "@" not in email or "." not in email:
                st.error("Invalid email.")
            elif len(pw1) < 6 or pw1 != pw2:
                st.error("Password mismatch or too short.")
            elif not sid.strip() or not name.strip():
                st.error("All fields required.")
            else:
                show_handshake_loader("Registering Peer Account...")
                conn = get_conn()
                try:
                    conn.execute(
                        """INSERT INTO users (full_name, email, phone, student_id, password_hash, role, verified, trust_score, unicoins, created_at)
                           VALUES (?, ?, ?, ?, ?, 'student', 0, 50, 50, ?)""",
                        (name.strip(), email.strip().lower(), phone.strip(), sid.strip(), generate_password_hash(pw1), now_iso()),
                    )
                    uid = conn.execute("SELECT last_insert_rowid() id").fetchone()["id"]
                    conn.commit()
                    add_unicoins(uid, 50, "Welcome bonus")
                    notify_admin("NEW_ACCOUNT", sid.strip(), f"Student {name.strip()} ({sid.strip()}) registered.")
                    st.success("Registered! Awaiting Admin Approval.")
                    st.session_state["auth_mode"] = "student_login"
                    st.rerun()
                except sqlite3.IntegrityError:
                    st.error("ID or Email already exists.")
                finally:
                    conn.close()

        if st.button("← Back", use_container_width=True):
            show_handshake_loader("Returning...")
            st.session_state["auth_mode"] = "student_login"
            st.rerun()

def render_admin_login():
    col1, col2, col3 = st.columns([0.1, 2, 0.1])
    with col2:
        st.markdown("<h2 style='text-align:center;'>🛡️ Admin Portal</h2>", unsafe_allow_html=True)
        with st.form("admin_login_box"):
            admin_user = st.text_input("Admin Email", key="admin_email_field")
            admin_pwd = st.text_input("Password", type="password", key="admin_pwd_field")
            submit_adm = st.form_submit_button("Log In", use_container_width=True, type="primary")

        if submit_adm:
            show_handshake_loader("Authenticating Proctor...")
            conn = get_conn()
            row = conn.execute("SELECT * FROM users WHERE email=? AND role='admin'", (admin_user.strip().lower(),)).fetchone()
            conn.close()
            if row and check_password_hash(row["password_hash"], admin_pwd):
                st.session_state["user"] = dict(row)
                st.rerun()
            else:
                st.error("Access denied.")

        if st.button("← Back", use_container_width=True):
            show_handshake_loader("Returning...")
            st.session_state["auth_mode"] = "student_login"
            st.rerun()

# =============================================================================
# 6. EXPANDED ADMIN WORKSPACE (WITH ACCOUNT DELETION)
# =============================================================================

def render_admin_student_profile(admin_user, student_id):
    conn = get_conn()
    student = conn.execute("SELECT * FROM users WHERE id=? AND role='student'", (student_id,)).fetchone()
    if not student:
        conn.close()
        st.error("Student profile could not be found.")
        if st.button("← Back to Student Registry"):
            st.session_state["admin_selected_student_id"] = None
            st.rerun()
        return

    st.markdown(f"### 👤 Student Profile: {student['full_name']}")
    if st.button("← Back to Student Registry", key="prof_back_top"):
        st.session_state["admin_selected_student_id"] = None
        st.rerun()

    c1, c2 = st.columns([1.5, 2])
    with c1:
        with st.container(border=True):
            st.markdown("##### Account Details")
            st.write(f"**Student ID:** `{student['student_id']}`")
            st.write(f"**Email:** {student['email']}")
            st.write(f"**Phone:** {student['phone'] or 'Not provided'}")
            st.write(f"**Joined:** {student['created_at'][:10]}")
            st.write(f"**Status:** `{'🔴 Suspended' if student['is_suspended'] else '🟢 Active'}`")
            st.write(f"**Verified:** `{'✓ Yes' if student['verified'] else '○ No'}`")

            st.divider()
            st.markdown("##### Moderation Actions")
            if student["is_suspended"]:
                if st.button("Unsuspend Student Account", use_container_width=True):
                    conn.execute("UPDATE users SET is_suspended=0 WHERE id=?", (student["id"],))
                    conn.commit()
                    log_admin_action(admin_user["id"], "UNSUSPEND_USER", student["id"], "Account unsuspended by admin")
                    st.success("User unsuspended.")
                    st.rerun()
            else:
                if st.button("Suspend Student Account", use_container_width=True):
                    conn.execute("UPDATE users SET is_suspended=1 WHERE id=?", (student["id"],))
                    conn.commit()
                    log_admin_action(admin_user["id"], "SUSPEND_USER", student["id"], "Account suspended by admin")
                    st.warning("User suspended.")
                    st.rerun()

            if student["verified"]:
                if st.button("Revoke Verification Status", use_container_width=True):
                    conn.execute("UPDATE users SET verified=0 WHERE id=?", (student["id"],))
                    conn.commit()
                    log_admin_action(admin_user["id"], "REVOKE_VERIFICATION", student["id"])
                    st.rerun()
            else:
                if st.button("Verify & Approve Student Account", use_container_width=True):
                    conn.execute("UPDATE users SET verified=1 WHERE id=?", (student["id"],))
                    conn.commit()
                    log_admin_action(admin_user["id"], "GRANT_VERIFICATION", student["id"])
                    notify(student["id"], "Your student account has been approved and verified by the administration!")
                    st.success("Student approved & verified.")
                    st.rerun()

            st.divider()
            st.markdown("##### Danger Zone")
            if st.button("🗑️ Permanently Delete Account", use_container_width=True, type="primary"):
                delete_student_account(student["id"])
                log_admin_action(admin_user["id"], "DELETE_STUDENT_ACCOUNT", student["id"], f"Deleted student {student['student_id']}")
                st.session_state["admin_selected_student_id"] = None
                st.success("Student account permanently deleted.")
                st.rerun()

    with c2:
        with st.container(border=True):
            st.markdown("##### Balance & Reputation Adjustments")
            m1, m2 = st.columns(2)
            m1.metric("Trust Score", f"{student['trust_score']}/100")
            m2.metric("UniCoins", f"🪙 {student['unicoins']}")

            new_trust = st.slider("Adjust Trust Score", 0, 100, student["trust_score"], key="adjust_trust_slider")
            if st.button("Save Trust Score"):
                conn.execute("UPDATE users SET trust_score=? WHERE id=?", (new_trust, student["id"]))
                conn.commit()
                log_admin_action(admin_user["id"], "UPDATE_TRUST", student["id"], f"Trust score set to {new_trust}")
                st.success("Trust score updated!")
                st.rerun()

            st.write("")
            adj_col1, adj_col2 = st.columns(2)
            coin_adjust = adj_col1.number_input("Add/Deduct UniCoins", min_value=-500, max_value=500, value=10, step=5)
            reason = adj_col2.text_input("Reason", value="Admin Adjustment")
            if st.button("Apply UniCoin Change"):
                add_unicoins(student["id"], coin_adjust, reason)
                log_admin_action(admin_user["id"], "ADJUST_UNICOINS", student["id"], f"{coin_adjust} coins: {reason}")
                st.success(f"{coin_adjust:+d} UniCoins adjusted.")
                st.rerun()

    # --- IN-PERSON OFFICE SUMMON FEATURE ---
    st.write("")
    with st.container(border=True):
        st.markdown("##### 🏛️ Schedule In-Person Meeting / Office Summons")
        st.caption("Request this student to meet an administrator or proctor for verification or complaint clarification.")
        
        sum_loc = st.text_input("Office / Location", value="Proctor Office, Block 34 - Room 102")
        sum_time = st.text_input("Date & Time", value="Tomorrow at 3:00 PM")
        sum_reason = st.text_area("Reason for Meeting / Clarification", placeholder="e.g. Account verification or dispute clarification.")

        if st.button("📢 Send Official Office Summons", type="primary"):
            if not sum_loc.strip() or not sum_reason.strip():
                st.error("Please provide an office location and reason.")
            else:
                msg = f"🏛️ OFFICIAL SUMMONS: Please report to {sum_loc.strip()} on {sum_time.strip()} to meet administration. Reason: {sum_reason.strip()}"
                notify(student["id"], msg)
                send_realtime_email(student["email"], "UNI HELP — Official Administration Summons", f"Hello {student['full_name']},\n\n{msg}\n\n— UNI HELP Proctor Office")
                log_admin_action(admin_user["id"], "SEND_SUMMONS", student["id"], f"Summoned to {sum_loc}")
                st.success(f"Official summons dispatched to {student['full_name']}!")

    st.write("")
    st.markdown("##### Complete Student Activity Audit History")
    h_tab1, h_tab2, h_tab3, h_tab4 = st.tabs(["📦 Delivery Requests", "🤝 Borrowings", "🛠 Micro-Tasks", "💰 Transactions"])

    with h_tab1:
        delivs = conn.execute(
            """SELECT r.*, req.full_name requester_name, h.full_name helper_name 
               FROM requests r 
               JOIN users req ON req.id = r.requester_id 
               LEFT JOIN users h ON h.id = r.helper_id
               WHERE r.requester_id = ? OR r.helper_id = ? ORDER BY r.id DESC""",
            (student["id"], student["id"])
        ).fetchall()
        if not delivs:
            st.caption("No deliveries associated with this student.")
        for d in delivs:
            role = "Requester" if d["requester_id"] == student["id"] else "Helper"
            st.markdown(f"**Task ID: `{task_code(d['id'])}` — {d['item_name']}** (`{d['status']}`) — Role: **{role}** | Reward: ₹{d['reward']:.0f}")
            st.caption(f"Route: {d['pickup_location']} ➔ {d['destination']} | Created: {d['created_at'][:16]}")
            st.divider()

    with h_tab2:
        borrows = conn.execute(
            """SELECT b.*, bor.full_name borrower_name, len.full_name lender_name 
               FROM borrow_requests b
               JOIN users bor ON bor.id = b.borrower_id
               LEFT JOIN users len ON len.id = b.lender_id
               WHERE b.borrower_id = ? OR b.lender_id = ? ORDER BY b.id DESC""",
            (student["id"], student["id"])
        ).fetchall()
        if not borrows:
            st.caption("No borrowing history for this student.")
        for b in borrows:
            role = "Borrower" if b["borrower_id"] == student["id"] else "Lender"
            st.markdown(f"**Task ID: `{task_code(b['id'])}` — {b['item_name']}** (`{b['status']}`) — Role: **{role}** | Location: {b['location']}")
            st.caption(f"Borrower: {b['borrower_name']} | Lender: {b['lender_name'] or 'Unassigned'} | Date: {b['created_at'][:16]}")
            st.divider()

    with h_tab3:
        tsks = conn.execute(
            """SELECT t.*, c.full_name creator_name, h.full_name helper_name 
               FROM tasks t
               JOIN users c ON c.id = t.creator_id
               LEFT JOIN users h ON h.id = t.helper_id
               WHERE t.creator_id = ? OR t.helper_id = ? ORDER BY t.id DESC""",
            (student["id"], student["id"])
        ).fetchall()
        if not tsks:
            st.caption("No micro-tasks associated with this student.")
        for t in tsks:
            role = "Creator" if t["creator_id"] == student["id"] else "Helper"
            st.markdown(f"**Task ID: `{task_code(t['id'])}` — {t['title']}** (`{t['status']}`) — Role: **{role}** | Reward: ₹{t['reward']:.0f}")
            st.caption(f"Category: {t['category']} | Created: {t['created_at'][:16]}")
            st.divider()

    with h_tab4:
        txs = conn.execute(
            """SELECT * FROM transactions WHERE payer_id = ? OR payee_id = ? ORDER BY id DESC""",
            (student["id"], student["id"])
        ).fetchall()
        if not txs:
            st.caption("No transactions for this student.")
        for tx in txs:
            role = "Payer" if tx["payer_id"] == student["id"] else "Payee"
            st.markdown(f"**TXN #{tx['id']}** — ₹{tx['amount']:.0f} (`{tx['status']}`) | Role: **{role}** | Related: {tx['related_type']} `{task_code(tx['related_id'])}`")
            st.divider()

    conn.close()

def render_admin_workspace(user):
    top1, top2 = st.columns([3, 1])
    with top1:
        st.markdown("### 🛡️ Campus Administration Control Center")
        st.caption(f"Authenticated Officer: **{user['full_name']}** ({user['email']})")
    with top2:
        st.write("")
        if st.button("🚪 Logout of Admin Console", use_container_width=True):
            show_handshake_loader("Logging Out...")
            st.session_state["user"] = None
            st.session_state["admin_selected_student_id"] = None
            st.session_state["auth_mode"] = "student_login"
            st.rerun()

    st.divider()

    current_paused = is_platform_paused()
    with st.container(border=True):
        p_c1, p_c2 = st.columns([3.5, 1.5])
        with p_c1:
            if current_paused:
                st.error("🚨 **PLATFORM EMERGENCY STATUS: PAUSED / STOPPED**\n\nStudents cannot post or complete tasks right now.")
            else:
                st.success("🟢 **PLATFORM STATUS: ACTIVE & RUNNING**\n\nAll student services and micro-networks are online.")
        with p_c2:
            st.write("")
            if current_paused:
                if st.button("▶️ Resume UNI HELP Network", use_container_width=True, type="primary"):
                    set_platform_paused(False)
                    log_admin_action(user["id"], "RESUME_PLATFORM", None, "Emergency stop removed by admin")
                    st.success("Platform has been resumed!")
                    st.rerun()
            else:
                if st.button("⏸️ Emergency Stop / Pause App", use_container_width=True):
                    set_platform_paused(True)
                    log_admin_action(user["id"], "PAUSE_PLATFORM", None, "Emergency stop engaged by admin")
                    st.warning("Platform paused!")
                    st.rerun()

    if st.session_state.get("admin_selected_student_id"):
        render_admin_student_profile(user, st.session_state["admin_selected_student_id"])
        return

    conn = get_conn()
    students_count = conn.execute("SELECT COUNT(*) c FROM users WHERE role='student'").fetchone()["c"]
    pending_approvals = conn.execute("SELECT COUNT(*) c FROM users WHERE role='student' AND verified=0").fetchone()["c"]
    open_disputes = conn.execute("SELECT COUNT(*) c FROM disputes WHERE status IN ('OPEN','UNDER_REVIEW')").fetchone()["c"]
    held_escrow = conn.execute("SELECT COALESCE(SUM(amount), 0) s FROM transactions WHERE status='HELD'").fetchone()["s"]
    active_deliveries = conn.execute("SELECT COUNT(*) c FROM requests WHERE status NOT IN ('COMPLETED', 'CANCELLED')").fetchone()["c"]
    unread_admin_notifs = conn.execute("SELECT COUNT(*) c FROM admin_notifications WHERE is_read=0").fetchone()["c"]
    conn.close()

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Registered Students", students_count)
    m2.metric("Pending Approvals", pending_approvals)
    m3.metric("Open Disputes", open_disputes)
    m4.metric("Admin Alerts", unread_admin_notifs)
    m5.metric("Active Deliveries", active_deliveries)

    adm_tabs = st.tabs([
        "🔔 Admin Alerts",
        "🔍 Search & Lookup Order",
        "📦 Delivery Orders", 
        "🤝 Borrowing Requests", 
        "🛠 Micro-Task Orders", 
        "👥 Student Profiles", 
        "⚠️ Dispute Queue", 
        "💰 Escrow Ledger",
        "📢 Broadcast Notice"
    ])

    # ---------------- TAB 0: ADMIN ALERTS & ACTION RESOLUTION ----------------
    with adm_tabs[0]:
        st.markdown("#### 🔔 System Alerts & Immediate Actions")
        conn = get_conn()
        notifs = conn.execute("SELECT * FROM admin_notifications ORDER BY id DESC LIMIT 50").fetchall()
        conn.close()

        if st.button("Mark All Alerts as Read"):
            conn = get_conn()
            conn.execute("UPDATE admin_notifications SET is_read=1")
            conn.commit()
            conn.close()
            st.rerun()

        if not notifs:
            st.info("No incoming alerts at this time.")
        for n in notifs:
            with st.container(border=True):
                icon = "⚠️" if n["category"] == "DISPUTE" else "👤"
                badge = "🔴 UNREAD" if not n["is_read"] else "⚪ Read"
                st.markdown(f"**{icon} [{n['category']}] Ref: `{n['reference_id']}`** — `{badge}`")
                st.write(n["message"])
                st.caption(f"Logged at: {n['created_at'][:19].replace('T', ' ')}")

                if n["category"] == "DISPUTE":
                    parsed_d_id = parse_task_id(n["reference_id"])
                    act_c1, act_c2, act_c3, act_c4 = st.columns(4)
                    with act_c1:
                        if st.button("🔎 Review In Dispute Tab", key=f"alert_rev_{n['id']}", use_container_width=True):
                            conn = get_conn()
                            conn.execute("UPDATE admin_notifications SET is_read=1 WHERE id=?", (n["id"],))
                            conn.commit()
                            conn.close()
                            st.rerun()
                    with act_c2:
                        if st.button("✅ Resolve & Release", key=f"alert_res_{n['id']}", use_container_width=True):
                            conn = get_conn()
                            conn.execute("UPDATE disputes SET status='RESOLVED', resolved_at=? WHERE transaction_id=?", (now_iso(), parsed_d_id))
                            conn.execute("UPDATE admin_notifications SET is_read=1 WHERE id=?", (n["id"],))
                            conn.commit()
                            conn.close()
                            update_transaction_status("DELIVERY", parsed_d_id, "RELEASED")
                            update_transaction_status("BORROWING", parsed_d_id, "RELEASED")
                            update_transaction_status("TASK", parsed_d_id, "RELEASED")
                            st.success(f"Dispute {n['reference_id']} resolved and escrow released.")
                            st.rerun()
                    with act_c3:
                        if st.button("❌ Dismiss & Refund", key=f"alert_rej_{n['id']}", use_container_width=True):
                            conn = get_conn()
                            conn.execute("UPDATE disputes SET status='REJECTED', resolved_at=? WHERE transaction_id=?", (now_iso(), parsed_d_id))
                            conn.execute("UPDATE admin_notifications SET is_read=1 WHERE id=?", (n["id"],))
                            conn.commit()
                            conn.close()
                            update_transaction_status("DELIVERY", parsed_d_id, "CANCELLED")
                            update_transaction_status("BORROWING", parsed_d_id, "CANCELLED")
                            update_transaction_status("TASK", parsed_d_id, "CANCELLED")
                            st.info(f"Dispute {n['reference_id']} dismissed and refunded.")
                            st.rerun()
                    with act_c4:
                        if st.button("Dismiss Alert", key=f"alert_dism_{n['id']}", use_container_width=True):
                            conn = get_conn()
                            conn.execute("UPDATE admin_notifications SET is_read=1 WHERE id=?", (n["id"],))
                            conn.commit()
                            conn.close()
                            st.rerun()

                elif n["category"] == "NEW_ACCOUNT":
                    act_c1, act_c2 = st.columns(2)
                    with act_c1:
                        if st.button("✅ Approve Student Registration", key=f"alert_appr_{n['id']}", use_container_width=True):
                            conn = get_conn()
                            conn.execute("UPDATE users SET verified=1 WHERE student_id=?", (n["reference_id"],))
                            conn.execute("UPDATE admin_notifications SET is_read=1 WHERE id=?", (n["id"],))
                            u_target = conn.execute("SELECT id FROM users WHERE student_id=?", (n["reference_id"],)).fetchone()
                            conn.commit()
                            conn.close()
                            if u_target:
                                notify(u_target["id"], "Your student account registration has been approved by the admin!")
                            st.success(f"Approved student account {n['reference_id']}!")
                            st.rerun()
                    with act_c2:
                        if st.button("Dismiss Alert", key=f"alert_dism_u_{n['id']}", use_container_width=True):
                            conn = get_conn()
                            conn.execute("UPDATE admin_notifications SET is_read=1 WHERE id=?", (n["id"],))
                            conn.commit()
                            conn.close()
                            st.rerun()

    # ---------------- TAB 1: INSTANT SEARCH BY TASK ID ----------------
    with adm_tabs[1]:
        st.markdown("#### 🔍 Instant Order Search & Control")
        st.caption("Look up any delivery, borrow request, or task directly by entering its UNIH Task ID (e.g., `UNIH0004` or `4`).")

        search_id_input = st.text_input("Enter Task ID", placeholder="UNIH0001", key="search_task_input_box").strip()
        parsed_id = parse_task_id(search_id_input) if search_id_input else None

        if parsed_id:
            conn = get_conn()
            req = conn.execute(
                """SELECT r.*, req.full_name requester_name, h.full_name helper_name 
                   FROM requests r 
                   JOIN users req ON req.id = r.requester_id 
                   LEFT JOIN users h ON h.id = r.helper_id 
                   WHERE r.id = ?""", (parsed_id,)
            ).fetchone()

            bor = conn.execute(
                """SELECT b.*, bor.full_name borrower_name, len.full_name lender_name 
                   FROM borrow_requests b
                   JOIN users bor ON bor.id = b.borrower_id 
                   LEFT JOIN users len ON len.id = b.lender_id 
                   WHERE b.id = ?""", (parsed_id,)
            ).fetchone()

            tsk = conn.execute(
                """SELECT t.*, c.full_name creator_name, h.full_name helper_name 
                   FROM tasks t
                   JOIN users c ON c.id = t.creator_id 
                   LEFT JOIN users h ON h.id = t.helper_id 
                   WHERE t.id = ?""", (parsed_id,)
            ).fetchone()
            conn.close()

            found_any = False

            if req:
                found_any = True
                with st.container(border=True):
                    st.markdown(f"##### 📦 Delivery Order: `{task_code(req['id'])}` — {req['item_name']}")
                    st.caption(f"Status: **{req['status']}** | Requester: **{req['requester_name']}** | Helper: **{req['helper_name'] or 'None'}** | Reward: ₹{req['reward']:.0f}")
                    st.write(f"**Route:** {req['pickup_location']} ➔ {req['destination']}")

                    b_c1, b_c2, b_c3 = st.columns(3)
                    with b_c1:
                        if req["status"] in ("ACCEPTED", "PICKUP_VERIFIED"):
                            if st.button("⚡ Permit OTP-less Pickup Handover", key=f"bypass_del_{req['id']}"):
                                conn = get_conn()
                                conn.execute("UPDATE requests SET status='PICKUP_VERIFIED', pickup_verified_at=? WHERE id=?", (now_iso(), req["id"]))
                                conn.commit(); conn.close()
                                log_admin_action(user["id"], "ADMIN_BYPASS_PICKUP_OTP", req["id"], "Pickup approved without OTP")
                                st.success("OTP-less handover approved! Status updated to Pickup Verified.")
                                st.rerun()
                    with b_c2:
                        if req["status"] not in ("COMPLETED", "CANCELLED"):
                            if st.button("Force Complete & Release Escrow", key=f"srch_fc_del_{req['id']}"):
                                conn = get_conn()
                                conn.execute("UPDATE requests SET status='COMPLETED', completed_at=? WHERE id=?", (now_iso(), req["id"]))
                                conn.commit(); conn.close()
                                update_transaction_status("DELIVERY", req["id"], "RELEASED")
                                log_admin_action(user["id"], "FORCE_COMPLETE_DELIVERY", req["id"])
                                st.success("Force completed.")
                                st.rerun()
                    with b_c3:
                        if st.button("🗑️ Delete Order Permanently", key=f"del_srch_req_{req['id']}"):
                            delete_order("DELIVERY", req["id"])
                            log_admin_action(user["id"], "DELETE_ORDER", req["id"], "Deleted delivery order")
                            st.warning("Order deleted.")
                            st.rerun()

            if bor:
                found_any = True
                with st.container(border=True):
                    st.markdown(f"##### 🤝 Borrow Request: `{task_code(bor['id'])}` — {bor['item_name']}")
                    st.caption(f"Status: **{bor['status']}** | Location: **{bor['location']}** | Borrower: **{bor['borrower_name']}** | Lender: **{bor['lender_name'] or 'Unassigned'}**")

                    b_c1, b_c2 = st.columns(2)
                    with b_c1:
                        if bor["status"] in ("ACCEPTED", "ACTIVE"):
                            if st.button("Force Complete & Release Escrow", key=f"srch_fc_bor_{bor['id']}"):
                                conn = get_conn()
                                conn.execute("UPDATE borrow_requests SET status='COMPLETED', returned_at=? WHERE id=?", (now_iso(), bor["id"]))
                                conn.commit(); conn.close()
                                update_transaction_status("BORROW_DEPOSIT", bor["id"], "RELEASED")
                                update_transaction_status("BORROW_REWARD", bor["id"], "RELEASED")
                                log_admin_action(user["id"], "FORCE_COMPLETE_BORROWING", bor["id"])
                                st.success("Borrow request closed and funds released.")
                                st.rerun()
                    with b_c2:
                        if st.button("🗑️ Delete Borrow Request", key=f"del_srch_bor_{bor['id']}"):
                            delete_order("BORROWING", bor["id"])
                            log_admin_action(user["id"], "DELETE_ORDER", bor["id"], "Deleted borrow request")
                            st.warning("Request deleted.")
                            st.rerun()

            if tsk:
                found_any = True
                with st.container(border=True):
                    st.markdown(f"##### 🛠 Micro-Task: `{task_code(tsk['id'])}` — {tsk['title']}")
                    st.caption(f"Status: **{tsk['status']}** | Creator: **{tsk['creator_name']}** | Helper: **{tsk['helper_name'] or 'None'}** | Reward: ₹{tsk['reward']:.0f}")
                    st.write(tsk["description"])

                    b_c1, b_c2 = st.columns(2)
                    with b_c1:
                        if tsk["status"] not in ("COMPLETED", "CANCELLED"):
                            if st.button("Force Complete Task", key=f"srch_fc_tsk_{tsk['id']}"):
                                conn = get_conn()
                                conn.execute("UPDATE tasks SET status='COMPLETED' WHERE id=?", (tsk["id"],))
                                conn.commit(); conn.close()
                                update_transaction_status("TASK", tsk["id"], "RELEASED")
                                log_admin_action(user["id"], "FORCE_COMPLETE_TASK", tsk["id"])
                                st.success("Task completed.")
                                st.rerun()
                    with b_c2:
                        if st.button("🗑️ Delete Task Permanently", key=f"del_srch_tsk_{tsk['id']}"):
                            delete_order("TASK", tsk["id"])
                            log_admin_action(user["id"], "DELETE_ORDER", tsk["id"], "Deleted micro-task")
                            st.warning("Task deleted.")
                            st.rerun()

            if not found_any:
                st.warning(f"No order found matching Task ID `{task_code(parsed_id)}`.")

    # 2. Delivery Orders
    with adm_tabs[2]:
        st.markdown("#### Manage Delivery Orders")
        status_filter = st.selectbox("Filter Status", ["ALL", "CREATED", "ACCEPTED", "PICKUP_VERIFIED", "DELIVERED", "COMPLETED", "CANCELLED"], key="deliv_filter")
        
        conn = get_conn()
        query = """SELECT r.*, req.full_name requester_name, h.full_name helper_name 
                   FROM requests r 
                   JOIN users req ON req.id = r.requester_id 
                   LEFT JOIN users h ON h.id = r.helper_id"""
        if status_filter != "ALL":
            query += f" WHERE r.status = '{status_filter}'"
        query += " ORDER BY r.id DESC"
        deliv_rows = conn.execute(query).fetchall()
        conn.close()

        if not deliv_rows:
            st.info("No delivery orders match this filter.")
        for r in deliv_rows:
            with st.container(border=True):
                d_c1, d_c2 = st.columns([3, 1.8])
                code = task_code(r["id"])
                with d_c1:
                    st.markdown(f"**Task ID: `{code}` — {r['item_name']}** — ₹{r['reward']:.0f} | Status: `{r['status']}`")
                    st.caption(f"Requester: **{r['requester_name']}** | Helper: **{r['helper_name'] or 'Unassigned'}**")
                    st.caption(f"Route: {r['pickup_location']} ➔ {r['destination']} | Created: {r['created_at'][:16]}")
                with d_c2:
                    if r["status"] == "ACCEPTED":
                        if st.button("⚡ Permit OTP-less Handover", key=f"bypass_list_{r['id']}", use_container_width=True):
                            conn = get_conn()
                            conn.execute("UPDATE requests SET status='PICKUP_VERIFIED', pickup_verified_at=? WHERE id=?", (now_iso(), r["id"]))
                            conn.commit(); conn.close()
                            log_admin_action(user["id"], "ADMIN_BYPASS_PICKUP_OTP", r["id"])
                            st.success("OTP-less handover approved!")
                            st.rerun()

                    if r["status"] not in ("COMPLETED", "CANCELLED"):
                        if st.button("Force Complete & Pay", key=f"force_comp_del_{r['id']}", use_container_width=True):
                            conn = get_conn()
                            conn.execute("UPDATE requests SET status='COMPLETED', completed_at=? WHERE id=?", (now_iso(), r["id"]))
                            conn.commit(); conn.close()
                            update_transaction_status("DELIVERY", r["id"], "RELEASED")
                            log_admin_action(user["id"], "FORCE_COMPLETE_DELIVERY", r["id"])
                            st.success(f"Task {code} completed.")
                            st.rerun()

                        if st.button("Cancel & Refund", key=f"force_cancel_del_{r['id']}", use_container_width=True):
                            conn = get_conn()
                            conn.execute("UPDATE requests SET status='CANCELLED' WHERE id=?", (r["id"],))
                            conn.commit(); conn.close()
                            update_transaction_status("DELIVERY", r["id"], "CANCELLED")
                            log_admin_action(user["id"], "FORCE_CANCEL_DELIVERY", r["id"])
                            st.warning(f"Task {code} cancelled.")
                            st.rerun()

                    if st.button("🗑️ Delete Order", key=f"del_deliv_{r['id']}", use_container_width=True):
                        delete_order("DELIVERY", r["id"])
                        log_admin_action(user["id"], "DELETE_ORDER", r["id"], "Deleted delivery order")
                        st.warning(f"Task {code} deleted.")
                        st.rerun()

    # 3. Borrowing Requests
    with adm_tabs[3]:
        st.markdown("#### Manage Borrow Requests")
        conn = get_conn()
        borrow_reqs = conn.execute(
            """SELECT b.*, bor.full_name borrower_name, len.full_name lender_name 
               FROM borrow_requests b
               JOIN users bor ON bor.id = b.borrower_id 
               LEFT JOIN users len ON len.id = b.lender_id 
               ORDER BY b.id DESC"""
        ).fetchall()
        conn.close()

        if not borrow_reqs:
            st.info("No borrow requests logged.")
        for b in borrow_reqs:
            with st.container(border=True):
                b_c1, b_c2 = st.columns([3, 1.8])
                code = task_code(b["id"])
                with b_c1:
                    st.markdown(f"**Task ID: `{code}` — {b['item_name']}** ({b['category']}) | Status: `{b['status']}`")
                    st.caption(f"Location: **{b['location']}** | Borrower: **{b['borrower_name']}** | Lender: **{b['lender_name'] or 'Unassigned'}**")
                    st.caption(f"Duration: {b['duration']} | Deposit: ₹{b['deposit']:.0f} | Reward: ₹{b['reward']:.0f}")
                with b_c2:
                    if b["status"] in ("ACCEPTED", "ACTIVE"):
                        if st.button("Force Complete & Release Escrow", key=f"force_comp_bor_{b['id']}", use_container_width=True):
                            conn = get_conn()
                            conn.execute("UPDATE borrow_requests SET status='COMPLETED', returned_at=? WHERE id=?", (now_iso(), b["id"]))
                            conn.commit(); conn.close()
                            update_transaction_status("BORROW_DEPOSIT", b["id"], "RELEASED")
                            update_transaction_status("BORROW_REWARD", b["id"], "RELEASED")
                            log_admin_action(user["id"], "FORCE_COMPLETE_BORROWING", b["id"])
                            st.success(f"Borrow request {code} closed.")
                            st.rerun()

                    if st.button("🗑️ Delete Request", key=f"del_bor_{b['id']}", use_container_width=True):
                        delete_order("BORROWING", b["id"])
                        log_admin_action(user["id"], "DELETE_ORDER", b["id"], "Deleted borrow request")
                        st.warning(f"Borrow request {code} deleted.")
                        st.rerun()

    # 4. Micro-Task Orders
    with adm_tabs[4]:
        st.markdown("#### Manage Micro-Task Gigs")
        conn = get_conn()
        tasks = conn.execute(
            """SELECT t.*, c.full_name creator_name, h.full_name helper_name 
               FROM tasks t
               JOIN users c ON c.id = t.creator_id 
               LEFT JOIN users h ON h.id = t.helper_id 
               ORDER BY t.id DESC"""
        ).fetchall()
        conn.close()

        if not tasks:
            st.info("No micro-tasks currently logged.")
        for t in tasks:
            with st.container(border=True):
                t_c1, t_c2 = st.columns([3, 1.8])
                code = task_code(t["id"])
                with t_c1:
                    st.markdown(f"**Task ID: `{code}` — {t['title']}** — ₹{t['reward']:.0f} | Status: `{t['status']}`")
                    st.caption(f"Creator: **{t['creator_name']}** | Helper: **{t['helper_name'] or 'Unassigned'}** | Deadline: {t['deadline']}")
                    st.write(t["description"])
                with t_c2:
                    if t["status"] not in ("COMPLETED", "CANCELLED"):
                        if st.button("Force Complete Task", key=f"force_comp_tsk_{t['id']}", use_container_width=True):
                            conn = get_conn()
                            conn.execute("UPDATE tasks SET status='COMPLETED' WHERE id=?", (t["id"],))
                            conn.commit(); conn.close()
                            update_transaction_status("TASK", t["id"], "RELEASED")
                            log_admin_action(user["id"], "FORCE_COMPLETE_TASK", t["id"])
                            st.success(f"Task {code} completed.")
                            st.rerun()

                    if st.button("🗑️ Delete Task", key=f"del_tsk_{t['id']}", use_container_width=True):
                        delete_order("TASK", t["id"])
                        log_admin_action(user["id"], "DELETE_ORDER", t["id"], "Deleted micro-task")
                        st.warning(f"Task {code} deleted.")
                        st.rerun()

    # 5. Student Directory
    with adm_tabs[5]:
        st.markdown("#### Student Directory & Account Management")
        conn = get_conn()
        students = conn.execute("SELECT * FROM users WHERE role='student' ORDER BY verified ASC, id DESC").fetchall()
        conn.close()

        search_query = st.text_input("🔍 Search Student (by Name, Student ID, or Email)", placeholder="Search student...", key="admin_stud_search_box").strip().lower()

        filtered_students = [
            s for s in students 
            if search_query in s["full_name"].lower() or search_query in s["student_id"].lower() or search_query in s["email"].lower()
        ] if search_query else students

        if not filtered_students:
            st.info("No matching students found.")
        for s in filtered_students:
            with st.container(border=True):
                c_info, c_action = st.columns([3, 1.2])
                with c_info:
                    approval_tag = "🟡 AWAITING APPROVAL" if not s["verified"] else "🟢 VERIFIED"
                    st.markdown(f"**{s['full_name']}** (`{s['student_id']}`) — `{approval_tag}`")
                    st.caption(f"Email: {s['email']} | Phone: {s['phone'] or '—'} | Status: `{'🔴 Suspended' if s['is_suspended'] else '🟢 Active'}`")
                with c_action:
                    if not s["verified"]:
                        if st.button("✅ Approve Account", key=f"appr_btn_{s['id']}", use_container_width=True):
                            conn = get_conn()
                            conn.execute("UPDATE users SET verified=1 WHERE id=?", (s["id"],))
                            conn.commit()
                            conn.close()
                            notify(s["id"], "Your student account registration has been approved by admin!")
                            log_admin_action(user["id"], "APPROVE_ACCOUNT", s["id"], f"Approved account {s['student_id']}")
                            st.success(f"Approved {s['full_name']}!")
                            st.rerun()
                    if st.button("Manage Profile →", key=f"view_stu_{s['id']}", use_container_width=True):
                        st.session_state["admin_selected_student_id"] = s["id"]
                        st.rerun()

    # 6. Dispute Queue
    with adm_tabs[6]:
        st.markdown("#### Community Safety & Dispute Arbitration")
        conn = get_conn()
        disputes = conn.execute(
            """SELECT d.*, u.full_name reporter_name, u.email reporter_email, u.id reporter_uid FROM disputes d
               JOIN users u ON u.id = d.reporter_id ORDER BY d.id DESC"""
        ).fetchall()
        conn.close()

        if not disputes:
            st.success("No disputes currently open.")
        for d in disputes:
            with st.container(border=True):
                st.markdown(f"**Dispute #{d['id']} — {d['category']}** on {d['transaction_type']} `{task_code(d['transaction_id'])}`")
                st.caption(f"Reporter: **{d['reporter_name']}** (`{d['reporter_email']}`) | Status: `{d['status']}`")
                st.write(d["description"] or "No description provided.")

                if d["status"] in ("OPEN", "UNDER_REVIEW"):
                    b1, b2 = st.columns(2)
                    if b1.button("Resolve & Release Escrow", key=f"res_{d['id']}"):
                        conn = get_conn()
                        conn.execute("UPDATE disputes SET status='RESOLVED', resolved_at=? WHERE id=?", (now_iso(), d["id"]))
                        conn.commit(); conn.close()
                        update_transaction_status(d["transaction_type"], d["transaction_id"], "RELEASED")
                        log_admin_action(user["id"], "RESOLVE_DISPUTE", d["id"])
                        st.success("Dispute resolved.")
                        st.rerun()
                    if b2.button("Dismiss & Refund Requester", key=f"rej_{d['id']}"):
                        conn = get_conn()
                        conn.execute("UPDATE disputes SET status='REJECTED', resolved_at=? WHERE id=?", (now_iso(), d["id"]))
                        conn.commit(); conn.close()
                        update_transaction_status(d["transaction_type"], d["transaction_id"], "CANCELLED")
                        log_admin_action(user["id"], "REJECT_DISPUTE", d["id"])
                        st.info("Dispute dismissed.")
                        st.rerun()

                # --- SUMMON COMPLAINTER FOR CLARIFICATION ---
                with st.expander(f"🏛️ Summon {d['reporter_name']} to Office for Clarification"):
                    s_loc = st.text_input("Office Location", value="Proctor Office, Block 34 - Room 102", key=f"d_loc_{d['id']}")
                    s_time = st.text_input("Date & Time", value="Tomorrow at 3:00 PM", key=f"d_time_{d['id']}")
                    s_rsn = st.text_area("Meeting Reason / Discussion", value=f"Clarification regarding Dispute #{d['id']} on {d['transaction_type']} {task_code(d['transaction_id'])}", key=f"d_rsn_{d['id']}")
                    if st.button("Send Official Summons Email & Notification", key=f"send_sum_{d['id']}", type="primary"):
                        msg = f"🏛️ OFFICIAL SUMMONS: Please report to {s_loc} on {s_time} regarding Dispute #{d['id']}. Reason: {s_rsn}"
                        notify(d["reporter_uid"], msg)
                        send_realtime_email(d["reporter_email"], "UNI HELP — Official Administration Summons", f"Hello {d['reporter_name']},\n\n{msg}\n\n— UNI HELP Proctor Office")
                        log_admin_action(user["id"], "SEND_DISPUTE_SUMMONS", d["reporter_uid"], f"Summoned regarding dispute #{d['id']}")
                        st.success(f"Summons successfully sent to {d['reporter_name']}!")

    # 7. Escrow Ledger
    with adm_tabs[7]:
        st.markdown("#### Global Escrow & Financial Audit Ledger")
        conn = get_conn()
        txs = conn.execute(
            """SELECT t.*, p.full_name payer_name, py.full_name payee_name 
               FROM transactions t
               LEFT JOIN users p ON p.id = t.payer_id
               LEFT JOIN users py ON py.id = t.payee_id
               ORDER BY t.id DESC LIMIT 100"""
        ).fetchall()
        conn.close()

        for tx in txs:
            st.markdown(
                f"**TXN #{tx['id']} — ₹{tx['amount']:.0f}** (`{tx['status']}`) | "
                f"Payer: {tx['payer_name'] or 'Platform'} ➔ Payee: {tx['payee_name'] or 'Platform'} | "
                f"Source: {tx['related_type']} `{task_code(tx['related_id'])}`"
            )
            st.divider()

    # 8. Broadcast Notice
    with adm_tabs[8]:
        st.markdown("#### Campus-Wide Broadcast Center")
        st.caption("Send notifications directly to all student dashboards.")

        notice_text = st.text_area("Broadcast Announcement", placeholder="e.g. Maintenance scheduled tonight or safety notice.")
        send_email_copy = st.checkbox("Also attempt email delivery to all registered students", value=False)

        if st.button("📢 Send Broadcast Announcement", type="primary"):
            if not notice_text.strip():
                st.error("Please enter a notice message.")
            else:
                conn = get_conn()
                students = conn.execute("SELECT id, email, full_name FROM users WHERE role='student' AND is_suspended=0").fetchall()
                for s in students:
                    notify(s["id"], f"📢 ANNOUNCEMENT: {notice_text.strip()}")
                    if send_email_copy:
                        send_realtime_email(s["email"], "UNI HELP — Campus Announcement", notice_text.strip())
                conn.close()
                log_admin_action(user["id"], "BROADCAST_NOTICE", None, notice_text[:50])
                st.success(f"Announcement broadcasted to {len(students)} active students!")

# =============================================================================
# 7. STUDENT WORKSPACE
# =============================================================================

def render_student_workspace(user):
    if is_platform_paused():
        st.markdown("### ⏸️ Platform Paused")
        st.warning("Maintenance in progress.")
        if st.button("Logout"):
            st.session_state["user"] = None
            st.rerun()
        return

    c1, c2 = st.columns([3, 1])
    c1.markdown(f"### 🎓 UNI HELP")
    if c2.button("Logout"):
        show_handshake_loader("Logging Out...")
        st.session_state["user"] = None
        st.session_state["auth_mode"] = "student_login"
        st.rerun()

    conn = get_conn()
    unreads = conn.execute("SELECT * FROM notifications WHERE user_id=? AND is_read=0", (user["id"],)).fetchall()
    conn.close()

    if unreads:
        with st.container(border=True):
            st.markdown(f"🔔 **{len(unreads)} Unread Notice(s)**")
            for u in unreads[:2]:
                st.write(f"• {u['message']}")
            if st.button("Mark Read"):
                conn = get_conn()
                conn.execute("UPDATE notifications SET is_read=1 WHERE user_id=?", (user["id"],))
                conn.commit(); conn.close()
                st.rerun()

    st.write("")

    tabs = st.tabs(["📦 Deliveries", "🤝 Borrows", "🛠 Tasks", "💰 Wallet", "👤 Profile", "⚠️ Disputes", "🔔 Inbox"])

    with tabs[0]:
        mode = st.radio("Mode", ["Browse", "Post"], horizontal=True, label_visibility="collapsed")
        if mode == "Post":
            with st.form("d_post"):
                iname = st.text_input("Item")
                desc = st.text_input("Instructions")
                p = st.text_input("Pickup", value="Main Gate")
                d = st.text_input("Destination", value="Hostel")
                rew = st.number_input("Reward (₹)", value=30.0)
                if st.form_submit_button("Post Request", type="primary"):
                    show_handshake_loader("Publishing Request...")
                    conn = get_conn()
                    conn.execute("INSERT INTO requests (requester_id, item_name, description, pickup_location, destination, reward, status, created_at) VALUES (?,?,?,?,?,?,'CREATED',?)",
                                 (user["id"], iname, desc, p, d, rew, now_iso()))
                    conn.commit(); conn.close()
                    st.success("Posted!")
                    st.rerun()
        else:
            conn = get_conn()
            for r in conn.execute("SELECT r.*, u.full_name req FROM requests r JOIN users u ON u.id=r.requester_id ORDER BY r.id DESC").fetchall():
                with st.container(border=True):
                    code = task_code(r["id"])
                    st.write(f"**{code} — {r['item_name']}** (₹{r['reward']:.0f})")
                    st.caption(f"📍 {r['pickup_location']} ➔ {r['destination']} | {estimate_campus_distance(r['pickup_location'])}")
                    if r["status"] == "CREATED" and r["requester_id"] != user["id"]:
                        if st.button("Accept", key=f"ac_d_{r['id']}"):
                            show_handshake_loader("Accepting Delivery...")
                            conn2 = get_conn()
                            conn2.execute("UPDATE requests SET helper_id=?, status='ACCEPTED' WHERE id=?", (user["id"], r["id"]))
                            conn2.commit(); conn2.close()
                            create_transaction(r["requester_id"], user["id"], "DELIVERY", r["id"], r["reward"], "HELD")
                            st.rerun()
            conn.close()

    with tabs[1]:
        bmode = st.radio("BMode", ["Browse Borrows", "Post Borrow"], horizontal=True, label_visibility="collapsed")
        if bmode == "Post Borrow":
            selected_block = st.selectbox("Block", options=list(CAMPUS_LOCATION_MAP.keys()))
            selected_room = st.selectbox("Room", options=CAMPUS_LOCATION_MAP[selected_block])
            loc = f"{selected_block} - {selected_room}"
            with st.form("b_post"):
                iname = st.text_input("Item Needed")
                dur = st.text_input("Duration")
                rew = st.number_input("Reward (₹)", value=20.0)
                dep = st.number_input("Deposit (₹)", value=100.0)
                if st.form_submit_button("Post Borrow Request", type="primary"):
                    show_handshake_loader("Posting Request...")
                    conn = get_conn()
                    conn.execute("INSERT INTO borrow_requests (borrower_id, item_name, category, location, description, duration, deposit, reward, status, created_at) VALUES (?,?,'General',?,'',?,?,?,'REQUESTED',?)",
                                 (user["id"], iname, loc, dur, dep, rew, now_iso()))
                    conn.commit(); conn.close()
                    st.success("Posted borrow request!")
                    st.rerun()
        else:
            conn = get_conn()
            for b in conn.execute("SELECT b.*, u.full_name bor FROM borrow_requests b JOIN users u ON u.id=b.borrower_id ORDER BY b.id DESC").fetchall():
                with st.container(border=True):
                    code = task_code(b["id"])
                    st.write(f"**{code} — {b['item_name']}**")
                    st.caption(f"📍 {b['location']} | {estimate_campus_distance(b['location'])}")
                    if b["status"] == "REQUESTED" and b["borrower_id"] != user["id"]:
                        if st.button("Lend This", key=f"lend_{b['id']}"):
                            show_handshake_loader("Accepting Borrow Request...")
                            conn2 = get_conn()
                            conn2.execute("UPDATE borrow_requests SET lender_id=?, status='ACCEPTED' WHERE id=?", (user["id"], b["id"]))
                            conn2.commit(); conn2.close()
                            st.success("Accepted!")
                            st.rerun()
            conn.close()

    with tabs[2]:
        st.markdown("#### Micro-Tasks")
        conn = get_conn()
        for t in conn.execute("SELECT * FROM tasks ORDER BY id DESC").fetchall():
            with st.container(border=True):
                st.write(f"**{t['title']}** — ₹{t['reward']}")
        conn.close()

    with tabs[3]:
        st.markdown("#### Wallet")
        st.metric("UniCoins", f"🪙 {user['unicoins']}")

    with tabs[4]:
        st.markdown(f"#### {user['full_name']}")
        st.write(f"**ID:** {user['student_id']}")
        st.write(f"**Trust Score:** {user['trust_score']}/100")

    with tabs[5]:
        st.markdown("#### Report Dispute & Queries")
        st.info("For any queries, complaints, or support, please mail us directly at **unihelp.lpu@gmail.com**.")
        with st.form("disp"):
            tid = st.text_input("Task ID (e.g. UNIH0001 or 1)")
            cat = st.selectbox("Category", ["Item Damaged", "No-Show", "Other"])
            exp = st.text_area("Details")
            if st.form_submit_button("Submit Dispute", type="primary"):
                pid = parse_task_id(tid)
                if pid:
                    show_handshake_loader("Filing Dispute...")
                    conn = get_conn()
                    conn.execute("INSERT INTO disputes (transaction_type, transaction_id, reporter_id, category, description, status, created_at) VALUES ('DELIVERY', ?, ?, ?, ?, 'OPEN', ?)",
                                 (pid, user["id"], cat, exp, now_iso()))
                    conn.commit(); conn.close()
                    notify_admin("DISPUTE", task_code(pid), f"Dispute filed by {user['full_name']}")
                    st.success("Dispute filed.")
                    st.rerun()

    with tabs[6]:
        st.markdown("#### Inbox")
        conn = get_conn()
        for n in conn.execute("SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC", (user["id"],)).fetchall():
            with st.container(border=True):
                st.write(n["message"])
        conn.close()

# =============================================================================
# 8. MAIN CONTROLLER
# =============================================================================

def main():
    user = st.session_state.get("user")
    if user is None:
        mode = st.session_state.get("auth_mode", "student_login")
        if mode == "admin_login":
            render_admin_login()
        elif mode == "student_otp":
            render_student_otp()
        elif mode == "forgot_password":
            render_forgot_password()
        elif mode == "reset_password":
            render_reset_password()
        elif mode == "register":
            render_registration()
        else:
            render_student_login()
        return

    if user.get("role") == "admin":
        render_admin_workspace(user)
    else:
        render_student_workspace(user)

if __name__ == "__main__":
    main()
