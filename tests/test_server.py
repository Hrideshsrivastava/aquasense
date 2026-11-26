"""Live-device server: ingest, baseline, quick check, alert -> job, report, repair, roles, WebSocket."""

import json
import os
import threading
import time

import pytest

os.environ["AQUASENSE_SERVER_DB"] = os.path.join(os.environ.get("TMPDIR", "/tmp"), f"aq_server_test_{os.getpid()}.db")

from server import db  # noqa: E402
from server.app import create_app  # noqa: E402
from server.core import Core  # noqa: E402
from server.device_sim import Esp32Model  # noqa: E402
from server.protocol import ble_packet_pack, ble_packet_unpack  # noqa: E402


@pytest.fixture()
def env():
    db.reset()
    core = Core()
    app = create_app(core)
    app.testing = True
    return core, app.test_client()


def login(c, user, pin):
    r = c.post("/api/login", json={"username": user, "pin": pin})
    assert r.status_code == 200, r.get_json()
    return {"Authorization": "Bearer " + r.get_json()["token"]}


def feed(c, m: Esp32Model, n: int):
    """Post n readings like the ESP32 and apply the returned overlay."""
    for _ in range(n):
        res = c.post("/sensor-data", json=m.read(2.0))
        assert res.status_code == 200
        m.overlay = res.get_json()["overlay"]


def test_ble_packet_roundtrip():
    m = Esp32Model("AQ-X", seed=3)
    r = m.read()
    p = ble_packet_pack(r, 7, 0b101)
    assert len(p) == 20
    u = ble_packet_unpack(p)
    assert u["seq"] == 7 and u["running"] and not u["wifi"] and u["server_ok"]
    assert abs(u["flow_rate_Lmin"] - r["flow_rate_Lmin"]) < 0.002
    assert abs(u["tds_out_ppm"] - r["tds_out_ppm"]) < 0.06
    with pytest.raises(ValueError):
        ble_packet_unpack(p[:-1] + bytes([p[-1] ^ 1]))


def test_full_flow_alert_job_report_repair(env):
    core, c = env
    m = Esp32Model("AQ-T01", seed=1, duty=(10_000, 1))  # always running
    feed(c, m, 35)
    admin = login(c, "admin", "1234")
    d = c.get("/api/devices/AQ-T01", headers=admin).get_json()
    assert d["baseline"] and d["status"] == "normal"
    # assign the new device to a customer
    kavya = next(u for u in c.get("/api/users?role=customer", headers=admin).get_json() if u["username"] == "kavya")
    c.patch("/api/devices/AQ-T01", json={"customer_id": kavya["id"], "name": "Kitchen RO"}, headers=admin)
    # trigger a fault from the dashboard: membrane wear (virtual TDS channel)
    ov = c.post("/api/devices/AQ-T01/overlay", json={"membrane": 2}, headers=admin).get_json()
    assert ov["membrane"] == 2 and ov["rev"] == 1
    feed(c, m, 60)
    alerts = c.get("/api/alerts", headers=admin).get_json()
    assert alerts and alerts[0]["fault"] == "membrane"
    jobs = c.get("/api/jobs", headers=admin).get_json()
    job = jobs[0]
    assert job["hypothesis"] == "membrane" and job["status"] == "assigned" and job["technician"]
    assert job["estimate"]["total"] == 1800 + 300 + 200
    tech_user = {"Ravi Kumar": ("ravi", "1111"), "Anita Desai": ("anita", "2222")}[job["technician"]["name"]]
    tech = login(c, *tech_user)
    assert [j["id"] for j in c.get("/api/jobs", headers=tech).get_json()] == [job["id"]]
    # the technician can now see the device; the customer sees it too
    assert c.get("/api/devices/AQ-T01", headers=tech).status_code == 200
    cust = login(c, "kavya", "3333")
    assert [x["id"] for x in c.get("/api/devices", headers=cust).get_json()] == ["AQ-T01"]
    assert c.post(f"/api/jobs/{job['id']}/report", json={"verdict": "confirmed"}, headers=cust).status_code == 403
    r = c.post(f"/api/jobs/{job['id']}/report", json={"verdict": "confirmed", "fault": "membrane", "confidence": 0.95}, headers=tech)
    assert r.get_json()["status"] == "confirmed"
    r = c.patch(f"/api/jobs/{job['id']}", json={"status": "resolved"}, headers=tech)
    assert r.get_json()["status"] == "resolved"
    # repair clears the fault on the device
    feed(c, m, 1)
    assert m.overlay["membrane"] == 0
    feed(c, m, 40)
    d = c.get("/api/devices/AQ-T01", headers=admin).get_json()
    assert d["status"] == "normal", d["hypothesis"]


