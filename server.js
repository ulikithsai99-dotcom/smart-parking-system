const express = require("express");
const sqlite3 = require("sqlite3").verbose();
const bodyParser = require("body-parser");
const session = require("express-session");
const bcrypt = require("bcrypt");

const app = express();
const PORT = 3000;

// ===================== CONFIG =====================
const TOTAL_SLOTS = 200;

// ===================== MIDDLEWARE =====================
app.use(bodyParser.urlencoded({ extended: true }));
app.use(bodyParser.json());

app.use(
  session({
    secret: "smart_parking_secret_key",
    resave: false,
    saveUninitialized: false,
    cookie: {
      httpOnly: true,
      secure: false,
      maxAge: 1000 * 60 * 30
    }
  })
);
app.use(
  session({
    secret: "smart_parking_secret_key",
    resave: false,
    saveUninitialized: false,
    cookie: {
      httpOnly: true,
      secure: false,
      maxAge: 1000 * 60 * 30
    }
  })
);

// ===================== DATABASE =====================
const db = new sqlite3.Database("database.db");

// Parking table
db.run(`
CREATE TABLE IF NOT EXISTS parking (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT,
  phone TEXT,
  vehicle TEXT UNIQUE,
  slot INTEGER,
  entry_time TEXT,
  exit_time TEXT
)
`);

// Admin table
db.run(`
CREATE TABLE IF NOT EXISTS admin (
  id INTEGER PRIMARY KEY,
  password TEXT
)
`);

// ===================== HELPERS =====================
function now() {
  return new Date().toLocaleString();
}

function isValidName(name) {
  return /^[A-Za-z ]+$/.test(name);
}

function isValidPhone(phone) {
  return /^[0-9]{10}$/.test(phone);
}

function isValidVehicle(vehicle) {
  return /^[A-Za-z0-9]+$/.test(vehicle);
}

// Get next free slot
function getNextFreeSlot(callback) {
  db.all("SELECT slot FROM parking WHERE exit_time IS NULL", [], (err, rows) => {
    if (err) return callback(null);

    const used = new Set(rows.map(r => r.slot));

    for (let s = 1; s <= TOTAL_SLOTS; s++) {
      if (!used.has(s)) {
        return callback(s);
      }
    }

    callback(null);
  });
}

// Slot counters
function getSlotStats(callback) {
  db.all("SELECT slot FROM parking WHERE exit_time IS NULL", [], (err, rows) => {
    if (err) return callback(null);

    const used = rows.length;

    callback({
      used,
      free: TOTAL_SLOTS - used
    });
  });
}
// ===================== BRUTE FORCE PROTECTION =====================
let loginAttempts = {};

function blockBruteForce(ip) {
  if (!loginAttempts[ip]) {
    loginAttempts[ip] = { count: 0, time: Date.now() };
  }

  const diff = Date.now() - loginAttempts[ip].time;

  // Reset attempts after 5 minutes
  if (diff > 5 * 60 * 1000) {
    loginAttempts[ip] = { count: 0, time: Date.now() };
  }

  loginAttempts[ip].count++;

  return loginAttempts[ip].count > 5; // block after 5 wrong tries
}


// ===================== ROUTES =====================

// ---------- HOME ----------
app.get("/", (req, res) => {
  res.send(`
<!DOCTYPE html>
<html>
<head>
<title>Smart Parking</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
* { box-sizing: border-box; }

body { 
  font-family: Arial; 
  background:#f4f6f8; 
  display:flex; 
  justify-content:center; 
  align-items:center; 
  min-height:100vh;
  margin:0;
  padding:15px;
}

.box { 
  background:white; 
  padding:30px; 
  border-radius:12px; 
  width:100%;
  max-width:350px;
  box-shadow:0 0 12px rgba(0,0,0,.12); 
  text-align:center; 
}

button { 
  width:100%; 
  padding:12px; 
  margin-top:15px; 
  font-size:16px; 
  border:none; 
  border-radius:10px; 
  cursor:pointer; 
}

.userBtn { background:#4CAF50; color:white; }
.adminBtn { background:#222; color:white; }
</style>
</head>
<body>

<div class="box">
  <h2>🚲 Smart Parking System</h2>
  <p>Select an option</p>

  <button class="userBtn" onclick="window.location.href='/user'">👤 User Entry</button>
  <button class="adminBtn" onclick="window.location.href='/admin-login'">🔑 Admin Login</button>
</div>

</body>
</html>
  `);
});

