"""Data format shared by the ESP32 SD log, the /ingest endpoint and the handheld.

Build plan section 5.3: one CSV per day, named by date (e.g. 2026-11-03.csv).
"""

from __future__ import annotations

CSV_HEADER = [
    "timestamp",
    "device_id",
    "tds_in",
    "tds_out",
    "temp_c",
    "pressure_bar",
    "flow_lpm",
    "pump_current_a",
    "pump_on",
    "buildup_s",
    "pump_runtime_s",
    "pump_cycles",
    "uv_hours",
    "filter_hours",
    "label",
]

NUMERIC_COLUMNS = [c for c in CSV_HEADER if c not in ("timestamp", "device_id", "label")]

# Firmware timing (build plan 5.1 / 5.2)
PUMP_ON_S = 90
PUMP_OFF_S = 30
LOG_INTERVAL_NORMAL_S = 5
LOG_INTERVAL_TRAINING_S = 2
SYNC_INTERVAL_S = 10

# Feature windows (build plan 8.2)
WINDOW_S = 60

# Fault classes (build plan 5.3 label column / 8.1)
NORMAL, CLOGGING, PUMP, MEMBRANE, SENSOR = 0, 1, 2, 3, 4
CLASS_NAMES = {
    NORMAL: "Normal",
    CLOGGING: "Filter/line clogging",
    PUMP: "Pump weakness",
    MEMBRANE: "Membrane wear",
    SENSOR: "Sensor fault",
}
CLASS_KEYS = {NORMAL: "normal", CLOGGING: "clogging", PUMP: "pump", MEMBRANE: "membrane", SENSOR: "sensor"}

# Components that get a health score (build plan 7.2)
COMPONENTS = ["pump", "membrane", "filter", "sensors"]
COMPONENT_NAMES = {
    "pump": "Booster pump",
    "membrane": "RO membrane",
    "filter": "Filter / line",
    "sensors": "Sensors",
}
# Which fault class damages which component
CLASS_TO_COMPONENT = {CLOGGING: "filter", PUMP: "pump", MEMBRANE: "membrane", SENSOR: "sensors"}
COMPONENT_TO_CLASS = {v: k for k, v in CLASS_TO_COMPONENT.items()}

# Health score target per fault-injection level (100 = healthy).
# Level 3 sits below the 40 % replacement threshold on purpose.
LEVEL_HEALTH = {0: 100.0, 1: 77.0, 2: 53.0, 3: 30.0}
REPLACE_THRESHOLD = 40.0

# Alerting (build plan 6, console item 3)
ALERT_PROB = 0.7
ALERT_CONSECUTIVE = 3

# Status colours used by the console, handheld and LEDs
STATUS_OK, STATUS_WARN, STATUS_REPLACE = "normal", "warning", "replace"


def health_status(health: float) -> str:
    if health < REPLACE_THRESHOLD:
        return STATUS_REPLACE
    if health < 70:
        return STATUS_WARN
    return STATUS_OK


def csv_filename(date_str: str) -> str:
    """'2026-11-03' -> '2026-11-03.csv'"""
    return f"{date_str}.csv"
