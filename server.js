const express = require("express");
const sqlite3 = require("sqlite3").verbose();
const bodyParser = require("body-parser");
const path = require("path");
const session = require("express-session");

const app = express();
const PORT = process.env.PORT || 5000;

app.use(bodyParser.urlencoded({ extended: true }));
app.use(bodyParser.json());
app.use(
  session({
    secret: "smartparkingsecret",
    resave: false,
    saveUninitialized: true
  })
);

const db = new sqlite3.Database("database.db");

// Create Table
db.run(
  `
  CREATE TABLE IF NOT EXISTS parking (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    phone TEXT UNIQUE,
    vehicle TEXT UNIQUE,
    slot INTEGER,
    entry_time TEXT,
    exit_time TEXT
  )
`,
  (err) => {
    if (err) {
      console.log("Table creation error:", err);
    } else {
      console.log("Database Ready");
    }
  }
);
db.run(`
CREATE TABLE IF NOT EXISTS reservations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id TEXT UNIQUE,
    name TEXT,
    vehicle TEXT,
    slot INTEGER,
    reservation_date TEXT
)
`);
db.run(`
CREATE TABLE IF NOT EXISTS admin (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE,
  password TEXT
)
`);
app.use(express.static(path.join(__dirname, "views")));

// Find Lowest Available Slot
function getNextSlot(callback) {
  db.all(
`
SELECT slot
FROM parking
WHERE exit_time IS NULL

UNION

SELECT slot
FROM reservations

ORDER BY slot
`

    ,
    (err, rows) => {
      if (err) return callback(null);

      let slot = 1;

      for (let row of rows) {
        if (row.slot === slot) {
          slot++;
        } else {
          break;
        }
      }

      if (slot > 200) {
        return callback(null);
      }

      callback(slot);
    }
  );
}

// Home
app.get("/", (req, res) => {
  res.send(`
    <h1>🚗 Smart Parking System</h1>

    <a href="/user">👤 User Portal</a>

    <br><br>

    <a href="/admin">🔐 Admin Login</a>
  `);
});

// User Form
app.get("/admin", (req, res) => {

    res.sendFile(
        path.join(
            __dirname,
            "views",
            "admin.html"
        )
    );

});
app.post("/createAdmin", (req, res) => {

  const { username, password } = req.body;

  db.run(
    "INSERT INTO admin(username,password) VALUES(?,?)",
    [username, password],
    (err) => {

      if (err) {
        console.log(err);
        return res.send("<h2>Admin Already Exists</h2>");
      }

      res.redirect("/admin");
    }
  );

});
app.post("/adminLogin", (req, res) => {

  const { username, password } = req.body;

  db.get(
    "SELECT * FROM admin WHERE username=? AND password=?",
    [username, password],
    (err, admin) => {

      if (err) {
        return res.send("Database Error");
      }

      if (!admin) {
        return res.send("<h2>❌ Invalid Login</h2>");
      }

      req.session.admin = true;

      res.redirect("/dashboard");

    }
  );

});
app.get("/dashboard", (req, res) => {

  if (!req.session.admin) {
    return res.redirect("/admin");
  }

 res.sendFile(
    path.join(
        __dirname,
        "views",
        "dashboard.html"
    )
);
});
app.get("/user", (req, res) => {
  res.sendFile(path.join(__dirname, "views", "form.html"));
});

