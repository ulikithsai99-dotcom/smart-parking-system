"""
Smart Parking System — Python/Flask Backend
AI-powered slot allocation with peak hour prediction.
"""

import sqlite3
import os
import json
from datetime import datetime
from functools import wraps
from flask import (
    Flask, request, session, redirect, url_for,
    send_from_directory, jsonify, make_response
)
import bcrypt
from ai_engine import (
    get_smart_slot,
    get_next_24h_predictions,
    get_current_peak_status,
    get_today_stats,
    get_busiest_hours,
    get_hourly_predictions,
)

# ─── App Setup ────────────────────────────────────────────────────────────────

app = Flask(__name__, static_folder="views", static_url_path="")
app.secret_key = "smartparkingsecret_py"
app.config["SESSION_TYPE"] = "filesystem"

DB_PATH = "database.db"
TOTAL_SLOTS = 200

# ─── Database ─────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS parking (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT,
            vehicle TEXT UNIQUE,
            slot INTEGER,
            entry_time TEXT,
            exit_time TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS reservations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id TEXT UNIQUE,
            name TEXT,
            vehicle TEXT,
            slot INTEGER,
            reservation_date TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS admin (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            password TEXT
        )
    """)
    conn.commit()
    conn.close()
    print("✅ Database ready")

# ─── Helpers ──────────────────────────────────────────────────────────────────

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin"):
            return redirect("/admin")
        return f(*args, **kwargs)
    return decorated

def get_occupied_and_reserved():
    """Returns (set of occupied slot numbers, set of reserved slot numbers)."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT slot FROM parking WHERE exit_time IS NULL")
    occupied = {r["slot"] for r in cur.fetchall()}
    cur.execute("SELECT slot FROM reservations")
    reserved = {r["slot"] for r in cur.fetchall()}
    conn.close()
    return occupied, reserved

def html_page(content, title="Smart Parking"):
    """Wrap raw HTML content in a minimal shell."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{title}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
*{{margin:0;padding:0;box-sizing:border-box;font-family:'Inter',sans-serif;}}
body{{min-height:100vh;background:#04080f;color:#f0f4ff;display:flex;align-items:center;justify-content:center;padding:20px;}}
body::before{{content:'';position:fixed;inset:0;background:radial-gradient(ellipse 70% 60% at 30% 20%,rgba(37,99,235,.14) 0%,transparent 65%),radial-gradient(ellipse 50% 50% at 75% 80%,rgba(6,182,212,.08) 0%,transparent 60%);pointer-events:none;}}
body::after{{content:'';position:fixed;inset:0;background-image:linear-gradient(rgba(255,255,255,.018) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.018) 1px,transparent 1px);background-size:60px 60px;pointer-events:none;}}
.box{{position:relative;z-index:1;max-width:560px;width:100%;background:rgba(6,12,24,.9);border:1px solid rgba(255,255,255,.12);border-radius:28px;padding:48px 44px;box-shadow:0 30px 70px rgba(0,0,0,.6);overflow:hidden;}}
.box::before{{content:'';position:absolute;top:0;left:0;right:0;height:1px;background:linear-gradient(90deg,transparent,rgba(59,130,246,.6),transparent);}}
.icon{{width:72px;height:72px;border-radius:20px;background:linear-gradient(135deg,rgba(37,99,235,.2),rgba(6,182,212,.15));border:1px solid rgba(59,130,246,.3);display:flex;align-items:center;justify-content:center;font-size:2rem;margin:0 auto 24px;}}
h1{{text-align:center;font-size:1.9rem;font-weight:800;letter-spacing:-.03em;margin-bottom:10px;}}
.sub{{text-align:center;color:#94a3b8;margin-bottom:28px;font-size:.92rem;}}
.receipt{{background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.08);border-radius:14px;padding:22px 26px;margin-bottom:24px;}}
.receipt-row{{display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid rgba(255,255,255,.05);font-size:.9rem;}}
.receipt-row:last-child{{border-bottom:none;}}
.receipt-row .lbl{{color:#64748b;font-weight:500;}}
.receipt-row .val{{color:#f0f4ff;font-weight:600;}}
.badge{{display:inline-flex;align-items:center;gap:6px;padding:6px 14px;border-radius:100px;font-size:.78rem;font-weight:700;letter-spacing:.04em;}}
.badge-green{{background:rgba(16,185,129,.15);border:1px solid rgba(16,185,129,.3);color:#34d399;}}
.badge-red{{background:rgba(239,68,68,.15);border:1px solid rgba(239,68,68,.3);color:#f87171;}}
.badge-blue{{background:rgba(59,130,246,.15);border:1px solid rgba(59,130,246,.3);color:#93c5fd;}}
.badge-yellow{{background:rgba(245,158,11,.15);border:1px solid rgba(245,158,11,.3);color:#fcd34d;}}
.ai-note{{background:rgba(37,99,235,.08);border:1px solid rgba(59,130,246,.2);border-radius:12px;padding:14px 18px;margin-bottom:20px;font-size:.85rem;color:#93c5fd;}}
.ai-note strong{{display:block;margin-bottom:3px;color:#60a5fa;}}
.btn-row{{display:flex;flex-wrap:wrap;gap:10px;}}
.btn{{flex:1;min-width:120px;padding:11px 16px;border-radius:10px;font-size:.85rem;font-weight:600;text-decoration:none;text-align:center;transition:.2s ease;border:1px solid rgba(255,255,255,.1);color:#94a3b8;cursor:pointer;background:rgba(255,255,255,.04);}}
.btn:hover{{background:rgba(255,255,255,.08);color:#f0f4ff;border-color:rgba(255,255,255,.18);}}
.btn-primary{{background:linear-gradient(135deg,#2563eb,#06b6d4);border-color:transparent;color:white;}}
.btn-primary:hover{{box-shadow:0 6px 24px rgba(37,99,235,.45);transform:translateY(-1px);}}
.error-icon{{font-size:3rem;text-align:center;margin-bottom:12px;}}
</style>
</head>
<body><div class="box">{content}</div></body>
</html>"""

