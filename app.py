"""
UNI HELP - "Your campus. Your community. Someone can help."
A university-verified student-to-student campus assistance platform.

Single-file Streamlit prototype. Run with:
    streamlit run app.py
"""

import os
import sqlite3
import secrets
import string
import smtplib
import ssl
import math
import io
import time
import re
from email.mime.text import MIMEText
from datetime import datetime, timedelta

import streamlit as st
from werkzeug.security import generate_password_hash, check_password_hash
import qrcode
from dotenv import load_dotenv

# MSG91 is used for real SMS OTP delivery.
# Credentials are read only from Streamlit Secrets; nothing is hard-coded.
try:
    import urllib.request
    import urllib.parse
    import json
    MSG91_HTTP_AVAILABLE = True
except ImportError:
    MSG91_HTTP_AVAILABLE = False

# =============================================================================
# 0. CONFIG / ENVIRONMENT
# =============================================================================

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "database.db")
UPLOADS_DIR = os.path.join(BASE_DIR, "uploads")
QR_DIR = os.path.join(UPLOADS_DIR, "qr")
PHOTOS_DIR = os.path.join(UPLOADS_DIR, "photos")

UNIVERSITY_EMAIL_DOMAIN = os.getenv("UNIVERSITY_EMAIL_DOMAIN", "@student.university.edu")
OTP_EXPIRY_MINUTES = 5
OTP_MAX_ATTEMPTS = int(os.getenv("OTP_MAX_ATTEMPTS", "5"))
LOCATION_RADIUS_METERS = float(os.getenv("LOCATION_RADIUS_METERS", "100"))
QR_EXPIRY_MINUTES = int(os.getenv("QR_EXPIRY_MINUTES", "30"))
SECRET_KEY = os.getenv("SECRET_KEY", secrets.token_hex(16))

# SMTP credentials are intentionally loaded from Streamlit Secrets.
# No SMTP credential is hardcoded in the application.
try:
    SMTP_HOST = str(st.secrets.get("SMTP_HOST", "smtp.gmail.com")).strip()
    SMTP_PORT = int(st.secrets.get("SMTP_PORT", 587))
    SMTP_USERNAME = str(st.secrets.get("SMTP_USERNAME", "")).strip()
    SMTP_PASSWORD = str(st.secrets.get("SMTP_PASSWORD", "")).strip()
except Exception:
    SMTP_HOST = "smtp.gmail.com"
    SMTP_PORT = 587
    SMTP_USERNAME = ""
    SMTP_PASSWORD = ""

EMAIL_CONFIGURED = bool(SMTP_HOST and SMTP_PORT and SMTP_USERNAME and SMTP_PASSWORD)

try:
    MSG91_AUTHKEY = str(st.secrets.get("MSG91_AUTHKEY", "")).strip()
    MSG91_OTP_TEMPLATE_ID = str(st.secrets.get("MSG91_OTP_TEMPLATE_ID", "")).strip()
except Exception:
    MSG91_AUTHKEY = ""
    MSG91_OTP_TEMPLATE_ID = ""

# MSG91's OTP API needs an approved OTP template. For Indian numbers this is
# especially important because SMS delivery follows DLT/template requirements.
SMS_CONFIGURED = bool(MSG91_HTTP_AVAILABLE and MSG91_AUTHKEY and MSG91_OTP_TEMPLATE_ID)
SMS_RESEND_SECONDS = 30

MIN_REWARD = float(os.getenv("MIN_REWARD", "0"))
MAX_REWARD = float(os.getenv("MAX_REWARD", "5000"))

ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@unihelp.local")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "AdminUniHelp123!")

for d in (UPLOADS_DIR, QR_DIR, PHOTOS_DIR):
    os.makedirs(d, exist_ok=True)


# =============================================================================
# 1. DATABASE
# =============================================================================

def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=15)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    phone TEXT,
    student_id TEXT,
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

CREATE TABLE IF NOT EXISTS requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    requester_id INTEGER NOT NULL REFERENCES users(id),
    helper_id INTEGER REFERENCES users(id),
    item_name TEXT NOT NULL,
    description TEXT,
    pickup_location TEXT NOT NULL,
    destination TEXT NOT NULL,
    pickup_lat REAL,
    pickup_lng REAL,
    dest_lat REAL,
    dest_lng REAL,
    reward REAL NOT NULL,
    preferred_time TEXT,
    notes TEXT,
    status TEXT NOT NULL DEFAULT 'CREATED',
    created_at TEXT NOT NULL,
    accepted_at TEXT,
    pickup_verified_at TEXT,
    delivered_at TEXT,
    completed_at TEXT,
    cancelled_at TEXT
);

CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER NOT NULL REFERENCES users(id),
    item_name TEXT NOT NULL,
    category TEXT,
    description TEXT,
    condition TEXT,
    photo_path TEXT,
    availability TEXT,
    deposit REAL NOT NULL DEFAULT 0,
    rules TEXT,
    status TEXT NOT NULL DEFAULT 'AVAILABLE',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS borrowings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL REFERENCES items(id),
    owner_id INTEGER NOT NULL REFERENCES users(id),
    borrower_id INTEGER NOT NULL REFERENCES users(id),
    status TEXT NOT NULL DEFAULT 'REQUESTED',
    expected_return TEXT,
    start_date TEXT,
    actual_return TEXT,
    deposit REAL NOT NULL DEFAULT 0,
    pickup_verified INTEGER NOT NULL DEFAULT 0,
    return_verified INTEGER NOT NULL DEFAULT 0,
    condition_photo_start TEXT,
    condition_photo_end TEXT,
    created_at TEXT NOT NULL
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
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    payer_id INTEGER REFERENCES users(id),
    payee_id INTEGER REFERENCES users(id),
    related_type TEXT NOT NULL,
    related_id INTEGER NOT NULL,
    amount REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    created_at TEXT NOT NULL,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS ratings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_type TEXT NOT NULL,
    transaction_id INTEGER NOT NULL,
    rater_id INTEGER NOT NULL REFERENCES users(id),
    ratee_id INTEGER NOT NULL REFERENCES users(id),
    stars INTEGER NOT NULL,
    review TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(transaction_type, transaction_id, rater_id)
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    message TEXT NOT NULL,
    is_read INTEGER NOT NULL DEFAULT 0,
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

CREATE TABLE IF NOT EXISTS disputes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_type TEXT NOT NULL,
    transaction_id INTEGER NOT NULL,
    reporter_id INTEGER NOT NULL REFERENCES users(id),
    category TEXT NOT NULL,
    description TEXT,
    evidence_path TEXT,
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

CREATE TABLE IF NOT EXISTS locations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reference_type TEXT NOT NULL,
    reference_id INTEGER NOT NULL,
    lat REAL NOT NULL,
    lng REAL NOT NULL,
    is_demo INTEGER NOT NULL DEFAULT 0,
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

CREATE INDEX IF NOT EXISTS idx_requests_status ON requests(status);
CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);
CREATE INDEX IF NOT EXISTS idx_borrowings_status ON borrowings(status);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_otp_lookup ON otp_records(user_id, purpose, reference_id, used);
CREATE INDEX IF NOT EXISTS idx_qr_lookup ON qr_tokens(reference_type, reference_id, used);
CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id, is_read);
"""


def init_db():
    conn = get_conn()
    conn.executescript(SCHEMA)
    conn.commit()
    # Create a default admin account if none exists (demo convenience only).
    cur = conn.execute("SELECT id FROM users WHERE role = 'admin' LIMIT 1")
    if cur.fetchone() is None:
        conn.execute(
            """INSERT INTO users (full_name, email, phone, student_id, password_hash,
                role, verified, trust_score, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                "Platform Admin",
                ADMIN_EMAIL,
                "",
                "ADMIN-0",
                generate_password_hash(ADMIN_PASSWORD),
                "admin",
                1,
                100,
                now_iso(),
            ),
        )
        conn.commit()
    conn.close()


def now_iso():
    return datetime.utcnow().isoformat()


def parse_iso(s):
    return datetime.fromisoformat(s)


# =============================================================================
# 2. SECURITY / OTP / QR HELPERS
# =============================================================================

def hash_password(pw):
    return generate_password_hash(pw)


def verify_password(pw, pw_hash):
    try:
        return check_password_hash(pw_hash, pw)
    except Exception:
        return False


def generate_numeric_otp(length=6):
    return "".join(secrets.choice(string.digits) for _ in range(length))


def create_otp(user_id, purpose, reference_id=None):
    """Creates a new OTP, invalidates prior unused OTPs for the same
    user/purpose/reference, and returns the PLAIN OTP (caller decides whether
    to email it or show it in demo mode). Only a hash is ever persisted."""
    conn = get_conn()
    conn.execute(
        """UPDATE otp_records SET used = 1
           WHERE user_id = ? AND purpose = ? AND
                 (reference_id = ? OR (reference_id IS NULL AND ? IS NULL)) AND used = 0""",
        (user_id, purpose, reference_id, reference_id),
    )
    otp_plain = generate_numeric_otp()
    expires_at = (datetime.utcnow() + timedelta(minutes=OTP_EXPIRY_MINUTES)).isoformat()
    conn.execute(
        """INSERT INTO otp_records (user_id, purpose, reference_id, otp_hash,
            expires_at, attempts, max_attempts, used, created_at)
           VALUES (?,?,?,?,?,0,?,0,?)""",
        (user_id, purpose, reference_id, generate_password_hash(otp_plain),
         expires_at, OTP_MAX_ATTEMPTS, now_iso()),
    )
    conn.commit()
    conn.close()
    return otp_plain


def verify_otp(user_id, purpose, reference_id, submitted_otp):
    """Returns (ok: bool, message: str). All state transitions happen here,
    never trusting anything the frontend claims about verification status."""
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
        return False, "No active OTP found. Please generate a new one."

    if row["attempts"] >= row["max_attempts"]:
        conn.close()
        return False, "Too many incorrect attempts. Please generate a new OTP."

    if datetime.utcnow() > parse_iso(row["expires_at"]):
        conn.close()
        return False, "OTP expired. Please generate a new OTP."

    if not check_password_hash(row["otp_hash"], submitted_otp.strip()):
        conn.execute("UPDATE otp_records SET attempts = attempts + 1 WHERE id = ?", (row["id"],))
        conn.commit()
        remaining = row["max_attempts"] - (row["attempts"] + 1)
        conn.close()
        return False, f"Invalid verification code. {max(remaining,0)} attempt(s) left."

    conn.execute("UPDATE otp_records SET used = 1 WHERE id = ?", (row["id"],))
    conn.commit()
    conn.close()
    return True, "Verified successfully."


def send_email(to_email, subject, body):
    """Send an email through the configured SMTP server using STARTTLS."""
    if not EMAIL_CONFIGURED:
        return False
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = SMTP_USERNAME
        msg["To"] = to_email
        context = ssl.create_default_context()
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.sendmail(SMTP_USERNAME, [to_email], msg.as_string())
        st.session_state.pop("_last_email_error", None)
        return True
    except Exception as e:
        # Keep technical SMTP details away from normal users. Admins can see
        # the configuration status from the Admin Dashboard.
        st.session_state["_last_email_error"] = str(e)
        return False


def deliver_otp(user_row, purpose, reference_id, context_label=""):
    """Generate and email a real OTP. Never expose the OTP in the UI."""
    otp_plain = create_otp(user_row["id"], purpose, reference_id)
    subject = f"UNI HELP - Your {purpose.replace('_', ' ').title()} Code"
    body = (
        f"Hi {user_row['full_name']},\n\n"
        f"Your verification code for {context_label or purpose} is: {otp_plain}\n"
        f"This code expires in {OTP_EXPIRY_MINUTES} minutes.\n\n"
        f"If you did not request this, ignore this email.\n\n- UNI HELP"
    )
    sent = send_email(user_row["email"], subject, body)
    return sent


def create_qr_token(reference_type, reference_id, purpose):
    conn = get_conn()
    conn.execute(
        """UPDATE qr_tokens SET used = 1
           WHERE reference_type = ? AND reference_id = ? AND purpose = ? AND used = 0""",
        (reference_type, reference_id, purpose),
    )
    token = f"UNIHELP|{reference_type}|{reference_id}|{secrets.token_urlsafe(16)}"
    expires_at = (datetime.utcnow() + timedelta(minutes=QR_EXPIRY_MINUTES)).isoformat()
    conn.execute(
        """INSERT INTO qr_tokens (reference_type, reference_id, purpose, token, used, expires_at, created_at)
           VALUES (?,?,?,?,0,?,?)""",
        (reference_type, reference_id, purpose, token, expires_at, now_iso()),
    )
    conn.commit()
    conn.close()
    return token


def generate_qr_image_bytes(token):
    img = qrcode.make(token)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


def verify_qr_token(submitted_token, reference_type, reference_id, purpose):
    conn = get_conn()
    row = conn.execute(
        """SELECT * FROM qr_tokens WHERE token = ? AND reference_type = ?
           AND reference_id = ? AND purpose = ?""",
        (submitted_token.strip(), reference_type, reference_id, purpose),
    ).fetchone()
    if row is None:
        conn.close()
        return False, "Invalid QR / verification token for this transaction."
    if row["used"]:
        conn.close()
        return False, "This QR code has already been used."
    if datetime.utcnow() > parse_iso(row["expires_at"]):
        conn.close()
        return False, "This QR code has expired."
    conn.execute("UPDATE qr_tokens SET used = 1 WHERE id = ?", (row["id"],))
    conn.commit()
    conn.close()
    return True, "QR verified successfully."


def haversine_meters(lat1, lon1, lat2, lon2):
    R = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def record_location(reference_type, reference_id, lat, lng, is_demo):
    conn = get_conn()
    conn.execute(
        """INSERT INTO locations (reference_type, reference_id, lat, lng, is_demo, created_at)
           VALUES (?,?,?,?,?,?)""",
        (reference_type, reference_id, lat, lng, 1 if is_demo else 0, now_iso()),
    )
    conn.commit()
    conn.close()


def check_location(helper_lat, helper_lng, target_lat, target_lng):
    if target_lat is None or target_lng is None:
        return True, 0.0  # No location set on the request; skip the check.
    dist = haversine_meters(helper_lat, helper_lng, target_lat, target_lng)
    return dist <= LOCATION_RADIUS_METERS, dist


# =============================================================================
# 3. NOTIFICATIONS / TRUST SCORE / UNICOINS / RATINGS / TRANSACTIONS
# =============================================================================

def notify(user_id, message):
    conn = get_conn()
    conn.execute(
        "INSERT INTO notifications (user_id, message, is_read, created_at) VALUES (?,?,0,?)",
        (user_id, message, now_iso()),
    )
    conn.commit()
    conn.close()


def get_notifications(user_id, unread_only=False, limit=50):
    conn = get_conn()
    q = "SELECT * FROM notifications WHERE user_id = ?"
    if unread_only:
        q += " AND is_read = 0"
    q += " ORDER BY id DESC LIMIT ?"
    rows = conn.execute(q, (user_id, limit)).fetchall()
    conn.close()
    return rows


