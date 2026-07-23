const express = require("express");
const sqlite3 = require("sqlite3").verbose();
const bodyParser = require("body-parser");
const path = require("path");
const session = require("express-session");
const fs = require("fs");
const math = { ceil: Math.ceil, floor: Math.floor };
const bcrypt = require("bcryptjs");


const app = express();
const PORT = process.env.PORT || 5000;

app.use(bodyParser.urlencoded({ extended: true }));
app.use(bodyParser.json());
app.use(
  session({
    secret: process.env.SESSION_SECRET || "smartparking_secret_2024",
    resave: false,
    saveUninitialized: true,
    cookie: { maxAge: 8 * 60 * 60 * 1000 }
  })
);
app.use(express.static(path.join(__dirname, "views")));

const db = new sqlite3.Database("database.db");

// ═══════════════════════════════════════════════
// DATABASE SETUP & MIGRATIONS
// ═══════════════════════════════════════════════

const dbRun = (sql, params = []) =>
  new Promise((res, rej) =>
    db.run(sql, params, (err) => (err ? rej(err) : res()))
  );
const dbGet = (sql, params = []) =>
  new Promise((res, rej) =>
    db.get(sql, params, (err, row) => (err ? rej(err) : res(row)))
  );
const dbAll = (sql, params = []) =>
  new Promise((res, rej) =>
    db.all(sql, params, (err, rows) => (err ? rej(err) : res(rows)))
  );

async function initDB() {
  await dbRun(`CREATE TABLE IF NOT EXISTS parking (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    phone TEXT,
    vehicle TEXT,
    slot INTEGER,
    zone TEXT,
    slot_label TEXT,
    entry_time TEXT,
    exit_time TEXT,
    amount_charged REAL
  )`);
  await dbRun(`CREATE TABLE IF NOT EXISTS reservations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id TEXT UNIQUE,
    name TEXT,
    vehicle TEXT,
    slot INTEGER,
    zone TEXT,
    slot_label TEXT,
    reservation_date TEXT
  )`);
  await dbRun(`CREATE TABLE IF NOT EXISTS admin (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE,
    password TEXT
  )`);
  await dbRun(`CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
  )`);

  // Migration: remove UNIQUE constraint on phone if it exists (legacy schema)
  try {
    const idxList = await dbAll(`PRAGMA index_list(parking)`);
    for (const idx of idxList) {
      if (!idx.unique) continue;
      const idxCols = await dbAll(`PRAGMA index_info(${idx.name})`);
      if (idxCols.some(c => c.name === 'phone')) {
        // Recreate the table without the bad constraint
        await dbRun(`CREATE TABLE IF NOT EXISTS parking_fix (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL,
          phone TEXT,
          vehicle TEXT,
          slot INTEGER,
          zone TEXT,
          slot_label TEXT,
          entry_time TEXT,
          exit_time TEXT,
          amount_charged REAL
        )`);
        await dbRun(`INSERT OR IGNORE INTO parking_fix SELECT id, name, phone, vehicle, slot, zone, slot_label, entry_time, exit_time, amount_charged FROM parking`);
        await dbRun(`DROP TABLE parking`);
        await dbRun(`ALTER TABLE parking_fix RENAME TO parking`);
        console.log("✅ Removed UNIQUE constraint from parking.phone");
        break;
      }
    }
  } catch (_) {}

  // Migrations — add missing columns if needed
  const cols = await dbAll(`PRAGMA table_info(parking)`);
  const colNames = cols.map(c => c.name);
  if (!colNames.includes("amount_charged"))
    await dbRun(`ALTER TABLE parking ADD COLUMN amount_charged REAL`);
  if (!colNames.includes("zone"))
    await dbRun(`ALTER TABLE parking ADD COLUMN zone TEXT`);
  if (!colNames.includes("slot_label"))
    await dbRun(`ALTER TABLE parking ADD COLUMN slot_label TEXT`);

  const resCols = await dbAll(`PRAGMA table_info(reservations)`);
  const resColNames = resCols.map(c => c.name);
  if (!resColNames.includes("zone"))
    await dbRun(`ALTER TABLE reservations ADD COLUMN zone TEXT`);
  if (!resColNames.includes("slot_label"))
    await dbRun(`ALTER TABLE reservations ADD COLUMN slot_label TEXT`);

  // Default settings
  const defaults = [
    ["hourly_rate", "20"],
    ["daily_rate", "250"],
    ["anomaly_threshold", "8"],
    ["allocation_mode", "ai"],
  ];
  for (const [k, v] of defaults) {
    await dbRun(
      `INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)`,
      [k, v]
    );
  }

  console.log("✅ Database Ready");
}

initDB().catch(console.error);

// ═══════════════════════════════════════════════
// AI ENGINE (Ported from ai_engine.py)
// ═══════════════════════════════════════════════

const TOTAL_SLOTS = 200;
const ZONES = { A: [1, 50], B: [51, 100], C: [101, 150], D: [151, 200] };

const DEFAULT_HOURLY_CURVE = {
  0: 0.05, 1: 0.03, 2: 0.02, 3: 0.02, 4: 0.03, 5: 0.07,
  6: 0.18, 7: 0.38, 8: 0.72, 9: 0.85, 10: 0.75, 11: 0.78,
  12: 0.88, 13: 0.92, 14: 0.82, 15: 0.80, 16: 0.88, 17: 0.96,
  18: 0.85, 19: 0.62, 20: 0.45, 21: 0.28, 22: 0.14, 23: 0.07,
};

function slotToLabel(n) {
  if (n >= 1 && n <= 50)   return `A${n}`;
  if (n >= 51 && n <= 100)  return `B${n - 50}`;
  if (n >= 101 && n <= 150) return `C${n - 100}`;
  if (n >= 151 && n <= 200) return `D${n - 150}`;
  return String(n);
}

function slotToZone(n) {
  if (n >= 1 && n <= 50)   return "A";
  if (n >= 51 && n <= 100)  return "B";
  if (n >= 101 && n <= 150) return "C";
  if (n >= 151 && n <= 200) return "D";
  return "A";
}

function escapeHtml(str) {
  if (str === null || str === undefined) return "";

  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}


async function getSettings() {
  const rows = await dbAll(`SELECT key, value FROM settings`);
  const s = {};
  for (const r of rows) s[r.key] = r.value;
  return {
    hourly_rate: parseFloat(s.hourly_rate || "20"),
    daily_rate: parseFloat(s.daily_rate || "250"),
    anomaly_threshold: parseInt(s.anomaly_threshold || "8"),
    allocation_mode: s.allocation_mode || "ai",
  };
}

async function getHistoricalHourlyPattern() {
  try {
    const rows = await dbAll(`
      SELECT entry_time, exit_time FROM parking
      WHERE entry_time IS NOT NULL AND entry_time >= datetime('now','-30 days')
    `);
    if (rows.length < 10) return null;

    const hourlyCounts = {};
    for (let h = 0; h < 24; h++) hourlyCounts[h] = [];

    for (const row of rows) {
      try {
        const entry = new Date(row.entry_time.replace(" ", "T"));
        const exitT = row.exit_time
          ? new Date(row.exit_time.replace(" ", "T"))
          : new Date(entry.getTime() + 3 * 3600000);
        let curH = new Date(entry);
        curH.setMinutes(0, 0, 0);
        const endH = new Date(exitT);
        endH.setMinutes(0, 0, 0);
        while (curH <= endH) {
          hourlyCounts[curH.getHours()].push(1);
          curH = new Date(curH.getTime() + 3600000);
        }
      } catch (_) {}
    }

    const pattern = {};
    for (let h = 0; h < 24; h++) {
      const arr = hourlyCounts[h] || [0];
      const count = arr.reduce((a, b) => a + b, 0) / Math.max(arr.length, 1);
      pattern[h] = Math.min(count / TOTAL_SLOTS, 1.0);
    }
    return pattern;
  } catch (_) {
    return null;
  }
}