def ai_badge_html(peak_status):
    """Returns a small AI status line to show on receipts."""
    pct = peak_status["current_percent"]
    label = peak_status["status_label"]
    strategy = peak_status["strategy"]
    strategy_map = {
        "zone_rotation": "Zone Rotation Active",
        "balanced": "Balanced Mode",
        "sequential": "Standard Mode",
    }
    return f"""
<div class="ai-note">
  <strong>🤖 AI Slot Engine</strong>
  Demand forecast: <b>{pct}% occupancy predicted</b> — {label}<br>
  Allocation: {strategy_map.get(strategy, strategy)}
</div>
"""

# ─── Static File Serving ───────────────────────────────────────────────────────

@app.route("/css/<path:filename>")
def serve_css(filename):
    return send_from_directory("views/css", filename)

@app.route("/images/<path:filename>")
def serve_images(filename):
    return send_from_directory("views/images", filename)

# ─── Core Routes ──────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return redirect("/user")

@app.route("/user")
def user_portal():
    return send_from_directory("views", "form.html")

@app.route("/admin")
def admin_login():
    return send_from_directory("views", "admin.html")

@app.route("/exit")
def exit_page():
    return send_from_directory("views", "exit.html")

@app.route("/reserve")
def reserve_page():
    return send_from_directory("views", "reserve.html")

@app.route("/search")
def search_page():
    return send_from_directory("views", "search.html")

# ─── Admin Auth ───────────────────────────────────────────────────────────────

@app.route("/createAdmin", methods=["POST"])
def create_admin():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "").strip()
    if not username or not password:
        return html_page('<div class="error-icon">⚠️</div><h1>Missing Fields</h1><p class="sub">Username and password are required.</p><div class="btn-row"><a class="btn btn-primary" href="/admin">← Back</a></div>')

    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    try:
        conn = get_db()
        conn.execute("INSERT INTO admin (username, password) VALUES (?, ?)", (username, hashed))
        conn.commit()
        conn.close()
    except sqlite3.IntegrityError:
        return html_page('<div class="error-icon">❌</div><h1>Admin Exists</h1><p class="sub">That username is already taken.</p><div class="btn-row"><a class="btn btn-primary" href="/admin">← Back</a></div>')
    return redirect("/admin")

