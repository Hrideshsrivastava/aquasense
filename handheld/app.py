"""AquaSense Technician Handheld (Unit B, build plan section 7).

Raspberry Pi + small touchscreen + USB card reader. Works fully offline:
  1. the technician inserts the RO unit's SD card
  2. the app detects it and reads the latest CSV files
  3. it builds the same features and runs the same models as the cloud, and
     shows component health, the most likely fault with confidence, the RUL
     range and the dynamic repair price
  4. "Save quote" writes the quote and a job summary to a file

    python -m handheld.app                    # full screen (Esc leaves full screen)
    python -m handheld.app --windowed         # on a laptop
    python -m handheld.app --card data/sample_sdcard
    python -m handheld.app --cli --card data/sample_sdcard   # text report, no screen needed
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aquasense import pricing, schema  # noqa: E402
from aquasense.engine import get_engine  # noqa: E402

from handheld.card import find_cards, read_card  # noqa: E402

DEMO_CARD = ROOT / "data" / "sample_sdcard"
QUOTE_DIR = Path(os.environ.get("AQUASENSE_QUOTES", Path.home() / "AquaSense_quotes"))


# ------------------------------------------------------------------ logic (no GUI)
def analyze_card(path: str | Path, minutes: float = 30, lookback: int = 6) -> dict:
    df, info = read_card(path, minutes=minutes)
    if df.empty:
        return {"card": info, "assessment": {"status": "no_data", "message": "The card has no readings."}}
    eng = get_engine()
    a = eng.assess(df, lookback=lookback)
    return {"card": info, "assessment": a, "analysed_at": datetime.now().isoformat(timespec="seconds"),
            "model": {k: eng.card.get(k) for k in ("trained_at", "data_source", "simulated")}}


def quote_text(res: dict, technician: str = "") -> str:
    a, c = res["assessment"], res["card"]
    q = a["quote"]
    L = ["AQUASENSE SERVICE QUOTE (technician handheld, offline)", "=" * 50,
         f"Date        : {datetime.now():%Y-%m-%d %H:%M}",
         f"Unit        : {c.get('device_id', '?')}",
         f"Data on card: {c.get('first', '')[:16]} to {c.get('last', '')[:16]} ({c.get('rows', 0)} rows)"]
    if technician:
        L.append(f"Technician  : {technician}")
    L += ["", f"Most likely fault: {a['fault_name']} ({a['confidence']:.0%} confidence)", f"Why: {a['reason']}"]
    L += [f"  - {e}" for e in a.get("evidence", [])[:4]]
    L += ["", "Component health"]
    for comp in a["components"]:
        r = comp.get("rul")
        rs = (f"RUL {r['low']}-{r['high']} days" if r and r.get("high") else "replace now") if r else ""
        L.append(f"  {comp['name']:<16}{comp['health']:5.0f}%   {rs}")
    L += ["", "Replace now"]
    L += [f"  {l['part']:<28}{pricing.format_inr(l['cost']):>9}" for l in q["replace_now"]] or ["  (nothing)"]
    if q["labour"]:
        L.append(f"  {'Labour':<28}{pricing.format_inr(q['labour']):>9}")
    L.append(f"  {'Visit charge':<28}{pricing.format_inr(q['visit_charge']):>9}")
    L.append(f"  {'REPAIR PRICE NOW':<28}{pricing.format_inr(q['repair_price_now']):>9}")
    if q["watch_list"]:
        L += ["", "Watch list (not billed now)"]
        L += [f"  {l['part']}: failure probability {l['failure_prob']:.2f}, expected {pricing.format_inr(l['expected_cost'])}"
              for l in q["watch_list"]]
    L += ["", f"Expected cost for warranty planning: {pricing.format_inr(q['expected_cost'])}"]
    if a.get("counters"):
        k = a["counters"]
        L.append(f"Counters: pump {k['pump_runtime_h']} h, {k['pump_cycles']} starts, UV {k['uv_hours']} h, filter {k['filter_hours']} h")
    L += ["", "RUL ranges come from a model trained on simulated degradation calibrated from the rig."]
    return "\n".join(L)


def save_quote(res: dict, out_dir: str | Path = QUOTE_DIR, technician: str = "") -> tuple[Path, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = f"quote_{res['card'].get('device_id', 'unit')}_{datetime.now():%Y%m%d_%H%M%S}"
    txt, js = out / f"{stem}.txt", out / f"{stem}.json"
    txt.write_text(quote_text(res, technician), encoding="utf-8")
    js.write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
    return txt, js


# ------------------------------------------------------------------ GUI
STATUS_COL = {"normal": "#0ca30c", "warning": "#fab219", "replace": "#d03b3b"}
STATUS_TXT = {"normal": "OK", "warning": "WATCH", "replace": "REPLACE"}
BG, SURF, INK, MUTE, ACC = "#101a2b", "#17243a", "#eef2f8", "#9aa8bf", "#3987e5"


class HandheldApp:
    def __init__(self, root, card: str | None, windowed: bool):
        import tkinter as tk
        from tkinter import ttk

        self.tk, self.ttk, self.root = tk, ttk, root
        self.extra = [card] if card else []
        self.result: dict | None = None
        self.busy = False
        root.title("AquaSense Technician")
        root.configure(bg=BG)
        if windowed:
            root.geometry("800x480")
        else:
            root.attributes("-fullscreen", True)
        root.bind("<Escape>", lambda e: root.attributes("-fullscreen", False))
        root.bind("<F11>", lambda e: root.attributes("-fullscreen", not root.attributes("-fullscreen")))
        w = root.winfo_screenwidth() if not windowed else 800
        self.f = 1.0 if w >= 700 else 0.75  # font scale for 480x320 screens
        st = ttk.Style()
        st.theme_use("clam")
        st.configure(".", background=BG, foreground=INK, fieldbackground=SURF, font=("DejaVu Sans", int(11 * self.f)))
        st.configure("TNotebook", background=BG, borderwidth=0)
        st.configure("TNotebook.Tab", background=SURF, foreground=MUTE, padding=(int(14 * self.f), int(6 * self.f)),
                     font=("DejaVu Sans", int(12 * self.f), "bold"))
        st.map("TNotebook.Tab", background=[("selected", ACC)], foreground=[("selected", "#ffffff")])
        st.configure("Big.TButton", font=("DejaVu Sans", int(13 * self.f), "bold"), padding=int(10 * self.f),
                     background=ACC, foreground="#ffffff")
        st.map("Big.TButton", background=[("active", "#2a78d6"), ("disabled", "#34465f")])
        st.configure("Flat.TButton", font=("DejaVu Sans", int(11 * self.f)), padding=int(8 * self.f), background=SURF, foreground=INK)
        st.map("Flat.TButton", background=[("active", "#22324d")], foreground=[("active", INK)])
        self.container = tk.Frame(root, bg=BG)
        self.container.pack(fill="both", expand=True)
        self.home()
        self.poll()

    # -- helpers
    def clear(self):
        for w in self.container.winfo_children():
            w.destroy()

    def label(self, parent, text, size=11, bold=False, color=INK, **kw):
        return self.tk.Label(parent, text=text, bg=kw.pop("bg", BG), fg=color, justify="left", anchor="w",
                             font=("DejaVu Sans", int(size * self.f), "bold" if bold else "normal"), **kw)

    # -- home screen
    def home(self):
        self.screen = "home"
        self.clear()
        c = self.container
        top = self.tk.Frame(c, bg=BG)
        top.pack(fill="x", padx=16, pady=(14, 4))
        self.label(top, "AquaSense", 22, True, ACC).pack(side="left")
        self.label(top, "  technician handheld · offline", 11, color=MUTE).pack(side="left", pady=(8, 0))
        self.label(c, "Insert the RO unit's SD card into the reader.", 15, True).pack(fill="x", padx=16, pady=(18, 4))
        self.card_lbl = self.label(c, "Looking for a card...", 12, color=MUTE)
        self.card_lbl.pack(fill="x", padx=16)
        btns = self.tk.Frame(c, bg=BG)
        btns.pack(fill="x", padx=16, pady=18)
        self.scan_btn = self.ttk.Button(btns, text="Scan card", style="Big.TButton", command=self.scan, state="disabled")
        self.scan_btn.pack(side="left", padx=(0, 10))
        self.ttk.Button(btns, text="Open folder…", style="Flat.TButton", command=self.pick).pack(side="left", padx=4)
        if DEMO_CARD.exists():
            self.ttk.Button(btns, text="Demo card", style="Flat.TButton",
                            command=lambda: self.scan(str(DEMO_CARD))).pack(side="left", padx=4)
        self.msg = self.label(c, "", 12, color=MUTE)
        self.msg.pack(fill="x", padx=16)
        self.label(c, "Everything runs on this device. No internet needed.", 10, color=MUTE).pack(side="bottom", fill="x", padx=16, pady=8)
        self.cards: list[str] = []

    def poll(self):
        if self.screen == "home" and not self.busy:
            self.cards = find_cards(self.extra)
            if self.cards:
                self.card_lbl.config(text=f"Card found: {self.cards[0]}", fg=STATUS_COL["normal"])
                self.scan_btn.config(state="normal")
            else:
                self.card_lbl.config(text="No AquaSense card detected yet.", fg=MUTE)
                self.scan_btn.config(state="disabled")
        self.root.after(2000, self.poll)

    def pick(self):
        from tkinter import filedialog

        d = filedialog.askdirectory(title="Select the SD card folder")
        if d:
            self.scan(d)

    def scan(self, path: str | None = None):
        path = path or (self.cards[0] if self.cards else None)
        if not path or self.busy:
            return
        self.busy = True
        self.msg.config(text="Reading the card and running the models…", fg=INK)

        def work():
            try:
                res = analyze_card(path)
                self.root.after(0, lambda: self.show(res))
            except Exception as e:  # show any problem on screen, never crash
                err = f"Could not analyse the card: {e}"
                self.root.after(0, lambda: self.msg.config(text=err, fg=STATUS_COL["replace"]))
            finally:
                self.busy = False

        threading.Thread(target=work, daemon=True).start()

    # -- result screen
    def show(self, res: dict):
        self.result, self.screen = res, "result"
        a, info = res["assessment"], res["card"]
        self.clear()
        c = self.container
        if a.get("status") == "no_data":
            self.label(c, a["message"], 14, True).pack(padx=16, pady=20)
            self.ttk.Button(c, text="Back", style="Flat.TButton", command=self.home).pack()
            return
        col = STATUS_COL[a["status"]]
        head = self.tk.Frame(c, bg=SURF)
        head.pack(fill="x")
        self.tk.Label(head, text=f" {STATUS_TXT[a['status']]} ", bg=col, fg="#000000" if a["status"] == "warning" else "#ffffff",
                      font=("DejaVu Sans", int(13 * self.f), "bold")).pack(side="left", padx=10, pady=8)
        self.label(head, f"{a['fault_name']}  ·  {a['confidence']:.0%}", 14, True, bg=SURF).pack(side="left")
        self.label(head, f"{info.get('device_id', '')}", 11, color=MUTE, bg=SURF).pack(side="right", padx=10)

        bar = self.tk.Frame(c, bg=BG)  # packed before the notebook so it is never pushed off a small screen
        bar.pack(side="bottom", fill="x", padx=8, pady=(0, 8))
        nb = self.ttk.Notebook(c)
        nb.pack(fill="both", expand=True, padx=6, pady=6)
        for title, build in (("Health", self.tab_health), ("Fault", self.tab_fault), ("Quote", self.tab_quote), ("Unit", self.tab_unit)):
            fr = self.tk.Frame(nb, bg=BG)
            nb.add(fr, text=title)
            build(fr, res)
        if a["quote"]["replace_now"]:
            nb.select(2)

        self.ttk.Button(bar, text="Save quote", style="Big.TButton", command=self.save).pack(side="left")
        self.ttk.Button(bar, text="Rescan", style="Flat.TButton", command=lambda: self.scan(info["card"])).pack(side="left", padx=6)
        self.ttk.Button(bar, text="Done", style="Flat.TButton", command=self.home).pack(side="left")
        self.saved = self.label(bar, "", 10, color=MUTE)
        self.saved.pack(side="left", padx=8)

    def tab_health(self, fr, res):
        a = res["assessment"]
        cv = self.tk.Canvas(fr, bg=BG, highlightthickness=0)
        cv.pack(fill="both", expand=True, padx=8, pady=6)

        def draw(_=None):
            cv.delete("all")
            W = max(cv.winfo_width(), 300)
            f = self.f
            rowh = int(50 * f)
            namew, barw = int(150 * self.f), max(80, W - int(150 * self.f) - int(210 * self.f))
            for i, comp in enumerate(a["components"]):
                y = 10 + i * rowh
                colr = STATUS_COL[comp["status"]]
                cv.create_text(0, y + 10 * f, text=comp["name"], fill=INK, anchor="w", font=("DejaVu Sans", int(12 * self.f), "bold"))
                cv.create_rectangle(namew, y + 2, namew + barw, y + 18 * f, fill=SURF, outline="")
                cv.create_rectangle(namew, y + 2, namew + barw * comp["health"] / 100, y + 18 * f, fill=colr, outline="")
                th = namew + barw * schema.REPLACE_THRESHOLD / 100
                cv.create_line(th, y - 1, th, y + 21 * f, fill=MUTE, dash=(2, 2))
                verdict = {"replace": "REPLACE", "watch": "WATCH", "ok": "OK"}.get(comp.get("decision"), STATUS_TXT[comp["status"]])
                cv.create_text(namew + barw + 8, y + 10 * f, text=f"{comp['health']:.0f}%  {verdict}",
                               fill=INK, anchor="w", font=("DejaVu Sans", int(11 * self.f), "bold"))
                r = comp.get("rul")
                sub = (f"RUL {r['low']}–{r['high']} days" if r and r.get("high") else "below 40 %: replace now") if r \
                    else f"fault probability {comp['failure_prob']:.2f}"
                cv.create_text(namew, y + 31 * f, text=sub, fill=MUTE, anchor="w", font=("DejaVu Sans", int(10 * self.f)))
            cv.create_text(0, 10 + 4 * rowh + 4, anchor="w", fill=MUTE, font=("DejaVu Sans", int(9 * self.f)),
                           text="Dashed line = 40 % replacement threshold. RUL from simulated degradation.")

        cv.bind("<Configure>", draw)

    def tab_fault(self, fr, res):
        a = res["assessment"]
        self.label(fr, f"{a['fault_name']}", 15, True).pack(fill="x", padx=10, pady=(8, 0))
        self.label(fr, f"Confidence {a['confidence']:.0%}" + ("  ·  unusual pattern" if a.get("anomaly") else ""), 11,
                   color=MUTE).pack(fill="x", padx=10)
        self.label(fr, a["reason"], 11, wraplength=int(740 * self.f)).pack(fill="x", padx=10, pady=6)
        for e in a.get("evidence", [])[:4]:
            self.label(fr, f"•  {e}", 11).pack(fill="x", padx=18)
        if a.get("explanation"):
            self.label(fr, "Signals that drove the model: " + ", ".join(x["label"] for x in a["explanation"]), 10,
                       color=MUTE, wraplength=int(740 * self.f)).pack(fill="x", padx=10, pady=8)

    def tab_quote(self, fr, res):
        q = res["assessment"]["quote"]
        g = self.tk.Frame(fr, bg=BG)
        g.pack(fill="x", padx=10, pady=8)
        rows = [(l["part"], pricing.format_inr(l["cost"]), True) for l in q["replace_now"]]
        if q["labour"]:
            rows.append(("Labour", pricing.format_inr(q["labour"]), False))
        rows.append(("Visit charge", pricing.format_inr(q["visit_charge"]), False))
        if not q["replace_now"]:
            self.label(g, "No part needs replacing now.", 12, color=STATUS_COL["normal"]).grid(row=0, column=0, sticky="w")
        for i, (name, cost, part) in enumerate(rows, start=1):
            self.label(g, ("Replace: " if part else "") + name, 12, part).grid(row=i, column=0, sticky="w", pady=1)
            self.label(g, cost, 12).grid(row=i, column=1, sticky="e", padx=20)
        self.label(g, "Repair price now", 15, True).grid(row=len(rows) + 1, column=0, sticky="w", pady=(8, 0))
        self.label(g, pricing.format_inr(q["repair_price_now"]), 18, True, ACC).grid(row=len(rows) + 1, column=1, sticky="e", padx=20, pady=(8, 0))
        if q["watch_list"]:
            self.label(fr, "Watch list (not billed): " + "; ".join(
                f"{l['part']} p={l['failure_prob']:.2f} (≈{pricing.format_inr(l['expected_cost'])})" for l in q["watch_list"]),
                10, color=MUTE, wraplength=int(740 * self.f)).pack(fill="x", padx=10)
        self.label(fr, f"Expected cost for warranty planning: {pricing.format_inr(q['expected_cost'])}", 10,
                   color=MUTE).pack(fill="x", padx=10, pady=4)

    def tab_unit(self, fr, res):
        a, info = res["assessment"], res["card"]
        lines = [f"Unit: {info.get('device_id')}", f"Card: {info.get('card')}",
                 f"Data analysed: {info.get('first', '')[:19]} → {info.get('last', '')[:19]} ({info.get('rows')} rows)",
                 f"Day files on card: {len(info.get('files', []))} (newest {info.get('files', ['-'])[-1]})",
                 f"TDS in {a['tds_in']:.0f} ppm · rejection {a['rejection_pct']:.1f}%"]
        if info.get("sync"):
            lines.append(f"Cloud sync position: {info['sync']}")
        if a.get("counters"):
            k = a["counters"]
            lines.append(f"Pump {k['pump_runtime_h']} h · {k['pump_cycles']} starts · UV {k['uv_hours']} h · filter {k['filter_hours']} h")
        m = res.get("model", {})
        lines.append(f"Models: {m.get('data_source', '?')} ({m.get('trained_at', '?')})")
        for l in lines:
            self.label(fr, l, 10, wraplength=int(760 * self.f)).pack(fill="x", padx=10, pady=1)

    def save(self):
        txt, _ = save_quote(self.result)
        self.saved.config(text=f"Saved {txt}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--card", help="SD card folder (default: auto-detect mounted cards)")
    ap.add_argument("--windowed", action="store_true")
    ap.add_argument("--cli", action="store_true", help="print the report instead of opening the screen")
    ap.add_argument("--save", action="store_true", help="with --cli: also save the quote")
    a = ap.parse_args(argv)
    if a.cli:
        cards = [a.card] if a.card else find_cards()
        if not cards:
            raise SystemExit("No AquaSense SD card found. Pass --card PATH.")
        res = analyze_card(cards[0])
        if res["assessment"].get("status") == "no_data":
            raise SystemExit(res["assessment"]["message"])
        print(quote_text(res))
        if a.save:
            print("\nSaved:", *save_quote(res))
        return
    import tkinter as tk

    get_engine()  # load models before the first scan
    root = tk.Tk()
    HandheldApp(root, a.card, a.windowed)
    root.mainloop()


if __name__ == "__main__":
    main()
