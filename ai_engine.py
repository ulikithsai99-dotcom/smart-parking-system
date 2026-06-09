"""
AI Engine — Smart Parking Prediction, Slot Allocation, Anomaly Detection & Revenue Analysis
"""

import sqlite3
import math
from datetime import datetime, timedelta
from collections import defaultdict

DB_PATH = "database.db"
TOTAL_SLOTS = 200

DEFAULT_HOURLY_CURVE = {
    0: 0.05,  1: 0.03,  2: 0.02,  3: 0.02,  4: 0.03,  5: 0.07,
    6: 0.18,  7: 0.38,  8: 0.72,  9: 0.85, 10: 0.75, 11: 0.78,
    12: 0.88, 13: 0.92, 14: 0.82, 15: 0.80, 16: 0.88, 17: 0.96,
    18: 0.85, 19: 0.62, 20: 0.45, 21: 0.28, 22: 0.14, 23: 0.07,
}

ZONES = {"A": (1, 50), "B": (51, 100), "C": (101, 150), "D": (151, 200)}

# ─── Helpers ──────────────────────────────────────────────────────────────────

def _get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def slot_to_label(n):
    if 1 <= n <= 50:   return f"A{n}"
    if 51 <= n <= 100:  return f"B{n - 50}"
    if 101 <= n <= 150: return f"C{n - 100}"
    if 151 <= n <= 200: return f"D{n - 150}"
    return str(n)

def label_to_slot(label):
    try:
        z = label[0].upper()
        num = int(label[1:])
        offsets = {"A": 0, "B": 50, "C": 100, "D": 150}
        return offsets[z] + num
    except Exception:
        return int(label) if label.isdigit() else None

# ─── Prediction ────────────────────────────────────────────────────────────────