@app.route("/adminLogin", methods=["POST"])
def admin_login_post():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "").strip()

    conn = get_db()
    admin = conn.execute("SELECT * FROM admin WHERE username = ?", (username,)).fetchone()
    conn.close()

    if not admin:
        return html_page('<div class="error-icon">❌</div><h1>Invalid Login</h1><p class="sub">Username not found.</p><div class="btn-row"><a class="btn btn-primary" href="/admin">← Try Again</a></div>')

    if not bcrypt.checkpw(password.encode(), admin["password"].encode()):
        return html_page('<div class="error-icon">🔒</div><h1>Wrong Password</h1><p class="sub">Please check your credentials.</p><div class="btn-row"><a class="btn btn-primary" href="/admin">← Try Again</a></div>')

    session["admin"] = True
    session["admin_user"] = username
    return redirect("/dashboard")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/admin")

@app.route("/resetAdmin")
def reset_admin():
    conn = get_db()
    conn.execute("DELETE FROM admin")
    conn.commit()
    conn.close()
    return html_page('<div class="error-icon">✅</div><h1>Admin Reset</h1><p class="sub">All admin accounts removed.</p><div class="btn-row"><a class="btn btn-primary" href="/admin">Go to Login</a></div>')

# ─── Dashboard ────────────────────────────────────────────────────────────────

@app.route("/dashboard")
@admin_required
def dashboard():
    return send_from_directory("views", "dashboard.html")

@app.route("/history")
@admin_required
def history():
    conn = get_db()
    rows = conn.execute("SELECT * FROM parking ORDER BY id DESC").fetchall()
    conn.close()

    rows_html = ""
    for r in rows:
        status = '<span class="badge badge-red">Exited</span>' if r["exit_time"] else '<span class="badge badge-green">Active</span>'
        rows_html += f"""
        <tr>
          <td>{r["id"]}</td>
          <td>{r["name"]}</td>
          <td>{r["phone"] or "—"}</td>
          <td><b>{r["vehicle"]}</b></td>
          <td>{r["slot"]}</td>
          <td style="font-size:.8rem;color:#64748b">{r["entry_time"]}</td>
          <td>{status}</td>
          <td><a class="delete-btn" href="/delete/{r["id"]}">🗑 Delete</a></td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>History — Smart Parking</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/css/style.css">
</head>
<body style="padding:0;">
<div class="dashboard">
  <div class="sidebar">
    <div class="sidebar-logo"><div class="logo-icon">🚗</div><div><h2>Smart Parking</h2><small>Control Center</small></div></div>
    <div class="nav-label">Management</div>
    <a href="/history" class="active"><span class="nav-icon">📜</span> Parking History</a>
    <a href="/stats"><span class="nav-icon">📊</span> Statistics</a>
    <a href="/reservations"><span class="nav-icon">📋</span> Reservations</a>
    <a href="/map"><span class="nav-icon">🅿</span> Live Map</a>
    <div class="nav-label" style="margin-top:20px;">User Portal</div>
    <a href="/user"><span class="nav-icon">👤</span> Parking Entry</a>
    <a href="/exit"><span class="nav-icon">🚪</span> Vehicle Exit</a>
    <div class="sidebar-footer" style="margin-top:auto;padding-top:20px;border-top:1px solid rgba(255,255,255,.06);">
      <a href="/logout" style="display:flex;align-items:center;gap:10px;color:#f87171;text-decoration:none;padding:12px 16px;border-radius:12px;font-size:.9rem;font-weight:500;border:1px solid rgba(239,68,68,.15);background:rgba(239,68,68,.05);">⏻ Logout</a>
    </div>
  </div>
  <div class="main" style="position:relative;z-index:1;">
    <div class="page-header"><h1>📜 Parking History</h1><p style="color:#94a3b8;margin-top:5px;">Full log of all parking records</p></div>
    <div style="overflow-x:auto;border-radius:16px;border:1px solid rgba(255,255,255,.07);">
      <table class="table">
        <thead><tr><th>ID</th><th>Name</th><th>Phone</th><th>Vehicle</th><th>Slot</th><th>Entry Time</th><th>Status</th><th>Action</th></tr></thead>
        <tbody>{rows_html}</tbody>
      </table>
    </div>
  </div>
</div>
</body></html>"""