// ---------- USER ENTRY PAGE ----------
app.get("/user", (req, res) => {
  getSlotStats(stats => {
    if (!stats) stats = { used: 0, free: TOTAL_SLOTS };

    res.send(`
<!DOCTYPE html>
<html>
<head>
<title>User Entry</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
* { box-sizing: border-box; }

body { 
  font-family: Arial; 
  background:#f4f6f8; 
  display:flex; 
  justify-content:center; 
  align-items:center; 
  min-height:100vh;
  margin:0;
  padding:15px;
}

.box { 
  background:white; 
  padding:30px; 
  border-radius:12px; 
  width:100%;
  max-width:380px;
  box-shadow:0 0 12px rgba(0,0,0,.12); 
}

h2 { text-align:center; }

.stats {
  background:#f0f0f0;
  padding:12px;
  border-radius:10px;
  font-size:14px;
  margin-bottom:15px;
}

input, button { 
  width:100%; 
  padding:12px; 
  margin:10px 0; 
  border-radius:8px; 
  border:1px solid #ccc; 
  font-size:15px;
}

button { 
  background:#4CAF50; 
  color:white; 
  border:none; 
  font-size:16px; 
  cursor:pointer; 
}

a { 
  display:block; 
  text-align:center; 
  margin-top:10px; 
  text-decoration:none; 
  color:#333; 
  font-weight:bold;
}
</style>
</head>
<body>

<div class="box">
  <h2>🚲 User Parking Entry</h2>

  <div class="stats">
    <b>📊 Slot Availability</b><br><br>
    ✅ Free Slots: <b>${stats.free}</b><br>
    ❌ Used Slots: <b>${stats.used}</b><br>
    🎯 Total Slots: <b>${TOTAL_SLOTS}</b>
  </div>

  <form method="POST" action="/submit">
    <input name="name" placeholder="Name" required>
    <input name="phone" placeholder="Phone Number" required>
    <input name="vehicle" placeholder="Bike Number" required>
    <button type="submit">Get Slot</button>
  </form>

  <a href="/map">🗺 View Parking Map</a>
  <a href="/">⬅ Back</a>
</div>

</body>
</html>
    `);
  });
});

// ---------- SUBMIT ----------
app.post("/submit", (req, res) => {
  const { name, phone, vehicle } = req.body;

  if (!name || !isValidName(name)) {
    return res.send("❌ Name must contain only alphabets. <a href='/user'>Go back</a>");
  }

  if (!phone || !isValidPhone(phone)) {
    return res.send("❌ Phone must be exactly 10 digits. <a href='/user'>Go back</a>");
  }

  if (!vehicle || !isValidVehicle(vehicle)) {
    return res.send("❌ Vehicle number must not be empty and no special chars allowed. <a href='/user'>Go back</a>");
  }

  db.get(
    "SELECT * FROM parking WHERE vehicle = ? AND exit_time IS NULL",
    [vehicle],
    (err, row) => {
      if (row) {
        return res.send("❌ This vehicle is already parked. <a href='/user'>Go back</a>");
      }

      getNextFreeSlot(slot => {
        if (!slot) {
          return res.send("❌ Parking Full.");
        }

        db.run(
          "INSERT INTO parking (name, phone, vehicle, slot, entry_time) VALUES (?,?,?,?,?)",
          [name, phone, vehicle, slot, now()],
          (err) => {
            if (err) {
              console.error(err);
              return res.send("❌ Database error. Try again.");
            }

            res.send(`
<!DOCTYPE html>
<html>
<head>
<title>Parking Confirmed</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
* { box-sizing: border-box; }
body {
  font-family: Arial;
  background:#f4f6f8;
  display:flex;
  justify-content:center;
  align-items:center;
  min-height:100vh;
  margin:0;
  padding:15px;
}
.box {
  background:white;
  padding:30px;
  border-radius:12px;
  width:100%;
  max-width:400px;
  box-shadow:0 0 12px rgba(0,0,0,.12);
  text-align:center;
}
a {
  display:inline-block;
  margin-top:15px;
  padding:10px 20px;
  background:black;
  color:white;
  text-decoration:none;
  border-radius:10px;
}
</style>
</head>
<body>

<div class="box">
  <h1>✅ Parking Confirmed</h1>
  <h2>Your Slot: ${slot}</h2>
  <p>Please park your bike in the given slot.</p>
  <a href="/">Back</a>
</div>

</body>
</html>
            `);
          }
        );
      });
    }
  );
});