def _get_historical_hourly_pattern():
    try:
        conn = _get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT entry_time, exit_time FROM parking
            WHERE entry_time IS NOT NULL AND entry_time >= datetime('now','-30 days')
        """)
        rows = cur.fetchall()
        conn.close()
    except Exception:
        return None

    if len(rows) < 10:
        return None

    hourly_counts = defaultdict(list)
    for row in rows:
        try:
            entry = datetime.strptime(row["entry_time"], "%Y-%m-%d %H:%M:%S")
            exit_t = datetime.strptime(row["exit_time"], "%Y-%m-%d %H:%M:%S") if row["exit_time"] else entry + timedelta(hours=3)
            cur_h = entry.replace(minute=0, second=0, microsecond=0)
            end_h = exit_t.replace(minute=0, second=0, microsecond=0)
            while cur_h <= end_h:
                hourly_counts[cur_h.hour].append(1)
                cur_h += timedelta(hours=1)
        except Exception:
            continue

    if not hourly_counts:
        return None

    pattern = {}
    for h in range(24):
        count = sum(hourly_counts.get(h, [0])) / max(len(hourly_counts.get(h, [1])), 1)
        pattern[h] = min(count / TOTAL_SLOTS, 1.0)
    return pattern

def get_hourly_predictions():
    historical = _get_historical_hourly_pattern()
    predictions = {}
    for h in range(24):
        default_val = DEFAULT_HOURLY_CURVE.get(h, 0.1)
        if historical:
            hist_val = historical.get(h, default_val)
            predictions[h] = round(0.7 * hist_val + 0.3 * default_val, 3)
        else:
            predictions[h] = default_val
    return predictions

def get_next_24h_predictions():
    hourly = get_hourly_predictions()
    now = datetime.now()
    results = []
    for offset in range(24):
        target = now + timedelta(hours=offset)
        h = target.hour
        rate = hourly[h]
        label = target.strftime("%I %p").lstrip("0")
        if rate >= 0.80:
            status, color = "peak", "#ef4444"
        elif rate >= 0.55:
            status, color = "moderate", "#f59e0b"
        else:
            status, color = "low", "#10b981"
        results.append({"hour": h, "offset": offset, "label": label, "rate": rate, "percent": round(rate * 100), "status": status, "color": color})
    return results

def get_current_peak_status():
    hourly = get_hourly_predictions()
    now = datetime.now()
    current_hour = now.hour
    current_rate = hourly[current_hour]

    if current_rate >= 0.80:
        status, status_label, status_color = "peak", "🔴 Peak Hour", "#ef4444"
    elif current_rate >= 0.55:
        status, status_label, status_color = "moderate", "🟡 Moderate", "#f59e0b"
    else:
        status, status_label, status_color = "low", "🟢 Off-Peak", "#10b981"

    next_peak_hour = None
    for offset in range(1, 24):
        h = (current_hour + offset) % 24
        if hourly[h] >= 0.80:
            next_peak_hour = h
            break

    next_peak_label = None
    if next_peak_hour is not None:
        t = datetime.now().replace(hour=next_peak_hour, minute=0)
        next_peak_label = t.strftime("%I:00 %p").lstrip("0")

    if current_rate >= 0.80:
        strategy, strategy_label = "zone_rotation", "Zone Rotation — Distributing across A→B→C→D to minimize congestion"
    elif current_rate >= 0.55:
        strategy, strategy_label = "balanced", "Balanced — Progressive zone filling with congestion monitoring"
    else:
        strategy, strategy_label = "sequential", "Sequential — Standard next-available slot assignment"

    return {
        "current_rate": current_rate, "current_percent": round(current_rate * 100),
        "status": status, "status_label": status_label, "status_color": status_color,
        "next_peak_label": next_peak_label, "strategy": strategy, "strategy_label": strategy_label,
        "hour": current_hour,
    }

# ─── Slot Allocation ───────────────────────────────────────────────────────────

def get_smart_slot(occupied_slots: set, reserved_slots: set) -> int | None:
    hourly = get_hourly_predictions()
    current_rate = hourly[datetime.now().hour]
    taken = occupied_slots | reserved_slots

    if current_rate >= 0.80:
        for i in range(1, 51):
            for zone_start in [0, 50, 100, 150]:
                candidate = zone_start + i
                if candidate <= TOTAL_SLOTS and candidate not in taken:
                    return candidate
    else:
        for slot in range(1, TOTAL_SLOTS + 1):
            if slot not in taken:
                return slot
    return None

# ─── Zone Data ─────────────────────────────────────────────────────────────────

def get_zone_occupancy():
    """Returns per-zone occupancy stats."""
    try:
        conn = _get_db()
        cur = conn.cursor()
        cur.execute("SELECT slot FROM parking WHERE exit_time IS NULL")
        occupied = {r["slot"] for r in cur.fetchall()}
        cur.execute("SELECT slot FROM reservations")
        reserved = {r["slot"] for r in cur.fetchall()}
        conn.close()
    except Exception:
        occupied, reserved = set(), set()

    zones = {}
    for zone_letter, (start, end) in ZONES.items():
        zone_occ = sum(1 for s in range(start, end + 1) if s in occupied)
        zone_res = sum(1 for s in range(start, end + 1) if s in reserved)
        zones[zone_letter] = {
            "occupied": zone_occ,
            "reserved": zone_res,
            "available": 50 - zone_occ - zone_res,
            "total": 50,
            "percent": round((zone_occ / 50) * 100),
        }
    return zones

# ─── Anomaly Detection ─────────────────────────────────────────────────────────

ANOMALY_THRESHOLD_HOURS = 8  # Flag vehicles parked > 8 hours

def get_anomalies():
    """Detect vehicles parked unusually long and other anomalies."""
    anomalies = []
    try:
        conn = _get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT id, name, vehicle, slot, entry_time
            FROM parking
            WHERE exit_time IS NULL AND entry_time IS NOT NULL
        """)
        rows = cur.fetchall()
        conn.close()

        now = datetime.now()
        for row in rows:
            try:
                entry = datetime.strptime(row["entry_time"], "%Y-%m-%d %H:%M:%S")
                hours = (now - entry).total_seconds() / 3600
                if hours >= ANOMALY_THRESHOLD_HOURS:
                    zone_label = slot_to_label(row["slot"])
                    anomalies.append({
                        "id": row["id"],
                        "vehicle": row["vehicle"],
                        "name": row["name"],
                        "slot_label": zone_label,
                        "entry_time": row["entry_time"],
                        "hours_parked": round(hours, 1),
                        "type": "long_stay",
                        "severity": "critical" if hours >= 24 else "warning",
                        "message": f"Parked for {round(hours, 1)}h — exceeds {ANOMALY_THRESHOLD_HOURS}h threshold",
                    })
            except Exception:
                continue
    except Exception:
        pass

    # Zone overload anomalies
    zones = get_zone_occupancy()
    for zone_letter, data in zones.items():
        if data["percent"] >= 95:
            anomalies.append({
                "type": "zone_overload",
                "severity": "critical",
                "zone": zone_letter,
                "message": f"Zone {zone_letter} is {data['percent']}% full — critical overload",
            })
        elif data["percent"] >= 85:
            anomalies.append({
                "type": "zone_high",
                "severity": "warning",
                "zone": zone_letter,
                "message": f"Zone {zone_letter} is {data['percent']}% full — approaching capacity",
            })

    return anomalies