@app.route("/reservations")
@admin_required
def reservations():
    conn = get_db()
    rows = conn.execute("SELECT * FROM reservations ORDER BY id DESC").fetchall()
    conn.close()

    rows_html = ""
    for r in rows:
        rows_html += f"""
        <tr>
          <td>{r["id"]}</td>
          <td><b>{r["employee_id"]}</b></td>
          <td>{r["name"]}</td>
          <td>{r["vehicle"]}</td>
          <td><span class="badge badge-blue">Slot {r["slot"]}</span></td>
          <td style="font-size:.8rem;color:#64748b">{r["reservation_date"]}</td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Reservations — Smart Parking</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/css/style.css">
</head>
<body style="padding:0;">
<div class="dashboard">
  <div class="sidebar">
    <div class="sidebar-logo"><div class="logo-icon">🚗</div><div><h2>Smart Parking</h2><small>Control Center</small></div></div>
    <div class="nav-label">Management</div>
    <a href="/history"><span class="nav-icon">📜</span> Parking History</a>
    <a href="/stats"><span class="nav-icon">📊</span> Statistics</a>
    <a href="/reservations" class="active"><span class="nav-icon">📋</span> Reservations</a>
    <a href="/map"><span class="nav-icon">🅿</span> Live Map</a>
    <div class="sidebar-footer" style="margin-top:auto;padding-top:20px;border-top:1px solid rgba(255,255,255,.06);">
      <a href="/logout" style="display:flex;align-items:center;gap:10px;color:#f87171;text-decoration:none;padding:12px 16px;border-radius:12px;font-size:.9rem;font-weight:500;border:1px solid rgba(239,68,68,.15);background:rgba(239,68,68,.05);">⏻ Logout</a>
    </div>
  </div>
  <div class="main" style="position:relative;z-index:1;">
    <div class="page-header"><h1>📋 Staff Reservations</h1><p style="color:#94a3b8;margin-top:5px;">All pre-reserved employee slots</p></div>
    <div style="overflow-x:auto;border-radius:16px;border:1px solid rgba(255,255,255,.07);">
      <table class="table">
        <thead><tr><th>ID</th><th>Employee ID</th><th>Name</th><th>Vehicle</th><th>Slot</th><th>Reserved On</th></tr></thead>
        <tbody>{rows_html}</tbody>
      </table>
    </div>
  </div>
</div>
</body></html>"""

