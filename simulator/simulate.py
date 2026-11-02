"""ESP32-style purifier simulator. Sends telemetry over HTTP, like the firmware would.
Usage: python simulator/simulate.py --id P-xxxx --key KEY [--fault pump|membrane|clog] [--interval 5]"""
import argparse, random, time, httpx

def reading(step, fault):
    t = dict(tds_in=620, tds_out=28, flow=1.2, pressure=4.6, current=1.0, build=44, uv=2500 + step * 2, temp=28)
    drift = min(1.0, step / 60)
    if fault == "pump": t.update(pressure=4.6 - 2.0 * drift, current=1.0 + 0.6 * drift, build=44 + 65 * drift)
    if fault == "membrane": t["tds_out"] = 28 + 130 * drift
    if fault == "clog": t.update(flow=1.2 - 0.6 * drift, pressure=4.6 + 1.7 * drift)
    return {k: round(v * random.uniform(0.98, 1.02), 2) for k, v in t.items()}

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://localhost:8000"); p.add_argument("--id", required=True)
    p.add_argument("--key", required=True); p.add_argument("--fault", choices=["pump", "membrane", "clog"])
    p.add_argument("--interval", type=float, default=5); p.add_argument("--count", type=int, default=0)
    a = p.parse_args(); step = 0
    while a.count == 0 or step < a.count:
        r = httpx.post(f"{a.url}/api/telemetry", json=reading(step, a.fault), headers={"X-Device-Id": a.id, "X-Device-Key": a.key})
        print(step, r.status_code); step += 1; time.sleep(a.interval)
