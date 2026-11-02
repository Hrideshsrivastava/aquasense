"""Fill the database with demo purifiers so the console and fleet view are populated.
Run from the project root:  python -m simulator.seed"""
import json, secrets, time
from app import db
from simulator.simulate import reading

HOMES = [("demo@example.com", "Kitchen purifier", "municipal", None, 5), ("demo@example.com", "Office purifier", "borewell", "membrane", 40),
         ("asha@example.com", "Flat 12 purifier", "tanker", "pump", 60), ("ravi@example.com", "Villa purifier", "borewell", "clog", 45),
         ("meera@example.com", "Home purifier", "municipal", None, 10)]
db.init()
with db.conn() as c:
    for i, (owner, name, src, fault, steps) in enumerate(HOMES):
        did = f"P-demo{i}"
        c.execute("INSERT OR REPLACE INTO devices VALUES(?,?,?,?,?)", (did, owner, name, src, secrets.token_urlsafe(16)))
        c.execute("DELETE FROM telemetry WHERE device=?", (did,))
        now = time.time()
        for s in range(steps - 11 if steps > 11 else 0, steps + 1):
            c.execute("INSERT INTO telemetry(device, ts, data) VALUES(?,?,?)", (did, now - (steps - s) * 3600, json.dumps(reading(s, fault))))
print("Seeded", len(HOMES), "purifiers. Sign in (dev mode) as demo@example.com or admin@example.com.")