// Parking Entry
app.post("/submit", (req, res) => {
  const { name, phone, vehicle } = req.body;

  // Validation
  if (!name || !phone || !vehicle) {
    return res.send("<h2>❌ All fields are required</h2>");
  }
  if (!/^\d{10}$/.test(phone)) {
  return res.send("<h2>❌ Phone number must be exactly 10 digits</h2>");
  }

  // Check Duplicate Vehicle
  db.get(
    "SELECT * FROM parking WHERE vehicle=? AND exit_time IS NULL",
    [vehicle],
    (err, row) => {
      if (err) {
        console.log(err);
        return res.send("Database Error");
      }

      if (row) {
        return res.send(
          "<h2>🚗 Vehicle already parked!</h2><a href='/user'>Back</a>"
        );
      }

      getNextSlot((slot) => {
        if (slot === null) {
          return res.send("<h2>❌ Parking Full</h2>");
        }

        db.run(
          `INSERT INTO parking
          (name, phone, vehicle, slot, entry_time)
          VALUES (?, ?, ?, ?, datetime('now'))`,
          [name, phone, vehicle, slot],
          (err) => {
            if (err) {
              console.log(err);
              return res.send("Database Error");
            }

            res.send(`
<h1>✅ Parking Confirmed</h1>

<pre>
================================
       PARKING RECEIPT
================================
Name       : ${name}
Phone      : ${phone}
Vehicle    : ${vehicle}
Slot       : ${slot}
Entry Time : ${new Date().toLocaleString()}
================================
</pre>

<a href="/user">Back</a><br><br>
<a href="/map">View Parking Map</a><br><br>
<a href="/history">View History</a><br><br>
<a href="/search">Search Vehicle</a>
`);
          }
        );
      });
    }
  );
});

app.get("/exit", (req, res) => {

  res.sendFile(
    path.join(
      __dirname,
      "views",
      "exit.html"
    )
  );

});
app.post("/exitVehicle", (req, res) => {
  const { vehicle } = req.body;

  db.get(
    "SELECT * FROM parking WHERE vehicle=? AND exit_time IS NULL",
    [vehicle],
    (err, row) => {
      if (err) {
        console.log(err);
        return res.send("Database Error");
      }

      if (!row) {
        return res.send("<h2>Vehicle Not Found</h2>");
      }
      const entryTime = new Date(row.entry_time);
const exitTime = new Date();

const diffMs = exitTime - entryTime;
const hours = Math.floor(diffMs / (1000 * 60 * 60));
const minutes = Math.floor(
  (diffMs % (1000 * 60 * 60)) / (1000 * 60)
);

      db.run(
        "UPDATE parking SET exit_time=datetime('now') WHERE vehicle=?",
        [vehicle],
        (err) => {
          if (err) {
            console.log(err);
            return res.send("Database Error");
          }

         res.send(`
  <h1>Exit Successful</h1>
  <p>Vehicle: ${vehicle}</p>
  <p>Freed Slot: ${row.slot}</p>
<p>Parking Duration: ${hours} Hour(s) ${minutes} Minute(s)</p>

  <a href="/user">Back</a><br><br>
  <a href="/map">View Parking Map</a><br><br>
  <a href="/history">View History</a>
`);
        }
      );
    }
  );
});
// Parking Map
app.get("/reserve", (req, res) => {

  res.sendFile(
    path.join(
      __dirname,
      "views",
      "reserve.html"
    )
  );

});
app.post("/reserveSlot", (req, res) => {

  const {
    employee_id,
    name,
    vehicle,
    slot
  } = req.body;

  db.run(
    `INSERT INTO reservations
    (employee_id,name,vehicle,slot,reservation_date)
    VALUES(?,?,?,?,datetime('now'))`,
    [
      employee_id,
      name,
      vehicle,
      slot
    ],
    (err) => {

      if (err) {
        console.log(err);
        return res.send(
          "<h2>Reservation Failed</h2>"
        );
      }

      res.send(`
        <h1>✅ Reservation Confirmed</h1>

        <p>Employee ID: ${employee_id}</p>
        <p>Name: ${name}</p>
        <p>Vehicle: ${vehicle}</p>
        <p>Reserved Slot: ${slot}</p>

        <a href="/user">Home</a>
      `);
    }
  );
});
app.get("/search", (req, res) => {

  res.sendFile(
    path.join(
      __dirname,
      "views",
      "search.html"
    )
  );

});
app.post("/searchVehicle", (req, res) => {
  const { vehicle } = req.body;

  db.get(
    "SELECT * FROM parking WHERE vehicle=? AND exit_time IS NULL",
    [vehicle],
    (err, row) => {

      if (err) {
        console.log(err);
        return res.send("Database Error");
      }

      if (!row) {
        return res.send("<h2>❌ Vehicle Not Found</h2>");
      }

     res.send(`
<!DOCTYPE html>
<html>
<head>
<style>

body{
  background:#0f172a;
  font-family:Poppins,sans-serif;
  display:flex;
  justify-content:center;
  align-items:center;
  height:100vh;
}

.card{
  background:white;
  padding:40px;
  border-radius:20px;
  width:450px;
}

h1{
  text-align:center;
  color:#2563eb;
}

p{
  margin:10px 0;
  font-size:18px;
}

a{
  display:block;
  text-align:center;
  margin-top:20px;
}

</style>
</head>


<body>

<div class="card">

<h1>🚗 Vehicle Found</h1>

<p><b>Name:</b> ${row.name}</p>
<p><b>Phone:</b> ${row.phone}</p>
<p><b>Vehicle:</b> ${row.vehicle}</p>
<p><b>Slot:</b> ${row.slot}</p>
<p><b>Entry Time:</b> ${row.entry_time}</p>

<a href="/search">Search Again</a>

</div>

</body>
</html>
`);
    }
  );
});

