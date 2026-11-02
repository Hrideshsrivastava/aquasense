import numpy as np
import pytest

from aquasense import features as F, schema
from aquasense.engine import get_engine
from aquasense.rigsim import FaultState, RigCalibration, VirtualRig


def run(faults, seconds=600, seed=7, training=None):
    # seed 7 = a rig whose healthy baseline is close to the fleet average. Units far
    # from the average (2+ sigma) can be mistaken for a level-1 fault: see README
    # "Known limitations" (per-unit baseline).
    rig = VirtualRig(faults=faults, seed=seed, calib=RigCalibration.random(np.random.default_rng(seed)),
                     training_label=training)
    rig.run(150)
    return rig.run(seconds)


@pytest.fixture(scope="module")
def eng():
    return get_engine()


CASES = [
    ("normal", FaultState(tds_source=300)),
    ("normal", FaultState(tds_source=1500)),  # high-TDS source is NOT a fault
    ("clogging", FaultState(clog=2 / 3)),
    ("pump", FaultState(pump=2 / 3)),
    ("membrane", FaultState(membrane=2 / 3)),
    ("sensor", FaultState(sensor_mode="tds_out_unplugged")),
    ("sensor", FaultState(sensor_mode="pressure_frozen")),
]


@pytest.mark.parametrize("expected,faults", CASES)
def test_fault_classes(eng, expected, faults):
    a = eng.assess(run(faults))
    assert a["fault"] == expected, (a["fault_name"], a["reason"])


def test_sensor_fault_does_not_blame_membrane(eng):
    a = eng.assess(run(FaultState(sensor_mode="tds_out_unplugged")))
    comp = {c["component"]: c for c in a["components"]}
    assert comp["membrane"]["health"] >= 80
    assert comp["sensors"]["decision"] == "replace"
    assert "Sensor/electronics fix" in [l["part"] for l in a["quote"]["replace_now"]]


def test_severity_moves_the_price(eng):
    """Change the fault, rescan, and the quote moves (dynamic pricing)."""
    healthy = eng.assess(run(FaultState()))["quote"]["repair_price_now"]
    pump = eng.assess(run(FaultState(pump=1.0)))["quote"]["repair_price_now"]
    membrane = eng.assess(run(FaultState(membrane=1.0)))["quote"]["repair_price_now"]
    assert healthy == 200 and pump == 2000 and membrane == 2300


def test_health_falls_with_level(eng):
    hs = [eng.assess(run(FaultState(clog=l / 3)))["components"][2]["health"] for l in (0, 1, 2, 3)]
    assert hs[0] > hs[1] > hs[2] > hs[3]
    assert hs[3] < schema.REPLACE_THRESHOLD


def test_training_and_normal_logging_give_same_features():
    """Binning makes 2 s (training) and 5 s (normal) logs comparable."""
    f = {}
    keys = ("pressure_bar_std", "flow_lpm_std", "pump_current_a_std", "pressure_bar_mean", "buildup_s")
    for name, label in (("normal", None), ("training", 0)):
        df = F.to_frame(run(FaultState(), seconds=900, training=label, seed=9))
        ws = F.sliding_windows(df)
        f[name] = {k: np.mean([w[1][k] for w in ws]) for k in keys}
    for k in keys:
        assert abs(f["normal"][k] - f["training"][k]) / max(f["training"][k], 1e-3) < 0.3, k


def test_row_format_matches_header():
    rows = run(FaultState(), seconds=30)
    assert rows and set(schema.CSV_HEADER) <= set(rows[0])
    assert rows[0]["label"] == ""
