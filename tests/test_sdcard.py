from datetime import datetime

from aquasense.rigsim import FaultState, VirtualRig
from handheld.app import analyze_card, quote_text, save_quote
from simulator.make_sample_card import make
from simulator.virtual_rig import SDCard


def test_backlog_across_midnight(tmp_path):
    """Offline over midnight: the backlog is sent file by file, in order, once."""
    card = SDCard(tmp_path)
    rig = VirtualRig(start=datetime(2026, 11, 3, 23, 55, 0), faults=FaultState(), seed=1)
    rows = rig.run(600)
    for r in rows:
        card.append(r)
    assert sorted(p.name for p in card.files()) == ["2026-11-03.csv", "2026-11-04.csv"]
    assert card.backlog_lines() == len(rows)
    sent = []
    while True:
        lines, name, pos = card.unsent(max_lines=25)
        if not lines:
            break
        sent += lines
        card.save_sync(name, pos)  # pretend the POST returned 200
    assert len(sent) == len(rows) and card.backlog_lines() == 0
    assert [l.split(",")[0] for l in sent] == [r["timestamp"] for r in rows]


def test_handheld_scenario2_on_sample_card(tmp_path):
    p = make(tmp_path / "card", fault="pump", level=2)
    res = analyze_card(p)
    a = res["assessment"]
    assert a["fault"] == "pump"
    assert a["quote"]["repair_price_now"] == 2000
    assert "Booster pump" in quote_text(res)
    txt, js = save_quote(res, tmp_path / "quotes")
    assert txt.exists() and js.exists()


def test_rescan_with_other_fault_changes_quote(tmp_path):
    pump = analyze_card(make(tmp_path / "a", fault="pump", level=3))["assessment"]["quote"]["repair_price_now"]
    mem = analyze_card(make(tmp_path / "b", fault="membrane", level=3))["assessment"]["quote"]["repair_price_now"]
    assert pump != mem
