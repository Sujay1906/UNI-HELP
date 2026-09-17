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
import math
import io
import time
import re
from email.mime.text import MIMEText
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import streamlit as st
import streamlit.components.v1 as components
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
OTP_EXPIRY_MINUTES = 5
OTP_MAX_ATTEMPTS = int(os.getenv("OTP_MAX_ATTEMPTS", "5"))
LOCATION_RADIUS_METERS = float(os.getenv("LOCATION_RADIUS_METERS", "100"))
QR_EXPIRY_MINUTES = int(os.getenv("QR_EXPIRY_MINUTES", "30"))

ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@unihelp.local")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "AdminUniHelp123!")

MIN_REWARD = float(os.getenv("MIN_REWARD", "0"))
MAX_REWARD = float(os.getenv("MAX_REWARD", "5000"))

for d in (UPLOADS_DIR, QR_DIR, PHOTOS_DIR):
    os.makedirs(d, exist_ok=True)

# =============================================================================
# 1. DATABASE & INITIALIZATION
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
    profile_photo_path TEXT,
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
    accepted_at TEXT,
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

CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_type TEXT NOT NULL,
    reference_id INTEGER NOT NULL,
    sender_id INTEGER NOT NULL REFERENCES users(id),
    message TEXT NOT NULL,
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
                "ADMIN-0",
                generate_password_hash(ADMIN_PASSWORD),
                "admin",
                1,
                100,
                0,
                now_iso(),
            ),
        )
        conn.commit()
    conn.close()

# =============================================================================
# 2. CORE HELPERS: SEED, AUTH & TOKENS
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
            (full_name, email, phone, sid, generate_password_hash("demo1234"), "student", 1, 60, 50, now_iso()),
        )
        created_ids.append(conn.execute("SELECT last_insert_rowid() id").fetchone()["id"])
    conn.commit()

    s1, s2, s3 = created_ids[0], created_ids[1], created_ids[2]

    # Seed delivery requests if empty
    if not conn.execute("SELECT id FROM requests LIMIT 1").fetchone():
        deliveries = [
            (s1, "Data Structures Textbook", "Need reserve copy brought to hostel", "Central Library", "Hostel Block C", 30),
            (s2, "Lab Coat & Safety Glasses", "Left behind in Organic Chem lab", "Chemistry Building", "Hostel Block A", 20),
            (s3, "Canteen Meal Tray", "Veg thali, packed parcel", "Main Canteen", "Engineering Block", 25),
        ]
        for requester, item, desc, pickup, dest, reward in deliveries:
            conn.execute(
                """INSERT INTO requests (requester_id, item_name, description, pickup_location,
                    destination, reward, preferred_time, notes, status, created_at)
                   VALUES (?,?,?,?,?,?,?,?, 'CREATED', ?)""",
                (requester, item, desc, pickup, dest, reward, "Today, before 6 PM", "", now_iso()),
            )

    # Seed borrowable items if empty
    if not conn.execute("SELECT id FROM items LIMIT 1").fetchone():
        items = [
            (s1, "Scientific Calculator FX-991EX", "Electronics", "Solar powered, perfect for exams", "Good", 100),
            (s2, "USB-C Laptop Fast Charger (65W)", "Electronics", "Original OEM adapter", "Excellent", 150),
            (s3, "English Willow Cricket Bat", "Sports", "Lightly oiled, ready for turf matches", "Good", 120),
        ]
        for owner, name, cat, desc, cond, dep in items:
            conn.execute(
                """INSERT INTO items (owner_id, item_name, category, description, condition,
                    availability, deposit, rules, status, created_at)
                   VALUES (?,?,?,?,?, 'Weekdays', ?, 'Return undamaged', 'AVAILABLE', ?)""",
                (owner, name, cat, desc, cond, dep, now_iso()),
            )

    # Seed micro-tasks if empty
    if not conn.execute("SELECT id FROM tasks LIMIT 1").fetchone():
        tasks = [
            (s1, "Collect Courier from Main Gate", "Sign for postal package and bring to room 204", "Main Gate", "Hostel Block C", 25, "Today 5 PM", "Errand"),
            (s2, "Setup Projector for Club Meeting", "Connect HDMI, setup tripod in seminar room", "AV Hub", "Room 402", 35, "Today 6 PM", "Setup"),
            (s3, "Print & Bind Study Notes", "30 pages color printout, spiral bound", "Print Depot", "Block B", 20, "Tomorrow 10 AM", "Errand"),
        ]
        for creator, title, desc, pickup, dest, reward, dead, cat in tasks:
            conn.execute(
                """INSERT INTO tasks (creator_id, title, description, pickup, destination, reward,
                    deadline, category, status, created_at)
                   VALUES (?,?,?,?,?,?,?,?, 'CREATED', ?)""",
                (creator, title, desc, pickup, dest, reward, dead, cat, now_iso()),
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
    row = conn.execute("SELECT * FROM users WHERE lower(email) = ?", (email.strip().lower(),)).fetchone()
    conn.close()
    return row

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
        return False, "No active code found. Generate a new one."

    if row["attempts"] >= row["max_attempts"]:
        conn.close()
        return False, "Too many attempts. Generate a new code."

    if datetime.utcnow() > parse_iso(row["expires_at"]):
        conn.close()
        return False, "Code expired. Request a new one."

    if not check_password_hash(row["otp_hash"], submitted_otp.strip()):
        conn.execute("UPDATE otp_records SET attempts = attempts + 1 WHERE id = ?", (row["id"],))
        conn.commit()
        remaining = row["max_attempts"] - (row["attempts"] + 1)
        conn.close()
        return False, f"Incorrect code. {max(remaining, 0)} attempt(s) left."

    conn.execute("UPDATE otp_records SET used = 1 WHERE id = ?", (row["id"],))
    conn.commit()
    conn.close()
    return True, "Verified successfully."

def create_qr_token(reference_type, reference_id, purpose):
    conn = get_conn()
    conn.execute(
        """UPDATE qr_tokens SET used = 1
           WHERE reference_type = ? AND reference_id = ? AND purpose = ? AND used = 0""",
        (reference_type, reference_id, purpose),
    )
    token = f"UNIHELP|{reference_type}|{reference_id}|{secrets.token_urlsafe(8)}"
    expires_at = (datetime.utcnow() + timedelta(minutes=QR_EXPIRY_MINUTES)).isoformat()
    conn.execute(
        """INSERT INTO qr_tokens (reference_type, reference_id, purpose, token, used, expires_at, created_at)
           VALUES (?,?,?,?,0,?,?)""",
        (reference_type, reference_id, purpose, token, expires_at, now_iso()),
    )
    conn.commit()
    conn.close()
    return token

def verify_qr_token(submitted_token, reference_type, reference_id, purpose):
    conn = get_conn()
    row = conn.execute(
        """SELECT * FROM qr_tokens WHERE token = ? AND reference_type = ?
           AND reference_id = ? AND purpose = ?""",
        (submitted_token.strip(), reference_type, reference_id, purpose),
    ).fetchone()
    if row is None:
        conn.close()
        return False, "Invalid token for this transaction."
    if row["used"]:
        conn.close()
        return False, "Token already used."
    if datetime.utcnow() > parse_iso(row["expires_at"]):
        conn.close()
        return False, "Token expired."
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

def haversine_meters(lat1, lon1, lat2, lon2):
    R = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))