@app.route("/stats")
@admin_required
def stats():
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) as c FROM parking").fetchone()["c"]
    active = conn.execute("SELECT COUNT(*) as c FROM parking WHERE exit_time IS NULL").fetchone()["c"]
    exited = conn.execute("SELECT COUNT(*) as c FROM parking WHERE exit_time IS NOT NULL").fetchone()["c"]
    conn.close()

    occupancy = round((active / 200) * 100, 1)
    today_data = get_today_stats()
    busiest = get_busiest_hours()

    busiest_html = "".join([
        f'<div style="display:flex;justify-content:space-between;align-items:center;padding:10px 0;border-bottom:1px solid rgba(255,255,255,.05);"><span style="color:#94a3b8;">{b["label"]}</span><div style="display:flex;align-items:center;gap:10px;"><div style="width:100px;height:6px;border-radius:3px;background:rgba(255,255,255,.06);"><div style="width:{b["percent"]}%;height:100%;border-radius:3px;background:linear-gradient(90deg,#2563eb,#06b6d4);"></div></div><span style="font-weight:700;font-size:.85rem;">{b["percent"]}%</span></div></div>'
        for b in busiest
    ])

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Statistics — Smart Parking</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/css/style.css">
</head>
<body style="padding:0;">
<div class="dashboard">
  <div class="sidebar">
    <div class="sidebar-logo"><div class="logo-icon">🚗</div><div><h2>Smart Parking</h2><small>Control Center</small></div></div>
    <div class="nav-label">Management</div>
    <a href="/history"><span class="nav-icon">📜</span> Parking History</a>
    <a href="/stats" class="active"><span class="nav-icon">📊</span> Statistics</a>
    <a href="/reservations"><span class="nav-icon">📋</span> Reservations</a>
    <a href="/map"><span class="nav-icon">🅿</span> Live Map</a>
    <div class="sidebar-footer" style="margin-top:auto;padding-top:20px;border-top:1px solid rgba(255,255,255,.06);">
      <a href="/logout" style="display:flex;align-items:center;gap:10px;color:#f87171;text-decoration:none;padding:12px 16px;border-radius:12px;font-size:.9rem;font-weight:500;border:1px solid rgba(239,68,68,.15);background:rgba(239,68,68,.05);">⏻ Logout</a>
    </div>
  </div>
  <div class="main" style="position:relative;z-index:1;">
    <div class="page-header"><h1>📊 Statistics</h1><p style="color:#94a3b8;margin-top:5px;">Parking analytics & performance metrics</p></div>
    <div class="stats-grid" style="margin-bottom:28px;">
      <div class="stat-card"><div style="font-size:1.5rem;margin-bottom:8px;">📁</div><h2 style="font-size:2rem;font-weight:900;color:#93c5fd;">{total}</h2><span>Total Records</span></div>
      <div class="stat-card"><div style="font-size:1.5rem;margin-bottom:8px;">🟢</div><h2 style="font-size:2rem;font-weight:900;color:#6ee7b7;">{active}</h2><span>Currently Parked</span></div>
      <div class="stat-card"><div style="font-size:1.5rem;margin-bottom:8px;">🔴</div><h2 style="font-size:2rem;font-weight:900;color:#fca5a5;">{exited}</h2><span>Exited Today</span></div>
      <div class="stat-card"><div style="font-size:1.5rem;margin-bottom:8px;">📈</div><h2 style="font-size:2rem;font-weight:900;color:#fcd34d;">{occupancy}%</h2><span>Occupancy Rate</span></div>
      <div class="stat-card"><div style="font-size:1.5rem;margin-bottom:8px;">📅</div><h2 style="font-size:2rem;font-weight:900;color:#c4b5fd;">{today_data["today_entries"]}</h2><span>Today's Entries</span></div>
      <div class="stat-card"><div style="font-size:1.5rem;margin-bottom:8px;">⏱</div><h2 style="font-size:2rem;font-weight:900;">{today_data["avg_stay_minutes"]}<span style="font-size:1rem;font-weight:500;color:#94a3b8;"> min</span></h2><span>Avg Stay Duration</span></div>
    </div>
    <div style="background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.07);border-radius:16px;padding:24px;">
      <h3 style="font-size:.9rem;font-weight:700;color:#94a3b8;letter-spacing:.06em;text-transform:uppercase;margin-bottom:16px;">🤖 AI — Predicted Peak Hours</h3>
      {busiest_html}
    </div>
    <div style="margin-top:16px;text-align:center;">
      <a href="/user" style="color:#94a3b8;text-decoration:none;font-size:.875rem;font-weight:500;padding:10px 18px;border-radius:10px;border:1px solid rgba(255,255,255,.08);">← Home</a>
    </div>
  </div>
</div>
</body></html>"""

@app.route("/delete/<int:record_id>")
@admin_required
def delete_record(record_id):
    conn = get_db()
    conn.execute("DELETE FROM parking WHERE id = ?", (record_id,))
    conn.commit()
    conn.close()
    return redirect("/history")

# ─── Parking Actions ───────────────────────────────────────────────────────────

@app.route("/submit", methods=["POST"])
def submit():
    name = request.form.get("name", "").strip()
    phone = request.form.get("phone", "").strip()
    vehicle = request.form.get("vehicle", "").strip().upper()

    if not name or not phone or not vehicle:
        return html_page('<div class="error-icon">⚠️</div><h1>Missing Fields</h1><p class="sub">All fields are required.</p><div class="btn-row"><a class="btn btn-primary" href="/user">← Back</a></div>')

    if not phone.isdigit() or len(phone) != 10:
        return html_page('<div class="error-icon">📞</div><h1>Invalid Phone</h1><p class="sub">Phone number must be exactly 10 digits.</p><div class="btn-row"><a class="btn btn-primary" href="/user">← Back</a></div>')

    conn = get_db()
    existing = conn.execute(
        "SELECT * FROM parking WHERE vehicle = ? AND exit_time IS NULL", (vehicle,)
    ).fetchone()

    if existing:
        conn.close()
        return html_page(f'<div class="error-icon">🚗</div><h1>Already Parked</h1><p class="sub">Vehicle <b>{vehicle}</b> is currently assigned to Slot {existing["slot"]}.</p><div class="btn-row"><a class="btn btn-primary" href="/user">← Back</a><a class="btn" href="/map">📍 Map</a></div>')

    # Get occupied & reserved slots
    occupied_rows = conn.execute("SELECT slot FROM parking WHERE exit_time IS NULL").fetchall()
    reserved_rows = conn.execute("SELECT slot FROM reservations").fetchall()
    occupied = {r["slot"] for r in occupied_rows}
    reserved = {r["slot"] for r in reserved_rows}
    conn.close()

    # AI-powered smart slot allocation
    peak_status = get_current_peak_status()
    slot = get_smart_slot(occupied, reserved)

    if slot is None:
        return html_page('<div class="error-icon">🅿</div><h1>Parking Full</h1><p class="sub">All 200 slots are currently occupied. Please try again later.</p><div class="btn-row"><a class="btn btn-primary" href="/user">← Back</a><a class="btn" href="/map">📍 Map</a></div>')

    entry_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO parking (name, phone, vehicle, slot, entry_time) VALUES (?, ?, ?, ?, ?)",
            (name, phone, vehicle, slot, entry_time)
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return html_page(f'<div class="error-icon">⚠️</div><h1>Duplicate Entry</h1><p class="sub">Vehicle {vehicle} already exists in the database.</p><div class="btn-row"><a class="btn btn-primary" href="/user">← Back</a></div>')
    conn.close()

    # Build zone label
    zone = "A" if slot <= 50 else "B" if slot <= 100 else "C" if slot <= 150 else "D"
    strategy_map = {"zone_rotation": "Zone Rotation (Peak)", "balanced": "Balanced", "sequential": "Standard"}
    ai_strategy = strategy_map.get(peak_status["strategy"], "Standard")

    return html_page(f"""
