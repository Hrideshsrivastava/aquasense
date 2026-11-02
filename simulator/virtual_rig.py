"""Virtual RO rig: a software stand-in for Unit A (ESP32 + sensors).

It behaves like the firmware: 90 s ON / 30 s OFF pump cycle, one CSV per day
on a (virtual) SD card, a log line every 5 s (2 s in training mode), and every
10 s it sends all unsent lines to the cloud. If the server is unreachable or
Wi-Fi is "off", it keeps logging and remembers the last line sent in
sync.txt, then sends the backlog in batches when the link returns.

Use it to rehearse the demo before the rig is plumbed, or as the backup if
the rig breaks on the day.

    python -m simulator.virtual_rig --panel                 # with a fault-injection panel (Tk)
    python -m simulator.virtual_rig                          # headless; type commands on stdin
    python -m simulator.virtual_rig --fault pump --level 2 --offline --prefill 10

Headless commands: normal | clog N | pump N | membrane N | sensor unplug|pfreeze|tfreeze |
                   tds PPM | wifi on|off | label N|off | status | quit
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from aquasense import schema
from aquasense.rigsim import Counters, FaultState, RigCalibration, VirtualRig

ROOT = Path(__file__).resolve().parent.parent
SENSOR_ALIASES = {"unplug": "tds_out_unplugged", "pfreeze": "pressure_frozen", "tfreeze": "tds_out_frozen",
                  "none": None, "off": None}


class SDCard:
    """Folder that mimics the ESP32's SD card layout."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.sync_file = self.root / "sync.txt"

    def append(self, row: dict):
        path = self.root / schema.csv_filename(row["timestamp"][:10])
        new = not path.exists()
        with path.open("a", newline="") as f:  # open/append/close = flush after every line
            w = csv.DictWriter(f, fieldnames=schema.CSV_HEADER, extrasaction="ignore")
            if new:
                w.writeheader()
            w.writerow(row)

    def files(self) -> list[Path]:
        return sorted(p for p in self.root.glob("????-??-??.csv"))

    def sync_state(self) -> tuple[str, int]:
        try:
            name, off = self.sync_file.read_text().strip().split(",")
            return name, int(off)
        except Exception:
            files = self.files()
            return (files[0].name if files else ""), 0

    def save_sync(self, name: str, offset: int):
        tmp = self.sync_file.with_suffix(".tmp")
        tmp.write_text(f"{name},{offset}\n")
        os.replace(tmp, self.sync_file)

    def unsent(self, max_lines: int = 200) -> tuple[list[str], str, int]:
        """Next batch of unsent lines (without header), and the position after them."""
        name, off = self.sync_state()
        files = self.files()
        names = [p.name for p in files]
        if not names:
            return [], name, off
        if name not in names:
            name, off = names[0], 0
        idx = names.index(name)
        while True:
            path = self.root / name
            with path.open("rb") as f:
                f.seek(off)
                lines, pos = [], off
                for raw in f:
                    if not raw.endswith(b"\n"):
                        break  # partially written line
                    pos += len(raw)
                    line = raw.decode().strip()
                    if line and not line.startswith("timestamp,"):
                        lines.append(line)
                    if len(lines) >= max_lines:
                        break
            if lines or idx == len(names) - 1:
                return lines, name, pos
            idx += 1  # this day is fully sent; move to the next file
            name, off = names[idx], 0
            self.save_sync(name, 0)

    def backlog_lines(self) -> int:
        name, off = self.sync_state()
        n = 0
        started = False
        for p in self.files():
            if p.name == name:
                started = True
                with p.open("rb") as f:
                    f.seek(off)
                    n += sum(1 for l in f if l.strip() and not l.startswith(b"timestamp,"))
            elif started:
                with p.open("rb") as f:
                    n += sum(1 for l in f if l.strip() and not l.startswith(b"timestamp,"))
        return n