def notify(user_id, message):
    conn = get_conn()
    conn.execute(
        "INSERT INTO notifications (user_id, message, is_read, created_at) VALUES (?,?,0,?)",
        (user_id, message, now_iso()),
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

# =============================================================================
# 3. APP THEME & INTERFACE SETUP
# =============================================================================

st.set_page_config(page_title="UNI HELP — Campus Services", page_icon="🎓", layout="wide")

st.markdown("""
<style>
:root {
  --navy: #091e42;
  --blue: #2563eb;
  --sky: #38bdf8;
  --emerald: #059669;
  --bg-subtle: #f8fafc;
}
.portal-card {
  background: white;
  border-radius: 16px;
  border: 1px solid #e2e8f0;
  padding: 1.25rem;
  box-shadow: 0 4px 12px rgba(15, 23, 42, 0.04);
  margin-bottom: 1rem;
}
.status-pill {
  display: inline-block;
  padding: 0.2rem 0.6rem;
  border-radius: 9999px;
  font-size: 0.72rem;
  font-weight: 700;
  text-transform: uppercase;
}
.pill-held { background: #fef3c7; color: #b45309; }
.pill-released { background: #dcfce7; color: #15803d; }
.pill-active { background: #e0f2fe; color: #0369a1; }
.pill-completed { background: #f0fdf4; color: #166534; }
.pill-disputed { background: #fee2e2; color: #991b1b; }
.admin-badge {
  background: #f1f5f9;
  border: 1px solid #cbd5e1;
  color: #334155;
  padding: 4px 10px;
  border-radius: 8px;
  font-size: 0.75rem;
  font-weight: 600;
}
</style>
""", unsafe_allow_html=True)

init_db()
seed_demo_data()

if "user" not in st.session_state:
    st.session_state["user"] = None
if "auth_mode" not in st.session_state:
    st.session_state["auth_mode"] = "student"  # 'student', 'register', or 'admin_login'
if "active_tab" not in st.session_state:
    st.session_state["active_tab"] = "Delivery"

# =============================================================================
# 4. AUTHENTICATION SCREENS (SEPARATED ADMIN VS STUDENT)
# =============================================================================

def render_admin_login():
    col1, col2, col3 = st.columns([1, 1.8, 1])
    with col2:
        st.markdown("<h2 style='text-align: center;'>🛡️ Campus Administration Portal</h2>", unsafe_allow_html=True)
        st.caption("<div style='text-align:center;'>Restricted Proctor & Moderation Access Only</div>", unsafe_allow_html=True)
        st.write("")
        with st.form("admin_auth_form"):
            admin_email = st.text_input("Admin ID / Email", value=ADMIN_EMAIL)
            admin_pwd = st.text_input("Security Key / Password", type="password", value="AdminUniHelp123!")
            submitted = st.form_submit_button("Enter Moderation Console", use_container_width=True, type="primary")

        if submitted:
            conn = get_conn()
            row = conn.execute("SELECT * FROM users WHERE email=? AND role='admin'", (admin_email.strip().lower(),)).fetchone()
            conn.close()
            if row and check_password_hash(row["password_hash"], admin_pwd):
                st.session_state["user"] = dict(row)
                st.session_state["auth_mode"] = "student"
                st.rerun()
            else:
                st.error("Access denied. Unauthorized credentials.")

        st.write("")
        if st.button("← Return to Student Network", use_container_width=True):
            st.session_state["auth_mode"] = "student"
            st.rerun()

def render_student_login():
    col1, col2, col3 = st.columns([1, 2.2, 1])
    with col2:
        st.markdown("<h1 style='text-align:center; color:#0f172a; margin-bottom:0;'>🎓 UNI HELP</h1>", unsafe_allow_html=True)
        st.markdown("<p style='text-align:center; color:#64748b;'>The University Verified Micro-Service Network</p>", unsafe_allow_html=True)
        st.write("")

        # Interactive quick switch for Hackathon / Presentation demo
        with st.container(border=True):
            st.markdown("##### ⚡ Quick Student Switcher (Demo Mode)")
            st.caption("Select a demo student account to sign in immediately:")
            s_cols = st.columns(3)
            conn = get_conn()
            s_rows = conn.execute("SELECT * FROM users WHERE role='student' LIMIT 3").fetchall()
            conn.close()

            for idx, s in enumerate(s_rows):
                with s_cols[idx]:
                    if st.button(f"👤 {s['full_name'].split()[0]}", key=f"quick_s_{s['id']}", use_container_width=True):
                        st.session_state["user"] = dict(s)
                        st.rerun()

        st.write("")
        with st.container(border=True):
            st.markdown("##### 🔐 Registered Student Sign In")
            sid_input = st.text_input("University Email or Student ID", placeholder="aarav.sharma@student.university.edu")
            pwd_input = st.text_input("Password", type="password", placeholder="••••••••")

            if st.button("Log In as Student", use_container_width=True, type="primary"):
                conn = get_conn()
                u_row = conn.execute(
                    "SELECT * FROM users WHERE (lower(email)=? OR student_id=?) AND role='student'",
                    (sid_input.strip().lower(), sid_input.strip())
                ).fetchone()
                conn.close()

                if u_row and check_password_hash(u_row["password_hash"], pwd_input):
                    st.session_state["user"] = dict(u_row)
                    st.rerun()
                else:
                    st.error("Invalid credentials. Try using a quick switcher account above or check password.")

            st.divider()
            if st.button("Create New Student Account", use_container_width=True):
                st.session_state["auth_mode"] = "register"
                st.rerun()

        # Discreet Footer link for Admin Access
        st.write("")
        st.write("")
        st.markdown(
            "<div style='text-align:center;'><small style='color:#94a3b8;'>Campus Proctor or Staff? </small></div>",
            unsafe_allow_html=True
        )
        if st.button("Access University Admin Portal", use_container_width=True):
            st.session_state["auth_mode"] = "admin_login"
            st.rerun()

def render_registration():
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.markdown("### 🎓 Register New Student Account")
        with st.form("student_registration"):
            name = st.text_input("Full Name", placeholder="Rahul Verma")
            email = st.text_input("University Email", placeholder="r.verma@student.university.edu")
            phone = st.text_input("Phone Number", placeholder="9876543210")
            student_id = st.text_input("Student ID (8 digits)", placeholder="12645890")
            pw1 = st.text_input("Password", type="password")
            pw2 = st.text_input("Confirm Password", type="password")
            reg_submit = st.form_submit_button("Submit & Receive Verification OTP", use_container_width=True, type="primary")

        if reg_submit:
            if not email.endswith(UNIVERSITY_EMAIL_DOMAIN):
                st.error(f"Email must end with your university domain: {UNIVERSITY_EMAIL_DOMAIN}")
            elif pw1 != pw2 or len(pw1) < 6:
                st.error("Passwords must match and have at least 6 characters.")
            else:
                conn = get_conn()
                try:
                    conn.execute(
                        """INSERT INTO users (full_name, email, phone, student_id, password_hash, role, verified, trust_score, unicoins, created_at)
                           VALUES (?, ?, ?, ?, ?, 'student', 1, 50, 50, ?)""",
                        (name.strip(), email.strip().lower(), phone.strip(), student_id.strip(), generate_password_hash(pw1), now_iso())
                    )
                    uid = conn.execute("SELECT last_insert_rowid() id").fetchone()["id"]
                    conn.commit()
                    add_unicoins(uid, 50, "Welcome bonus")
                    st.success("Account registered and verified with 50 UniCoins welcome reward! Please log in.")
                    st.session_state["auth_mode"] = "student"
                except sqlite3.IntegrityError:
                    st.error("An account with that email or Student ID already exists.")
                finally:
                    conn.close()

        if st.button("← Back to Student Login", use_container_width=True):
            st.session_state["auth_mode"] = "student"
            st.rerun()

# =============================================================================
# 5. ADMIN CONSOLE (ISOLATED WORKSPACE)
# =============================================================================

def render_admin_portal(user):
    # Top Admin bar
    top_c1, top_c2 = st.columns([3, 1])
    with top_c1:
        st.markdown(f"### 🛡️ Campus Administration Workspace")
        st.caption(f"Authenticated Officer: **{user['full_name']}** ({user['email']})")
    with top_c2:
        if st.button("🚪 Logout of Admin Portal", type="secondary", use_container_width=True):
            st.session_state["user"] = None
            st.session_state["auth_mode"] = "student"
            st.rerun()

    st.divider()

    conn = get_conn()
    students_count = conn.execute("SELECT COUNT(*) c FROM users WHERE role='student'").fetchone()["c"]
    open_disputes = conn.execute("SELECT COUNT(*) c FROM disputes WHERE status IN ('OPEN','UNDER_REVIEW')").fetchone()["c"]
    held_escrow = conn.execute("SELECT COALESCE(SUM(amount), 0) s FROM transactions WHERE status='HELD'").fetchone()["s"]
    active_deliveries = conn.execute("SELECT COUNT(*) c FROM requests WHERE status NOT IN ('COMPLETED', 'CANCELLED')").fetchone()["c"]
    conn.close()

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Verified Students", students_count)
    m2.metric("Open Disputes", open_disputes)
    m3.metric("Escrow in Holding", f"₹{held_escrow:.0f}")
    m4.metric("Active Campus Runs", active_deliveries)

    adm_tab1, adm_tab2, adm_tab3 = st.tabs(["⚠️ Dispute Arbitration Queue", "👥 Student Registry", "💰 Financial Escrow Audit"])

    with adm_tab1:
        conn = get_conn()
        disputes = conn.execute(
            """SELECT d.*, u.full_name reporter_name FROM disputes d
               JOIN users u ON u.id = d.reporter_id ORDER BY d.id DESC"""
        ).fetchall()
        conn.close()

        if not disputes:
            st.success("No disputes currently logged.")
        for d in disputes:
            with st.container(border=True):
                st.markdown(f"**Dispute #{d['id']} — {d['category']}** on {d['transaction_type']} #{d['transaction_id']}")
                st.caption(f"Reported by: **{d['reporter_name']}** | Status: `{d['status']}`")
                st.write(d["description"] or "No detailed description.")

                if d["status"] in ("OPEN", "UNDER_REVIEW"):
                    b1, b2, b3 = st.columns(3)
                    if b1.button("Mark Under Investigation", key=f"inv_{d['id']}"):
                        conn = get_conn()
                        conn.execute("UPDATE disputes SET status='UNDER_REVIEW' WHERE id=?", (d["id"],))
                        conn.commit(); conn.close()
                        st.rerun()
                    if b2.button("Resolve & Release Escrow", key=f"res_{d['id']}"):
                        conn = get_conn()
                        conn.execute("UPDATE disputes SET status='RESOLVED', resolved_at=? WHERE id=?", (now_iso(), d["id"]))
                        conn.commit(); conn.close()
                        update_transaction_status(d["transaction_type"], d["transaction_id"], "RELEASED")
                        st.success("Dispute resolved.")
                        st.rerun()
                    if b3.button("Dismiss & Refund", key=f"rej_{d['id']}"):
                        conn = get_conn()
                        conn.execute("UPDATE disputes SET status='REJECTED', resolved_at=? WHERE id=?", (now_iso(), d["id"]))
                        conn.commit(); conn.close()
                        update_transaction_status(d["transaction_type"], d["transaction_id"], "CANCELLED")
                        st.info("Dispute dismissed.")
                        st.rerun()

    with adm_tab2:
        conn = get_conn()
        students = conn.execute("SELECT id, full_name, email, student_id, trust_score, unicoins, is_suspended FROM users WHERE role='student'").fetchall()
        conn.close()
        for s in students:
            c1, c2, c3, c4 = st.columns([3, 2, 2, 2])
            c1.write(f"**{s['full_name']}** (`{s['student_id']}`)")
            c2.write(f"Trust: **{s['trust_score']}/100**")
            c3.write(f"UniCoins: **{s['unicoins']}**")
            if s["is_suspended"]:
                if c4.button("Unsuspend", key=f"unsusp_{s['id']}"):
                    conn = get_conn()
                    conn.execute("UPDATE users SET is_suspended=0 WHERE id=?", (s["id"],))
                    conn.commit(); conn.close(); st.rerun()
            else:
                if c4.button("Suspend", key=f"susp_{s['id']}"):
                    conn = get_conn()
                    conn.execute("UPDATE users SET is_suspended=1 WHERE id=?", (s["id"],))
                    conn.commit(); conn.close(); st.rerun()

    with adm_tab3:
        conn = get_conn()
        txs = conn.execute("SELECT * FROM transactions ORDER BY id DESC LIMIT 50").fetchall()
        conn.close()
        if not txs:
            st.caption("No ledger transactions recorded yet.")
        for tx in txs:
            st.markdown(f"TXN `#{tx['id']}` — Amount: **₹{tx['amount']:.0f}** | Context: **{tx['related_type']} #{tx['related_id']}** | Status: `{tx['status']}`")

# =============================================================================
# 6. STUDENT WORKSPACE & MODULES
# =============================================================================

def render_student_portal(user):
    # Student Header with profile status and quick user switcher for seamless testing
    header_col1, header_col2 = st.columns([2.5, 1.5])
    with header_col1:
        st.markdown(f"### 🎓 UNI HELP")
        st.caption(f"Signed in as **{user['full_name']}** (`{user['student_id']}`) • Trust Score: **{user['trust_score']}/100** • 🪙 **{user['unicoins']} UniCoins**")
    with header_col2:
        switch_col, out_col = st.columns([2, 1])
        with switch_col:
            # Dropdown switcher solely between demo students
            conn = get_conn()
            other_students = conn.execute("SELECT id, full_name FROM users WHERE role='student'").fetchall()
            conn.close()
            opts = {s["id"]: s["full_name"] for s in other_students}
            chosen = st.selectbox("Switch Student View", options=list(opts.keys()), format_func=lambda x: opts[x], index=list(opts.keys()).index(user["id"]) if user["id"] in opts else 0)
            if chosen != user["id"]:
                st.session_state["user"] = dict(user_by_id(chosen))
                st.rerun()
        with out_col:
            if st.button("Logout", key="student_logout_btn"):
                st.session_state["user"] = None
                st.rerun()

    st.write("")
    nav_tabs = st.tabs(["📦 Delivery Hub", "🤝 Borrowing Hub", "🛠 Micro-Tasks", "💰 Virtual Wallet & UniCoins", "⚠️ Disputes"])

    # ---------------- 6.1 DELIVERY HUB ----------------
    with nav_tabs[0]:
        st.markdown("#### Campus Delivery Requests")
        deliv_action = st.radio("Mode", ["Active Requests", "Create Delivery Request"], horizontal=True, label_visibility="collapsed")

        if deliv_action == "Create Delivery Request":
            with st.form("new_delivery_form"):
                item_name = st.text_input("Item Name", placeholder="e.g., Assignment Sheets / Parcel from Gate")
                description = st.text_area("Details & Instructions", placeholder="Pick up from security desk and deliver to room 302")
                p1, p2 = st.columns(2)
                pickup_loc = p1.text_input("Pickup Point", value="Main Gate")
                drop_loc = p2.text_input("Destination Point", value="Hostel Block C")
                reward = st.number_input("Reward (₹ UniCoins/Simulated)", min_value=10.0, max_value=500.0, value=30.0, step=5.0)
                sub = st.form_submit_button("Post Request to Campus", type="primary")

                if sub:
                    conn = get_conn()
                    conn.execute(
                        """INSERT INTO requests (requester_id, item_name, description, pickup_location, destination, reward, status, created_at)
                           VALUES (?, ?, ?, ?, ?, ?, 'CREATED', ?)""",
                        (user["id"], item_name.strip(), description.strip(), pickup_loc.strip(), drop_loc.strip(), reward, now_iso())
                    )
                    conn.commit(); conn.close()
                    st.success("Delivery request posted!")
                    st.rerun()
        else:
            conn = get_conn()
            requests = conn.execute(
                """SELECT r.*, u.full_name requester_name FROM requests r
                   JOIN users u ON u.id = r.requester_id
                   WHERE r.status != 'CANCELLED' ORDER BY r.id DESC"""
            ).fetchall()
            conn.close()

            if not requests:
                st.info("No active delivery runs right now.")
            for r in requests:
                with st.container(border=True):
                    is_owner = (r["requester_id"] == user["id"])
                    is_helper = (r["helper_id"] == user["id"])

                    st.markdown(f"**{r['item_name']}** — ₹{r['reward']:.0f} Reward | Status: `<span class='status-pill pill-active'>{r['status']}</span>`", unsafe_allow_html=True)
                    st.caption(f"📍 {r['pickup_location']} ➔ {r['destination']} | Requester: **{r['requester_name']}**")
                    if r["description"]:
                        st.write(r["description"])

                    # Delivery Flow Steps
                    if r["status"] == "CREATED" and not is_owner:
                        if st.button("Accept Delivery Run", key=f"acc_del_{r['id']}"):
                            conn = get_conn()
                            conn.execute("UPDATE requests SET helper_id=?, status='ACCEPTED', accepted_at=? WHERE id=?", (user["id"], now_iso(), r["id"]))
                            conn.commit(); conn.close()
                            create_transaction(r["requester_id"], user["id"], "DELIVERY", r["id"], r["reward"], "HELD")
                            notify(r["requester_id"], f"{user['full_name']} accepted your delivery request: {r['item_name']}")
                            st.rerun()

                    elif r["status"] == "ACCEPTED":
                        if is_owner:
                            st.info("Helper has accepted. Show this OTP or QR code to the helper upon item handover.")
                            c_otp, c_qr = st.columns(2)
                            with c_otp:
                                if st.button("Generate Handover OTP", key=f"gen_otp_{r['id']}"):
                                    otp = create_otp(user["id"], "DELIVERY_PICKUP", r["id"])
                                    st.success(f"One-Time Code: **{otp}**")
                            with c_qr:
                                if st.button("Generate Handover QR", key=f"gen_qr_{r['id']}"):
                                    tok = create_qr_token("DELIVERY", r["id"], "DELIVERY_PICKUP")
                                    st.image(generate_qr_bytes(tok), width=140)
                                    st.caption(f"Token: `{tok}`")
                        elif is_helper:
                            st.markdown("##### 🔑 Confirm Pickup with Requester")
                            method = st.radio("Verification Method", ["Enter OTP", "Enter QR Token"], horizontal=True, key=f"v_m_{r['id']}")
                            code_in = st.text_input("Verification Code", key=f"code_in_{r['id']}")

                            # Location simulation
                            geo_check = st.checkbox("Simulate Geofence Check (<100m Radius)", value=True, key=f"geo_{r['id']}")

                            if st.button("Verify Pickup", key=f"v_sub_{r['id']}"):
                                if not geo_check:
                                    st.error("Distance check failed: You must be within 100 meters of the pickup location.")
                                else:
                                    verified = False
                                    if method == "Enter OTP":
                                        ok, msg = verify_otp(r["requester_id"], "DELIVERY_PICKUP", r["id"], code_in)
                                        verified = ok
                                    else:
                                        ok, msg = verify_qr_token(code_in, "DELIVERY", r["id"], "DELIVERY_PICKUP")
                                        verified = ok

                                    if verified:
                                        conn = get_conn()
                                        conn.execute("UPDATE requests SET status='PICKUP_VERIFIED', pickup_verified_at=? WHERE id=?", (now_iso(), r["id"]))
                                        conn.commit(); conn.close()
                                        st.success("Pickup verified! Delivery status updated to In-Transit.")
                                        st.rerun()
                                    else:
                                        st.error(msg)

                    elif r["status"] == "PICKUP_VERIFIED" and is_helper:
                        if st.button("Mark Item as Delivered", key=f"mark_deliv_{r['id']}"):
                            conn = get_conn()
                            conn.execute("UPDATE requests SET status='DELIVERED', delivered_at=? WHERE id=?", (now_iso(), r["id"]))
                            conn.commit(); conn.close()
                            st.rerun()

                    elif r["status"] == "DELIVERED" and is_owner:
                        st.info("Your helper marked this item delivered. Confirm to release the reward escrow.")
                        if st.button("Confirm Handover & Release Escrow", key=f"conf_deliv_{r['id']}", type="primary"):
                            conn = get_conn()
                            conn.execute("UPDATE requests SET status='COMPLETED', completed_at=? WHERE id=?", (now_iso(), r["id"]))
                            conn.commit(); conn.close()
                            update_transaction_status("DELIVERY", r["id"], "RELEASED")
                            add_unicoins(r["helper_id"], 20, f"Delivery completion reward for #{r['id']}")
                            notify(r["helper_id"], f"Delivery #{r['id']} completed! Escrow released.")
                            st.success("Completed! Escrow funds released.")
                            st.rerun()

    # ---------------- 6.2 BORROWING HUB ----------------
    with nav_tabs[1]:
        st.markdown("#### Peer-to-Peer Borrowing Hub")
        borrow_view = st.radio("Borrow Mode", ["Available Items", "List My Item"], horizontal=True, label_visibility="collapsed")

        if borrow_view == "List My Item":
            with st.form("new_item_form"):
                it_name = st.text_input("Item Name", placeholder="e.g., Casio Scientific Calculator")
                it_cat = st.selectbox("Category", ["Electronics", "Books", "Sports", "Lab Equipment", "Other"])
                it_cond = st.selectbox("Condition", ["Like New", "Good", "Fair"])
                it_dep = st.number_input("Security Deposit (₹)", min_value=0.0, value=50.0, step=10.0)
                it_sub = st.form_submit_button("List for Borrowing", type="primary")

                if it_sub:
                    conn = get_conn()
                    conn.execute(
                        """INSERT INTO items (owner_id, item_name, category, condition, deposit, status, created_at)
                           VALUES (?, ?, ?, ?, ?, 'AVAILABLE', ?)""",
                        (user["id"], it_name.strip(), it_cat, it_cond, it_dep, now_iso())
                    )
                    conn.commit(); conn.close()
                    st.success("Item listed!")
                    st.rerun()
        else:
            conn = get_conn()
            items = conn.execute(
                """SELECT i.*, u.full_name owner_name FROM items i
                   JOIN users u ON u.id = i.owner_id WHERE i.status='AVAILABLE'"""
            ).fetchall()
            conn.close()

            if not items:
                st.info("No items listed right now.")
            for it in items:
                with st.container(border=True):
                    st.markdown(f"**{it['item_name']}** ({it['category']}) — Deposit: **₹{it['deposit']:.0f}**")
                    st.caption(f"Owner: **{it['owner_name']}** | Condition: {it['condition']}")
                    if it["owner_id"] != user["id"]:
                        if st.button("Request to Borrow", key=f"req_it_{it['id']}"):
                            conn = get_conn()
                            conn.execute(
                                """INSERT INTO borrowings (item_id, owner_id, borrower_id, deposit, status, created_at)
                                   VALUES (?, ?, ?, ?, 'REQUESTED', ?)""",
                                (it["id"], it["owner_id"], user["id"], it["deposit"], now_iso())
                            )
                            conn.execute("UPDATE items SET status='BORROWED' WHERE id=?", (it["id"],))
                            conn.commit(); conn.close()
                            create_transaction(user["id"], it["owner_id"], "BORROW_DEPOSIT", it["id"], it["deposit"], "HELD")
                            st.success("Borrow request dispatched!")
                            st.rerun()

    # ---------------- 6.3 MICRO-TASKS ----------------
    with nav_tabs[2]:
        st.markdown("#### Campus Micro-Tasks & Quick Gigs")
        conn = get_conn()
        tasks = conn.execute(
            """SELECT t.*, u.full_name creator_name FROM tasks t
               JOIN users u ON u.id = t.creator_id ORDER BY t.id DESC"""
        ).fetchall()
        conn.close()

        for t in tasks:
            with st.container(border=True):
                st.markdown(f"**{t['title']}** — ₹{t['reward']:.0f} | Status: `{t['status']}`")
                st.caption(f"Category: {t['category']} | Posted by: **{t['creator_name']}** | Deadline: {t['deadline']}")
                st.write(t["description"])

                if t["status"] == "CREATED" and t["creator_id"] != user["id"]:
                    if st.button("Accept Task & Claim Escrow", key=f"acc_t_{t['id']}"):
                        conn = get_conn()
                        conn.execute("UPDATE tasks SET helper_id=?, status='ACCEPTED', accepted_at=? WHERE id=?", (user["id"], now_iso(), t["id"]))
                        conn.commit(); conn.close()
                        create_transaction(t["creator_id"], user["id"], "TASK", t["id"], t["reward"], "HELD")
                        st.rerun()

                elif t["status"] == "ACCEPTED" and t["helper_id"] == user["id"]:
                    if st.button("Complete Task", key=f"done_t_{t['id']}"):
                        conn = get_conn()
                        conn.execute("UPDATE tasks SET status='COMPLETED', completed_at=? WHERE id=?", (now_iso(), t["id"]))
                        conn.commit(); conn.close()
                        update_transaction_status("TASK", t["id"], "RELEASED")
                        add_unicoins(user["id"], 15, f"Completed task #{t['id']}")
                        st.success("Task completed and funds released from escrow!")
                        st.rerun()

    # ---------------- 6.4 VIRTUAL WALLET ----------------
    with nav_tabs[3]:
        st.markdown("#### Virtual Wallet & UniCoins Ledger")
        conn = get_conn()
        released = conn.execute("SELECT COALESCE(SUM(amount), 0) s FROM transactions WHERE payee_id=? AND status='RELEASED'", (user["id"],)).fetchone()["s"]
        held = conn.execute("SELECT COALESCE(SUM(amount), 0) s FROM transactions WHERE payee_id=? AND status='HELD'", (user["id"],)).fetchone()["s"]
        tx_logs = conn.execute("SELECT * FROM transactions WHERE payer_id=? OR payee_id=? ORDER BY id DESC", (user["id"], user["id"])).fetchall()
        conn.close()

        w1, w2, w3 = st.columns(3)
        w1.metric("UniCoins Balance", f"🪙 {user['unicoins']}")
        w2.metric("Released Earnings", f"₹{released:.0f}")
        w3.metric("Held in Escrow", f"₹{held:.0f}")

        st.caption("Transactions are simulated internally via UniCoins/Escrow prototype.")
        st.markdown("##### Recent Transaction Ledger")
        for tx in tx_logs:
            role = "Paid" if tx["payer_id"] == user["id"] else "Received"
            st.write(f"• **{role} ₹{tx['amount']:.0f}** for `{tx['related_type']} #{tx['related_id']}` — Status: `{tx['status']}` ({tx['created_at'][:16]})")

    # ---------------- 6.5 DISPUTES ----------------
    with nav_tabs[4]:
        st.markdown("#### Raise a Community Dispute")
        with st.form("dispute_submission"):
            tx_type = st.selectbox("Transaction Source", ["DELIVERY", "BORROWING", "TASK"])
            ref_id = st.number_input("Transaction / Request ID", min_value=1, step=1)
            cat = st.selectbox("Issue Category", ["Item Damaged", "No-show / Abandoned", "Incomplete Work", "Other"])
            desc = st.text_area("Detailed Explanation")
            sub_d = st.form_submit_button("Submit Dispute to Administration", type="primary")

            if sub_d:
                conn = get_conn()
                conn.execute(
                    """INSERT INTO disputes (transaction_type, transaction_id, reporter_id, category, description, status, created_at)
                       VALUES (?, ?, ?, ?, ?, 'OPEN', ?)""",
                    (tx_type, ref_id, user["id"], cat, desc.strip(), now_iso())
                )
                conn.commit(); conn.close()
                update_transaction_status(tx_type, ref_id, "DISPUTED")
                st.success("Dispute filed. Escrow has been frozen pending proctor arbitration.")
                st.rerun()

# =============================================================================
# 7. MAIN CONTROLLER
# =============================================================================

def main():
    user = st.session_state.get("user")

    if user is None:
        auth_mode = st.session_state.get("auth_mode", "student")
        if auth_mode == "admin_login":
            render_admin_login()
        elif auth_mode == "register":
            render_registration()
        else:
            render_student_login()
        return

    # Route based on role
    if user.get("role") == "admin":
        render_admin_portal(user)
    else:
        render_student_portal(user)

if __name__ == "__main__":
    main()