<div class="icon">✅</div>
<h1>Parking Confirmed!</h1>
<p class="sub">Your slot has been assigned by the AI engine</p>
{ai_badge_html(peak_status)}
<div class="receipt">
  <div class="receipt-row"><span class="lbl">Name</span><span class="val">{name}</span></div>
  <div class="receipt-row"><span class="lbl">Phone</span><span class="val">{phone}</span></div>
  <div class="receipt-row"><span class="lbl">Vehicle</span><span class="val">{vehicle}</span></div>
  <div class="receipt-row"><span class="lbl">Slot</span><span class="val" style="font-size:1.1rem;color:#60a5fa;font-weight:800;">#{slot} — Zone {zone}</span></div>
  <div class="receipt-row"><span class="lbl">Entry Time</span><span class="val">{datetime.now().strftime("%I:%M %p, %d %b %Y")}</span></div>
  <div class="receipt-row"><span class="lbl">AI Mode</span><span class="val">{ai_strategy}</span></div>
  <div class="receipt-row"><span class="lbl">Demand Level</span><span class="val">{peak_status["status_label"]}</span></div>
</div>
<div class="btn-row">
  <a class="btn btn-primary" href="/user">← Home</a>
  <a class="btn" href="/map">📍 Live Map</a>
  <a class="btn" href="/search">🔍 Search</a>
</div>
""", title="Parking Confirmed")

@app.route("/exitVehicle", methods=["POST"])
def exit_vehicle():
    vehicle = request.form.get("vehicle", "").strip().upper()

    conn = get_db()
    row = conn.execute(
        "SELECT * FROM parking WHERE vehicle = ? AND exit_time IS NULL", (vehicle,)
    ).fetchone()

    if not row:
        conn.close()
        return html_page(f'<div class="error-icon">❌</div><h1>Vehicle Not Found</h1><p class="sub">No active parking record for <b>{vehicle}</b>.</p><div class="btn-row"><a class="btn btn-primary" href="/exit">← Try Again</a></div>')

    entry_dt = datetime.strptime(row["entry_time"], "%Y-%m-%d %H:%M:%S")
    exit_dt = datetime.now()
    diff = exit_dt - entry_dt
    hours = int(diff.total_seconds() // 3600)
    minutes = int((diff.total_seconds() % 3600) // 60)

    conn.execute(
        "UPDATE parking SET exit_time = ? WHERE vehicle = ?",
        (exit_dt.strftime("%Y-%m-%d %H:%M:%S"), vehicle)
    )
    conn.commit()
    conn.close()

    zone = "A" if row["slot"] <= 50 else "B" if row["slot"] <= 100 else "C" if row["slot"] <= 150 else "D"

    return html_page(f"""
