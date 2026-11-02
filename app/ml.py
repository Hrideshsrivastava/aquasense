"""Optional ML layer. Loads ml/rul.json (XGBoost) and ml/iso.pkl (Isolation Forest) if present."""
import pickle
from pathlib import Path
D = Path(__file__).resolve().parent.parent / "ml"
KEYS = ("tds_in", "tds_out", "flow", "pressure", "current", "build", "uv", "temp")
_m = _iso = None
_tried = False

def _load():
    global _m, _iso, _tried
    if _tried: return
    _tried = True
    try:
        import xgboost as xgb
        if (D / "rul.json").exists():
            _m = xgb.XGBRegressor(); _m.load_model(str(D / "rul.json"))
        if (D / "iso.pkl").exists():
            _iso = pickle.load(open(D / "iso.pkl", "rb"))  # our own file; do not load pickles from others
    except Exception as e:
        print("ML models not loaded:", e)

def predict(t: dict):
    _load()
    if _m is None and _iso is None: return None
    import numpy as np
    x = np.array([[t[k] for k in KEYS]])
    out = {}
    if _m is not None: out["rul_days"] = max(0, round(float(_m.predict(x)[0])))
    if _iso is not None: out["anomaly"] = bool(_iso.predict(x)[0] == -1)
    return out