def mark_notifications_read(user_id):
    conn = get_conn()
    conn.execute("UPDATE notifications SET is_read = 1 WHERE user_id = ?", (user_id,))
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
    notify(user_id, f"{'🪙 +' if amount >= 0 else '🪙 '}{amount} UniCoins — {reason}")


def recalc_trust_score(user_id):
    """Transparent, explainable scoring formula (kept in [0, 100])."""
    conn = get_conn()

    completed_deliveries = conn.execute(
        "SELECT COUNT(*) c FROM requests WHERE helper_id = ? AND status = 'COMPLETED'", (user_id,)
    ).fetchone()["c"]
    completed_borrow_returns = conn.execute(
        "SELECT COUNT(*) c FROM borrowings WHERE borrower_id = ? AND status = 'COMPLETED'", (user_id,)
    ).fetchone()["c"]
    completed_tasks = conn.execute(
        "SELECT COUNT(*) c FROM tasks WHERE helper_id = ? AND status = 'COMPLETED'", (user_id,)
    ).fetchone()["c"]
    positive_ratings = conn.execute(
        "SELECT COUNT(*) c FROM ratings WHERE ratee_id = ? AND stars >= 4", (user_id,)
    ).fetchone()["c"]
    negative_ratings = conn.execute(
        "SELECT COUNT(*) c FROM ratings WHERE ratee_id = ? AND stars <= 2", (user_id,)
    ).fetchone()["c"]
    confirmed_disputes = conn.execute(
        """SELECT COUNT(*) c FROM disputes d
           JOIN requests r ON d.transaction_type='DELIVERY' AND d.transaction_id = r.id
           WHERE (r.helper_id = ? OR r.requester_id = ?) AND d.status = 'RESOLVED'""",
        (user_id, user_id),
    ).fetchone()["c"]

    score = 50
    score += min(completed_deliveries, 15) * 2
    score += min(completed_borrow_returns, 15) * 2
    score += min(completed_tasks, 15) * 2
    score += min(positive_ratings, 10) * 1
    score -= min(negative_ratings, 10) * 3
    score -= min(confirmed_disputes, 10) * 5

    score = max(0, min(100, score))
    conn.execute("UPDATE users SET trust_score = ? WHERE id = ?", (score, user_id))
    conn.commit()
    conn.close()
    return score


def submit_rating(transaction_type, transaction_id, rater_id, ratee_id, stars, review):
    conn = get_conn()
    existing = conn.execute(
        "SELECT id FROM ratings WHERE transaction_type=? AND transaction_id=? AND rater_id=?",
        (transaction_type, transaction_id, rater_id),
    ).fetchone()
    if existing:
        conn.close()
        return False, "You already rated this transaction."
    conn.execute(
        """INSERT INTO ratings (transaction_type, transaction_id, rater_id, ratee_id, stars, review, created_at)
           VALUES (?,?,?,?,?,?,?)""",
        (transaction_type, transaction_id, rater_id, ratee_id, stars, review, now_iso()),
    )
    conn.execute(
        "UPDATE users SET rating_sum = rating_sum + ?, rating_count = rating_count + 1 WHERE id = ?",
        (stars, ratee_id),
    )
    conn.commit()
    conn.close()
    recalc_trust_score(ratee_id)
    notify(ratee_id, f"⭐ You received a {stars}-star rating.")
    return True, "Rating submitted."


def create_transaction(payer_id, payee_id, related_type, related_id, amount, status="PENDING"):
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


def user_by_id(user_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return row


def user_by_email(email):
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    conn.close()
    return row


def refresh_current_user():
    if st.session_state.get("user"):
        st.session_state["user"] = dict(user_by_id(st.session_state["user"]["id"]))


# =============================================================================
# 4. AUTH
# =============================================================================

def is_valid_email(email):
    """Lightweight validation for any normal email address."""
    return bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email.strip()))


def normalize_phone(phone):
    raw = phone.strip().replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    if raw.startswith("+") and raw[1:].isdigit() and 10 <= len(raw[1:]) <= 15: return raw
    if raw.isdigit() and len(raw) == 10: return "+91" + raw
    return ""


def is_valid_phone(phone): return bool(normalize_phone(phone))


def _msg91_api_request(url, method="GET"):
    """Call MSG91 without exposing the auth key to the client/UI."""
    req = urllib.request.Request(
        url,
        method=method,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=20) as response:
        raw = response.read().decode("utf-8", errors="replace")
    try:
        return json.loads(raw)
    except Exception:
        return {"raw": raw}


def _msg91_phone(user_row):
    try:
        value = user_row["phone"]
    except Exception:
        value = user_row.get("phone", "") if hasattr(user_row, "get") else ""
    return normalize_phone(value or "")