<div class="icon">🚪</div>
<h1>Exit Successful</h1>
<p class="sub">Slot {row["slot"]} has been released</p>
<div class="receipt">
  <div class="receipt-row"><span class="lbl">Vehicle</span><span class="val">{vehicle}</span></div>
  <div class="receipt-row"><span class="lbl">Slot Released</span><span class="val">#{row["slot"]} — Zone {zone}</span></div>
  <div class="receipt-row"><span class="lbl">Entry</span><span class="val">{entry_dt.strftime("%I:%M %p")}</span></div>
  <div class="receipt-row"><span class="lbl">Exit</span><span class="val">{exit_dt.strftime("%I:%M %p")}</span></div>
  <div class="receipt-row"><span class="lbl">Duration</span><span class="val" style="color:#60a5fa;">{hours}h {minutes}m</span></div>
</div>
<div class="btn-row">
  <a class="btn btn-primary" href="/user">← Home</a>
  <a class="btn" href="/map">📍 Live Map</a>
</div>
""", title="Exit Successful")

@app.route("/reserveSlot", methods=["POST"])
def reserve_slot():
    employee_id = request.form.get("employee_id", "").strip()
    name = request.form.get("name", "").strip()
    vehicle = request.form.get("vehicle", "").strip().upper()
    slot = request.form.get("slot", "").strip()

    if not all([employee_id, name, vehicle, slot]):
        return html_page('<div class="error-icon">⚠️</div><h1>Missing Fields</h1><div class="btn-row"><a class="btn btn-primary" href="/reserve">← Back</a></div>')

    try:
        slot = int(slot)
        if slot < 1 or slot > 200:
            raise ValueError
    except ValueError:
        return html_page('<div class="error-icon">⚠️</div><h1>Invalid Slot</h1><p class="sub">Slot must be between 1 and 200.</p><div class="btn-row"><a class="btn btn-primary" href="/reserve">← Back</a></div>')

    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO reservations (employee_id, name, vehicle, slot, reservation_date) VALUES (?, ?, ?, ?, ?)",
            (employee_id, name, vehicle, slot, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return html_page(f'<div class="error-icon">❌</div><h1>Reservation Failed</h1><p class="sub">Employee ID already has a reservation, or slot is taken.</p><div class="btn-row"><a class="btn btn-primary" href="/reserve">← Back</a></div>')
    conn.close()

    zone = "A" if slot <= 50 else "B" if slot <= 100 else "C" if slot <= 150 else "D"

    return html_page(f"""
<div class="icon">🏢</div>
<h1>Reservation Confirmed!</h1>
<p class="sub">Slot pre-reserved for your vehicle</p>
<div class="receipt">
  <div class="receipt-row"><span class="lbl">Employee ID</span><span class="val">{employee_id}</span></div>
  <div class="receipt-row"><span class="lbl">Name</span><span class="val">{name}</span></div>
  <div class="receipt-row"><span class="lbl">Vehicle</span><span class="val">{vehicle}</span></div>
  <div class="receipt-row"><span class="lbl">Reserved Slot</span><span class="val" style="color:#60a5fa;font-weight:800;">#{slot} — Zone {zone}</span></div>
</div>
<div class="btn-row">
  <a class="btn btn-primary" href="/user">← Home</a>
  <a class="btn" href="/map">📍 Map</a>
</div>
""", title="Reservation Confirmed")

@app.route("/searchVehicle", methods=["POST"])
def search_vehicle():
    vehicle = request.form.get("vehicle", "").strip().upper()
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM parking WHERE vehicle = ? AND exit_time IS NULL", (vehicle,)
    ).fetchone()
    conn.close()

    if not row:
        return html_page(f'<div class="error-icon">🔍</div><h1>Not Found</h1><p class="sub">No active parking record for <b>{vehicle}</b>.</p><div class="btn-row"><a class="btn btn-primary" href="/search">← Search Again</a></div>')

    zone = "A" if row["slot"] <= 50 else "B" if row["slot"] <= 100 else "C" if row["slot"] <= 150 else "D"

    return html_page(f"""
