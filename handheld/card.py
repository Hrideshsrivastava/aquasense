"""Find and read the RO unit's SD card (build plan 7.2 steps 1-2)."""

from __future__ import annotations

import csv
import glob
import os
import re
import string
import sys
from pathlib import Path

import pandas as pd

from aquasense import schema

DAY_FILE = re.compile(r"^\d{4}-\d{2}-\d{2}\.csv$")


def is_card(path: str | Path) -> bool:
    p = Path(path)
    if not p.is_dir():
        return False
    try:
        for f in sorted(p.iterdir(), reverse=True):
            if DAY_FILE.match(f.name):
                with f.open(newline="") as fh:
                    first = fh.readline().strip()
                return first.split(",")[:3] == schema.CSV_HEADER[:3]
    except OSError:
        return False
    return False


def candidate_mounts() -> list[str]:
    pats = ["/media/*/*", "/media/*", "/mnt/*", "/run/media/*/*", "/Volumes/*"]
    out = [d for pat in pats for d in glob.glob(pat)]
    if sys.platform.startswith("win"):
        out += [f"{d}:\\" for d in string.ascii_uppercase[3:] if os.path.exists(f"{d}:\\")]
    return out


def find_cards(extra: list[str] | None = None) -> list[str]:
    seen, cards = set(), []
    for d in (extra or []) + candidate_mounts():
        if d in seen:
            continue
        seen.add(d)
        if is_card(d):
            cards.append(d)
    return cards


def read_card(path: str | Path, max_files: int = 3, minutes: float | None = 30) -> tuple[pd.DataFrame, dict]:
    """Rows from the newest day files (the newest device on the card), plus
    information about what is on the card."""
    p = Path(path)
    files = sorted(f for f in p.iterdir() if DAY_FILE.match(f.name))
    if not files:
        raise FileNotFoundError(f"No YYYY-MM-DD.csv files on {p}")
    use = files[-max_files:]
    frames = []
    for f in use:
        try:
            frames.append(pd.read_csv(f, dtype={"label": str}, on_bad_lines="skip"))
        except (pd.errors.EmptyDataError, csv.Error):
            continue
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=schema.CSV_HEADER)
    df = df[df["timestamp"] != "timestamp"]  # stray header lines
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp"]).sort_values("timestamp")
    info = {"card": str(p), "files": [f.name for f in files], "files_read": [f.name for f in use], "rows_total": int(len(df))}
    if df.empty:
        return df, info
    device = df["device_id"].iloc[-1]
    df = df[df["device_id"] == device]
    if minutes:
        df = df[df["timestamp"] > df["timestamp"].iloc[-1] - pd.Timedelta(minutes=minutes)]
    info.update(device_id=str(device), first=str(df["timestamp"].iloc[0]), last=str(df["timestamp"].iloc[-1]),
                rows=int(len(df)))
    sync = p / "sync.txt"
    if sync.exists():
        info["sync"] = sync.read_text().strip()
    return df.reset_index(drop=True), info