// ---------- ADMIN LOGIN (CREATE PASSWORD IF NOT EXISTS) ----------
app.get("/admin-login", (req, res) => {
  db.get("SELECT password FROM admin WHERE id = 1", [], (err, row) => {

    // If password not created yet
    if (!row || !row.password) {
      return res.send(`
<!DOCTYPE html>
<html>
<head>
<title>Create Admin Password</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
* { box-sizing: border-box; }
body { font-family: Arial; background:#f4f6f8; display:flex; justify-content:center; align-items:center; min-height:100vh; margin:0; padding:15px; }
.box { background:white; padding:30px; border-radius:12px; width:100%; max-width:350px; box-shadow:0 0 12px rgba(0,0,0,.12); text-align:center; }
input, button { width:100%; padding:12px; margin:10px 0; border-radius:8px; border:1px solid #ccc; font-size:15px; }
button { background:#4CAF50; color:white; border:none; font-size:16px; cursor:pointer; }
a { display:block; margin-top:10px; text-decoration:none; font-weight:bold; color:#333; }
</style>
</head>
<body>

<div class="box">
  <h2>🛠 Create Admin Password</h2>

  <form method="POST" action="/create-admin">
    <input type="password" name="newPass" placeholder="Create Password" required>
    <input type="password" name="confirmPass" placeholder="Confirm Password" required>
    <button type="submit">Create Password</button>
  </form>

  <a href="/">⬅ Back</a>
</div>

</body>
</html>
      `);
    }

    // Normal admin login
    res.send(`
<!DOCTYPE html>
<html>
<head>
<title>Admin Login</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
* { box-sizing: border-box; }
body { font-family: Arial; background:#f4f6f8; display:flex; justify-content:center; align-items:center; min-height:100vh; margin:0; padding:15px; }
.box { background:white; padding:30px; border-radius:12px; width:100%; max-width:350px; box-shadow:0 0 12px rgba(0,0,0,.12); text-align:center; }
input, button { width:100%; padding:12px; margin:10px 0; border-radius:8px; border:1px solid #ccc; font-size:15px; }
button { background:black; color:white; border:none; font-size:16px; cursor:pointer; }
a { display:block; margin-top:10px; text-decoration:none; font-weight:bold; color:#333; }
</style>
</head>
<body>

<div class="box">
  <h2>🔑 Admin Login</h2>

  <form method="POST" action="/admin-login">
    <input type="password" name="password" placeholder="Enter Admin Password" required>
    <button type="submit">Login</button>
  </form>

  <a href="/">⬅ Back</a>
</div>

</body>
</html>
    `);
  });
});

// ---------- CREATE ADMIN PASSWORD ----------
app.post("/create-admin", async (req, res) => {
  const { newPass, confirmPass } = req.body;

  if (newPass !== confirmPass) {
    return res.send("❌ Passwords do not match. <a href='/admin-login'>Try again</a>");
  }

  if (newPass.length < 6) {
    return res.send("❌ Password must be at least 6 characters. <a href='/admin-login'>Try again</a>");
  }

  const hashedPassword = await bcrypt.hash(newPass, 10);

  db.run(
    "INSERT OR REPLACE INTO admin (id, password) VALUES (1, ?)",
    [hashedPassword],
    (err) => {
      if (err) return res.send("❌ Error creating admin password.");

      res.send("✅ Admin password created successfully! <a href='/admin-login'>Go to Login</a>");
    }
  );
});


// ---------- ADMIN LOGIN POST ----------
app.post("/admin-login", (req, res) => {
  const { password } = req.body;

  db.get("SELECT password FROM admin WHERE id = 1", [], async (err, row) => {
    if (!row || !row.password) {
      return res.send("❌ Admin password not created yet. <a href='/admin-login'>Create Password</a>");
    }

    const match = await bcrypt.compare(password, row.password);

    if (match) {
      req.session.admin = true;
      return res.redirect("/admin");
    } else {
      return res.send("❌ Wrong Password <br><a href='/admin-login'>Try Again</a>");
    }
  });
});

