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

try:
    from twilio.rest import Client as TwilioClient
    TWILIO_SDK_AVAILABLE = True
except ImportError:
    TwilioClient = None
    TWILIO_SDK_AVAILABLE = False

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
    TWILIO_ACCOUNT_SID = str(st.secrets.get("TWILIO_ACCOUNT_SID", "")).strip()
    TWILIO_AUTH_TOKEN = str(st.secrets.get("TWILIO_AUTH_TOKEN", "")).strip()
    TWILIO_VERIFY_SERVICE_SID = str(st.secrets.get("TWILIO_VERIFY_SERVICE_SID", "")).strip()
except Exception:
    TWILIO_ACCOUNT_SID = ""
    TWILIO_AUTH_TOKEN = ""
    TWILIO_VERIFY_SERVICE_SID = ""
SMS_CONFIGURED = bool(
    TWILIO_SDK_AVAILABLE
    and TWILIO_ACCOUNT_SID
    and TWILIO_AUTH_TOKEN
    and TWILIO_VERIFY_SERVICE_SID
)
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
    user_columns = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "profile_photo_path" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN profile_photo_path TEXT")

    # Backward-compatible migration for existing UNI HELP databases.
    # Older databases did not have tasks.accepted_at; adding it lets us keep
    # the active-task experience without replacing or resetting existing data.
    task_columns = {row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    if "accepted_at" not in task_columns:
        conn.execute("ALTER TABLE tasks ADD COLUMN accepted_at TEXT")

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


def is_valid_phone(phone):
    """Registration accepts a plain 10-digit Indian mobile number; country code is added internally for providers."""
    raw = str(phone or "").strip()
    return bool(re.fullmatch(r"\d{10}", raw))


def is_valid_student_id(student_id):
    """UNI HELP student IDs are exactly 8 digits and start with the common 126 prefix."""
    return bool(re.fullmatch(r"126\d{5}", str(student_id or "").strip()))


def _twilio_verify_client():
    """Return the configured Twilio Verify client without exposing secrets."""
    if not SMS_CONFIGURED:
        return None
    return TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)


def send_twilio_verify_sms(to_phone):
    """Start a real Twilio Verify SMS verification. No OTP is generated or stored locally."""
    if not SMS_CONFIGURED:
        st.session_state["_last_sms_error"] = "Twilio Verify is not configured."
        return False
    phone = normalize_phone(to_phone)
    if not phone:
        st.session_state["_last_sms_error"] = "Invalid phone number."
        return False
    try:
        client = _twilio_verify_client()
        verification = (
            client.verify.v2
            .services(TWILIO_VERIFY_SERVICE_SID)
            .verifications.create(to=phone, channel="sms")
        )
        return getattr(verification, "status", "") in {"pending", "approved"}
    except Exception as exc:
        # Keep provider details server-side; never surface credentials or raw API responses.
        st.session_state["_last_sms_error"] = str(exc)
        return False


def verify_twilio_sms(to_phone, code):
    """Check a user-entered code against Twilio Verify."""
    if not SMS_CONFIGURED:
        return False, "SMS verification is not configured. Please contact the administrator."
    phone = normalize_phone(to_phone)
    code = str(code or "").strip()
    if not phone:
        return False, "No valid mobile number is registered."
    if not re.fullmatch(r"\d{6}", code):
        return False, "Please enter the 6-digit OTP."
    try:
        client = _twilio_verify_client()
        check = (
            client.verify.v2
            .services(TWILIO_VERIFY_SERVICE_SID)
            .verification_checks.create(to=phone, code=code)
        )
        status = getattr(check, "status", "")
        if status == "approved":
            return True, "Verified successfully."
        if status == "pending":
            return False, "Invalid OTP. Please try again."
        return False, "OTP verification failed. Please request a new OTP."
    except Exception as exc:
        st.session_state["_last_sms_error"] = str(exc)
        return False, "Unable to verify OTP. Please try again."


def deliver_sms_otp(user_row, purpose, reference_id=None, context_label="verification"):
    """Start a Twilio Verify SMS challenge. Twilio owns OTP generation, expiry and verification."""
    return send_twilio_verify_sms(user_row.get("phone") or "")


