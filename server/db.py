"""SQLite persistence for the company server."""

from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(os.environ.get("AQUASENSE_SERVER_DB", Path(__file__).resolve().parent.parent / "data" / "aquasense_server.db"))
_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('admin','technician','customer')),
  phone TEXT DEFAULT '',
  area TEXT DEFAULT '',
  pin_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, created_at TEXT DEFAULT (datetime('now','localtime')));
CREATE TABLE IF NOT EXISTS devices(
  id TEXT PRIMARY KEY,
  name TEXT DEFAULT '',
  customer_id INTEGER,
  address TEXT DEFAULT '',
  fw TEXT DEFAULT '',
  ip TEXT DEFAULT '',
  simulated INTEGER DEFAULT 0,
  overlay_json TEXT,
  baseline_json TEXT,
  first_seen TEXT DEFAULT (datetime('now','localtime')),
  last_seen TEXT
);
CREATE TABLE IF NOT EXISTS readings(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  device_id TEXT NOT NULL,
  ts REAL NOT NULL,
  data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_readings ON readings(device_id, ts);
CREATE TABLE IF NOT EXISTS alerts(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  device_id TEXT NOT NULL,
  fault TEXT NOT NULL,
  label TEXT NOT NULL,
  score REAL,
  evidence TEXT,
  status TEXT DEFAULT 'open',
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  device_id TEXT NOT NULL,
  alert_id INTEGER,
  source TEXT NOT NULL,
  hypothesis TEXT,
  hypothesis_label TEXT,
  technician_id INTEGER,
  status TEXT NOT NULL DEFAULT 'new',
  note TEXT DEFAULT '',
  report_json TEXT,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
"""


def hash_pin(pin: str) -> str:
    return hashlib.sha256(("aquasense:" + str(pin)).encode()).hexdigest()


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=15, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    return c


@contextmanager
def tx():
    with _lock:
        c = connect()
        try:
            yield c
            c.commit()
        finally:
            c.close()


DEFAULT_USERS = [
    # username, name, role, phone, area, pin
    ("admin", "Service Desk", "admin", "+91 80000 00001", "", "1234"),
    ("ravi", "Ravi Kumar", "technician", "+91 98765 10001", "North", "1111"),
    ("anita", "Anita Desai", "technician", "+91 98765 10002", "South", "2222"),
    ("kavya", "Kavya Sharma", "customer", "+91 98111 21001", "Flat 302, Green Park, New Delhi", "3333"),
    ("arjun", "Arjun Rao", "customer", "+91 98600 22731", "Rao Clinic, MG Road, Hyderabad", "4444"),
    ("meera", "Meera Iyer", "customer", "+91 98300 10871", "17 Palm Grove, Bengaluru", "5555"),
]


def init(seed: bool = True):
    with tx() as c:
        c.executescript(SCHEMA)
        if seed and not c.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            for u, n, r, p, a, pin in DEFAULT_USERS:
                c.execute("INSERT INTO users(username,name,role,phone,area,pin_hash) VALUES(?,?,?,?,?,?)",
                          (u, n, r, p, a, hash_pin(pin)))


def reset():
    with tx() as c:
        for t in ("sessions", "readings", "alerts", "jobs", "devices", "users"):
            c.execute(f"DROP TABLE IF EXISTS {t}")
    init()


def new_token() -> str:
    return secrets.token_hex(20)