# ─── Revenue & Billing ─────────────────────────────────────────────────────────

def calculate_bill(entry_time_str, exit_time_str, hourly_rate=20, daily_rate=250):
    """Returns (amount: float, duration_str: str, hours: float)."""
    try:
        entry = datetime.strptime(entry_time_str, "%Y-%m-%d %H:%M:%S")
        exit_t = datetime.strptime(exit_time_str, "%Y-%m-%d %H:%M:%S")
        diff_seconds = max((exit_t - entry).total_seconds(), 60)
        hours = diff_seconds / 3600

        full_days = int(hours // 24)
        remaining_hours = hours % 24
        remaining_hours_ceil = math.ceil(remaining_hours) if remaining_hours > 0.1 else 0

        hourly_amount = full_days * daily_rate + remaining_hours_ceil * hourly_rate
        daily_amount = math.ceil(hours / 24) * daily_rate
        amount = min(hourly_amount, daily_amount)
        amount = max(amount, hourly_rate)  # Minimum 1 hour

        h = int(hours)
        m = int((hours - h) * 60)
        duration_str = f"{h}h {m}m" if h > 0 else f"{m}m"
        return round(amount, 2), duration_str, round(hours, 2)
    except Exception:
        return 0.0, "0m", 0.0

def get_revenue_stats(hourly_rate=20, daily_rate=250):
    """Returns today's and monthly revenue totals."""
    try:
        conn = _get_db()
        cur = conn.cursor()
        today = datetime.now().strftime("%Y-%m-%d")
        month_start = datetime.now().strftime("%Y-%m-01")

        # Try to use stored amount_charged first
        cur.execute("""
            SELECT COALESCE(SUM(amount_charged), 0) as rev
            FROM parking
            WHERE exit_time LIKE ? AND amount_charged IS NOT NULL
        """, (f"{today}%",))
        row = cur.fetchone()
        today_rev = row["rev"] or 0

        cur.execute("""
            SELECT COALESCE(SUM(amount_charged), 0) as rev
            FROM parking
            WHERE exit_time >= ? AND amount_charged IS NOT NULL
        """, (month_start,))
        row = cur.fetchone()
        month_rev = row["rev"] or 0

        cur.execute("SELECT COUNT(*) as c FROM parking WHERE exit_time LIKE ? AND amount_charged IS NOT NULL", (f"{today}%",))
        today_txns = cur.fetchone()["c"]

        conn.close()
        return {
            "today": round(today_rev, 2),
            "month": round(month_rev, 2),
            "today_transactions": today_txns,
        }
    except Exception:
        return {"today": 0, "month": 0, "today_transactions": 0}

def get_today_stats():
    try:
        conn = _get_db()
        cur = conn.cursor()
        today = datetime.now().strftime("%Y-%m-%d")
        cur.execute("SELECT COUNT(*) as cnt FROM parking WHERE entry_time LIKE ?", (f"{today}%",))
        today_entries = cur.fetchone()["cnt"]
        cur.execute("""
            SELECT AVG((strftime('%s', exit_time) - strftime('%s', entry_time)) / 60.0) as avg_min
            FROM parking WHERE exit_time IS NOT NULL
        """)
        row = cur.fetchone()
        avg_stay = round(row["avg_min"] or 0)
        conn.close()
        return {"today_entries": today_entries, "avg_stay_minutes": avg_stay}
    except Exception:
        return {"today_entries": 0, "avg_stay_minutes": 0}

def get_busiest_hours():
    hourly = get_hourly_predictions()
    sorted_hours = sorted(hourly.items(), key=lambda x: x[1], reverse=True)[:3]
    result = []
    for h, rate in sorted_hours:
        t = datetime.now().replace(hour=h, minute=0)
        result.append({"label": t.strftime("%I:00 %p").lstrip("0"), "percent": round(rate * 100)})
    return result

def get_report_data(period="daily"):
    """Generate report data for a given period."""
    now = datetime.now()
    if period == "daily":
        start_dt = now.replace(hour=0, minute=0, second=0)
        label = now.strftime("%B %d, %Y")
    elif period == "weekly":
        start_dt = now - timedelta(days=7)
        label = f"{start_dt.strftime('%b %d')} – {now.strftime('%b %d, %Y')}"
    else:
        start_dt = now.replace(day=1, hour=0, minute=0, second=0)
        label = now.strftime("%B %Y")

    start_str = start_dt.strftime("%Y-%m-%d %H:%M:%S")
    try:
        conn = _get_db()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) as c FROM parking WHERE entry_time >= ?", (start_str,))
        total_vehicles = cur.fetchone()["c"]
        cur.execute("""
            SELECT COALESCE(SUM(amount_charged), 0) as rev
            FROM parking WHERE exit_time >= ? AND amount_charged IS NOT NULL
        """, (start_str,))
        revenue = cur.fetchone()["rev"] or 0
        cur.execute("""
            SELECT AVG((strftime('%s', exit_time) - strftime('%s', entry_time)) / 60.0) as avg_min
            FROM parking WHERE exit_time IS NOT NULL AND entry_time >= ?
        """, (start_str,))
        avg_stay = round(cur.fetchone()["avg_min"] or 0)
        cur.execute("""
            SELECT slot, COUNT(*) as c FROM parking
            WHERE entry_time >= ? GROUP BY slot ORDER BY c DESC LIMIT 5
        """, (start_str,))
        top_slots = [{"slot": slot_to_label(r["slot"]), "count": r["c"]} for r in cur.fetchall()]

        # Daily breakdown for weekly/monthly
        daily_data = []
        if period in ("weekly", "monthly"):
            days = 7 if period == "weekly" else 30
            for d in range(days):
                day = (now - timedelta(days=days-1-d)).strftime("%Y-%m-%d")
                cur.execute("SELECT COUNT(*) as c FROM parking WHERE entry_time LIKE ?", (f"{day}%",))
                cnt = cur.fetchone()["c"]
                cur.execute("SELECT COALESCE(SUM(amount_charged),0) as r FROM parking WHERE exit_time LIKE ? AND amount_charged IS NOT NULL", (f"{day}%",))
                rev = cur.fetchone()["r"] or 0
                daily_data.append({"date": day, "vehicles": cnt, "revenue": round(rev, 2)})

        conn.close()
        return {
            "period": period, "label": label,
            "total_vehicles": total_vehicles,
            "revenue": round(revenue, 2),
            "avg_stay_minutes": avg_stay,
            "top_slots": top_slots,
            "daily_data": daily_data,
            "zone_occupancy": get_zone_occupancy(),
        }
    except Exception as e:
        return {"period": period, "label": label, "total_vehicles": 0, "revenue": 0, "avg_stay_minutes": 0, "top_slots": [], "daily_data": [], "zone_occupancy": {}}