// ---------- CHANGE PASSWORD PAGE ----------
app.get("/change-password", (req, res) => {
  if (!req.session.admin) {
    return res.send("❌ Access Denied. Admin only.");
  }

  res.send(`
<!DOCTYPE html>
<html>
<head>
<title>Change Password</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
* { box-sizing: border-box; }
body { font-family: Arial; background:#f4f6f8; display:flex; justify-content:center; align-items:center; min-height:100vh; margin:0; padding:15px; }
.box { background:white; padding:30px; border-radius:12px; width:100%; max-width:350px; box-shadow:0 0 12px rgba(0,0,0,.12); text-align:center; }
input, button { width:100%; padding:12px; margin:10px 0; border-radius:8px; border:1px solid #ccc; font-size:15px; }
button { background:black; color:white; border:none; font-size:16px; cursor:pointer; }
a { display:block; margin-top:10px; text-decoration:none; font-weight:bold; color:#333; }
</style>
</head>
<body>

<div class="box">
  <h2>🔐 Change Admin Password</h2>

  <form method="POST" action="/change-password">
    <input type="password" name="oldPass" placeholder="Old Password" required>
    <input type="password" name="newPass" placeholder="New Password" required>
    <input type="password" name="confirmPass" placeholder="Confirm New Password" required>
    <button type="submit">Update Password</button>
  </form>

  <a href="/admin">⬅ Back</a>
</div>

</body>
</html>
  `);
});

// ---------- CHANGE PASSWORD LOGIC ----------
app.post("/change-password", (req, res) => {
  if (!req.session.admin) {
    return res.send("❌ Access Denied. Admin only.");
  }

  const { oldPass, newPass, confirmPass } = req.body;

  if (newPass !== confirmPass) {
    return res.send("❌ New password and confirm password do not match. <a href='/change-password'>Try again</a>");
  }

  if (newPass.length < 6) {
    return res.send("❌ Password must be at least 6 characters. <a href='/change-password'>Try again</a>");
  }

  db.get("SELECT password FROM admin WHERE id = 1", [], async (err, row) => {
    if (!row) return res.send("❌ Admin password not found.");

    const match = await bcrypt.compare(oldPass, row.password);

    if (!match) {
      return res.send("❌ Old password incorrect. <a href='/change-password'>Try again</a>");
    }

    const hashedNew = await bcrypt.hash(newPass, 10);

    db.run("UPDATE admin SET password = ? WHERE id = 1", [hashedNew], (err) => {
      if (err) return res.send("❌ Error updating password.");

      res.send("✅ Password Updated Successfully! <a href='/admin'>Go Dashboard</a>");
    });
  });
});

