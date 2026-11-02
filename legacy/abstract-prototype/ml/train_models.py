"""Train XGBoost (RUL regression) and Isolation Forest (anomaly) on SYNTHETIC data.
Production should use consented, anonymised Atomberg telemetry.
Run from project root:  python -m ml.train_models   (writes ml/rul.json, ml/iso.pkl)"""
import pickle, numpy as np, xgboost as xgb
from sklearn.ensemble import IsolationForest
from app import twin

rng = np.random.default_rng(7)
X, y = [], []
for _ in range(6000):
    t = dict(tds_in=rng.uniform(150, 2000), flow=rng.uniform(.3, 1.6), pressure=rng.uniform(2, 7),
             current=rng.uniform(.6, 1.8), build=rng.uniform(30, 130), uv=rng.uniform(0, 9000), temp=rng.uniform(10, 40))
    t["tds_out"] = t["tds_in"] * rng.uniform(.02, .35)
    r = twin.evaluate(t, str(rng.choice(list(twin.SOURCE_K))))
    X.append([t[k] for k in ("tds_in", "tds_out", "flow", "pressure", "current", "build", "uv", "temp")])
    y.append(min(sum(p["rul_days"]) / 2 for p in r["parts"].values()))
X, y = np.array(X), np.array(y)
m = xgb.XGBRegressor(n_estimators=200, max_depth=5, learning_rate=.08).fit(X, y)
m.save_model("ml/rul.json")
pickle.dump(IsolationForest(contamination=.03, random_state=7).fit(X[y > 60]), open("ml/iso.pkl", "wb"))
print("trained on", len(X), "rows; R2 (train):", round(m.score(X, y), 3))
