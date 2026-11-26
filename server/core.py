"""Server business logic: devices, readings, quick check, alerts, jobs, users.

Live state (latest readings, quick-check windows) is kept in memory for
speed; everything that matters is also written to SQLite.
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque

from . import db
from .hub import Hub
from .protocol import FAULTS, READING_FIELDS, default_overlay, validate_overlay_patch
from .quickcheck import COMPONENTS, FAULT_COMPONENT, FAULT_LABEL, DeviceModel

OFFLINE_AFTER_S = 15
RECENT = 900                 # readings kept in memory per device (30 min at 2 s)
KEEP_DAYS = 3
ACTIVE_JOB = ("new", "assigned", "en_route", "on_site", "confirmed")
JOB_STATUSES = ("new", "assigned", "en_route", "on_site", "confirmed", "no_fault", "resolved", "cancelled")

# Pricing used in job estimates (same table as the handheld / app)
PARTS = {"filter": ("Sediment/carbon filter", 400), "prefilter": ("Sediment pre-filter", 350),
         "membrane": ("RO membrane", 1800), "pump": ("Booster pump", 1500), "sensors": ("Sensor/electronics fix", 600)}
LABOUR, VISIT = 300, 200


class Core:
    def __init__(self, hub: Hub | None = None):
        self.hub = hub or Hub()
        self.lock = threading.RLock()
        self.dev: dict[str, dict] = {}  # id -> live state
        db.init()
        self._load()

    # ------------------------------------------------------------------ load / users
    def _load(self):
        with db.tx() as c:
            for row in c.execute("SELECT * FROM devices"):
                st = self._new_state(dict(row))
                if row["baseline_json"]:
                    st["model"].baseline = json.loads(row["baseline_json"])
                for r in c.execute("SELECT ts, data FROM readings WHERE device_id=? ORDER BY ts DESC LIMIT ?", (row["id"], RECENT)):
                    st["recent"].appendleft({"ts": r["ts"], **json.loads(r["data"])})
                if st["recent"]:
                    st["last_ts"] = st["recent"][-1]["ts"]
                    for r in list(st["recent"])[-30:]:
                        st["model"].window.append(r)

    def _new_state(self, row: dict) -> dict:
        st = {"row": row, "model": DeviceModel(), "recent": deque(maxlen=RECENT), "last_ts": 0.0, "hyp": None,
              "overlay": json.loads(row["overlay_json"]) if row.get("overlay_json") else default_overlay(),
              "applied_rev": None, "online": False}
        self.dev[row["id"]] = st
        return st

    def user_by_token(self, token: str | None) -> dict | None:
        if not token:
            return None
        with db.tx() as c:
            r = c.execute("SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=?", (token,)).fetchone()
        return self._user(r) if r else None

    @staticmethod
    def _user(r) -> dict:
        d = dict(r)
        d.pop("pin_hash", None)
        return d

    def login(self, username: str, pin: str) -> tuple[str, dict]:
        with db.tx() as c:
            r = c.execute("SELECT * FROM users WHERE username=?", ((username or "").strip().lower(),)).fetchone()
            if not r or r["pin_hash"] != db.hash_pin(pin):
                raise PermissionError("Wrong username or PIN")
            tok = db.new_token()
            c.execute("INSERT INTO sessions(token, user_id) VALUES(?,?)", (tok, r["id"]))
        return tok, self._user(r)

    def users(self, role: str | None = None) -> list[dict]:
        q, a = "SELECT * FROM users", []
        if role:
            q += " WHERE role=?"
            a.append(role)
        with db.tx() as c:
            return [self._user(r) for r in c.execute(q + " ORDER BY role, name", a)]

    def get_user(self, uid: int | None) -> dict | None:
        if uid is None:
            return None
        with db.tx() as c:
            r = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        return self._user(r) if r else None

    def visible_devices(self, user: dict) -> set[str]:
        if user["role"] == "admin":
            return set(self.dev)
        if user["role"] == "customer":
            return {d for d, st in self.dev.items() if st["row"].get("customer_id") == user["id"]}
        with db.tx() as c:  # technician: devices of their jobs
            return {r["device_id"] for r in c.execute("SELECT DISTINCT device_id FROM jobs WHERE technician_id=?", (user["id"],))}

    def refresh_client_scopes(self):
        for cl in self.hub.clients():
            cl.devices = self.visible_devices(cl.user)

    # ------------------------------------------------------------------ ingest
    def ingest(self, payload: dict, ip: str = "") -> dict:
        did = str(payload.get("device_id") or "").strip()
        if not did or len(did) > 40:
            raise ValueError("device_id is required")
        r = {k: float(payload.get(k) or 0.0) for k in READING_FIELDS}
        r["seq"] = int(payload.get("seq") or 0)
        now = time.time()
        with self.lock:
            st = self.dev.get(did)
            new_device = st is None
            if new_device:
                row = {"id": did, "name": payload.get("name") or "", "customer_id": None, "address": "", "fw": str(payload.get("fw", "")),
                       "ip": ip, "simulated": int(bool(payload.get("simulated"))), "overlay_json": None, "baseline_json": None}
                with db.tx() as c:
                    c.execute("INSERT INTO devices(id,name,fw,ip,simulated,last_seen) VALUES(?,?,?,?,?,datetime('now','localtime'))",
                              (did, row["name"], row["fw"], ip, row["simulated"]))
                st = self._new_state(row)
            was_online = st["online"]
            st["online"] = True
            st["last_ts"] = now
            st["applied_rev"] = payload.get("overlay_rev")
            st["row"].update(fw=str(payload.get("fw", st["row"].get("fw", ""))), ip=ip)
            reading = {"ts": now, **r}
            st["recent"].append(reading)
            ov = st["overlay"]
            overlay_active = any(ov.get(k) for k in FAULTS) or ov.get("sensor", "none") != "none"
            had_baseline = st["model"].baseline is not None
            hyp = st["model"].add(r, overlay_active)
            if not had_baseline and st["model"].baseline is not None:
                self._save_baseline(did)
        with db.tx() as c:
            c.execute("INSERT INTO readings(device_id, ts, data) VALUES(?,?,?)", (did, now, json.dumps(r)))
            c.execute("UPDATE devices SET last_seen=datetime('now','localtime'), fw=?, ip=? WHERE id=?", (st["row"]["fw"], ip, did))
        if new_device:
            self.refresh_client_scopes()
        self.hub.send({"type": "reading", "device_id": did, "reading": reading}, device_id=did)
        if hyp is not None:
            st["hyp"] = hyp
            if hyp["alert"]:
                self._raise_alert(did, hyp)
        if hyp is not None or new_device or not was_online:
            self.push_device(did)
        return {"ok": True, "overlay": ov, "interval_ms": 2000}

    def _save_baseline(self, did: str):
        b = self.dev[did]["model"].baseline
        with db.tx() as c:
            c.execute("UPDATE devices SET baseline_json=? WHERE id=?", (json.dumps(b) if b else None, did))

    def reset_baseline(self, did: str):
        with self.lock:
            self.dev[did]["model"].reset_baseline()
        self._save_baseline(did)
        self.push_device(did)

    def tick_offline(self):
        now = time.time()
        for did, st in list(self.dev.items()):
            if st["online"] and now - st["last_ts"] > OFFLINE_AFTER_S:
                st["online"] = False
                self.push_device(did)

    def prune(self):
        with db.tx() as c:
            c.execute("DELETE FROM readings WHERE ts < ?", (time.time() - KEEP_DAYS * 86400,))

    # ------------------------------------------------------------------ device views
    def device_summary(self, did: str) -> dict:
        st = self.dev[did]
        row = st["row"]
        cust = self.get_user(row.get("customer_id"))
        job = self.active_job(did)
        hyp = st["hyp"] or {}
        latest = st["recent"][-1] if st["recent"] else None
        if not st["online"]:
            status = "offline"
        elif job:
            status = "service"
        elif self.open_alert(did):
            status = "alert"
        elif not st["model"].baseline:
            status = "learning"
        elif hyp.get("fault") == "idle":
            status = "idle"
        elif hyp.get("fault") not in (None, "normal") and hyp.get("score", 0) >= 0.5:
            status = "suspect"
        else:
            status = "normal"
        return {
            "id": did, "name": row.get("name") or did, "address": row.get("address") or (cust or {}).get("area", ""),
            "customer": {k: cust[k] for k in ("id", "name", "phone", "area")} if cust else None,
            "simulated": bool(row.get("simulated")), "fw": row.get("fw"), "ip": row.get("ip"),
            "online": st["online"], "last_seen": st["last_ts"], "status": status,
            "hypothesis": {k: hyp.get(k) for k in ("fault", "label", "score", "evidence", "streak", "signals")} if hyp else None,
            "health": hyp.get("health") or {k: 100.0 for k in COMPONENTS},
            "baseline": st["model"].baseline, "baseline_progress": len(st["model"]._base_acc),
            "latest": latest, "overlay": st["overlay"], "overlay_applied": st["applied_rev"] == st["overlay"]["rev"],
            "job": job,
        }

    def push_device(self, did: str):
        self.hub.send({"type": "device", "device": self.device_summary(did)}, device_id=did)

    def devices_for(self, user: dict) -> list[dict]:
        vis = self.visible_devices(user)
        return [self.device_summary(d) for d in sorted(vis)]

    def recent(self, did: str, minutes: float = 10) -> list[dict]:
        st = self.dev[did]
        cut = time.time() - minutes * 60
        return [r for r in st["recent"] if r["ts"] >= cut]

    def update_device(self, did: str, patch: dict) -> dict:
        if did not in self.dev:
            raise KeyError(did)
        fields = {k: patch[k] for k in ("name", "address", "customer_id") if k in patch}
        if "customer_id" in fields and fields["customer_id"] not in (None, ""):
            u = self.get_user(int(fields["customer_id"]))
            if not u or u["role"] != "customer":
                raise ValueError("customer_id must be a customer")
            fields["customer_id"] = u["id"]
        if fields:
            with db.tx() as c:
                c.execute(f"UPDATE devices SET {', '.join(k + '=?' for k in fields)} WHERE id=?", (*fields.values(), did))
            self.dev[did]["row"].update(fields)
        self.refresh_client_scopes()
        self.push_device(did)
        return self.device_summary(did)

    def set_overlay(self, did: str, patch: dict) -> dict:
        if did not in self.dev:
            raise KeyError(did)
        clean = validate_overlay_patch(patch)
        with self.lock:
            ov = dict(self.dev[did]["overlay"])
            ov.update(clean)
            ov["rev"] = int(ov.get("rev", 0)) + 1
            self.dev[did]["overlay"] = ov
        with db.tx() as c:
            c.execute("UPDATE devices SET overlay_json=? WHERE id=?", (json.dumps(ov), did))
        self.push_device(did)
        return ov

    # ------------------------------------------------------------------ alerts
    def open_alert(self, did: str) -> dict | None:
        with db.tx() as c:
            r = c.execute("SELECT * FROM alerts WHERE device_id=? AND status='open' ORDER BY id DESC LIMIT 1", (did,)).fetchone()
        return self._alert(r) if r else None

    @staticmethod
    def _alert(r) -> dict:
        d = dict(r)
        d["evidence"] = json.loads(d["evidence"] or "[]")
        return d

    def alerts(self, user: dict, limit: int = 100) -> list[dict]:
        vis = self.visible_devices(user)
        with db.tx() as c:
            rows = [self._alert(r) for r in c.execute("SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (limit,))]
        return [a for a in rows if a["device_id"] in vis]

    def _raise_alert(self, did: str, hyp: dict):
        if self.open_alert(did) or self.active_job(did):
            return
        with db.tx() as c:
            cur = c.execute("INSERT INTO alerts(device_id,fault,label,score,evidence,created_at) VALUES(?,?,?,?,?,?)",
                            (did, hyp["fault"], hyp["label"], hyp["score"], json.dumps(hyp["evidence"]), time.time()))
            aid = cur.lastrowid
            a = self._alert(c.execute("SELECT * FROM alerts WHERE id=?", (aid,)).fetchone())
        self.hub.send({"type": "alert", "alert": a, "device_name": self.device_summary(did)["name"]}, device_id=did)
        # the company's automatic response: open a job and assign a technician
        self.create_job(did, source="quick_check", alert=a)

    # ------------------------------------------------------------------ jobs
    def _job(self, r) -> dict:
        d = dict(r)
        d["report"] = json.loads(d.pop("report_json") or "null")
        tech = self.get_user(d["technician_id"])
        d["technician"] = {k: tech[k] for k in ("id", "name", "phone")} if tech else None
        st = self.dev.get(d["device_id"])
        if st:
            row = st["row"]
            cust = self.get_user(row.get("customer_id"))
            d["device"] = {"id": d["device_id"], "name": row.get("name") or d["device_id"],
                           "address": row.get("address") or (cust or {}).get("area", ""),
                           "customer": {k: cust[k] for k in ("id", "name", "phone")} if cust else None,
                           "baseline": st["model"].baseline}
        comp = FAULT_COMPONENT.get(d.get("hypothesis") or "")
        if comp:
            part, cost = PARTS[comp]
            d["estimate"] = {"part": part, "parts": cost, "labour": LABOUR, "visit": VISIT, "total": cost + LABOUR + VISIT}
        return d

    def active_job(self, did: str) -> dict | None:
        with db.tx() as c:
            r = c.execute(f"SELECT * FROM jobs WHERE device_id=? AND status IN ({','.join('?' * len(ACTIVE_JOB))}) ORDER BY id DESC LIMIT 1",
                          (did, *ACTIVE_JOB)).fetchone()
        return self._job(r) if r else None

    def get_job(self, jid: int) -> dict:
        with db.tx() as c:
            r = c.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
        if not r:
            raise KeyError(jid)
        return self._job(r)

    def jobs(self, user: dict, limit: int = 200) -> list[dict]:
        q, a = "SELECT * FROM jobs", []
        if user["role"] == "technician":
            q += " WHERE technician_id=?"
            a.append(user["id"])
        with db.tx() as c:
            rows = [self._job(r) for r in c.execute(q + " ORDER BY id DESC LIMIT ?", (*a, limit))]
        if user["role"] == "customer":
            vis = self.visible_devices(user)
            rows = [j for j in rows if j["device_id"] in vis]
        return rows

    def pick_technician(self) -> dict | None:
        techs = self.users("technician")
        if not techs:
            return None
        with db.tx() as c:
            load = {r["technician_id"]: r["n"] for r in c.execute(
                f"SELECT technician_id, COUNT(*) AS n FROM jobs WHERE status IN ({','.join('?' * len(ACTIVE_JOB))}) GROUP BY technician_id", ACTIVE_JOB)}
        return min(techs, key=lambda t: (load.get(t["id"], 0), t["id"]))

    def create_job(self, did: str, source: str, alert: dict | None = None, note: str = "", technician_id: int | None = None) -> dict:
        if did not in self.dev:
            raise KeyError(did)
        existing = self.active_job(did)
        if existing:
            return existing
        hyp = alert or (self.dev[did]["hyp"] or {})
        fault = hyp.get("fault") if hyp.get("fault") not in (None, "normal", "idle") else None
        tech = self.get_user(technician_id) if technician_id else self.pick_technician()
        now = time.time()
        with db.tx() as c:
            cur = c.execute("INSERT INTO jobs(device_id,alert_id,source,hypothesis,hypothesis_label,technician_id,status,note,created_at,updated_at) "
                            "VALUES(?,?,?,?,?,?,?,?,?,?)",
                            (did, (alert or {}).get("id"), source, fault, FAULT_LABEL.get(fault, "Service request") if fault else "Service request",
                             tech["id"] if tech else None, "assigned" if tech else "new", note, now, now))
            jid = cur.lastrowid
        job = self.get_job(jid)
        self.refresh_client_scopes()
        self._push_job(job, created=True)
        self.push_device(did)
        return job

    def _push_job(self, job: dict, created: bool = False):
        did = job["device_id"]
        self.hub.send({"type": "job", "job": job}, device_id=did)
        if created and job.get("technician"):
            self.hub.send({"type": "notify", "title": f"New job #{job['id']}: {job['hypothesis_label']}",
                           "body": f"{job['device']['name']} · {job['device']['address']}", "job_id": job["id"]},
                          user_ids={job["technician"]["id"]})

    def update_job(self, jid: int, patch: dict, user: dict) -> dict:
        job = self.get_job(jid)
        if user["role"] == "technician" and job["technician_id"] != user["id"]:
            raise PermissionError("Not your job")
        if user["role"] == "customer":
            raise PermissionError("Customers cannot change jobs")
        sets = {}
        if "status" in patch:
            if patch["status"] not in JOB_STATUSES:
                raise ValueError(f"status must be one of {JOB_STATUSES}")
            sets["status"] = patch["status"]
        if "technician_id" in patch and user["role"] == "admin":
            t = self.get_user(int(patch["technician_id"]))
            if not t or t["role"] != "technician":
                raise ValueError("technician_id must be a technician")
            sets["technician_id"] = t["id"]
            if job["status"] == "new":
                sets["status"] = "assigned"
        if "note" in patch:
            sets["note"] = str(patch["note"])[:500]
        if not sets:
            return job
        sets["updated_at"] = time.time()
        with db.tx() as c:
            c.execute(f"UPDATE jobs SET {', '.join(k + '=?' for k in sets)} WHERE id=?", (*sets.values(), jid))
            if sets.get("status") in ("resolved", "no_fault", "cancelled") and job.get("alert_id"):
                c.execute("UPDATE alerts SET status=? WHERE id=?", ("resolved" if sets["status"] == "resolved" else "dismissed", job["alert_id"]))
        if sets.get("status") == "resolved" and patch.get("repair", True):
            self._repair(job)
        job = self.get_job(jid)
        self.refresh_client_scopes()
        self._push_job(job, created="technician_id" in sets)
        self.push_device(job["device_id"])
        return job

    def _repair(self, job: dict):
        """A resolved job means the part was replaced: clear the matching fault."""
        did = job["device_id"]
        fault = (job.get("report") or {}).get("fault") or job.get("hypothesis")
        patch = {}
        if fault in FAULTS:
            patch[fault] = 0
        if fault == "sensor":
            patch["sensor"] = "none"
        if patch:
            self.set_overlay(did, patch)
        st = self.dev[did]
        st["model"].streak, st["model"].streak_fault = 0, None
        st["model"].window.clear()  # start the quick check afresh after the repair
        st["hyp"] = None

    def submit_report(self, jid: int, report: dict, user: dict) -> dict:
        """The technician's on-site result from the full diagnosis on the phone."""
        job = self.get_job(jid)
        if user["role"] != "admin" and job["technician_id"] != user["id"]:
            raise PermissionError("Not your job")
        verdict = report.get("verdict")
        if verdict not in ("confirmed", "different", "no_fault"):
            raise ValueError("verdict must be confirmed, different or no_fault")
        status = "no_fault" if verdict == "no_fault" else "confirmed"
        with db.tx() as c:
            c.execute("UPDATE jobs SET report_json=?, status=?, updated_at=? WHERE id=?", (json.dumps(report), status, time.time(), jid))
            if job.get("alert_id"):
                c.execute("UPDATE alerts SET status=? WHERE id=?", ("confirmed" if verdict != "no_fault" else "dismissed", job["alert_id"]))
        job = self.get_job(jid)
        self._push_job(job)
        self.push_device(job["device_id"])
        return job

    def snapshot(self, user: dict) -> dict:
        return {"type": "snapshot", "user": user, "devices": self.devices_for(user), "jobs": self.jobs(user),
                "alerts": self.alerts(user), "technicians": self.users("technician"),
                "customers": self.users("customer") if user["role"] == "admin" else []}
