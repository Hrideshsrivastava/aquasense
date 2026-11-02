"""SQLite storage for the company cloud (Unit C)."""

from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(os.environ.get("AQUASENSE_DB", Path(__file__).resolve().parent.parent / "data" / "aquasense.db"))
_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS units(
    id TEXT PRIMARY KEY,
    name TEXT,
    customer TEXT,
    phone TEXT,
    address TEXT,
    city TEXT,
    water_source TEXT DEFAULT 'municipal',
    usage_h REAL DEFAULT 2.0,
    simulated INTEGER DEFAULT 0,
    installed TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS readings(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    unit_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    tds_in REAL, tds_out REAL, temp_c REAL, pressure_bar REAL, flow_lpm REAL,
    pump_current_a REAL, pump_on INTEGER, buildup_s REAL, pump_runtime_s REAL,
    pump_cycles INTEGER, uv_hours REAL, filter_hours REAL, label TEXT,
    received_at TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(unit_id, ts)
);
CREATE INDEX IF NOT EXISTS ix_readings_unit_ts ON readings(unit_id, ts);
CREATE TABLE IF NOT EXISTS assessments(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    unit_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    fault TEXT, confidence REAL,
    result_json TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS ix_assess_unit_ts ON assessments(unit_id, ts);
CREATE TABLE IF NOT EXISTS daily_health(
    unit_id TEXT, day TEXT, component TEXT, health REAL, n INTEGER DEFAULT 1, simulated INTEGER DEFAULT 0,
    PRIMARY KEY(unit_id, day, component)
);
CREATE TABLE IF NOT EXISTS alert_state(
    unit_id TEXT PRIMARY KEY, fault TEXT, streak INTEGER, last_ts TEXT
);
CREATE TABLE IF NOT EXISTS alerts(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    unit_id TEXT, fault TEXT, fault_name TEXT, confidence REAL, reason TEXT,
    status TEXT DEFAULT 'open',
    created_at TEXT DEFAULT (datetime('now','localtime')),
    data_ts TEXT
);
CREATE TABLE IF NOT EXISTS tickets(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    unit_id TEXT, alert_id INTEGER, fault TEXT, part_to_carry TEXT,
    price_estimate INTEGER, job_card TEXT, status TEXT DEFAULT 'open',
    visit_window TEXT, note TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
"""


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


def init():
    with tx() as c:
        c.executescript(SCHEMA)


def reset():
    """Delete everything (demo reset)."""
    with tx() as c:
        for t in ("readings", "assessments", "daily_health", "alert_state", "alerts", "tickets", "units"):
            c.execute(f"DELETE FROM {t}")