<div class="icon">🚗</div>
<h1>Vehicle Found</h1>
<p class="sub">Currently active parking record</p>
<div class="receipt">
  <div class="receipt-row"><span class="lbl">Name</span><span class="val">{row["name"]}</span></div>
  <div class="receipt-row"><span class="lbl">Phone</span><span class="val">{row["phone"] or "—"}</span></div>
  <div class="receipt-row"><span class="lbl">Vehicle</span><span class="val">{row["vehicle"]}</span></div>
  <div class="receipt-row"><span class="lbl">Slot</span><span class="val" style="color:#60a5fa;font-weight:800;">#{row["slot"]} — Zone {zone}</span></div>
  <div class="receipt-row"><span class="lbl">Entry Time</span><span class="val">{row["entry_time"]}</span></div>
</div>
<div class="btn-row">
  <a class="btn btn-primary" href="/search">← Search Again</a>
  <a class="btn" href="/map">📍 Map</a>
</div>
""", title="Vehicle Found")

# ─── Parking Map ───────────────────────────────────────────────────────────────

@app.route("/map")
def parking_map():
    conn = get_db()
    occupied_rows = conn.execute("SELECT slot FROM parking WHERE exit_time IS NULL").fetchall()
    reserved_rows = conn.execute("SELECT slot FROM reservations").fetchall()
    conn.close()

    occupied = {r["slot"] for r in occupied_rows}
    reserved = {r["slot"] for r in reserved_rows}
    occ_count = len(occupied)
    avail_count = TOTAL_SLOTS - occ_count
    occupancy = round((occ_count / TOTAL_SLOTS) * 100, 1)

    slots_html = ""
    for i in range(1, TOTAL_SLOTS + 1):
        if i in occupied:
            cls = "red"
        elif i in reserved:
            cls = "blue"
        else:
            cls = "green"
        slots_html += f'<div class="slot {cls}">{i}</div>'

    with open("views/map.html", "r") as f:
        template = f.read()

    content = f"""
<div class="map-stats-row">
  <div class="map-stat-card stat-total"><div class="big num-total">200</div><div class="lbl">Total Slots</div></div>
  <div class="map-stat-card stat-occ"><div class="big num-occ">{occ_count}</div><div class="lbl">Occupied</div></div>
  <div class="map-stat-card stat-avail"><div class="big num-avail">{avail_count}</div><div class="lbl">Available</div></div>
  <div class="map-stat-card stat-rate"><div class="big num-rate">{occupancy}%</div><div class="lbl">Occupancy Rate</div></div>
</div>
<div class="legend-row">
  <span class="legend-item legend-occupied"><span class="legend-dot ld-red"></span> Occupied</span>
  <span class="legend-item legend-reserved"><span class="legend-dot ld-blue"></span> Reserved</span>
  <span class="legend-item legend-available"><span class="legend-dot ld-green"></span> Available</span>
</div>
<div class="grid-wrap">{slots_html}</div>
<div style="display:flex;gap:12px;flex-wrap:wrap;margin-top:24px;">
  <a href="/dashboard" style="display:inline-flex;align-items:center;gap:8px;color:#94a3b8;text-decoration:none;padding:10px 18px;border-radius:10px;border:1px solid rgba(255,255,255,.08);font-size:.875rem;font-weight:500;font-family:Inter,sans-serif;">← Dashboard</a>
  <a href="/user" style="display:inline-flex;align-items:center;gap:8px;color:#94a3b8;text-decoration:none;padding:10px 18px;border-radius:10px;border:1px solid rgba(255,255,255,.08);font-size:.875rem;font-weight:500;font-family:Inter,sans-serif;">👤 User Portal</a>
</div>
"""
    return template.replace("{{CONTENT}}", content)

# ─── AI API Endpoints ──────────────────────────────────────────────────────────

@app.route("/api/predictions")
def api_predictions():
    """Returns 24-hour occupancy forecast as JSON."""
    data = get_next_24h_predictions()
    return jsonify({"predictions": data})

@app.route("/api/insights")
def api_insights():
    """Returns current peak status, strategy, next peak, and today stats."""
    peak = get_current_peak_status()
    today = get_today_stats()
    busiest = get_busiest_hours()
    occupied, reserved = get_occupied_and_reserved()
    live_count = len(occupied)
    live_pct = round((live_count / TOTAL_SLOTS) * 100, 1)

    return jsonify({
        "peak": peak,
        "today": today,
        "busiest_hours": busiest,
        "live_count": live_count,
        "live_percent": live_pct,
        "available": TOTAL_SLOTS - live_count,
    })

# ─── Entrypoint ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 Smart Parking AI Server starting on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