async function getHourlyPredictions() {
  const historical = await getHistoricalHourlyPattern();
  const predictions = {};
  for (let h = 0; h < 24; h++) {
    const defaultVal = DEFAULT_HOURLY_CURVE[h] || 0.1;
    if (historical) {
      const histVal = historical[h] ?? defaultVal;
      predictions[h] = Math.round((0.7 * histVal + 0.3 * defaultVal) * 1000) / 1000;
    } else {
      predictions[h] = defaultVal;
    }
  }
  return predictions;
}

async function getNext24hPredictions() {
  const hourly = await getHourlyPredictions();
  const now = new Date();
  const results = [];
  for (let offset = 0; offset < 24; offset++) {
    const target = new Date(now.getTime() + offset * 3600000);
    const h = target.getHours();
    const rate = hourly[h];
    const label = target.toLocaleTimeString("en-IN", { hour: "2-digit", hour12: true }).replace(/^0/, "");
    let status, color;
    if (rate >= 0.80) { status = "peak"; color = "#ef4444"; }
    else if (rate >= 0.55) { status = "moderate"; color = "#f59e0b"; }
    else { status = "low"; color = "#10b981"; }
    results.push({ hour: h, offset, label, rate, percent: Math.round(rate * 100), status, color });
  }
  return results;
}

async function getCurrentPeakStatus() {
  const hourly = await getHourlyPredictions();
  const settings = await getSettings();
  const now = new Date();
  const currentHour = now.getHours();
  const currentRate = hourly[currentHour];

  let status, statusLabel, statusColor, strategy;
  if (currentRate >= 0.80) {
    status = "peak"; statusLabel = "🔴 Peak Hour"; statusColor = "#ef4444";
    strategy = settings.allocation_mode === "ai" ? "zone_rotation" : settings.allocation_mode;
  } else if (currentRate >= 0.55) {
    status = "moderate"; statusLabel = "🟡 Moderate"; statusColor = "#f59e0b";
    strategy = settings.allocation_mode === "ai" ? "balanced" : settings.allocation_mode;
  } else {
    status = "low"; statusLabel = "🟢 Off-Peak"; statusColor = "#10b981";
    strategy = settings.allocation_mode === "ai" ? "sequential" : settings.allocation_mode;
  }

  let nextPeakHour = null;
  for (let offset = 1; offset < 24; offset++) {
    const h = (currentHour + offset) % 24;
    if (hourly[h] >= 0.80) { nextPeakHour = h; break; }
  }

  let nextPeakLabel = null;
  if (nextPeakHour !== null) {
    const t = new Date();
    t.setHours(nextPeakHour, 0, 0, 0);
    nextPeakLabel = t.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: true });
  }

  return {
    current_rate: currentRate,
    current_percent: Math.round(currentRate * 100),
    status, status_label: statusLabel, status_color: statusColor,
    next_peak_label: nextPeakLabel,
    strategy,
    hour: currentHour,
  };
}

async function getSmartSlot(occupiedSlots, reservedSlots) {
  const hourly = await getHourlyPredictions();
  const settings = await getSettings();
  const currentRate = hourly[new Date().getHours()];
  const taken = new Set([...occupiedSlots, ...reservedSlots]);
  const mode = settings.allocation_mode;

  if (mode === "ai") {
    if (currentRate >= 0.80) {
      // Zone rotation: pick one from each zone in round-robin
      for (let i = 1; i <= 50; i++) {
        for (const zoneStart of [0, 50, 100, 150]) {
          const candidate = zoneStart + i;
          if (candidate <= TOTAL_SLOTS && !taken.has(candidate)) return candidate;
        }
      }
    } else if (currentRate >= 0.55) {
      // Balanced: find least-occupied zone
      const zoneCounts = { A: 0, B: 0, C: 0, D: 0 };
      for (const s of occupiedSlots) {
        const z = slotToZone(s);
        if (zoneCounts[z] !== undefined) zoneCounts[z]++;
      }
      const sortedZones = Object.entries(zoneCounts).sort((a, b) => a[1] - b[1]);
      for (const [zone] of sortedZones) {
        const [start, end] = ZONES[zone];
        for (let s = start; s <= end; s++) {
          if (!taken.has(s)) return s;
        }
      }
    } else {
      // Sequential
      for (let s = 1; s <= TOTAL_SLOTS; s++) {
        if (!taken.has(s)) return s;
      }
    }
  } else if (mode === "balanced") {
    const zoneCounts = { A: 0, B: 0, C: 0, D: 0 };
    for (const s of occupiedSlots) {
      const z = slotToZone(s);
      if (zoneCounts[z] !== undefined) zoneCounts[z]++;
    }
    const sortedZones = Object.entries(zoneCounts).sort((a, b) => a[1] - b[1]);
    for (const [zone] of sortedZones) {
      const [start, end] = ZONES[zone];
      for (let s = start; s <= end; s++) {
        if (!taken.has(s)) return s;
      }
    }
  } else {
    // Sequential (default)
    for (let s = 1; s <= TOTAL_SLOTS; s++) {
      if (!taken.has(s)) return s;
    }
  }
  return null;
}

async function getZoneOccupancy() {
  const occupied = new Set(
    (await dbAll(`SELECT slot FROM parking WHERE exit_time IS NULL`)).map(r => r.slot)
  );
  const reserved = new Set(
    (await dbAll(`SELECT slot FROM reservations`)).map(r => r.slot)
  );
  const zones = {};
  for (const [zone, [start, end]] of Object.entries(ZONES)) {
    let zoneOcc = 0, zoneRes = 0;
    for (let s = start; s <= end; s++) {
      if (occupied.has(s)) zoneOcc++;
      else if (reserved.has(s)) zoneRes++;
    }
    zones[zone] = {
      occupied: zoneOcc,
      reserved: zoneRes,
      available: 50 - zoneOcc - zoneRes,
      total: 50,
      percent: Math.round((zoneOcc / 50) * 100),
    };
  }
  return zones;
}

async function getAnomalies() {
  const settings = await getSettings();
  const threshold = settings.anomaly_threshold;
  const anomalies = [];

  const rows = await dbAll(`
    SELECT id, name, vehicle, slot, slot_label, entry_time
    FROM parking WHERE exit_time IS NULL AND entry_time IS NOT NULL
  `);
  const now = new Date();
  for (const row of rows) {
    const entry = new Date(row.entry_time.replace(" ", "T"));
    const hours = (now - entry) / 3600000;
    if (hours >= threshold) {
      const label = row.slot_label || slotToLabel(row.slot);
      anomalies.push({
        id: row.id,
        vehicle: row.vehicle,
        name: row.name,
        slot_label: label,
        entry_time: row.entry_time,
        hours_parked: Math.round(hours * 10) / 10,
        type: "long_stay",
        severity: hours >= 24 ? "critical" : "warning",
        message: `${escapeHtml(row.vehicle)} parked ${Math.round(hours * 10) / 10}h — exceeds ${threshold}h threshold (Slot ${label})`,
      });
    }
  }

  const zones = await getZoneOccupancy();
  for (const [zone, data] of Object.entries(zones)) {
    if (data.percent >= 95) {
      anomalies.push({
        type: "zone_overload", severity: "critical", zone,
        message: `Zone ${zone} is ${data.percent}% full — critical overload`,
      });
    } else if (data.percent >= 85) {
      anomalies.push({
        type: "zone_high", severity: "warning", zone,
        message: `Zone ${zone} is ${data.percent}% full — approaching capacity`,
      });
    }
  }
  return anomalies;
}

function calculateBill(entryTimeStr, exitTimeStr, hourlyRate = 20, dailyRate = 250) {
  try {
    const entry = new Date(entryTimeStr.replace(" ", "T"));
    const exitT = new Date(exitTimeStr.replace(" ", "T"));
    const diffMs = Math.max(exitT - entry, 60000);
    const hours = diffMs / 3600000;

    const fullDays = Math.floor(hours / 24);
    const remainingHours = hours % 24;
    const remainingHoursCeil = remainingHours > 0.1 ? Math.ceil(remainingHours) : 0;

    const hourlyAmount = fullDays * dailyRate + remainingHoursCeil * hourlyRate;
    const dailyAmount = Math.ceil(hours / 24) * dailyRate;
    let amount = Math.min(hourlyAmount, dailyAmount);
    amount = Math.max(amount, hourlyRate);

    const h = Math.floor(hours);
    const m = Math.floor((hours - h) * 60);
    const durationStr = h > 0 ? `${h}h ${m}m` : `${m}m`;
    return { amount: Math.round(amount * 100) / 100, duration: durationStr, hours };
  } catch (_) {
    return { amount: 0, duration: "0m", hours: 0 };
  }
}

