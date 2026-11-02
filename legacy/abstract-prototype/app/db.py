import json, secrets, sqlite3, time
from pathlib import Path
PATH = Path(__file__).resolve().parent.parent / "aquasense.db"

def conn():
    c = sqlite3.connect(PATH)
    c.row_factory = sqlite3.Row
    return c

def init():
    with conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS devices(id TEXT PRIMARY KEY, owner TEXT, name TEXT, source TEXT DEFAULT 'municipal', key TEXT);
        CREATE TABLE IF NOT EXISTS telemetry(id INTEGER PRIMARY KEY AUTOINCREMENT, device TEXT, ts REAL, data TEXT);
        CREATE TABLE IF NOT EXISTS tickets(id TEXT PRIMARY KEY, owner TEXT, device TEXT, issue TEXT, action TEXT, health INTEGER, status TEXT DEFAULT 'open', ts REAL);
        CREATE INDEX IF NOT EXISTS ix_tel ON telemetry(device, ts);
        """)

def new_id(prefix): return prefix + secrets.token_hex(4)

def latest(device):
    with conn() as c:
        r = c.execute("SELECT data, ts FROM telemetry WHERE device=? ORDER BY ts DESC LIMIT 1", (device,)).fetchone()
    return (json.loads(r["data"]), r["ts"]) if r else (None, None)

def add_telemetry(device, data):
    with conn() as c:
        c.execute("INSERT INTO telemetry(device, ts, data) VALUES(?,?,?)", (device, time.time(), json.dumps(data)))