app.get("/reservations", (req, res) => {
  if (!req.session.admin) {
    return res.redirect("/admin");
  }

  db.all(
    "SELECT * FROM reservations ORDER BY id DESC",
    [],
    (err, rows) => {

      if (err) {
        return res.send("Database Error");
      }

      let html = `
      <h1>🏢 Staff Reservations</h1>

      <table border="1" cellpadding="10">
      <tr>
        <th>ID</th>
        <th>Employee ID</th>
        <th>Name</th>
        <th>Vehicle</th>
        <th>Reserved Slot</th>
        <th>Date</th>
      </tr>
      `;

      rows.forEach((row) => {
        html += `
        <tr>
          <td>${row.id}</td>
          <td>${row.employee_id}</td>
          <td>${row.name}</td>
          <td>${row.vehicle}</td>
          <td>${row.slot}</td>
          <td>${row.reservation_date}</td>
        </tr>
        `;
      });

      html += `
      </table>

      <br><br>
      <a href="/user">🏠 Home</a>
      `;

      res.send(html);
    }
  );
});

app.get("/history", (req, res) => {
  if (!req.session.admin) {
    return res.redirect("/admin");
  }
  db.all(
    "SELECT * FROM parking ORDER BY id DESC",
    [],
    (err, rows) => {
      if (err) {
        console.log(err);
        return res.send("Database Error");
      }
let html = `
<!DOCTYPE html>
<html>
<head>

<title>Parking History</title>

<link rel="stylesheet" href="/css/style.css">

<style>

body{
    padding:30px;
}

.history-card{
    background:white;
    border-radius:20px;
    overflow:hidden;
    box-shadow:0 15px 40px rgba(0,0,0,0.3);
}

.history-title{
    text-align:center;
    margin-bottom:25px;
}

table{
    width:100%;
    border-collapse:collapse;
    background:white;
    color:black;
}

th{
    background:#2563eb;
    color:white;
}

th,td{
    padding:14px;
    text-align:center;
}

tr:nth-child(even){
    background:#f7f7f7;
}

.delete-btn{
    color:red;
    text-decoration:none;
    font-weight:bold;
}

</style>

</head>

<body>

<div class="container">

<div class="history-title">
<h1>📜 Parking History</h1>
</div>

<div class="history-card">

<table>

<tr>
<th>ID</th>
<th>Name</th>
<th>Phone</th>
<th>Vehicle</th>
<th>Slot</th>
<th>Entry Time</th>
<th>Status</th>
<th>Action</th>
</tr>
`;

      rows.forEach((row) => {
        html += `
        <tr>
          <td>${row.id}</td>
          <td>${row.name}</td>
          <td>${row.phone}</td>
          <td>${row.vehicle}</td>
          <td>${row.slot}</td>
          <td>${row.entry_time}</td>
          <td>
${
row.exit_time
? '<span class="badge-red">Exited</span>'
: '<span class="badge-green">Active</span>'
}
</td>
          <td>
            <a
class="delete-btn"
href="/delete/${row.id}">
🗑 Delete
</a>
          </td>
        </tr>
        `;
      });

      html += `
</table>

<br><br>

<center>
<a href="/user">🏠 Home</a>
</center>

</body>
</html>
`;

      res.send(html);
    }
  );
});