async function getRevenueStats() {
  const settings = await getSettings();
  const today = new Date().toISOString().slice(0, 10);
  const monthStart = today.slice(0, 7) + "-01";

  const todayRow = await dbGet(`
    SELECT COALESCE(SUM(amount_charged), 0) as rev FROM parking
    WHERE exit_time LIKE ? AND amount_charged IS NOT NULL
  `, [today + "%"]);
  const monthRow = await dbGet(`
    SELECT COALESCE(SUM(amount_charged), 0) as rev FROM parking
    WHERE exit_time >= ? AND amount_charged IS NOT NULL
  `, [monthStart]);
  const txnRow = await dbGet(`
    SELECT COUNT(*) as c FROM parking WHERE exit_time LIKE ? AND amount_charged IS NOT NULL
  `, [today + "%"]);

  return {
    today: Math.round((todayRow?.rev || 0) * 100) / 100,
    month: Math.round((monthRow?.rev || 0) * 100) / 100,
    today_transactions: txnRow?.c || 0,
    hourly_rate: settings.hourly_rate,
    daily_rate: settings.daily_rate,
  };
}

async function getTodayStats() {
  const today = new Date().toISOString().slice(0, 10);
  const row1 = await dbGet(`SELECT COUNT(*) as cnt FROM parking WHERE entry_time LIKE ?`, [today + "%"]);
  const row2 = await dbGet(`
    SELECT AVG((strftime('%s', exit_time) - strftime('%s', entry_time)) / 60.0) as avg_min
    FROM parking WHERE exit_time IS NOT NULL
  `);
  return {
    today_entries: row1?.cnt || 0,
    avg_stay_minutes: Math.round(row2?.avg_min || 0),
  };
}

async function getReportData(period = "daily") {
  const now = new Date();
  let startDt, label;
  if (period === "daily") {
    startDt = new Date(now); startDt.setHours(0, 0, 0, 0);
    label = now.toLocaleDateString("en-IN", { day: "numeric", month: "long", year: "numeric" });
  } else if (period === "weekly") {
    startDt = new Date(now.getTime() - 7 * 86400000);
    label = `${startDt.toLocaleDateString("en-IN", { month: "short", day: "numeric" })} – ${now.toLocaleDateString("en-IN", { month: "short", day: "numeric", year: "numeric" })}`;
  } else {
    startDt = new Date(now.getFullYear(), now.getMonth(), 1);
    label = now.toLocaleDateString("en-IN", { month: "long", year: "numeric" });
  }

  const startStr = startDt.toISOString().replace("T", " ").slice(0, 19);
  const total = await dbGet(`SELECT COUNT(*) as c FROM parking WHERE entry_time >= ?`, [startStr]);
  const revRow = await dbGet(`SELECT COALESCE(SUM(amount_charged), 0) as rev FROM parking WHERE exit_time >= ? AND amount_charged IS NOT NULL`, [startStr]);
  const avgRow = await dbGet(`SELECT AVG((strftime('%s',exit_time)-strftime('%s',entry_time))/60.0) as avg_min FROM parking WHERE exit_time IS NOT NULL AND entry_time >= ?`, [startStr]);
  const topSlotsRows = await dbAll(`SELECT slot, slot_label, COUNT(*) as c FROM parking WHERE entry_time >= ? GROUP BY slot ORDER BY c DESC LIMIT 5`, [startStr]);

  const topSlots = topSlotsRows.map(r => ({
    slot: r.slot_label || slotToLabel(r.slot),
    count: r.c,
  }));

  let dailyData = [];
  if (period === "weekly" || period === "monthly") {
    const days = period === "weekly" ? 7 : 30;
    for (let d = days - 1; d >= 0; d--) {
      const day = new Date(now.getTime() - d * 86400000).toISOString().slice(0, 10);
      const cntRow = await dbGet(`SELECT COUNT(*) as c FROM parking WHERE entry_time LIKE ?`, [day + "%"]);
      const revD = await dbGet(`SELECT COALESCE(SUM(amount_charged),0) as r FROM parking WHERE exit_time LIKE ? AND amount_charged IS NOT NULL`, [day + "%"]);
      dailyData.push({ date: day, vehicles: cntRow?.c || 0, revenue: Math.round((revD?.r || 0) * 100) / 100 });
    }
  }

  const zoneOcc = await getZoneOccupancy();
  const zoneOccForPeriod = {};
  for (const [z] of Object.entries(ZONES)) {
    const cnt = await dbGet(`SELECT COUNT(*) as c FROM parking WHERE entry_time >= ? AND slot >= ? AND slot <= ?`,
      [startStr, ZONES[z][0], ZONES[z][1]]);
    const total50 = 50;
    const pct = Math.round(((cnt?.c || 0) / Math.max(total50, 1)) * 100);
    zoneOccForPeriod[z] = { count: cnt?.c || 0, percent: Math.min(pct, 100) };
  }

  return {
    period, label,
    total_vehicles: total?.c || 0,
    revenue: Math.round((revRow?.rev || 0) * 100) / 100,
    avg_stay_minutes: Math.round(avgRow?.avg_min || 0),
    top_slots: topSlots,
    daily_data: dailyData,
    zone_occupancy: zoneOccForPeriod,
  };
}

// ═══════════════════════════════════════════════
// HELPER: Next slot (legacy sequential fallback)
// ═══════════════════════════════════════════════

async function getNextSlotSmart() {
  const occupiedRows = await dbAll(`SELECT slot FROM parking WHERE exit_time IS NULL`);
  const reservedRows = await dbAll(`SELECT slot FROM reservations`);
  const occupied = new Set(occupiedRows.map(r => r.slot));
  const reserved = new Set(reservedRows.map(r => r.slot));
  return await getSmartSlot(occupied, reserved);
}

// ═══════════════════════════════════════════════
// FAKE ANPR — Plate generation (since no OpenCV)
// ═══════════════════════════════════════════════

const STATES = ["MH", "KA", "DL", "TN", "GJ", "UP", "RJ", "MP", "WB", "AP", "TS", "KL", "HR", "PB"];
const ALPHA = "ABCDEFGHJKLMNPQRSTUVWXY";

function generatePlate() {
  const state = STATES[Math.floor(Math.random() * STATES.length)];
  const dist = String(Math.floor(Math.random() * 99) + 1).padStart(2, "0");
  const s1 = ALPHA[Math.floor(Math.random() * ALPHA.length)];
  const s2 = ALPHA[Math.floor(Math.random() * ALPHA.length)];
  const num = String(Math.floor(Math.random() * 9000) + 1000);
  return `${state}${dist}${s1}${s2}${num}`;
}

// ═══════════════════════════════════════════════
// PAGE ROUTES
// ═══════════════════════════════════════════════

app.get("/", (req, res) => res.redirect("/user"));

app.get("/user", (req, res) =>
  res.sendFile(path.join(__dirname, "views", "form.html"))
);
app.get("/exit", (req, res) =>
  res.sendFile(path.join(__dirname, "views", "exit.html"))
);
app.get("/search", (req, res) =>
  res.sendFile(path.join(__dirname, "views", "search.html"))
);
app.get("/reserve", (req, res) =>
  res.sendFile(path.join(__dirname, "views", "reserve.html"))
);
app.get("/admin", (req, res) =>
  res.sendFile(path.join(__dirname, "views", "admin.html"))
);

function requireAdmin(req, res, next) {
  if (!req.session.admin) return res.redirect("/admin");
  next();
}