class RigRunner:
    def __init__(self, a):
        import numpy as np

        self.a = a
        self.card = SDCard(Path(a.card))
        self.server = a.server.rstrip("/")
        self.wifi = not a.offline
        self.lock = threading.Lock()
        faults = FaultState.from_class(
            {"normal": 0, "clog": 1, "pump": 2, "membrane": 3, "sensor": 4}[a.fault], a.level, a.tds,
            SENSOR_ALIASES.get(a.sensor_mode, a.sensor_mode))
        start = datetime.now().replace(microsecond=0) - timedelta(minutes=a.prefill)
        self.rig = VirtualRig(device_id=a.device, start=start, faults=faults,
                              calib=RigCalibration.random(np.random.default_rng(a.seed)),
                              counters=Counters(pump_runtime_s=36_000, pump_cycles=420, uv_hours=10.0, filter_hours=10.0),
                              training_label=a.label, seed=a.seed)
        self.last_sync_msg = "not synced yet"
        self.last_row: dict | None = None
        self.stop = threading.Event()
        self._last_sync = 0.0

    # ---------------------------------------------------------- controls
    def set_fault(self, kind: str, level: int = 0, sensor: str | None = None):
        with self.lock:
            tds = self.rig.faults.tds_source
            cls = {"normal": 0, "clog": 1, "pump": 2, "membrane": 3, "sensor": 4}[kind]
            self.rig.faults = FaultState.from_class(cls, level, tds, sensor)

    def set_tds(self, ppm: float):
        with self.lock:
            self.rig.faults.tds_source = float(ppm)

    def set_label(self, label: int | None):
        with self.lock:
            self.rig.training_label = label

    # ---------------------------------------------------------- loop
    def sync(self):
        import requests

        if not self.wifi:
            self.last_sync_msg = f"Wi-Fi off: {self.card.backlog_lines()} lines waiting on SD"
            return
        for _ in range(20):  # catch up in batches
            lines, name, pos = self.card.unsent()
            if not lines:
                self.last_sync_msg = f"in sync at {datetime.now():%H:%M:%S}"
                return
            headers = {"Content-Type": "text/csv"}
            if self.a.api_key:
                headers["X-Api-Key"] = self.a.api_key
            try:
                r = requests.post(f"{self.server}/ingest", data="\n".join(lines).encode(), headers=headers, timeout=10)
                if r.status_code != 200:
                    self.last_sync_msg = f"server said {r.status_code}; keeping backlog"
                    return
                self.card.save_sync(name, pos)
                res = r.json()
                for al in res.get("alerts", []):
                    print(f"  [cloud] ALERT raised: {al['fault_name']} ({al['confidence']:.0%})")
            except Exception as e:
                self.last_sync_msg = f"server unreachable ({type(e).__name__}); {self.card.backlog_lines()} lines waiting"
                return

    def loop(self):
        while not self.stop.is_set():
            behind = (datetime.now() - self.rig.t).total_seconds()
            fast = behind > 2  # catching up on --prefill history
            with self.lock:
                row = self.rig.step()
            if row is not None:
                self.card.append(row)
                self.last_row = row
            now = time.time()
            # while catching up on --prefill history nothing is sent; the
            # backlog goes out in batches once the rig reaches real time
            if not fast and now - self._last_sync >= schema.SYNC_INTERVAL_S / max(self.a.speed, 1e-3):
                self._last_sync = now
                self.sync()
            if self.a.duration and self.rig.t >= self.end_t:
                self.sync()
                self.stop.set()
                break
            if not fast:
                time.sleep(1.0 / self.a.speed)

    def status(self) -> str:
        f = self.rig.faults
        fault = schema.CLASS_NAMES[f.dominant_class()]
        r = self.last_row or {}
        return (f"{self.rig.t:%H:%M:%S} pump={'ON ' if r.get('pump_on') else 'OFF'} P={r.get('pressure_bar', 0):.2f}bar "
                f"Q={r.get('flow_lpm', 0):.3f}L/min I={r.get('pump_current_a', 0):.2f}A TDS {r.get('tds_in', 0):.0f}->"
                f"{r.get('tds_out', 0):.0f} | fault: {fault} | wifi {'on' if self.wifi else 'OFF'} | {self.last_sync_msg}")

    def start(self):
        # --duration counts simulated seconds after the --prefill history
        self.end_t = datetime.now().replace(microsecond=0) + timedelta(seconds=self.a.duration)
        th = threading.Thread(target=self.loop, daemon=True)
        th.start()
        return th


def handle(cmd: str, rr: RigRunner) -> bool:
    p = cmd.strip().lower().split()
    if not p:
        return True
    try:
        if p[0] in ("quit", "exit", "q"):
            return False
        if p[0] == "normal":
            rr.set_fault("normal")
        elif p[0] in ("clog", "pump", "membrane"):
            rr.set_fault(p[0], int(p[1]) if len(p) > 1 else 2)
        elif p[0] == "sensor":
            rr.set_fault("sensor", 1, SENSOR_ALIASES.get(p[1] if len(p) > 1 else "unplug", "tds_out_unplugged"))
        elif p[0] == "tds":
            rr.set_tds(float(p[1]))
        elif p[0] == "wifi":
            rr.wifi = p[1] == "on"
        elif p[0] == "label":
            rr.set_label(None if p[1] == "off" else int(p[1]))
        elif p[0] != "status":
            print("unknown command")
        print(rr.status())
    except (IndexError, ValueError, KeyError) as e:
        print("bad command:", e)
    return True


