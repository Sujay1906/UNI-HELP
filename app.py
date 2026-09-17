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
OTP_EXPIRY_MINUTES = 5
OTP_MAX_ATTEMPTS = int(os.getenv("OTP_MAX_ATTEMPTS", "5"))
PASSWORD_RESET_EXPIRY_HOURS = 2

ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@unihelp.local")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "AdminUniHelp123!")

# SMTP setup (Uses Streamlit Secrets or Environment Variables)
try:
    SMTP_HOST = str(st.secrets.get("SMTP_HOST", os.getenv("SMTP_HOST", "smtp.gmail.com"))).strip()
    SMTP_PORT = int(st.secrets.get("SMTP_PORT", os.getenv("SMTP_PORT", 587)))
    SMTP_USERNAME = str(st.secrets.get("SMTP_USERNAME", os.getenv("SMTP_USERNAME", ""))).strip()
    SMTP_PASSWORD = str(st.secrets.get("SMTP_PASSWORD", os.getenv("SMTP_PASSWORD", ""))).strip()
    APP_URL = str(st.secrets.get("APP_URL", os.getenv("APP_URL", "http://localhost:8501"))).strip().rstrip("/")
except Exception:
    SMTP_HOST = "smtp.gmail.com"
    SMTP_PORT = 587
    SMTP_USERNAME = ""
    SMTP_PASSWORD = ""
    APP_URL = "http://localhost:8501"

EMAIL_CONFIGURED = bool(SMTP_HOST and SMTP_PORT and SMTP_USERNAME and SMTP_PASSWORD)

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

CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER NOT NULL REFERENCES users(id),
    item_name TEXT NOT NULL,
    category TEXT,
    condition TEXT,
    deposit REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'AVAILABLE',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS borrowings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL REFERENCES items(id),
    owner_id INTEGER NOT NULL REFERENCES users(id),
    borrower_id INTEGER NOT NULL REFERENCES users(id),
    deposit REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'REQUESTED',
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
# 2. EMAIL & TOKEN UTILITIES
# =============================================================================

def send_realtime_email(to_email, subject, body):
    """Sends a real-time email via SMTP if configured; saves last status in session."""
    if not EMAIL_CONFIGURED:
        st.session_state["_last_email_simulated"] = (to_email, subject, body)
        return True
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = SMTP_USERNAME
        msg["To"] = to_email
        context = ssl.create_default_context()
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.sendmail(SMTP_USERNAME, [to_email], msg.as_string())
        return True
    except Exception as e:
        st.session_state["_last_email_error"] = str(e)
        st.session_state["_last_email_simulated"] = (to_email, subject, body)
        return False

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
        return None, "Empty token provided."
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
        return None, "Password reset link has expired."
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
        f"This code will expire in {OTP_EXPIRY_MINUTES} minutes. If you did not attempt to sign in, "
        f"please secure your account immediately.\n\n"
        f"— UNI HELP Security Team"
    )
    send_realtime_email(user_row["email"], subject, body)
    return otp

def send_password_reset_email(user_row):
    raw_token = create_password_reset_token(user_row["id"])
    reset_link = f"{APP_URL}/?reset_token={raw_token}"
    subject = "UNI HELP — Password Reset Request"
    body = (
        f"Hello {user_row['full_name']},\n\n"
        f"We received a request to reset your password for your UNI HELP student account.\n\n"
        f"Click the link below to set a new password:\n{reset_link}\n\n"
        f"Or use your manual reset token:\n{raw_token}\n\n"
        f"This link is valid for {PASSWORD_RESET_EXPIRY_HOURS} hours. If you did not make this request, "
        f"you can safely ignore this email.\n\n"
        f"— UNI HELP Support"
    )
    send_realtime_email(user_row["email"], subject, body)
    return raw_token

# =============================================================================
# 3. SEEDING & RECORD HELPERS
# =============================================================================

DEMO_STUDENTS = [
    ("Aarav Sharma", "aarav.sharma", "9990001111", "STU1001"),
    ("Priya Nair", "priya.nair", "9990002222", "STU1002"),
    ("Rohan Mehta", "rohan.mehta", "9990003333", "STU1003"),
]