app.get("/dashboard", requireAdmin, (req, res) =>
  res.sendFile(path.join(__dirname, "views", "dashboard.html"))
);
app.get("/anpr", requireAdmin, (req, res) =>
  res.sendFile(path.join(__dirname, "views", "anpr.html"))
);
app.get("/demo", requireAdmin, (req, res) =>
  res.sendFile(path.join(__dirname, "views", "demo.html"))
);
app.get("/report", requireAdmin, (req, res) =>
  res.sendFile(path.join(__dirname, "views", "report.html"))
);

app.get("/admin/settings", requireAdmin, (req, res) =>
  res.sendFile(path.join(__dirname, "views", "admin_settings.html"))
);

// Zone detail page
app.get("/zone/:zone", requireAdmin, async (req, res) => {
  const z = req.params.zone.toUpperCase();
  if (!ZONES[z]) return res.redirect("/dashboard");
  res.sendFile(path.join(__dirname, "views", "zone.html"));
});

// ═══════════════════════════════════════════════
// PARKING ENTRY
// ═══════════════════════════════════════════════

app.post("/submit", async (req, res) => {
  const { name, phone, vehicle } = req.body;
  if (!name || !vehicle) return res.redirect("/user?error=fields");
  if (phone && !/^\d{10}$/.test(phone)) return res.redirect("/user?error=phone");

  const existing = await dbGet(
    `SELECT * FROM parking WHERE vehicle=? AND exit_time IS NULL`,
    [vehicle.toUpperCase()]
  );
  if (existing) return res.redirect(`/user?error=duplicate&v=${encodeURIComponent(vehicle)}`);

  const slot = await getNextSlotSmart();
  if (!slot) return res.redirect("/user?error=full");

  const zone = slotToZone(slot);
  const label = slotToLabel(slot);
  const entryTime = new Date().toISOString().replace("T", " ").slice(0, 19);

  await dbRun(
    `INSERT INTO parking (name, phone, vehicle, slot, zone, slot_label, entry_time)
     VALUES (?, ?, ?, ?, ?, ?, ?)`,
    [name.trim(), phone || "", vehicle.toUpperCase(), slot, zone, label, entryTime]
  );

  const now = new Date();
  const settings = await getSettings();
  const peak = await getCurrentPeakStatus();

  res.send(`<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Parking Confirmed — Smart Parking</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/css/style.css">
<style>
.receipt-page{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px;position:relative;z-index:1;}
.receipt{width:100%;max-width:520px;background:rgba(6,12,24,.95);border:1px solid rgba(255,255,255,.14);border-radius:28px;padding:44px 40px;box-shadow:0 0 0 1px rgba(16,185,129,.12),0 30px 70px rgba(0,0,0,.6),0 0 100px rgba(16,185,129,.08);}
.receipt::before{content:'';position:absolute;top:0;left:0;right:0;height:1px;background:linear-gradient(90deg,transparent,rgba(16,185,129,.7),transparent);}
.check-icon{width:72px;height:72px;border-radius:20px;background:linear-gradient(135deg,rgba(16,185,129,.2),rgba(6,182,212,.12));border:1px solid rgba(16,185,129,.3);display:flex;align-items:center;justify-content:center;font-size:2rem;margin:0 auto 24px;box-shadow:0 0 40px rgba(16,185,129,.2);animation:float 4s ease-in-out infinite;}
@keyframes float{0%,100%{transform:translateY(0);}50%{transform:translateY(-6px);}}
.slot-badge{display:inline-flex;align-items:center;gap:8px;background:linear-gradient(135deg,rgba(37,99,235,.18),rgba(6,182,212,.12));border:1px solid rgba(59,130,246,.3);padding:10px 22px;border-radius:100px;font-size:1.4rem;font-weight:900;letter-spacing:.06em;color:#93c5fd;margin:16px auto;box-shadow:0 0 30px rgba(59,130,246,.15);}
.info-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:20px 0;}
.info-item{background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.08);border-radius:12px;padding:14px;}
.info-lbl{font-size:.72rem;font-weight:700;color:#64748b;letter-spacing:.05em;text-transform:uppercase;margin-bottom:4px;}
.info-val{font-size:.95rem;font-weight:700;color:#f0f4ff;}
.ai-insight{background:linear-gradient(135deg,rgba(37,99,235,.08),rgba(6,182,212,.05));border:1px solid rgba(59,130,246,.2);border-radius:14px;padding:14px 18px;margin:16px 0;font-size:.83rem;color:#93c5fd;}
.btn-row{display:flex;flex-direction:column;gap:10px;margin-top:24px;}
.btn-primary{display:flex;align-items:center;justify-content:center;gap:8px;padding:13px;background:linear-gradient(135deg,#2563eb,#06b6d4);border:none;border-radius:12px;color:white;font-weight:700;font-size:.95rem;text-decoration:none;font-family:Inter,sans-serif;cursor:pointer;transition:.2s;}
.btn-primary:hover{transform:translateY(-2px);box-shadow:0 8px 28px rgba(37,99,235,.4);}
.btn-sec{display:flex;align-items:center;justify-content:center;gap:8px;padding:12px;border:1px solid rgba(255,255,255,.1);border-radius:12px;color:#94a3b8;text-decoration:none;font-size:.88rem;font-weight:500;transition:.2s;}
.btn-sec:hover{background:rgba(255,255,255,.05);color:#f0f4ff;}
</style>
</head><body>
<div class="receipt-page">
<div class="receipt" style="position:relative;">
  <div style="text-align:center;">
    <div class="check-icon">✅</div>
    <h1 style="font-size:1.7rem;font-weight:900;letter-spacing:-.02em;margin-bottom:6px;">Parking Confirmed</h1>
    <p style="color:#94a3b8;font-size:.9rem;margin-bottom:4px;">Your slot has been reserved</p>
    <div class="slot-badge">🅿 ${label}</div>
    <div style="font-size:.78rem;color:#64748b;margin-top:4px;">Zone ${zone} · Slot ${label}</div>
  </div>

  <div class="info-grid">
    <div class="info-item">
      <div class="info-lbl">Name</div>
      <div class="info-val">${escapeHtml(name)}</div>
    </div>
    <div class="info-item">
      <div class="info-lbl">Vehicle</div>
      <div class="info-val">${escapeHtml(vehicle.toUpperCase())}</div>
    </div>
    <div class="info-item">
      <div class="info-lbl">Phone</div>
      <div class="info-val">${escapeHtml(phone) || "—"}</div>
    </div>
    <div class="info-item">
      <div class="info-lbl">Entry Time</div>
      <div class="info-val">${now.toLocaleTimeString("en-IN")}</div>
    </div>
    <div class="info-item">
      <div class="info-lbl">Date</div>
      <div class="info-val">${now.toLocaleDateString("en-IN")}</div>
    </div>
    <div class="info-item">
      <div class="info-lbl">Rate</div>
      <div class="info-val">₹${settings.hourly_rate}/hr</div>
    </div>
  </div>

  <div class="ai-insight">
    🤖 <strong>AI Allocation:</strong> Assigned <strong>${label}</strong> because ${
      peak.strategy === "zone_rotation"
        ? `Zone ${zone} supports optimal distribution during peak demand`
        : peak.strategy === "balanced"
        ? `Zone ${zone} currently has the best occupancy balance`
        : `${label} is the next available slot in sequence`
    }.
  </div>

  <div class="btn-row">
    <a href="/user" class="btn-primary">← Park Another Vehicle</a>
    <a href="/map" class="btn-sec">🗺 View Live Parking Map</a>
    <a href="/search" class="btn-sec">🔍 Search a Vehicle</a>
  </div>
</div>
</div>
</body></html>`);
});

// ═══════════════════════════════════════════════
// VEHICLE EXIT + BILLING
// ═══════════════════════════════════════════════