app.get("/delete/:id", (req, res) => {
  if (!req.session.admin) {
  return res.redirect("/admin");
}
  db.run(
    "DELETE FROM parking WHERE id=?",
    [req.params.id],
    (err) => {
      if (err) {
        console.log(err);
        return res.send("Database Error");
      }

      res.redirect("/history");
    }
  );
});

app.get("/stats", (req, res) => {
  if (!req.session.admin) {
    return res.redirect("/admin");
  }

  db.get(
    `SELECT COUNT(*) AS totalRecords FROM parking`,
    [],
    (err, total) => {

      db.get(
        `SELECT COUNT(*) AS activeVehicles
         FROM parking
         WHERE exit_time IS NULL`,
        [],
        (err, active) => {

          db.get(
            `SELECT COUNT(*) AS exitedVehicles
             FROM parking
             WHERE exit_time IS NOT NULL`,
            [],
            (err, exited) => {

              res.send(`
                <h1>📊 Parking Statistics</h1>

                <h2>Total Records: ${total.totalRecords}</h2>

                <h2>Active Vehicles:
                ${active.activeVehicles}</h2>

                <h2>Exited Vehicles:
                ${exited.exitedVehicles}</h2>

                <br>

                <a href="/user">🏠 Home</a>
              `);

            }
          );
        }
      );
    }
  );
});
app.get("/map", (req, res) => {

  db.all(
    "SELECT slot FROM parking WHERE exit_time IS NULL",
    [],
    (err, occupiedRows) => {

      if (err) {
        return res.send("Database Error");
      }

      db.all(
        "SELECT slot FROM reservations",
        [],
        (err, reservedRows) => {

          if (err) {
            return res.send("Database Error");
          }

          const occupiedCount = occupiedRows.length;
          const availableCount = 200 - occupiedCount;
          const occupancy =
            ((occupiedCount / 200) * 100).toFixed(1);

       let slotsHtml = `
<div class="map-card">
<h3>Total Slots: 200</h3>
<h3>Occupied Slots: ${occupiedCount}</h3>
<h3>Available Slots: ${availableCount}</h3>
<h3>Occupancy Rate: ${occupancy}%</h3>
</div>

<div class="legend">

<span style="background:red;color:white;">
Occupied
</span>

<span style="background:#3b82f6;color:white;">
Reserved
</span>

<span style="background:#22c55e;color:white;">
Available
</span>

</div>

<div class="map-grid">
`;

          for (let i = 1; i <= 200; i++) {
slotsHtml += `</div>`;
            const occupied =
              occupiedRows.some(r => r.slot === i);

            const reserved =
              reservedRows.some(r => r.slot === i);

            slotsHtml += `
<div class="
slot
${
occupied
? "red"
: reserved
? "blue"
: "green"
}
">
${i}
</div>
`;
            
          }

        

         const fs = require("fs");

let template = fs.readFileSync(
  path.join(__dirname,"views","map.html"),
  "utf8"
);
slotsHtml += `
<br><br>

<center>
<a href="/dashboard">
⬅ Dashboard
</a>
</center>
`;
template = template.replace(
  "{{CONTENT}}",
  slotsHtml
);

res.send(template);
        }
      );
    }
  );
});
app.get("/logout", (req, res) => {
  req.session.destroy();
  res.redirect("/admin");
});
app.get("/resetAdmin", (req, res) => {

  db.run(
    "DELETE FROM admin",
    [],
    (err) => {

      if (err) {
        return res.send("Error");
      }

      res.send("Admin Reset Done");
    }
  );
});
app.listen(PORT, "0.0.0.0", () => {
  console.log(`🚀 Server running on port ${PORT}`);
});
