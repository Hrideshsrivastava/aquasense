"""AquaSense company server: HTTP API + WebSocket + dashboard.

    python -m server                 # http://<laptop-ip>:5000
    python -m server --port 5000 --reset

ESP32s POST to /sensor-data (the path in the firmware). The company dashboard
is served at /, the Android app uses /api/* and /ws.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import threading
import time
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory
from flask_sock import Sock

from . import db
from .core import Core
from .hub import Client

STATIC = Path(__file__).resolve().parent / "static"
DEVICE_KEY = os.environ.get("AQUASENSE_DEVICE_KEY", "")


def create_app(core: Core | None = None) -> Flask:
    app = Flask(__name__, static_folder=None)
    sock = Sock(app)
    app.config["SOCK_SERVER_OPTIONS"] = {"ping_interval": 20}
    core = core or Core()
    app.core = core  # type: ignore[attr-defined]

    def auth(roles: tuple = ("admin", "technician", "customer")) -> dict:
        tok = (request.headers.get("Authorization") or "").removeprefix("Bearer ").strip() or request.args.get("token")
        u = core.user_by_token(tok)
        if not u:
            raise PermissionError("Sign in required")
        if u["role"] not in roles:
            raise PermissionError("Not allowed for your role")
        return u

    def can_see(u: dict, did: str):
        if did not in core.dev:
            raise KeyError(did)
        if u["role"] != "admin" and did not in core.visible_devices(u):
            raise PermissionError("Not your device")

    @app.after_request
    def cors(resp):
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Device-Key"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, OPTIONS"
        return resp

    @app.errorhandler(PermissionError)
    def forbidden(e):
        return jsonify(error=str(e)), 401 if "Sign in" in str(e) or "Wrong" in str(e) else 403

    @app.errorhandler(KeyError)
    def not_found(e):
        return jsonify(error=f"not found: {e}"), 404

    @app.errorhandler(ValueError)
    def bad(e):
        return jsonify(error=str(e)), 400

    @app.before_request
    def preflight():
        if request.method == "OPTIONS":
            return Response(status=204)
        return None

    # ---------------------------------------------------------------- devices (ESP32)
    @app.post("/sensor-data")
    def sensor_data():
        if DEVICE_KEY and request.headers.get("X-Device-Key") != DEVICE_KEY:
            return jsonify(error="bad device key"), 401
        payload = request.get_json(force=True, silent=True)
        if not isinstance(payload, dict):
            raise ValueError("JSON body required")
        return jsonify(core.ingest(payload, ip=request.remote_addr or ""))

    # ---------------------------------------------------------------- auth
    @app.post("/api/login")
    def login():
        b = request.get_json(force=True, silent=True) or {}
        tok, user = core.login(b.get("username", ""), str(b.get("pin", "")))
        return jsonify(token=tok, user=user)

    @app.get("/api/me")
    def me():
        return jsonify(auth())

    @app.get("/api/health")
    def health():
        return jsonify(ok=True, devices=len(core.dev), clients=core.hub.count(), time=time.time())

    # ---------------------------------------------------------------- read
    @app.get("/api/devices")
    def devices():
        return jsonify(core.devices_for(auth()))

    @app.get("/api/devices/<did>")
    def device(did):
        u = auth()
        can_see(u, did)
        return jsonify(core.device_summary(did))

    @app.get("/api/devices/<did>/readings")
    def readings(did):
        u = auth()
        can_see(u, did)
        return jsonify(core.recent(did, float(request.args.get("minutes", 10))))

    @app.get("/api/jobs")
    def jobs():
        return jsonify(core.jobs(auth()))

    @app.get("/api/jobs/<int:jid>")
    def job(jid):
        u = auth()
        j = core.get_job(jid)
        can_see(u, j["device_id"])
        return jsonify(j)

    @app.get("/api/alerts")
    def alerts():
        return jsonify(core.alerts(auth()))

    @app.get("/api/users")
    def users():
        auth(("admin", "technician"))
        return jsonify(core.users(request.args.get("role")))

    # ---------------------------------------------------------------- write
    @app.patch("/api/devices/<did>")
    def patch_device(did):
        auth(("admin",))
        return jsonify(core.update_device(did, request.get_json(force=True) or {}))

    @app.post("/api/devices/<did>/overlay")
    def overlay(did):
        auth(("admin",))
        return jsonify(core.set_overlay(did, request.get_json(force=True) or {}))

    @app.post("/api/devices/<did>/baseline/reset")
    def baseline_reset(did):
        auth(("admin",))
        core.reset_baseline(did)
        return jsonify(core.device_summary(did))

    @app.post("/api/jobs")
    def new_job():
        u = auth()
        b = request.get_json(force=True) or {}
        did = b.get("device_id")
        can_see(u, did)
        src = "customer" if u["role"] == "customer" else "company"
        return jsonify(core.create_job(did, source=src, note=str(b.get("note", ""))[:500],
                                       technician_id=b.get("technician_id") if u["role"] == "admin" else None)), 201

    @app.patch("/api/jobs/<int:jid>")
    def patch_job(jid):
        return jsonify(core.update_job(jid, request.get_json(force=True) or {}, auth()))

    @app.post("/api/jobs/<int:jid>/report")
    def report(jid):
        return jsonify(core.submit_report(jid, request.get_json(force=True) or {}, auth(("admin", "technician"))))

    # ---------------------------------------------------------------- real time
    @sock.route("/ws")
    def ws(ws):
        u = core.user_by_token(request.args.get("token"))
        if not u:
            ws.send(json.dumps({"type": "error", "error": "Sign in required"}))
            return
        client = Client(user=u, devices=core.visible_devices(u))
        core.hub.add(client)
        try:
            ws.send(json.dumps(core.snapshot(u), default=str))
            while True:
                try:
                    msg = client.q.get(timeout=1.0)
                except Exception:
                    if not ws.connected:
                        break
                    continue
                ws.send(msg)
        except Exception:
            pass
        finally:
            core.hub.remove(client)

    # ---------------------------------------------------------------- dashboard
    @app.get("/")
    def index():
        return send_from_directory(STATIC, "index.html")

    @app.get("/<path:path>")
    def static_files(path):
        return send_from_directory(STATIC, path)

    return app


def lan_ips() -> list[str]:
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    return sorted(i for i in ips if not i.startswith("127."))


def background(core: Core, stop: threading.Event):
    n = 0
    while not stop.wait(5):
        core.tick_offline()
        n += 1
        if n % 720 == 0:  # hourly
            core.prune()


def main(argv=None):
    ap = argparse.ArgumentParser(description="AquaSense company server")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 5000)))
    ap.add_argument("--reset", action="store_true", help="delete all data and re-create the default users")
    a = ap.parse_args(argv)
    if a.reset:
        db.reset()
    core = Core()
    app = create_app(core)
    stop = threading.Event()
    threading.Thread(target=background, args=(core, stop), daemon=True).start()
    print("\nAquaSense server")
    print(f"  Dashboard:        http://localhost:{a.port}   (sign in: admin / 1234)")
    for ip in lan_ips():
        print(f"  On your Wi-Fi:    http://{ip}:{a.port}   <- ESP32 SERVER_URL = http://{ip}:{a.port}/sensor-data")
    print(f"  Database:         {db.DB_PATH}\n")
    app.run(host=a.host, port=a.port, threaded=True, debug=False)


if __name__ == "__main__":
    main()