app.post("/exitVehicle", async (req, res) => {
  const vehicle = (req.body.vehicle || "").toUpperCase();
  const row = await dbGet(
    `SELECT * FROM parking WHERE vehicle=? AND exit_time IS NULL`,
    [vehicle]
  );
  if (!row) return res.redirect("/exit?error=notfound");

  const settings = await getSettings();
  const exitTime = new Date().toISOString().replace("T", " ").slice(0, 19);
  const { amount, duration } = calculateBill(row.entry_time, exitTime, settings.hourly_rate, settings.daily_rate);

  await dbRun(
    `UPDATE parking SET exit_time=?, amount_charged=? WHERE vehicle=? AND exit_time IS NULL`,
    [exitTime, amount, vehicle]
  );

  const slotLabel = row.slot_label || slotToLabel(row.slot);
  const zone = row.zone || slotToZone(row.slot);
  const now = new Date();

  res.send(`<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Exit Confirmed — Smart Parking</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/css/style.css">
<style>
.receipt-page{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px;position:relative;z-index:1;}
.receipt{width:100%;max-width:520px;background:rgba(6,12,24,.95);border:1px solid rgba(255,255,255,.14);border-radius:28px;padding:44px 40px;position:relative;box-shadow:0 0 0 1px rgba(245,158,11,.1),0 30px 70px rgba(0,0,0,.6);}
.receipt::before{content:'';position:absolute;top:0;left:0;right:0;height:1px;background:linear-gradient(90deg,transparent,rgba(245,158,11,.7),transparent);}
.amt-badge{background:linear-gradient(135deg,rgba(16,185,129,.15),rgba(6,182,212,.1));border:1px solid rgba(16,185,129,.3);border-radius:20px;padding:20px 24px;text-align:center;margin:20px 0;}
.info-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:16px 0;}
.info-item{background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.08);border-radius:12px;padding:14px;}
.info-lbl{font-size:.72rem;font-weight:700;color:#64748b;letter-spacing:.05em;text-transform:uppercase;margin-bottom:4px;}
.info-val{font-size:.95rem;font-weight:700;color:#f0f4ff;}
.btn-primary{display:flex;align-items:center;justify-content:center;gap:8px;padding:13px;background:linear-gradient(135deg,#2563eb,#06b6d4);border:none;border-radius:12px;color:white;font-weight:700;font-size:.95rem;text-decoration:none;transition:.2s;margin-bottom:10px;}
.btn-primary:hover{transform:translateY(-2px);box-shadow:0 8px 28px rgba(37,99,235,.4);}
.btn-sec{display:flex;align-items:center;justify-content:center;gap:8px;padding:12px;border:1px solid rgba(255,255,255,.1);border-radius:12px;color:#94a3b8;text-decoration:none;font-size:.88rem;font-weight:500;transition:.2s;}
.btn-sec:hover{background:rgba(255,255,255,.05);color:#f0f4ff;}
@media print{.btn-row{display:none;}}
</style>
</head><body>
<div class="receipt-page">
<div class="receipt">
  <div style="text-align:center;margin-bottom:16px;">
    <div style="width:72px;height:72px;border-radius:20px;background:linear-gradient(135deg,rgba(245,158,11,.2),rgba(239,68,68,.12));border:1px solid rgba(245,158,11,.3);display:flex;align-items:center;justify-content:center;font-size:2rem;margin:0 auto 20px;animation:float 4s ease-in-out infinite;">🚗</div>
    <h1 style="font-size:1.7rem;font-weight:900;letter-spacing:-.02em;margin-bottom:4px;">Exit Successful</h1>
    <p style="color:#94a3b8;font-size:.9rem;">Slot ${slotLabel} has been freed</p>
  </div>

  <div class="amt-badge">
    <div style="font-size:.75rem;font-weight:700;color:#64748b;letter-spacing:.06em;text-transform:uppercase;margin-bottom:8px;">Amount Due</div>
    <div style="font-size:2.8rem;font-weight:900;letter-spacing:-.03em;background:linear-gradient(135deg,#6ee7b7,#34d399);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;">₹${amount}</div>
    <div style="font-size:.82rem;color:#64748b;margin-top:4px;">Duration: ${duration}</div>
  </div>

  <div class="info-grid">
    <div class="info-item"><div class="info-lbl">Vehicle</div><div class="info-val">${escapeHtml(vehicle)}</div></div>
    <div class="info-item"><div class="info-lbl">Slot</div><div class="info-val">${slotLabel} · Zone ${zone}</div></div>
    <div class="info-item"><div class="info-lbl">Entry</div><div class="info-val" style="font-size:.85rem;">${row.entry_time}</div></div>
    <div class="info-item"><div class="info-lbl">Exit</div><div class="info-val" style="font-size:.85rem;">${now.toLocaleString("en-IN")}</div></div>
    <div class="info-item"><div class="info-lbl">Rate</div><div class="info-val">₹${settings.hourly_rate}/hr</div></div>
    <div class="info-item"><div class="info-lbl">Name</div><div class="info-val">${escapeHtml(row.name)}</div></div>
  </div>

  <div class="btn-row" style="margin-top:20px;">
    <button onclick="window.print()" class="btn-primary">🖨 Print Receipt</button>
    <a href="/user" class="btn-sec">← Back to User Portal</a>
    <a href="/map" class="btn-sec">🗺 View Parking Map</a>
  </div>
</div>
</div>
</body></html>`);
});

// ═══════════════════════════════════════════════
// SEARCH
// ═══════════════════════════════════════════════

app.post("/searchVehicle", async (req, res) => {
  const vehicle = (req.body.vehicle || "").toUpperCase();
  const row = await dbGet(
    `SELECT * FROM parking WHERE vehicle=? AND exit_time IS NULL`,
    [vehicle]
  );
  if (!row) return res.redirect(`/search?error=notfound&v=${encodeURIComponent(vehicle)}`);

  const slotLabel = row.slot_label || slotToLabel(row.slot);
  const zone = row.zone || slotToZone(row.slot);
  const entry = new Date(row.entry_time.replace(" ", "T"));
  const now = new Date();
  const diffMs = now - entry;
  const hours = Math.floor(diffMs / 3600000);
  const mins = Math.floor((diffMs % 3600000) / 60000);
  const settings = await getSettings();
  const { amount } = calculateBill(row.entry_time, now.toISOString().replace("T", " ").slice(0, 19), settings.hourly_rate, settings.daily_rate);

  res.send(`<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Vehicle Found — Smart Parking</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/css/style.css">
<style>
.receipt-page{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px;position:relative;z-index:1;}
.card{width:100%;max-width:480px;background:rgba(6,12,24,.95);border:1px solid rgba(255,255,255,.14);border-radius:28px;padding:44px 40px;position:relative;}
.card::before{content:'';position:absolute;top:0;left:0;right:0;height:1px;background:linear-gradient(90deg,transparent,rgba(59,130,246,.7),transparent);}
.slot-big{display:inline-flex;align-items:center;gap:8px;background:linear-gradient(135deg,rgba(37,99,235,.18),rgba(6,182,212,.12));border:1px solid rgba(59,130,246,.3);padding:10px 22px;border-radius:100px;font-size:1.5rem;font-weight:900;letter-spacing:.06em;color:#93c5fd;margin:12px auto;}
.info-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:16px 0;}
.info-item{background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.08);border-radius:12px;padding:14px;}
.info-lbl{font-size:.72rem;font-weight:700;color:#64748b;letter-spacing:.05em;text-transform:uppercase;margin-bottom:4px;}
.info-val{font-size:.95rem;font-weight:700;color:#f0f4ff;}
.btn-row{display:flex;flex-direction:column;gap:10px;margin-top:20px;}
.btn-primary{display:flex;align-items:center;justify-content:center;gap:8px;padding:13px;background:linear-gradient(135deg,#2563eb,#06b6d4);border:none;border-radius:12px;color:white;font-weight:700;font-size:.95rem;text-decoration:none;transition:.2s;}
.btn-sec{display:flex;align-items:center;justify-content:center;gap:8px;padding:12px;border:1px solid rgba(255,255,255,.1);border-radius:12px;color:#94a3b8;text-decoration:none;font-size:.88rem;font-weight:500;transition:.2s;}
.btn-sec:hover{background:rgba(255,255,255,.05);color:#f0f4ff;}
</style>
</head><body>
<div class="receipt-page">
<div class="card">
  <div style="text-align:center;margin-bottom:4px;">
    <div style="width:72px;height:72px;border-radius:20px;background:linear-gradient(135deg,rgba(37,99,235,.2),rgba(6,182,212,.15));border:1px solid rgba(59,130,246,.3);display:flex;align-items:center;justify-content:center;font-size:2rem;margin:0 auto 20px;">🚗</div>
    <h1 style="font-size:1.7rem;font-weight:900;letter-spacing:-.02em;margin-bottom:4px;">Vehicle Found</h1>
    <div class="slot-big">🅿 ${slotLabel}</div>
    <div style="font-size:.82rem;color:#64748b;">Zone ${zone} · Currently Parked</div>
  </div>
  <div class="info-grid">
    <div class="info-item"><div class="info-lbl">Name</div><div class="info-val">${escapeHtml(row.name)}</div></div>
    <div class="info-item"><div class="info-lbl">Vehicle</div><div class="info-val">${escapeHtml(row.vehicle)}</div></div>
    <div class="info-item"><div class="info-lbl">Duration</div><div class="info-val">${hours}h ${mins}m</div></div>
    <div class="info-item"><div class="info-lbl">Est. Bill</div><div class="info-val" style="color:#6ee7b7;">₹${amount}</div></div>
    <div class="info-item" style="grid-column:1/-1;"><div class="info-lbl">Entry Time</div><div class="info-val">${row.entry_time}</div></div>
  </div>
  <div class="btn-row">
    <a href="/exit" class="btn-primary">🚪 Process Exit</a>
    <a href="/search" class="btn-sec">← Search Again</a>
    <a href="/map" class="btn-sec">🗺 View on Map</a>
  </div>
</div>
</div>
</body></html>`);
});