def panel(rr: RigRunner):
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title(f"AquaSense virtual rig - fault-injection panel ({rr.a.device})")
    root.geometry("560x430")
    pad = {"padx": 8, "pady": 4}

    ttk.Label(root, text="Fault-injection panel", font=("TkDefaultFont", 14, "bold")).pack(anchor="w", **pad)
    frm = ttk.Frame(root)
    frm.pack(fill="x", **pad)

    vars_ = {k: tk.IntVar(value=0) for k in ("clog", "pump", "membrane")}
    labels = {"clog": "CLOG ball valve (filter/line clogging)", "pump": "PWM knob down (pump weakness)",
              "membrane": "BYPASS needle valve (membrane wear)"}

    def apply(*_):
        active = [(k, v.get()) for k, v in vars_.items() if v.get() > 0]
        with rr.lock:
            f = rr.rig.faults
            f.clog, f.pump, f.membrane = (vars_["clog"].get() / 3, vars_["pump"].get() / 3, vars_["membrane"].get() / 3)
            f.sensor_mode = {"none": None, "unplug": "tds_out_unplugged", "pfreeze": "pressure_frozen",
                             "tfreeze": "tds_out_frozen"}[sensor.get()]
            f.tds_source = float(salt.get())
        _ = active

    for i, k in enumerate(vars_):
        ttk.Label(frm, text=labels[k]).grid(row=i, column=0, sticky="w")
        for lvl in range(4):
            ttk.Radiobutton(frm, text=["off", "1", "2", "3"][lvl], variable=vars_[k], value=lvl, command=apply).grid(row=i, column=1 + lvl)

    sensor = tk.StringVar(value="none")
    sf = ttk.LabelFrame(root, text="Sensor fault")
    sf.pack(fill="x", **pad)
    for txt, val in (("none", "none"), ("unplug TDS-out probe", "unplug"), ("freeze pressure (toggle)", "pfreeze"), ("freeze TDS-out", "tfreeze")):
        ttk.Radiobutton(sf, text=txt, variable=sensor, value=val, command=apply).pack(side="left", padx=4)

    salt = tk.IntVar(value=int(rr.rig.faults.tds_source))
    tf = ttk.LabelFrame(root, text="Salt in the bucket (source TDS, not a fault)")
    tf.pack(fill="x", **pad)
    for ppm in (300, 800, 1500):
        ttk.Radiobutton(tf, text=f"{ppm} ppm", variable=salt, value=ppm, command=apply).pack(side="left", padx=4)

    wifi = tk.BooleanVar(value=rr.wifi)
    ttk.Checkbutton(root, text="Wi-Fi / hotspot on", variable=wifi, command=lambda: setattr(rr, "wifi", wifi.get())).pack(anchor="w", **pad)

    status = tk.StringVar()
    ttk.Label(root, textvariable=status, wraplength=540, justify="left", font=("TkFixedFont", 9)).pack(fill="x", **pad)

    def tick():
        status.set(rr.status().replace(" | ", "\n"))
        if rr.stop.is_set():
            root.destroy()
            return
        root.after(1000, tick)

    tick()
    root.protocol("WM_DELETE_WINDOW", lambda: (rr.stop.set(), root.destroy()))
    root.mainloop()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--device", default="AQ-RIG-01")
    ap.add_argument("--card", default=str(ROOT / "data" / "sdcard"), help="virtual SD card folder")
    ap.add_argument("--server", default=os.environ.get("AQUASENSE_SERVER", "http://localhost:5000"))
    ap.add_argument("--api-key", default=os.environ.get("AQUASENSE_API_KEY", ""))
    ap.add_argument("--fault", choices=["normal", "clog", "pump", "membrane", "sensor"], default="normal")
    ap.add_argument("--level", type=int, default=2)
    ap.add_argument("--sensor-mode", default="unplug", help="unplug | pfreeze | tfreeze")
    ap.add_argument("--tds", type=float, default=300)
    ap.add_argument("--label", type=int, default=None, help="training mode with this label (0-4)")
    ap.add_argument("--offline", action="store_true", help="start with Wi-Fi off (Scenario 2)")
    ap.add_argument("--prefill", type=float, default=0, help="minutes of history to generate instantly at start")
    ap.add_argument("--speed", type=float, default=1.0, help="simulated seconds per real second")
    ap.add_argument("--duration", type=float, default=0, help="stop after this many simulated seconds (0 = run forever)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--panel", action="store_true", help="show the Tk fault-injection panel")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    rr = RigRunner(a)
    th = rr.start()
    print(f"Virtual rig {a.device}: SD card at {a.card}, server {rr.server}")
    if a.panel:
        panel(rr)
        return
    if a.duration or not sys.stdin.isatty():
        while th.is_alive():
            th.join(5)
            if not a.quiet:
                print(rr.status(), flush=True)
        return
    print("Commands: normal | clog N | pump N | membrane N | sensor unplug|pfreeze|tfreeze | tds PPM | wifi on|off | status | quit")
    try:
        while handle(input("> "), rr):
            pass
    except (EOFError, KeyboardInterrupt):
        pass
    rr.stop.set()


if __name__ == "__main__":
    main()
