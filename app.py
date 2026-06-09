"""
Smart Parking System — Python/Flask Backend
AI-powered slot allocation, billing, ANPR, demo mode, and zone management.
"""

import sqlite3
import os
import io
import csv
import json
import math
import random
import string
from datetime import datetime, timedelta
from functools import wraps
from flask import (
    Flask, request, session, redirect,
    send_from_directory, jsonify, make_response, Response
)
import bcrypt
from ai_engine import (
    get_smart_slot, get_next_24h_predictions, get_current_peak_status,
    get_today_stats, get_busiest_hours, get_zone_occupancy, get_anomalies,
    get_revenue_stats, calculate_bill, get_report_data, slot_to_label, label_to_slot,
    ZONES, TOTAL_SLOTS,
)

# ─── App Setup ────────────────────────────────────────────────────────────────

app = Flask(__name__, static_folder="views", static_url_path="")
app.secret_key = os.environ.get("SECRET_KEY", "smartparking_ai_2025_secret")
DB_PATH = "database.db"

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
            name TEXT NOT NULL, phone TEXT, vehicle TEXT UNIQUE,
            slot INTEGER, entry_time TEXT, exit_time TEXT,
            amount_charged REAL, duration_minutes REAL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS reservations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id TEXT UNIQUE, name TEXT, vehicle TEXT,
            slot INTEGER, reservation_date TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS admin (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE, password TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY, value TEXT
        )
    """)
    # Default billing rates
    cur.execute("INSERT OR IGNORE INTO settings VALUES ('hourly_rate', '20')")
    cur.execute("INSERT OR IGNORE INTO settings VALUES ('daily_rate', '250')")
    cur.execute("INSERT OR IGNORE INTO settings VALUES ('allocation_mode', 'ai')")
    conn.commit()
    conn.close()
    print("✅ Database ready")

def get_setting(key, default=None):
    try:
        conn = get_db()
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        conn.close()
        return row["value"] if row else default
    except Exception:
        return default

def get_billing_rates():
    hourly = float(get_setting("hourly_rate", "20"))
    daily = float(get_setting("daily_rate", "250"))
    return hourly, daily

# ─── Helpers ──────────────────────────────────────────────────────────────────

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin"):
            return redirect("/admin")
        return f(*args, **kwargs)
    return decorated

def get_occupied_and_reserved():
    conn = get_db()
    occupied = {r["slot"] for r in conn.execute("SELECT slot FROM parking WHERE exit_time IS NULL").fetchall()}
    reserved = {r["slot"] for r in conn.execute("SELECT slot FROM reservations").fetchall()}
    conn.close()
    return occupied, reserved

SIDEBAR_LINKS = [
    ("dashboard", "/dashboard", "🏠", "Dashboard", "Overview"),
    ("history", "/history", "📜", "History", "Management"),
    ("stats", "/stats", "📊", "Statistics", "Management"),
    ("reservations", "/reservations", "📋", "Reservations", "Management"),
    ("map", "/map", "🗺️", "Live Map", "Management"),
    ("report", "/report", "📈", "Reports", "Management"),
    ("anpr", "/anpr", "📷", "ANPR Scanner", "Tools"),
    ("demo", "/demo", "🎮", "Demo Mode", "Tools"),
    ("admin_settings", "/admin/settings", "⚙️", "Settings", "Tools"),
    ("user", "/user", "👤", "Park Vehicle", "User Portal"),
    ("exit", "/exit", "🚪", "Vehicle Exit", "User Portal"),
    ("search", "/search", "🔍", "Search", "User Portal"),
]

def sidebar_html(active="dashboard"):
    groups = {}
    for id_, url, icon, label, group in SIDEBAR_LINKS:
        groups.setdefault(group, []).append((id_, url, icon, label))

    out = '<div class="sidebar-logo"><div class="logo-icon">🚗</div><div><h2>Smart Parking</h2><small>AI Platform</small></div></div>'
    for group, items in groups.items():
        out += f'<div class="nav-label">{group}</div>'
        for id_, url, icon, label in items:
            cls = "active" if id_ == active else ""
            out += f'<a href="{url}" class="{cls}"><span class="nav-icon">{icon}</span> {label}</a>'

    out += '''<div class="sidebar-footer" style="margin-top:auto;padding-top:20px;border-top:1px solid rgba(255,255,255,.06);">
      <a href="/logout" style="display:flex;align-items:center;gap:10px;color:#f87171;text-decoration:none;padding:12px 16px;border-radius:12px;font-size:.9rem;font-weight:500;border:1px solid rgba(239,68,68,.15);background:rgba(239,68,68,.05);">⏻ Logout</a>
    </div>'''
    return out

def page_shell(title, active, content, extra_head=""):
    sb = sidebar_html(active)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{title} — Smart Parking AI</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/css/style.css">
{extra_head}
</head>
<body style="padding:0;">
<div class="dashboard">
  <div class="sidebar">{sb}</div>
  <div class="main" style="position:relative;z-index:1;overflow-y:auto;">{content}</div>
</div>
</body></html>"""