// ═══════════════════════════════════════════════
// RESERVATIONS
// ═══════════════════════════════════════════════

app.post("/reserveSlot", async (req, res) => {
  const { employee_id, name, vehicle, slot } = req.body;
  const slotNum = parseInt(slot);
  if (slotNum < 1 || slotNum > 200) return res.redirect("/reserve?error=invalid");

  const zone = slotToZone(slotNum);
  const label = slotToLabel(slotNum);
  const now = new Date().toISOString().replace("T", " ").slice(0, 19);

  try {
    await dbRun(
      `INSERT INTO reservations (employee_id, name, vehicle, slot, zone, slot_label, reservation_date)
       VALUES (?, ?, ?, ?, ?, ?, ?)`,
      [employee_id, name, vehicle.toUpperCase(), slotNum, zone, label, now]
    );
    res.redirect(`/reserve?success=1&slot=${label}`);
  } catch (_) {
    res.redirect("/reserve?error=duplicate");
  }
});

// ═══════════════════════════════════════════════
// ADMIN AUTH
// ═══════════════════════════════════════════════

app.post("/createAdmin", async (req, res) => {
  const { username, password } = req.body;

  try {
    const hashedPassword = await bcrypt.hash(password, 10);

    await dbRun(
      `INSERT INTO admin (username, password) VALUES (?, ?)`,
      [username, hashedPassword]
    );

    res.redirect("/admin?created=1");
  } catch (_) {
    res.redirect("/admin?error=exists");
  }
});

app.post("/adminLogin", async (req, res) => {
  const { username, password } = req.body;

  const admin = await dbGet(
    `SELECT * FROM admin WHERE username=?`,
    [username]
  );

  if (!admin) {
    return res.redirect("/admin?error=invalid");
  }

  const passwordMatch = await bcrypt.compare(password, admin.password);

  if (!passwordMatch) {
    return res.redirect("/admin?error=invalid");
  }

  req.session.admin = true;
  req.session.username = username;

  res.redirect("/dashboard");
});

app.get("/logout", (req, res) => {
  req.session.destroy();
  res.redirect("/admin");
});

// Settings save
app.post("/admin/settings", requireAdmin, async (req, res) => {
  const { hourly_rate, daily_rate, anomaly_threshold, allocation_mode } = req.body;
  const updates = [
    ["hourly_rate", hourly_rate || "20"],
    ["daily_rate", daily_rate || "250"],
    ["anomaly_threshold", anomaly_threshold || "8"],
    ["allocation_mode", allocation_mode || "ai"],
  ];
  for (const [k, v] of updates) {
    await dbRun(`INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)`, [k, v]);
  }
  res.redirect("/admin/settings?saved=1");
});

// ═══════════════════════════════════════════════
// PARKING MAP (server-side rendered)
// ═══════════════════════════════════════════════

app.get("/map", async (req, res) => {
  const occupiedRows = await dbAll(`SELECT slot FROM parking WHERE exit_time IS NULL`);
  const reservedRows = await dbAll(`SELECT slot FROM reservations`);
  const occupiedSet = new Set(occupiedRows.map(r => r.slot));
  const reservedSet = new Set(reservedRows.map(r => r.slot));

  const occupiedCount = occupiedSet.size;
  const availableCount = 200 - occupiedCount;
  const occupancy = ((occupiedCount / 200) * 100).toFixed(1);

  let slotsHtml = `
<div class="map-stats-row">
  <div class="map-stat-card stat-total"><div class="big num-total">200</div><div class="lbl">Total Slots</div></div>
  <div class="map-stat-card stat-occ"><div class="big num-occ">${occupiedCount}</div><div class="lbl">Occupied</div></div>
  <div class="map-stat-card stat-avail"><div class="big num-avail">${availableCount}</div><div class="lbl">Available</div></div>
  <div class="map-stat-card stat-rate"><div class="big num-rate">${occupancy}%</div><div class="lbl">Occupancy Rate</div></div>
</div>
<div class="legend-row">
  <span class="legend-item legend-occupied"><span class="legend-dot ld-red"></span> Occupied</span>
  <span class="legend-item legend-reserved"><span class="legend-dot ld-blue"></span> Reserved</span>
  <span class="legend-item legend-available"><span class="legend-dot ld-green"></span> Available</span>
</div>`;

  for (const [zone, [start, end]] of Object.entries(ZONES)) {
    slotsHtml += `<div class="zone-section" data-zone="${zone}">
<div class="zone-section-header"><span class="zone-section-label">Zone ${zone}</span><span class="zone-section-sub">${slotToLabel(start)}–${slotToLabel(end-1+(zone==="A"?0:0))} · ${50 - [...Array(end-start+1)].filter((_,i)=>occupiedSet.has(start+i)).length} free</span></div>
<div class="grid-wrap">`;
    for (let i = start; i <= end; i++) {
      const lbl = slotToLabel(i);
      const cls = occupiedSet.has(i) ? "red" : reservedSet.has(i) ? "blue" : "green";
      slotsHtml += `<div class="slot ${cls}" title="Slot ${lbl}">${lbl}</div>`;
    }
    slotsHtml += `</div></div>`;
  }

  slotsHtml += `<div style="display:flex;gap:12px;flex-wrap:wrap;margin-top:24px;">
  <a href="/dashboard" style="display:inline-flex;align-items:center;gap:8px;color:#94a3b8;text-decoration:none;padding:10px 18px;border-radius:10px;border:1px solid rgba(255,255,255,.08);font-size:.875rem;font-weight:500;font-family:Inter,sans-serif;transition:.2s;">← Dashboard</a>
  <a href="/user" style="display:inline-flex;align-items:center;gap:8px;color:#94a3b8;text-decoration:none;padding:10px 18px;border-radius:10px;border:1px solid rgba(255,255,255,.08);font-size:.875rem;font-weight:500;font-family:Inter,sans-serif;transition:.2s;">👤 User Portal</a>
</div>`;

  const template = fs.readFileSync(path.join(__dirname, "views", "map.html"), "utf8");
  res.send(template.replace("{{CONTENT}}", slotsHtml));
});

// ═══════════════════════════════════════════════
// HISTORY & ADMIN VIEWS
// ═══════════════════════════════════════════════

