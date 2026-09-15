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
from email.mime.text import MIMEText
from datetime import datetime, timedelta

import streamlit as st
from werkzeug.security import generate_password_hash, check_password_hash
import qrcode
from dotenv import load_dotenv

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
OTP_EXPIRY_MINUTES = int(os.getenv("OTP_EXPIRY_MINUTES", "5"))
OTP_MAX_ATTEMPTS = int(os.getenv("OTP_MAX_ATTEMPTS", "5"))
LOCATION_RADIUS_METERS = float(os.getenv("LOCATION_RADIUS_METERS", "100"))
QR_EXPIRY_MINUTES = int(os.getenv("QR_EXPIRY_MINUTES", "30"))
SECRET_KEY = os.getenv("SECRET_KEY", secrets.token_hex(16))

SMTP_EMAIL = os.getenv("SMTP_EMAIL", "").strip()
SMTP_APP_PASSWORD = os.getenv("SMTP_APP_PASSWORD", "").strip()
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))

# Email is only "live" if both credentials are configured. Otherwise the whole
# app runs in DEMO MODE for anything that would otherwise require an external
# service (email delivery, real GPS). This is intentional and always labeled.
EMAIL_CONFIGURED = bool(SMTP_EMAIL and SMTP_APP_PASSWORD)

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
    """Attempts real Gmail SMTP delivery. Returns True if actually sent."""
    if not EMAIL_CONFIGURED:
        return False
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = SMTP_EMAIL
        msg["To"] = to_email
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context) as server:
            server.login(SMTP_EMAIL, SMTP_APP_PASSWORD)
            server.sendmail(SMTP_EMAIL, [to_email], msg.as_string())
        return True
    except Exception as e:
        st.session_state["_last_email_error"] = str(e)
        return False


def deliver_otp(user_row, purpose, reference_id, context_label=""):
    """Generates an OTP and either emails it (live mode) or displays it
    clearly labeled as DEMO ONLY. Never mixes the two silently."""
    otp_plain = create_otp(user_row["id"], purpose, reference_id)
    subject = f"UNI HELP - Your {purpose.replace('_',' ').title()} Code"
    body = (
        f"Hi {user_row['full_name']},\n\n"
        f"Your verification code for {context_label or purpose} is: {otp_plain}\n"
        f"This code expires in {OTP_EXPIRY_MINUTES} minutes.\n\n"
        f"If you did not request this, ignore this email.\n- UNI HELP"
    )
    sent = send_email(user_row["email"], subject, body)
    if sent:
        st.success(f"📧 A verification code was emailed to {user_row['email']}.")
    else:
        st.warning("🧪 DEMO MODE — Email not configured. Showing code directly (never do this in production):")
        st.code(otp_plain, language=None)
    return otp_plain if not sent else None


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

