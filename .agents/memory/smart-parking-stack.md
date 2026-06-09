---
name: Smart Parking Platform
description: Architecture decisions and quirks for the Smart Parking AI system
---

## Stack
- Python 3 + Flask 3.1.3, SQLite (database.db), bcrypt auth
- ai_engine.py: predictions, slot allocation, anomaly detection, billing, zone data
- app.py: all routes, DB init, PDF receipts (fpdf2), CSV export

## Slot Naming
- Internal: integer 1-200 in DB
- Display: A1-A50 (1-50), B1-B50 (51-100), C1-C50 (101-150), D1-D50 (151-200)
- Helpers: slot_to_label(n), label_to_slot(label) in ai_engine.py

## Billing
- Configurable hourly/daily rates stored in settings table (key-value)
- Formula: min(ceil_hours × hourly, ceil_days × daily), minimum 1 hour
- Default: ₹20/hr, ₹250/day
- PDF receipts via fpdf2

## Key Routes (all in app.py)
- /zone/<letter> → zone detail page (50-slot visual grid, dynamic)
- /anpr → ANPR scanner (pytesseract fallback to simulation — no tesseract binary)
- /demo → traffic simulator (POST /api/simulate creates random vehicles)
- /report → reports page (daily/weekly/monthly via /api/report/<period>)
- /admin/settings → billing config (POST saves to settings table)
- /api/anomalies → long-stay vehicle detection (>8h threshold)
- /api/zone-data → per-zone occupancy JSON
- /api/revenue → today/month revenue + billing rates
- /receipt/<vehicle> → PDF receipt download

## Important Quirks
- No tesseract binary available → ANPR always falls back to simulation (realistic Indian plate format)
- Admin auth uses bcrypt (old plaintext DB passwords won't work — re-register via /admin)
- Workflow: "Start application" → python app.py → port 5000
- 404 in logs = favicon.ico only (not an error)
- page_shell() helper generates sidebar-wrapped admin pages consistently

**Why:** Documenting because several quirks (no tesseract, bcrypt migration, zone label mapping) caused non-obvious failures during development.