app.get("/history", requireAdmin, async (req, res) => {
  const rows = await dbAll(`SELECT * FROM parking ORDER BY id DESC`);
  let html = `<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Parking History — Smart Parking</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/css/style.css">
</head><body>
<div class="dashboard">
  <div class="sidebar">
    <div class="sidebar-logo"><div class="logo-icon">🚗</div><div><h2>Smart Parking</h2><small>AI Platform</small></div></div>
    <div class="nav-label">Overview</div>
    <a href="/dashboard"><span class="nav-icon">🏠</span> Dashboard</a>
    <div class="nav-label">Management</div>
    <a href="/history" class="active"><span class="nav-icon">📜</span> History</a>
    <a href="/stats"><span class="nav-icon">📊</span> Statistics</a>
    <a href="/reservations"><span class="nav-icon">📋</span> Reservations</a>
    <a href="/map"><span class="nav-icon">🗺️</span> Live Map</a>
    <a href="/report"><span class="nav-icon">📈</span> Reports</a>
    <div class="nav-label">Tools</div>
    <a href="/anpr"><span class="nav-icon">📷</span> ANPR Scanner</a>
    <a href="/demo"><span class="nav-icon">🎮</span> Demo Mode</a>
    <a href="/admin/settings"><span class="nav-icon">⚙️</span> Settings</a>
    <div class="nav-label">User Portal</div>
    <a href="/user"><span class="nav-icon">👤</span> Park Vehicle</a>
    <a href="/exit"><span class="nav-icon">🚪</span> Vehicle Exit</a>
    <div style="margin-top:auto;padding-top:20px;border-top:1px solid rgba(255,255,255,.06);">
      <a href="/logout" style="display:flex;align-items:center;gap:10px;color:#f87171;text-decoration:none;padding:12px 16px;border-radius:12px;font-size:.9rem;font-weight:500;border:1px solid rgba(239,68,68,.15);background:rgba(239,68,68,.05);">⏻ Logout</a>
    </div>
  </div>
  <div class="main">
    <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:14px;margin-bottom:28px;">
      <div><h1 style="font-size:1.9rem;font-weight:900;letter-spacing:-.03em">📜 Parking History</h1>
      <p style="color:#94a3b8;margin-top:4px">All ${rows.length} parking records</p></div>
      <a href="/api/export/csv" style="display:inline-flex;align-items:center;gap:8px;padding:10px 20px;border-radius:10px;background:rgba(16,185,129,.1);border:1px solid rgba(16,185,129,.25);color:#34d399;text-decoration:none;font-size:.85rem;font-weight:600;">⬇ Export CSV</a>
    </div>
    <div class="table-wrap" style="background:rgba(255,255,255,.025);border:1px solid rgba(255,255,255,.07);border-radius:20px;">
    <table class="table">
    <thead><tr>
      <th>ID</th><th>Name</th><th>Vehicle</th><th>Slot</th><th>Zone</th>
      <th>Entry</th><th>Exit</th><th>Duration</th><th>Bill</th><th>Status</th><th>Action</th>
    </tr></thead><tbody>`;

  for (const row of rows) {
    const label = row.slot_label || slotToLabel(row.slot);
    const zone = row.zone || slotToZone(row.slot);
    let duration = "—", bill = "—";
    if (row.exit_time) {
      const { duration: d } = calculateBill(row.entry_time, row.exit_time);
      duration = d;
      bill = row.amount_charged ? `₹${row.amount_charged}` : "—";
    }
    const status = row.exit_time
      ? `<span class="badge-red">Exited</span>`
      : `<span class="badge-green">Active</span>`;
    html += `<tr>
      <td>${row.id}</td><td>${escapeHtml(row.name)}</td><td style="font-weight:700;color:#60a5fa">${escapeHtml(row.vehicle)}</td>
      <td style="font-weight:700;color:#93c5fd">${label}</td><td>${zone}</td>
      <td>${row.entry_time || "—"}</td><td>${row.exit_time || "—"}</td>
      <td>${duration}</td><td style="color:#6ee7b7;font-weight:700">${bill}</td>
      <td>${status}</td>
      <td><form action="/delete/${row.id}" method="POST" style="display:inline;">
  <button type="submit" class="delete-btn">🗑 Delete</button>
</form></td>
    </tr>`;
  }

  html += `</tbody></table></div></div></div></body></html>`;
  res.send(html);
});

app.get("/stats", requireAdmin, async (req, res) => {
  const total = await dbGet(`SELECT COUNT(*) AS c FROM parking`);
  const active = await dbGet(`SELECT COUNT(*) AS c FROM parking WHERE exit_time IS NULL`);
  const exited = await dbGet(`SELECT COUNT(*) AS c FROM parking WHERE exit_time IS NOT NULL`);
  const zones = await getZoneOccupancy();
  const rev = await getRevenueStats();
  res.redirect("/report");
});

app.get("/reservations", requireAdmin, async (req, res) => {
  const rows = await dbAll(`SELECT * FROM reservations ORDER BY id DESC`);
  let html = `<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Reservations — Smart Parking</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/css/style.css">
</head><body>
<div class="dashboard">
  <div class="sidebar">
    <div class="sidebar-logo"><div class="logo-icon">🚗</div><div><h2>Smart Parking</h2><small>AI Platform</small></div></div>
    <div class="nav-label">Overview</div><a href="/dashboard"><span class="nav-icon">🏠</span> Dashboard</a>
    <div class="nav-label">Management</div>
    <a href="/history"><span class="nav-icon">📜</span> History</a>
    <a href="/reservations" class="active"><span class="nav-icon">📋</span> Reservations</a>
    <a href="/map"><span class="nav-icon">🗺️</span> Live Map</a>
    <a href="/report"><span class="nav-icon">📈</span> Reports</a>
    <div style="margin-top:auto;padding-top:20px;border-top:1px solid rgba(255,255,255,.06);">
      <a href="/logout" style="display:flex;align-items:center;gap:10px;color:#f87171;text-decoration:none;padding:12px 16px;border-radius:12px;font-size:.9rem;font-weight:500;border:1px solid rgba(239,68,68,.15);background:rgba(239,68,68,.05);">⏻ Logout</a>
    </div>
  </div>
  <div class="main">
    <div style="margin-bottom:28px;"><h1 style="font-size:1.9rem;font-weight:900;letter-spacing:-.03em">📋 Staff Reservations</h1>
    <p style="color:#94a3b8;margin-top:4px">${rows.length} active reservations</p></div>
    <div class="table-wrap" style="background:rgba(255,255,255,.025);border:1px solid rgba(255,255,255,.07);border-radius:20px;">
    <table class="table"><thead><tr>
      <th>ID</th><th>Employee ID</th><th>Name</th><th>Vehicle</th><th>Slot</th><th>Zone</th><th>Date</th><th>Action</th>
    </tr></thead><tbody>`;

  for (const row of rows) {
    const label = row.slot_label || slotToLabel(row.slot);
    const zone = row.zone || slotToZone(row.slot);
    html += `<tr>
      <td>${row.id}</td><td style="color:#fcd34d;font-weight:700"><td>${escapeHtml(row.employee_id)}</td></td>
      <td>${escapeHtml(row.name)}</td>
      <td style="font-weight:700;color:#60a5fa">${escapeHtml(row.vehicle)}</td>>
      <td>${row.reservation_date || "—"}</td>
      <td><form action="/deleteReservation/${row.id}" method="POST" style="display:inline;">
  <button type="submit" class="delete-btn">🗑 Cancel</button>
</form></td>
    </tr>`;
  }

  html += `</tbody></table></div></div></div></body></html>`;
  res.send(html);
});

app.post("/delete/:id", requireAdmin, async (req, res) => {
  await dbRun(`DELETE FROM parking WHERE id=?`, [req.params.id]);
  res.redirect("/history");
});

app.post("/deleteReservation/:id", requireAdmin, async (req, res) => {
  await dbRun(`DELETE FROM reservations WHERE id=?`, [req.params.id]);
  res.redirect("/reservations");
});


// ═══════════════════════════════════════════════
// API ENDPOINTS
// ═══════════════════════════════════════════════

