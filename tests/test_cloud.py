import json

import numpy as np
import pytest

from aquasense.rigsim import FaultState, RigCalibration, VirtualRig
from cloud import db, service
from cloud.server import app


def csv_lines(rows):
    from aquasense import schema
    return "\n".join(",".join(str(r[k]) for k in schema.CSV_HEADER) for r in rows)


@pytest.fixture()
def client():
    db.init()
    db.reset()
    app.testing = True
    return app.test_client()


def make_rows(faults, seconds, device="AQ-T-1", seed=4):
    rig = VirtualRig(device_id=device, faults=faults, seed=seed, calib=RigCalibration.random(np.random.default_rng(seed)))
    rig.run(150)
    return rig.run(seconds)


def test_parse_payload_formats():
    rows = make_rows(FaultState(), 30)
    from aquasense import schema
    with_header = ",".join(schema.CSV_HEADER) + "\n" + csv_lines(rows)
    assert len(service.parse_payload(with_header.encode())) == len(rows)
    assert len(service.parse_payload(csv_lines(rows).encode())) == len(rows)
    assert len(service.parse_payload(json.dumps({"rows": rows}).encode(), "application/json")) == len(rows)
    assert len(service.parse_payload(json.dumps(rows[0]).encode(), "application/json")) == 1


def test_scenario1_alert_and_job_card(client):
    """Clog the line: after 3 confident windows an alert appears; Contact
    customer creates a ticket whose job card names the part to carry."""
    rows = make_rows(FaultState(), 300)
    rows += make_rows(FaultState(clog=2 / 3), 300, seed=5)
    # keep timestamps continuous: re-time the second half after the first
    from datetime import datetime, timedelta
    t0 = datetime.fromisoformat(rows[0]["timestamp"])
    for i, r in enumerate(rows):
        r["timestamp"] = (t0 + timedelta(seconds=5 * i)).isoformat(timespec="seconds")
    for k in range(0, len(rows), 2):  # like the ESP32: 2 lines every 10 s
        res = client.post("/ingest", data=csv_lines(rows[k:k + 2]), content_type="text/csv")
        assert res.status_code == 200
    alerts = client.get("/api/alerts").get_json()
    assert alerts and alerts[0]["fault"] == "clogging"
    d = client.get("/api/units/AQ-T-1").get_json()
    assert d["assessment"]["fault"] == "clogging"
    t = client.post("/api/units/AQ-T-1/contact", json={}).get_json()
    assert "Sediment/carbon filter" in t["part_to_carry"]
    assert "JOB CARD" in t["job_card"] and t["price_estimate"] >= 900
    assert client.get("/api/alerts").get_json()[0]["status"] == "ticketed"
    again = client.post("/api/units/AQ-T-1/contact", json={}).get_json()
    assert again["id"] == t["id"]  # no duplicate job for the same open fault
    assert client.patch(f"/api/tickets/{t['id']}", json={"status": "done"}).status_code == 200
    w = client.get("/api/warranty").get_json()
    assert w["expected_cost_total"] > 0


def test_duplicates_ignored_and_backlog_scored(client):
    rows = make_rows(FaultState(), 600)
    r1 = client.post("/ingest", data=csv_lines(rows), content_type="text/csv").get_json()
    r2 = client.post("/ingest", data=csv_lines(rows), content_type="text/csv").get_json()
    assert r1["stored"] == len(rows) and r2["duplicates"] == len(rows)
    assert r1["assessed"] > 10  # the whole backlog is scored in time order, not just the last window
    assert client.get("/api/units").get_json()[0]["status"] == "normal"


def test_api_key(client, monkeypatch):
    import cloud.server as srv
    monkeypatch.setattr(srv, "API_KEY", "secret")
    rows = make_rows(FaultState(), 20)
    assert client.post("/ingest", data=csv_lines(rows), content_type="text/csv").status_code == 401
    assert client.post("/ingest", data=csv_lines(rows), content_type="text/csv",
                       headers={"X-Api-Key": "secret"}).status_code == 200
