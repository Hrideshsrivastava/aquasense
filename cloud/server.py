"""AquaSense cloud server (Unit C): Flask + SQLite + the ML engine.

    python -m cloud.server                 # listens on 0.0.0.0:5000

The ESP32 POSTs to /ingest. The Streamlit console and the mobile app use the
/api/* JSON endpoints. On a phone hotspot, give the laptop a fixed IP and put
http://<laptop-ip>:5000/ingest in firmware/aquasense_rig/config.h.

Set AQUASENSE_API_KEY to require the header `X-Api-Key` on /ingest.
"""

from __future__ import annotations

import argparse
import os

from flask import Flask, Response, jsonify, request

from aquasense.engine import ModelsMissing, get_engine

from . import db, service

API_KEY = os.environ.get("AQUASENSE_API_KEY", "")

app = Flask(__name__)


@app.after_request
def cors(resp):
    # Prototype on a private hotspot: allow the console/mobile app (web build) to call us.
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Api-Key, X-Device-Id"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, OPTIONS"
    return resp


@app.errorhandler(KeyError)
def not_found(e):
    return jsonify(error=f"not found: {e}"), 404


@app.errorhandler(ValueError)
def bad_request(e):
    return jsonify(error=str(e)), 400


@app.route("/", methods=["GET"])
def root():
    return jsonify(service="AquaSense cloud", endpoints=[
        "POST /ingest", "GET /api/health", "GET /api/units", "GET /api/units/<id>", "GET /api/units/<id>/readings",
        "GET /api/units/<id>/history", "POST /api/units/<id>/contact", "GET /api/alerts", "POST /api/alerts/<id>/ack",
        "GET /api/tickets", "PATCH /api/tickets/<id>", "GET /api/tickets/<id>/jobcard", "GET /api/warranty"])


@app.route("/api/health")
def health():
    eng = get_engine()
    return jsonify(ok=True, model=eng.card)


@app.route("/ingest", methods=["POST", "OPTIONS"])
def ingest():
    if request.method == "OPTIONS":
        return "", 204
    if API_KEY and request.headers.get("X-Api-Key") != API_KEY:
        return jsonify(error="bad api key"), 401
    rows = service.parse_payload(request.get_data(), request.content_type or "", request.headers.get("X-Device-Id"))
    if not rows:
        return jsonify(error="no rows"), 400
    res = service.ingest(rows)
    return jsonify(ok=True, **res)


@app.route("/api/units")
def units():
    return jsonify(service.fleet())


@app.route("/api/units", methods=["POST"])
def add_unit():
    body = request.get_json(force=True)
    if not body.get("id"):
        raise ValueError("id is required")
    service.upsert_unit(body)
    return jsonify(service.get_unit(body["id"])), 201


@app.route("/api/units/<unit_id>")
def unit(unit_id):
    return jsonify(service.unit_summary(unit_id))


@app.route("/api/units/<unit_id>/readings")
def unit_readings(unit_id):
    return jsonify(service.readings(unit_id, float(request.args.get("minutes", 15))))


@app.route("/api/units/<unit_id>/history")
def unit_history(unit_id):
    return jsonify(service.history(unit_id, int(request.args.get("days", 120))))


@app.route("/api/units/<unit_id>/contact", methods=["POST", "OPTIONS"])
def contact(unit_id):
    if request.method == "OPTIONS":
        return "", 204
    body = request.get_json(silent=True) or {}
    t = service.contact_customer(unit_id, body.get("alert_id"), body.get("note", ""))
    return jsonify(t), 201


@app.route("/api/alerts")
def alerts():
    return jsonify(service.list_alerts(request.args.get("status")))


@app.route("/api/alerts/<int:alert_id>/ack", methods=["POST", "OPTIONS"])
def ack(alert_id):
    if request.method == "OPTIONS":
        return "", 204
    if not service.set_alert_status(alert_id, "acknowledged"):
        raise KeyError(alert_id)
    return jsonify(ok=True)


@app.route("/api/tickets")
def tickets():
    return jsonify(service.list_tickets(request.args.get("status")))


@app.route("/api/tickets/<int:tid>", methods=["GET", "PATCH", "OPTIONS"])
def ticket(tid):
    if request.method == "OPTIONS":
        return "", 204
    if request.method == "PATCH":
        status = (request.get_json(force=True) or {}).get("status")
        if status not in ("open", "scheduled", "done"):
            raise ValueError("status must be open, scheduled or done")
        if not service.set_ticket_status(tid, status):
            raise KeyError(tid)
    return jsonify(service.get_ticket(tid))


@app.route("/api/tickets/<int:tid>/jobcard")
def jobcard(tid):
    t = service.get_ticket(tid)
    return Response(t["job_card"], mimetype="text/plain",
                    headers={"Content-Disposition": f"attachment; filename=jobcard_{tid}.txt"})


@app.route("/api/warranty")
def warranty():
    return jsonify(service.warranty())


def main(argv=None):
    ap = argparse.ArgumentParser(description="AquaSense cloud server")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 5000)))
    ap.add_argument("--debug", action="store_true")
    a = ap.parse_args(argv)
    db.init()
    try:
        get_engine()
    except ModelsMissing as e:
        raise SystemExit(str(e))
    print(f"AquaSense cloud on http://{a.host}:{a.port}  (database {db.DB_PATH})")
    app.run(host=a.host, port=a.port, debug=a.debug, threaded=True)


db.init()

if __name__ == "__main__":
    main()