@pytest.mark.parametrize("patch,fault", [({"clog": 2}, "clog"), ({"pump": 2}, "pump"), ({"turbidity": 2}, "turbidity"),
                                         ({"sensor": "tds_out_dead"}, "sensor"), ({"sensor": "flow_stuck"}, "sensor")])
def test_quick_check_faults(env, patch, fault):
    core, c = env
    m = Esp32Model("AQ-Q", seed=5, duty=(10_000, 1))
    feed(c, m, 35)
    admin = login(c, "admin", "1234")
    c.post("/api/devices/AQ-Q/overlay", json=patch, headers=admin)
    feed(c, m, 45)
    a = c.get("/api/alerts", headers=admin).get_json()
    assert a and a[0]["fault"] == fault, a


def test_high_tds_source_is_not_a_fault(env):
    core, c = env
    m = Esp32Model("AQ-H", seed=6, duty=(10_000, 1))
    feed(c, m, 35)
    admin = login(c, "admin", "1234")
    c.post("/api/devices/AQ-H/overlay", json={"tds_source": 1500}, headers=admin)
    feed(c, m, 45)
    assert c.get("/api/alerts", headers=admin).get_json() == []


def test_customer_booking_and_roles(env):
    core, c = env
    m = Esp32Model("AQ-B", seed=7)
    feed(c, m, 5)
    admin = login(c, "admin", "1234")
    meera = next(u for u in c.get("/api/users?role=customer", headers=admin).get_json() if u["username"] == "meera")
    c.patch("/api/devices/AQ-B", json={"customer_id": meera["id"]}, headers=admin)
    cust = login(c, "meera", "5555")
    other = login(c, "kavya", "3333")
    assert c.get("/api/devices/AQ-B", headers=other).status_code == 403
    j = c.post("/api/jobs", json={"device_id": "AQ-B", "note": "water tastes salty"}, headers=cust)
    assert j.status_code == 201 and j.get_json()["source"] == "customer"
    assert c.post("/api/jobs", json={"device_id": "AQ-B"}, headers=other).status_code == 403
    assert c.post("/api/devices/AQ-B/overlay", json={"clog": 1}, headers=cust).status_code == 403
    assert c.post("/api/login", json={"username": "admin", "pin": "0000"}).status_code == 401


def test_websocket_realtime(env):
    """A real server + WebSocket client receives the snapshot, readings and alerts live."""
    import websocket
    from werkzeug.serving import make_server

    core, c = env
    app = create_app(core)
    srv = make_server("127.0.0.1", 0, app, threaded=True)
    port = srv.server_port
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        tok = c.post("/api/login", json={"username": "admin", "pin": "1234"}).get_json()["token"]
        ws = websocket.create_connection(f"ws://127.0.0.1:{port}/ws?token={tok}", timeout=5)
        first = json.loads(ws.recv())
        assert first["type"] == "snapshot" and first["user"]["role"] == "admin"
        m = Esp32Model("AQ-WS", seed=9, duty=(10_000, 1))
        for _ in range(6):
            c.post("/sensor-data", json=m.read())
        types = set()
        t0 = time.time()
        while time.time() - t0 < 5 and not {"reading", "device"} <= types:
            types.add(json.loads(ws.recv())["type"])
        assert {"reading", "device"} <= types
        ws.close()
    finally:
        srv.shutdown()