// ---------- ADMIN DASHBOARD + SEARCH ----------
app.get("/admin", (req, res) => {
  if (!req.session.admin) {
    return res.send(`
      <h1>❌ Access Denied</h1>
      <a href="/admin-login">Login</a>
    `);
  }

  const search = req.query.search ? req.query.search.trim() : "";

  let query = "SELECT * FROM parking WHERE exit_time IS NULL";
  let params = [];

  if (search) {
    query += ` AND (
      vehicle LIKE ? OR
      name LIKE ? OR
      phone LIKE ? OR
      slot LIKE ?
    )`;
    params = [`%${search}%`, `%${search}%`, `%${search}%`, `%${search}%`];
  }

  db.all(query, params, (err, rows) => {
    if (!rows) rows = [];

    const slotMap = {};
    rows.forEach(r => {
      slotMap[r.slot] = r;
    });

    getSlotStats(stats => {
      if (!stats) stats = { used: 0, free: TOTAL_SLOTS };

      function box(n) {
        if (slotMap[n]) {
          const p = slotMap[n];
          return `
          <div class="slot used">
            <div class="num">Slot ${n}</div>
            <div class="info">
              👤 <b>${p.name}</b><br>
              🏍️ ${p.vehicle}<br>
              📞 ${p.phone}<br>
              ⏰ ${p.entry_time}<br>
              <a class="exit" href="/exit/${encodeURIComponent(p.vehicle)}">EXIT</a>
            </div>
          </div>
          `;
        } else {
          return `<div class="slot free"><div class="num">${n}</div></div>`;
        }
      }

      let allSlotsHTML = `<h2 class="sectionTitle">🏍️ Bike Slots (1 - ${TOTAL_SLOTS})</h2><div class="grid">`;

      for (let i = 1; i <= TOTAL_SLOTS; i++) {
        allSlotsHTML += box(i);
      }

      allSlotsHTML += "</div>";

      res.send(`
<!DOCTYPE html>
<html>
<head>
<title>Admin Dashboard</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
* { box-sizing: border-box; }

body { font-family: Arial; background:#f4f6f8; padding:20px; margin:0; }

.topbar {
  background:#222;
  color:white;
  padding:15px 20px;
  display:flex;
  justify-content:space-between;
  align-items:center;
  flex-wrap:wrap;
  gap:10px;
}

.topbar h1 {
  margin:0;
  font-size:20px;
}

.btn {
  padding:8px 14px;
  color:white;
  text-decoration:none;
  border-radius:8px;
  font-weight:bold;
  text-align:center;
}

.historyBtn { background:#4CAF50; }
.passBtn { background:blue; }
.logoutBtn { background:red; }

.container {
  max-width:1200px;
  margin:auto;
  padding:10px;
}

.stats {
  display:flex;
  gap:15px;
  flex-wrap:wrap;
  margin-bottom:20px;
}

.statCard {
  flex:1;
  min-width:250px;
  background:white;
  padding:15px;
  border-radius:12px;
  box-shadow:0 0 10px rgba(0,0,0,.1);
}

.searchBox {
  margin-bottom:20px;
}

.searchBox input {
  width:100%;
  padding:12px;
  border-radius:10px;
  border:1px solid #ccc;
  font-size:16px;
}

.sectionTitle {
  margin-top:30px;
  margin-bottom:10px;
  font-size:20px;
}

.grid {
  display:grid;
  grid-template-columns: repeat(6, 1fr);
  gap:10px;
}

.slot {
  border-radius:12px;
  padding:10px;
  min-height:80px;
  font-size:12px;
  box-shadow:0 0 8px rgba(0,0,0,.08);
}

.free {
  background:#b6fcb6;
  display:flex;
  justify-content:center;
  align-items:center;
  font-size:16px;
  font-weight:bold;
}

.used {
  background:#ff9a9a;
}

.num {
  font-weight:bold;
  font-size:14px;
}

.exit {
  display:inline-block;
  margin-top:6px;
  padding:5px 10px;
  background:black;
  color:white;
  text-decoration:none;
  border-radius:8px;
  font-size:12px;
}

/* ✅ Mobile Responsive Fix */
@media (max-width: 768px) {
  .grid {
    grid-template-columns: repeat(2, 1fr);
  }

  .stats {
    flex-direction: column;
  }

  .statCard {
    min-width: 100%;
  }

  .topbar {
    flex-direction: column;
    text-align: center;
  }
}

@media (max-width: 480px) {
  .grid {
    grid-template-columns: repeat(1, 1fr);
  }
}
</style>
</head>
<body>

<div class="topbar">
  <h1>🚦 Smart Parking Admin Dashboard</h1>

  <div style="display:flex; gap:10px; flex-wrap:wrap; justify-content:center;">
    <a class="btn historyBtn" href="/history">📜 History</a>
    <a class="btn passBtn" href="/change-password">🔐 Change Password</a>
    <a class="btn logoutBtn" href="/logout">Logout</a>
  </div>
</div>

<div class="container">

  <div class="stats">
    <div class="statCard">
      <h3>📊 Slot Status</h3>
      <p>Free: <b>${stats.free}</b></p>
      <p>Used: <b>${stats.used}</b></p>
      <p>Total: <b>${TOTAL_SLOTS}</b></p>
    </div>
  </div>

  <div class="searchBox">
    <form method="GET" action="/admin">
      <input name="search" placeholder="🔍 Search by Name / Phone / Vehicle / Slot..." value="${search}">
    </form>
  </div>

  ${allSlotsHTML}

</div>

</body>
</html>
      `);
    });
  });
});