def receipt_html(content, title="Smart Parking"):
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{title}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
*{{margin:0;padding:0;box-sizing:border-box;font-family:'Inter',sans-serif;}}
body{{min-height:100vh;background:#04080f;color:#f0f4ff;display:flex;align-items:center;justify-content:center;padding:20px;}}
body::before{{content:'';position:fixed;inset:0;background:radial-gradient(ellipse 70% 60% at 30% 20%,rgba(37,99,235,.14) 0%,transparent 65%),radial-gradient(ellipse 50% 50% at 75% 80%,rgba(6,182,212,.08) 0%,transparent 60%);pointer-events:none;}}
body::after{{content:'';position:fixed;inset:0;background-image:linear-gradient(rgba(255,255,255,.018) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.018) 1px,transparent 1px);background-size:60px 60px;pointer-events:none;}}
.box{{position:relative;z-index:1;max-width:560px;width:100%;background:rgba(6,12,24,.9);border:1px solid rgba(255,255,255,.12);border-radius:28px;padding:44px 40px;box-shadow:0 30px 70px rgba(0,0,0,.6);overflow:hidden;}}
.box::before{{content:'';position:absolute;top:0;left:0;right:0;height:1px;background:linear-gradient(90deg,transparent,rgba(59,130,246,.6),transparent);}}
.icon{{width:72px;height:72px;border-radius:20px;background:linear-gradient(135deg,rgba(37,99,235,.2),rgba(6,182,212,.15));border:1px solid rgba(59,130,246,.3);display:flex;align-items:center;justify-content:center;font-size:2rem;margin:0 auto 20px;}}
h1{{text-align:center;font-size:1.8rem;font-weight:800;letter-spacing:-.03em;margin-bottom:8px;}}
.sub{{text-align:center;color:#94a3b8;margin-bottom:24px;font-size:.9rem;}}
.receipt{{background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.08);border-radius:14px;padding:20px 24px;margin-bottom:20px;}}
.receipt-row{{display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid rgba(255,255,255,.05);font-size:.88rem;}}
.receipt-row:last-child{{border-bottom:none;}}
.lbl{{color:#64748b;font-weight:500;}}.val{{color:#f0f4ff;font-weight:600;}}
.badge{{display:inline-flex;align-items:center;gap:6px;padding:5px 12px;border-radius:100px;font-size:.76rem;font-weight:700;letter-spacing:.04em;}}
.badge-green{{background:rgba(16,185,129,.15);border:1px solid rgba(16,185,129,.3);color:#34d399;}}
.badge-red{{background:rgba(239,68,68,.15);border:1px solid rgba(239,68,68,.3);color:#f87171;}}
.badge-blue{{background:rgba(59,130,246,.15);border:1px solid rgba(59,130,246,.3);color:#93c5fd;}}
.badge-yellow{{background:rgba(245,158,11,.15);border:1px solid rgba(245,158,11,.3);color:#fcd34d;}}
.ai-note{{background:rgba(37,99,235,.08);border:1px solid rgba(59,130,246,.2);border-radius:12px;padding:12px 16px;margin-bottom:18px;font-size:.82rem;color:#93c5fd;}}
.ai-note strong{{display:block;margin-bottom:3px;color:#60a5fa;font-size:.78rem;letter-spacing:.03em;text-transform:uppercase;}}
.bill-total{{background:linear-gradient(135deg,rgba(37,99,235,.1),rgba(6,182,212,.07));border:1px solid rgba(59,130,246,.25);border-radius:12px;padding:14px 20px;margin-bottom:18px;display:flex;justify-content:space-between;align-items:center;}}
.bill-total .amount{{font-size:1.7rem;font-weight:900;background:linear-gradient(135deg,#60a5fa,#34d399);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;}}
.btn-row{{display:flex;flex-wrap:wrap;gap:10px;}}
.btn{{flex:1;min-width:110px;padding:11px 16px;border-radius:10px;font-size:.85rem;font-weight:600;text-decoration:none;text-align:center;transition:.2s ease;border:1px solid rgba(255,255,255,.1);color:#94a3b8;background:rgba(255,255,255,.04);cursor:pointer;}}
.btn:hover{{background:rgba(255,255,255,.08);color:#f0f4ff;border-color:rgba(255,255,255,.18);}}
.btn-primary{{background:linear-gradient(135deg,#2563eb,#06b6d4);border-color:transparent;color:white;}}
.btn-primary:hover{{box-shadow:0 6px 24px rgba(37,99,235,.45);transform:translateY(-1px);}}
@media print{{body{{background:white;color:black;}}.btn-row{{display:none;}}.box{{border:1px solid #ccc;box-shadow:none;background:white;}}}}
</style>
</head>
<body><div class="box">{content}</div></body>
</html>"""

# ─── Static Files ──────────────────────────────────────────────────────────────

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
        return receipt_html('<div class="icon">⚠️</div><h1>Missing Fields</h1><div class="btn-row"><a class="btn btn-primary" href="/admin">← Back</a></div>')
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    try:
        conn = get_db(); conn.execute("INSERT INTO admin (username, password) VALUES (?, ?)", (username, hashed)); conn.commit(); conn.close()
    except sqlite3.IntegrityError:
        return receipt_html('<div class="icon">❌</div><h1>Username Taken</h1><div class="btn-row"><a class="btn btn-primary" href="/admin">← Back</a></div>')
    return redirect("/admin")

@app.route("/adminLogin", methods=["POST"])
def admin_login_post():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "").strip()
    conn = get_db()
    admin = conn.execute("SELECT * FROM admin WHERE username = ?", (username,)).fetchone()
    conn.close()
    if not admin:
        return receipt_html('<div class="icon">❌</div><h1>User Not Found</h1><div class="btn-row"><a class="btn btn-primary" href="/admin">← Try Again</a></div>')
    if not bcrypt.checkpw(password.encode(), admin["password"].encode()):
        return receipt_html('<div class="icon">🔒</div><h1>Wrong Password</h1><div class="btn-row"><a class="btn btn-primary" href="/admin">← Try Again</a></div>')
    session["admin"] = True
    session["admin_user"] = username
    return redirect("/dashboard")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/admin")

@app.route("/resetAdmin")
def reset_admin():
    conn = get_db(); conn.execute("DELETE FROM admin"); conn.commit(); conn.close()
    return receipt_html('<div class="icon">✅</div><h1>Admin Reset</h1><div class="btn-row"><a class="btn btn-primary" href="/admin">Go to Login</a></div>')

# ─── Dashboard ────────────────────────────────────────────────────────────────

@app.route("/dashboard")
@admin_required
def dashboard():
    return send_from_directory("views", "dashboard.html")

# ─── Parking Entry ─────────────────────────────────────────────────────────────

@app.route("/submit", methods=["POST"])
def submit():
    name = request.form.get("name", "").strip()
    phone = request.form.get("phone", "").strip()
    vehicle = request.form.get("vehicle", "").strip().upper()
    if not name or not phone or not vehicle:
        return receipt_html('<div class="icon">⚠️</div><h1>Missing Fields</h1><p class="sub">All fields are required.</p><div class="btn-row"><a class="btn btn-primary" href="/user">← Back</a></div>')
    if not phone.isdigit() or len(phone) != 10:
        return receipt_html('<div class="icon">📞</div><h1>Invalid Phone</h1><p class="sub">Must be exactly 10 digits.</p><div class="btn-row"><a class="btn btn-primary" href="/user">← Back</a></div>')

    conn = get_db()
    existing = conn.execute("SELECT * FROM parking WHERE vehicle = ? AND exit_time IS NULL", (vehicle,)).fetchone()
    if existing:
        conn.close()
        lbl = slot_to_label(existing["slot"])
        return receipt_html(f'<div class="icon">🚗</div><h1>Already Parked</h1><p class="sub">Vehicle <b>{vehicle}</b> is in slot <b>{lbl}</b>.</p><div class="btn-row"><a class="btn btn-primary" href="/user">← Back</a><a class="btn" href="/map">📍 Map</a></div>')

    occupied = {r["slot"] for r in conn.execute("SELECT slot FROM parking WHERE exit_time IS NULL").fetchall()}
    reserved = {r["slot"] for r in conn.execute("SELECT slot FROM reservations").fetchall()}
    conn.close()

    peak_status = get_current_peak_status()
    slot = get_smart_slot(occupied, reserved)
    if slot is None:
        return receipt_html('<div class="icon">🅿</div><h1>Parking Full</h1><p class="sub">All 200 slots are occupied. Try again later.</p><div class="btn-row"><a class="btn btn-primary" href="/user">← Back</a></div>')

    entry_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db()
    try:
        conn.execute("INSERT INTO parking (name, phone, vehicle, slot, entry_time) VALUES (?, ?, ?, ?, ?)", (name, phone, vehicle, slot, entry_time))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return receipt_html(f'<div class="icon">⚠️</div><h1>Duplicate Vehicle</h1><div class="btn-row"><a class="btn btn-primary" href="/user">← Back</a></div>')
    conn.close()

    lbl = slot_to_label(slot)
    zone = lbl[0]
    hourly, daily = get_billing_rates()
    strategy_map = {"zone_rotation": "Zone Rotation (Peak)", "balanced": "Balanced", "sequential": "Standard"}
    ai_mode = strategy_map.get(peak_status["strategy"], "Standard")

    return receipt_html(f"""
<div class="icon">✅</div>
<h1>Parking Confirmed!</h1>
<p class="sub">AI-assigned slot — {peak_status["status_label"]}</p>
<div class="ai-note">
  <strong>🤖 AI Slot Engine</strong>
  Demand: <b>{peak_status["current_percent"]}%</b> predicted — Mode: <b>{ai_mode}</b>
</div>
<div class="receipt">
  <div class="receipt-row"><span class="lbl">Name</span><span class="val">{name}</span></div>
  <div class="receipt-row"><span class="lbl">Phone</span><span class="val">{phone}</span></div>
  <div class="receipt-row"><span class="lbl">Vehicle</span><span class="val">{vehicle}</span></div>
  <div class="receipt-row"><span class="lbl">Zone & Slot</span><span class="val" style="font-size:1.1rem;color:#60a5fa;font-weight:800;">Zone {zone} — {lbl}</span></div>
  <div class="receipt-row"><span class="lbl">Entry Time</span><span class="val">{datetime.now().strftime("%I:%M %p, %d %b %Y")}</span></div>
  <div class="receipt-row"><span class="lbl">Rate</span><span class="val">₹{int(hourly)}/hr · ₹{int(daily)}/day</span></div>
</div>
<div class="btn-row">
  <a class="btn btn-primary" href="/user">← Home</a>
  <a class="btn" href="/zone/{zone}">Zone {zone} Map</a>
  <a class="btn" href="/map">📍 Full Map</a>
</div>
""", title="Parking Confirmed")

# ─── Vehicle Exit & Billing ────────────────────────────────────────────────────

@app.route("/exitVehicle", methods=["POST"])
def exit_vehicle():
    vehicle = request.form.get("vehicle", "").strip().upper()
    conn = get_db()
    row = conn.execute("SELECT * FROM parking WHERE vehicle = ? AND exit_time IS NULL", (vehicle,)).fetchone()
    if not row:
        conn.close()
        return receipt_html(f'<div class="icon">❌</div><h1>Vehicle Not Found</h1><p class="sub">No active record for <b>{vehicle}</b>.</p><div class="btn-row"><a class="btn btn-primary" href="/exit">← Try Again</a></div>')

    exit_dt = datetime.now()
    exit_str = exit_dt.strftime("%Y-%m-%d %H:%M:%S")
    hourly, daily = get_billing_rates()
    amount, duration_str, hours = calculate_bill(row["entry_time"], exit_str, hourly, daily)
    duration_minutes = round(hours * 60)

    conn.execute("UPDATE parking SET exit_time=?, amount_charged=?, duration_minutes=? WHERE vehicle=?",
                 (exit_str, amount, duration_minutes, vehicle))
    conn.commit()
    conn.close()

    lbl = slot_to_label(row["slot"])
    zone = lbl[0]
    entry_dt = datetime.strptime(row["entry_time"], "%Y-%m-%d %H:%M:%S")

    return receipt_html(f"""
<div class="icon">🚪</div>
<h1>Exit Successful</h1>
<p class="sub">Slot {lbl} released — Bill generated</p>
<div class="bill-total">
  <div>
    <div style="font-size:.75rem;color:#64748b;font-weight:600;letter-spacing:.04em;text-transform:uppercase;margin-bottom:4px;">Total Charge</div>
    <div style="font-size:.8rem;color:#94a3b8;">Duration: {duration_str}</div>
  </div>
  <div class="amount">₹{int(amount)}</div>
</div>
<div class="receipt">
  <div class="receipt-row"><span class="lbl">Vehicle</span><span class="val">{vehicle}</span></div>
  <div class="receipt-row"><span class="lbl">Name</span><span class="val">{row["name"]}</span></div>
  <div class="receipt-row"><span class="lbl">Slot Released</span><span class="val" style="color:#60a5fa;">{lbl} — Zone {zone}</span></div>
  <div class="receipt-row"><span class="lbl">Entry</span><span class="val">{entry_dt.strftime("%I:%M %p, %d %b")}</span></div>
  <div class="receipt-row"><span class="lbl">Exit</span><span class="val">{exit_dt.strftime("%I:%M %p, %d %b")}</span></div>
  <div class="receipt-row"><span class="lbl">Duration</span><span class="val">{duration_str}</span></div>
  <div class="receipt-row"><span class="lbl">Rate Applied</span><span class="val">₹{int(hourly)}/hr</span></div>
  <div class="receipt-row"><span class="lbl">Total Charged</span><span class="val" style="color:#34d399;font-weight:800;font-size:1rem;">₹{int(amount)}</span></div>
</div>
<div class="btn-row">
  <a class="btn btn-primary" href="/user">← Home</a>
  <a class="btn" onclick="window.print()" style="cursor:pointer;">🖨 Print</a>
  <a class="btn" href="/receipt/{vehicle}">📄 PDF</a>
  <a class="btn" href="/map">📍 Map</a>
</div>
""", title="Exit & Bill")

# ─── PDF Receipt ───────────────────────────────────────────────────────────────

@app.route("/receipt/<vehicle>")
def download_receipt(vehicle):
    vehicle = vehicle.upper()
    conn = get_db()
    row = conn.execute("SELECT * FROM parking WHERE vehicle = ? ORDER BY id DESC LIMIT 1", (vehicle,)).fetchone()
    conn.close()
    if not row:
        return receipt_html(f'<div class="icon">❌</div><h1>No Record Found</h1><div class="btn-row"><a class="btn btn-primary" href="/user">← Home</a></div>')

    try:
        from fpdf import FPDF
        pdf = FPDF()
        pdf.add_page()
        pdf.set_fill_color(15, 23, 42)
        pdf.rect(0, 0, 210, 297, 'F')
        pdf.set_text_color(240, 244, 255)
        pdf.set_font("Helvetica", "B", 22)
        pdf.cell(0, 18, "SMART PARKING SYSTEM", align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 11)
        pdf.set_text_color(148, 163, 184)
        pdf.cell(0, 8, "AI-Powered Parking Receipt", align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.cell(0, 8, datetime.now().strftime("%d %B %Y, %I:%M %p"), align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(8)
        pdf.set_draw_color(37, 99, 235)
        pdf.set_line_width(0.5)
        pdf.line(15, pdf.get_y(), 195, pdf.get_y())
        pdf.ln(8)

        lbl = slot_to_label(row["slot"])
        zone = lbl[0]
        amount = row["amount_charged"] or 0
        hourly, daily = get_billing_rates()

        fields = [
            ("Name", row["name"]),
            ("Vehicle Number", row["vehicle"]),
            ("Phone", row["phone"] or "N/A"),
            ("Zone & Slot", f"Zone {zone} — {lbl}"),
            ("Entry Time", row["entry_time"]),
            ("Exit Time", row["exit_time"] or "Active"),
            ("Duration", f"{round((row['duration_minutes'] or 0))} min"),
            ("Rate Applied", f"Rs {int(hourly)}/hr"),
            ("TOTAL CHARGE", f"Rs {int(amount)}"),
        ]

        for label, value in fields:
            pdf.set_font("Helvetica", "B" if label == "TOTAL CHARGE" else "", 10)
            pdf.set_text_color(100, 116, 139)
            pdf.cell(60, 8, label.upper(), new_x="RIGHT")
            if label == "TOTAL CHARGE":
                pdf.set_text_color(52, 211, 153)
                pdf.set_font("Helvetica", "B", 14)
            else:
                pdf.set_text_color(240, 244, 255)
            pdf.cell(0, 8, str(value), new_x="LMARGIN", new_y="NEXT")
            pdf.set_draw_color(255, 255, 255, 10)

        pdf.ln(10)
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(100, 116, 139)
        pdf.cell(0, 6, "Thank you for using Smart Parking AI System", align="C", new_x="LMARGIN", new_y="NEXT")

        buf = io.BytesIO(pdf.output())
        resp = make_response(buf.read())
        resp.headers["Content-Type"] = "application/pdf"
        resp.headers["Content-Disposition"] = f"attachment; filename=receipt_{vehicle}.pdf"
        return resp
    except Exception as e:
        return receipt_html(f'<div class="icon">📄</div><h1>PDF Error</h1><p class="sub">{str(e)}</p><div class="btn-row"><a class="btn btn-primary" href="/user">← Home</a></div>')

# ─── Reservation & Search ──────────────────────────────────────────────────────

@app.route("/reserveSlot", methods=["POST"])
def reserve_slot():
    employee_id = request.form.get("employee_id", "").strip()
    name = request.form.get("name", "").strip()
    vehicle = request.form.get("vehicle", "").strip().upper()
    slot_input = request.form.get("slot", "").strip()
    if not all([employee_id, name, vehicle, slot_input]):
        return receipt_html('<div class="icon">⚠️</div><h1>Missing Fields</h1><div class="btn-row"><a class="btn btn-primary" href="/reserve">← Back</a></div>')
    # Accept both "A1" zone label and "1" integer
    if slot_input[0].upper() in "ABCD" and len(slot_input) > 1:
        slot = label_to_slot(slot_input)
    else:
        try: slot = int(slot_input)
        except: slot = None
    if not slot or slot < 1 or slot > 200:
        return receipt_html('<div class="icon">⚠️</div><h1>Invalid Slot</h1><p class="sub">Enter slot 1-200 or zone label like A1, B23.</p><div class="btn-row"><a class="btn btn-primary" href="/reserve">← Back</a></div>')
    conn = get_db()
    try:
        conn.execute("INSERT INTO reservations (employee_id, name, vehicle, slot, reservation_date) VALUES (?,?,?,?,?)",
                     (employee_id, name, vehicle, slot, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return receipt_html('<div class="icon">❌</div><h1>Reservation Failed</h1><p class="sub">Employee ID already has a reservation or slot is taken.</p><div class="btn-row"><a class="btn btn-primary" href="/reserve">← Back</a></div>')
    conn.close()
    lbl = slot_to_label(slot)
    return receipt_html(f"""
<div class="icon">🏢</div><h1>Reserved!</h1><p class="sub">Staff slot pre-reserved</p>
<div class="receipt">
  <div class="receipt-row"><span class="lbl">Employee ID</span><span class="val">{employee_id}</span></div>
  <div class="receipt-row"><span class="lbl">Name</span><span class="val">{name}</span></div>
  <div class="receipt-row"><span class="lbl">Vehicle</span><span class="val">{vehicle}</span></div>
  <div class="receipt-row"><span class="lbl">Reserved Slot</span><span class="val" style="color:#60a5fa;font-weight:800;">Zone {lbl[0]} — {lbl}</span></div>
</div>
<div class="btn-row"><a class="btn btn-primary" href="/user">← Home</a><a class="btn" href="/map">📍 Map</a></div>
""", title="Reserved")

@app.route("/searchVehicle", methods=["POST"])
def search_vehicle():
    vehicle = request.form.get("vehicle", "").strip().upper()
    conn = get_db()
    row = conn.execute("SELECT * FROM parking WHERE vehicle = ? AND exit_time IS NULL", (vehicle,)).fetchone()
    conn.close()
    if not row:
        return receipt_html(f'<div class="icon">🔍</div><h1>Not Found</h1><p class="sub">No active record for <b>{vehicle}</b>.</p><div class="btn-row"><a class="btn btn-primary" href="/search">← Search Again</a></div>')
    lbl = slot_to_label(row["slot"])
    hourly, daily = get_billing_rates()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    est_amount, duration_str, _ = calculate_bill(row["entry_time"], now_str, hourly, daily)
    return receipt_html(f"""
<div class="icon">🚗</div><h1>Vehicle Found</h1><p class="sub">Currently active</p>
<div class="receipt">
  <div class="receipt-row"><span class="lbl">Name</span><span class="val">{row["name"]}</span></div>
  <div class="receipt-row"><span class="lbl">Phone</span><span class="val">{row["phone"] or "—"}</span></div>
  <div class="receipt-row"><span class="lbl">Vehicle</span><span class="val">{row["vehicle"]}</span></div>
  <div class="receipt-row"><span class="lbl">Zone & Slot</span><span class="val" style="color:#60a5fa;font-weight:800;">Zone {lbl[0]} — {lbl}</span></div>
  <div class="receipt-row"><span class="lbl">Entry Time</span><span class="val">{row["entry_time"]}</span></div>
  <div class="receipt-row"><span class="lbl">Duration So Far</span><span class="val">{duration_str}</span></div>
  <div class="receipt-row"><span class="lbl">Est. Charge</span><span class="val" style="color:#fcd34d;">₹{int(est_amount)}</span></div>
</div>
<div class="btn-row"><a class="btn btn-primary" href="/search">← Search Again</a><a class="btn" href="/zone/{lbl[0]}">Zone Map</a></div>
""", title="Vehicle Found")

# ─── Admin Management Pages ────────────────────────────────────────────────────

def _make_sidebar_page(title, active, body_content, extra_head=""):
    return page_shell(title, active, body_content, extra_head)

@app.route("/history")
@admin_required
def history():
    conn = get_db()
    rows = conn.execute("SELECT * FROM parking ORDER BY id DESC").fetchall()
    conn.close()
    anomalies = get_anomalies()
    long_stay_ids = {a["id"] for a in anomalies if a.get("type") == "long_stay"}

    rows_html = ""
    for r in rows:
        lbl = slot_to_label(r["slot"]) if r["slot"] else "—"
        status = '<span class="badge-red">Exited</span>' if r["exit_time"] else '<span class="badge-green">Active</span>'
        warn = ' <span style="color:#fbbf24;font-size:.7rem;">⚠ Long Stay</span>' if r["id"] in long_stay_ids else ""
        amount = f'₹{int(r["amount_charged"])}' if r["amount_charged"] else "—"
        rows_html += f"""<tr>
          <td>{r["id"]}</td><td>{r["name"]}</td><td>{r["phone"] or "—"}</td>
          <td><b>{r["vehicle"]}</b>{warn}</td>
          <td><b>{lbl}</b></td>
          <td style="font-size:.78rem;color:#64748b">{r["entry_time"]}</td>
          <td style="color:#34d399;font-weight:700">{amount}</td>
          <td>{status}</td>
          <td><a class="delete-btn" href="/delete/{r["id"]}">🗑 Del</a></td>
        </tr>"""

    alert_html = ""
    if anomalies:
        crit = [a for a in anomalies if a["severity"] == "critical"]
        if crit:
            alert_html = f'''<div style="background:rgba(239,68,68,.08);border:1px solid rgba(239,68,68,.25);border-radius:14px;padding:16px 20px;margin-bottom:20px;display:flex;align-items:center;gap:12px;">
              <span style="font-size:1.5rem;">🚨</span>
              <div><div style="font-weight:700;color:#fca5a5;font-size:.9rem;">{len(crit)} Critical Anomaly{"s" if len(crit)>1 else ""}</div>
              <div style="font-size:.8rem;color:#94a3b8;">{crit[0]["message"]}</div></div>
              <a href="/api/anomalies" style="margin-left:auto;color:#60a5fa;font-size:.78rem;text-decoration:none;">View All →</a></div>'''

    content = f"""
<div class="page-header"><h1>📜 Parking History</h1><p style="color:#94a3b8;margin-top:5px">Full vehicle log with billing data</p></div>
{alert_html}
<div style="overflow-x:auto;border-radius:16px;border:1px solid rgba(255,255,255,.07);">
  <table class="table">
    <thead><tr><th>ID</th><th>Name</th><th>Phone</th><th>Vehicle</th><th>Slot</th><th>Entry Time</th><th>Charge</th><th>Status</th><th>Action</th></tr></thead>
    <tbody>{rows_html}</tbody>
  </table>
</div>
<div style="margin-top:16px;display:flex;gap:10px;">
  <a href="/api/export/csv" style="display:inline-flex;align-items:center;gap:8px;padding:10px 18px;background:rgba(16,185,129,.1);border:1px solid rgba(16,185,129,.25);border-radius:10px;color:#34d399;text-decoration:none;font-size:.85rem;font-weight:600;">⬇ Export CSV</a>
</div>"""
    return page_shell("Parking History", "history", content)

@app.route("/reservations")
@admin_required
def reservations():
    conn = get_db()
    rows = conn.execute("SELECT * FROM reservations ORDER BY id DESC").fetchall()
    conn.close()
    rows_html = ""
    for r in rows:
        lbl = slot_to_label(r["slot"])
        rows_html += f"""<tr>
          <td>{r["id"]}</td><td><b>{r["employee_id"]}</b></td>
          <td>{r["name"]}</td><td>{r["vehicle"]}</td>
          <td><span class="badge-blue">Zone {lbl[0]} — {lbl}</span></td>
          <td style="font-size:.78rem;color:#64748b">{r["reservation_date"]}</td>
        </tr>"""
    content = f"""
<div class="page-header"><h1>📋 Staff Reservations</h1><p style="color:#94a3b8;margin-top:5px">Pre-reserved employee slots</p></div>
<div style="overflow-x:auto;border-radius:16px;border:1px solid rgba(255,255,255,.07);">
  <table class="table">
    <thead><tr><th>ID</th><th>Employee ID</th><th>Name</th><th>Vehicle</th><th>Slot</th><th>Reserved On</th></tr></thead>
    <tbody>{rows_html}</tbody>
  </table>
</div>"""
    return page_shell("Reservations", "reservations", content)

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
    rev = get_revenue_stats()
    busiest_html = "".join([
        f'<div style="display:flex;justify-content:space-between;align-items:center;padding:10px 0;border-bottom:1px solid rgba(255,255,255,.05);"><span style="color:#94a3b8;font-size:.88rem">{b["label"]}</span><div style="display:flex;align-items:center;gap:10px;"><div style="width:100px;height:5px;border-radius:3px;background:rgba(255,255,255,.06);"><div style="width:{b["percent"]}%;height:100%;border-radius:3px;background:linear-gradient(90deg,#2563eb,#06b6d4);"></div></div><span style="font-weight:700;font-size:.85rem;">{b["percent"]}%</span></div></div>'
        for b in busiest
    ])
    content = f"""
<div class="page-header"><h1>📊 Statistics</h1><p style="color:#94a3b8;margin-top:5px">Analytics & performance metrics</p></div>
<div class="stats-grid" style="margin-bottom:24px;">
  <div class="stat-card"><h2 style="color:#93c5fd">{total}</h2><span>Total Records</span></div>
  <div class="stat-card"><h2 style="color:#6ee7b7">{active}</h2><span>Currently Parked</span></div>
  <div class="stat-card"><h2 style="color:#fca5a5">{exited}</h2><span>Exited</span></div>
  <div class="stat-card"><h2 style="color:#fcd34d">{occupancy}%</h2><span>Occupancy Rate</span></div>
  <div class="stat-card"><h2 style="color:#a5b4fc">{today_data["today_entries"]}</h2><span>Today's Entries</span></div>
  <div class="stat-card"><h2>{today_data["avg_stay_minutes"]}<small style="font-size:1rem;color:#64748b"> min</small></h2><span>Avg Stay</span></div>
  <div class="stat-card"><h2 style="color:#34d399">₹{int(rev["today"])}</h2><span>Revenue Today</span></div>
  <div class="stat-card"><h2 style="color:#67e8f9">₹{int(rev["month"])}</h2><span>Revenue This Month</span></div>
</div>
<div style="background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.07);border-radius:16px;padding:24px;">
  <h3 style="font-size:.8rem;font-weight:700;color:#64748b;letter-spacing:.06em;text-transform:uppercase;margin-bottom:16px;">🤖 AI Predicted Peak Hours</h3>
  {busiest_html}
</div>"""
    return page_shell("Statistics", "stats", content)

@app.route("/delete/<int:record_id>")
@admin_required
def delete_record(record_id):
    conn = get_db(); conn.execute("DELETE FROM parking WHERE id = ?", (record_id,)); conn.commit(); conn.close()
    return redirect("/history")

# ─── Live Map ──────────────────────────────────────────────────────────────────

@app.route("/map")
def parking_map():
    conn = get_db()
    occupied = {r["slot"] for r in conn.execute("SELECT slot FROM parking WHERE exit_time IS NULL").fetchall()}
    reserved = {r["slot"] for r in conn.execute("SELECT slot FROM reservations").fetchall()}
    conn.close()
    occ_count = len(occupied)
    avail_count = TOTAL_SLOTS - occ_count
    occupancy = round((occ_count / TOTAL_SLOTS) * 100, 1)

    # Zone sections
    zone_sections = ""
    for zone_letter, (start, end) in ZONES.items():
        zone_occ = sum(1 for s in range(start, end + 1) if s in occupied)
        slots_html = ""
        for i in range(start, end + 1):
            lbl = slot_to_label(i)
            if i in occupied: cls = "red"
            elif i in reserved: cls = "blue"
            else: cls = "green"
            slots_html += f'<div class="slot {cls}" title="{lbl}">{lbl}</div>'
        zone_pct = round((zone_occ / 50) * 100)
        zone_sections += f"""
<div style="margin-bottom:24px;">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;">
    <div style="display:flex;align-items:center;gap:12px;">
      <div style="width:36px;height:36px;border-radius:10px;background:rgba(37,99,235,.2);border:1px solid rgba(59,130,246,.3);display:flex;align-items:center;justify-content:center;font-weight:800;font-size:.9rem;color:#93c5fd;">Z{zone_letter}</div>
      <div><div style="font-weight:700;font-size:.95rem;">Zone {zone_letter}</div><div style="font-size:.75rem;color:#64748b;">{zone_occ}/50 occupied</div></div>
    </div>
    <div style="display:flex;align-items:center;gap:10px;">
      <div style="width:80px;height:5px;border-radius:3px;background:rgba(255,255,255,.06);">
        <div style="width:{zone_pct}%;height:100%;border-radius:3px;background:{'#ef4444' if zone_pct>=80 else '#f59e0b' if zone_pct>=55 else '#10b981'};"></div>
      </div>
      <span style="font-size:.8rem;font-weight:700;color:{'#fca5a5' if zone_pct>=80 else '#fcd34d' if zone_pct>=55 else '#6ee7b7'};">{zone_pct}%</span>
      <a href="/zone/{zone_letter}" style="color:#60a5fa;font-size:.75rem;text-decoration:none;padding:4px 10px;border:1px solid rgba(59,130,246,.3);border-radius:6px;">Detail →</a>
    </div>
  </div>
  <div style="background:rgba(255,255,255,.025);border:1px solid rgba(255,255,255,.06);border-radius:14px;padding:16px;display:flex;flex-wrap:wrap;gap:5px;">{slots_html}</div>
</div>"""

    with open("views/map.html") as f:
        template = f.read()

    content = f"""
<div class="map-stats-row">
  <div class="map-stat-card stat-total"><div class="big num-total">200</div><div class="lbl">Total</div></div>
  <div class="map-stat-card stat-occ"><div class="big num-occ">{occ_count}</div><div class="lbl">Occupied</div></div>
  <div class="map-stat-card stat-avail"><div class="big num-avail">{avail_count}</div><div class="lbl">Available</div></div>
  <div class="map-stat-card stat-rate"><div class="big num-rate">{occupancy}%</div><div class="lbl">Occupancy</div></div>
</div>
<div class="legend-row">
  <span class="legend-item legend-occupied"><span class="legend-dot ld-red"></span>Occupied</span>
  <span class="legend-item legend-reserved"><span class="legend-dot ld-blue"></span>Reserved</span>
  <span class="legend-item legend-available"><span class="legend-dot ld-green"></span>Available</span>
</div>
{zone_sections}
<div style="display:flex;gap:12px;flex-wrap:wrap;margin-top:8px;">
  <a href="/dashboard" style="display:inline-flex;align-items:center;gap:8px;color:#94a3b8;text-decoration:none;padding:10px 18px;border-radius:10px;border:1px solid rgba(255,255,255,.08);font-size:.875rem;font-weight:500;font-family:Inter,sans-serif;">← Dashboard</a>
  <a href="/user" style="display:inline-flex;align-items:center;gap:8px;color:#94a3b8;text-decoration:none;padding:10px 18px;border-radius:10px;border:1px solid rgba(255,255,255,.08);font-size:.875rem;font-weight:500;font-family:Inter,sans-serif;">👤 User Portal</a>
</div>"""
    return template.replace("{{CONTENT}}", content)

# ─── Zone Detail Page ──────────────────────────────────────────────────────────

@app.route("/zone/<zone_letter>")
def zone_detail(zone_letter):
    zone_letter = zone_letter.upper()
    if zone_letter not in ZONES:
        return redirect("/map")
    start, end = ZONES[zone_letter]
    conn = get_db()
    occupied_rows = conn.execute("SELECT slot, vehicle, name, entry_time FROM parking WHERE exit_time IS NULL").fetchall()
    reserved_rows = conn.execute("SELECT slot, name, employee_id FROM reservations").fetchall()
    conn.close()

    occupied = {r["slot"]: r for r in occupied_rows}
    reserved = {r["slot"]: r for r in reserved_rows}

    occ_count = sum(1 for s in range(start, end + 1) if s in occupied)
    res_count = sum(1 for s in range(start, end + 1) if s in reserved)
    avail_count = 50 - occ_count - res_count
    pct = round((occ_count / 50) * 100)

    slots_html = ""
    for i in range(start, end + 1):
        lbl = slot_to_label(i)
        if i in occupied:
            r = occupied[i]
            tooltip = f'{r["vehicle"]} | {r["name"]}'
            slots_html += f'<div class="zone-slot red" title="{tooltip}"><div class="slot-id">{lbl}</div><div class="slot-info">{r["vehicle"][:6]}</div></div>'
        elif i in reserved:
            r = reserved[i]
            tooltip = f'Reserved — {r["employee_id"]}'
            slots_html += f'<div class="zone-slot blue" title="{tooltip}"><div class="slot-id">{lbl}</div><div class="slot-info">RESV</div></div>'
        else:
            slots_html += f'<div class="zone-slot green" title="Available"><div class="slot-id">{lbl}</div><div class="slot-info">Free</div></div>'

    occ_list = ""
    for s in range(start, end + 1):
        if s in occupied:
            r = occupied[s]
            entry = datetime.strptime(r["entry_time"], "%Y-%m-%d %H:%M:%S")
            hrs = round((datetime.now() - entry).total_seconds() / 3600, 1)
            occ_list += f"""<tr>
              <td style="font-weight:700;color:#60a5fa">{slot_to_label(s)}</td>
              <td>{r["vehicle"]}</td><td>{r["name"]}</td>
              <td style="color:#fcd34d">{hrs}h</td>
              <td><span class="badge-green">Active</span></td>
            </tr>"""

    zone_colors = {"A": "#3b82f6", "B": "#10b981", "C": "#f59e0b", "D": "#ec4899"}
    zone_color = zone_colors.get(zone_letter, "#3b82f6")

    content = f"""
<div style="display:flex;align-items:center;gap:16px;margin-bottom:28px;flex-wrap:wrap;">
  <div style="width:56px;height:56px;border-radius:16px;background:rgba(37,99,235,.15);border:1px solid rgba(59,130,246,.3);display:flex;align-items:center;justify-content:center;font-size:1.5rem;font-weight:900;color:{zone_color}">Z{zone_letter}</div>
  <div>
    <h1 style="font-size:1.9rem;font-weight:900;letter-spacing:-.03em">Zone {zone_letter} Detail</h1>
    <p style="color:#94a3b8;margin-top:3px">Slots {slot_to_label(start)} – {slot_to_label(end)} · Interactive map</p>
  </div>
  <a href="/map" style="margin-left:auto;padding:10px 18px;border:1px solid rgba(255,255,255,.1);border-radius:10px;text-decoration:none;color:#94a3b8;font-size:.875rem;font-weight:500;">← Full Map</a>
</div>

<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:24px;">
  <div style="background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.08);border-radius:14px;padding:18px;text-align:center;">
    <div style="font-size:1.8rem;font-weight:900;color:{zone_color}">{occ_count}</div>
    <div style="font-size:.75rem;color:#64748b;margin-top:4px">Occupied</div>
  </div>
  <div style="background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.08);border-radius:14px;padding:18px;text-align:center;">
    <div style="font-size:1.8rem;font-weight:900;color:#93c5fd">{res_count}</div>
    <div style="font-size:.75rem;color:#64748b;margin-top:4px">Reserved</div>
  </div>
  <div style="background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.08);border-radius:14px;padding:18px;text-align:center;">
    <div style="font-size:1.8rem;font-weight:900;color:#6ee7b7">{avail_count}</div>
    <div style="font-size:.75rem;color:#64748b;margin-top:4px">Available</div>
  </div>
  <div style="background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.08);border-radius:14px;padding:18px;text-align:center;">
    <div style="font-size:1.8rem;font-weight:900;color:{'#fca5a5' if pct>=80 else '#fcd34d' if pct>=55 else '#6ee7b7'}">{pct}%</div>
    <div style="font-size:.75rem;color:#64748b;margin-top:4px">Fill Rate</div>
  </div>
</div>

<div style="background:rgba(255,255,255,.025);border:1px solid rgba(255,255,255,.07);border-radius:16px;padding:20px;margin-bottom:24px;">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px;flex-wrap:wrap;gap:10px;">
    <h3 style="font-size:.8rem;font-weight:700;color:#64748b;letter-spacing:.06em;text-transform:uppercase">Slot Grid — Zone {zone_letter}</h3>
    <div style="display:flex;gap:10px;font-size:.72rem;font-weight:600;">
      <span style="display:inline-flex;align-items:center;gap:6px;padding:4px 12px;border-radius:100px;background:rgba(239,68,68,.12);border:1px solid rgba(239,68,68,.25);color:#fca5a5;">● Occupied</span>
      <span style="display:inline-flex;align-items:center;gap:6px;padding:4px 12px;border-radius:100px;background:rgba(59,130,246,.12);border:1px solid rgba(59,130,246,.25);color:#93c5fd;">● Reserved</span>
      <span style="display:inline-flex;align-items:center;gap:6px;padding:4px 12px;border-radius:100px;background:rgba(16,185,129,.1);border:1px solid rgba(16,185,129,.22);color:#6ee7b7;">● Free</span>
    </div>
  </div>
  <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(72px,1fr));gap:8px;">
    {slots_html}
  </div>
</div>

{"<div style='background:rgba(255,255,255,.025);border:1px solid rgba(255,255,255,.07);border-radius:16px;overflow:hidden;'><div style='padding:16px 20px;border-bottom:1px solid rgba(255,255,255,.06);font-size:.8rem;font-weight:700;color:#64748b;letter-spacing:.06em;text-transform:uppercase;'>Active Vehicles</div><div style='overflow-x:auto'><table class='table'><thead><tr><th>Slot</th><th>Vehicle</th><th>Name</th><th>Duration</th><th>Status</th></tr></thead><tbody>" + occ_list + "</tbody></table></div></div>" if occ_list else '<div style="background:rgba(16,185,129,.05);border:1px solid rgba(16,185,129,.15);border-radius:14px;padding:20px;text-align:center;color:#6ee7b7;font-size:.9rem;">✅ No vehicles currently parked in Zone ' + zone_letter + '</div>'}
"""
    return page_shell(f"Zone {zone_letter}", "map", content, extra_head="""
<style>
.zone-slot { border-radius:10px;padding:10px 6px;text-align:center;cursor:default;transition:.2s cubic-bezier(.4,0,.2,1);border:1px solid; }
.zone-slot:hover { transform:scale(1.08);z-index:2;position:relative; }
.zone-slot.red { background:rgba(239,68,68,.15);border-color:rgba(239,68,68,.35);color:#fca5a5; }
.zone-slot.blue { background:rgba(59,130,246,.15);border-color:rgba(59,130,246,.35);color:#93c5fd; }
.zone-slot.green { background:rgba(16,185,129,.1);border-color:rgba(16,185,129,.25);color:#6ee7b7; }
.slot-id { font-weight:800;font-size:.82rem; }
.slot-info { font-size:.65rem;margin-top:2px;opacity:.7; }
</style>
""")

# ─── Reports Page ──────────────────────────────────────────────────────────────

@app.route("/report")
@admin_required
def report_page():
    return send_from_directory("views", "report.html")

@app.route("/api/report/<period>")
@admin_required
def api_report(period):
    if period not in ("daily", "weekly", "monthly"):
        return jsonify({"error": "Invalid period"}), 400
    return jsonify(get_report_data(period))

# ─── ANPR Page ─────────────────────────────────────────────────────────────────

@app.route("/anpr")
def anpr_page():
    return send_from_directory("views", "anpr.html")

@app.route("/api/anpr", methods=["POST"])
def api_anpr():
    """ANPR endpoint — accepts uploaded image, attempts plate detection."""
    import re
    plate = None
    method = "simulation"

    if "image" in request.files:
        img_file = request.files["image"]
        if img_file.filename:
            try:
                import pytesseract
                from PIL import Image, ImageEnhance, ImageFilter
                img = Image.open(img_file.stream).convert("L")
                img = img.filter(ImageFilter.SHARPEN)
                img = ImageEnhance.Contrast(img).enhance(2.5)
                text = pytesseract.image_to_string(img, config="--psm 8 --oem 3 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
                candidates = re.findall(r"[A-Z]{2}[0-9]{1,2}[A-Z]{1,3}[0-9]{4}", text.upper())
                if candidates:
                    plate = candidates[0]
                    method = "ocr"
            except Exception:
                pass

    # Fallback: generate a realistic demo plate
    if not plate:
        state_codes = ["KA", "MH", "DL", "TN", "GJ", "UP", "RJ", "WB", "AP", "KL"]
        state = random.choice(state_codes)
        district = f"{random.randint(1,99):02d}"
        series = "".join(random.choices("ABCDEFGHJKLMNPQRSTUVWXYZ", k=2))
        number = f"{random.randint(1000,9999)}"
        plate = f"{state}{district}{series}{number}"
        method = "simulation"

    # Get AI-suggested slot
    occupied, reserved = get_occupied_and_reserved()
    suggested_slot = get_smart_slot(occupied, reserved)
    suggested_label = slot_to_label(suggested_slot) if suggested_slot else None

    return jsonify({
        "detected_plate": plate,
        "method": method,
        "suggested_slot": suggested_label,
        "suggested_slot_int": suggested_slot,
        "confidence": "85%" if method == "ocr" else "Demo Mode",
        "message": f"Plate {plate} detected · Recommended slot: {suggested_label}",
    })

# ─── Demo Mode ─────────────────────────────────────────────────────────────────

DEMO_NAMES = ["Arjun Sharma", "Priya Patel", "Rahul Singh", "Neha Verma", "Amit Joshi", "Deepa Nair",
              "Ravi Kumar", "Sunita Rao", "Vijay Menon", "Anita Gupta", "Suresh Pillai", "Kavya Reddy"]
DEMO_VEHICLES = ["KA01AB1234","MH12CD5678","DL7CQ1234","TN01AB9999","GJ05XY4321",
                 "UP32KL8765","RJ14MN2468","WB01PQ7531","AP09RS3579","KL10TU6420"]

@app.route("/demo")
def demo_page():
    return send_from_directory("views", "demo.html")

@app.route("/api/simulate", methods=["POST"])
def api_simulate():
    data = request.get_json() or {}
    count = min(int(data.get("count", 10)), 30)
    hourly, daily = get_billing_rates()
    conn = get_db()

    created, skipped = 0, 0
    for _ in range(count):
        name = random.choice(DEMO_NAMES)
        phone = f"9{''.join(random.choices('0123456789', k=9))}"
        # Generate unique vehicle number
        state = random.choice(["KA","MH","DL","TN","GJ","UP"])
        vehicle = f"{state}{random.randint(1,99):02d}{''.join(random.choices('ABCDEFGHJKLMNP',k=2))}{random.randint(1000,9999)}"

        occupied = {r["slot"] for r in conn.execute("SELECT slot FROM parking WHERE exit_time IS NULL").fetchall()}
        reserved = {r["slot"] for r in conn.execute("SELECT slot FROM reservations").fetchall()}
        slot = get_smart_slot(occupied, reserved)
        if not slot:
            break

        entry_minutes_ago = random.randint(5, 240)
        entry_dt = datetime.now() - timedelta(minutes=entry_minutes_ago)
        entry_str = entry_dt.strftime("%Y-%m-%d %H:%M:%S")

        # 40% chance already exited
        if random.random() < 0.4:
            exit_dt = entry_dt + timedelta(minutes=random.randint(20, entry_minutes_ago))
            if exit_dt > datetime.now():
                exit_dt = datetime.now()
            exit_str = exit_dt.strftime("%Y-%m-%d %H:%M:%S")
            amount, _, hrs = calculate_bill(entry_str, exit_str, hourly, daily)
            try:
                conn.execute("INSERT INTO parking (name, phone, vehicle, slot, entry_time, exit_time, amount_charged, duration_minutes) VALUES (?,?,?,?,?,?,?,?)",
                             (name, phone, vehicle, slot, entry_str, exit_str, amount, round(hrs * 60)))
                conn.commit(); created += 1
            except sqlite3.IntegrityError: skipped += 1
        else:
            try:
                conn.execute("INSERT INTO parking (name, phone, vehicle, slot, entry_time) VALUES (?,?,?,?,?)",
                             (name, phone, vehicle, slot, entry_str))
                conn.commit(); created += 1
            except sqlite3.IntegrityError: skipped += 1

    conn.close()
    return jsonify({"created": created, "skipped": skipped, "message": f"Simulation complete — {created} vehicles added"})

# ─── Admin Settings ────────────────────────────────────────────────────────────

@app.route("/admin/settings", methods=["GET"])
@admin_required
def admin_settings():
    return send_from_directory("views", "admin_settings.html")

@app.route("/admin/settings", methods=["POST"])
@admin_required
def admin_settings_post():
    hourly = request.form.get("hourly_rate", "20")
    daily = request.form.get("daily_rate", "250")
    mode = request.form.get("allocation_mode", "ai")
    threshold = request.form.get("anomaly_threshold", "8")
    try:
        hourly = str(float(hourly))
        daily = str(float(daily))
    except Exception:
        hourly, daily = "20", "250"
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO settings VALUES ('hourly_rate', ?)", (hourly,))
    conn.execute("INSERT OR REPLACE INTO settings VALUES ('daily_rate', ?)", (daily,))
    conn.execute("INSERT OR REPLACE INTO settings VALUES ('allocation_mode', ?)", (mode,))
    conn.execute("INSERT OR REPLACE INTO settings VALUES ('anomaly_threshold', ?)", (threshold,))
    conn.commit(); conn.close()
    return redirect("/admin/settings?saved=1")

# ─── AI API Endpoints ──────────────────────────────────────────────────────────

@app.route("/api/predictions")
def api_predictions():
    return jsonify({"predictions": get_next_24h_predictions()})

@app.route("/api/insights")
def api_insights():
    peak = get_current_peak_status()
    today = get_today_stats()
    occupied, reserved = get_occupied_and_reserved()
    live_count = len(occupied)
    rev = get_revenue_stats()
    return jsonify({
        "peak": peak, "today": today,
        "busiest_hours": get_busiest_hours(),
        "live_count": live_count,
        "live_percent": round((live_count / TOTAL_SLOTS) * 100, 1),
        "available": TOTAL_SLOTS - live_count,
        "revenue": rev,
        "zones": get_zone_occupancy(),
    })

@app.route("/api/anomalies")
def api_anomalies():
    return jsonify({"anomalies": get_anomalies(), "count": len(get_anomalies())})

@app.route("/api/zone-data")
def api_zone_data():
    return jsonify(get_zone_occupancy())

@app.route("/api/revenue")
def api_revenue():
    rev = get_revenue_stats()
    hourly, daily = get_billing_rates()
    return jsonify({**rev, "hourly_rate": hourly, "daily_rate": daily})

@app.route("/api/export/csv")
@admin_required
def export_csv():
    conn = get_db()
    rows = conn.execute("SELECT * FROM parking ORDER BY id DESC").fetchall()
    conn.close()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Name", "Phone", "Vehicle", "Slot", "Zone Label", "Entry Time", "Exit Time", "Duration (min)", "Amount Charged"])
    for r in rows:
        lbl = slot_to_label(r["slot"]) if r["slot"] else ""
        writer.writerow([r["id"], r["name"], r["phone"], r["vehicle"], r["slot"], lbl, r["entry_time"], r["exit_time"] or "", r["duration_minutes"] or "", r["amount_charged"] or ""])
    resp = make_response(output.getvalue())
    resp.headers["Content-Type"] = "text/csv"
    resp.headers["Content-Disposition"] = f"attachment; filename=parking_export_{datetime.now().strftime('%Y%m%d')}.csv"
    return resp

@app.route("/api/clear-demo", methods=["POST"])
@admin_required
def clear_demo():
    conn = get_db()
    conn.execute("DELETE FROM parking")
    conn.commit(); conn.close()
    return jsonify({"message": "All parking records cleared"})

# ─── Entrypoint ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 Smart Parking AI — http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
