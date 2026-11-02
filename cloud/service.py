"""Cloud business logic: ingest, scoring, alerts, tickets, warranty.

The Flask app (server.py) is a thin HTTP layer over these functions, so the
console, the mobile app and the tests all exercise the same code.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from aquasense import degradation, features as F, pricing, schema
from aquasense.engine import get_engine

from . import db

LOOKBACK_S = 180  # rows loaded around a new window
EVAL_STEP_S = 10  # one window score per 10 s of data (the ESP32 sync period)
MAX_WINDOWS_PER_INGEST = 240
KEEP_ASSESSMENTS = 3000  # per unit
ONLINE_S = 45

READING_COLS = [c for c in schema.CSV_HEADER if c not in ("device_id",)]


# ------------------------------------------------------------------ parsing
def parse_payload(body: bytes, content_type: str = "", device_header: str | None = None) -> list[dict]:
    """Accepts what the ESP32 sends (CSV lines in the SD-card format, with or
    without the header) or JSON: one row, a list of rows, or {"rows": [...]}."""
    text = body.decode("utf-8", errors="replace").strip()
    if not text:
        return []
    rows: list[dict]
    if "json" in content_type or text[:1] in "[{":
        data = json.loads(text)
        if isinstance(data, dict) and "rows" in data:
            rows = list(data["rows"])
            device_header = device_header or data.get("device_id")
        elif isinstance(data, dict):
            rows = [data]
        else:
            rows = list(data)
    else:
        lines = [l for l in text.splitlines() if l.strip()]
        if lines and lines[0].startswith("timestamp,"):
            reader = csv.DictReader(io.StringIO("\n".join(lines)))
            rows = list(reader)
        else:
            rows = [dict(zip(schema.CSV_HEADER, next(csv.reader([l])))) for l in lines]
    out = []
    for r in rows:
        r = dict(r)
        if not r.get("device_id"):
            r["device_id"] = device_header
        if not r.get("device_id") or not r.get("timestamp"):
            continue
        out.append(r)
    return out


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ units
def ensure_unit(c, unit_id: str):
    if not c.execute("SELECT 1 FROM units WHERE id=?", (unit_id,)).fetchone():
        c.execute(
            "INSERT INTO units(id, name, customer, phone, address, city, water_source, usage_h, simulated, installed) "
            "VALUES(?,?,?,?,?,?,?,?,0,date('now','localtime'))",
            (unit_id, f"RO unit {unit_id}", "Demo customer", "+91 90000 00000", "Demo address", "Live rig", "tanker", 2.0),
        )


def upsert_unit(info: dict):
    with db.tx() as c:
        c.execute(
            "INSERT INTO units(id, name, customer, phone, address, city, water_source, usage_h, simulated, installed) "
            "VALUES(:id,:name,:customer,:phone,:address,:city,:water_source,:usage_h,:simulated,:installed) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name, customer=excluded.customer, phone=excluded.phone, "
            "address=excluded.address, city=excluded.city, water_source=excluded.water_source, "
            "usage_h=excluded.usage_h, simulated=excluded.simulated, installed=excluded.installed",
            {"name": info["id"], "customer": "", "phone": "", "address": "", "city": "", "water_source": "municipal",
             "usage_h": 2.0, "simulated": 0, "installed": datetime.now().date().isoformat(), **info},
        )


def get_unit(unit_id: str) -> dict | None:
    with db.tx() as c:
        r = c.execute("SELECT * FROM units WHERE id=?", (unit_id,)).fetchone()
    return dict(r) if r else None


# ------------------------------------------------------------------ ingest
def ingest(rows: list[dict], evaluate: bool = True) -> dict:
    by_unit: dict[str, list[dict]] = {}
    for r in rows:
        by_unit.setdefault(str(r["device_id"]), []).append(r)
    stored = dup = 0
    alerts: list[dict] = []
    assessed = 0
    for unit_id, urows in by_unit.items():
        with db.tx() as c:
            ensure_unit(c, unit_id)
            for r in urows:
                ts = pd.to_datetime(r["timestamp"], errors="coerce")
                if pd.isna(ts):
                    continue
                vals = [ts.strftime("%Y-%m-%dT%H:%M:%S")] + [_num(r.get(k)) for k in READING_COLS[1:-1]] + [str(r.get("label") or "")]
                cur = c.execute(
                    f"INSERT OR IGNORE INTO readings(unit_id, ts, {', '.join(READING_COLS[1:])}) VALUES(?{', ?' * len(READING_COLS)})",
                    [unit_id] + vals,
                )
                if cur.rowcount:
                    stored += 1
                else:
                    dup += 1
        if evaluate:
            n, a = evaluate_unit(unit_id)
            assessed += n
            alerts += a
    return {"stored": stored, "duplicates": dup, "assessed": assessed, "alerts": alerts}


def _load_readings(c, unit_id: str, since: str | None = None, until: str | None = None, limit: int | None = None) -> pd.DataFrame:
    q, a = "SELECT * FROM readings WHERE unit_id=?", [unit_id]
    if since:
        q += " AND ts>=?"
        a.append(since)
    if until:
        q += " AND ts<=?"
        a.append(until)
    q += " ORDER BY ts DESC" + (f" LIMIT {int(limit)}" if limit else "")
    rows = [dict(r) for r in c.execute(q, a)]
    rows.reverse()
    for r in rows:
        r["timestamp"] = r.pop("ts")
        r["device_id"] = r.pop("unit_id")
    return pd.DataFrame(rows)


def evaluate_unit(unit_id: str) -> tuple[int, list[dict]]:
    """Score every 10 s of new data with a 60 s window, in time order, and
    apply the alert rule (fault probability > 0.7 for 3 consecutive windows)."""
    eng = get_engine()
    with db.tx() as c:
        last = c.execute("SELECT MAX(ts) AS t FROM assessments WHERE unit_id=?", (unit_id,)).fetchone()["t"]
        newest = c.execute("SELECT MAX(ts) AS t FROM readings WHERE unit_id=?", (unit_id,)).fetchone()["t"]
        if newest is None:
            return 0, []
        newest_t = pd.Timestamp(newest)
        if last:
            start_t = pd.Timestamp(last) + pd.Timedelta(seconds=EVAL_STEP_S)
        else:  # first data for this unit: score everything we have (alert history included)
            oldest = c.execute("SELECT MIN(ts) AS t FROM readings WHERE unit_id=?", (unit_id,)).fetchone()["t"]
            start_t = min(newest_t, pd.Timestamp(oldest) + pd.Timedelta(seconds=60))
        if start_t > newest_t:
            return 0, []
        df = _load_readings(c, unit_id, since=(min(start_t, newest_t) - pd.Timedelta(seconds=LOOKBACK_S)).isoformat(),
                            until=newest)
    fr = F.to_frame(df)
    if fr.empty:
        return 0, []
    span = (newest_t - start_t).total_seconds()
    step = max(EVAL_STEP_S, span / MAX_WINDOWS_PER_INGEST)
    ends = [start_t + pd.Timedelta(seconds=step * i) for i in range(int(span // step) + 1)]
    if ends[-1] < newest_t:
        ends.append(newest_t)
    results = []
    for end in ends:
        w = F.latest_window(fr, end=end)
        if w is None:
            continue
        r = eng.diagnose_window(w[1])
        r["window_end"] = end.isoformat()
        r["features"] = {k: (None if isinstance(v, float) and np.isnan(v) else round(float(v), 4)) for k, v in w[1].items()}
        results.append(r)
    new_alerts = []
    with db.tx() as c:
        for r in results:
            c.execute("INSERT INTO assessments(unit_id, ts, fault, confidence, result_json) VALUES(?,?,?,?,?)",
                      (unit_id, r["window_end"][:19], r["fault"], r["confidence"], json.dumps(r)))
            _update_daily(c, unit_id, r)
            a = _alert_rule(c, unit_id, r)
            if a:
                new_alerts.append(a)
        c.execute(
            "DELETE FROM assessments WHERE unit_id=? AND id NOT IN (SELECT id FROM assessments WHERE unit_id=? ORDER BY ts DESC LIMIT ?)",
            (unit_id, unit_id, KEEP_ASSESSMENTS),
        )
    return len(results), new_alerts


def _update_daily(c, unit_id: str, r: dict):
    day = r["window_end"][:10]
    for comp, h in r["health"].items():
        c.execute(
            "INSERT INTO daily_health(unit_id, day, component, health, n, simulated) VALUES(?,?,?,?,1,0) "
            "ON CONFLICT(unit_id, day, component) DO UPDATE SET health=(health*n + excluded.health)/(n+1), n=n+1 "
            "WHERE daily_health.simulated=0",  # never blend into a simulated history
            (unit_id, day, comp, h),
        )


def _alert_rule(c, unit_id: str, r: dict) -> dict | None:
    st = c.execute("SELECT * FROM alert_state WHERE unit_id=?", (unit_id,)).fetchone()
    qualifies = r["fault"] != "normal" and r["confidence"] > schema.ALERT_PROB
    if not qualifies:
        streak, fault = 0, None
    elif st and st["fault"] == r["fault"]:
        streak, fault = st["streak"] + 1, r["fault"]
    else:
        streak, fault = 1, r["fault"]
    c.execute("INSERT OR REPLACE INTO alert_state(unit_id, fault, streak, last_ts) VALUES(?,?,?,?)",
              (unit_id, fault, streak, r["window_end"][:19]))
    if streak < schema.ALERT_CONSECUTIVE:
        return None
    if c.execute("SELECT 1 FROM alerts WHERE unit_id=? AND fault=? AND status IN ('open','ticketed')",
                 (unit_id, fault)).fetchone():
        return None
    cur = c.execute(
        "INSERT INTO alerts(unit_id, fault, fault_name, confidence, reason, data_ts) VALUES(?,?,?,?,?,?)",
        (unit_id, fault, r["fault_name"], r["confidence"], r["reason"], r["window_end"][:19]),
    )
    return {"id": cur.lastrowid, "unit_id": unit_id, "fault": fault, "fault_name": r["fault_name"],
            "confidence": r["confidence"], "reason": r["reason"]}


# ------------------------------------------------------------------ reading back
def _slopes(c, unit_id: str) -> dict:
    out = {}
    for comp in degradation.DEGRADING:
        rows = c.execute("SELECT health FROM daily_health WHERE unit_id=? AND component=? ORDER BY day DESC LIMIT 14",
                         (unit_id, comp)).fetchall()
        if len(rows) >= 3:
            out[comp] = degradation.trend_slope(np.array([r["health"] for r in rows][::-1]))
    return out


def unit_summary(unit_id: str) -> dict:
    eng = get_engine()
    with db.tx() as c:
        u = c.execute("SELECT * FROM units WHERE id=?", (unit_id,)).fetchone()
        if not u:
            raise KeyError(unit_id)
        u = dict(u)
        res = [json.loads(r["result_json"]) for r in c.execute(
            "SELECT result_json FROM assessments WHERE unit_id=? ORDER BY ts DESC LIMIT 6", (unit_id,))][::-1]
        df = _load_readings(c, unit_id, limit=60)
        slopes = _slopes(c, unit_id)
        override = None
        if u["simulated"]:
            override = {r["component"]: r["health"] for r in c.execute(
                "SELECT component, health FROM daily_health WHERE unit_id=? AND simulated=1 AND day=(SELECT MAX(day) FROM daily_health WHERE unit_id=? AND simulated=1)",
                (unit_id, unit_id))}
            override = {k: v for k, v in override.items() if k in degradation.DEGRADING} or None
        last_rx = c.execute("SELECT MAX(received_at) AS t FROM readings WHERE unit_id=?", (unit_id,)).fetchone()["t"]
        alerts = [dict(r) for r in c.execute("SELECT * FROM alerts WHERE unit_id=? ORDER BY id DESC LIMIT 10", (unit_id,))]
        tickets = [dict(r) for r in c.execute("SELECT * FROM tickets WHERE unit_id=? ORDER BY id DESC LIMIT 10", (unit_id,))]
    fr = F.to_frame(df) if not df.empty else df
    s = eng.summarise(res, fr if not df.empty else None, usage_h=u["usage_h"], slopes=slopes, health_override=override)
    online = False
    if last_rx:
        online = (datetime.now() - datetime.fromisoformat(last_rx)).total_seconds() < ONLINE_S
    latest = None
    if not df.empty:
        lr = df.iloc[-1].to_dict()
        latest = {k: (None if isinstance(v, float) and np.isnan(v) else v) for k, v in lr.items() if k in schema.CSV_HEADER}
    return {"unit": u, "assessment": s, "online": online and not u["simulated"], "last_received": last_rx,
            "latest_reading": latest, "alerts": alerts, "tickets": tickets, "trend_used": bool(slopes)}


def fleet() -> list[dict]:
    with db.tx() as c:
        ids = [r["id"] for r in c.execute("SELECT id FROM units ORDER BY simulated, id")]
        open_alerts = {r["unit_id"]: r["n"] for r in c.execute(
            "SELECT unit_id, COUNT(*) AS n FROM alerts WHERE status='open' GROUP BY unit_id")}
    out = []
    for uid in ids:
        d = unit_summary(uid)
        a, u = d["assessment"], d["unit"]
        row = {"id": uid, "name": u["name"], "customer": u["customer"], "city": u["city"],
               "water_source": u["water_source"], "simulated": bool(u["simulated"]), "online": d["online"],
               "last_received": d["last_received"], "open_alerts": open_alerts.get(uid, 0)}
        if a.get("status") == "no_data":
            row.update(status="no_data", fault_name="Waiting for data", min_health=None)
        else:
            worst = min(a["components"], key=lambda x: x["health"])
            row.update(status=a["status"], fault=a["fault"], fault_name=a["fault_name"], confidence=a["confidence"],
                       min_health=worst["health"], worst_component=worst["name"], worst_rul=worst["rul"],
                       repair_price_now=a["quote"]["repair_price_now"], expected_cost=a["quote"]["expected_cost"],
                       health={x["component"]: x["health"] for x in a["components"]})
        out.append(row)
    return out


def readings(unit_id: str, minutes: float = 15) -> list[dict]:
    with db.tx() as c:
        newest = c.execute("SELECT MAX(ts) AS t FROM readings WHERE unit_id=?", (unit_id,)).fetchone()["t"]
        if not newest:
            return []
        since = (pd.Timestamp(newest) - pd.Timedelta(minutes=minutes)).isoformat()
        df = _load_readings(c, unit_id, since=since)
    return json.loads(df.to_json(orient="records"))


def history(unit_id: str, days: int = 120) -> dict:
    """Daily component health (observed or simulated) plus the RUL forecast."""
    with db.tx() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT day, component, health, simulated FROM daily_health WHERE unit_id=? ORDER BY day", (unit_id,))]
    df = pd.DataFrame(rows)
    out = {"series": {}, "simulated": bool(df["simulated"].max()) if not df.empty else False}
    if df.empty:
        return out
    df = df[df["day"] >= (pd.Timestamp(df["day"].max()) - pd.Timedelta(days=days)).strftime("%Y-%m-%d")]
    for comp, g in df.groupby("component"):
        out["series"][comp] = [{"day": r.day, "health": round(r.health, 1)} for r in g.itertuples()]
    return out


# ------------------------------------------------------------------ alerts / tickets
def list_alerts(status: str | None = None) -> list[dict]:
    q, a = "SELECT a.*, u.name AS unit_name, u.customer FROM alerts a LEFT JOIN units u ON u.id=a.unit_id", []
    if status:
        q += " WHERE a.status=?"
        a.append(status)
    with db.tx() as c:
        return [dict(r) for r in c.execute(q + " ORDER BY a.id DESC LIMIT 200", a)]


def set_alert_status(alert_id: int, status: str) -> bool:
    with db.tx() as c:
        return c.execute("UPDATE alerts SET status=? WHERE id=?", (status, alert_id)).rowcount > 0


def contact_customer(unit_id: str, alert_id: int | None = None, note: str = "") -> dict:
    """Scenario 1: create a service ticket and a job card that lists the likely
    fault and the part to carry."""
    d = unit_summary(unit_id)
    u, a = d["unit"], d["assessment"]
    if a.get("status") == "no_data":
        raise ValueError("No assessment yet for this unit")
    q = a["quote"]
    fault_comp = schema.CLASS_TO_COMPONENT.get({v: k for k, v in schema.CLASS_KEYS.items()}[a["fault"]])
    carry = [l["part"] for l in q["replace_now"]]
    if fault_comp and pricing.PARTS[fault_comp][0] not in carry:
        carry.append(pricing.PARTS[fault_comp][0])
    if not carry:
        carry = ["Standard service kit"]
    urgent = a["status"] == schema.STATUS_REPLACE
    window = "Within 48 hours" if urgent else "Within 7 days"
    card = job_card_text(u, a, carry, window, note)
    with db.tx() as c:
        dup = c.execute("SELECT id FROM tickets WHERE unit_id=? AND fault=? AND status IN ('open','scheduled') "
                        "ORDER BY id DESC LIMIT 1", (unit_id, a["fault_name"])).fetchone()
    if dup:  # one open job per unit and fault: pressing the button twice must not send two vans
        return get_ticket(dup["id"])
    with db.tx() as c:
        if alert_id is None:
            r = c.execute("SELECT id FROM alerts WHERE unit_id=? AND status='open' ORDER BY id DESC LIMIT 1", (unit_id,)).fetchone()
            alert_id = r["id"] if r else None
        cur = c.execute(
            "INSERT INTO tickets(unit_id, alert_id, fault, part_to_carry, price_estimate, job_card, visit_window, note) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (unit_id, alert_id, a["fault_name"], ", ".join(carry), q["repair_price_now"], card, window, note),
        )
        if alert_id:
            c.execute("UPDATE alerts SET status='ticketed' WHERE id=?", (alert_id,))
        tid = cur.lastrowid
    return get_ticket(tid)


def job_card_text(u: dict, a: dict, carry: list[str], window: str, note: str = "") -> str:
    L = []
    L.append("AQUASENSE SERVICE JOB CARD")
    L.append("=" * 42)
    L.append(f"Created      : {datetime.now():%Y-%m-%d %H:%M}")
    L.append(f"Unit         : {u['id']}  ({u['name']})")
    L.append(f"Customer     : {u['customer']}   {u['phone']}")
    L.append(f"Address      : {u['address']}, {u['city']}")
    L.append(f"Water source : {u['water_source']}  (TDS in ~{a.get('tds_in', 0):.0f} ppm)")
    L.append(f"Visit window : {window}")
    L.append("")
    L.append(f"LIKELY FAULT : {a['fault_name']}  (confidence {a['confidence']:.0%})")
    L.append(f"Why          : {a['reason']}")
    for e in a.get("evidence", [])[:4]:
        L.append(f"   - {e}")
    L.append("")
    L.append("PARTS TO CARRY")
    for p in carry:
        L.append(f"   [ ] {p}")
    L.append("")
    L.append("COMPONENT HEALTH")
    for comp in a["components"]:
        r = comp.get("rul")
        rul_s = f"RUL {r['low']}-{r['high']} days" if r and r.get("high") else ("replace now" if r else "")
        L.append(f"   {comp['name']:<16} {comp['health']:5.0f}%  {rul_s}")
    q = a["quote"]
    L.append("")
    L.append("ESTIMATE (dynamic)")
    for l in q["replace_now"]:
        L.append(f"   {l['part']:<26} {pricing.format_inr(l['cost']):>8}")
    if q["labour"]:
        L.append(f"   {'Labour':<26} {pricing.format_inr(q['labour']):>8}")
    L.append(f"   {'Visit charge':<26} {pricing.format_inr(q['visit_charge']):>8}")
    L.append(f"   {'TOTAL':<26} {pricing.format_inr(q['repair_price_now']):>8}")
    if q["watch_list"]:
        L.append("   Watch list (not billed): " + ", ".join(f"{l['part']} (p={l['failure_prob']:.2f})" for l in q["watch_list"]))
    if note:
        L.append("")
        L.append(f"Note: {note}")
    L.append("")
    L.append("RUL ranges use a model trained on simulated degradation.")
    return "\n".join(L)


def get_ticket(tid: int) -> dict:
    with db.tx() as c:
        r = c.execute("SELECT t.*, u.name AS unit_name, u.customer, u.phone, u.city FROM tickets t "
                      "LEFT JOIN units u ON u.id=t.unit_id WHERE t.id=?", (tid,)).fetchone()
    if not r:
        raise KeyError(tid)
    return dict(r)


def list_tickets(status: str | None = None) -> list[dict]:
    q, a = ("SELECT t.*, u.name AS unit_name, u.customer, u.phone, u.city FROM tickets t "
            "LEFT JOIN units u ON u.id=t.unit_id"), []
    if status:
        q += " WHERE t.status=?"
        a.append(status)
    with db.tx() as c:
        return [dict(r) for r in c.execute(q + " ORDER BY t.id DESC LIMIT 200", a)]


def set_ticket_status(tid: int, status: str) -> bool:
    with db.tx() as c:
        n = c.execute("UPDATE tickets SET status=? WHERE id=?", (status, tid)).rowcount
        if n and status == "done":
            r = c.execute("SELECT alert_id FROM tickets WHERE id=?", (tid,)).fetchone()
            if r and r["alert_id"]:
                c.execute("UPDATE alerts SET status='resolved' WHERE id=?", (r["alert_id"],))
    return n > 0


# ------------------------------------------------------------------ warranty
def warranty() -> dict:
    """Expected repair cost per unit and in total, plus a 30-day parts forecast
    (same pricing logic as the handheld)."""
    units, total = [], 0.0
    parts = {k: {"part": v[0], "unit_cost": v[1], "expected_units": 0.0, "likely_needed": 0, "expected_cost": 0.0}
             for k, v in pricing.PARTS.items()}
    for f in fleet():
        if f["status"] == "no_data":
            continue
        a = unit_summary(f["id"])["assessment"]
        for line in a["quote"]["lines"]:
            p = parts[line["component"]]
            p["expected_units"] += line["failure_prob"]
            p["expected_cost"] += line["expected_cost"]
            p["likely_needed"] += int(line["decision"] == "replace")
        total += a["quote"]["expected_cost"]
        units.append({"id": f["id"], "name": f["name"], "customer": f["customer"], "status": a["status"],
                      "repair_price_now": a["quote"]["repair_price_now"], "expected_cost": a["quote"]["expected_cost"],
                      "replace_now": [l["part"] for l in a["quote"]["replace_now"]],
                      "watch_list": [l["part"] for l in a["quote"]["watch_list"]]})
    for p in parts.values():
        p["expected_units"] = round(p["expected_units"], 2)
        p["expected_cost"] = round(p["expected_cost"])
    return {"units": units, "parts": list(parts.values()), "expected_cost_total": round(total),
            "repair_now_total": sum(u["repair_price_now"] for u in units),
            "prices": {"labour": pricing.LABOUR, "visit_charge": pricing.VISIT_CHARGE}}