// Live insights for dashboard
app.get("/api/insights", async (req, res) => {
  try {
    const occupiedRows = await dbAll(`SELECT slot FROM parking WHERE exit_time IS NULL`);
    const liveCount = occupiedRows.length;
    const zones = await getZoneOccupancy();
    const peak = await getCurrentPeakStatus();
    const today = await getTodayStats();
    const revenue = await getRevenueStats();

    res.json({
      live_count: liveCount,
      available: 200 - liveCount,
      live_percent: Math.round((liveCount / 200) * 100),
      zones,
      peak,
      today,
      revenue,
      total_slots: 200,
    });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// 24h predictions
app.get("/api/predictions", async (req, res) => {
  try {
    const predictions = await getNext24hPredictions();
    res.json({ predictions });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// Anomaly detection
app.get("/api/anomalies", async (req, res) => {
  try {
    const anomalies = await getAnomalies();
    res.json({
      anomalies,
      count: anomalies.length,
      critical: anomalies.filter(a => a.severity === "critical").length,
    });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// Revenue & settings
app.get("/api/revenue", async (req, res) => {
  try {
    const data = await getRevenueStats();
    res.json(data);
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// Zone detail API
app.get("/api/zone/:zone", async (req, res) => {
  try {
    const z = req.params.zone.toUpperCase();
    if (!ZONES[z]) return res.status(404).json({ error: "Zone not found" });
    const [start, end] = ZONES[z];
    const occupiedRows = await dbAll(`SELECT slot, slot_label, vehicle, name, entry_time FROM parking WHERE exit_time IS NULL AND slot >= ? AND slot <= ?`, [start, end]);
    const reservedRows = await dbAll(`SELECT slot, slot_label FROM reservations WHERE slot >= ? AND slot <= ?`, [start, end]);
    const occupiedSet = new Set(occupiedRows.map(r => r.slot));
    const reservedSet = new Set(reservedRows.map(r => r.slot));
    const slots = [];
    for (let i = start; i <= end; i++) {
      const lbl = slotToLabel(i);
      const occ = occupiedRows.find(r => r.slot === i);
      slots.push({
        num: i,
        label: lbl,
        status: occupiedSet.has(i) ? "occupied" : reservedSet.has(i) ? "reserved" : "available",
        vehicle: occ?.vehicle || null,
        name: occ?.name || null,
        entry_time: occ?.entry_time || null,
      });
    }
    const zoneData = (await getZoneOccupancy())[z];
    const settings = await getSettings();
    const anomalies = (await getAnomalies()).filter(a => a.slot_label && a.slot_label.startsWith(z));
    const revRow = await dbGet(`SELECT COALESCE(SUM(amount_charged),0) as rev FROM parking WHERE exit_time IS NOT NULL AND slot >= ? AND slot <= ?`, [start, end]);

    res.json({ zone: z, slots, stats: zoneData, hourly_rate: settings.hourly_rate, anomalies, zone_revenue: Math.round((revRow?.rev || 0) * 100) / 100 });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// Reports
app.get("/api/report/:period", async (req, res) => {
  try {
    const data = await getReportData(req.params.period);
    res.json(data);
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// CSV Export
app.get("/api/export/csv", requireAdmin, async (req, res) => {
  const rows = await dbAll(`SELECT * FROM parking ORDER BY id DESC`);
  const settings = await getSettings();
  let csv = "ID,Name,Phone,Vehicle,Slot,Zone,Entry Time,Exit Time,Duration,Amount Charged\n";
  for (const row of rows) {
    const label = row.slot_label || slotToLabel(row.slot);
    const zone = row.zone || slotToZone(row.slot);
    let dur = "";
    if (row.exit_time) {
      const { duration } = calculateBill(row.entry_time, row.exit_time, settings.hourly_rate, settings.daily_rate);
      dur = duration;
    }
    csv += `${row.id},"${escapeHtml(row.name)}","${row.phone || ""}","${escapeHtml(row.vehicle)}","${label}","${zone}","${row.entry_time || ""}","${row.exit_time || ""}","${dur}","${row.amount_charged || ""}"\n`;
  }
  res.setHeader("Content-Type", "text/csv");
  res.setHeader("Content-Disposition", `attachment; filename="parking_export_${new Date().toISOString().slice(0,10)}.csv"`);
  res.send(csv);
});

// ANPR simulation
app.post("/api/anpr", async (req, res) => {
  try {
    const manualPlate = req.body.manual_plate || "";
    const plate = manualPlate ? manualPlate.toUpperCase() : generatePlate();
    await new Promise(r => setTimeout(r, 800 + Math.random() * 600));
    const slot = await getNextSlotSmart();
    const slotLabel = slot ? slotToLabel(slot) : "None";
    const confidence = Math.round(85 + Math.random() * 12) + "%";
    res.json({
      detected_plate: plate,
      suggested_slot: slotLabel,
      confidence,
      method: manualPlate ? "manual" : "simulation",
    });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// Demo simulation
app.post("/api/simulate", requireAdmin, async (req, res) => {
  const count = Math.min(parseInt(req.body.count || "10"), 50);
  const settings = await getSettings();
  let created = 0, skipped = 0;

  for (let i = 0; i < count; i++) {
    const plate = generatePlate();
    const name = ["Rahul Sharma", "Priya Patel", "Amit Kumar", "Sneha Reddy", "Vijay Singh", "Anita Joshi", "Kiran Nair", "Suresh Menon", "Deepa Verma", "Rajesh Gupta"][i % 10];
    const phone = "9" + String(Math.floor(Math.random() * 1e9)).padStart(9, "0");

    const existing = await dbGet(`SELECT 1 FROM parking WHERE vehicle=? AND exit_time IS NULL`, [plate]);
    if (existing) { skipped++; continue; }

    const slot = await getNextSlotSmart();
    if (!slot) { skipped++; continue; }

    const zone = slotToZone(slot);
    const label = slotToLabel(slot);
    const hoursAgo = Math.random() * 6;
    const entry = new Date(Date.now() - hoursAgo * 3600000);
    const entryStr = entry.toISOString().replace("T", " ").slice(0, 19);

    let exitStr = null, amount = null;
    if (Math.random() > 0.4) {
      const exitT = new Date(entry.getTime() + (0.5 + Math.random() * 3) * 3600000);
      exitStr = exitT.toISOString().replace("T", " ").slice(0, 19);
      const { amount: a } = calculateBill(entryStr, exitStr, settings.hourly_rate, settings.daily_rate);
      amount = a;
    }

    try {
      await dbRun(
        `INSERT INTO parking (name, phone, vehicle, slot, zone, slot_label, entry_time, exit_time, amount_charged) VALUES (?,?,?,?,?,?,?,?,?)`,
        [name, phone, plate, slot, zone, label, entryStr, exitStr, amount]
      );
      created++;
    } catch (_) { skipped++; }
  }

  res.json({ created, skipped, total: count });
});

// Clear demo data
app.post("/api/clear-demo", requireAdmin, async (req, res) => {
  await dbRun(`DELETE FROM parking`);
  res.json({ ok: true, message: "All parking records cleared" });
});

// Smart slot suggestion (used by ANPR)
app.get("/api/suggest-slot", async (req, res) => {
  const slot = await getNextSlotSmart();
  res.json({ slot, label: slot ? slotToLabel(slot) : null, zone: slot ? slotToZone(slot) : null });
});

// Check if a vehicle is currently parked (for ANPR duplicate detection)
app.get("/api/check-vehicle/:vehicle", async (req, res) => {
  try {
    const vehicle = req.params.vehicle.toUpperCase().replace(/\s/g, "");
    const row = await dbGet(
      `SELECT id, slot, zone, slot_label, entry_time FROM parking WHERE vehicle=? AND exit_time IS NULL`,
      [vehicle]
    );
    if (row) {
      const slotLabel = row.slot_label || slotToLabel(row.slot);
      const zone      = row.zone      || slotToZone(row.slot);
      res.json({ already_parked: true, slot: slotLabel, zone, entry_time: row.entry_time });
    } else {
      res.json({ already_parked: false });
    }
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// Settings API
app.get("/api/settings", async (req, res) => {
  const s = await getSettings();
  res.json(s);
});

// ═══════════════════════════════════════════════
// START
// ═══════════════════════════════════════════════

app.listen(PORT, "0.0.0.0", () => {
  console.log(`🚀 Smart Parking AI Platform running on port ${PORT}`);
});
