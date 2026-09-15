# 🎓 UNI HELP

**"Your campus. Your community. Someone can help."**

A verified university micro-service network — not just a courier app. UNI HELP
combines a university-verified student community, a delivery system, a
borrowing marketplace, and a micro-task marketplace, all secured with OTP,
QR, and location-aware handovers, backed by trust scores, ratings, and
UniCoins.

## Installation

1. Install Python 3.10+.
2. From the `UNI_HELP` folder, install dependencies:
   ```
   pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and edit values as needed:
   ```
   cp .env.example .env
   ```
4. Run the app:
   ```
   streamlit run app.py
   ```

The database (`database.db`) and `uploads/` folder are created automatically
on first run — nothing to set up by hand.

## Gmail App Password setup (optional — for real OTP emails)

If you leave `SMTP_EMAIL` / `SMTP_APP_PASSWORD` blank, UNI HELP runs in
**🧪 DEMO MODE**: generated OTPs are shown directly on-screen labeled
"DEMO ONLY" instead of being emailed.

To send real emails:
1. Enable 2-Step Verification on a Gmail account.
2. Go to Google Account → Security → App Passwords, and create one for "Mail".
3. Put the Gmail address in `SMTP_EMAIL` and the 16-character app password in
   `SMTP_APP_PASSWORD` in your `.env` file.
4. Restart the app. OTPs will now be emailed for real.

## Demo Mode

Demo Mode is not an all-or-nothing switch — each feature that depends on an
external service degrades gracefully and labels itself clearly:

- **Email:** if SMTP isn't configured, OTPs are shown on-screen with a
  "🧪 DEMO ONLY" label instead of being sent.
- **Location:** the location-check tool lets you enter latitude/longitude
  manually and tick "Use DEMO MODE simulated location" — this is never
  presented as real GPS.
- **QR scanning:** rather than requiring camera hardware, the helper pastes
  the QR token value (shown under the generated QR image) to verify — see
  *Known limitations* below.

From the landing page, click **"LOAD DEMO DATA"** to populate 3 demo
students, 5 delivery requests, 5 borrowable items, 4 micro-tasks, UniCoins,
and notifications, so you can explore the whole app immediately.

Demo student accounts (password `demo1234` for all):
- `aarav.sharma@student.university.edu` (or your configured domain)
- `priya.nair@student.university.edu`
- `rohan.mehta@student.university.edu`

Default admin account (auto-created on first run):
- Email: value of `ADMIN_EMAIL` in `.env` (default `admin@unihelp.local`)
- Password: value of `ADMIN_PASSWORD` in `.env` (default `AdminUniHelp123!`)

**Change the default admin password in `.env` before any real deployment.**

## How to test the core flows

1. **Registration → email verification → login:** Register with an email
   ending in your configured `UNIVERSITY_EMAIL_DOMAIN`. Verify using the
   on-screen (or emailed) OTP, then log in.
2. **Delivery:** Create a request as Student A. Log in as Student B, accept
   it under Delivery → Browse & Accept. As Student A, generate a pickup OTP
   or QR. As Student B, enter it to verify pickup, then mark In Transit →
   Delivered. Student A confirms completion and rates Student B.
3. **Borrowing:** List an item as Student A. Request to borrow as Student B.
   Student A accepts. Student A generates a pickup OTP/QR; Student B enters
   it to activate the borrowing. Student B requests return; Student A
   generates a return OTP/QR; Student B enters it to complete.
4. **Micro-tasks:** Create a task, have another student accept it, start it,
   then have the creator generate a completion code for the helper to enter.
5. **OTP edge cases:** try an invalid code (rejected with attempts
   remaining), wait past `OTP_EXPIRY_MINUTES` (rejected as expired), or
   reuse a code that was already verified (rejected as no longer active).
6. **QR reuse:** try submitting the same QR token twice — the second attempt
   is rejected as "already used".
7. **Admin access:** log in as a regular student and note there is no Admin
   option in the sidebar. Log in as the admin account to see the dashboard,
   suspend/unsuspend users, and resolve disputes.
8. **Ratings:** after a completed transaction, rate the other party once;
   attempting to rate the same transaction twice is blocked.
9. **Trust score / UniCoins:** completing deliveries, tasks, and on-time
   returns increases trust score and awards UniCoins, visible on the
   dashboard and Wallet page.

## Known limitations

- **QR "scanning"** is implemented as real, unique, single-use, expiring
  tokens rendered as QR images — but the "scan" step is a manual token-paste
  rather than live camera decoding, since that requires a camera-capable
  component outside Streamlit's core widgets. The underlying verification
  logic (uniqueness, expiry, one-time use, transaction binding) is fully
  real and server-enforced either way.
- **Location verification** uses manually entered or simulated
  latitude/longitude rather than the browser's live Geolocation API, since
  Streamlit does not expose that natively without a custom component. The
  Haversine distance calculation and radius enforcement are fully real.
- **Payments** are prototype-only ledger entries (`PENDING` /`HELD`/
  `RELEASED`/`CANCELLED`), clearly labeled "PROTOTYPE TRANSACTION" — no real
  payment gateway is connected, per the project spec.
- **UniCoins** are a community reward counter only, not a real currency.
- This is a single-process SQLite app intended for demos/hackathons, not
  concurrent production traffic.
