"""Software ESP32s that talk to the server exactly like the firmware.

Use them to add more purifiers to the fleet next to your real ESP32, or to run
everything without hardware:

    python -m server.device_sim --count 4                     # AQ-SIM01..AQ-SIM04
    python -m server.device_sim --server http://192.168.1.20:5000 --count 2 --ble-print

Each simulated device produces the three real channels (flow, pump current,
turbidity) with noise and a household usage pattern, fabricates the virtual
channels (TDS in/out, pressure, temperature) and applies the fault overlay the
server returns -- the same maths as firmware/aquasense_esp32_v2.
"""

from __future__ import annotations

import argparse
import random
import threading
import time

from .protocol import ble_packet_pack, default_overlay


class Esp32Model:
    """Mirror of the firmware's readSensors() + applyOverlay() + fabricate()."""

    def __init__(self, device_id: str, seed: int = 0, base_flow=1.20, base_current=0.90, base_turb=2.90, duty=(90, 30)):
        self.id = device_id
        self.rng = random.Random(seed)
        self.base = {"flow": base_flow * self.rng.uniform(0.92, 1.08), "current": base_current * self.rng.uniform(0.94, 1.06),
                     "turb": base_turb * self.rng.uniform(0.97, 1.03)}
        self.overlay = default_overlay()
        self.total_volume = self.rng.uniform(800, 3000)
        self.t = self.rng.uniform(0, sum(duty))
        self.duty = duty
        self.temp = 26 + self.rng.uniform(-1.5, 1.5)
        self.frozen_p = None
        self.seq = 0

    def running(self) -> bool:
        on, off = self.duty
        return (self.t % (on + off)) < on

    def read(self, dt: float = 2.0) -> dict:
        g = self.rng.gauss
        self.t += dt
        on = self.running()
        o = self.overlay
        s = {k: o.get(k, 0) / 3 for k in ("clog", "pump", "membrane", "turbidity")}
        # ---- real channels (what the sensors would measure)
        flow = max(0.0, self.base["flow"] * (1 + g(0, 0.03))) if on else max(0.0, g(0, 0.004))
        current = max(0.0, self.base["current"] * (1 + g(0, 0.025))) if on else abs(g(0.01, 0.01))
        turb = self.base["turb"] * (1 + g(0, 0.01))
        # ---- fault overlay on real channels
        flow *= (1 - 0.7 * s["clog"]) * (1 - 0.45 * s["pump"])
        current *= (1 + 0.15 * s["clog"]) * (1 - 0.35 * s["pump"])
        turb *= 1 - 0.35 * s["turbidity"]
        # ---- virtual channels
        running = current > 0.15 or flow > 0.05
        tds_in = o.get("tds_source", 300) * (1 + g(0, 0.01))
        rej = 0.955 - 0.45 * s["membrane"]
        tds_out = tds_in * (1 - rej) * (1 + g(0, 0.02)) * (1.0 if running else 1.05)
        pressure = 5.5 * (1 + 0.45 * s["clog"]) * (1 - 0.55 * s["pump"]) * (1 + g(0, 0.012)) if running else max(0.1, 0.2 + g(0, 0.02))
        self.temp += g(0, 0.02)
        # ---- sensor faults
        mode = o.get("sensor", "none")
        if mode == "flow_stuck":
            flow = 0.0
        elif mode == "tds_out_dead":
            tds_out = abs(g(0.4, 0.3))
        elif mode == "turbidity_disconnected":
            turb = abs(g(0.01, 0.005))
        if mode == "pressure_frozen":
            self.frozen_p = self.frozen_p if self.frozen_p is not None else pressure
            pressure = self.frozen_p
        else:
            self.frozen_p = None
        self.total_volume += flow * dt / 60
        self.seq += 1
        return {
            "device_id": self.id, "fw": "2.0.0-sim", "seq": self.seq, "simulated": True, "overlay_rev": o.get("rev", 0),
            "flow_rate_Lmin": round(flow, 3), "total_volume_L": round(self.total_volume, 3), "pump_current_A": round(current, 3),
            "current_ADC": int(max(0, min(4095, (2.5 + current * 0.185) * 0.6 / 3.3 * 4095))),
            "turbidity_ADC": int(max(0, min(4095, turb / 3.3 * 4095))), "turbidity_voltage_V": round(turb, 3),
            "tds_in_ppm": round(tds_in, 1), "tds_out_ppm": round(tds_out, 1), "pressure_bar": round(pressure, 3),
            "water_temp_C": round(self.temp, 2), "running": int(running),
        }


def run_device(m: Esp32Model, server: str, interval: float, stop: threading.Event, ble_print: bool = False):
    import requests

    url = server.rstrip("/") + "/sensor-data"
    while not stop.is_set():
        r = m.read(interval)
        try:
            resp = requests.post(url, json=r, timeout=5)
            if resp.ok:
                ov = resp.json().get("overlay")
                if ov:
                    m.overlay = ov
        except Exception as e:  # keep going like the firmware does
            print(f"[{m.id}] server unreachable: {type(e).__name__}")
        if ble_print:
            print(f"[{m.id}] BLE packet {ble_packet_pack(r, r['seq'], 1 | 2 | 4).hex()}")
        stop.wait(interval)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--server", default="http://localhost:5000")
    ap.add_argument("--count", type=int, default=4)
    ap.add_argument("--prefix", default="AQ-SIM")
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--ble-print", action="store_true", help="print the 20-byte BLE packet each device would notify")
    a = ap.parse_args(argv)
    stop = threading.Event()
    threads = []
    for k in range(a.count):
        m = Esp32Model(f"{a.prefix}{k + 1:02d}", seed=k + 1)
        th = threading.Thread(target=run_device, args=(m, a.server, a.interval, stop, a.ble_print), daemon=True)
        th.start()
        threads.append(th)
        time.sleep(a.interval / max(a.count, 1))  # spread the posts
    print(f"{a.count} simulated devices posting to {a.server}/sensor-data every {a.interval:g} s. Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        stop.set()


if __name__ == "__main__":
    main()