def register_user(full_name,email,phone,student_id,password):
    email=email.strip().lower(); phone=normalize_phone(phone); student_id=student_id.strip()
    if not full_name.strip(): return False,"Full name is required."
    if not is_valid_email(email): return False,"Please enter a valid email address."
    if not phone: return False,"Please enter a valid 10-digit mobile number."
    if not is_valid_student_id(student_id): return False,"Student ID must be exactly 8 digits and start with 126."
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
    if not row["verified"]: return False,"Please complete email verification before logging in."
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
:root{
  --uh-navy:#081a3a; --uh-blue:#2563eb; --uh-blue2:#60a5fa; --uh-cyan:#22d3ee;
  --uh-orange:#f97316; --uh-bg:#f4f8ff; --uh-text:#0f1f3d; --uh-muted:#64748b;
  --uh-line:#dbe5f2; --uh-card:rgba(255,255,255,.92)
}
.stApp:has(.uh-auth-shell){
  background:
    radial-gradient(circle at 8% 12%,rgba(37,99,235,.13),transparent 24%),
    radial-gradient(circle at 88% 18%,rgba(34,211,238,.10),transparent 21%),
    radial-gradient(circle at 82% 92%,rgba(249,115,22,.08),transparent 24%),
    linear-gradient(135deg,#f8fbff 0%,#f3f7fd 48%,#eef5ff 100%);
}
.uh-auth-shell{min-height:calc(100dvh - 1rem);display:flex;align-items:center;justify-content:center;padding:.45rem .8rem;box-sizing:border-box;overflow:hidden;position:relative}
.uh-auth-shell:before,.uh-auth-shell:after{content:"";position:absolute;border-radius:999px;filter:blur(2px);pointer-events:none;animation:uhFloat 7s ease-in-out infinite}
.uh-auth-shell:before{width:190px;height:190px;left:-75px;top:11%;background:radial-gradient(circle,rgba(37,99,235,.16),transparent 68%)}
.uh-auth-shell:after{width:230px;height:230px;right:-90px;bottom:5%;background:radial-gradient(circle,rgba(249,115,22,.12),transparent 68%);animation-delay:-3s}
.uh-auth-content{width:min(100%,460px);margin:auto;position:relative;z-index:1}
.uh-auth-hero{text-align:center;margin:0 auto .6rem;animation:uhFadeUp .55s cubic-bezier(.22,1,.36,1) both}
.uh-auth-logo-wrap{position:relative;width:62px;height:62px;margin:0 auto .45rem}
.uh-auth-logo-ring{position:absolute;inset:-5px;border-radius:20px;background:linear-gradient(135deg,rgba(37,99,235,.13),rgba(96,165,250,.03));animation:uhPulse 2.8s ease-in-out infinite}
.uh-auth-logo-mark{position:relative;width:62px;height:62px;border-radius:20px;display:flex;align-items:center;justify-content:center;background:linear-gradient(145deg,#ffffff,#edf4ff);border:1px solid #d6e3f4;box-shadow:0 14px 32px rgba(8,26,58,.12),inset 0 1px 0 #fff;font-size:1.7rem}
.uh-auth-kicker{display:inline-flex;align-items:center;gap:.35rem;padding:.23rem .52rem;border-radius:999px;background:rgba(255,255,255,.75);border:1px solid #dfe8f3;color:#31507d;font-size:.6rem;font-weight:800;letter-spacing:.08em;text-transform:uppercase;margin-bottom:.34rem;box-shadow:0 4px 12px rgba(8,26,58,.04)}
.uh-auth-kicker-dot{width:6px;height:6px;border-radius:999px;background:linear-gradient(135deg,#22c55e,#16a34a);box-shadow:0 0 0 4px rgba(34,197,94,.10)}
.uh-auth-hero h1{color:var(--uh-navy)!important;font-size:2.2rem!important;line-height:1!important;margin:0!important;letter-spacing:-.06em;font-weight:900}
.uh-auth-tagline{color:#23385b!important;font-size:.94rem;line-height:1.28;margin:.36rem 0 .15rem;font-weight:700}
.uh-auth-subtitle{color:#6b7d97!important;font-size:.72rem;margin:0;letter-spacing:.08em;font-weight:700}
.stApp:has(.uh-auth-shell) [data-testid="stVerticalBlockBorderWrapper"]{position:relative;background:var(--uh-card)!important;border:1px solid rgba(214,226,241,.92)!important;border-radius:24px!important;box-shadow:0 26px 60px rgba(8,26,58,.12),0 6px 18px rgba(8,26,58,.05)!important;backdrop-filter:blur(18px);padding:1rem!important;animation:uhCardIn .6s .06s cubic-bezier(.22,1,.36,1) both;overflow:hidden}
.stApp:has(.uh-auth-shell) [data-testid="stVerticalBlockBorderWrapper"]:before{content:"";position:absolute;left:-10%;right:-10%;top:-45%;height:70%;background:radial-gradient(circle at 50% 75%,rgba(37,99,235,.075),transparent 58%);pointer-events:none}
.stApp:has(.uh-auth-shell) [data-testid="stVerticalBlockBorderWrapper"]>div{position:relative;z-index:1}
.uh-auth-tabs{display:grid;grid-template-columns:1fr 1fr;gap:.25rem;background:#edf3fa;border:1px solid #dde7f3;border-radius:13px;padding:.23rem;margin-bottom:.72rem}
.uh-auth-tab-button button{border:0!important;background:transparent!important;color:#71819a!important;box-shadow:none!important;min-height:38px!important;border-radius:10px!important;font-size:.75rem!important;font-weight:850!important;letter-spacing:.04em;transition:all .18s ease!important}
.uh-auth-tab-button-active button{background:linear-gradient(135deg,#fff,#f8fbff)!important;color:var(--uh-navy)!important;box-shadow:0 6px 14px rgba(8,26,58,.08),inset 0 0 0 1px #e4ebf4!important}
.uh-auth-card-title{color:var(--uh-navy);font-size:1.2rem;font-weight:850;margin:.12rem 0 .1rem;letter-spacing:-.02em}
.uh-auth-card-copy{color:#61728b!important;font-size:.75rem!important;margin:0 0 .72rem!important}
.uh-auth-demo{margin:.55rem 0 0;padding:.45rem .62rem;border-radius:10px;background:#fff7ed;border:1px solid #fed7aa;color:#9a3412!important;font-size:.68rem;text-align:center}
.uh-auth-divider{display:flex;align-items:center;gap:.55rem;color:#94a3b8;font-size:.67rem;margin:.62rem 0}.uh-auth-divider:before,.uh-auth-divider:after{content:"";height:1px;flex:1;background:linear-gradient(90deg,transparent,#dbe5f2,transparent)}
.uh-otp-note{text-align:center;padding:.42rem .6rem;background:linear-gradient(135deg,#eff6ff,#f5f9ff);border:1px solid #c9dcfb;color:#1e40af!important;border-radius:10px;font-size:.69rem;margin:.35rem 0 .5rem}
.uh-auth-status{padding:.5rem .62rem;border-radius:10px;font-size:.7rem;font-weight:700;margin:.35rem 0;background:#f8fbff;border:1px solid #dce8f6;color:#375273}.uh-auth-status.ok{background:#ecfdf5;color:#047857;border-color:#a7f3d0}.uh-auth-status.err{background:#fff1f2;color:#be123c;border-color:#fecdd3}
.stApp:has(.uh-auth-shell) div[data-testid="stTextInput"] label,.stApp:has(.uh-auth-shell) div[data-testid="stTextInput"] label p{color:#30425f!important;font-weight:750!important;font-size:.7rem!important}
.stApp:has(.uh-auth-shell) div[data-testid="stTextInput"] input{border:1px solid #c8d4e3!important;border-radius:11px!important;background:rgba(255,255,255,.96)!important;color:#0f172a!important;min-height:40px!important;box-shadow:inset 0 1px 1px rgba(8,26,58,.02)!important;font-size:.83rem!important;transition:border-color .18s ease,box-shadow .18s ease,transform .18s ease!important}
.stApp:has(.uh-auth-shell) div[data-testid="stTextInput"] input:focus{border-color:#5b8def!important;box-shadow:0 0 0 3px rgba(37,99,235,.11),0 5px 14px rgba(37,99,235,.06)!important;transform:translateY(-1px)}
.stApp:has(.uh-auth-shell) button{border-radius:11px!important;min-height:40px!important;font-weight:800!important;transition:transform .16s ease,box-shadow .18s ease,border-color .18s ease,background .18s ease!important}
.stApp:has(.uh-auth-shell) button:hover{transform:translateY(-2px)}
.stApp:has(.uh-auth-shell) button:active{transform:translateY(0) scale(.985)}
.stApp:has(.uh-auth-shell) button[kind="primary"]{background:linear-gradient(135deg,#2563eb 0%,#4f7cff 62%,#60a5fa 100%)!important;color:#fff!important;border:0!important;box-shadow:0 10px 20px rgba(37,99,235,.20)!important;position:relative;overflow:hidden}
.stApp:has(.uh-auth-shell) button[kind="primary"]:after{content:"";position:absolute;top:-40%;left:-30%;width:30%;height:180%;transform:rotate(18deg);background:linear-gradient(90deg,transparent,rgba(255,255,255,.32),transparent);animation:uhShimmer 3.2s linear infinite}
.uh-auth-secondary button{background:#fff!important;color:var(--uh-navy)!important;border:1px solid #cbd8e7!important;box-shadow:0 4px 10px rgba(8,26,58,.03)!important}
.uh-auth-secondary button:hover{border-color:#8eb6fb!important;box-shadow:0 7px 15px rgba(8,26,58,.07)!important}
.uh-auth-create button,.uh-auth-back button{background:transparent!important;color:#2563eb!important;border:0!important;box-shadow:none!important}
.uh-auth-back button{color:#64748b!important}
.uh-auth-mini-row{display:flex;justify-content:center;align-items:center;gap:.38rem;color:#8a99ad;font-size:.63rem;margin-top:.46rem}
.uh-auth-mini-row b{color:#526782}
@keyframes uhFadeUp{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:none}}
@keyframes uhCardIn{from{opacity:0;transform:translateY(16px) scale(.985)}to{opacity:1;transform:none}}
@keyframes uhFloat{0%,100%{transform:translate3d(0,0,0)}50%{transform:translate3d(10px,-12px,0)}}
@keyframes uhPulse{0%,100%{transform:scale(.98);opacity:.9}50%{transform:scale(1.05);opacity:1}}
@keyframes uhShimmer{0%{left:-35%}55%,100%{left:120%}}
@media(max-width:640px){.uh-auth-shell{min-height:100dvh;padding:.35rem .45rem;overflow:visible}.uh-auth-content{width:min(100%,410px)}.uh-auth-hero{margin-bottom:.48rem}.uh-auth-logo-wrap,.uh-auth-logo-mark{width:52px;height:52px}.uh-auth-logo-mark{border-radius:17px;font-size:1.4rem}.uh-auth-hero h1{font-size:1.9rem!important}.uh-auth-tagline{font-size:.82rem}.uh-auth-subtitle{font-size:.66rem}.stApp:has(.uh-auth-shell) [data-testid="stVerticalBlockBorderWrapper"]{padding:.78rem!important;border-radius:19px!important}.stApp:has(.uh-auth-shell) div[data-testid="stTextInput"] input{min-height:40px!important}.stApp:has(.uh-auth-shell) button{min-height:41px!important}.uh-auth-shell:before{left:-110px}.uh-auth-shell:after{right:-120px}}

/* Student dashboard / profile polish */
.uh-app-context{display:flex;align-items:baseline;gap:.7rem;margin:.1rem 0 .8rem;color:#0b1f44;font-weight:900;letter-spacing:-.02em}.uh-app-context span{font-size:.9rem}.uh-app-context small{font-size:.7rem;color:#71819a;font-weight:700}.uh-side-label{font-size:.63rem;letter-spacing:.12em;font-weight:850;color:#8b9ab0;margin:.7rem 0 .25rem}.uh-side-current{padding:.55rem .7rem;border-radius:10px;background:#eaf2ff;color:#174ea6;font-weight:800;font-size:.82rem}.uh-dashboard-hero{display:flex;justify-content:space-between;align-items:center;gap:1.5rem;padding:1.15rem 1.35rem;border-radius:22px;background:linear-gradient(135deg,#0a1d42 0%,#123d80 62%,#2563eb 100%);box-shadow:0 18px 42px rgba(8,26,58,.16);margin-bottom:1rem;overflow:hidden;position:relative}.uh-dashboard-hero:after{content:"";position:absolute;width:260px;height:260px;border-radius:50%;right:-100px;top:-150px;background:rgba(255,255,255,.09)}.uh-dashboard-eyebrow{font-size:.6rem;letter-spacing:.14em;color:#9fc4ff;font-weight:850;margin-bottom:.35rem}.uh-dashboard-hero h1{color:#fff!important;margin:0!important;font-size:1.7rem!important;letter-spacing:-.04em}.uh-dashboard-hero p{color:#dbeafe!important;margin:.3rem 0 0;font-size:.76rem}.uh-dashboard-trust{min-width:125px;position:relative;z-index:1;color:#fff;text-align:right}.uh-dashboard-trust span{display:block;font-size:.63rem;color:#bfdbfe}.uh-dashboard-trust strong{display:block;font-size:1.45rem}.uh-progress{height:5px;background:rgba(255,255,255,.18);border-radius:99px;margin-top:.35rem;overflow:hidden}.uh-progress i{display:block;height:100%;background:#fb923c;border-radius:99px}.uh-stat-card{display:flex;align-items:center;gap:.7rem;background:#fff;border:1px solid #e2eaf4;border-radius:17px;padding:.75rem .8rem;box-shadow:0 8px 22px rgba(8,26,58,.055);min-height:70px}.uh-stat-icon,.uh-action-icon,.uh-info-icon{width:38px;height:38px;border-radius:12px;display:flex;align-items:center;justify-content:center;background:#edf4ff;font-size:1.05rem;flex:none}.uh-stat-card span,.uh-info-card span{display:block;color:#7a8aa1;font-size:.62rem;font-weight:750}.uh-stat-card strong{display:block;color:#0b1f44;font-size:1.08rem;margin-top:.08rem}.uh-section-heading{display:flex;justify-content:space-between;align-items:end;margin:1.25rem 0 .7rem}.uh-section-heading span,.uh-page-kicker{font-size:.58rem;letter-spacing:.13em;color:#2563eb;font-weight:900}.uh-section-heading h2{font-size:1.05rem!important;color:#10274e!important;margin:.12rem 0 0!important}.uh-section-space{margin-top:1.15rem}.uh-action-card{height:112px;padding:.85rem;border:1px solid #e1e9f3;border-radius:17px;background:linear-gradient(180deg,#fff,#f9fbff);box-shadow:0 8px 22px rgba(8,26,58,.045);transition:transform .18s ease,box-shadow .18s ease,border-color .18s ease}.uh-action-card:hover{transform:translateY(-3px);box-shadow:0 14px 28px rgba(8,26,58,.09);border-color:#bfd5f7}.uh-action-icon{width:32px;height:32px;border-radius:10px}.uh-action-card h3{font-size:.85rem!important;color:#10274e!important;margin:.35rem 0 .08rem!important}.uh-action-card p{font-size:.64rem;color:#71819a!important;margin:0!important}.uh-info-card{display:flex;gap:.8rem;align-items:center;padding:.85rem 1rem;border:1px solid #e1e9f3;border-radius:17px;background:#fff;box-shadow:0 8px 22px rgba(8,26,58,.045)}.uh-info-icon{background:#fff3ea}.uh-info-card strong{display:block;color:#10274e;font-size:1.12rem}.uh-info-card p{font-size:.63rem;color:#7a8aa1;margin:.12rem 0 0}.uh-soft-alert{margin-top:1rem;padding:.7rem .85rem;border:1px solid #bfdbfe;background:#eff6ff;border-radius:13px;color:#1e40af;font-size:.7rem}.uh-profile-photo-placeholder{width:150px;height:150px;border-radius:28px;background:linear-gradient(135deg,#0a1d42,#2563eb);color:#fff;display:flex;align-items:center;justify-content:center;font-size:2.4rem;font-weight:900;box-shadow:0 16px 34px rgba(8,26,58,.15)}.uh-profile-main-card{padding:1rem 1.1rem;border-radius:18px;border:1px solid #e0e8f2;background:#fff;box-shadow:0 10px 25px rgba(8,26,58,.05)}.uh-profile-name{font-size:1.35rem;font-weight:900;color:#0b1f44}.uh-profile-id{font-size:.72rem;color:#71819a;margin-top:.2rem}.uh-profile-badges{display:flex;gap:.45rem;margin-top:.65rem;flex-wrap:wrap}.uh-profile-badge{padding:.25rem .55rem;border-radius:999px;font-size:.62rem;font-weight:850}.uh-profile-badge.green{background:#dcfce7;color:#166534}.uh-profile-badge.blue{background:#eaf2ff;color:#174ea6}.uh-detail-card{padding:.7rem .8rem;border:1px solid #e2eaf4;background:#fff;border-radius:13px;margin-bottom:.55rem}.uh-detail-card span{display:block;color:#7a8aa1;font-size:.6rem;font-weight:750}.uh-detail-card strong{display:block;color:#10274e;font-size:.78rem;margin-top:.12rem}@media(max-width:760px){.uh-dashboard-hero{padding:1rem;display:block}.uh-dashboard-hero h1{font-size:1.4rem!important}.uh-dashboard-trust{text-align:left;margin-top:.8rem}.uh-stat-card{min-height:62px;padding:.6rem}.uh-action-card{height:105px}.uh-profile-photo-placeholder{width:110px;height:110px;border-radius:22px;font-size:1.8rem}}

/* Premium admin control center */
.uh-admin-shell{background:linear-gradient(135deg,#f7faff 0%,#eef5ff 55%,#fff8f1 100%);border:1px solid #e1e9f4;border-radius:24px;padding:1.15rem 1.25rem;margin-bottom:1rem;box-shadow:0 14px 38px rgba(8,26,58,.07)}
.uh-admin-eyebrow{font-size:.58rem;letter-spacing:.16em;color:#2563eb;font-weight:900;text-transform:uppercase}
.uh-admin-title{font-size:1.65rem;font-weight:900;color:#081a3a;letter-spacing:-.04em;margin:.15rem 0 .15rem}
.uh-admin-subtitle{font-size:.73rem;color:#64748b;margin:0}
.uh-admin-online{display:inline-flex;align-items:center;gap:.35rem;padding:.34rem .55rem;border-radius:999px;background:#ecfdf5;color:#047857;border:1px solid #bbf7d0;font-size:.62rem;font-weight:850}
.uh-admin-dot{width:7px;height:7px;border-radius:50%;background:#22c55e;box-shadow:0 0 0 4px rgba(34,197,94,.10)}
.uh-admin-metric{height:100%;min-height:92px;padding:.9rem 1rem;border:1px solid #e0e8f2;border-radius:17px;background:#fff;box-shadow:0 8px 22px rgba(8,26,58,.05);transition:transform .18s ease,box-shadow .18s ease}
.uh-admin-metric:hover{transform:translateY(-2px);box-shadow:0 13px 28px rgba(8,26,58,.08)}
.uh-admin-metric .icon{font-size:1.05rem;margin-bottom:.35rem}.uh-admin-metric .label{font-size:.62rem;color:#71819a;font-weight:750}.uh-admin-metric .value{font-size:1.35rem;color:#0b1f44;font-weight:900;line-height:1.05;margin-top:.12rem}.uh-admin-metric .hint{font-size:.57rem;color:#94a3b8;margin-top:.2rem}
.uh-admin-section{font-size:1rem;font-weight:900;color:#10274e;margin:1rem 0 .55rem}
.uh-admin-panel{padding:.85rem 1rem;border:1px solid #e1e9f3;border-radius:17px;background:#fff;box-shadow:0 8px 22px rgba(8,26,58,.045)}
.uh-admin-panel-title{font-size:.78rem;font-weight:900;color:#10274e;margin-bottom:.25rem}.uh-admin-panel-copy{font-size:.64rem;color:#71819a}
.uh-admin-alert{display:flex;align-items:center;gap:.65rem;padding:.7rem .8rem;border-radius:13px;border:1px solid #fed7aa;background:#fff7ed;color:#9a3412;font-size:.68rem;font-weight:750;margin-bottom:.5rem}.uh-admin-alert strong{display:block;color:#7c2d12;font-size:.78rem}.uh-admin-alert small{display:block;font-size:.6rem;color:#9a3412;font-weight:650}
.uh-admin-activity{display:flex;gap:.65rem;align-items:flex-start;padding:.65rem 0;border-bottom:1px solid #eef2f7}.uh-admin-activity:last-child{border-bottom:0}.uh-admin-activity-icon{width:30px;height:30px;border-radius:10px;background:#edf4ff;display:flex;align-items:center;justify-content:center;flex:none}.uh-admin-activity strong{display:block;font-size:.68rem;color:#10274e}.uh-admin-activity span{display:block;font-size:.58rem;color:#8a99ad;margin-top:.12rem}
.uh-admin-search{padding:.9rem;border-radius:17px;background:linear-gradient(135deg,#0b1f44,#174ea6);box-shadow:0 12px 28px rgba(23,78,166,.14);margin-bottom:.8rem}.uh-admin-search h3{color:#fff!important;font-size:.92rem!important;margin:0 0 .12rem!important}.uh-admin-search p{color:#bfdbfe!important;font-size:.62rem!important;margin:0 0 .6rem!important}
.uh-admin-statline{height:7px;background:#edf2f7;border-radius:99px;overflow:hidden;margin-top:.45rem}.uh-admin-statline i{display:block;height:100%;background:linear-gradient(90deg,#2563eb,#60a5fa);border-radius:99px}
@media(max-width:760px){.uh-admin-shell{padding:.9rem;border-radius:18px}.uh-admin-title{font-size:1.35rem}.uh-admin-metric{min-height:80px;padding:.7rem}.uh-admin-metric .value{font-size:1.15rem}}

.uh-student-shell{display:block}

/* =====================================================================
   UNI HELP — GLASS / GLOSSY STUDENT APP THEME
   Keeps the existing Streamlit widgets and backend intact while making
   the student experience feel like a real mobile-first product.
   ===================================================================== */
.stApp:has(.uh-student-shell){
  --uh-app-bg:#eef5ff;
  --uh-app-bg-2:#f8fbff;
  --uh-ink:#10264a;
  --uh-ink-soft:#526784;
  --uh-border:rgba(255,255,255,.72);
  background:
    radial-gradient(circle at 8% 8%,rgba(37,99,235,.16),transparent 25%),
    radial-gradient(circle at 92% 18%,rgba(34,211,238,.12),transparent 24%),
    radial-gradient(circle at 80% 90%,rgba(96,165,250,.13),transparent 27%),
    linear-gradient(145deg,var(--uh-app-bg-2) 0%,var(--uh-app-bg) 52%,#edf4ff 100%)!important;
  color:var(--uh-ink)!important;
}
.stApp:has(.uh-student-shell) .main{
  background:transparent!important;
}
.stApp:has(.uh-student-shell) .main .block-container{
  position:relative;
  max-width:720px!important;
  padding:18px 20px 105px!important;
  margin:0 auto!important;
}
.stApp:has(.uh-student-shell) .main .block-container:before,
.stApp:has(.uh-student-shell) .main .block-container:after{
  content:"";
  position:fixed;
  width:220px;height:220px;border-radius:50%;
  pointer-events:none;z-index:-1;filter:blur(4px);
  animation:uhAmbient 9s ease-in-out infinite;
}
.stApp:has(.uh-student-shell) .main .block-container:before{
  left:-90px;top:18%;
  background:radial-gradient(circle,rgba(37,99,235,.13),transparent 68%);
}
.stApp:has(.uh-student-shell) .main .block-container:after{
  right:-100px;bottom:12%;
  background:radial-gradient(circle,rgba(34,211,238,.10),transparent 68%);
  animation-delay:-4s;
}

/* Native Streamlit text: force readable contrast regardless of the
   selected Streamlit Cloud theme. */
.stApp:has(.uh-student-shell) .stMarkdown,
.stApp:has(.uh-student-shell) .stMarkdown p,
.stApp:has(.uh-student-shell) label,
.stApp:has(.uh-student-shell) label p,
.stApp:has(.uh-student-shell) [data-testid="stCaptionContainer"]{
  color:var(--uh-ink)!important;
}
.stApp:has(.uh-student-shell) h1,
.stApp:has(.uh-student-shell) h2,
.stApp:has(.uh-student-shell) h3,
.stApp:has(.uh-student-shell) h4{
  color:var(--uh-ink)!important;
}
.stApp:has(.uh-student-shell) [data-testid="stWidgetLabel"] p,
.stApp:has(.uh-student-shell) [data-testid="stWidgetLabel"] label{
  color:#344b6d!important;
  font-weight:800!important;
}

/* Glassy, nearly transparent fields — no ugly dark boxes. */
.stApp:has(.uh-student-shell) div[data-testid="stTextInput"],
.stApp:has(.uh-student-shell) div[data-testid="stTextArea"],
.stApp:has(.uh-student-shell) div[data-testid="stNumberInput"],
.stApp:has(.uh-student-shell) div[data-testid="stSelectbox"],
.stApp:has(.uh-student-shell) div[data-testid="stMultiSelect"],
.stApp:has(.uh-student-shell) div[data-testid="stDateInput"]{
  position:relative;
}
.stApp:has(.uh-student-shell) div[data-testid="stTextInput"] input,
.stApp:has(.uh-student-shell) div[data-testid="stTextArea"] textarea,
.stApp:has(.uh-student-shell) div[data-testid="stNumberInput"] input,
.stApp:has(.uh-student-shell) div[data-testid="stSelectbox"] [data-baseweb="select"] > div,
.stApp:has(.uh-student-shell) div[data-testid="stMultiSelect"] [data-baseweb="select"] > div,
.stApp:has(.uh-student-shell) div[data-testid="stDateInput"] input{
  background:rgba(255,255,255,.48)!important;
  color:#10264a!important;
  -webkit-text-fill-color:#10264a!important;
  border:1px solid rgba(116,145,184,.28)!important;
  border-radius:15px!important;
  box-shadow:
    inset 0 1px 0 rgba(255,255,255,.88),
    inset 0 -1px 0 rgba(148,163,184,.08),
    0 8px 22px rgba(31,73,125,.055)!important;
  backdrop-filter:blur(14px)!important;
  transition:border-color .18s ease,box-shadow .18s ease,transform .18s ease,background .18s ease!important;
}
.stApp:has(.uh-student-shell) div[data-testid="stTextInput"] input,
.stApp:has(.uh-student-shell) div[data-testid="stNumberInput"] input,
.stApp:has(.uh-student-shell) div[data-testid="stDateInput"] input{min-height:43px!important;}
.stApp:has(.uh-student-shell) div[data-testid="stTextArea"] textarea{min-height:105px!important;}
.stApp:has(.uh-student-shell) div[data-testid="stTextInput"] input:focus,
.stApp:has(.uh-student-shell) div[data-testid="stTextArea"] textarea:focus,
.stApp:has(.uh-student-shell) div[data-testid="stNumberInput"] input:focus,
.stApp:has(.uh-student-shell) div[data-testid="stSelectbox"] [data-baseweb="select"] > div:focus-within,
.stApp:has(.uh-student-shell) div[data-testid="stMultiSelect"] [data-baseweb="select"] > div:focus-within,
.stApp:has(.uh-student-shell) div[data-testid="stDateInput"] input:focus{
  background:rgba(255,255,255,.72)!important;
  border-color:rgba(37,99,235,.50)!important;
  box-shadow:
    0 0 0 3px rgba(37,99,235,.09),
    0 12px 28px rgba(37,99,235,.09),
    inset 0 1px 0 rgba(255,255,255,.95)!important;
  transform:translateY(-1px);
  outline:none!important;
}
.stApp:has(.uh-student-shell) input::placeholder,
.stApp:has(.uh-student-shell) textarea::placeholder{
  color:#8192aa!important;
  opacity:1!important;
}

/* Native Streamlit buttons: premium glass with blue primary actions. */
.stApp:has(.uh-student-shell) button{
  color:#17345d!important;
  border:1px solid rgba(107,135,173,.26)!important;
  border-radius:14px!important;
  background:rgba(255,255,255,.52)!important;
  box-shadow:inset 0 1px 0 rgba(255,255,255,.9),0 8px 20px rgba(31,73,125,.06)!important;
  backdrop-filter:blur(12px)!important;
  transition:transform .16s ease,box-shadow .18s ease,border-color .18s ease,background .18s ease!important;
}
.stApp:has(.uh-student-shell) button:hover{
  color:#0b3f91!important;
  background:rgba(255,255,255,.76)!important;
  border-color:rgba(37,99,235,.32)!important;
  box-shadow:inset 0 1px 0 rgba(255,255,255,.96),0 12px 25px rgba(31,73,125,.10)!important;
  transform:translateY(-2px);
}
.stApp:has(.uh-student-shell) button:active{transform:translateY(0) scale(.985)!important;}
.stApp:has(.uh-student-shell) button[kind="primary"]{
  color:#fff!important;
  border:0!important;
  background:linear-gradient(135deg,#123a7a,#2563eb 58%,#4f8cff)!important;
  box-shadow:0 12px 25px rgba(37,99,235,.22),inset 0 1px 0 rgba(255,255,255,.24)!important;
  position:relative;overflow:hidden;
}
.stApp:has(.uh-student-shell) button[kind="primary"]:after{
  content:"";position:absolute;top:-50%;left:-35%;width:28%;height:200%;
  transform:rotate(18deg);
  background:linear-gradient(90deg,transparent,rgba(255,255,255,.28),transparent);
  animation:uhGloss 3.4s linear infinite;
}

/* Make Streamlit expanders, bordered containers and forms feel like
   translucent product surfaces instead of opaque default boxes. */
.stApp:has(.uh-student-shell) [data-testid="stExpander"],
.stApp:has(.uh-student-shell) [data-testid="stVerticalBlockBorderWrapper"],
.stApp:has(.uh-student-shell) [data-testid="stForm"]{
  background:rgba(255,255,255,.34)!important;
  border:1px solid rgba(255,255,255,.72)!important;
  border-radius:20px!important;
  box-shadow:0 12px 32px rgba(31,73,125,.055),inset 0 1px 0 rgba(255,255,255,.86)!important;
  backdrop-filter:blur(14px)!important;
}
.stApp:has(.uh-student-shell) [data-testid="stExpander"] summary,
.stApp:has(.uh-student-shell) [data-testid="stExpander"] summary p{
  color:#10264a!important;
  font-weight:850!important;
}

/* Tables / dataframe surfaces */
.stApp:has(.uh-student-shell) [data-testid="stDataFrame"],
.stApp:has(.uh-student-shell) [data-testid="stTable"]{
  border-radius:16px!important;
  overflow:hidden!important;
  box-shadow:0 10px 28px rgba(31,73,125,.055)!important;
}

/* Tabs are compact glass pills. */
.stApp:has(.uh-student-shell) .stTabs [data-baseweb="tab-list"]{
  background:rgba(255,255,255,.40)!important;
  border:1px solid rgba(255,255,255,.72)!important;
  border-radius:15px!important;
  padding:.25rem!important;
  gap:.18rem!important;
  box-shadow:inset 0 1px 0 rgba(255,255,255,.85),0 7px 18px rgba(31,73,125,.045)!important;
  backdrop-filter:blur(14px)!important;
}
.stApp:has(.uh-student-shell) .stTabs [data-baseweb="tab"]{
  color:#647792!important;
  border-radius:11px!important;
  font-weight:850!important;
}
.stApp:has(.uh-student-shell) .stTabs [aria-selected="true"]{
  color:#123b79!important;
  background:rgba(255,255,255,.76)!important;
  box-shadow:0 5px 14px rgba(31,73,125,.08),inset 0 1px 0 rgba(255,255,255,.9)!important;
}

/* Keep the fixed mobile navigation glossy and readable. */
.stApp:has(.uh-student-shell) .st-key-uh-bottom-nav{
  background:rgba(255,255,255,.62)!important;
  border:1px solid rgba(255,255,255,.86)!important;
  box-shadow:0 16px 38px rgba(31,73,125,.16),inset 0 1px 0 rgba(255,255,255,.95)!important;
  backdrop-filter:blur(22px) saturate(145%)!important;
}
.stApp:has(.uh-student-shell) .st-key-uh-bottom-nav button{
  color:#647792!important;
  background:transparent!important;
  border:0!important;
  box-shadow:none!important;
}
.stApp:has(.uh-student-shell) .st-key-uh-bottom-nav .uh-nav-active button{color:#1554ad!important;}

@keyframes uhGloss{0%{left:-35%}55%,100%{left:125%}}
@keyframes uhAmbient{0%,100%{transform:translate3d(0,0,0) scale(1)}50%{transform:translate3d(10px,-14px,0) scale(1.04)}}

@media(max-width:640px){
  .stApp:has(.uh-student-shell) .main .block-container{max-width:100%!important;padding:12px 12px 100px!important;}
  .stApp:has(.uh-student-shell) div[data-testid="stTextInput"] input,
  .stApp:has(.uh-student-shell) div[data-testid="stNumberInput"] input,
  .stApp:has(.uh-student-shell) div[data-testid="stSelectbox"] [data-baseweb="select"] > div,
  .stApp:has(.uh-student-shell) div[data-testid="stMultiSelect"] [data-baseweb="select"] > div,
  .stApp:has(.uh-student-shell) div[data-testid="stDateInput"] input{min-height:45px!important;}
  .stApp:has(.uh-student-shell) button{min-height:44px!important;}
}

/* Mobile-first student app shell */
.stApp:has(.uh-student-shell){background:#f7f7f5!important}
.stApp:has(.uh-student-shell) [data-testid="stSidebar"]{display:none!important}
.stApp:has(.uh-student-shell) .main .block-container{max-width:620px!important;padding:0 18px 96px!important;margin:0 auto!important}
.stApp:has(.uh-student-shell) [data-testid="stHeader"]{background:transparent!important}
.stApp:has(.uh-student-shell) .uh-app-header{display:flex;align-items:center;justify-content:space-between;padding:.85rem .05rem .5rem;position:sticky;top:0;z-index:20;background:rgba(247,247,245,.94);backdrop-filter:blur(14px)}
.uh-app-header-brand{font-size:.7rem;font-weight:900;letter-spacing:.12em;color:#8b4a12;text-transform:uppercase}.uh-app-header-title{font-size:1.05rem;font-weight:900;color:#141414;letter-spacing:-.03em}.uh-app-header-user{font-size:.68rem;color:#737373;font-weight:700}
.uh-header-icon button{width:42px!important;height:42px!important;min-height:42px!important;border-radius:50%!important;background:#fff!important;border:1px solid #e7e4df!important;box-shadow:0 4px 14px rgba(0,0,0,.06)!important;font-size:1.05rem!important;padding:0!important}
.uh-home-greeting{margin:.35rem 0 .85rem}.uh-home-greeting .eyebrow{font-size:.64rem;letter-spacing:.13em;color:#9a5317;font-weight:900;text-transform:uppercase}.uh-home-greeting h1{font-size:2rem!important;line-height:1.04!important;letter-spacing:-.055em!important;color:#171717!important;margin:.2rem 0 0!important}.uh-home-greeting p{font-size:.78rem;color:#7b7b7b;margin:.35rem 0 0}
.uh-coins-card{position:relative;overflow:hidden;border-radius:25px;padding:1.05rem 1.15rem 1.15rem;background:linear-gradient(135deg,#a94e05 0%,#c46109 52%,#e18a1a 100%);box-shadow:0 16px 32px rgba(163,76,5,.18);color:#fff;margin:.35rem 0 1.15rem}.uh-coins-card:after{content:"";position:absolute;width:170px;height:170px;border-radius:50%;right:-65px;top:-85px;background:rgba(255,255,255,.1)}.uh-coins-top{display:flex;justify-content:space-between;align-items:flex-start;position:relative;z-index:1}.uh-coins-label{font-size:.62rem;letter-spacing:.16em;font-weight:900;color:#ffe7a3}.uh-coins-value{font-size:2.6rem;line-height:1;font-weight:950;letter-spacing:-.06em;margin-top:.25rem}.uh-coins-value span{font-size:1rem;font-weight:700;opacity:.82;letter-spacing:-.02em}.uh-coins-badge{width:52px;height:52px;border-radius:17px;background:#fff;display:flex;align-items:center;justify-content:center;color:#c15b0a;font-size:1.45rem;box-shadow:0 7px 18px rgba(0,0,0,.12)}.uh-coins-bar{height:8px;border-radius:99px;background:rgba(255,255,255,.22);overflow:hidden;margin:1rem 0 .55rem;position:relative;z-index:1}.uh-coins-bar i{display:block;height:100%;border-radius:99px;background:#ffe27c}.uh-coins-foot{font-size:.7rem;color:#fff2d1;position:relative;z-index:1}
.uh-impact-title{font-size:1.3rem!important;font-weight:900!important;letter-spacing:-.04em!important;color:#161616!important;margin:.25rem 0 .65rem!important}.uh-impact-grid{display:grid;gap:.55rem}.uh-impact-card{display:flex;align-items:center;gap:.75rem;background:#fff;border:1px solid #e8e5e0;border-radius:20px;padding:.72rem .82rem;box-shadow:0 5px 14px rgba(24,24,24,.045);transition:transform .18s ease,box-shadow .18s ease}.uh-impact-card:hover{transform:translateY(-2px);box-shadow:0 10px 22px rgba(24,24,24,.08)}.uh-impact-icon{width:48px;height:48px;border-radius:16px;display:flex;align-items:center;justify-content:center;font-size:1.35rem;flex:none}.uh-impact-icon.blue{background:#e9f6fb;color:#1489a7}.uh-impact-icon.orange{background:#fff2e7;color:#b86417}.uh-impact-icon.yellow{background:#fff6cf;color:#c18a0b}.uh-impact-icon.red{background:#fde9e8;color:#c43e36}.uh-impact-copy{flex:1}.uh-impact-copy strong{display:block;font-size:.9rem;color:#171717}.uh-impact-copy span{display:block;font-size:.64rem;color:#8a8a8a;margin-top:.08rem}.uh-chevron{font-size:1.35rem;color:#777}
.uh-nearby-head{display:flex;justify-content:space-between;align-items:center;margin:1.25rem 0 .55rem}.uh-nearby-head h2{font-size:1.25rem!important;color:#171717!important;margin:0!important;letter-spacing:-.04em}.uh-seeall button{border:0!important;background:transparent!important;color:#a45612!important;font-weight:850!important;font-size:.72rem!important;min-height:30px!important;box-shadow:none!important;padding:0!important}.uh-request-card{background:#fff;border:1px solid #e9e6e1;border-radius:20px;padding:.88rem;margin:.55rem 0;box-shadow:0 5px 14px rgba(24,24,24,.045)}.uh-request-card.urgent{background:#fff9f8;border-color:#f2d0cb}.uh-request-top{display:flex;align-items:center;gap:.65rem}.uh-request-avatar{width:40px;height:40px;border-radius:13px;background:#eaf5fa;color:#1387a5;display:flex;align-items:center;justify-content:center;font-weight:900}.uh-request-card.urgent .uh-request-avatar{background:#fde6e4;color:#c43e36}.uh-request-main{flex:1}.uh-request-main strong{font-size:.84rem;color:#191919}.uh-request-main span{display:block;font-size:.65rem;color:#8a8a8a;margin-top:.08rem}.uh-request-status{font-size:.57rem;font-weight:900;color:#b33d37;letter-spacing:.06em}.uh-request-meta{display:flex;align-items:center;gap:.4rem;color:#8a8a8a;font-size:.66rem;margin-top:.62rem}.uh-request-action button{background:#bd5b08!important;color:#fff!important;border:0!important;border-radius:12px!important;min-height:36px!important;box-shadow:0 6px 14px rgba(189,91,8,.18)!important;font-size:.68rem!important;font-weight:900!important}
.uh-refresh{text-align:center;margin:.75rem 0;color:#ad5a15;font-size:.72rem;font-weight:800}.uh-refresh button{border:0!important;background:transparent!important;color:#ad5a15!important;box-shadow:none!important;font-weight:800!important}
.stApp:has(.uh-student-shell) .st-key-uh-bottom-nav{position:fixed;left:50%;bottom:10px;transform:translateX(-50%);width:min(590px,calc(100% - 22px));z-index:100;background:rgba(255,255,255,.96);border:1px solid #e6e2dc;border-radius:24px;box-shadow:0 12px 32px rgba(0,0,0,.13);padding:.45rem .45rem .38rem;backdrop-filter:blur(16px)}
.stApp:has(.uh-student-shell) .st-key-uh-bottom-nav [data-testid="column"]{display:flex;align-items:center;justify-content:center}.stApp:has(.uh-student-shell) .st-key-uh-bottom-nav button{border:0!important;background:transparent!important;color:#777!important;box-shadow:none!important;min-height:45px!important;padding:.2rem .1rem!important;font-size:.68rem!important;font-weight:800!important}.stApp:has(.uh-student-shell) .st-key-uh-bottom-nav button:hover{color:#a9530c!important;transform:none!important}.stApp:has(.uh-student-shell) .st-key-uh-bottom-nav .uh-nav-active button{color:#a9530c!important}.stApp:has(.uh-student-shell) .st-key-uh-bottom-nav .uh-nav-plus button{width:56px!important;height:56px!important;min-height:56px!important;border-radius:19px!important;background:#bd5b08!important;color:#fff!important;font-size:1.6rem!important;box-shadow:0 9px 18px rgba(189,91,8,.25)!important;margin-top:-20px!important}.stApp:has(.uh-student-shell) .st-key-uh-bottom-nav [data-testid="stHorizontalBlock"]{gap:.1rem}
.uh-page-card{background:#fff;border:1px solid #e9e6e1;border-radius:22px;padding:.95rem;box-shadow:0 6px 18px rgba(24,24,24,.05)}.stApp:has(.uh-student-shell) .st-key-impact_borrow button,.stApp:has(.uh-student-shell) .st-key-impact_lend button,.stApp:has(.uh-student-shell) .st-key-impact_tasks button,.stApp:has(.uh-student-shell) .st-key-impact_help button{height:64px!important;min-height:64px!important;background:#fff!important;color:#171717!important;border:1px solid #e8e5e0!important;border-radius:20px!important;box-shadow:0 5px 14px rgba(24,24,24,.045)!important;text-align:left!important;padding:0 1rem!important;font-size:.88rem!important;font-weight:850!important;transition:transform .18s ease,box-shadow .18s ease!important}.stApp:has(.uh-student-shell) .st-key-impact_borrow button:hover,.stApp:has(.uh-student-shell) .st-key-impact_lend button:hover,.stApp:has(.uh-student-shell) .st-key-impact_tasks button:hover,.stApp:has(.uh-student-shell) .st-key-impact_help button:hover{transform:translateY(-2px)!important;box-shadow:0 10px 22px rgba(24,24,24,.08)!important}
.stApp:has(.uh-student-shell) .stTabs [data-baseweb="tab-list"]{background:#efede9;border-radius:13px;padding:.2rem;gap:.15rem}.stApp:has(.uh-student-shell) .stTabs [data-baseweb="tab"]{height:34px;border-radius:10px;color:#777;font-size:.65rem;font-weight:800}.stApp:has(.uh-student-shell) .stTabs [aria-selected="true"]{background:#fff;color:#a6530c;box-shadow:0 3px 9px rgba(0,0,0,.06)}
@media(max-width:640px){.stApp:has(.uh-student-shell) .main .block-container{padding-left:12px!important;padding-right:12px!important;padding-bottom:95px!important}.uh-home-greeting h1{font-size:1.72rem!important}.uh-coins-card{border-radius:22px;padding:.95rem}.uh-coins-value{font-size:2.35rem}.uh-impact-card{border-radius:18px}.uh-impact-icon{width:44px;height:44px}.stApp:has(.uh-student-shell) .st-key-uh-bottom-nav{bottom:7px;width:calc(100% - 14px);border-radius:22px}}

/* Premium mobile-first product UI overrides */
.stApp:has(.uh-student-shell){background:radial-gradient(circle at 0% 0%,rgba(225,236,255,.9),transparent 28%),radial-gradient(circle at 100% 20%,rgba(255,239,219,.72),transparent 30%),#f7f8fb!important;color:#14233f!important}
.stApp:has(.uh-student-shell) .main .block-container{max-width:760px!important;padding-top:.55rem!important;padding-bottom:105px!important}
.stApp:has(.uh-student-shell) [data-testid="stMarkdownContainer"]{color:#17243b}
.stApp:has(.uh-student-shell) [data-testid="stTextInput"] label,.stApp:has(.uh-student-shell) [data-testid="stTextArea"] label,.stApp:has(.uh-student-shell) [data-testid="stSelectbox"] label,.stApp:has(.uh-student-shell) [data-testid="stNumberInput"] label{color:#33476a!important;font-weight:800!important}
.stApp:has(.uh-student-shell) input,.stApp:has(.uh-student-shell) textarea,.stApp:has(.uh-student-shell) [data-baseweb="select"]>div{background:#fff!important;color:#16233b!important;border:1px solid #dbe3ee!important;border-radius:14px!important;box-shadow:inset 0 1px 0 rgba(255,255,255,.9),0 5px 16px rgba(28,52,88,.045)!important}
.stApp:has(.uh-student-shell) input::placeholder,.stApp:has(.uh-student-shell) textarea::placeholder{color:#9aa7b8!important}
.stApp:has(.uh-student-shell) button{border-radius:14px!important;border:1px solid #dbe3ee!important;background:rgba(255,255,255,.9)!important;color:#203452!important;box-shadow:0 5px 15px rgba(31,54,90,.055)!important;font-weight:800!important;transition:all .2s ease!important}
.stApp:has(.uh-student-shell) button:hover{transform:translateY(-2px)!important;box-shadow:0 10px 24px rgba(31,54,90,.10)!important}
.stApp:has(.uh-student-shell) .stButton button[kind="primary"]{background:linear-gradient(135deg,#102a56,#1d5dcc)!important;color:white!important;border:0!important}
.uh-mobile-header{display:flex;align-items:center;justify-content:space-between;padding:.35rem .1rem .2rem;margin-bottom:.2rem}
.uh-mobile-brand{display:flex;align-items:center;gap:.55rem}.uh-mobile-brand>span{width:39px;height:39px;border-radius:13px;display:flex;align-items:center;justify-content:center;background:linear-gradient(145deg,#fff,#eaf2ff);box-shadow:0 8px 20px rgba(18,53,101,.09);font-size:1.15rem}.uh-mobile-brand strong{display:block;color:#9b5215;font-size:.67rem;letter-spacing:.12em}.uh-mobile-brand small{display:block;color:#1c2e4c;font-size:.92rem;font-weight:900;margin-top:.08rem}.uh-mobile-greeting{font-size:.68rem;color:#748196;font-weight:750}
.uh-page-kicker{font-size:.62rem;letter-spacing:.15em;color:#a45a19;font-weight:900;margin:.8rem 0 .15rem;text-transform:uppercase}
.uh-choice-card,.uh-help-card{display:flex;align-items:center;gap:.8rem;padding:.8rem;margin:.45rem 0 .25rem;background:linear-gradient(145deg,rgba(255,255,255,.97),rgba(248,251,255,.94));border:1px solid rgba(214,224,237,.9);border-radius:20px;box-shadow:0 9px 25px rgba(22,48,86,.065),inset 0 1px 0 #fff;position:relative;overflow:hidden}
.uh-choice-card:after,.uh-help-card:after{content:"";position:absolute;left:-20%;right:-20%;top:-80%;height:100%;background:linear-gradient(105deg,transparent 30%,rgba(255,255,255,.55) 50%,transparent 70%);transform:rotate(4deg);animation:uhGloss 5s ease-in-out infinite;pointer-events:none}
.uh-choice-icon{width:48px;height:48px;border-radius:16px;background:linear-gradient(145deg,#edf6ff,#e3eefb);display:flex;align-items:center;justify-content:center;font-size:1.35rem;flex:none}.uh-choice-copy{flex:1}.uh-choice-copy strong{display:block;color:#172640;font-size:.92rem}.uh-choice-copy span{display:block;color:#7b899d;font-size:.68rem;margin-top:.12rem}
.uh-choice-card + button,.uh-help-card + button{margin-bottom:.65rem}
.uh-help-note{margin-top:1rem;padding:.8rem;border-radius:17px;background:#fff7ed;border:1px solid #fed7aa;color:#8a4b14}.uh-help-note strong{display:block;font-size:.76rem}.uh-help-note span{display:block;font-size:.67rem;margin-top:.2rem;line-height:1.45}
@keyframes uhGloss{0%,55%{left:-80%;opacity:0}65%{opacity:1}85%,100%{left:120%;opacity:0}}
@media(max-width:640px){.stApp:has(.uh-student-shell) .main .block-container{padding-left:12px!important;padding-right:12px!important}.uh-mobile-brand small{font-size:.84rem}.uh-home-greeting h1{font-size:1.68rem!important}.uh-coins-card{box-shadow:0 13px 28px rgba(163,76,5,.16)!important}}
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
        '<div class="uh-auth-hero">'
        '<div class="uh-auth-kicker"><span class="uh-auth-kicker-dot"></span>Student-powered campus network</div>'
        '<div class="uh-auth-logo-wrap"><div class="uh-auth-logo-ring"></div><div class="uh-auth-logo-mark">🎓</div></div>'
        '<h1>UNI HELP</h1>'
        '<p class="uh-auth-tagline">Your campus. Your community.<br>Someone can help.</p>'
        '<p class="uh-auth-subtitle">Borrow • Deliver • Assist • Earn</p>'
        '</div>',
        unsafe_allow_html=True,
    )


def _set_user_and_route(user):
    st.session_state["user"] = user
    st.session_state["nav"] = "Admin" if user.get("role") == "admin" else "Dashboard"
    st.rerun()


def _send_student_login_email_otp(student_id, password):
    """Validate existing credentials, then send a real email OTP to the registered email."""
    if not EMAIL_CONFIGURED:
        return False, "Email verification is not configured. Please contact the administrator."
    ok, result = authenticate_student_credentials(student_id, password)
    if not ok:
        return False, result
    email = (result.get("email") or "").strip()
    if not email or not is_valid_email(email):
        return False, "No valid email address is registered for this Student ID."
    if not deliver_otp(result, "LOGIN_EMAIL_OTP", None, "login verification"):
        return False, "Unable to send OTP. Please try again."
    st.session_state["login_student_id"] = student_id.strip()
    st.session_state["login_otp_resend_at"] = time.time() + SMS_RESEND_SECONDS
    return True, "OTP sent successfully to your registered email."


def _verify_student_login_email(student_id, password, otp):
    """Revalidate credentials and verify the email OTP stored by the existing OTP system."""
    ok, result = authenticate_student_credentials(student_id, password)
    if not ok:
        return False, result
    ok, msg = verify_otp(result["id"], "LOGIN_EMAIL_OTP", None, otp)
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
    # Registration now uses email verification only.  Keep unrelated
    # authentication/session state untouched.
    for key in ("pending_registration_user_id", "registration_email_verified", "registration_email_resend_at", "registration_phone_otp_sent", "registration_sms_resend_at", "registration_phone_verified"):
        st.session_state.pop(key, None)


def _auth_tabs(active):
    st.markdown('<div class="uh-auth-tabs">', unsafe_allow_html=True)
    a, b = st.columns(2, gap="small")
    with a:
        if active == "login":
            st.markdown('<div class="uh-auth-tab-button uh-auth-tab-button-active"><button disabled>LOGIN</button></div>', unsafe_allow_html=True)
        elif st.button("LOGIN", use_container_width=True, key="tab_login"):
            _clear_registration_state()
            st.session_state["auth_mode"] = "Home"
            st.rerun()
    with b:
        if active == "register":
            st.markdown('<div class="uh-auth-tab-button uh-auth-tab-button-active"><button disabled>CREATE ACCOUNT</button></div>', unsafe_allow_html=True)
        elif st.button("CREATE ACCOUNT", use_container_width=True, key="tab_register"):
            st.session_state["auth_mode"] = "Register"
            st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)


def render_landing():
    _render_auth_shell_start()
    _render_auth_hero()
    with st.container(border=True):
        _auth_tabs("login")
        st.markdown('<div class="uh-auth-card-title">Welcome back</div><div class="uh-auth-card-copy">Sign in with your Student ID, password and registered email.</div>', unsafe_allow_html=True)
        sid = st.text_input("Student ID", placeholder="126xxxxx", max_chars=8, key="auth_student_id")
        pw = st.text_input("Password", type="password", placeholder="Enter your password", key="auth_student_password")
        if st.button("Send OTP", use_container_width=True, type="primary", key="auth_send_email"):
            with st.spinner("Sending secure OTP…"):
                ok, msg = _send_student_login_email_otp(sid, pw)
            if ok:
                st.success(msg)
                st.rerun()
            else:
                st.error(msg)
        if st.session_state.get("login_student_id"):
            st.text_input("Email OTP", max_chars=6, placeholder="Enter 6-digit OTP", key="auth_login_otp")
            remaining = max(0, int(st.session_state.get("login_otp_resend_at", 0) - time.time()))
            if st.button("Verify & Login", use_container_width=True, type="primary", key="auth_verify_email"):
                with st.spinner("Verifying OTP…"):
                    ok, result = _verify_student_login_email(sid or st.session_state.get("login_student_id", ""), pw, st.session_state.get("auth_login_otp", ""))
                if ok:
                    st.success("Login successful. Welcome back!")
                    st.session_state.pop("login_student_id", None)
                    st.session_state.pop("login_otp_resend_at", None)
                    _set_user_and_route(result)
                else:
                    st.error("OTP expired. Please request a new one." if "expired" in str(result).lower() else "Invalid OTP. Please try again.")
            if st.button("Resend OTP", disabled=remaining > 0, use_container_width=True, key="auth_resend_email"):
                with st.spinner("Sending secure OTP…"):
                    ok, msg = _send_student_login_email_otp(sid or st.session_state.get("login_student_id", ""), pw)
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
        st.markdown('<div class="uh-auth-mini-row"><span>Protected by secure account verification</span><b>•</b><span>UNI HELP</span></div>', unsafe_allow_html=True)
        if not EMAIL_CONFIGURED:
            missing = "email"
            st.markdown(f'<div class="uh-auth-demo">Configuration notice • {missing} service is not configured</div>', unsafe_allow_html=True)
    _render_auth_shell_end()


def render_register():
    _render_auth_shell_start()
    _render_auth_hero()
    with st.container(border=True):
        _auth_tabs("register")
        st.markdown(
            '<div class="uh-auth-card-title">Create your account</div>'
            '<div class="uh-auth-card-copy">Use any valid email address. Your phone number is kept for UNI HELP services, not OTP verification.</div>',
            unsafe_allow_html=True,
        )
        pending_id = st.session_state.get("pending_registration_user_id")
        email_verified = bool(st.session_state.get("registration_email_verified"))
        user = user_by_id(pending_id) if pending_id else None

        if not user or not pending_id:
            with st.form("register_form"):
                full_name = st.text_input("Full Name", placeholder="Your full name")
                email = st.text_input("Email Address", placeholder="you@example.com")
                student_id = st.text_input("Student ID", placeholder="126xxxxx", max_chars=8)
                phone = st.text_input("Phone Number", placeholder="9876543210", max_chars=10)
                password = st.text_input("Password", type="password", placeholder="At least 6 characters")
                password2 = st.text_input("Confirm Password", type="password", placeholder="Repeat your password")
                submitted = st.form_submit_button("Create Account & Send Email OTP", use_container_width=True, type="primary")
            if submitted:
                if password != password2:
                    st.error("Passwords do not match.")
                elif not is_valid_email(email):
                    st.error("Please enter a valid email address.")
                elif not is_valid_student_id(student_id):
                    st.error("Student ID must be exactly 8 digits and start with 126.")
                elif not is_valid_phone(phone):
                    st.error("Please enter a valid 10-digit mobile number.")
                elif not EMAIL_CONFIGURED:
                    st.error("Email verification is not configured. Please contact the administrator.")
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
            st.markdown(
                f'<div class="uh-auth-status ok">Verification started for <strong>{user["email"]}</strong></div>',
                unsafe_allow_html=True,
            )

            if not email_verified:
                st.text_input(
                    "Email OTP",
                    max_chars=6,
                    placeholder="Enter 6-digit email OTP",
                    key="reg_email_otp",
                )
                if st.button("Verify Email", use_container_width=True, type="primary", key="reg_verify_email"):
                    with st.spinner("Verifying email…"):
                        ok, msg = verify_otp(
                            user["id"],
                            "EMAIL_VERIFICATION",
                            None,
                            st.session_state.get("reg_email_otp", ""),
                        )
                    if ok:
                        st.session_state["registration_email_verified"] = True
                        st.success("Email verified successfully.")
                        st.rerun()
                    else:
                        st.error(
                            "OTP expired. Please request a new one."
                            if "expired" in msg.lower()
                            else "Invalid email OTP. Please try again."
                        )

                remaining = max(
                    0,
                    int(st.session_state.get("registration_email_resend_at", 0) - time.time()),
                )
                if st.button(
                    "Resend Email OTP",
                    disabled=remaining > 0,
                    use_container_width=True,
                    key="reg_resend_email",
                ):
                    with st.spinner("Sending email OTP…"):
                        ok, msg = _send_registration_email_otp(user["id"])
                    if ok:
                        st.success(msg)
                        st.rerun()
                    else:
                        st.error(msg)
                if remaining:
                    st.markdown(
                        f'<div class="uh-otp-note">Resend available in {remaining}s</div>',
                        unsafe_allow_html=True,
                    )
            else:
                st.markdown(
                    '<div class="uh-auth-status ok">✓ Email verified</div>',
                    unsafe_allow_html=True,
                )
                st.markdown(
                    '<div class="uh-auth-status info">Your email is verified. You can now create your UNI HELP account.</div>',
                    unsafe_allow_html=True,
                )
                if st.button(
                    "Create Account",
                    use_container_width=True,
                    type="primary",
                    key="reg_finish",
                ):
                    with st.spinner("Creating your UNI HELP account…"):
                        conn = get_conn()
                        conn.execute("UPDATE users SET verified=1 WHERE id=?", (user["id"],))
                        conn.commit()
                        conn.close()
                    notify(user["id"], "Welcome to UNI HELP! Your email has been verified.")
                    _clear_registration_state()
                    st.session_state["auth_mode"] = "Home"
                    st.success("Account created successfully. Please log in.")
                    st.rerun()

        st.markdown(
            '<div class="uh-auth-divider"><span>Already have an account?</span></div>',
            unsafe_allow_html=True,
        )
        if st.button("← Back to Login", use_container_width=True, key="register_back"):
            _clear_registration_state()
            st.session_state["auth_mode"] = "Home"
            st.rerun()
        st.markdown(
            '<div class="uh-auth-mini-row"><span>Your information stays inside UNI HELP</span></div>',
            unsafe_allow_html=True,
        )
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


# 6.2.1 CREATE / HELP HUBS
# -----------------------------------------------------------------------------

def render_create_hub(user):
    st.markdown("<div class='uh-page-kicker'>CREATE</div>", unsafe_allow_html=True)
    st.markdown("# What do you want to do? ✨")
    st.caption("Choose an action. Each option opens its own UNI HELP workflow.")
    actions = [
        ("📦", "Delivery", "Create or manage campus deliveries.", "Delivery", "create_delivery_hub"),
        ("🤝", "Borrow / Lend", "Find an item or share something with another student.", "Borrowing", "create_borrow_hub"),
        ("⚡", "Micro-Task", "Post a small task or help someone nearby.", "Micro-Tasks", "create_task_hub"),
        ("🙋", "Need Help", "Tell your campus community what you need.", "Help", "create_help_hub"),
    ]
    for icon, title, copy, target, key in actions:
        st.markdown(f"<div class='uh-choice-card'><div class='uh-choice-icon'>{icon}</div><div class='uh-choice-copy'><strong>{title}</strong><span>{copy}</span></div></div>", unsafe_allow_html=True)
        if st.button(f"Open {title}  →", key=key, use_container_width=True):
            st.session_state['nav'] = target
            st.rerun()


def render_help_hub(user):
    st.markdown("<div class='uh-page-kicker'>CAMPUS SUPPORT</div>", unsafe_allow_html=True)
    st.markdown("# Need Help? 🙋")
    st.caption("Start with the type of help you need. UNI HELP will take you to the correct workflow.")

    choices = [
        ("📦", "Delivery help", "Need someone to pick up and deliver something?", "Delivery", "help_delivery"),
        ("🤝", "Borrow something", "Need an item from another student?", "Borrowing", "help_borrow"),
        ("⚡", "Small campus task", "Need someone to complete a quick task?", "Micro-Tasks", "help_task"),
    ]
    for icon, title, copy, target, key in choices:
        st.markdown(f"<div class='uh-help-card'><div class='uh-choice-icon'>{icon}</div><div class='uh-choice-copy'><strong>{title}</strong><span>{copy}</span></div></div>", unsafe_allow_html=True)
        if st.button(f"{title}  →", key=key, use_container_width=True):
            st.session_state['nav'] = target
            st.rerun()

    st.markdown("<div class='uh-help-note'><strong>Not sure which one?</strong><span>Choose Delivery for moving an item, Borrowing for an item you need to use, or Micro-Task for a small action you need another student to complete.</span></div>", unsafe_allow_html=True)

# 6.2 DASHBOARD
# -----------------------------------------------------------------------------

def render_dashboard(user):
    """Mobile-first student home. Uses existing SQLite data and existing module routes."""
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
    active_requests = conn.execute(
        "SELECT COUNT(*) c FROM requests WHERE requester_id=? AND status NOT IN ('COMPLETED','CANCELLED')",
        (user["id"],),
    ).fetchone()["c"]
    nearby = conn.execute(
        """SELECT r.*, u.full_name requester_name FROM requests r
           JOIN users u ON u.id=r.requester_id
           WHERE r.requester_id != ? AND r.status='CREATED'
           ORDER BY r.id DESC LIMIT 3""", (user["id"],)
    ).fetchall()
    nearby_borrow = conn.execute(
        """SELECT i.*, u.full_name owner_name FROM items i
           JOIN users u ON u.id=i.owner_id
           WHERE i.owner_id != ? AND i.status='AVAILABLE'
           ORDER BY i.id DESC LIMIT 3""", (user["id"],)
    ).fetchall()
    unread = conn.execute("SELECT COUNT(*) c FROM notifications WHERE user_id=? AND is_read=0", (user["id"],)).fetchone()["c"]
    conn.close()

    safe_name = str(user['full_name']).replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
    coins = int(user.get('unicoins') or 0)
    coin_progress = min(100, int((coins / 500) * 100))

    st.markdown(f"""<div class='uh-home-greeting'>
        <div class='eyebrow'>UNI HELP • CAMPUS MARKETPLACE</div>
        <h1>Welcome back, {safe_name} 👋</h1>
        <p>Small actions. Real campus impact.</p>
    </div>""", unsafe_allow_html=True)

    st.markdown(f"""<div class='uh-coins-card'>
        <div class='uh-coins-top'><div><div class='uh-coins-label'>UNI COINS</div>
        <div class='uh-coins-value'>{coins} <span>/ 500</span></div></div>
        <div class='uh-coins-badge'>✦</div></div>
        <div class='uh-coins-bar'><i style='width:{coin_progress}%'></i></div>
        <div class='uh-coins-foot'>Keep helping to build your campus reputation.</div>
    </div>""", unsafe_allow_html=True)

    st.markdown("<div class='uh-impact-title'>Make an impact</div>", unsafe_allow_html=True)
    impact = [
        ("🔎", "blue", "Find to borrow", "Discover useful items from students", "Borrowing", "impact_borrow"),
        ("📦", "orange", "Lend an item", "Share something another student needs", "Borrowing", "impact_lend"),
        ("☷", "yellow", "Find tasks", "Earn UniCoins by helping around campus", "Micro-Tasks", "impact_tasks"),
        ("!", "red", "Need help", "Tell the campus what you need", "Help", "impact_help"),
    ]
    for icon, tone, title, copy, nav, key in impact:
        if st.button(f"{icon}   {title}   ›", key=key, use_container_width=True):
            st.session_state['nav'] = nav
            st.rerun()

    st.markdown("<div class='uh-nearby-head'><h2>Nearby requests</h2></div>", unsafe_allow_html=True)
    if not nearby:
        st.markdown("<div class='uh-page-card'><strong>No nearby requests yet</strong><div style='font-size:.7rem;color:#888;margin-top:.25rem'>New campus requests will appear here.</div></div>", unsafe_allow_html=True)
    else:
        for r in nearby:
            name = str(r['requester_name'] or 'Student')
            initial = name[0].upper()
            urgent = 'urgent' in str(r['item_name'] or '').lower() or 'urgent' in str(r['notes'] or '').lower()
            location = r['pickup_location'] or 'Campus'
            st.markdown(f"""<div class='uh-request-card {'urgent' if urgent else ''}'>
                <div class='uh-request-top'><div class='uh-request-avatar'>{initial}</div><div class='uh-request-main'>
                <strong>{str(r['item_name']).replace('<','&lt;').replace('>','&gt;')}</strong><span>{name} • {str(location).replace('<','&lt;').replace('>','&gt;')}</span></div>
                <div class='uh-request-status'>{'URGENT' if urgent else 'OPEN'}</div></div>
                <div class='uh-request-meta'>⌖ Reward ₹{float(r['reward']):.0f} · {str(r['destination'] or 'Campus')}</div>
            </div>""", unsafe_allow_html=True)

    if nearby_borrow:
        st.markdown("<div class='uh-nearby-head' style='margin-top:1.1rem'><h2>Available to borrow</h2></div>", unsafe_allow_html=True)
        for it in nearby_borrow:
            item_name = str(it['item_name'] or 'Item').replace('<','&lt;').replace('>','&gt;')
            owner_name = str(it['owner_name'] or 'Student').replace('<','&lt;').replace('>','&gt;')
            initial = owner_name[0].upper() if owner_name else 'S'
            st.markdown(f"""<div class='uh-request-card'>
                <div class='uh-request-top'><div class='uh-request-avatar'>{initial}</div><div class='uh-request-main'>
                <strong>🤝 {item_name}</strong><span>{owner_name} • {str(it['category'] or 'Other')}</span></div>
                <div class='uh-request-status' style='color:#a45612'>AVAILABLE</div></div>
                <div class='uh-request-meta'>💰 Deposit ₹{float(it['deposit']):.0f} · {str(it['condition'] or 'Condition not specified')}</div>
            </div>""", unsafe_allow_html=True)
            if st.button("🤝 Request to Borrow", key=f"home_borrow_{it['id']}", use_container_width=True):
                st.session_state['nav'] = 'Borrowing'
                st.session_state['borrow_focus_item'] = int(it['id'])
                st.rerun()

    st.markdown("<div class='uh-refresh'>↻ &nbsp;Live campus requests</div>", unsafe_allow_html=True)

    if not user["verified"]:
        st.markdown("<div class='uh-soft-alert'>📧 <strong>Email verification pending.</strong> Verify your email to unlock all campus features.</div>", unsafe_allow_html=True)


def render_leaders(user):
    """Lightweight leaderboard using existing users/trust/UniCoins; no new database tables."""
    conn = get_conn()
    leaders = conn.execute(
        """SELECT full_name, student_id, trust_score, rating_sum, rating_count, unicoins
           FROM users WHERE role='student' AND is_suspended=0
           ORDER BY trust_score DESC, unicoins DESC LIMIT 10"""
    ).fetchall()
    conn.close()
    st.markdown("<div class='uh-home-greeting'><div class='eyebrow'>CAMPUS COMMUNITY</div><h1>Leaders 🏆</h1><p>Students making a difference through verified help.</p></div>", unsafe_allow_html=True)
    for i, row in enumerate(leaders, 1):
        avg = round(row['rating_sum'] / row['rating_count'], 1) if row['rating_count'] else 0
        name = str(row['full_name'] or 'Student')
        st.markdown(f"""<div class='uh-request-card'><div class='uh-request-top'><div class='uh-request-avatar'>{i}</div>
        <div class='uh-request-main'><strong>{name}</strong><span>Trust {row['trust_score']}/100 · ⭐ {avg if avg else 'New'}</span></div>
        <div class='uh-request-status' style='color:#a45612'>🪙 {row['unicoins']}</div></div></div>""", unsafe_allow_html=True)


# -----------------------------------------------------------------------------
# 6.3 DELIVERY MODULE
# -----------------------------------------------------------------------------

def render_delivery(user):
    st.markdown("## 📦 Delivery")
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
    st.markdown("## 🤝 Borrowing")
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
    st.markdown("## 🛠 Micro-Tasks")
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
        # A helper may work on only ONE micro-task at a time. Once a task is
        # accepted, do not expose any other tasks until that task is completed.
        conn = get_conn()
        active_task = conn.execute(
            "SELECT t.*, u.full_name creator_name FROM tasks t JOIN users u ON u.id=t.creator_id "
            "WHERE t.helper_id=? AND t.status IN ('ACCEPTED','IN_PROGRESS') "
            "ORDER BY t.accepted_at DESC, t.id DESC LIMIT 1",
            (user["id"],),
        ).fetchone()
        conn.close()

        if active_task:
            st.markdown("### 🔒 Your current task")
            st.info("You already accepted a task. Finish it before accepting another one.")
            with st.container(border=True):
                st.markdown(
                    f"**{active_task['title']}** ({active_task['category']}) — "
                    f"by {active_task['creator_name']}  {status_badge(active_task['status'])}",
                    unsafe_allow_html=True,
                )
                st.write(active_task["description"] or "")
                st.write(f"💰 ₹{active_task['reward']:.0f}  |  ⏰ {active_task['deadline'] or 'Flexible'}")
                if active_task["status"] == "ACCEPTED":
                    if st.button("▶ Start Task", key=f"tbrowse_start_{active_task['id']}"):
                        conn = get_conn()
                        conn.execute(
                            "UPDATE tasks SET status='IN_PROGRESS' WHERE id=? AND helper_id=? AND status='ACCEPTED'",
                            (active_task["id"], user["id"]),
                        )
                        conn.commit()
                        conn.close()
                        st.rerun()
                elif active_task["status"] == "IN_PROGRESS":
                    completion_code = st.text_input(
                        "Enter completion code from task creator",
                        key=f"tbrowse_comp_{active_task['id']}",
                        max_chars=6,
                    )
                    if st.button("Verify & Complete", key=f"tbrowse_compbtn_{active_task['id']}"):
                        ok, msg = verify_otp(
                            active_task["creator_id"],
                            "DELIVERY_COMPLETION",
                            active_task["id"],
                            completion_code,
                        )
                        if ok:
                            conn = get_conn()
                            conn.execute(
                                "UPDATE tasks SET status='COMPLETED', completed_at=? WHERE id=? AND helper_id=?",
                                (now_iso(), active_task["id"], user["id"]),
                            )
                            conn.commit()
                            conn.close()
                            update_transaction_status("TASK", active_task["id"], "RELEASED")
                            add_unicoins(user["id"], 15, "Micro-task completed")
                            recalc_trust_score(user["id"])
                            notify(
                                active_task["creator_id"],
                                f"Your task '{active_task['title']}' was completed and verified.",
                            )
                            st.success("Task completed! You can now accept another task.")
                            st.rerun()
                        else:
                            st.error(msg)
        else:
            open_tasks_conn = get_conn()
            open_tasks = open_tasks_conn.execute(
                "SELECT t.*, u.full_name creator_name FROM tasks t JOIN users u ON u.id=t.creator_id "
                "WHERE t.status='CREATED' AND t.creator_id != ? ORDER BY t.id DESC",
                (user["id"],),
            ).fetchall()
            open_tasks_conn.close()

            if not open_tasks:
                st.caption("No open tasks right now.")
            else:
                st.caption("Accept one task. Other tasks will be hidden until you finish it.")

            for t in open_tasks:
                with st.container(border=True):
                    st.markdown(
                        f"**{t['title']}** ({t['category']}) — by {t['creator_name']}  "
                        f"{status_badge(t['status'])}",
                        unsafe_allow_html=True,
                    )
                    st.write(t["description"] or "")
                    st.write(f"💰 ₹{t['reward']:.0f}  |  ⏰ {t['deadline'] or 'Flexible'}")
                    if st.button("✅ Accept Task", key=f"tacc_{t['id']}"):
                        conn = get_conn()
                        # Re-check the one-active-task rule at acceptance time so
                        # a second task cannot be accepted after a rerun/race.
                        active_check = conn.execute(
                            "SELECT id FROM tasks WHERE helper_id=? AND status IN ('ACCEPTED','IN_PROGRESS') LIMIT 1",
                            (user["id"],),
                        ).fetchone()
                        check = conn.execute("SELECT status FROM tasks WHERE id=?", (t["id"],)).fetchone()
                        if active_check:
                            conn.close()
                            st.warning("You already have an active task. Finish it before accepting another one.")
                        elif not check or check["status"] != "CREATED":
                            conn.close()
                            st.error("This task is no longer available.")
                        else:
                            conn.execute(
                                "UPDATE tasks SET helper_id=?, status='ACCEPTED', accepted_at=? WHERE id=? AND status='CREATED'",
                                (user["id"], now_iso(), t["id"]),
                            )
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
    st.markdown("## 📨 Notifications")
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
    st.markdown("## 💰 Wallet & UniCoins")
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
    st.markdown("## ⚠ Disputes")
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


def _admin_metric_card(icon, label, value, hint=""):
    st.markdown(
        f'<div class="uh-admin-metric"><div class="icon">{icon}</div>'
        f'<div class="label">{label}</div><div class="value">{value}</div>'
        f'<div class="hint">{hint}</div></div>', unsafe_allow_html=True
    )


def render_admin(user):
    """Professional platform-management dashboard using the existing tables."""
    if user["role"] != "admin":
        st.error("Access denied. Admins only.")
        return

    if st.session_state.get("admin_profile_user_id"):
        render_admin_student_profile(user, st.session_state["admin_profile_user_id"])
        return

    conn = get_conn()
    total_users = conn.execute("SELECT COUNT(*) c FROM users WHERE role='student'").fetchone()["c"]
    verified_users = conn.execute("SELECT COUNT(*) c FROM users WHERE role='student' AND verified=1").fetchone()["c"]
    active_users = conn.execute("SELECT COUNT(*) c FROM users WHERE role='student' AND is_suspended=0").fetchone()["c"]
    suspended = conn.execute("SELECT COUNT(*) c FROM users WHERE role='student' AND is_suspended=1").fetchone()["c"]
    active_requests = conn.execute("SELECT COUNT(*) c FROM requests WHERE status NOT IN ('COMPLETED','CANCELLED')").fetchone()["c"]
    total_requests = conn.execute("SELECT COUNT(*) c FROM requests").fetchone()["c"]
    completed_deliveries = conn.execute("SELECT COUNT(*) c FROM requests WHERE status='COMPLETED'").fetchone()["c"]
    active_borrowings = conn.execute("SELECT COUNT(*) c FROM borrowings WHERE status NOT IN ('COMPLETED','REJECTED','CANCELLED')").fetchone()["c"]
    completed_tasks = conn.execute("SELECT COUNT(*) c FROM tasks WHERE status='COMPLETED'").fetchone()["c"]
    open_disputes = conn.execute("SELECT COUNT(*) c FROM disputes WHERE status IN ('OPEN','UNDER_REVIEW')").fetchone()["c"]
    total_value = conn.execute("SELECT COALESCE(SUM(amount),0) s FROM transactions WHERE status IN ('RELEASED','COMPLETED','PAID')").fetchone()["s"] or 0
    total_transactions = conn.execute("SELECT COUNT(*) c FROM transactions").fetchone()["c"]
    unread_notifications = conn.execute("SELECT COUNT(*) c FROM notifications WHERE is_read=0").fetchone()["c"]

    st.markdown(
        '<div class="uh-admin-shell"><div style="display:flex;justify-content:space-between;align-items:flex-start;gap:1rem;flex-wrap:wrap">'
        '<div><div class="uh-admin-eyebrow">UNI HELP · PLATFORM CONTROL</div>'
        '<div class="uh-admin-title">Admin Control Center</div>'
        '<p class="uh-admin-subtitle">Monitor students, requests, safety, transactions and platform activity from one place.</p></div>'
        '<div class="uh-admin-online"><span class="uh-admin-dot"></span> System Online</div></div></div>', unsafe_allow_html=True)

    cols = st.columns(4)
    with cols[0]: _admin_metric_card("👥", "Students", total_users, f"{active_users} active")
    with cols[1]: _admin_metric_card("📦", "Open Requests", active_requests, f"{completed_deliveries} deliveries completed")
    with cols[2]: _admin_metric_card("⚠️", "Needs Attention", open_disputes + suspended, f"{open_disputes} disputes · {suspended} suspended")
    with cols[3]: _admin_metric_card("💰", "Transaction Value", f"₹{total_value:.0f}", f"{total_transactions} transactions")

    tabs = st.tabs(["Overview", "Users", "Requests", "Disputes", "Transactions", "Announcements", "System"])

    with tabs[0]:
        st.markdown('<div class="uh-admin-section">Platform health</div>', unsafe_allow_html=True)
        health_cols = st.columns(4)
        verification_rate = (verified_users / total_users * 100) if total_users else 0
        completion_rate = (completed_deliveries / total_requests * 100) if total_requests else 0
        borrow_rate = min(100, active_borrowings / max(total_users, 1) * 100)
        task_rate = min(100, completed_tasks / max(total_users, 1) * 100)
        health_cards = [
            (health_cols[0], "✅", "Verification", f"{verified_users} of {total_users} students verified", verification_rate),
            (health_cols[1], "📦", "Delivery completion", f"{completed_deliveries} completed of {total_requests}", completion_rate),
            (health_cols[2], "🤝", "Active borrowings", f"{active_borrowings} currently active", borrow_rate),
            (health_cols[3], "⚡", "Micro-tasks", f"{completed_tasks} completed", task_rate),
        ]
        for col, icon, title, copy, rate in health_cards:
            with col:
                st.markdown(f'<div class="uh-admin-panel"><div class="uh-admin-panel-title">{icon} {title}</div><div class="uh-admin-panel-copy">{copy}</div><div class="uh-admin-statline"><i style="width:{max(0,min(100,rate)):.1f}%"></i></div></div>', unsafe_allow_html=True)

        st.markdown('<div class="uh-admin-section">Needs attention</div>', unsafe_allow_html=True)
        attention_cols = st.columns(2)
        with attention_cols[0]:
            if open_disputes:
                st.markdown(f'<div class="uh-admin-alert">⚖️ <div><strong>{open_disputes} dispute(s) awaiting review</strong><small>Review evidence and resolve or reject them from Disputes.</small></div></div>', unsafe_allow_html=True)
            else:
                st.success("No open disputes require attention.")
            if suspended:
                st.markdown(f'<div class="uh-admin-alert">🛡️ <div><strong>{suspended} suspended student account(s)</strong><small>Review profiles if an account needs to be restored.</small></div></div>', unsafe_allow_html=True)
        with attention_cols[1]:
            if active_requests:
                st.markdown(f'<div class="uh-admin-alert">📦 <div><strong>{active_requests} active request(s)</strong><small>Monitor request volume and statuses from Requests.</small></div></div>', unsafe_allow_html=True)
            if unread_notifications:
                st.markdown(f'<div class="uh-admin-alert">🔔 <div><strong>{unread_notifications} unread student notification(s)</strong><small>Use Announcements for platform-wide communication.</small></div></div>', unsafe_allow_html=True)

        st.markdown('<div class="uh-admin-section">Recent admin activity</div>', unsafe_allow_html=True)
        actions = conn.execute("""SELECT a.*, u.full_name AS admin_name FROM admin_actions a
            JOIN users u ON u.id=a.admin_id ORDER BY a.id DESC LIMIT 8""").fetchall()
        if actions:
            for a in actions:
                action_text = str(a["action"]).replace("_", " ").title()
                st.markdown(f'<div class="uh-admin-activity"><div class="uh-admin-activity-icon">🛡️</div><div><strong>{action_text}</strong><span>{a["admin_name"]} · {str(a["created_at"])[:19]}</span></div></div>', unsafe_allow_html=True)
        else:
            st.info("No admin activity has been recorded yet.")

    with tabs[1]:
        st.markdown('<div class="uh-admin-search"><h3>🔎 Search student</h3><p>Find an exact student profile using Student ID.</p>', unsafe_allow_html=True)
        search_col, button_col = st.columns([5, 1])
        with search_col:
            search_id = st.text_input("Student ID", placeholder="Enter Student ID…", label_visibility="collapsed", key="admin_student_search")
        with button_col:
            search_clicked = st.button("Search", use_container_width=True, type="primary", key="admin_student_search_btn")
        st.markdown('</div>', unsafe_allow_html=True)
        if search_clicked:
            sid_value = search_id.strip()
            if not sid_value:
                st.warning("Enter a Student ID to search.")
                st.session_state.pop("admin_search_result_id", None)
            else:
                result = conn.execute("SELECT id FROM users WHERE role='student' AND student_id=? LIMIT 1", (sid_value,)).fetchone()
                if result:
                    st.session_state["admin_search_result_id"] = result["id"]
                else:
                    st.session_state.pop("admin_search_result_id", None)
                    st.warning("No student found with that Student ID.")

        result_id = st.session_state.get("admin_search_result_id")
        if result_id:
            u = conn.execute("SELECT * FROM users WHERE id=? AND role='student'", (result_id,)).fetchone()
            if u:
                rating = _admin_student_avg_rating(u)
                rating_text = f"⭐ {rating:.1f}" if rating else "—"
                completed_tasks_u = conn.execute("SELECT COUNT(*) c FROM tasks WHERE helper_id=? AND status='COMPLETED'", (u["id"],)).fetchone()["c"]
                active_borrow_u = conn.execute("SELECT COUNT(*) c FROM borrowings WHERE borrower_id=? AND status NOT IN ('COMPLETED','REJECTED','CANCELLED')", (u["id"],)).fetchone()["c"]
                completed_delivery_u = conn.execute("SELECT COUNT(*) c FROM requests WHERE helper_id=? AND status='COMPLETED'", (u["id"],)).fetchone()["c"]
                earnings_u = conn.execute("SELECT COALESCE(SUM(amount),0) s FROM transactions WHERE payee_id=? AND status='RELEASED'", (u["id"],)).fetchone()["s"] or 0
                status = "Suspended" if u["is_suspended"] else "Active"
                verification = "Verified" if u["verified"] else "Unverified"
                name = str(u["full_name"] or "S")
                html = (
                    '<div class="uh-admin-search-result"><div class="uh-profile-header">'
                    f'<div class="uh-profile-avatar">{name[0].upper()}</div><div><h3>{name}</h3>'
                    f'<div class="uh-profile-sub">Student ID: <strong>{u["student_id"]}</strong></div></div></div>'
                    '<div class="uh-profile-grid">'
                    f'<div><span>Email</span><strong>{u["email"]}</strong></div><div><span>Phone</span><strong>{u["phone"] or "—"}</strong></div>'
                    f'<div><span>Verification</span><strong>{verification}</strong></div><div><span>Trust Score</span><strong>{u["trust_score"]}/100</strong></div>'
                    f'<div><span>Rating</span><strong>{rating_text}</strong></div><div><span>Tasks Completed</span><strong>{completed_tasks_u}</strong></div>'
                    f'<div><span>Active Borrowings</span><strong>{active_borrow_u}</strong></div><div><span>Completed Deliveries</span><strong>{completed_delivery_u}</strong></div>'
                    f'<div><span>Earnings</span><strong>₹{earnings_u:.0f}</strong></div><div><span>UniCoins</span><strong>🪙 {u["unicoins"]}</strong></div>'
                    f'<div><span>Account Status</span><strong>{status}</strong></div><div><span>Registered</span><strong>{str(u["created_at"])[:10]}</strong></div>'
                    '</div></div>'
                )
                st.markdown(html, unsafe_allow_html=True)
                if st.button("View Full Profile", key=f"view_profile_{u['id']}", type="primary", use_container_width=True):
                    st.session_state["admin_profile_user_id"] = u["student_id"]
                    st.session_state.pop("admin_search_result_id", None)
                    st.rerun()

        st.markdown('<div class="uh-admin-section">All students</div>', unsafe_allow_html=True)
        users = conn.execute("SELECT * FROM users WHERE role='student' ORDER BY id DESC").fetchall()
        if not users:
            st.info("No student accounts found.")
        for u in users:
            with st.container(border=True):
                cols = st.columns([4.5, 1.1, 1.2, 1.2])
                status_text = "🔴 Suspended" if u["is_suspended"] else "🟢 Active"
                verification_text = "✓ Verified" if u["verified"] else "○ Unverified"
                cols[0].markdown(f"**{u['full_name']}** · `{u['student_id'] or '—'}`  · {verification_text}  · {status_text}<br><span style='color:#71819a;font-size:.8rem'>{u['email']} · Trust {u['trust_score']}/100</span>", unsafe_allow_html=True)
                if cols[1].button("Profile", key=f"profile_list_{u['id']}", use_container_width=True):
                    st.session_state["admin_profile_user_id"] = u["student_id"]
                    st.rerun()
                if u["is_suspended"]:
                    if cols[2].button("Unsuspend", key=f"unsusp_{u['id']}", use_container_width=True):
                        conn.execute("UPDATE users SET is_suspended=0 WHERE id=?", (u["id"],))
                        conn.execute("INSERT INTO admin_actions (admin_id, action, target_id, details, created_at) VALUES (?,?,?,?,?)", (user["id"], "UNSUSPEND_USER", u["id"], "", now_iso()))
                        conn.commit(); st.rerun()
                else:
                    if cols[2].button("Suspend", key=f"susp_{u['id']}", use_container_width=True):
                        conn.execute("UPDATE users SET is_suspended=1 WHERE id=?", (u["id"],))
                        conn.execute("INSERT INTO admin_actions (admin_id, action, target_id, details, created_at) VALUES (?,?,?,?,?)", (user["id"], "SUSPEND_USER", u["id"], "", now_iso()))
                        conn.commit(); st.rerun()

    with tabs[2]:
        st.markdown('<div class="uh-admin-section">Request operations</div>', unsafe_allow_html=True)
        req_filter = st.selectbox("Filter", ["ALL", "CREATED", "ACCEPTED", "IN_TRANSIT", "DELIVERED", "COMPLETED", "CANCELLED", "DISPUTED"], key="admin_req_filter")
        query = "SELECT r.*, ru.full_name requester_name, hu.full_name helper_name FROM requests r JOIN users ru ON ru.id=r.requester_id LEFT JOIN users hu ON hu.id=r.helper_id"
        params = ()
        if req_filter != "ALL": query += " WHERE r.status=?"; params = (req_filter,)
        query += " ORDER BY r.id DESC LIMIT 100"
        reqs = conn.execute(query, params).fetchall()
        if not reqs: st.info("No requests match this filter.")
        for r in reqs:
            with st.container(border=True):
                c1,c2,c3 = st.columns([4,2,1.2])
                c1.markdown(f"**#{r['id']} · {r['item_name']}**<br><span style='color:#71819a;font-size:.72rem'>{r['requester_name']} → {r['helper_name'] or 'Unassigned'}</span>", unsafe_allow_html=True)
                c2.markdown(status_badge(r["status"]), unsafe_allow_html=True)
                c3.write(f"₹{r['reward']:.0f}")

    with tabs[3]:
        st.markdown('<div class="uh-admin-section">Safety & dispute review</div>', unsafe_allow_html=True)
        dispute_filter = st.selectbox("Status", ["ALL", "OPEN", "UNDER_REVIEW", "RESOLVED", "REJECTED"], key="admin_dispute_filter")
        q = "SELECT d.*, u.full_name reporter_name FROM disputes d JOIN users u ON u.id=d.reporter_id"
        pms = ()
        if dispute_filter != "ALL": q += " WHERE d.status=?"; pms=(dispute_filter,)
        q += " ORDER BY d.id DESC"
        disputes = conn.execute(q,pms).fetchall()
        if not disputes: st.success("No disputes match this filter.")
        for d in disputes:
            with st.container(border=True):
                st.markdown(f"**{d['category']}** · {d['transaction_type']} #{d['transaction_id']} · {d['reporter_name']} {status_badge(d['status'])}", unsafe_allow_html=True)
                st.write(d["description"] or "No description provided.")
                if d["evidence_path"] and os.path.exists(d["evidence_path"]): st.image(d["evidence_path"], width=240)
                if d["status"] in ("OPEN", "UNDER_REVIEW"):
                    c1,c2,c3=st.columns(3)
                    if c1.button("Mark Under Review", key=f"dur_{d['id']}"):
                        conn.execute("UPDATE disputes SET status='UNDER_REVIEW' WHERE id=?", (d["id"],)); conn.commit(); st.rerun()
                    if c2.button("Resolve", key=f"dres_{d['id']}"):
                        conn.execute("UPDATE disputes SET status='RESOLVED', resolved_at=? WHERE id=?", (now_iso(), d["id"])); conn.execute("INSERT INTO admin_actions (admin_id, action, target_id, details, created_at) VALUES (?,?,?,?,?)", (user["id"], "RESOLVE_DISPUTE", d["id"], "", now_iso())); conn.commit(); st.rerun()
                    if c3.button("Reject", key=f"drej_{d['id']}"):
                        conn.execute("UPDATE disputes SET status='REJECTED', resolved_at=? WHERE id=?", (now_iso(), d["id"])); conn.execute("INSERT INTO admin_actions (admin_id, action, target_id, details, created_at) VALUES (?,?,?,?,?)", (user["id"], "REJECT_DISPUTE", d["id"], "", now_iso())); conn.commit(); st.rerun()

    with tabs[4]:
        st.markdown('<div class="uh-admin-section">Transaction monitoring</div>', unsafe_allow_html=True)
        tx_filter = st.selectbox("Transaction status", ["ALL", "PENDING", "RELEASED", "COMPLETED", "PAID", "CANCELLED", "DISPUTED"], key="admin_tx_filter")
        tq = "SELECT * FROM transactions"; tp = ()
        if tx_filter != "ALL": tq += " WHERE status=?"; tp=(tx_filter,)
        tq += " ORDER BY id DESC LIMIT 100"
        txs = conn.execute(tq,tp).fetchall()
        if not txs: st.info("No transactions match this filter.")
        for tx in txs:
            with st.container(border=True):
                c1,c2,c3=st.columns([4,2,1.3])
                c1.write(f"**#{tx['id']}** · {tx['related_type']} #{tx['related_id']}")
                c2.markdown(status_badge(tx["status"]), unsafe_allow_html=True)
                c3.write(f"₹{tx['amount']:.0f}")

    with tabs[5]:
        st.markdown('<div class="uh-admin-section">📣 Broadcast Center</div>', unsafe_allow_html=True)
        st.markdown(
            '<div class="uh-admin-panel">'
            '<div class="uh-admin-panel-title">📣 Message all students</div>'
            '<div class="uh-admin-panel-copy">Send one announcement through UNI HELP notifications, email, or both. Uses the existing users, notifications and SMTP systems — no new database tables.</div>'
            '</div>', unsafe_allow_html=True
        )

        audience = st.selectbox(
            "Audience",
            ["All active students", "All verified students", "All students"],
            key="admin_announcement_audience"
        )

        delivery_cols = st.columns(2)
        with delivery_cols[0]:
            send_in_app = st.checkbox("🔔 UNI HELP notification", value=True, key="admin_send_in_app")
        with delivery_cols[1]:
            send_email_broadcast = st.checkbox("✉️ Email", value=True, key="admin_send_email")

        email_subject = st.text_input(
            "Email subject",
            value="UNI HELP Announcement",
            max_chars=120,
            key="admin_announcement_subject",
            disabled=not send_email_broadcast,
            help="Used only when Email is selected."
        )
        message = st.text_area(
            "Announcement message",
            placeholder="Example: UNI HELP maintenance will take place tonight at 11 PM.",
            max_chars=1000,
            key="admin_announcement_message",
            height=140
        )

        if send_email_broadcast and not EMAIL_CONFIGURED:
            st.warning("✉️ Email delivery is not configured. Add the existing SMTP secrets to enable broadcast emails. UNI HELP notifications can still be sent.")

        if st.button("🚀 Send Broadcast", type="primary", use_container_width=True, key="admin_send_announcement"):
            clean = message.strip()
            subject = email_subject.strip()

            if not clean:
                st.warning("Enter an announcement message first.")
            elif not send_in_app and not send_email_broadcast:
                st.warning("Select at least one delivery method.")
            elif send_email_broadcast and not subject:
                st.warning("Enter an email subject.")
            else:
                if audience == "All active students":
                    recipients = conn.execute("SELECT id, full_name, email FROM users WHERE role='student' AND is_suspended=0").fetchall()
                elif audience == "All verified students":
                    recipients = conn.execute("SELECT id, full_name, email FROM users WHERE role='student' AND verified=1 AND is_suspended=0").fetchall()
                else:
                    recipients = conn.execute("SELECT id, full_name, email FROM users WHERE role='student'").fetchall()

                now = now_iso()
                notification_count = 0
                email_success = 0
                email_failed = 0

                if send_in_app and recipients:
                    conn.executemany(
                        "INSERT INTO notifications (user_id, message, is_read, created_at) VALUES (?,?,0,?)",
                        [(r["id"], clean, now) for r in recipients]
                    )
                    notification_count = len(recipients)

                # Email each recipient using the already-configured SMTP helper.
                # No credentials or message secrets are written to the database.
                if send_email_broadcast and EMAIL_CONFIGURED:
                    for recipient in recipients:
                        email = (recipient["email"] or "").strip()
                        if not email:
                            email_failed += 1
                            continue
                        email_body = f"Hi {recipient['full_name'] or 'there'},\n\n{clean}\n\n- UNI HELP"
                        if send_email(email, subject, email_body):
                            email_success += 1
                        else:
                            email_failed += 1

                details = (
                    f"audience={audience}; recipients={len(recipients)}; "
                    f"in_app={notification_count}; email_sent={email_success}; email_failed={email_failed}"
                )
                conn.execute(
                    "INSERT INTO admin_actions (admin_id, action, target_id, details, created_at) VALUES (?,?,?,?,?)",
                    (user["id"], "BROADCAST_ANNOUNCEMENT", None, details, now)
                )
                conn.commit()

                if send_in_app:
                    st.success(f"🔔 UNI HELP notification sent to {notification_count} student(s).")
                if send_email_broadcast:
                    if EMAIL_CONFIGURED:
                        if email_failed:
                            st.warning(f"✉️ Email broadcast finished: {email_success} sent, {email_failed} failed.")
                        else:
                            st.success(f"✉️ Email sent successfully to {email_success} student(s).")
                    else:
                        st.info("✉️ Email was not sent because SMTP is not configured. The in-app notification delivery was completed.")

    with tabs[6]:
        st.markdown('<div class="uh-admin-section">Platform diagnostics</div>', unsafe_allow_html=True)
        checks = [
            ("SQLite database", os.path.exists(DB_PATH), DB_PATH),
            ("Email SMTP", EMAIL_CONFIGURED, "Configured" if EMAIL_CONFIGURED else "Not configured"),
            ("QR storage", os.path.isdir(QR_DIR), QR_DIR),
            ("Profile photo storage", os.path.isdir(PHOTOS_DIR), PHOTOS_DIR),
        ]
        for label, ok, detail in checks:
            icon = "🟢" if ok else "🟠"
            text = "READY" if ok else "CHECK CONFIG"
            fg = "#047857" if ok else "#c2410c"
            st.markdown(f'<div class="uh-admin-panel" style="margin-bottom:.5rem;display:flex;justify-content:space-between;align-items:center"><div><div class="uh-admin-panel-title">{icon} {label}</div><div class="uh-admin-panel-copy">{detail}</div></div><strong style="color:{fg};font-size:.68rem">{text}</strong></div>', unsafe_allow_html=True)
        st.markdown('<div class="uh-admin-section">Database record counts</div>', unsafe_allow_html=True)
        table_counts = []
        for table in ["users","requests","items","borrowings","tasks","transactions","ratings","notifications","disputes","unicoin_transactions","locations","admin_actions"]:
            try:
                count = conn.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]
            except sqlite3.Error:
                count = "Unavailable"
            table_counts.append((table, count))
        st.dataframe({"Table": [x[0] for x in table_counts], "Records": [x[1] for x in table_counts]}, use_container_width=True, hide_index=True)

    conn.close()


# =============================================================================
# 7. MAIN ROUTER
# =============================================================================

def render_profile(user):
    fresh = user_by_id(user["id"])
    if not fresh:
        st.error("Unable to load your profile right now.")
        return
    user = dict(fresh)
    st.markdown("<div class='uh-page-kicker'>ACCOUNT</div>", unsafe_allow_html=True)
    st.markdown("# My Profile")
    st.caption("Manage your campus identity and profile photo.")
    photo_path = user.get("profile_photo_path")
    left, right = st.columns([1.15, 2.5])
    with left:
        if photo_path and os.path.exists(photo_path):
            st.image(photo_path, width=150)
        else:
            initials = ''.join(part[0] for part in str(user['full_name']).split()[:2]).upper() or "U"
            st.markdown(f'<div class="uh-profile-photo-placeholder">{initials}</div>', unsafe_allow_html=True)
    with right:
        safe_name = str(user['full_name']).replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
        st.markdown(f"<div class='uh-profile-main-card'><div class='uh-profile-name'>{safe_name}</div><div class='uh-profile-id'>Student ID · <strong>{user['student_id'] or '—'}</strong></div><div class='uh-profile-badges'><span class='uh-profile-badge green'>✓ {'Verified' if user['verified'] else 'Verification pending'}</span><span class='uh-profile-badge blue'>Trust {user['trust_score']}/100</span></div></div>", unsafe_allow_html=True)
    st.markdown("### Profile photo")
    uploaded = st.file_uploader("Add or change your profile photo", type=["png", "jpg", "jpeg", "webp"], key="student_profile_photo")
    if uploaded is not None and st.button("Save Profile Photo", type="primary", use_container_width=True):
        try:
            ext = os.path.splitext(uploaded.name)[1].lower() or ".jpg"
            safe_name = f"profile_{user['id']}_{int(time.time())}{ext}"
            path = os.path.join(PHOTOS_DIR, safe_name)
            with open(path, "wb") as fh:
                fh.write(uploaded.getbuffer())
            conn = get_conn(); conn.execute("UPDATE users SET profile_photo_path=? WHERE id=?", (path, user["id"])); conn.commit(); conn.close()
            st.session_state["user"] = dict(user_by_id(user["id"]))
            st.success("Profile photo updated successfully."); st.rerun()
        except Exception:
            st.error("Unable to update your profile photo. Please try again.")
    st.markdown("### Personal information")
    info1, info2 = st.columns(2)
    info1.markdown(f"<div class='uh-detail-card'><span>Full Name</span><strong>{safe_name}</strong></div>", unsafe_allow_html=True)
    info2.markdown(f"<div class='uh-detail-card'><span>Student ID</span><strong>{user['student_id'] or '—'}</strong></div>", unsafe_allow_html=True)
    info1.markdown(f"<div class='uh-detail-card'><span>Email</span><strong>{user['email']}</strong></div>", unsafe_allow_html=True)
    info2.markdown(f"<div class='uh-detail-card'><span>Phone</span><strong>{user['phone'] or '—'}</strong></div>", unsafe_allow_html=True)
    st.markdown("### Account shortcuts")
    q1, q2, q3 = st.columns(3)
    if q1.button("🔔 Notifications", use_container_width=True, key="profile_notifications"):
        st.session_state["nav"] = "Notifications"; st.rerun()
    if q2.button("⚖️ Disputes", use_container_width=True, key="profile_disputes"):
        st.session_state["nav"] = "Disputes"; st.rerun()
    if q3.button("🚪 Logout", use_container_width=True, key="profile_logout"):
        st.session_state["user"] = None; st.session_state["auth_mode"] = "Home"; st.session_state["nav"] = "Dashboard"; st.rerun()
    st.markdown("### Your UNI HELP stats")
    avg = round(user["rating_sum"] / user["rating_count"], 1) if user["rating_count"] else 0
    p1, p2, p3, p4 = st.columns(4)
    p1.metric("Trust Score", f"{user['trust_score']}/100"); p2.metric("Rating", f"⭐ {avg:.1f}" if avg else "—"); p3.metric("UniCoins", f"🪙 {user['unicoins']}"); p4.metric("Member since", str(user['created_at'])[:10])


def render_student_topbar(user):
    """Premium compact student header with quick Dashboard + notification access."""
    unread = get_notifications(user['id'], unread_only=True, limit=50)
    current = st.session_state.get('nav', 'Dashboard')
    title_map = {'Dashboard':'Home', 'Create':'Create', 'Help':'Need Help', 'Micro-Tasks':'Tasks', 'Leaders':'Leaders', 'Profile':'Profile', 'Delivery':'Delivery', 'Borrowing':'Borrowing', 'Wallet':'Wallet', 'Notifications':'Notifications'}
    title = title_map.get(current, current)
    first = str(user['full_name']).split()[0]
    st.markdown("<div class='uh-student-shell'></div>", unsafe_allow_html=True)
    st.markdown(f"""<div class='uh-mobile-header'>
      <div class='uh-mobile-brand'><span>🎓</span><div><strong>UNI HELP</strong><small>{title}</small></div></div>
      <div class='uh-mobile-actions'><div class='uh-mobile-greeting'>Hi, {first}</div></div>
    </div>""", unsafe_allow_html=True)
    c1,c2,c3 = st.columns([1.1, 4.2, 1.1])
    with c1:
        if current != 'Dashboard' and st.button('⌂', key='top_home', help='Dashboard', use_container_width=True):
            st.session_state['nav']='Dashboard'; st.rerun()
    with c3:
        if st.button(f"🔔 {len(unread) if unread else ''}", key='student_notifications', use_container_width=True):
            st.session_state['nav']='Notifications'; st.rerun()

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
    # Student navigation is rendered as a fixed mobile app bar below the page.
    return


def render_student_bottom_nav(user):
    current = st.session_state.get('nav', 'Dashboard')
    with st.container(key='uh_bottom_nav'):
        cols = st.columns(5)
        navs = [('⌂', 'Home', 'Dashboard'), ('☷', 'Tasks', 'Micro-Tasks'), ('＋', '', 'CREATE'), ('♕', 'Leaders', 'Leaders'), ('♙', 'Profile', 'Profile')]
        for col, (icon, label, target) in zip(cols, navs):
            with col:
                if target == 'CREATE':
                    st.markdown("<div class='uh-nav-plus'>", unsafe_allow_html=True)
                    if st.button(icon, key='bottom_create', use_container_width=True):
                        st.session_state['nav'] = 'Create'; st.rerun()
                    st.markdown("</div>", unsafe_allow_html=True)
                else:
                    cls = 'uh-nav-active' if current == target else ''
                    st.markdown(f"<div class='{cls}'>", unsafe_allow_html=True)
                    if st.button(f"{icon}\n{label}", key=f'bottom_{target}', use_container_width=True):
                        st.session_state['nav'] = target; st.rerun()
                    st.markdown("</div>", unsafe_allow_html=True)

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
    if user["role"] != "admin":
        render_student_topbar(user)

    nav = st.session_state.get("nav", "Dashboard")
    if nav == "Dashboard":
        render_dashboard(user)
    elif nav == "Create":
        render_create_hub(user)
    elif nav == "Help":
        render_help_hub(user)
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
    elif nav == "Profile":
        render_profile(user)
    elif nav == "Leaders":
        render_leaders(user)
    elif nav == "Disputes":
        render_disputes(user)
    elif nav == "Admin":
        render_admin(user)
    else:
        render_dashboard(user)

    if user["role"] != "admin":
        render_student_bottom_nav(user)


if __name__ == "__main__":
    main()