def seed_demo_data():
    conn = get_conn()
    for full_name, local, phone, sid in DEMO_STUDENTS:
        email = f"{local}{UNIVERSITY_EMAIL_DOMAIN}"
        existing = conn.execute("SELECT id FROM users WHERE email = ? OR student_id = ?", (email, sid)).fetchone()
        if not existing:
            conn.execute(
                """INSERT INTO users (full_name, email, phone, student_id, password_hash, role,
                    verified, trust_score, unicoins, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (full_name, email, phone, sid, generate_password_hash("demo1234"), "student", 1, 65, 50, now_iso()),
            )
    conn.commit()

    # Seed delivery requests if empty
    if not conn.execute("SELECT id FROM requests LIMIT 1").fetchone():
        s1 = conn.execute("SELECT id FROM users WHERE student_id='STU1001'").fetchone()["id"]
        s2 = conn.execute("SELECT id FROM users WHERE student_id='STU1002'").fetchone()["id"]
        conn.execute(
            """INSERT INTO requests (requester_id, item_name, description, pickup_location,
                destination, reward, preferred_time, status, created_at)
               VALUES (?, 'Data Structures Textbook', 'Need library reserve copy brought to Block C',
               'Central Library', 'Hostel Block C', 35, 'Today, by 6 PM', 'CREATED', ?)""",
            (s1, now_iso()),
        )
        conn.execute(
            """INSERT INTO requests (requester_id, item_name, description, pickup_location,
                destination, reward, preferred_time, status, created_at)
               VALUES (?, 'Lab Coat & Safety Goggles', 'Left behind in Chemistry Lab 3',
               'Chemistry Department', 'Hostel Block A', 25, 'Evening', 'CREATED', ?)""",
            (s2, now_iso()),
        )
        conn.commit()

    conn.close()

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
# 4. STREAMLIT PAGE CONFIG & GLOBAL STATE
# =============================================================================

st.set_page_config(page_title="UNI HELP — Campus Services", page_icon="🎓", layout="wide")

init_db()
seed_demo_data()

# Handle reset token from query parameter if present
query_params = st.query_params
if "reset_token" in query_params and "auth_mode" not in st.session_state:
    st.session_state["auth_mode"] = "reset_password"
    st.session_state["active_reset_token"] = query_params["reset_token"]

if "user" not in st.session_state:
    st.session_state["user"] = None
if "auth_mode" not in st.session_state:
    st.session_state["auth_mode"] = "student_login"  # 'student_login', 'student_otp', 'forgot_password', 'reset_password', 'register', 'admin_login'

# Helper for simulated email display notification when SMTP is off
def show_simulated_email_banner():
    if "_last_email_simulated" in st.session_state:
        to_addr, subj, body = st.session_state["_last_email_simulated"]
        with st.expander(f"📬 Real-time Dispatch Preview (Sent to: {to_addr})", expanded=True):
            st.caption(f"**Subject:** {subj}")
            st.code(body, language="text")

# =============================================================================
# 5. AUTHENTICATION MODULES
# =============================================================================

# --- 5.1 STUDENT LOGIN (STEP 1: STUDENT ID + PASSWORD) ---
def render_student_login():
    col1, col2, col3 = st.columns([1, 2.2, 1])
    with col2:
        st.markdown("<h1 style='text-align:center;'>🎓 UNI HELP</h1>", unsafe_allow_html=True)
        st.markdown("<p style='text-align:center; color:#64748b;'>Verified Student-to-Student Campus Assistance Network</p>", unsafe_allow_html=True)
        st.write("")

        # Demo Quick Select Helper
        with st.container(border=True):
            st.markdown("##### ⚡ Quick Demo Accounts")
            st.caption("Default password for all demo accounts: `demo1234`")
            q_cols = st.columns(3)
            q_cols[0].info("**STU1001**\nAarav S.")
            q_cols[1].info("**STU1002**\nPriya N.")
            q_cols[2].info("**STU1003**\nRohan M.")

        with st.container(border=True):
            st.markdown("##### 🔐 Student Authentication")
            st.caption("Step 1 of 2: Verify Credentials")

            sid = st.text_input("Student ID", placeholder="e.g. STU1001", key="login_sid")
            pwd = st.text_input("Password", type="password", placeholder="••••••••", key="login_pwd")

            c_btn1, c_btn2 = st.columns([1.5, 1])
            with c_btn1:
                send_otp_btn = st.button("Verify & Send Email OTP →", use_container_width=True, type="primary")
            with c_btn2:
                if st.button("Forgot Password?", use_container_width=True):
                    st.session_state["auth_mode"] = "forgot_password"
                    st.rerun()

            if send_otp_btn:
                if not sid.strip() or not pwd:
                    st.error("Please provide both Student ID and Password.")
                else:
                    user = user_by_student_id_or_email(sid)
                    if user and check_password_hash(user["password_hash"], pwd):
                        if user["is_suspended"]:
                            st.error("This student account is currently suspended. Please contact the campus proctor.")
                        else:
                            st.session_state["pending_student_user"] = dict(user)
                            # Generate and send real-time OTP to student's email
                            send_login_otp_email(user)
                            st.session_state["auth_mode"] = "student_otp"
                            st.rerun()
                    else:
                        st.error("Invalid Student ID or password. Please try again.")

            st.divider()
            if st.button("Register New Student Account", use_container_width=True):
                st.session_state["auth_mode"] = "register"
                st.rerun()

        # Discreet Gateway to University Admin Portal
        st.write("")
        st.markdown(
            "<div style='text-align:center;'><small style='color:#94a3b8;'>Authorized University Staff or Proctor? </small></div>",
            unsafe_allow_html=True,
        )
        if st.button("Access University Admin Portal", use_container_width=True):
            st.session_state["auth_mode"] = "admin_login"
            st.rerun()

# --- 5.2 STUDENT LOGIN (STEP 2: EMAIL OTP VERIFICATION) ---
def render_student_otp():
    user = st.session_state.get("pending_student_user")
    if not user:
        st.session_state["auth_mode"] = "student_login"
        st.rerun()

    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.markdown("### 📩 Security Check: Enter OTP")
        st.caption(f"Step 2 of 2: We sent a 6-digit real-time verification code to **{user['email']}**")

        show_simulated_email_banner()

        otp_val = st.text_input("Enter 6-Digit Email OTP", max_chars=6, placeholder="123456", key="login_otp_input")

        c1, c2 = st.columns([1.5, 1])
        with c1:
            if st.button("Verify Code & Log In", use_container_width=True, type="primary"):
                ok, msg = verify_otp(user["id"], "STUDENT_LOGIN_OTP", None, otp_val)
                if ok:
                    st.session_state["user"] = user
                    st.session_state.pop("pending_student_user", None)
                    st.session_state.pop("_last_email_simulated", None)
                    st.session_state["auth_mode"] = "student_login"
                    st.success("Verification successful! Welcome back.")
                    st.rerun()
                else:
                    st.error(msg)
        with c2:
            if st.button("Resend Code", use_container_width=True):
                send_login_otp_email(user)
                st.info("A fresh OTP has been dispatched to your email.")
                st.rerun()

        st.write("")
        if st.button("← Back to Student Sign In", use_container_width=True):
            st.session_state.pop("pending_student_user", None)
            st.session_state["auth_mode"] = "student_login"
            st.rerun()

# --- 5.3 FORGOT PASSWORD REQUEST ---
def render_forgot_password():
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.markdown("### 🔑 Recover Account Password")
        st.caption("Enter your Student ID or registered university email. UNI HELP will send a secure password reset link to your inbox.")

        identifier = st.text_input("Student ID or University Email", placeholder="STU1001 or aarav.sharma@student.university.edu")

        if st.button("Send Reset Link to Email", use_container_width=True, type="primary"):
            if not identifier.strip():
                st.error("Please enter your Student ID or email.")
            else:
                user = user_by_student_id_or_email(identifier)
                if user:
                    send_password_reset_email(user)
                    st.success(f"A password reset link has been dispatched to **{user['email']}**.")
                else:
                    # Provide generic success message to prevent user enumeration
                    st.info("If that account is registered in our system, a reset link has been sent.")

        show_simulated_email_banner()

        st.write("")
        st.divider()
        st.markdown("##### Already have your reset token?")
        manual_tok = st.text_input("Paste Password Reset Token", placeholder="e.g. 32-character token")
        if st.button("Proceed with Token →", use_container_width=True):
            if manual_tok.strip():
                st.session_state["active_reset_token"] = manual_tok.strip()
                st.session_state["auth_mode"] = "reset_password"
                st.rerun()
            else:
                st.error("Please provide the token.")

        if st.button("← Back to Student Login", use_container_width=True):
            st.session_state["auth_mode"] = "student_login"
            st.rerun()

# --- 5.4 RESET PASSWORD SCREEN ---
def render_reset_password():
    token = st.session_state.get("active_reset_token", "").strip()
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.markdown("### 🔒 Create New Password")
        st.caption("Enter and confirm your new account password.")

        new_pw = st.text_input("New Password", type="password", placeholder="At least 6 characters")
        confirm_pw = st.text_input("Confirm New Password", type="password", placeholder="Repeat new password")

        if st.button("Update Password & Continue", use_container_width=True, type="primary"):
            if len(new_pw) < 6:
                st.error("Password must be at least 6 characters.")
            elif new_pw != confirm_pw:
                st.error("Passwords do not match.")
            else:
                user_id, msg = verify_and_consume_password_reset_token(token)
                if user_id:
                    conn = get_conn()
                    conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(new_pw), user_id))
                    conn.commit()
                    conn.close()
                    notify(user_id, "Your UNI HELP password was updated successfully.")
                    st.success("Your password has been reset! Please sign in with your new credentials.")
                    st.session_state.pop("active_reset_token", None)
                    st.session_state.pop("_last_email_simulated", None)
                    st.session_state["auth_mode"] = "student_login"
                    st.rerun()
                else:
                    st.error(msg)

        if st.button("Cancel & Return to Login", use_container_width=True):
            st.session_state.pop("active_reset_token", None)
            st.session_state["auth_mode"] = "student_login"
            st.rerun()

# --- 5.5 REGISTRATION SCREEN ---
def render_registration():
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.markdown("### 🎓 Register Student Account")
        st.caption("Join your verified campus network.")

        with st.form("reg_form"):
            name = st.text_input("Full Name", placeholder="Ananya Patel")
            sid = st.text_input("Student ID (Unique)", placeholder="STU1004")
            email = st.text_input("University Email", placeholder="ananya.p@student.university.edu")
            phone = st.text_input("Phone Number", placeholder="9876543210")
            pw1 = st.text_input("Password", type="password")
            pw2 = st.text_input("Confirm Password", type="password")
            submitted = st.form_submit_button("Register & Activate Account", use_container_width=True, type="primary")

        if submitted:
            if not email.endswith(UNIVERSITY_EMAIL_DOMAIN):
                st.error(f"Email domain must match: {UNIVERSITY_EMAIL_DOMAIN}")
            elif len(pw1) < 6 or pw1 != pw2:
                st.error("Passwords must match and be at least 6 characters.")
            elif not sid.strip() or not name.strip():
                st.error("Full name and Student ID are required.")
            else:
                conn = get_conn()
                try:
                    conn.execute(
                        """INSERT INTO users (full_name, email, phone, student_id, password_hash, role, verified, trust_score, unicoins, created_at)
                           VALUES (?, ?, ?, ?, ?, 'student', 1, 50, 50, ?)""",
                        (name.strip(), email.strip().lower(), phone.strip(), sid.strip(), generate_password_hash(pw1), now_iso()),
                    )
                    uid = conn.execute("SELECT last_insert_rowid() id").fetchone()["id"]
                    conn.commit()
                    add_unicoins(uid, 50, "Welcome bonus")
                    st.success("Account created successfully with 50 UniCoins! Please sign in.")
                    st.session_state["auth_mode"] = "student_login"
                    st.rerun()
                except sqlite3.IntegrityError:
                    st.error("An account with that Student ID or Email already exists.")
                finally:
                    conn.close()

        if st.button("← Back to Student Login", use_container_width=True):
            st.session_state["auth_mode"] = "student_login"
            st.rerun()

# --- 5.6 ISOLATED ADMIN LOGIN SCREEN ---
def render_admin_login():
    col1, col2, col3 = st.columns([1, 1.8, 1])
    with col2:
        st.markdown("<h2 style='text-align:center;'>🛡️ University Administration Portal</h2>", unsafe_allow_html=True)
        st.caption("<div style='text-align:center;'>Confidential Proctor & Safety Dashboard</div>", unsafe_allow_html=True)
        st.write("")

        with st.form("admin_login_box"):
            admin_user = st.text_input("Administrative Email", value=ADMIN_EMAIL)
            admin_pwd = st.text_input("Admin Security Password", type="password", value="AdminUniHelp123!")
            submit_adm = st.form_submit_button("Enter Administration Workspace", use_container_width=True, type="primary")

        if submit_adm:
            conn = get_conn()
            row = conn.execute("SELECT * FROM users WHERE email=? AND role='admin'", (admin_user.strip().lower(),)).fetchone()
            conn.close()
            if row and check_password_hash(row["password_hash"], admin_pwd):
                st.session_state["user"] = dict(row)
                st.rerun()
            else:
                st.error("Access denied. Invalid administrative credentials.")

        st.write("")
        if st.button("← Return to Student Network", use_container_width=True):
            st.session_state["auth_mode"] = "student_login"
            st.rerun()

# =============================================================================
# 6. ADMIN WORKSPACE (COMPLETELY SEPARATE)
# =============================================================================

def render_admin_workspace(user):
    top1, top2 = st.columns([3, 1])
    with top1:
        st.markdown("### 🛡️ Campus Administration Control Center")
        st.caption(f"Authenticated Officer: **{user['full_name']}** ({user['email']})")
    with top2:
        if st.button("🚪 Logout of Admin Console", use_container_width=True):
            st.session_state["user"] = None
            st.session_state["auth_mode"] = "student_login"
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
    m3.metric("Held Escrow Value", f"₹{held_escrow:.0f}")
    m4.metric("Active Runs", active_deliveries)

    adm_tab1, adm_tab2, adm_tab3 = st.tabs(["⚠️ Dispute Resolution", "👥 Student Registry", "💰 Escrow Oversight"])

    with adm_tab1:
        conn = get_conn()
        disputes = conn.execute(
            """SELECT d.*, u.full_name reporter_name FROM disputes d
               JOIN users u ON u.id = d.reporter_id ORDER BY d.id DESC"""
        ).fetchall()
        conn.close()

        if not disputes:
            st.success("No disputes currently open.")
        for d in disputes:
            with st.container(border=True):
                st.markdown(f"**Dispute #{d['id']} — {d['category']}** on {d['transaction_type']} #{d['transaction_id']}")
                st.caption(f"Reporter: **{d['reporter_name']}** | Status: `{d['status']}`")
                st.write(d["description"] or "No description provided.")

                if d["status"] in ("OPEN", "UNDER_REVIEW"):
                    b1, b2 = st.columns(2)
                    if b1.button("Resolve & Release Escrow", key=f"res_{d['id']}"):
                        conn = get_conn()
                        conn.execute("UPDATE disputes SET status='RESOLVED', resolved_at=? WHERE id=?", (now_iso(), d["id"]))
                        conn.commit(); conn.close()
                        update_transaction_status(d["transaction_type"], d["transaction_id"], "RELEASED")
                        st.success("Dispute resolved.")
                        st.rerun()
                    if b2.button("Dismiss & Refund Requester", key=f"rej_{d['id']}"):
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
            c3.write(f"Coins: **{s['unicoins']}**")
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
        for tx in txs:
            st.write(f"TXN `#{tx['id']}` — ₹{tx['amount']:.0f} | Related: {tx['related_type']} #{tx['related_id']} | Status: `{tx['status']}`")

# =============================================================================
# 7. STUDENT WORKSPACE
# =============================================================================

def render_student_workspace(user):
    top1, top2 = st.columns([3, 1.5])
    with top1:
        st.markdown(f"### 🎓 UNI HELP")
        st.caption(f"Student: **{user['full_name']}** (`{user['student_id']}`) • Trust Score: **{user['trust_score']}/100** • 🪙 **{user['unicoins']} UniCoins**")
    with top2:
        # Quick student switch helper (for presentation / hackathon demo)
        conn = get_conn()
        students = conn.execute("SELECT id, full_name FROM users WHERE role='student'").fetchall()
        conn.close()
        opts = {s["id"]: s["full_name"] for s in students}
        chosen = st.selectbox("Demo Switch Student", options=list(opts.keys()), format_func=lambda x: opts[x], index=list(opts.keys()).index(user["id"]) if user["id"] in opts else 0)
        if chosen != user["id"]:
            st.session_state["user"] = dict(user_by_id(chosen))
            st.rerun()

        if st.button("Logout", key="student_exit"):
            st.session_state["user"] = None
            st.session_state["auth_mode"] = "student_login"
            st.rerun()

    st.write("")
    tabs = st.tabs(["📦 Delivery Requests", "🤝 Borrowing Hub", "🛠 Micro-Tasks", "💰 UniCoins & Wallet", "⚠️ Report Dispute"])

    # 1. Delivery
    with tabs[0]:
        st.markdown("#### Campus Delivery Network")
        sub_mode = st.radio("Delivery Mode", ["Active Requests", "Post a Delivery Request"], horizontal=True, label_visibility="collapsed")

        if sub_mode == "Post a Delivery Request":
            with st.form("new_delivery"):
                item_name = st.text_input("Item Name", placeholder="e.g. Courier at Main Gate")
                desc = st.text_area("Pickup / Delivery Instructions")
                c_p1, c_p2 = st.columns(2)
                p_loc = c_p1.text_input("Pickup Point", value="Main Gate")
                d_loc = c_p2.text_input("Drop Point", value="Hostel Block C")
                reward = st.number_input("Reward (₹ / UniCoins)", min_value=10.0, value=30.0, step=5.0)
                sub_del = st.form_submit_button("Post Request", type="primary")

                if sub_del:
                    conn = get_conn()
                    conn.execute(
                        """INSERT INTO requests (requester_id, item_name, description, pickup_location, destination, reward, status, created_at)
                           VALUES (?, ?, ?, ?, ?, ?, 'CREATED', ?)""",
                        (user["id"], item_name.strip(), desc.strip(), p_loc.strip(), d_loc.strip(), reward, now_iso()),
                    )
                    conn.commit(); conn.close()
                    st.success("Delivery request posted to campus!")
                    st.rerun()
        else:
            conn = get_conn()
            requests = conn.execute("SELECT r.*, u.full_name requester_name FROM requests r JOIN users u ON u.id = r.requester_id ORDER BY r.id DESC").fetchall()
            conn.close()

            for r in requests:
                with st.container(border=True):
                    is_owner = (r["requester_id"] == user["id"])
                    is_helper = (r["helper_id"] == user["id"])
                    st.markdown(f"**{r['item_name']}** — Reward: **₹{r['reward']:.0f}** | Status: `{r['status']}`")
                    st.caption(f"📍 {r['pickup_location']} ➔ {r['destination']} | Requester: **{r['requester_name']}**")

                    if r["status"] == "CREATED" and not is_owner:
                        if st.button("Accept Delivery", key=f"acc_d_{r['id']}"):
                            conn = get_conn()
                            conn.execute("UPDATE requests SET helper_id=?, status='ACCEPTED', accepted_at=? WHERE id=?", (user["id"], now_iso(), r["id"]))
                            conn.commit(); conn.close()
                            create_transaction(r["requester_id"], user["id"], "DELIVERY", r["id"], r["reward"], "HELD")
                            notify(r["requester_id"], f"{user['full_name']} accepted your delivery request: {r['item_name']}")
                            st.rerun()

                    elif r["status"] == "ACCEPTED":
                        if is_owner:
                            st.info("Helper is assigned. Provide this OTP during item handover:")
                            if st.button("Generate Handover Code", key=f"gen_h_code_{r['id']}"):
                                code = create_otp(user["id"], "HANDOVER_OTP", r["id"])
                                st.success(f"Handover Code: **{code}**")
                        elif is_helper:
                            st.markdown("##### Handover Verification")
                            entered = st.text_input("Enter Handover Code from Requester", key=f"h_code_in_{r['id']}")
                            if st.button("Confirm Handover", key=f"sub_h_code_{r['id']}"):
                                ok, msg = verify_otp(r["requester_id"], "HANDOVER_OTP", r["id"], entered)
                                if ok:
                                    conn = get_conn()
                                    conn.execute("UPDATE requests SET status='PICKUP_VERIFIED', pickup_verified_at=? WHERE id=?", (now_iso(), r["id"]))
                                    conn.commit(); conn.close()
                                    st.success("Handover confirmed! In Transit.")
                                    st.rerun()
                                else:
                                    st.error(msg)

                    elif r["status"] == "PICKUP_VERIFIED" and is_helper:
                        if st.button("Mark as Delivered", key=f"mark_deliv_{r['id']}"):
                            conn = get_conn()
                            conn.execute("UPDATE requests SET status='DELIVERED', delivered_at=? WHERE id=?", (now_iso(), r["id"]))
                            conn.commit(); conn.close()
                            st.rerun()

                    elif r["status"] == "DELIVERED" and is_owner:
                        if st.button("Confirm Receipt & Release Reward", key=f"conf_rec_{r['id']}", type="primary"):
                            conn = get_conn()
                            conn.execute("UPDATE requests SET status='COMPLETED', completed_at=? WHERE id=?", (now_iso(), r["id"]))
                            conn.commit(); conn.close()
                            update_transaction_status("DELIVERY", r["id"], "RELEASED")
                            add_unicoins(r["helper_id"], 20, f"Delivery completion reward for #{r['id']}")
                            st.success("Delivery completed and reward released!")
                            st.rerun()

    # 2. Borrowing
    with tabs[1]:
        st.markdown("#### Campus Borrowing Hub")
        conn = get_conn()
        items = conn.execute("SELECT i.*, u.full_name owner_name FROM items i JOIN users u ON u.id = i.owner_id WHERE i.status='AVAILABLE'").fetchall()
        conn.close()

        if not items:
            st.info("No borrowable items available.")
        for it in items:
            with st.container(border=True):
                st.markdown(f"**{it['item_name']}** ({it['category']}) — Deposit: **₹{it['deposit']:.0f}**")
                st.caption(f"Owner: **{it['owner_name']}** | Condition: {it['condition']}")
                if it["owner_id"] != user["id"]:
                    if st.button("Request to Borrow", key=f"req_borrow_{it['id']}"):
                        conn = get_conn()
                        conn.execute("INSERT INTO borrowings (item_id, owner_id, borrower_id, deposit, status, created_at) VALUES (?,?,?,?,'REQUESTED',?)", (it["id"], it["owner_id"], user["id"], it["deposit"], now_iso()))
                        conn.execute("UPDATE items SET status='BORROWED' WHERE id=?", (it["id"],))
                        conn.commit(); conn.close()
                        create_transaction(user["id"], it["owner_id"], "BORROW_DEPOSIT", it["id"], it["deposit"], "HELD")
                        st.success("Request sent!")
                        st.rerun()

    # 3. Tasks
    with tabs[2]:
        st.markdown("#### Micro-Tasks & Campus Gigs")
        conn = get_conn()
        tasks = conn.execute("SELECT t.*, u.full_name creator_name FROM tasks t JOIN users u ON u.id = t.creator_id ORDER BY t.id DESC").fetchall()
        conn.close()

        for t in tasks:
            with st.container(border=True):
                st.markdown(f"**{t['title']}** — ₹{t['reward']:.0f} | Status: `{t['status']}`")
                st.caption(f"Posted by: **{t['creator_name']}** | Deadline: {t['deadline']}")
                st.write(t["description"])
                if t["status"] == "CREATED" and t["creator_id"] != user["id"]:
                    if st.button("Accept Task", key=f"acc_task_{t['id']}"):
                        conn = get_conn()
                        conn.execute("UPDATE tasks SET helper_id=?, status='ACCEPTED' WHERE id=?", (user["id"], t["id"]))
                        conn.commit(); conn.close()
                        create_transaction(t["creator_id"], user["id"], "TASK", t["id"], t["reward"], "HELD")
                        st.rerun()

    # 4. Wallet
    with tabs[3]:
        st.markdown("#### Virtual Wallet & UniCoins")
        conn = get_conn()
        released = conn.execute("SELECT COALESCE(SUM(amount), 0) s FROM transactions WHERE payee_id=? AND status='RELEASED'", (user["id"],)).fetchone()["s"]
        held = conn.execute("SELECT COALESCE(SUM(amount), 0) s FROM transactions WHERE payee_id=? AND status='HELD'", (user["id"],)).fetchone()["s"]
        tx_logs = conn.execute("SELECT * FROM transactions WHERE payer_id=? OR payee_id=? ORDER BY id DESC", (user["id"], user["id"])).fetchall()
        conn.close()

        w1, w2, w3 = st.columns(3)
        w1.metric("UniCoins Balance", f"🪙 {user['unicoins']}")
        w2.metric("Released Earnings", f"₹{released:.0f}")
        w3.metric("Held in Escrow", f"₹{held:.0f}")

        st.markdown("##### Recent Ledger")
        for tx in tx_logs:
            role = "Paid" if tx["payer_id"] == user["id"] else "Received"
            st.write(f"• **{role} ₹{tx['amount']:.0f}** for `{tx['related_type']} #{tx['related_id']}` — Status: `{tx['status']}`")

    # 5. Disputes
    with tabs[4]:
        st.markdown("#### Raise a Campus Dispute")
        with st.form("raise_dispute_form"):
            t_src = st.selectbox("Service Type", ["DELIVERY", "BORROWING", "TASK"])
            r_id = st.number_input("Request / Task ID", min_value=1, step=1)
            cat = st.selectbox("Category", ["Item Damaged", "No-Show / Abandoned", "Incomplete Task", "Other"])
            exp = st.text_area("Explanation")
            sub_disp = st.form_submit_button("Submit Dispute", type="primary")

            if sub_disp:
                conn = get_conn()
                conn.execute(
                    """INSERT INTO disputes (transaction_type, transaction_id, reporter_id, category, description, status, created_at)
                       VALUES (?, ?, ?, ?, ?, 'OPEN', ?)""",
                    (t_src, r_id, user["id"], cat, exp.strip(), now_iso()),
                )
                conn.commit(); conn.close()
                update_transaction_status(t_src, r_id, "DISPUTED")
                st.success("Dispute filed. Escrow funds have been frozen for proctor arbitration.")
                st.rerun()

# =============================================================================
# 8. MAIN CONTROLLER & ROUTER
# =============================================================================

def main():
    user = st.session_state.get("user")

    if user is None:
        auth_mode = st.session_state.get("auth_mode", "student_login")
        if auth_mode == "admin_login":
            render_admin_login()
        elif auth_mode == "student_otp":
            render_student_otp()
        elif auth_mode == "forgot_password":
            render_forgot_password()
        elif auth_mode == "reset_password":
            render_reset_password()
        elif auth_mode == "register":
            render_registration()
        else:
            render_student_login()
        return

    # Route based on role
    if user.get("role") == "admin":
        render_admin_workspace(user)
    else:
        render_student_workspace(user)

if __name__ == "__main__":
    main()