def register_user(full_name, email, phone, student_id, password):
    email = email.strip().lower()
    if not full_name.strip():
        return False, "Full name is required."
    if not email.endswith(UNIVERSITY_EMAIL_DOMAIN.lower()):
        return False, f"Email must be a university address ending with {UNIVERSITY_EMAIL_DOMAIN}"
    if len(password) < 6:
        return False, "Password must be at least 6 characters."
    if user_by_email(email):
        return False, "An account with this email already exists."

    conn = get_conn()
    conn.execute(
        """INSERT INTO users (full_name, email, phone, student_id, password_hash, role,
            verified, trust_score, created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (full_name.strip(), email, phone.strip(), student_id.strip(),
         hash_password(password), "student", 0, 50, now_iso()),
    )
    new_id = conn.execute("SELECT last_insert_rowid() id").fetchone()["id"]
    conn.commit()
    conn.close()

    user_row = user_by_id(new_id)
    deliver_otp(user_row, "EMAIL_VERIFICATION", None, "email verification")
    notify(new_id, "Welcome to UNI HELP! Please verify your email to unlock all features.")
    return True, new_id


def login_user(email, password):
    row = user_by_email(email.strip().lower())
    if row is None:
        return False, "No account found with that email."
    if not verify_password(password, row["password_hash"]):
        return False, "Incorrect password."
    if row["is_suspended"]:
        return False, "This account has been suspended. Contact an administrator."
    return True, dict(row)


# =============================================================================
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
:root{--navy:#071B3A;--navy2:#0D2B57;--blue:#1769FF;--blueSoft:#EAF2FF;--orange:#FF7A1A;--ink:#10213D;--muted:#61708A;--border:#DCE4F0;--bg:#F5F8FC}
.stApp{background:radial-gradient(circle at 8% 0%,rgba(23,105,255,.06),transparent 28%),radial-gradient(circle at 92% 8%,rgba(255,122,26,.05),transparent 25%),var(--bg);color:var(--ink)}
.block-container{max-width:1240px;padding:2rem 2rem 4rem}
[data-testid="stHeader"]{background:rgba(245,248,252,.82)}
[data-testid="stToolbar"]{display:none}
h1,h2,h3,h4,h5,h6{color:var(--navy)!important;letter-spacing:-.02em}
p,.stMarkdown{color:var(--ink)}
small,[data-testid="stCaptionContainer"]{color:var(--muted)!important}
section[data-testid="stSidebar"]{background:linear-gradient(180deg,var(--navy),#0A244A);border-right:1px solid rgba(255,255,255,.08)}
section[data-testid="stSidebar"] *{color:#F7FAFF!important}
section[data-testid="stSidebar"] button{border:1px solid rgba(255,255,255,.16)!important;background:rgba(255,255,255,.08)!important}
section[data-testid="stSidebar"] button:hover{background:rgba(255,122,26,.2)!important;border-color:rgba(255,122,26,.55)!important}
section[data-testid="stSidebar"] [data-testid="stRadio"] label{border-radius:12px;padding:.55rem .7rem;margin:.12rem 0}
section[data-testid="stSidebar"] [data-testid="stRadio"] label:hover{background:rgba(255,255,255,.09)}
.uh-hero{position:relative;overflow:hidden;background:linear-gradient(135deg,var(--navy),#114A9B 65%,var(--blue));padding:3.6rem 2.2rem;border-radius:28px;color:#fff!important;margin:0 0 1.5rem;box-shadow:0 20px 50px rgba(7,27,58,.18)}
.uh-hero:after{content:"";position:absolute;width:280px;height:280px;border-radius:50%;right:-80px;top:-110px;background:rgba(255,122,26,.18)}
.uh-hero h1,.uh-hero p{color:#fff!important;position:relative;z-index:1}
.uh-hero h1{font-size:clamp(2.2rem,5vw,3.6rem);margin:0 0 .35rem}
.uh-hero p{font-size:1.08rem;opacity:.92;max-width:720px}
.uh-eyebrow{color:#FFB77D!important;font-size:.78rem;font-weight:800;letter-spacing:.12em;text-transform:uppercase}
.uh-card{background:#fff;border:1px solid var(--border);border-radius:20px;padding:1.25rem;box-shadow:0 8px 24px rgba(15,38,72,.07);margin-bottom:.9rem;transition:.18s ease;color:var(--ink)!important}
.uh-card:hover{transform:translateY(-3px);box-shadow:0 14px 34px rgba(15,38,72,.12);border-color:#B9CBE6}
.uh-card h3{margin-top:0}
.uh-feature-icon{width:46px;height:46px;display:grid;place-items:center;border-radius:14px;background:var(--blueSoft);font-size:1.35rem;margin-bottom:.8rem}
.uh-page-title{margin-bottom:.15rem}.uh-page-subtitle{color:var(--muted)!important;margin-bottom:1.25rem}
[data-testid="stMetric"]{background:#fff;border:1px solid var(--border);border-radius:18px;padding:1rem;box-shadow:0 6px 18px rgba(15,38,72,.06)}
[data-testid="stMetricLabel"]{color:var(--muted)!important}[data-testid="stMetricValue"]{color:var(--navy)!important;font-weight:800}
.stButton>button,.stFormSubmitButton>button{min-height:46px;border-radius:12px!important;border:1px solid #C9D5E7!important;background:#fff!important;color:var(--navy)!important;font-weight:750!important;transition:.18s ease!important;box-shadow:0 3px 10px rgba(15,38,72,.05)}
.stButton>button:hover,.stFormSubmitButton>button:hover{border-color:var(--blue)!important;color:var(--blue)!important;transform:translateY(-1px);box-shadow:0 8px 18px rgba(23,105,255,.13)}
.stButton>button[kind="primary"],.stFormSubmitButton>button[kind="primary"]{background:var(--blue)!important;color:#fff!important;border-color:var(--blue)!important}
[data-baseweb="input"],[data-baseweb="textarea"],[data-baseweb="select"]>div{background:#fff!important;border:1px solid #C9D5E7!important;border-radius:12px!important;color:var(--ink)!important}
input,textarea{color:var(--ink)!important}
[data-baseweb="input"]:focus-within,[data-baseweb="textarea"]:focus-within{border-color:var(--blue)!important;box-shadow:0 0 0 3px rgba(23,105,255,.1)!important}
label{color:var(--navy)!important;font-weight:650!important}
.stForm{background:rgba(255,255,255,.72);border:1px solid var(--border);border-radius:20px;padding:1rem 1.15rem}
.stTabs [data-baseweb="tab-list"]{gap:.35rem;border-bottom:1px solid var(--border)}
.stTabs [data-baseweb="tab"]{color:var(--muted)!important;font-weight:700;border-radius:10px 10px 0 0}
.stTabs [aria-selected="true"]{color:var(--blue)!important}
div[data-testid="stVerticalBlockBorderWrapper"]{border-color:var(--border)!important;border-radius:18px!important;box-shadow:0 5px 16px rgba(15,38,72,.05)}
[data-testid="stAlert"]{border-radius:14px!important}
.uh-demo-banner{display:inline-flex;background:rgba(255,122,26,.09);border:1px solid rgba(255,122,26,.28);color:#8A430D!important;padding:.42rem .75rem;border-radius:999px;font-size:.78rem;font-weight:750;margin:0 0 1rem}
.uh-demo-banner *{color:#8A430D!important}
.uh-empty{text-align:center;padding:2.2rem 1rem;background:#fff;border:1px dashed #C9D5E7;border-radius:20px}
.uh-empty-icon{font-size:2rem}.uh-empty h4{margin:.2rem 0}.uh-empty p{color:var(--muted)!important;margin:0}
.uh-badge{display:inline-block;padding:4px 10px;border-radius:999px;font-size:.76rem;font-weight:800}
@keyframes uh-pop{0%{transform:scale(.96);opacity:.2}60%{transform:scale(1.02);opacity:1}100%{transform:scale(1);opacity:1}}
[data-testid="stAlert"][kind="success"]{animation:uh-pop .32s ease-out}
@media(max-width:768px){.block-container{padding:1rem .8rem 3rem}.uh-hero{padding:2rem 1.2rem;border-radius:22px}.uh-hero h1{font-size:2.25rem}.uh-card{padding:1rem;border-radius:16px}[data-testid="stMetric"]{padding:.75rem}.stButton>button,.stFormSubmitButton>button{min-height:50px}}
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

if not EMAIL_CONFIGURED:
    st.markdown('<div class="uh-demo-banner">🧪 Demo mode · verification codes are shown in-app</div>',
                unsafe_allow_html=True)


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

def render_landing():
    st.markdown(
        """
        <div class="uh-hero">
            <div class="uh-eyebrow">Verified campus community</div>
            <h1>🎓 UNI HELP</h1>
            <p>Your campus. Your community. Someone can help.</p>
            <p>Borrow, deliver, assist and earn — all inside one trusted university network.</p>
        </div>
        """, unsafe_allow_html=True
    )
    features = [
        ("📦","Delivery","Request or deliver items across campus with secure OTP, QR and location-aware handovers."),
        ("🤝","Borrowing","Share useful campus items like chargers, books, calculators and equipment."),
        ("🛠️","Micro-Tasks","Post quick campus errands and earn by helping verified students."),
        ("💳","Wallet","Track prototype earnings, UniCoins and transaction history in one place."),
        ("🛡️","Trust","Build reputation through verified handovers, ratings and transparent trust scoring."),
        ("🔔","Notifications","Stay on top of requests, verification updates and platform activity."),
    ]
    cols=st.columns(3)
    for i,(icon,title,desc) in enumerate(features):
        with cols[i%3]:
            st.markdown(f'<div class="uh-card"><div class="uh-feature-icon">{icon}</div><h3>{title}</h3><p>{desc}</p></div>',unsafe_allow_html=True)
    st.markdown("### Built for student life")
    st.markdown('<div class="uh-card"><b>🔐 Verified students</b> · <b>📱 OTP + QR</b> · <b>📍 Location-aware</b> · <b>⭐ Trust & ratings</b> · <b>🪙 UniCoins</b></div>',unsafe_allow_html=True)
    c1,c2=st.columns(2)
    with c1:
        if st.button("🚀 Get Started",use_container_width=True,type="primary"):
            st.session_state["auth_mode"]="Register"; st.rerun()
    with c2:
        if st.button("🔑 I already have an account",use_container_width=True):
            st.session_state["auth_mode"]="Login"; st.rerun()
    with st.expander("🧪 Demo access"):
        st.caption("For evaluators: load sample students, delivery requests, borrow items, tasks and UniCoins.")
        if st.button("Load demo data",use_container_width=True):
            seed_demo_data(); st.success("Demo data is ready."); st.caption("Demo accounts use password: demo1234")

def render_register():
    st.subheader("Create your UNI HELP account")
    st.caption(f"University email must end with **{UNIVERSITY_EMAIL_DOMAIN}**")
    with st.form("register_form"):
        full_name = st.text_input("Full name")
        email = st.text_input("University email", placeholder=f"yourname{UNIVERSITY_EMAIL_DOMAIN}")
        phone = st.text_input("Phone number")
        student_id = st.text_input("Student ID")
        password = st.text_input("Password", type="password")
        password2 = st.text_input("Confirm password", type="password")
        submitted = st.form_submit_button("Register")
        if submitted:
            if password != password2:
                st.error("Passwords do not match.")
            else:
                ok, result = register_user(full_name, email, phone, student_id, password)
                if ok:
                    st.session_state["pending_verify_email"] = email.strip().lower()
                    st.session_state["auth_mode"] = "Verify"
                    st.success("Account created! Please verify your email to continue.")
                    st.rerun()
                else:
                    st.error(result)
    if st.button("⬅ Back"):
        st.session_state["auth_mode"] = "Home"
        st.rerun()


def render_verify():
    st.subheader("📧 Verify your email")
    email = st.session_state.get("pending_verify_email", "")
    email = st.text_input("Email to verify", value=email)
    otp_input = st.text_input("Enter the 6-digit code", max_chars=6)
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Verify"):
            u = user_by_email(email.strip().lower())
            if not u:
                st.error("No account found with that email.")
            else:
                ok, msg = verify_otp(u["id"], "EMAIL_VERIFICATION", None, otp_input)
                if ok:
                    conn = get_conn()
                    conn.execute("UPDATE users SET verified = 1 WHERE id = ?", (u["id"],))
                    conn.commit()
                    conn.close()
                    notify(u["id"], "Your email has been verified. Welcome to the verified campus network!")
                    st.success("Email verified! You can now log in.")
                    st.session_state["auth_mode"] = "Login"
                    st.rerun()
                else:
                    st.error(msg)
    with col2:
        if st.button("Resend code"):
            u = user_by_email(email.strip().lower())
            if u:
                deliver_otp(u, "EMAIL_VERIFICATION", None, "email verification")
            else:
                st.error("No account found with that email.")
    if st.button("⬅ Back to login"):
        st.session_state["auth_mode"] = "Login"
        st.rerun()


def render_login():
    st.subheader("Login to UNI HELP")
    with st.form("login_form"):
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Login")
        if submitted:
            ok, result = login_user(email, password)
            if ok:
                st.session_state["user"] = result
                st.session_state["nav"] = "Dashboard"
                st.rerun()
            else:
                st.error(result)
    colA, colB = st.columns(2)
    with colA:
        if st.button("Need an account? Register"):
            st.session_state["auth_mode"] = "Register"
            st.rerun()
    with colB:
        if st.button("Verify email instead"):
            st.session_state["auth_mode"] = "Verify"
            st.rerun()
    if st.button("⬅ Back"):
        st.session_state["auth_mode"] = "Home"
        st.rerun()


# -----------------------------------------------------------------------------
# 6.2 DASHBOARD
# -----------------------------------------------------------------------------

def render_dashboard(user):
    st.markdown(f'<div class="uh-page-title"><h1>Welcome back, {user["full_name"].split()[0]} 👋</h1></div>',unsafe_allow_html=True)
    st.markdown('<div class="uh-page-subtitle">Here’s your campus activity at a glance.</div>',unsafe_allow_html=True)
    if not user["verified"]: st.warning("Verify your university email to unlock all features.")
    conn=get_conn()
    completed_deliveries=conn.execute("SELECT COUNT(*) c FROM requests WHERE helper_id=? AND status='COMPLETED'",(user["id"],)).fetchone()["c"]
    active_borrow=conn.execute("SELECT COUNT(*) c FROM borrowings WHERE borrower_id=? AND status NOT IN ('COMPLETED','REJECTED','CANCELLED')",(user["id"],)).fetchone()["c"]
    earnings=conn.execute("SELECT COALESCE(SUM(amount),0) s FROM transactions WHERE payee_id=? AND status='RELEASED'",(user["id"],)).fetchone()["s"]
    conn.close()
    avg_rating=round(user["rating_sum"]/user["rating_count"],1) if user["rating_count"] else 0
    metrics=[("Trust Score",f'{user["trust_score"]}/100'),("Rating",f'⭐ {avg_rating}' if user["rating_count"] else "—"),("Completed",completed_deliveries),("Borrowings",active_borrow),("Earnings",f'₹{earnings:.0f}'),("UniCoins",f'🪙 {user["unicoins"]}')]
    m=st.columns(6)
    for col,(label,value) in zip(m,metrics): col.metric(label,value)
    st.markdown("### Quick actions")
    actions=[("📦","Delivery","Send or carry something across campus."),("🤝","Borrowing","Lend or request useful items."),("🛠️","Micro-Tasks","Post or complete small campus tasks."),("💰","Wallet","View earnings and UniCoins.")]
    cols=st.columns(4)
    for col,(icon,label,desc) in zip(cols,actions):
        with col:
            st.markdown(f'<div class="uh-card"><div class="uh-feature-icon">{icon}</div><h3>{label}</h3><p>{desc}</p></div>',unsafe_allow_html=True)
            if st.button(f"Open {label}",key=f"dash_{label}",use_container_width=True):
                st.session_state["nav"]=label; st.rerun()
    colL,colR=st.columns(2)
    with colL:
        st.markdown("### 🔔 Recent notifications")
        notifs=get_notifications(user["id"],limit=6)
        if not notifs: st.markdown('<div class="uh-empty"><div class="uh-empty-icon">🔔</div><h4>All caught up</h4><p>No new notifications yet.</p></div>',unsafe_allow_html=True)
        for n in notifs:
            prefix="🔵 " if not n["is_read"] else ""
            st.markdown(f'<div class="uh-card"><b>{prefix}{n["message"]}</b><br><small>{n["created_at"][:19].replace("T"," ")}</small></div>',unsafe_allow_html=True)
    with colR:
        st.markdown("### 📦 Active delivery requests")
        conn=get_conn()
        active=conn.execute("SELECT * FROM requests WHERE requester_id=? AND status NOT IN ('COMPLETED','CANCELLED') ORDER BY id DESC",(user["id"],)).fetchall()
        conn.close()
        if not active: st.markdown('<div class="uh-empty"><div class="uh-empty-icon">✨</div><h4>No active requests</h4><p>Your campus board is clear.</p></div>',unsafe_allow_html=True)
        for r in active: st.markdown(f'<div class="uh-card"><b>{r["item_name"]}</b> {status_badge(r["status"])}<br><small>📍 {r["pickup_location"]} → {r["destination"]}</small></div>',unsafe_allow_html=True)


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

def render_admin(user):
    if user["role"] != "admin":
        st.error("Access denied. Admins only.")
        return

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

    r1 = st.columns(4)
    r1[0].metric("Total Users", total_users)
    r1[1].metric("Verified Users", verified_users)
    r1[2].metric("Active Requests", active_requests)
    r1[3].metric("Total Deliveries", deliveries)
    r2 = st.columns(4)
    r2[0].metric("Borrowings", borrowings)
    r2[1].metric("Completed Tasks", completed_tasks)
    r2[2].metric("Open Disputes", disputes_open)
    r2[3].metric("Suspended Users", suspended)
    st.metric("Total Prototype Transaction Value (₹)", f"{total_value:.0f}")

    st.divider()
    tabs = st.tabs(["Users", "Disputes", "Transactions", "Requests"])

    with tabs[0]:
        users = conn.execute("SELECT * FROM users WHERE role='student' ORDER BY id DESC").fetchall()
        for u in users:
            with st.container(border=True):
                cols = st.columns([3, 1, 1, 1])
                cols[0].write(f"**{u['full_name']}** ({u['email']}) — Trust: {u['trust_score']}  "
                               f"{'🟢 Verified' if u['verified'] else '🟡 Unverified'}  "
                               f"{'🔴 Suspended' if u['is_suspended'] else ''}")
                if u["is_suspended"]:
                    if cols[1].button("Unsuspend", key=f"unsusp_{u['id']}"):
                        conn.execute("UPDATE users SET is_suspended=0 WHERE id=?", (u["id"],))
                        conn.commit()
                        conn.execute(
                            "INSERT INTO admin_actions (admin_id, action, target_id, details, created_at) VALUES (?,?,?,?,?)",
                            (user["id"], "UNSUSPEND_USER", u["id"], "", now_iso()),
                        )
                        conn.commit()
                        st.rerun()
                else:
                    if cols[1].button("Suspend", key=f"susp_{u['id']}"):
                        conn.execute("UPDATE users SET is_suspended=1 WHERE id=?", (u["id"],))
                        conn.execute(
                            "INSERT INTO admin_actions (admin_id, action, target_id, details, created_at) VALUES (?,?,?,?,?)",
                            (user["id"], "SUSPEND_USER", u["id"], "", now_iso()),
                        )
                        conn.commit()
                        st.rerun()

    with tabs[1]:
        disputes = conn.execute(
            "SELECT d.*, u.full_name reporter_name FROM disputes d JOIN users u ON u.id=d.reporter_id ORDER BY d.id DESC"
        ).fetchall()
        for d in disputes:
            with st.container(border=True):
                st.markdown(f"**{d['category']}** — {d['transaction_type']} #{d['transaction_id']} "
                            f"reported by {d['reporter_name']}  {status_badge(d['status'])}", unsafe_allow_html=True)
                st.write(d["description"] or "")
                if d["evidence_path"] and os.path.exists(d["evidence_path"]):
                    st.image(d["evidence_path"], width=200)
                if d["status"] in ("OPEN", "UNDER_REVIEW"):
                    c1, c2, c3 = st.columns(3)
                    if c1.button("Mark Under Review", key=f"dur_{d['id']}"):
                        conn.execute("UPDATE disputes SET status='UNDER_REVIEW' WHERE id=?", (d["id"],))
                        conn.commit(); st.rerun()
                    if c2.button("Resolve", key=f"dres_{d['id']}"):
                        conn.execute("UPDATE disputes SET status='RESOLVED', resolved_at=? WHERE id=?", (now_iso(), d["id"]))
                        conn.commit()
                        conn.execute(
                            "INSERT INTO admin_actions (admin_id, action, target_id, details, created_at) VALUES (?,?,?,?,?)",
                            (user["id"], "RESOLVE_DISPUTE", d["id"], "", now_iso()),
                        )
                        conn.commit()
                        st.rerun()
                    if c3.button("Reject", key=f"drej_{d['id']}"):
                        conn.execute("UPDATE disputes SET status='REJECTED', resolved_at=? WHERE id=?", (now_iso(), d["id"]))
                        conn.commit(); st.rerun()

    with tabs[2]:
        txs = conn.execute("SELECT * FROM transactions ORDER BY id DESC LIMIT 100").fetchall()
        for tx in txs:
            st.markdown(f"#{tx['id']} — ₹{tx['amount']:.0f} — {tx['related_type']} #{tx['related_id']}  {status_badge(tx['status'])}", unsafe_allow_html=True)

    with tabs[3]:
        reqs = conn.execute("SELECT * FROM requests ORDER BY id DESC LIMIT 100").fetchall()
        for r in reqs:
            st.markdown(f"#{r['id']} {r['item_name']}  {status_badge(r['status'])}", unsafe_allow_html=True)

    conn.close()


# =============================================================================
# 7. MAIN ROUTER
# =============================================================================

def render_sidebar(user):
    if user["role"]=="admin":
        st.sidebar.markdown("## 🛡️ UNI HELP")
        st.sidebar.caption("Platform administration")
        st.sidebar.markdown("---")
        st.sidebar.radio("Admin navigation",["Admin Dashboard"])
        st.session_state["nav"]="Admin"
        st.sidebar.markdown("---")
        st.sidebar.caption("Users · requests · disputes · reports · verification")
        if st.sidebar.button("🚪 Logout",use_container_width=True):
            st.session_state["user"]=None; st.session_state["auth_mode"]="Home"; st.rerun()
        return
    st.sidebar.markdown("## 🎓 UNI HELP")
    st.sidebar.caption(f'{user["full_name"]} · {user["email"]}')
    if not user["verified"]: st.sidebar.warning("Email verification required")
    unread=len(get_notifications(user["id"],unread_only=True))
    options=["Dashboard","Delivery","Borrowing","Micro-Tasks",f"Notifications ({unread})" if unread else "Notifications","Wallet","Disputes"]
    clean_map={opt:opt.split(" (")[0] for opt in options}
    current_clean=st.session_state.get("nav","Dashboard")
    display_current=next((o for o in options if clean_map[o]==current_clean),options[0])
    choice=st.sidebar.radio("Navigate",options,index=options.index(display_current))
    st.session_state["nav"]=clean_map[choice]
    st.sidebar.markdown("---")
    if not user["verified"] and st.sidebar.button("📧 Verify Email",use_container_width=True):
        st.session_state["pending_verify_email"]=user["email"]; st.session_state["user"]=None; st.session_state["auth_mode"]="Verify"; st.rerun()
    if st.sidebar.button("🚪 Logout",use_container_width=True):
        st.session_state["user"]=None; st.session_state["auth_mode"]="Home"; st.rerun()


def main():
    user = st.session_state.get("user")

    if user is None:
        mode = st.session_state.get("auth_mode", "Home")
        if mode == "Register":
            render_register()
        elif mode == "Login":
            render_login()
        elif mode == "Verify":
            render_verify()
        else:
            render_landing()
        return

    refresh_current_user()
    user = st.session_state["user"]
    render_sidebar(user)

    nav = st.session_state.get("nav", "Dashboard")
    if user["role"] == "admin":
        nav = "Admin"
        st.session_state["nav"] = "Admin"
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