def send_msg91_otp(to_phone):
    """Generate/send an OTP through MSG91. MSG91 owns OTP generation and verification."""
    if not SMS_CONFIGURED:
        st.session_state["_last_sms_error"] = (
            "MSG91 SMS is not configured. Add MSG91_AUTHKEY and "
            "MSG91_OTP_TEMPLATE_ID to Streamlit Secrets."
        )
        return False

    phone = normalize_phone(to_phone)
    if not phone:
        st.session_state["_last_sms_error"] = "Invalid phone number."
        return False

    # MSG91 expects the mobile number without the leading + in this API.
    mobile = phone.lstrip("+")
    params = urllib.parse.urlencode({
        "template_id": MSG91_OTP_TEMPLATE_ID,
        "mobile": mobile,
        "otp_length": "6",
        "otp_expiry": str(OTP_EXPIRY_MINUTES),
    })
    url = f"https://control.msg91.com/api/v5/otp?{params}"

    try:
        # Authkey is sent as a header rather than embedded in the URL.
        req = urllib.request.Request(
            url,
            method="POST",
            headers={
                "authkey": MSG91_AUTHKEY,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            data=b"{}",
        )
        with urllib.request.urlopen(req, timeout=20) as response:
            raw = response.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
        success = str(data.get("type", "")).lower() == "success" or bool(data.get("request_id"))
        if not success:
            st.session_state["_last_sms_error"] = str(data.get("message") or data.get("msg") or "MSG91 rejected the OTP request.")
        else:
            st.session_state.pop("_last_sms_error", None)
        return success
    except Exception as exc:
        st.session_state["_last_sms_error"] = str(exc)
        return False


def verify_msg91_otp(to_phone, code):
    """Verify an OTP against MSG91; the OTP is never stored by UNI HELP."""
    if not SMS_CONFIGURED:
        return False, "MSG91 SMS verification is not configured. Please contact the administrator."
    phone = normalize_phone(to_phone)
    code = str(code or "").strip()
    if not phone:
        return False, "No valid mobile number is registered."
    if not re.fullmatch(r"\d{6}", code):
        return False, "Please enter the 6-digit OTP."

    params = urllib.parse.urlencode({"mobile": phone.lstrip("+"), "otp": code})
    url = f"https://control.msg91.com/api/v5/otp/verify?{params}"
    try:
        req = urllib.request.Request(
            url,
            method="GET",
            headers={"authkey": MSG91_AUTHKEY, "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=20) as response:
            raw = response.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
        message = str(data.get("message", "")).lower()
        if "verified success" in message or "success" in message or data.get("type") == "success":
            return True, "Verified successfully."
        return False, str(data.get("message") or data.get("msg") or "Invalid OTP. Please try again.")
    except Exception as exc:
        st.session_state["_last_sms_error"] = str(exc)
        return False, "Unable to verify OTP. Please try again."


def deliver_sms_otp(user_row, purpose, reference_id=None, context_label="verification"):
    """Start an MSG91 SMS OTP challenge."""
    return send_msg91_otp(_msg91_phone(user_row))


def register_user(full_name,email,phone,student_id,password):
    email=email.strip().lower(); phone=normalize_phone(phone); student_id=student_id.strip()
    if not full_name.strip(): return False,"Full name is required."
    if not is_valid_email(email): return False,"Please enter a valid email address."
    if not phone: return False,"Please enter a valid phone number with country code."
    if not student_id: return False,"Student ID is required."
    if len(password)<6: return False,"Password must be at least 6 characters."
    if user_by_email(email): return False,"An account with this email already exists."
    conn=get_conn()
    if conn.execute("SELECT id FROM users WHERE student_id=?",(student_id,)).fetchone(): conn.close(); return False,"An account with this Student ID already exists."
    conn.execute("INSERT INTO users (full_name,email,phone,student_id,password_hash,role,verified,trust_score,created_at) VALUES (?,?,?,?,?,?,?,?,?)",(full_name.strip(),email,phone,student_id,hash_password(password),"student",0,50,now_iso()))
    new_id=conn.execute("SELECT last_insert_rowid() id").fetchone()["id"]; conn.commit(); conn.close(); return True,new_id


def user_by_student_id(student_id):
    conn=get_conn(); row=conn.execute("SELECT * FROM users WHERE student_id=?",(student_id.strip(),)).fetchone(); conn.close(); return row


def authenticate_student_credentials(student_id,password):
    row=user_by_student_id(student_id)
    if row is None: return False,"No account found with that Student ID."
    if not verify_password(password,row["password_hash"]): return False,"Incorrect password."
    if row["is_suspended"]: return False,"This account has been suspended. Contact an administrator."
    if not row["verified"]: return False,"Please complete email and phone verification before logging in."
    return True,dict(row)


def login_user(email,password):
    row=user_by_email(email.strip().lower())
    if row is None: return False,"No account found with that email."
    if not verify_password(password,row["password_hash"]): return False,"Incorrect password."
    if row["is_suspended"]: return False,"This account has been suspended. Contact an administrator."
    return True,dict(row)


# 5. DEMO DATA SEEDING
# =============================================================================

DEMO_STUDENTS = [
    ("Aarav Sharma", "aarav.sharma", "9990001111", "STU1001"),
    ("Priya Nair", "priya.nair", "9990002222", "STU1002"),
    ("Rohan Mehta", "rohan.mehta", "9990003333", "STU1003"),
]


def seed_demo_data():
    conn = get_conn()
    created_ids = []
    for full_name, local, phone, sid in DEMO_STUDENTS:
        email = f"{local}{UNIVERSITY_EMAIL_DOMAIN}"
        existing = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if existing:
            created_ids.append(existing["id"])
            continue
        conn.execute(
            """INSERT INTO users (full_name, email, phone, student_id, password_hash, role,
                verified, trust_score, unicoins, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (full_name, email, phone, sid, hash_password("demo1234"), "student", 1, 60, 50, now_iso()),
        )
        created_ids.append(conn.execute("SELECT last_insert_rowid() id").fetchone()["id"])
    conn.commit()

    s1, s2, s3 = created_ids[0], created_ids[1], created_ids[2]

    deliveries = [
        (s1, "Textbook - Data Structures", "Pick up from library reserve desk", "Central Library", "Hostel Block C", 40),
        (s2, "Lab Coat", "Forgot it in the chem lab", "Chemistry Building", "Hostel Block A", 25),
        (s3, "Lunch from canteen", "Veg thali, extra roti", "Main Canteen", "Engineering Block", 30),
        (s1, "Charger cable", "Type-C, urgent", "Room 204 Block B", "CS Department", 15),
        (s2, "Printed assignment", "50 pages, stapled", "Print Shop Gate 2", "Room 118 Block D", 20),
    ]
    for requester, item, desc, pickup, dest, reward in deliveries:
        conn.execute(
            """INSERT INTO requests (requester_id, item_name, description, pickup_location,
                destination, reward, preferred_time, notes, status, created_at)
               VALUES (?,?,?,?,?,?,?,?, 'CREATED', ?)""",
            (requester, item, desc, pickup, dest, reward, "Anytime today", "", now_iso()),
        )

    items = [
        (s1, "Scientific Calculator", "Electronics", "Casio FX-991, works well", "Good", 100),
        (s2, "Laptop Charger (Dell)", "Electronics", "65W, original", "Good", 200),
        (s3, "Cricket Bat", "Sports", "Kashmir willow, lightly used", "Fair", 150),
        (s1, "Data Structures Textbook", "Books", "Cormen, 3rd edition", "Good", 50),
        (s2, "Digital Multimeter", "Lab Equipment", "For electronics lab work", "Good", 100),
    ]
    for owner, name, cat, desc, cond, dep in items:
        conn.execute(
            """INSERT INTO items (owner_id, item_name, category, description, condition,
                availability, deposit, rules, status, created_at)
               VALUES (?,?,?,?,?, 'This week', ?, 'Return in same condition', 'AVAILABLE', ?)""",
            (owner, name, cat, desc, cond, dep, now_iso()),
        )

    tasks = [
        (s1, "Collect parcel from gate", "Courier arrived, need pickup + drop to hostel", "Main Gate", "Hostel Block C", 20, "Errand"),
        (s2, "Help set up projector", "For a club event this evening", "AV Room", "Seminar Hall", 30, "Setup"),
        (s3, "Photocopy notes", "20 pages, needed before 5pm", "Print Shop", "Room 210", 15, "Errand"),
        (s1, "Move furniture", "Small table between rooms", "Room 101", "Room 105", 40, "Physical Help"),
    ]
    for creator, title, desc, pickup, dest, reward, cat in tasks:
        conn.execute(
            """INSERT INTO tasks (creator_id, title, description, pickup, destination, reward,
                deadline, category, status, created_at)
               VALUES (?,?,?,?,?,?, ?, ?, 'CREATED', ?)""",
            (creator, title, desc, pickup, dest, reward, "Today", cat, now_iso()),
        )

    conn.commit()
    conn.close()

    for uid in (s1, s2, s3):
        add_unicoins(uid, 50, "Welcome bonus")
        notify(uid, "Demo data loaded — explore Delivery, Borrowing and Micro-Tasks!")

    return True


# =============================================================================
# 6. STREAMLIT UI
# =============================================================================

st.set_page_config(page_title="UNI HELP", page_icon="🎓", layout="wide")

CUSTOM_CSS = """
<style>
:root{--uh-navy:#0b1736;--uh-blue:#2563eb;--uh-blue2:#4f7cff;--uh-orange:#f97316;--uh-bg:#f5f8fc;--uh-text:#10203f;--uh-muted:#64748b;--uh-line:#dbe4f0}
.stApp{background:#f6f8fc;}
.block-container{max-width:1180px!important;padding-top:1.35rem!important;padding-bottom:3rem!important;padding-left:2rem!important;padding-right:2rem!important;}
[data-testid="stSidebar"]{border-right:1px solid #e2e8f0;background:#ffffff;}
[data-testid="stSidebar"]>div:first-child{padding-top:1rem;}
[data-testid="stSidebar"] .stRadio label{font-weight:700;color:#334155;}
.uh-page-header{display:flex;align-items:flex-end;justify-content:space-between;gap:1rem;margin:.15rem 0 1.2rem;padding-bottom:.9rem;border-bottom:1px solid #e2e8f0;}
.uh-page-header h2{margin:0;color:var(--uh-navy);font-size:1.7rem;letter-spacing:-.035em;}
.uh-page-header p{margin:.25rem 0 0;color:#64748b;font-size:.82rem;}
[data-testid="stMetric"]{background:#fff;border:1px solid #e2e8f0;border-radius:14px;padding:.75rem .85rem;box-shadow:0 5px 18px rgba(15,23,42,.045);}
[data-testid="stVerticalBlockBorderWrapper"]{border-color:#e2e8f0!important;border-radius:16px!important;}
.stButton>button,.stFormSubmitButton>button{border-radius:10px!important;font-weight:750!important;}
.stApp:has(.uh-auth-shell){background:radial-gradient(circle at 12% 8%,rgba(37,99,235,.08),transparent 28%),radial-gradient(circle at 88% 92%,rgba(249,115,22,.07),transparent 24%),var(--uh-bg)}
.uh-auth-shell{min-height:calc(100dvh - 1.2rem);display:flex;align-items:center;justify-content:center;padding:.55rem 1rem;box-sizing:border-box;overflow:hidden}
.uh-auth-content{width:min(100%,440px);margin:auto}
.uh-auth-hero{text-align:center;margin:0 auto .75rem;animation:uhFade .42s ease both}
.uh-auth-logo-mark{width:48px;height:48px;margin:0 auto .35rem;border-radius:15px;display:flex;align-items:center;justify-content:center;background:linear-gradient(145deg,#e8f0ff,#fff);border:1px solid #d7e3f5;box-shadow:0 10px 24px rgba(11,23,54,.09);font-size:1.45rem;animation:uhLogo .5s ease both}
.uh-auth-hero h1{color:var(--uh-navy)!important;font-size:2.15rem!important;line-height:1;margin:0!important;letter-spacing:-.055em;font-weight:850}
.uh-auth-tagline{color:#203454!important;font-size:.94rem;line-height:1.32;margin:.38rem 0 .18rem;font-weight:650}
.uh-auth-subtitle{color:var(--uh-muted)!important;font-size:.76rem;margin:0;letter-spacing:.04em;font-weight:600}
.uh-auth-shell [data-testid="stVerticalBlockBorderWrapper"]{background:rgba(255,255,255,.97)!important;border:1px solid rgba(219,228,240,.95)!important;border-radius:20px!important;box-shadow:0 18px 50px rgba(11,23,54,.09),0 2px 8px rgba(11,23,54,.04)!important;padding:.95rem!important;animation:uhCard .48s .04s ease both}
.uh-auth-tabs{display:grid;grid-template-columns:1fr 1fr;gap:.2rem;background:#eef3f9;border-radius:11px;padding:.22rem;margin-bottom:.72rem}
.uh-auth-tab-button button{border:0!important;background:transparent!important;color:#64748b!important;box-shadow:none!important;min-height:38px!important;border-radius:9px!important;font-size:.78rem!important;font-weight:800!important}
.uh-auth-tab-button-active button{background:#fff!important;color:var(--uh-navy)!important;box-shadow:0 3px 10px rgba(11,23,54,.09)!important}
.uh-auth-card-title{color:var(--uh-navy);font-size:1.18rem;font-weight:800;margin:.15rem 0 .1rem}
.uh-auth-card-copy{color:#53637c!important;font-size:.78rem!important;margin:0 0 .72rem!important}
.uh-auth-demo{margin:.55rem 0 0;padding:.42rem .55rem;border-radius:9px;background:#fff7ed;border:1px solid #fed7aa;color:#9a3412!important;font-size:.68rem;text-align:center}
.uh-auth-divider{display:flex;align-items:center;gap:.55rem;color:#94a3b8;font-size:.68rem;margin:.65rem 0}.uh-auth-divider:before,.uh-auth-divider:after{content:"";height:1px;flex:1;background:#e2e8f0}
.uh-otp-note{text-align:center;padding:.4rem .55rem;background:#eff6ff;border:1px solid #bfdbfe;color:#1e40af!important;border-radius:9px;font-size:.7rem;margin:.35rem 0 .5rem}
.stApp:has(.uh-auth-shell) div[data-testid="stTextInput"] label,.stApp:has(.uh-auth-shell) div[data-testid="stTextInput"] label p{color:#334155!important;font-weight:700!important;font-size:.73rem!important}
.stApp:has(.uh-auth-shell) div[data-testid="stTextInput"] input{border:1px solid #cbd5e1!important;border-radius:10px!important;background:#fff!important;color:#0f172a!important;min-height:40px!important;box-shadow:none!important;font-size:.86rem!important}
.stApp:has(.uh-auth-shell) div[data-testid="stTextInput"] input:focus{border-color:var(--uh-blue)!important;box-shadow:0 0 0 3px rgba(37,99,235,.11)!important}
.stApp:has(.uh-auth-shell) button{border-radius:10px!important;min-height:40px!important;font-weight:750!important;transition:transform .15s ease,box-shadow .15s ease,border-color .15s ease!important}
.stApp:has(.uh-auth-shell) button:hover{transform:translateY(-1px)}
.stApp:has(.uh-auth-shell) button[kind="primary"]{background:linear-gradient(135deg,var(--uh-blue),var(--uh-blue2))!important;color:#fff!important;border:0!important;box-shadow:0 7px 16px rgba(37,99,235,.18)!important}
.uh-auth-secondary button{background:#fff!important;color:var(--uh-navy)!important;border:1px solid #cbd5e1!important}
.uh-auth-secondary button:hover{border-color:#93c5fd!important;box-shadow:0 5px 13px rgba(11,23,54,.06)!important}
.uh-auth-create button,.uh-auth-back button{background:transparent!important;color:var(--uh-blue)!important;border:0!important;box-shadow:none!important}
.uh-auth-back button{color:#64748b!important}
.uh-auth-status{padding:.48rem .6rem;border-radius:9px;font-size:.73rem;font-weight:650;margin:.35rem 0}.uh-auth-status.ok{background:#ecfdf5;color:#047857;border:1px solid #a7f3d0}.uh-auth-status.err{background:#fff1f2;color:#be123c;border:1px solid #fecdd3}
@keyframes uhFade{from{opacity:0;transform:translateY(7px)}to{opacity:1;transform:none}}@keyframes uhLogo{from{opacity:0;transform:scale(.92)}to{opacity:1;transform:none}}@keyframes uhCard{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
@media(max-width:900px){.block-container{max-width:100%!important;padding-left:1rem!important;padding-right:1rem!important;}}
@media(max-width:640px){.block-container{padding-top:.8rem!important;padding-left:.7rem!important;padding-right:.7rem!important}.uh-page-header h2{font-size:1.35rem}.uh-auth-shell{min-height:100dvh;padding:.35rem .45rem;overflow:visible}.uh-auth-content{width:min(100%,410px)}.uh-auth-hero{margin-bottom:.5rem}.uh-auth-logo-mark{width:42px;height:42px;border-radius:13px;font-size:1.25rem}.uh-auth-hero h1{font-size:1.85rem!important}.uh-auth-tagline{font-size:.84rem}.uh-auth-subtitle{font-size:.69rem}.stApp:has(.uh-auth-shell) [data-testid="stVerticalBlockBorderWrapper"]{padding:.72rem!important;border-radius:17px!important}.stApp:has(.uh-auth-shell) div[data-testid="stTextInput"] input{min-height:39px!important}.stApp:has(.uh-auth-shell) button{min-height:40px!important}}
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

init_db()

if "user" not in st.session_state:
    st.session_state["user"] = None
if "auth_mode" not in st.session_state:
    st.session_state["auth_mode"] = "Home"
if "nav" not in st.session_state:
    st.session_state["nav"] = "Dashboard"

def status_badge(status):
    colors = {
        "CREATED": ("🔵", "#e8f0ff", "#1550ff"),
        "REQUESTED": ("🔵", "#e8f0ff", "#1550ff"),
        "ACCEPTED": ("🟡", "#fff8e1", "#8a6d00"),
        "PICKUP_VERIFIED": ("🟡", "#fff8e1", "#8a6d00"),
        "IN_TRANSIT": ("🟡", "#fff8e1", "#8a6d00"),
        "IN_PROGRESS": ("🟡", "#fff8e1", "#8a6d00"),
        "ACTIVE": ("🟡", "#fff8e1", "#8a6d00"),
        "RETURN_REQUESTED": ("🟡", "#fff8e1", "#8a6d00"),
        "DELIVERED": ("🟢", "#e8f7ee", "#0a7a3d"),
        "COMPLETED": ("🟢", "#e8f7ee", "#0a7a3d"),
        "AVAILABLE": ("🟢", "#e8f7ee", "#0a7a3d"),
        "DISPUTED": ("🔴", "#fdeaea", "#b00020"),
        "REJECTED": ("🔴", "#fdeaea", "#b00020"),
        "CANCELLED": ("🔴", "#fdeaea", "#b00020"),
    }
    emoji, bg, fg = colors.get(status, ("⚪", "#f0f0f0", "#333"))
    return f'<span class="uh-badge" style="background:{bg};color:{fg};">{emoji} {status.replace("_"," ")}</span>'


# -----------------------------------------------------------------------------
# 6.1 LANDING / AUTH PAGES
# -----------------------------------------------------------------------------

def _render_auth_shell_start():
    st.markdown('<div class="uh-auth-shell"><div class="uh-auth-content">', unsafe_allow_html=True)


def _render_auth_shell_end():
    st.markdown('</div></div>', unsafe_allow_html=True)


def _render_auth_hero():
    st.markdown(
        '<div class="uh-auth-hero"><div class="uh-auth-logo-mark">🎓</div>'
        '<h1>UNI HELP</h1>'
        '<p class="uh-auth-tagline">Your campus. Your community.<br>Someone can help.</p>'
        '<p class="uh-auth-subtitle">Borrow • Deliver • Assist • Earn</p></div>',
        unsafe_allow_html=True,
    )


def _set_user_and_route(user):
    st.session_state["user"] = user
    st.session_state["nav"] = "Admin" if user.get("role") == "admin" else "Dashboard"
    st.rerun()


def _send_student_login_sms(student_id, password):
    if not SMS_CONFIGURED:
        return False, "SMS verification is not configured. Please contact the administrator."
    ok, result = authenticate_student_credentials(student_id, password)
    if not ok:
        return False, result
    phone = normalize_phone(result.get("phone") or "")
    if not phone:
        return False, "No valid mobile number is registered for this Student ID."
    if not deliver_sms_otp(result, "LOGIN_SMS_OTP", None, "login"):
        return False, st.session_state.get("_last_sms_error") or "Unable to send OTP. Please try again."
    st.session_state["login_student_id"] = student_id.strip()
    st.session_state["login_otp_resend_at"] = time.time() + SMS_RESEND_SECONDS
    return True, "OTP sent successfully."


def _verify_student_login_sms(student_id, password, otp):
    ok, result = authenticate_student_credentials(student_id, password)
    if not ok:
        return False, result
    ok, msg = verify_msg91_otp(result.get("phone") or "", otp)
    return (True, result) if ok else (False, msg)


def _send_registration_email_otp(user_id):
    user = user_by_id(user_id)
    if not user or not EMAIL_CONFIGURED:
        return False, "Email verification is not configured. Please contact the administrator."
    if not deliver_otp(user, "EMAIL_VERIFICATION", None, "email verification"):
        return False, "Unable to send email OTP. Please try again."
    st.session_state["registration_email_resend_at"] = time.time() + SMS_RESEND_SECONDS
    return True, "Email OTP sent successfully."


def _send_registration_sms_otp(user_id):
    user = user_by_id(user_id)
    if not user or not SMS_CONFIGURED:
        return False, "SMS verification is not configured. Please contact the administrator."
    if not normalize_phone(user["phone"] or ""):
        return False, "No valid mobile number is registered."
    if not deliver_sms_otp(user, "PHONE_VERIFICATION", None, "phone verification"):
        return False, "Unable to send SMS OTP. Please try again."
    st.session_state["registration_sms_resend_at"] = time.time() + SMS_RESEND_SECONDS
    return True, "SMS OTP sent successfully."


def _clear_registration_state():
    for key in ("pending_registration_user_id", "registration_email_verified", "registration_phone_otp_sent", "registration_email_resend_at", "registration_sms_resend_at"):
        st.session_state.pop(key, None)


def _auth_tabs(active):
    a, b = st.columns(2, gap="small")
    with a:
        st.markdown('<div class="uh-auth-tab-button uh-auth-tab-button-active"><button disabled>LOGIN</button></div>' if active == "login" else '<div class="uh-auth-tab-button"><button disabled>LOGIN</button></div>', unsafe_allow_html=True)
    with b:
        st.markdown('<div class="uh-auth-tab-button uh-auth-tab-button-active"><button disabled>CREATE ACCOUNT</button></div>' if active == "register" else '<div class="uh-auth-tab-button"><button disabled>CREATE ACCOUNT</button></div>', unsafe_allow_html=True)


def render_landing():
    _render_auth_shell_start()
    _render_auth_hero()
    with st.container(border=True):
        _auth_tabs("login")
        st.markdown('<div class="uh-auth-card-title">Welcome back</div><div class="uh-auth-card-copy">Sign in with your Student ID, password and registered mobile.</div>', unsafe_allow_html=True)
        sid = st.text_input("Student ID", placeholder="Enter your Student ID", key="auth_student_id")
        pw = st.text_input("Password", type="password", placeholder="Enter your password", key="auth_student_password")
        if st.button("Send OTP", use_container_width=True, type="primary", key="auth_send_sms"):
            with st.spinner("Sending secure OTP…"):
                ok, msg = _send_student_login_sms(sid, pw)
            if ok:
                st.success(msg)
                st.rerun()
            else:
                st.error(msg)
        if st.session_state.get("login_student_id"):
            st.text_input("Phone OTP", max_chars=6, placeholder="Enter 6-digit OTP", key="auth_login_otp")
            remaining = max(0, int(st.session_state.get("login_otp_resend_at", 0) - time.time()))
            if st.button("Verify & Login", use_container_width=True, type="primary", key="auth_verify_sms"):
                with st.spinner("Verifying OTP…"):
                    ok, result = _verify_student_login_sms(sid or st.session_state.get("login_student_id", ""), pw, st.session_state.get("auth_login_otp", ""))
                if ok:
                    st.success("Login successful. Welcome back!")
                    st.session_state.pop("login_student_id", None)
                    st.session_state.pop("login_otp_resend_at", None)
                    _set_user_and_route(result)
                else:
                    st.error("OTP expired. Please request a new one." if "expired" in str(result).lower() else "Invalid OTP. Please try again.")
            if st.button("Resend OTP", disabled=remaining > 0, use_container_width=True, key="auth_resend_sms"):
                with st.spinner("Sending secure OTP…"):
                    ok, msg = _send_student_login_sms(sid or st.session_state.get("login_student_id", ""), pw)
                if ok:
                    st.success(msg)
                    st.rerun()
                else:
                    st.error(msg)
            if remaining:
                st.markdown(f'<div class="uh-otp-note">Resend available in {remaining}s</div>', unsafe_allow_html=True)
        st.markdown('<div class="uh-auth-divider"><span>New to UNI HELP?</span></div>', unsafe_allow_html=True)
        if st.button("Create an account", use_container_width=True, key="auth_create"):
            st.session_state["auth_mode"] = "Register"
            st.rerun()
        if st.button("Admin Login", use_container_width=True, key="auth_admin"):
            st.session_state["auth_mode"] = "AdminLogin"
            st.rerun()
        if not EMAIL_CONFIGURED or not SMS_CONFIGURED:
            missing = " and ".join(x for x, configured in (("email", EMAIL_CONFIGURED), ("MSG91 SMS", SMS_CONFIGURED)) if not configured)
            st.markdown(f'<div class="uh-auth-demo">Configuration notice • {missing} service is not configured</div>', unsafe_allow_html=True)
    _render_auth_shell_end()


def render_register():
    _render_auth_shell_start()
    _render_auth_hero()
    with st.container(border=True):
        _auth_tabs("register")
        st.markdown('<div class="uh-auth-card-title">Create your account</div><div class="uh-auth-card-copy">Use any valid email address and a mobile number that can receive SMS.</div>', unsafe_allow_html=True)
        pending_id = st.session_state.get("pending_registration_user_id")
        email_verified = bool(st.session_state.get("registration_email_verified"))
        user = user_by_id(pending_id) if pending_id else None

        if not user or not pending_id:
            with st.form("register_form"):
                full_name = st.text_input("Full Name", placeholder="Your full name")
                email = st.text_input("Email Address", placeholder="you@example.com")
                student_id = st.text_input("Student ID", placeholder="Your Student ID")
                phone = st.text_input("Phone Number", placeholder="+91 9876543210")
                password = st.text_input("Password", type="password", placeholder="At least 6 characters")
                password2 = st.text_input("Confirm Password", type="password", placeholder="Repeat your password")
                submitted = st.form_submit_button("Create Account & Send Email OTP", use_container_width=True, type="primary")
            if submitted:
                if password != password2:
                    st.error("Passwords do not match.")
                elif not is_valid_email(email):
                    st.error("Please enter a valid email address.")
                elif not is_valid_phone(phone):
                    st.error("Please enter a valid phone number with country code.")
                elif not EMAIL_CONFIGURED:
                    st.error("Email verification is not configured. Please contact the administrator.")
                elif not SMS_CONFIGURED:
                    st.error("SMS verification is not configured. Please contact the administrator.")
                else:
                    with st.spinner("Creating secure verification session…"):
                        ok, result = register_user(full_name, email, phone, student_id, password)
                    if ok:
                        st.session_state["pending_registration_user_id"] = result
                        ok2, msg = _send_registration_email_otp(result)
                        if ok2:
                            st.success(msg)
                            st.rerun()
                        else:
                            st.error(msg)
                    else:
                        st.error(result)
        else:
            st.markdown(f'<div class="uh-auth-status ok">Verification started for <strong>{user["email"]}</strong></div>', unsafe_allow_html=True)
            if not email_verified:
                st.text_input("Email OTP", max_chars=6, placeholder="Enter 6-digit email OTP", key="reg_email_otp")
                if st.button("Verify Email", use_container_width=True, type="primary", key="reg_verify_email"):
                    with st.spinner("Verifying email…"):
                        ok, msg = verify_otp(user["id"], "EMAIL_VERIFICATION", None, st.session_state.get("reg_email_otp", ""))
                    if ok:
                        st.session_state["registration_email_verified"] = True
                        st.success("Email verified successfully.")
                        st.rerun()
                    else:
                        st.error("OTP expired. Please request a new one." if "expired" in msg.lower() else "Invalid email OTP. Please try again.")
                remaining = max(0, int(st.session_state.get("registration_email_resend_at", 0) - time.time()))
                if st.button("Resend Email OTP", disabled=remaining > 0, use_container_width=True, key="reg_resend_email"):
                    with st.spinner("Sending email OTP…"):
                        ok, msg = _send_registration_email_otp(user["id"])
                    if ok: st.success(msg); st.rerun()
                    else: st.error(msg)
                if remaining: st.markdown(f'<div class="uh-otp-note">Resend available in {remaining}s</div>', unsafe_allow_html=True)
            else:
                st.markdown('<div class="uh-auth-status ok">✓ Email verified</div>', unsafe_allow_html=True)
                if not st.session_state.get("registration_phone_otp_sent"):
                    if st.button("Send Mobile OTP", use_container_width=True, type="primary", key="reg_send_phone"):
                        with st.spinner("Sending secure SMS OTP…"):
                            ok, msg = _send_registration_sms_otp(user["id"])
                        if ok:
                            st.session_state["registration_phone_otp_sent"] = True
                            st.success(msg)
                            st.rerun()
                        else: st.error(msg)
                else:
                    st.text_input("Mobile OTP", max_chars=6, placeholder="Enter 6-digit SMS OTP", key="reg_phone_otp")
                    if st.button("Verify Mobile", use_container_width=True, type="primary", key="reg_verify_phone"):
                        with st.spinner("Verifying mobile…"):
                            ok, msg = verify_msg91_otp(user.get("phone") or "", st.session_state.get("reg_phone_otp", ""))
                        if ok:
                            st.session_state["registration_phone_verified"] = True
                            st.success("Mobile verified successfully.")
                            st.rerun()
                        else:
                            st.error("OTP expired. Please request a new one." if "expired" in msg.lower() else "Invalid mobile OTP. Please try again.")
                    remaining = max(0, int(st.session_state.get("registration_sms_resend_at", 0) - time.time()))
                    if st.button("Resend Mobile OTP", disabled=remaining > 0, use_container_width=True, key="reg_resend_phone"):
                        with st.spinner("Sending SMS OTP…"):
                            ok, msg = _send_registration_sms_otp(user["id"])
                        if ok: st.success(msg); st.rerun()
                        else: st.error(msg)
                    if remaining: st.markdown(f'<div class="uh-otp-note">Resend available in {remaining}s</div>', unsafe_allow_html=True)
                if st.session_state.get("registration_phone_verified"):
                    st.markdown('<div class="uh-auth-status ok">✓ Email and mobile verified</div>', unsafe_allow_html=True)
                    if st.button("Create Account", use_container_width=True, type="primary", key="reg_finish"):
                        with st.spinner("Creating your UNI HELP account…"):
                            conn = get_conn()
                            conn.execute("UPDATE users SET verified=1 WHERE id=?", (user["id"],))
                            conn.commit(); conn.close()
                        notify(user["id"], "Welcome to UNI HELP! Your email and phone have been verified.")
                        _clear_registration_state()
                        st.session_state["auth_mode"] = "Home"
                        st.success("Account created successfully. Please log in.")
                        st.rerun()
        st.markdown('<div class="uh-auth-divider"><span>Already have an account?</span></div>', unsafe_allow_html=True)
        if st.button("← Back to Login", use_container_width=True, key="register_back"):
            _clear_registration_state()
            st.session_state["auth_mode"] = "Home"
            st.rerun()
    _render_auth_shell_end()


def render_register_verify():
    # Legacy route retained so existing session links do not break.
    render_register()


def render_verify():
    _render_auth_shell_start(); _render_auth_hero()
    with st.container(border=True):
        st.markdown('<div class="uh-auth-card-title">Verify your email</div><div class="uh-auth-card-copy">Enter the 6-digit code sent to your email.</div>', unsafe_allow_html=True)
        email = st.session_state.get("pending_verify_email", "")
        email = st.text_input("Email address", value=email, key="verify_email")
        otp = st.text_input("Email OTP", max_chars=6, placeholder="Enter 6-digit OTP", key="verify_otp")
        if st.button("Verify OTP", use_container_width=True, type="primary", key="verify_otp_button"):
            u = user_by_email(email.strip().lower())
            if not u: st.error("No account found with that email.")
            else:
                with st.spinner("Verifying OTP…"):
                    ok, msg = verify_otp(u["id"], "EMAIL_VERIFICATION", None, otp)
                if ok:
                    conn = get_conn(); conn.execute("UPDATE users SET verified=1 WHERE id=?", (u["id"],)); conn.commit(); conn.close()
                    notify(u["id"], "Your email has been verified.")
                    st.success("Email verified successfully.")
                    st.session_state["auth_mode"] = "Home"; st.rerun()
                else: st.error("OTP expired. Please request a new one." if "expired" in msg.lower() else "Invalid OTP. Please try again.")
        if st.button("Resend OTP", use_container_width=True, key="verify_resend"):
            u = user_by_email(email.strip().lower())
            if not u or not EMAIL_CONFIGURED: st.error("Unable to send OTP. Please try again later.")
            elif deliver_otp(u, "EMAIL_VERIFICATION", None, "email verification"): st.success("OTP sent successfully."); st.rerun()
            else: st.error("Unable to send OTP. Please try again.")
        if st.button("← Back", use_container_width=True, key="verify_back"):
            st.session_state["auth_mode"] = "Home"; st.rerun()
    _render_auth_shell_end()


def render_login():
    # Existing route retained; use the redesigned login screen.
    render_landing()


def render_admin_login():
    _render_auth_shell_start(); _render_auth_hero()
    with st.container(border=True):
        st.markdown('<div class="uh-auth-card-title">Admin Login</div><div class="uh-auth-card-copy">Secure platform management access only.</div>', unsafe_allow_html=True)
        with st.form("admin_login_form"):
            email = st.text_input("Admin email", key="admin_login_email")
            password = st.text_input("Admin password", type="password", key="admin_login_password")
            submitted = st.form_submit_button("Sign in as Admin", use_container_width=True, type="primary")
        if submitted:
            with st.spinner("Signing in securely…"):
                ok, result = login_user(email, password)
            if ok and result.get("role") == "admin":
                _set_user_and_route(result)
            elif ok:
                st.error("This account does not have administrator access.")
            else:
                st.error("Invalid admin credentials.")
        if st.button("← Back to login", use_container_width=True, key="admin_login_back"):
            st.session_state["auth_mode"] = "Home"; st.rerun()
    _render_auth_shell_end()


# 6.2 DASHBOARD
# -----------------------------------------------------------------------------

def render_dashboard(user):
    st.markdown(
        f'<div class="uh-page-header"><div><h2>Welcome back, {user["full_name"]} 👋</h2>'
        '<p>Your campus help hub — requests, borrowing, tasks and earnings in one place.</p></div></div>',
        unsafe_allow_html=True,
    )
    if not user["verified"]:
        st.warning("Your email isn't verified yet. Some actions may be limited. "
                   "Go to the sidebar → Verify Email.")

    conn = get_conn()
    completed_deliveries = conn.execute(
        "SELECT COUNT(*) c FROM requests WHERE helper_id=? AND status='COMPLETED'", (user["id"],)
    ).fetchone()["c"]
    active_borrow = conn.execute(
        "SELECT COUNT(*) c FROM borrowings WHERE borrower_id=? AND status NOT IN ('COMPLETED','REJECTED','CANCELLED')",
        (user["id"],),
    ).fetchone()["c"]
    earnings = conn.execute(
        "SELECT COALESCE(SUM(amount),0) s FROM transactions WHERE payee_id=? AND status='RELEASED'", (user["id"],)
    ).fetchone()["s"]
    conn.close()

    m1, m2, m3, m4, m5, m6 = st.columns([1, 1, 1, 1, 1, 1], gap="small")
    m1.metric("Trust Score", f"{user['trust_score']}/100")
    avg_rating = round(user["rating_sum"] / user["rating_count"], 1) if user["rating_count"] else 0
    m2.metric("Rating", f"⭐ {avg_rating}" if user["rating_count"] else "No ratings yet")
    m3.metric("Tasks Completed", completed_deliveries)
    m4.metric("Active Borrowings", active_borrow)
    m5.metric("Earnings (prototype)", f"₹{earnings:.0f}")
    m6.metric("UniCoins", f"🪙 {user['unicoins']}")

    st.markdown("#### Quick actions")
    c1, c2, c3, c4 = st.columns(4)
    if c1.button("📦 Delivery", use_container_width=True):
        st.session_state["nav"] = "Delivery"; st.rerun()
    if c2.button("🤝 Borrow", use_container_width=True):
        st.session_state["nav"] = "Borrowing"; st.rerun()
    if c3.button("🛠 Micro Task", use_container_width=True):
        st.session_state["nav"] = "Micro-Tasks"; st.rerun()
    if c4.button("💰 Wallet / Earn", use_container_width=True):
        st.session_state["nav"] = "Wallet"; st.rerun()

    st.divider()
    colL, colR = st.columns(2)
    with colL:
        st.markdown("#### 📨 Recent notifications")
        notifs = get_notifications(user["id"], limit=6)
        if not notifs:
            st.caption("No notifications yet.")
        for n in notifs:
            prefix = "🔵 " if not n["is_read"] else ""
            st.markdown(f"- {prefix}{n['message']}  \n  <small>{n['created_at'][:19].replace('T',' ')}</small>", unsafe_allow_html=True)

    with colR:
        st.markdown("#### 📦 Your active delivery requests")
        conn = get_conn()
        active = conn.execute(
            "SELECT * FROM requests WHERE requester_id=? AND status NOT IN ('COMPLETED','CANCELLED') ORDER BY id DESC",
            (user["id"],),
        ).fetchall()
        conn.close()
        if not active:
            st.caption("No active requests.")
        for r in active:
            st.markdown(f"**{r['item_name']}** — {status_badge(r['status'])}", unsafe_allow_html=True)


# -----------------------------------------------------------------------------
# 6.3 DELIVERY MODULE
# -----------------------------------------------------------------------------

def render_delivery(user):
    st.markdown('<div class="uh-page-header"><div><h2>📦 Delivery</h2><p>Move items across campus with verified student helpers.</p></div></div>', unsafe_allow_html=True)
    tabs = st.tabs(["Create Request", "Browse & Accept", "My Requests (as requester)", "My Deliveries (as helper)"])

    with tabs[0]:
        st.markdown("#### Create a new delivery request")
        with st.form("create_delivery"):
            item_name = st.text_input("Item name")
            description = st.text_area("Description")
            col1, col2 = st.columns(2)
            pickup_location = col1.text_input("Pickup location")
            destination = col2.text_input("Destination")
            use_geo = st.checkbox("Add pickup/destination coordinates (optional, enables location-verified handover)")
            pickup_lat = pickup_lng = dest_lat = dest_lng = None
            if use_geo:
                g1, g2, g3, g4 = st.columns(4)
                pickup_lat = g1.number_input("Pickup lat", value=0.0, format="%.6f")
                pickup_lng = g2.number_input("Pickup lng", value=0.0, format="%.6f")
                dest_lat = g3.number_input("Destination lat", value=0.0, format="%.6f")
                dest_lng = g4.number_input("Destination lng", value=0.0, format="%.6f")
            reward = st.number_input("Reward (₹, prototype currency)", min_value=0.0, max_value=MAX_REWARD, step=5.0)
            preferred_time = st.text_input("Preferred time", placeholder="e.g. Today evening")
            notes = st.text_area("Additional notes (optional)")
            submitted = st.form_submit_button("Create Request")
            if submitted:
                if not item_name.strip() or not pickup_location.strip() or not destination.strip():
                    st.error("Item name, pickup location and destination are required.")
                elif reward < MIN_REWARD or reward > MAX_REWARD:
                    st.error(f"Reward must be between {MIN_REWARD} and {MAX_REWARD}.")
                else:
                    conn = get_conn()
                    conn.execute(
                        """INSERT INTO requests (requester_id, item_name, description, pickup_location,
                            destination, pickup_lat, pickup_lng, dest_lat, dest_lng, reward,
                            preferred_time, notes, status, created_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'CREATED', ?)""",
                        (user["id"], item_name.strip(), description, pickup_location.strip(),
                         destination.strip(), pickup_lat or None, pickup_lng or None,
                         dest_lat or None, dest_lng or None, reward, preferred_time, notes, now_iso()),
                    )
                    conn.commit()
                    conn.close()
                    st.success("Delivery request created!")
                    st.rerun()

    with tabs[1]:
        st.markdown("#### Available requests near you")
        conn = get_conn()
        available = conn.execute(
            "SELECT r.*, u.full_name req_name FROM requests r JOIN users u ON u.id=r.requester_id "
            "WHERE r.status='CREATED' AND r.requester_id != ? ORDER BY r.id DESC",
            (user["id"],),
        ).fetchall()
        conn.close()
        if not available:
            st.caption("No open requests right now.")
        for r in available:
            with st.container(border=True):
                st.markdown(f"**{r['item_name']}** — from {r['req_name']}  {status_badge(r['status'])}", unsafe_allow_html=True)
                st.write(r["description"] or "_No description_")
                st.write(f"📍 {r['pickup_location']} → {r['destination']}  |  💰 ₹{r['reward']:.0f}  |  🕒 {r['preferred_time'] or 'Flexible'}")
                if st.button("✅ ACCEPT REQUEST", key=f"accept_{r['id']}"):
                    conn = get_conn()
                    check = conn.execute("SELECT status FROM requests WHERE id=?", (r["id"],)).fetchone()
                    if check["status"] != "CREATED":
                        st.error("This request is no longer available.")
                    else:
                        conn.execute(
                            "UPDATE requests SET helper_id=?, status='ACCEPTED', accepted_at=? WHERE id=?",
                            (user["id"], now_iso(), r["id"]),
                        )
                        conn.commit()
                        create_transaction(r["requester_id"], user["id"], "DELIVERY", r["id"], r["reward"], "HELD")
                        conn.close()
                        notify(r["requester_id"], f"Someone accepted your delivery request: {r['item_name']}.")
                        notify(user["id"], f"You accepted the delivery request: {r['item_name']}.")
                        st.success("Request accepted! A pickup OTP is now available to the requester.")
                        st.rerun()

    with tabs[2]:
        st.markdown("#### Requests you created")
        conn = get_conn()
        mine = conn.execute(
            "SELECT r.*, u.full_name helper_name FROM requests r LEFT JOIN users u ON u.id=r.helper_id "
            "WHERE r.requester_id=? ORDER BY r.id DESC", (user["id"],)
        ).fetchall()
        conn.close()
        if not mine:
            st.caption("You haven't created any delivery requests yet.")
        for r in mine:
            with st.container(border=True):
                st.markdown(f"**{r['item_name']}**  {status_badge(r['status'])}", unsafe_allow_html=True)
                st.write(f"📍 {r['pickup_location']} → {r['destination']}  |  💰 ₹{r['reward']:.0f}")
                if r["helper_id"]:
                    st.write(f"Helper: {r['helper_name']}")

                if r["status"] == "ACCEPTED":
                    st.info("Share the pickup code below with your helper when they arrive.")
                    if st.button("🔐 Generate/View Pickup OTP", key=f"genotp_{r['id']}"):
                        deliver_otp(dict(user), "PICKUP_VERIFICATION", r["id"], f"pickup of {r['item_name']}")
                    st.caption("Or show this QR code instead:")
                    if st.button("📱 Generate Pickup QR", key=f"genqr_{r['id']}"):
                        token = create_qr_token("DELIVERY", r["id"], "PICKUP_VERIFICATION")
                        st.session_state[f"qr_token_{r['id']}"] = token
                    qr_key = f"qr_token_{r['id']}"
                    if st.session_state.get(qr_key):
                        qr_tok = st.session_state[qr_key]
                        st.image(generate_qr_image_bytes(qr_tok), width=180)
                        st.caption(f"Manual fallback code: `{qr_tok}`")

                if r["status"] == "DELIVERED":
                    if st.button("✅ Confirm Completion", key=f"complete_{r['id']}"):
                        conn = get_conn()
                        conn.execute("UPDATE requests SET status='COMPLETED', completed_at=? WHERE id=?", (now_iso(), r["id"]))
                        conn.commit()
                        conn.close()
                        update_transaction_status("DELIVERY", r["id"], "RELEASED")
                        add_unicoins(r["helper_id"], 20, "Delivery completed")
                        recalc_trust_score(r["helper_id"])
                        notify(r["helper_id"], f"Your delivery of {r['item_name']} was marked completed. Payment released.")
                        st.success("Delivery marked as completed. You can now rate your helper.")
                        st.rerun()

                if r["status"] == "COMPLETED" and r["helper_id"]:
                    render_rating_widget("DELIVERY", r["id"], user["id"], r["helper_id"], "helper")

                if r["status"] == "CREATED":
                    if st.button("❌ Cancel Request", key=f"cancel_{r['id']}"):
                        conn = get_conn()
                        conn.execute("UPDATE requests SET status='CANCELLED', cancelled_at=? WHERE id=?", (now_iso(), r["id"]))
                        conn.commit()
                        conn.close()
                        st.rerun()

    with tabs[3]:
        st.markdown("#### Deliveries you're helping with")
        conn = get_conn()
        mine = conn.execute(
            "SELECT r.*, u.full_name req_name FROM requests r JOIN users u ON u.id=r.requester_id "
            "WHERE r.helper_id=? AND r.status NOT IN ('CREATED') ORDER BY r.id DESC", (user["id"],)
        ).fetchall()
        conn.close()
        if not mine:
            st.caption("You haven't accepted any deliveries yet.")
        for r in mine:
            with st.container(border=True):
                st.markdown(f"**{r['item_name']}** — for {r['req_name']}  {status_badge(r['status'])}", unsafe_allow_html=True)
                st.write(f"📍 {r['pickup_location']} → {r['destination']}  |  💰 ₹{r['reward']:.0f}")

                if r["status"] == "ACCEPTED":
                    st.markdown("**Verify pickup**")
                    method = st.radio("Verification method", ["Enter OTP", "Scan/Enter QR token"], key=f"method_{r['id']}", horizontal=True)
                    if method == "Enter OTP":
                        otp_val = st.text_input("Enter pickup OTP from requester", key=f"otpval_{r['id']}", max_chars=6)
                        if st.button("Verify OTP", key=f"verifyotp_{r['id']}"):
                            ok, msg = verify_otp(r["requester_id"], "PICKUP_VERIFICATION", r["id"], otp_val)
                            if ok:
                                _finalize_pickup(r, user)
                            else:
                                st.error(msg)
                    else:
                        token_val = st.text_input("Paste QR token (or scanned value)", key=f"qrval_{r['id']}")
                        if st.button("Verify QR", key=f"verifyqr_{r['id']}"):
                            ok, msg = verify_qr_token(token_val, "DELIVERY", r["id"], "PICKUP_VERIFICATION")
                            if ok:
                                _finalize_pickup(r, user)
                            else:
                                st.error(msg)

                    with st.expander("📍 Optional: verify you're at the pickup location"):
                        demo_loc = st.checkbox("Use DEMO MODE simulated location", key=f"demoloc_{r['id']}")
                        if demo_loc:
                            st.caption("🧪 DEMO MODE — SIMULATED LOCATION (not real GPS)")
                        glat = st.number_input("Your latitude", value=r["pickup_lat"] or 0.0, format="%.6f", key=f"glat_{r['id']}")
                        glng = st.number_input("Your longitude", value=r["pickup_lng"] or 0.0, format="%.6f", key=f"glng_{r['id']}")
                        if st.button("Check distance to pickup", key=f"checkloc_{r['id']}"):
                            ok, dist = check_location(glat, glng, r["pickup_lat"], r["pickup_lng"])
                            record_location("DELIVERY_PICKUP", r["id"], glat, glng, demo_loc)
                            if r["pickup_lat"] is None:
                                st.info("No pickup coordinates were set for this request; location check skipped.")
                            elif ok:
                                st.success(f"✅ PICKUP_ALLOWED — {dist:.0f}m from pickup point.")
                            else:
                                st.error(f"🚫 TOO FAR FROM PICKUP — {dist:.0f}m away (limit {LOCATION_RADIUS_METERS:.0f}m).")

                if r["status"] == "PICKUP_VERIFIED":
                    if st.button("🚚 Mark In Transit", key=f"transit_{r['id']}"):
                        conn = get_conn()
                        conn.execute("UPDATE requests SET status='IN_TRANSIT' WHERE id=?", (r["id"],))
                        conn.commit(); conn.close()
                        notify(r["requester_id"], f"Your item {r['item_name']} is now in transit.")
                        st.rerun()

                if r["status"] == "IN_TRANSIT":
                    if st.button("📬 Mark Delivered", key=f"delivered_{r['id']}"):
                        conn = get_conn()
                        conn.execute("UPDATE requests SET status='DELIVERED', delivered_at=? WHERE id=?", (now_iso(), r["id"]))
                        conn.commit(); conn.close()
                        notify(r["requester_id"], f"Your item {r['item_name']} has been delivered! Please confirm completion.")
                        st.rerun()

                if r["status"] == "COMPLETED":
                    render_rating_widget("DELIVERY", r["id"], user["id"], r["requester_id"], "requester")

                with st.expander("⚠ Report an issue"):
                    render_dispute_form("DELIVERY", r["id"], user["id"])


def _finalize_pickup(r, helper_user):
    conn = get_conn()
    conn.execute("UPDATE requests SET status='PICKUP_VERIFIED', pickup_verified_at=? WHERE id=?", (now_iso(), r["id"]))
    conn.commit()
    conn.close()
    notify(r["requester_id"], f"Pickup for {r['item_name']} was verified by your helper.")
    st.success("Pickup verified! Status updated to PICKUP_VERIFIED.")
    st.rerun()


# -----------------------------------------------------------------------------
# 6.4 SHARED WIDGETS: RATING + DISPUTE
# -----------------------------------------------------------------------------

def render_rating_widget(transaction_type, transaction_id, rater_id, ratee_id, ratee_label):
    conn = get_conn()
    already = conn.execute(
        "SELECT id FROM ratings WHERE transaction_type=? AND transaction_id=? AND rater_id=?",
        (transaction_type, transaction_id, rater_id),
    ).fetchone()
    conn.close()
    key_prefix = f"rate_{transaction_type}_{transaction_id}_{rater_id}"
    if already:
        st.caption(f"✅ You already rated this {ratee_label}.")
        return
    with st.expander(f"⭐ Rate your {ratee_label}"):
        stars = st.slider("Stars", 1, 5, 5, key=f"{key_prefix}_stars")
        review = st.text_input("Optional review", key=f"{key_prefix}_review")
        if st.button("Submit Rating", key=f"{key_prefix}_submit"):
            ok, msg = submit_rating(transaction_type, transaction_id, rater_id, ratee_id, stars, review)
            if ok:
                st.success(msg)
                st.rerun()
            else:
                st.error(msg)


def render_dispute_form(transaction_type, transaction_id, reporter_id):
    with st.form(f"dispute_{transaction_type}_{transaction_id}_{reporter_id}"):
        category = st.selectbox(
            "Issue category",
            ["Item damaged", "Item not returned", "Wrong delivery", "User did not appear",
             "Incorrect completion", "Other"],
        )
        description = st.text_area("Describe the issue")
        evidence = st.file_uploader("Upload evidence photo (optional)", type=["png", "jpg", "jpeg"], key=f"ev_{transaction_type}_{transaction_id}")
        submitted = st.form_submit_button("Submit Report")
        if submitted:
            evidence_path = None
            if evidence is not None:
                fname = f"{transaction_type}_{transaction_id}_{int(time.time())}_{evidence.name}"
                fpath = os.path.join(PHOTOS_DIR, fname)
                with open(fpath, "wb") as f:
                    f.write(evidence.getbuffer())
                evidence_path = fpath
            conn = get_conn()
            conn.execute(
                """INSERT INTO disputes (transaction_type, transaction_id, reporter_id, category,
                    description, evidence_path, status, created_at)
                   VALUES (?,?,?,?,?,?, 'OPEN', ?)""",
                (transaction_type, transaction_id, reporter_id, category, description, evidence_path, now_iso()),
            )
            conn.commit()
            conn.close()
            st.success("Issue reported. An admin will review it.")


# -----------------------------------------------------------------------------
# 6.5 BORROWING MODULE
# -----------------------------------------------------------------------------

def render_borrowing(user):
    st.markdown('<div class="uh-page-header"><div><h2>🤝 Borrowing</h2><p>Lend and borrow useful items from verified students.</p></div></div>', unsafe_allow_html=True)
    tabs = st.tabs(["List an Item", "Browse Items", "My Listings", "My Borrow Requests"])

    with tabs[0]:
        st.markdown("#### List an item you're willing to lend")
        with st.form("list_item"):
            item_name = st.text_input("Item name")
            category = st.selectbox("Category", ["Electronics", "Books", "Sports", "Lab Equipment", "Tools", "Accessories", "Other"])
            description = st.text_area("Description")
            condition = st.selectbox("Condition", ["Excellent", "Good", "Fair", "Worn"])
            photo = st.file_uploader("Photo (optional)", type=["png", "jpg", "jpeg"])
            availability = st.text_input("Availability", placeholder="e.g. Weekdays after 5pm")
            deposit = st.number_input("Deposit (₹, prototype)", min_value=0.0, step=10.0)
            rules = st.text_area("Borrowing rules (optional)", placeholder="e.g. Return within 3 days, no scratches")
            submitted = st.form_submit_button("List Item")
            if submitted:
                if not item_name.strip():
                    st.error("Item name is required.")
                else:
                    photo_path = None
                    if photo is not None:
                        fname = f"item_{user['id']}_{int(time.time())}_{photo.name}"
                        fpath = os.path.join(PHOTOS_DIR, fname)
                        with open(fpath, "wb") as f:
                            f.write(photo.getbuffer())
                        photo_path = fpath
                    conn = get_conn()
                    conn.execute(
                        """INSERT INTO items (owner_id, item_name, category, description, condition,
                            photo_path, availability, deposit, rules, status, created_at)
                           VALUES (?,?,?,?,?,?,?,?,?, 'AVAILABLE', ?)""",
                        (user["id"], item_name.strip(), category, description, condition,
                         photo_path, availability, deposit, rules, now_iso()),
                    )
                    conn.commit()
                    conn.close()
                    st.success("Item listed!")
                    st.rerun()

    with tabs[1]:
        st.markdown("#### Browse borrowable items")
        conn = get_conn()
        avail = conn.execute(
            "SELECT i.*, u.full_name owner_name FROM items i JOIN users u ON u.id=i.owner_id "
            "WHERE i.status='AVAILABLE' AND i.owner_id != ? ORDER BY i.id DESC", (user["id"],)
        ).fetchall()
        conn.close()
        if not avail:
            st.caption("No items available right now.")
        for it in avail:
            with st.container(border=True):
                cols = st.columns([1, 3])
                if it["photo_path"] and os.path.exists(it["photo_path"]):
                    cols[0].image(it["photo_path"], width=100)
                with cols[1]:
                    st.markdown(f"**{it['item_name']}** ({it['category']}) — by {it['owner_name']}  {status_badge(it['status'])}", unsafe_allow_html=True)
                    st.write(it["description"] or "")
                    st.write(f"Condition: {it['condition']}  |  Deposit: ₹{it['deposit']:.0f}  |  Availability: {it['availability'] or 'N/A'}")
                    expected = st.date_input("Expected return date", key=f"exp_{it['id']}")
                    if st.button("📩 REQUEST TO BORROW", key=f"reqborrow_{it['id']}"):
                        conn = get_conn()
                        check = conn.execute("SELECT status FROM items WHERE id=?", (it["id"],)).fetchone()
                        if check["status"] != "AVAILABLE":
                            st.error("This item is no longer available.")
                        else:
                            conn.execute(
                                """INSERT INTO borrowings (item_id, owner_id, borrower_id, status,
                                    expected_return, deposit, created_at)
                                   VALUES (?,?,?, 'REQUESTED', ?, ?, ?)""",
                                (it["id"], it["owner_id"], user["id"], str(expected), it["deposit"], now_iso()),
                            )
                            conn.commit()
                            conn.close()
                            notify(it["owner_id"], f"{user['full_name']} requested to borrow your {it['item_name']}.")
                            st.success("Borrow request sent to the owner.")
                            st.rerun()

    with tabs[2]:
        st.markdown("#### Items you've listed & incoming requests")
        conn = get_conn()
        my_items = conn.execute("SELECT * FROM items WHERE owner_id=? ORDER BY id DESC", (user["id"],)).fetchall()
        conn.close()
        for it in my_items:
            with st.container(border=True):
                st.markdown(f"**{it['item_name']}**  {status_badge(it['status'])}", unsafe_allow_html=True)
                conn = get_conn()
                reqs = conn.execute(
                    "SELECT b.*, u.full_name borrower_name FROM borrowings b JOIN users u ON u.id=b.borrower_id "
                    "WHERE b.item_id=? ORDER BY b.id DESC", (it["id"],)
                ).fetchall()
                conn.close()
                for b in reqs:
                    st.markdown(f"— Request from **{b['borrower_name']}**  {status_badge(b['status'])} (expected return: {b['expected_return']})", unsafe_allow_html=True)
                    if b["status"] == "REQUESTED":
                        c1, c2 = st.columns(2)
                        if c1.button("✅ ACCEPT", key=f"bacc_{b['id']}"):
                            conn = get_conn()
                            conn.execute("UPDATE borrowings SET status='ACCEPTED' WHERE id=?", (b["id"],))
                            conn.execute("UPDATE items SET status='BORROWED' WHERE id=?", (it["id"],))
                            conn.commit()
                            conn.close()
                            if b["deposit"] > 0:
                                create_transaction(b["borrower_id"], b["owner_id"], "BORROW_DEPOSIT", b["id"], b["deposit"], "HELD")
                            notify(b["borrower_id"], f"Your request to borrow {it['item_name']} was accepted!")
                            st.rerun()
                        if c2.button("❌ REJECT", key=f"brej_{b['id']}"):
                            conn = get_conn()
                            conn.execute("UPDATE borrowings SET status='REJECTED' WHERE id=?", (b["id"],))
                            conn.commit()
                            conn.close()
                            notify(b["borrower_id"], f"Your request to borrow {it['item_name']} was rejected.")
                            st.rerun()

                    if b["status"] == "ACCEPTED":
                        st.caption("Waiting for borrower to complete pickup verification (OTP/QR you provide in person).")
                        if st.button("🔐 Generate Pickup OTP", key=f"bpotp_{b['id']}"):
                            deliver_otp(dict(user), "PICKUP_VERIFICATION", 100000 + b["id"], f"handover of {it['item_name']}")
                        if st.button("📱 Generate Pickup QR", key=f"bpqr_{b['id']}"):
                            token = create_qr_token("BORROWING", b["id"], "PICKUP_VERIFICATION")
                            st.session_state[f"bqr_{b['id']}"] = token
                        if st.session_state.get(f"bqr_{b['id']}"):
                            st.image(generate_qr_image_bytes(st.session_state[f"bqr_{b['id']}"]), width=160)

                    if b["status"] == "ACTIVE":
                        st.caption("Item is currently with the borrower.")
                        if st.button("🔐 Generate Return OTP", key=f"bretotp_{b['id']}"):
                            deliver_otp(dict(user), "RETURN_VERIFICATION", 100000 + b["id"], f"return of {it['item_name']}")
                        if st.button("📱 Generate Return QR", key=f"bretqr_{b['id']}"):
                            token = create_qr_token("BORROWING", b["id"], "RETURN_VERIFICATION")
                            st.session_state[f"bretqrtok_{b['id']}"] = token
                        if st.session_state.get(f"bretqrtok_{b['id']}"):
                            st.image(generate_qr_image_bytes(st.session_state[f"bretqrtok_{b['id']}"]), width=160)

                    if b["status"] == "COMPLETED":
                        render_rating_widget("BORROWING", b["id"], user["id"], b["borrower_id"], "borrower")

                    with st.expander(f"⚠ Report issue on borrowing #{b['id']}"):
                        render_dispute_form("BORROWING", b["id"], user["id"])

    with tabs[3]:
        st.markdown("#### Items you're borrowing")
        conn = get_conn()
        mine = conn.execute(
            "SELECT b.*, i.item_name, u.full_name owner_name FROM borrowings b "
            "JOIN items i ON i.id=b.item_id JOIN users u ON u.id=b.owner_id "
            "WHERE b.borrower_id=? ORDER BY b.id DESC", (user["id"],)
        ).fetchall()
        conn.close()
        if not mine:
            st.caption("You haven't requested to borrow anything yet.")
        for b in mine:
            with st.container(border=True):
                st.markdown(f"**{b['item_name']}** — owned by {b['owner_name']}  {status_badge(b['status'])}", unsafe_allow_html=True)

                if b["status"] == "ACCEPTED":
                    st.markdown("**Verify pickup with the owner**")
                    m = st.radio("Method", ["Enter OTP", "Enter QR token"], key=f"bpm_{b['id']}", horizontal=True)
                    if m == "Enter OTP":
                        val = st.text_input("Pickup OTP", key=f"bpotpval_{b['id']}", max_chars=6)
                        if st.button("Verify pickup OTP", key=f"bpverify_{b['id']}"):
                            ok, msg = verify_otp(b["owner_id"], "PICKUP_VERIFICATION", 100000 + b["id"], val)
                            if ok:
                                _finalize_borrow_pickup(b)
                            else:
                                st.error(msg)
                    else:
                        val = st.text_input("QR token", key=f"bpqrval_{b['id']}")
                        if st.button("Verify pickup QR", key=f"bpqrverify_{b['id']}"):
                            ok, msg = verify_qr_token(val, "BORROWING", b["id"], "PICKUP_VERIFICATION")
                            if ok:
                                _finalize_borrow_pickup(b)
                            else:
                                st.error(msg)
                    photo = st.file_uploader("Upload item condition photo at pickup (optional)", type=["png", "jpg", "jpeg"], key=f"bcs_{b['id']}")
                    if photo is not None and st.button("Save condition photo", key=f"bcs_save_{b['id']}"):
                        fname = f"borrow_{b['id']}_start_{int(time.time())}_{photo.name}"
                        fpath = os.path.join(PHOTOS_DIR, fname)
                        with open(fpath, "wb") as f:
                            f.write(photo.getbuffer())
                        conn = get_conn()
                        conn.execute("UPDATE borrowings SET condition_photo_start=? WHERE id=?", (fpath, b["id"]))
                        conn.commit(); conn.close()
                        st.success("Condition photo saved.")

                if b["status"] == "ACTIVE":
                    if st.button("↩ Request Return", key=f"reqret_{b['id']}"):
                        conn = get_conn()
                        conn.execute("UPDATE borrowings SET status='RETURN_REQUESTED' WHERE id=?", (b["id"],))
                        conn.commit(); conn.close()
                        notify(b["owner_id"], f"{user['full_name']} wants to return {b['item_name']}.")
                        st.rerun()

                if b["status"] == "RETURN_REQUESTED":
                    st.markdown("**Verify return with the owner**")
                    m = st.radio("Method", ["Enter OTP", "Enter QR token"], key=f"brm_{b['id']}", horizontal=True)
                    if m == "Enter OTP":
                        val = st.text_input("Return OTP", key=f"brotpval_{b['id']}", max_chars=6)
                        if st.button("Verify return OTP", key=f"brverify_{b['id']}"):
                            ok, msg = verify_otp(b["owner_id"], "RETURN_VERIFICATION", 100000 + b["id"], val)
                            if ok:
                                _finalize_return(b, user)
                            else:
                                st.error(msg)
                    else:
                        val = st.text_input("QR token", key=f"brqrval_{b['id']}")
                        if st.button("Verify return QR", key=f"brqrverify_{b['id']}"):
                            ok, msg = verify_qr_token(val, "BORROWING", b["id"], "RETURN_VERIFICATION")
                            if ok:
                                _finalize_return(b, user)
                            else:
                                st.error(msg)

                if b["status"] == "COMPLETED":
                    render_rating_widget("BORROWING", b["id"], user["id"], b["owner_id"], "item owner")

                with st.expander(f"⚠ Report issue on borrowing #{b['id']}"):
                    render_dispute_form("BORROWING", b["id"], user["id"])


def _finalize_borrow_pickup(b):
    conn = get_conn()
    conn.execute(
        "UPDATE borrowings SET status='ACTIVE', pickup_verified=1, start_date=? WHERE id=?",
        (now_iso(), b["id"]),
    )
    conn.commit()
    conn.close()
    notify(b["owner_id"], f"Handover of {b['item_name']} verified. Borrowing is now active.")
    st.success("Pickup verified — borrowing is now ACTIVE.")
    st.rerun()


def _finalize_return(b, borrower_user):
    conn = get_conn()
    conn.execute(
        "UPDATE borrowings SET status='COMPLETED', return_verified=1, actual_return=? WHERE id=?",
        (now_iso(), b["id"]),
    )
    conn.execute("UPDATE items SET status='AVAILABLE' WHERE id=?", (b["item_id"],))
    conn.commit()
    conn.close()
    if b["deposit"] > 0:
        update_transaction_status("BORROW_DEPOSIT", b["id"], "RELEASED")
    add_unicoins(borrower_user["id"], 10, "Item returned on time")
    recalc_trust_score(borrower_user["id"])
    notify(b["owner_id"], f"{borrower_user['full_name']} returned {b['item_name']}. Borrowing completed.")
    st.success("Return verified — borrowing COMPLETED.")
    st.rerun()


# -----------------------------------------------------------------------------
# 6.6 MICRO-TASK MODULE
# -----------------------------------------------------------------------------

def render_microtasks(user):
    st.markdown('<div class="uh-page-header"><div><h2>🛠 Micro-Tasks</h2><p>Post quick campus jobs or earn by helping others.</p></div></div>', unsafe_allow_html=True)
    tabs = st.tabs(["Create Task", "Browse & Accept", "My Tasks (creator)", "My Tasks (helper)"])

    with tabs[0]:
        with st.form("create_task"):
            title = st.text_input("Task title")
            description = st.text_area("Description")
            col1, col2 = st.columns(2)
            pickup = col1.text_input("Pickup (if applicable)")
            destination = col2.text_input("Destination (if applicable)")
            reward = st.number_input("Reward (₹, prototype)", min_value=0.0, max_value=MAX_REWARD, step=5.0)
            deadline = st.text_input("Deadline", placeholder="e.g. Today 6pm")
            category = st.selectbox("Category", ["Errand", "Setup", "Physical Help", "Printing", "Food", "Other"])
            submitted = st.form_submit_button("Create Task")
            if submitted:
                if not title.strip():
                    st.error("Task title is required.")
                elif reward < MIN_REWARD or reward > MAX_REWARD:
                    st.error("Invalid reward amount.")
                else:
                    conn = get_conn()
                    conn.execute(
                        """INSERT INTO tasks (creator_id, title, description, pickup, destination,
                            reward, deadline, category, status, created_at)
                           VALUES (?,?,?,?,?,?,?,?, 'CREATED', ?)""",
                        (user["id"], title.strip(), description, pickup, destination, reward, deadline, category, now_iso()),
                    )
                    conn.commit()
                    conn.close()
                    st.success("Task created!")
                    st.rerun()

    with tabs[1]:
        conn = get_conn()
        open_tasks = conn.execute(
            "SELECT t.*, u.full_name creator_name FROM tasks t JOIN users u ON u.id=t.creator_id "
            "WHERE t.status='CREATED' AND t.creator_id != ? ORDER BY t.id DESC", (user["id"],)
        ).fetchall()
        conn.close()
        if not open_tasks:
            st.caption("No open tasks right now.")
        for t in open_tasks:
            with st.container(border=True):
                st.markdown(f"**{t['title']}** ({t['category']}) — by {t['creator_name']}  {status_badge(t['status'])}", unsafe_allow_html=True)
                st.write(t["description"] or "")
                st.write(f"💰 ₹{t['reward']:.0f}  |  ⏰ {t['deadline'] or 'Flexible'}")
                if st.button("✅ Accept Task", key=f"tacc_{t['id']}"):
                    conn = get_conn()
                    check = conn.execute("SELECT status FROM tasks WHERE id=?", (t["id"],)).fetchone()
                    if check["status"] != "CREATED":
                        st.error("This task is no longer available.")
                    else:
                        conn.execute("UPDATE tasks SET helper_id=?, status='ACCEPTED' WHERE id=?", (user["id"], t["id"]))
                        conn.commit()
                        create_transaction(t["creator_id"], user["id"], "TASK", t["id"], t["reward"], "HELD")
                        conn.close()
                        notify(t["creator_id"], f"{user['full_name']} accepted your task: {t['title']}.")
                        st.rerun()

    with tabs[2]:
        conn = get_conn()
        mine = conn.execute(
            "SELECT t.*, u.full_name helper_name FROM tasks t LEFT JOIN users u ON u.id=t.helper_id "
            "WHERE t.creator_id=? ORDER BY t.id DESC", (user["id"],)
        ).fetchall()
        conn.close()
        for t in mine:
            with st.container(border=True):
                st.markdown(f"**{t['title']}**  {status_badge(t['status'])}", unsafe_allow_html=True)
                if t["helper_id"]:
                    st.write(f"Helper: {t['helper_name']}")
                if t["status"] == "COMPLETED" and t["helper_id"]:
                    render_rating_widget("TASK", t["id"], user["id"], t["helper_id"], "helper")
                if t["status"] == "CREATED":
                    if st.button("❌ Cancel", key=f"tcancel_{t['id']}"):
                        conn = get_conn()
                        conn.execute("UPDATE tasks SET status='CANCELLED' WHERE id=?", (t["id"],))
                        conn.commit(); conn.close()
                        st.rerun()
                with st.expander(f"⚠ Report issue on task #{t['id']}"):
                    render_dispute_form("TASK", t["id"], user["id"])

    with tabs[3]:
        conn = get_conn()
        mine = conn.execute(
            "SELECT t.*, u.full_name creator_name FROM tasks t JOIN users u ON u.id=t.creator_id "
            "WHERE t.helper_id=? ORDER BY t.id DESC", (user["id"],)
        ).fetchall()
        conn.close()
        for t in mine:
            with st.container(border=True):
                st.markdown(f"**{t['title']}** — for {t['creator_name']}  {status_badge(t['status'])}", unsafe_allow_html=True)
                if t["status"] == "ACCEPTED":
                    if st.button("▶ Start Task", key=f"tstart_{t['id']}"):
                        conn = get_conn()
                        conn.execute("UPDATE tasks SET status='IN_PROGRESS' WHERE id=?", (t["id"],))
                        conn.commit(); conn.close()
                        st.rerun()
                if t["status"] == "IN_PROGRESS":
                    completion_code = st.text_input("Enter completion code from task creator (verification)", key=f"tcomp_{t['id']}", max_chars=6)
                    if st.button("Verify & Complete", key=f"tcompbtn_{t['id']}"):
                        ok, msg = verify_otp(t["creator_id"], "DELIVERY_COMPLETION", t["id"], completion_code)
                        if ok:
                            conn = get_conn()
                            conn.execute("UPDATE tasks SET status='COMPLETED', completed_at=? WHERE id=?", (now_iso(), t["id"]))
                            conn.commit()
                            conn.close()
                            update_transaction_status("TASK", t["id"], "RELEASED")
                            add_unicoins(t["helper_id"], 15, "Micro-task completed")
                            recalc_trust_score(t["helper_id"])
                            notify(t["creator_id"], f"Your task '{t['title']}' was completed and verified.")
                            st.success("Task completed!")
                            st.rerun()
                        else:
                            st.error(msg)
                if t["status"] == "COMPLETED":
                    render_rating_widget("TASK", t["id"], user["id"], t["creator_id"], "task creator")

    st.divider()
    st.caption("💡 Task creators: generate a completion code for your helper to enter once the task is done.")
    conn = get_conn()
    my_in_progress = conn.execute(
        "SELECT * FROM tasks WHERE creator_id=? AND status='IN_PROGRESS' ORDER BY id DESC", (user["id"],)
    ).fetchall()
    conn.close()
    for t in my_in_progress:
        if st.button(f"🔐 Generate completion code for '{t['title']}'", key=f"gencomp_{t['id']}"):
            deliver_otp(dict(user), "DELIVERY_COMPLETION", t["id"], f"completion of task '{t['title']}'")


# -----------------------------------------------------------------------------
# 6.7 NOTIFICATIONS / WALLET / DISPUTES PAGES
# -----------------------------------------------------------------------------

def render_notifications(user):
    st.markdown('<div class="uh-page-header"><div><h2>📨 Notifications</h2><p>Stay updated on your UNI HELP activity.</p></div></div>', unsafe_allow_html=True)
    if st.button("Mark all as read"):
        mark_notifications_read(user["id"])
        st.rerun()
    notifs = get_notifications(user["id"], limit=100)
    if not notifs:
        st.caption("No notifications.")
    for n in notifs:
        icon = "🔵" if not n["is_read"] else "⚪"
        st.markdown(f"{icon} {n['message']}  \n<small>{n['created_at'][:19].replace('T',' ')}</small>", unsafe_allow_html=True)
        st.divider()


def render_wallet(user):
    st.markdown('<div class="uh-page-header"><div><h2>💰 Wallet & UniCoins</h2><p>Track prototype earnings, escrow and community rewards.</p></div></div>', unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    c1.metric("🪙 UniCoins", user["unicoins"])
    conn = get_conn()
    earnings = conn.execute(
        "SELECT COALESCE(SUM(amount),0) s FROM transactions WHERE payee_id=? AND status='RELEASED'", (user["id"],)
    ).fetchone()["s"]
    pending = conn.execute(
        "SELECT COALESCE(SUM(amount),0) s FROM transactions WHERE payee_id=? AND status='HELD'", (user["id"],)
    ).fetchone()["s"]
    c2.metric("Earnings released (prototype ₹)", f"{earnings:.0f}")
    st.caption(f"⏳ ₹{pending:.0f} currently HELD in escrow for in-progress work.")

    st.info("⚠ PROTOTYPE TRANSACTION — no real money moves in this demo. All reward amounts are illustrative.")

    st.markdown("#### UniCoins history")
    coin_tx = conn.execute(
        "SELECT * FROM unicoin_transactions WHERE user_id=? ORDER BY id DESC LIMIT 30", (user["id"],)
    ).fetchall()
    for tx in coin_tx:
        sign = "+" if tx["amount"] >= 0 else ""
        st.write(f"{sign}{tx['amount']} — {tx['reason']}  ({tx['created_at'][:19].replace('T',' ')})")

    st.markdown("#### Transaction history (prototype ₹)")
    tx_rows = conn.execute(
        "SELECT * FROM transactions WHERE payer_id=? OR payee_id=? ORDER BY id DESC LIMIT 30",
        (user["id"], user["id"]),
    ).fetchall()
    conn.close()
    for tx in tx_rows:
        role = "You paid" if tx["payer_id"] == user["id"] else "You received"
        st.markdown(f"{role} ₹{tx['amount']:.0f} — {tx['related_type']} #{tx['related_id']}  {status_badge(tx['status'])}", unsafe_allow_html=True)


def render_disputes(user):
    st.markdown('<div class="uh-page-header"><div><h2>⚠ Disputes</h2><p>Review issues reported on your transactions.</p></div></div>', unsafe_allow_html=True)
    conn = get_conn()
    mine = conn.execute("SELECT * FROM disputes WHERE reporter_id=? ORDER BY id DESC", (user["id"],)).fetchall()
    conn.close()
    if not mine:
        st.caption("You haven't reported any issues. You can report a problem from within an active "
                   "delivery, borrowing, or task screen.")
    for d in mine:
        with st.container(border=True):
            st.markdown(f"**{d['category']}** on {d['transaction_type']} #{d['transaction_id']}  {status_badge(d['status'])}", unsafe_allow_html=True)
            st.write(d["description"] or "")
            if d["evidence_path"] and os.path.exists(d["evidence_path"]):
                st.image(d["evidence_path"], width=200)


# -----------------------------------------------------------------------------
# 6.8 ADMIN DASHBOARD
# -----------------------------------------------------------------------------

def _admin_student_avg_rating(user_row):
    return round(user_row["rating_sum"] / user_row["rating_count"], 1) if user_row["rating_count"] else 0


def render_admin_student_profile(admin_user, student_id):
    conn = get_conn()
    student = conn.execute(
        "SELECT * FROM users WHERE role='student' AND student_id=? LIMIT 1", (student_id,)
    ).fetchone()
    if not student:
        conn.close()
        st.error("Student profile could not be found.")
        if st.button("← Back to Users", key="admin_profile_missing_back"):
            st.session_state.pop("admin_profile_user_id", None); st.rerun()
        return

    sid = student["id"]
    rating = _admin_student_avg_rating(student)
    completed_tasks = conn.execute("SELECT COUNT(*) c FROM tasks WHERE helper_id=? AND status='COMPLETED'", (sid,)).fetchone()["c"]
    active_borrowings = conn.execute(
        "SELECT COUNT(*) c FROM borrowings WHERE borrower_id=? AND status NOT IN ('COMPLETED','REJECTED','CANCELLED')", (sid,)
    ).fetchone()["c"]
    completed_deliveries = conn.execute("SELECT COUNT(*) c FROM requests WHERE helper_id=? AND status='COMPLETED'", (sid,)).fetchone()["c"]
    earnings = conn.execute("SELECT COALESCE(SUM(amount),0) s FROM transactions WHERE payee_id=? AND status='RELEASED'", (sid,)).fetchone()["s"]
    transaction_count = conn.execute("SELECT COUNT(*) c FROM transactions WHERE payer_id=? OR payee_id=?", (sid, sid)).fetchone()["c"]
    unread_notifications = conn.execute("SELECT COUNT(*) c FROM notifications WHERE user_id=? AND is_read=0", (sid,)).fetchone()["c"]
    dispute_count = conn.execute("SELECT COUNT(*) c FROM disputes WHERE reporter_id=?", (sid,)).fetchone()["c"]
    conn.close()

    st.markdown("## Student Profile")
    if st.button("← Back to Users", key="admin_profile_back_top"):
        st.session_state.pop("admin_profile_user_id", None); st.rerun()

    status_html = ('<span class="uh-badge" style="background:#fee2e2;color:#991b1b;">🔴 Suspended</span>' if student["is_suspended"] else
                   '<span class="uh-badge" style="background:#dcfce7;color:#166534;">🟢 Active</span>')
    verification_html = ('<span class="uh-badge" style="background:#dcfce7;color:#166534;">✓ Verified</span>' if student["verified"] else
                         '<span class="uh-badge" style="background:#fef3c7;color:#92400e;">Pending verification</span>')
    st.markdown(f'''<div class="uh-admin-profile-card"><div class="uh-profile-header">
        <div class="uh-profile-avatar">{str(student["full_name"] or "S")[0].upper()}</div>
        <div><h2>{student["full_name"]}</h2><div class="uh-profile-sub">Student ID: <strong>{student["student_id"]}</strong></div>
        <div class="uh-profile-status">{status_html} &nbsp; {verification_html}</div></div>
    </div></div>''', unsafe_allow_html=True)

    st.markdown("### Profile information")
    info = st.columns(2)
    info[0].markdown(f"**Email**\n\n{student['email'] or '—'}")
    info[1].markdown(f"**Phone Number**\n\n{student['phone'] or '—'}")
    info[0].markdown(f"**Registration Date**\n\n{str(student['created_at'])[:19].replace('T', ' ')}")
    info[1].markdown(f"**Account Status**\n\n{'Suspended' if student['is_suspended'] else 'Active'}")

    st.markdown("### Activity statistics")
    stats = st.columns(7)
    stats[0].metric("Trust Score", f"{student['trust_score']}/100")
    stats[1].metric("Rating", f"⭐ {rating:.1f}" if rating else "—")
    stats[2].metric("Tasks Completed", completed_tasks)
    stats[3].metric("Active Borrowings", active_borrowings)
    stats[4].metric("Completed Deliveries", completed_deliveries)
    stats[5].metric("UniCoins", student["unicoins"])
    stats[6].metric("Earnings", f"₹{earnings:.0f}")

    st.markdown("### Account activity")
    ac = st.columns(3)
    ac[0].metric("Transactions", transaction_count)
    ac[1].metric("Unread Notifications", unread_notifications)
    ac[2].metric("Reported Disputes", dispute_count)

    conn = get_conn()
    tabs = st.tabs(["Delivery History", "Borrowing History", "Micro-Task History", "Transactions", "Ratings", "Notifications", "Disputes"])
    with tabs[0]:
        rows = conn.execute("SELECT r.*, u.full_name requester_name FROM requests r LEFT JOIN users u ON u.id=r.requester_id WHERE r.helper_id=? OR r.requester_id=? ORDER BY r.id DESC LIMIT 100", (sid, sid)).fetchall()
        if not rows: st.info("No delivery history for this student.")
        for r in rows:
            role = "Requested" if r["requester_id"] == sid else "Completed as helper" if r["status"] == "COMPLETED" else "Helped with"
            st.markdown(f"**#{r['id']} — {r['item_name']}** · {role} · {status_badge(r['status'])}<br>{r['pickup_location']} → {r['destination']} · ₹{r['reward']:.0f}<br><small>{str(r['created_at'])[:19].replace('T',' ')}</small>", unsafe_allow_html=True); st.divider()
    with tabs[1]:
        rows = conn.execute("SELECT b.*, i.item_name, owner.full_name owner_name, borrower.full_name borrower_name FROM borrowings b JOIN items i ON i.id=b.item_id JOIN users owner ON owner.id=b.owner_id JOIN users borrower ON borrower.id=b.borrower_id WHERE b.borrower_id=? OR b.owner_id=? ORDER BY b.id DESC LIMIT 100", (sid, sid)).fetchall()
        if not rows: st.info("No borrowing history for this student.")
        for b in rows:
            role = "Borrower" if b["borrower_id"] == sid else "Owner"
            st.markdown(f"**#{b['id']} — {b['item_name']}** · {role} · {status_badge(b['status'])}<br>Owner: {b['owner_name']} · Borrower: {b['borrower_name']}<br><small>Created {str(b['created_at'])[:19].replace('T',' ')}</small>", unsafe_allow_html=True); st.divider()
    with tabs[2]:
        rows = conn.execute("SELECT t.*, creator.full_name creator_name, helper.full_name helper_name FROM tasks t JOIN users creator ON creator.id=t.creator_id LEFT JOIN users helper ON helper.id=t.helper_id WHERE t.creator_id=? OR t.helper_id=? ORDER BY t.id DESC LIMIT 100", (sid, sid)).fetchall()
        if not rows: st.info("No micro-task history for this student.")
        for t in rows:
            role = "Creator" if t["creator_id"] == sid else "Helper"
            st.markdown(f"**#{t['id']} — {t['title']}** · {role} · {status_badge(t['status'])}<br>Creator: {t['creator_name']} · Helper: {t['helper_name'] or 'Unassigned'} · ₹{t['reward']:.0f}<br><small>{str(t['created_at'])[:19].replace('T',' ')}</small>", unsafe_allow_html=True); st.divider()
    with tabs[3]:
        rows = conn.execute("SELECT * FROM transactions WHERE payer_id=? OR payee_id=? ORDER BY id DESC LIMIT 100", (sid, sid)).fetchall()
        if not rows: st.info("No transactions for this student.")
        for tx in rows:
            role = "Paid" if tx["payer_id"] == sid else "Received"
            st.markdown(f"**#{tx['id']} — ₹{tx['amount']:.0f}** · {role} · {tx['related_type']} #{tx['related_id']} · {status_badge(tx['status'])}<br><small>{str(tx['created_at'])[:19].replace('T',' ')}</small>", unsafe_allow_html=True); st.divider()
    with tabs[4]:
        rows = conn.execute("SELECT r.*, u.full_name rater_name FROM ratings r JOIN users u ON u.id=r.rater_id WHERE r.ratee_id=? ORDER BY r.id DESC LIMIT 100", (sid,)).fetchall()
        if not rows: st.info("No ratings received by this student.")
        for r in rows:
            st.markdown(f"**{'⭐' * r['stars']}** · from {r['rater_name']} · {r['transaction_type']} #{r['transaction_id']}")
            if r["review"]: st.caption(r["review"])
            st.divider()
    with tabs[5]:
        rows = conn.execute("SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 100", (sid,)).fetchall()
        if not rows: st.info("No notifications for this student.")
        for n in rows:
            st.markdown(f"{'🔵' if not n['is_read'] else '⚪'} {n['message']}  \n<small>{str(n['created_at'])[:19].replace('T',' ')}</small>", unsafe_allow_html=True); st.divider()
    with tabs[6]:
        rows = conn.execute("SELECT * FROM disputes WHERE reporter_id=? ORDER BY id DESC LIMIT 100", (sid,)).fetchall()
        if not rows: st.info("No disputes reported by this student.")
        for d in rows:
            st.markdown(f"**#{d['id']} — {d['category']}** · {d['transaction_type']} #{d['transaction_id']} · {status_badge(d['status'])}<br>{d['description'] or ''}<br><small>{str(d['created_at'])[:19].replace('T',' ')}</small>", unsafe_allow_html=True); st.divider()
    conn.close()

    action_col, back_col = st.columns([1, 1])
    if student["is_suspended"]:
        if action_col.button("Unsuspend User", key=f"profile_unsuspend_{sid}", use_container_width=True):
            conn = get_conn(); conn.execute("UPDATE users SET is_suspended=0 WHERE id=?", (sid,)); conn.execute("INSERT INTO admin_actions (admin_id, action, target_id, details, created_at) VALUES (?,?,?,?,?)", (admin_user["id"], "UNSUSPEND_USER", sid, "", now_iso())); conn.commit(); conn.close(); st.success("User unsuspended successfully."); st.rerun()
    else:
        if action_col.button("Suspend User", key=f"profile_suspend_{sid}", use_container_width=True):
            conn = get_conn(); conn.execute("UPDATE users SET is_suspended=1 WHERE id=?", (sid,)); conn.execute("INSERT INTO admin_actions (admin_id, action, target_id, details, created_at) VALUES (?,?,?,?,?)", (admin_user["id"], "SUSPEND_USER", sid, "", now_iso())); conn.commit(); conn.close(); st.success("User suspended successfully."); st.rerun()
    if back_col.button("← Back to Users", key="admin_profile_back_bottom", use_container_width=True):
        st.session_state.pop("admin_profile_user_id", None); st.rerun()


def render_admin(user):
    if user["role"] != "admin":
        st.error("Access denied. Admins only."); return
    if st.session_state.get("admin_profile_user_id"):
        render_admin_student_profile(user, st.session_state["admin_profile_user_id"]); return
    if not EMAIL_CONFIGURED:
        st.warning("⚙️ SMTP is not configured. Add SMTP_HOST, SMTP_PORT, SMTP_USERNAME and SMTP_PASSWORD to Streamlit Secrets before enabling email OTPs.")
    if not EMAIL_CONFIGURED or not SMS_CONFIGURED:
        missing=[]
        if not EMAIL_CONFIGURED: missing.append("email SMTP")
        if not SMS_CONFIGURED: missing.append("MSG91 SMS (authkey + OTP template)")
        st.warning("Authentication configuration incomplete: " + ", ".join(missing) + ".")
    st.markdown("## 🛡 Admin Dashboard")
    conn = get_conn()
    total_users = conn.execute("SELECT COUNT(*) c FROM users WHERE role='student'").fetchone()["c"]
    verified_users = conn.execute("SELECT COUNT(*) c FROM users WHERE role='student' AND verified=1").fetchone()["c"]
    active_requests = conn.execute("SELECT COUNT(*) c FROM requests WHERE status NOT IN ('COMPLETED','CANCELLED')").fetchone()["c"]
    deliveries = conn.execute("SELECT COUNT(*) c FROM requests").fetchone()["c"]
    borrowings = conn.execute("SELECT COUNT(*) c FROM borrowings").fetchone()["c"]
    completed_tasks = conn.execute("SELECT COUNT(*) c FROM tasks WHERE status='COMPLETED'").fetchone()["c"]
    disputes_open = conn.execute("SELECT COUNT(*) c FROM disputes WHERE status='OPEN'").fetchone()["c"]
    suspended = conn.execute("SELECT COUNT(*) c FROM users WHERE is_suspended=1").fetchone()["c"]
    total_value = conn.execute("SELECT COALESCE(SUM(amount),0) s FROM transactions").fetchone()["s"]
    r1 = st.columns(4); r1[0].metric("Total Users", total_users); r1[1].metric("Verified Users", verified_users); r1[2].metric("Active Requests", active_requests); r1[3].metric("Total Deliveries", deliveries)
    r2 = st.columns(4); r2[0].metric("Borrowings", borrowings); r2[1].metric("Completed Tasks", completed_tasks); r2[2].metric("Open Disputes", disputes_open); r2[3].metric("Suspended Users", suspended)
    st.metric("Total Prototype Transaction Value (₹)", f"{total_value:.0f}")
    st.divider(); tabs = st.tabs(["Users", "Disputes", "Transactions", "Requests"])
    with tabs[0]:
        st.markdown("### Search student by Student ID")
        search_col, button_col = st.columns([5, 1])
        with search_col:
            search_id = st.text_input("Enter Student ID", placeholder="Enter Student ID …", label_visibility="collapsed", key="admin_student_search").strip()
        with button_col:
            search_clicked = st.button("Search", use_container_width=True, type="primary", key="admin_student_search_btn")
        if search_clicked:
            if not search_id:
                st.warning("Enter a Student ID to search."); st.session_state.pop("admin_search_result_id", None)
            else:
                result = conn.execute("SELECT id FROM users WHERE role='student' AND student_id=? LIMIT 1", (search_id,)).fetchone()
                if result: st.session_state["admin_search_result_id"] = result["id"]
                else: st.session_state.pop("admin_search_result_id", None); st.warning("No student found with that Student ID.")
        result_id = st.session_state.get("admin_search_result_id")
        if result_id:
            u = conn.execute("SELECT * FROM users WHERE id=? AND role='student'", (result_id,)).fetchone()
            if u:
                rating = _admin_student_avg_rating(u)
                completed_tasks_u = conn.execute("SELECT COUNT(*) c FROM tasks WHERE helper_id=? AND status='COMPLETED'", (u["id"],)).fetchone()["c"]
                active_borrow_u = conn.execute("SELECT COUNT(*) c FROM borrowings WHERE borrower_id=? AND status NOT IN ('COMPLETED','REJECTED','CANCELLED')", (u["id"],)).fetchone()["c"]
                completed_delivery_u = conn.execute("SELECT COUNT(*) c FROM requests WHERE helper_id=? AND status='COMPLETED'", (u["id"],)).fetchone()["c"]
                earnings_u = conn.execute("SELECT COALESCE(SUM(amount),0) s FROM transactions WHERE payee_id=? AND status='RELEASED'", (u["id"],)).fetchone()["s"]
                status = "Suspended" if u["is_suspended"] else "Active"; verification = "Verified" if u["verified"] else "Unverified"
                st.markdown(f'''<div class="uh-admin-search-result"><div class="uh-profile-header"><div class="uh-profile-avatar">{str(u["full_name"] or "S")[0].upper()}</div><div><h3>{u["full_name"]}</h3><div class="uh-profile-sub">Student ID: <strong>{u["student_id"]}</strong></div></div></div><div class="uh-profile-grid"><div><span>Email</span><strong>{u["email"]}</strong></div><div><span>Phone</span><strong>{u["phone"] or '—'}</strong></div><div><span>Verification</span><strong>{verification}</strong></div><div><span>Trust Score</span><strong>{u["trust_score"]}/100</strong></div><div><span>Rating</span><strong>{f'⭐ {rating:.1f}' if rating else '—'}</strong></div><div><span>Tasks Completed</span><strong>{completed_tasks_u}</strong></div><div><span>Active Borrowings</span><strong>{active_borrow_u}</strong></div><div><span>Completed Deliveries</span><strong>{completed_delivery_u}</strong></div><div><span>Earnings</span><strong>₹{earnings_u:.0f}</strong></div><div><span>UniCoins</span><strong>🪙 {u["unicoins"]}</strong></div><div><span>Account Status</span><strong>{status}</strong></div><div><span>Registered</span><strong>{str(u["created_at"])[:10]}</strong></div></div></div>''', unsafe_allow_html=True)
                if st.button("View Full Profile", key=f"view_profile_{u['id']}", type="primary", use_container_width=True):
                    st.session_state["admin_profile_user_id"] = u["student_id"]; st.session_state.pop("admin_search_result_id", None); st.rerun()
        st.markdown("### All students")
        users = conn.execute("SELECT * FROM users WHERE role='student' ORDER BY id DESC").fetchall()
        for u in users:
            with st.container(border=True):
                cols = st.columns([4, 1, 1, 1])
                cols[0].write(f"**{u['full_name']}** · Student ID: **{u['student_id'] or '—'}** · {u['email']} — Trust: {u['trust_score']}  {'🟢 Verified' if u['verified'] else '🟡 Unverified'}  {'🔴 Suspended' if u['is_suspended'] else '🟢 Active'}")
                if cols[1].button("Profile", key=f"profile_list_{u['id']}"):
                    st.session_state["admin_profile_user_id"] = u["student_id"]; st.rerun()
                if u["is_suspended"]:
                    if cols[2].button("Unsuspend", key=f"unsusp_{u['id']}"):
                        conn.execute("UPDATE users SET is_suspended=0 WHERE id=?", (u["id"],)); conn.commit(); conn.execute("INSERT INTO admin_actions (admin_id, action, target_id, details, created_at) VALUES (?,?,?,?,?)", (user["id"], "UNSUSPEND_USER", u["id"], "", now_iso())); conn.commit(); st.rerun()
                else:
                    if cols[2].button("Suspend", key=f"susp_{u['id']}"):
                        conn.execute("UPDATE users SET is_suspended=1 WHERE id=?", (u["id"],)); conn.execute("INSERT INTO admin_actions (admin_id, action, target_id, details, created_at) VALUES (?,?,?,?,?)", (user["id"], "SUSPEND_USER", u["id"], "", now_iso())); conn.commit(); st.rerun()
    with tabs[1]:
        disputes = conn.execute("SELECT d.*, u.full_name reporter_name FROM disputes d JOIN users u ON u.id=d.reporter_id ORDER BY d.id DESC").fetchall()
        for d in disputes:
            with st.container(border=True):
                st.markdown(f"**{d['category']}** — {d['transaction_type']} #{d['transaction_id']} reported by {d['reporter_name']}  {status_badge(d['status'])}", unsafe_allow_html=True); st.write(d["description"] or "")
                if d["evidence_path"] and os.path.exists(d["evidence_path"]): st.image(d["evidence_path"], width=200)
                if d["status"] in ("OPEN", "UNDER_REVIEW"):
                    c1,c2,c3=st.columns(3)
                    if c1.button("Mark Under Review", key=f"dur_{d['id']}"): conn.execute("UPDATE disputes SET status='UNDER_REVIEW' WHERE id=?", (d["id"],)); conn.commit(); st.rerun()
                    if c2.button("Resolve", key=f"dres_{d['id']}"): conn.execute("UPDATE disputes SET status='RESOLVED', resolved_at=? WHERE id=?", (now_iso(), d["id"])); conn.commit(); conn.execute("INSERT INTO admin_actions (admin_id, action, target_id, details, created_at) VALUES (?,?,?,?,?)", (user["id"], "RESOLVE_DISPUTE", d["id"], "", now_iso())); conn.commit(); st.rerun()
                    if c3.button("Reject", key=f"drej_{d['id']}"): conn.execute("UPDATE disputes SET status='REJECTED', resolved_at=? WHERE id=?", (now_iso(), d["id"])); conn.commit(); st.rerun()
    with tabs[2]:
        txs=conn.execute("SELECT * FROM transactions ORDER BY id DESC LIMIT 100").fetchall()
        for tx in txs: st.markdown(f"#{tx['id']} — ₹{tx['amount']:.0f} — {tx['related_type']} #{tx['related_id']}  {status_badge(tx['status'])}", unsafe_allow_html=True)
    with tabs[3]:
        reqs=conn.execute("SELECT * FROM requests ORDER BY id DESC LIMIT 100").fetchall()
        for r in reqs: st.markdown(f"#{r['id']} {r['item_name']}  {status_badge(r['status'])}", unsafe_allow_html=True)
    conn.close()


# =============================================================================
# 7. MAIN ROUTER
# =============================================================================

def render_sidebar(user):
    if user["role"] == "admin":
        st.sidebar.markdown("### 🛡 UNI HELP ADMIN")
        st.sidebar.write(f"**{user['full_name']}**")
        st.sidebar.caption("Platform management")
        st.session_state["nav"] = "Admin"
        if st.sidebar.button("🚪 Logout", use_container_width=True):
            st.session_state["user"] = None
            st.session_state["auth_mode"] = "Home"
            st.rerun()
        return

    st.sidebar.markdown("### 🎓 UNI HELP")
    st.sidebar.write(f"**{user['full_name']}**")
    st.sidebar.caption(user["email"])
    if not user["verified"]:
        st.sidebar.warning("Email not verified")

    unread = len(get_notifications(user["id"], unread_only=True))
    options = ["Dashboard", "Delivery", "Borrowing", "Micro-Tasks",
               f"Notifications ({unread})" if unread else "Notifications",
               "Wallet", "Disputes"]
    clean_map = {opt: opt.split(" (")[0] for opt in options}
    current_clean = st.session_state.get("nav", "Dashboard")
    display_current = next((o for o in options if clean_map[o] == current_clean), options[0])
    choice = st.sidebar.radio("Navigate", options, index=options.index(display_current))
    st.session_state["nav"] = clean_map[choice]

    st.sidebar.divider()
    if not user["verified"]:
        if st.sidebar.button("📧 Verify Email"):
            st.session_state["pending_verify_email"] = user["email"]
            st.session_state["user"] = None
            st.session_state["auth_mode"] = "Verify"
            st.rerun()
    if st.sidebar.button("🚪 Logout"):
        st.session_state["user"] = None
        st.session_state["auth_mode"] = "Home"
        st.rerun()

def main():
    user = st.session_state.get("user")

    if user is None:
        mode = st.session_state.get("auth_mode", "Home")
        if mode == "Register":
            render_register()
        elif mode == "RegisterVerify":
            render_register_verify()
        elif mode == "Login":
            render_login()
        elif mode == "AdminLogin":
            render_admin_login()
        elif mode == "Verify":
            render_verify()
        else:
            render_landing()
        return

    refresh_current_user()
    user = st.session_state["user"]
    render_sidebar(user)

    nav = st.session_state.get("nav", "Dashboard")
    if nav == "Dashboard":
        render_dashboard(user)
    elif nav == "Delivery":
        render_delivery(user)
    elif nav == "Borrowing":
        render_borrowing(user)
    elif nav == "Micro-Tasks":
        render_microtasks(user)
    elif nav == "Notifications":
        render_notifications(user)
    elif nav == "Wallet":
        render_wallet(user)
    elif nav == "Disputes":
        render_disputes(user)
    elif nav == "Admin":
        render_admin(user)
    else:
        render_dashboard(user)


if __name__ == "__main__":
    main()