// ---------- HISTORY PAGE ----------
app.get("/history", (req, res) => {
  if (!req.session.admin) {
    return res.send("❌ Access Denied. Admin only.");
  }

  db.all("SELECT * FROM parking ORDER BY id DESC", [], (err, rows) => {
    if (!rows) rows = [];

    let tableRows = "";

    rows.forEach(r => {
      tableRows += `
        <tr>
          <td>${r.id}</td>
          <td>${r.name}</td>
          <td>${r.phone}</td>
          <td>${r.vehicle}</td>
          <td>${r.slot}</td>
          <td>${r.entry_time}</td>
          <td>${r.exit_time ? r.exit_time : "<b style='color:green;'>PARKED</b>"}</td>
          <td>
            ${r.exit_time ? "<span style='color:red;font-weight:bold;'>EXITED</span>" : "<span style='color:green;font-weight:bold;'>ACTIVE</span>"}
          </td>
        </tr>
      `;
    });

    res.send(`
<!DOCTYPE html>
<html>
<head>
<title>Parking History</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
* { box-sizing: border-box; }
body { font-family: Arial; background:#f4f6f8; padding:15px; margin:0; }

.topbar {
  background:#222;
  color:white;
  padding:15px 20px;
  display:flex;
  justify-content:space-between;
  align-items:center;
  flex-wrap:wrap;
  gap:10px;
}

.topbar h1 {
  margin:0;
  font-size:20px;
}

.btn {
  padding:8px 14px;
  color:white;
  text-decoration:none;
  border-radius:8px;
  font-weight:bold;
}

.backBtn { background:#4CAF50; }
.logoutBtn { background:red; }

.container {
  max-width:1200px;
  margin:auto;
  padding:10px;
  overflow-x:auto;
}

table {
  width:100%;
  min-width:800px;
  border-collapse:collapse;
  background:white;
  border-radius:12px;
  overflow:hidden;
  box-shadow:0 0 10px rgba(0,0,0,.1);
}

th, td {
  padding:12px;
  border-bottom:1px solid #ddd;
  text-align:center;
  font-size:14px;
}

th {
  background:#333;
  color:white;
}

tr:hover {
  background:#f1f1f1;
}
</style>
</head>
<body>

<div class="topbar">
  <h1>📜 Parking History</h1>
  <div>
    <a class="btn backBtn" href="/admin">⬅ Dashboard</a>
    <a class="btn logoutBtn" href="/logout">Logout</a>
  </div>
</div>

<div class="container">
  <table>
    <tr>
      <th>ID</th>
      <th>Name</th>
      <th>Phone</th>
      <th>Vehicle</th>
      <th>Slot</th>
      <th>Entry Time</th>
      <th>Exit Time</th>
      <th>Status</th>
    </tr>

    ${tableRows}
  </table>
</div>

</body>
</html>
    `);
  });
});

// ---------- EXIT VEHICLE ----------
app.get("/exit/:vehicle", (req, res) => {
  if (!req.session.admin) {
    return res.send("❌ Access Denied. Admin only.");
  }

  const vehicle = decodeURIComponent(req.params.vehicle);
  const exitTime = new Date().toLocaleString();

  db.run(
    "UPDATE parking SET exit_time = ? WHERE vehicle = ? AND exit_time IS NULL",
    [exitTime, vehicle],
    function (err) {
      if (err) return res.send("❌ Error while exiting vehicle");
      if (this.changes === 0) return res.send("❌ Vehicle not found or already exited");

      res.redirect("/admin");
    }
  );
});

// ---------- LOGOUT ----------
app.get("/logout", (req, res) => {
  req.session.destroy(() => {
    res.redirect("/");
  });
});

// ---------- PUBLIC MAP ----------
app.get("/map", (req, res) => {
  db.all("SELECT slot FROM parking WHERE exit_time IS NULL", [], (err, rows) => {
    const used = new Set(rows.map(r => r.slot));

    function box(n) {
      return `<div class="slot ${used.has(n) ? "used" : "free"}">${n}</div>`;
    }

    let slots = "";
    for (let i = 1; i <= TOTAL_SLOTS; i++) {
      slots += box(i);
    }

    res.send(`
<!DOCTYPE html>
<html>
<head>
<title>Parking Map</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
* { box-sizing: border-box; }
body { font-family:Arial; background:#f4f6f8; padding:15px; margin:0; }

.grid { 
  display:grid; 
  grid-template-columns: repeat(5, 1fr); 
  gap:8px; 
  max-width:800px; 
  margin:auto; 
}

.slot { 
  height:40px; 
  text-align:center; 
  line-height:40px; 
  font-weight:bold; 
  border-radius:8px; 
  font-size:14px;
}

.free { background:#b6fcb6; }
.used { background:#ff9a9a; }

a { 
  display:block; 
  text-align:center; 
  margin-top:20px; 
  font-size:18px; 
  text-decoration:none; 
  font-weight:bold;
  color:#333;
}

@media(max-width:480px){
  .grid{
    grid-template-columns: repeat(4, 1fr);
  }
}
</style>
</head>
<body>

<h2 style="text-align:center;">🚲 Public Parking Map</h2>
<div class="grid">${slots}</div>
<a href="/">⬅ Back</a>

</body>
</html>
    `);
  });
});

// ===================== START SERVER =====================
app.listen(PORT, "0.0.0.0", () => {
  console.log("Server running on all interfaces at port " + PORT);
});
