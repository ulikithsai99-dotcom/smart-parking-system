"""
AI Engine — Smart Parking Prediction & Slot Allocation
Analyzes historical entry patterns to predict peak hours
and intelligently distribute slot assignments.
"""

import sqlite3
import numpy as np
from datetime import datetime, timedelta
from collections import defaultdict

DB_PATH = "database.db"

# Default occupancy curve (fraction of capacity) for a typical parking lot
# Based on: morning rush (8-10am), lunch (12-2pm), evening rush (5-7pm)
DEFAULT_HOURLY_CURVE = {
    0: 0.05,  1: 0.03,  2: 0.02,  3: 0.02,  4: 0.03,  5: 0.07,
    6: 0.18,  7: 0.38,  8: 0.72,  9: 0.85, 10: 0.75, 11: 0.78,
    12: 0.88, 13: 0.92, 14: 0.82, 15: 0.80, 16: 0.88, 17: 0.96,
    18: 0.85, 19: 0.62, 20: 0.45, 21: 0.28, 22: 0.14, 23: 0.07,
}

# Zone definitions (slot ranges)
ZONES = {
    "A": (1, 50),
    "B": (51, 100),
    "C": (101, 150),
    "D": (151, 200),
}
TOTAL_SLOTS = 200

def _get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def _get_historical_hourly_pattern():
    """
    Query last 30 days of parking entries.
    Build average vehicles-present count per hour-of-day.
    Returns dict: hour (0-23) -> average fraction of capacity occupied.
    """
    try:
        conn = _get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT entry_time, exit_time
            FROM parking
            WHERE entry_time IS NOT NULL
              AND entry_time >= datetime('now', '-30 days')
        """)
        rows = cur.fetchall()
        conn.close()
    except Exception:
        return None

    if len(rows) < 10:
        return None  # Not enough data, use defaults

    # For each hour-of-day (0-23), count how many vehicles were present on average
    hourly_counts = defaultdict(list)

    for row in rows:
        try:
            entry = datetime.strptime(row["entry_time"], "%Y-%m-%d %H:%M:%S")
            exit_t = None
            if row["exit_time"]:
                exit_t = datetime.strptime(row["exit_time"], "%Y-%m-%d %H:%M:%S")
            else:
                exit_t = entry + timedelta(hours=3)  # Assume 3h average stay

            # Mark each hour this vehicle was occupying a slot
            current = entry.replace(minute=0, second=0, microsecond=0)
            end = exit_t.replace(minute=0, second=0, microsecond=0)
            while current <= end:
                hourly_counts[current.hour].append(1)
                current += timedelta(hours=1)
        except Exception:
            continue

    if not hourly_counts:
        return None

    # Normalize to fraction of TOTAL_SLOTS
    pattern = {}
    for h in range(24):
        count = sum(hourly_counts.get(h, [0])) / max(len(hourly_counts.get(h, [1])), 1)
        pattern[h] = min(count / TOTAL_SLOTS, 1.0)

    return pattern

def get_hourly_predictions():
    """
    Returns predicted occupancy rate (0.0-1.0) for each hour 0-23.
    Blends historical data (70%) with default curve (30%) when available.
    """
    historical = _get_historical_hourly_pattern()
    predictions = {}

    for h in range(24):
        default_val = DEFAULT_HOURLY_CURVE.get(h, 0.1)
        if historical:
            hist_val = historical.get(h, default_val)
            # Weighted blend: 70% historical, 30% default
            predictions[h] = round(0.7 * hist_val + 0.3 * default_val, 3)
        else:
            predictions[h] = default_val

    return predictions

def get_next_24h_predictions():
    """
    Returns a list of {hour_label, occupancy_rate, status} for the next 24 hours
    starting from the current hour.
    """
    hourly = get_hourly_predictions()
    now = datetime.now()
    results = []

    for offset in range(24):
        target = now + timedelta(hours=offset)
        h = target.hour
        rate = hourly[h]
        label = target.strftime("%I %p").lstrip("0")

        if rate >= 0.80:
            status = "peak"
            color = "#ef4444"
        elif rate >= 0.55:
            status = "moderate"
            color = "#f59e0b"
        else:
            status = "low"
            color = "#10b981"

        results.append({
            "hour": h,
            "offset": offset,
            "label": label,
            "rate": rate,
            "percent": round(rate * 100),
            "status": status,
            "color": color,
        })

    return results

def get_current_peak_status():
    """
    Returns current hour's predicted status and next peak window.
    """
    hourly = get_hourly_predictions()
    now = datetime.now()
    current_hour = now.hour
    current_rate = hourly[current_hour]

    # Determine current status
    if current_rate >= 0.80:
        status = "peak"
        status_label = "🔴 Peak Hour"
        status_color = "#ef4444"
    elif current_rate >= 0.55:
        status = "moderate"
        status_label = "🟡 Moderate"
        status_color = "#f59e0b"
    else:
        status = "low"
        status_label = "🟢 Off-Peak"
        status_color = "#10b981"

    # Find next peak window (next time rate crosses 0.80)
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

    # Allocation strategy
    if current_rate >= 0.80:
        strategy = "zone_rotation"
        strategy_label = "Zone Rotation — Distributing cars across all 4 zones to minimize congestion"
    elif current_rate >= 0.55:
        strategy = "balanced"
        strategy_label = "Balanced — Filling zones progressively with congestion monitoring"
    else:
        strategy = "sequential"
        strategy_label = "Sequential — Standard next-available slot assignment"

    return {
        "current_rate": current_rate,
        "current_percent": round(current_rate * 100),
        "status": status,
        "status_label": status_label,
        "status_color": status_color,
        "next_peak_label": next_peak_label,
        "strategy": strategy,
        "strategy_label": strategy_label,
        "hour": current_hour,
    }

def get_smart_slot(occupied_slots: set, reserved_slots: set) -> int | None:
    """
    AI-powered slot allocation.
    - Off-peak: Standard lowest-available (slot 1, 2, 3...)
    - Moderate: Fill Zone A first, but warn
    - Peak: Zone rotation interleaving — distributes load across all zones
            Pattern: 1, 51, 101, 151, 2, 52, 102, 152, 3, 53...
    Returns the slot number (1-200) or None if full.
    """
    hourly = get_hourly_predictions()
    current_rate = hourly[datetime.now().hour]

    taken = occupied_slots | reserved_slots

    if current_rate >= 0.80:
        # Peak: Zone rotation pattern
        # Interleave slots across zones A/B/C/D
        for i in range(1, 51):  # base index within zone (1-50)
            for zone_start in [0, 50, 100, 150]:  # A, B, C, D
                candidate = zone_start + i
                if candidate <= TOTAL_SLOTS and candidate not in taken:
                    return candidate
    else:
        # Off-peak / Moderate: sequential next-available
        for slot in range(1, TOTAL_SLOTS + 1):
            if slot not in taken:
                return slot

    return None  # Full

def get_today_stats():
    """
    Returns basic stats for today — vehicles entered, avg stay time.
    """
    try:
        conn = _get_db()
        cur = conn.cursor()
        today = datetime.now().strftime("%Y-%m-%d")

        cur.execute("""
            SELECT COUNT(*) as cnt FROM parking
            WHERE entry_time LIKE ?
        """, (f"{today}%",))
        today_entries = cur.fetchone()["cnt"]

        cur.execute("""
            SELECT AVG(
                (strftime('%s', exit_time) - strftime('%s', entry_time)) / 60.0
            ) as avg_min
            FROM parking
            WHERE exit_time IS NOT NULL
        """)
        row = cur.fetchone()
        avg_stay = round(row["avg_min"] or 0)
        conn.close()
        return {"today_entries": today_entries, "avg_stay_minutes": avg_stay}
    except Exception:
        return {"today_entries": 0, "avg_stay_minutes": 0}

def get_busiest_hours():
    """Returns top 3 historically busiest hours with counts."""
    hourly = get_hourly_predictions()
    sorted_hours = sorted(hourly.items(), key=lambda x: x[1], reverse=True)[:3]
    result = []
    for h, rate in sorted_hours:
        t = datetime.now().replace(hour=h, minute=0)
        result.append({
            "label": t.strftime("%I:00 %p").lstrip("0"),
            "percent": round(rate * 100),
        })
    return result
