"""Component-level digital twin: health score and RUL range per part.
Rule-based baseline. ml/train_models.py trains XGBoost and Isolation Forest on
synthetic data to replace or blend with these rules."""
SOURCE_K = {"municipal": 1.0, "borewell": 0.8, "tanker": 0.65}
DAYS_PER_PT = {"Sediment": 1.5, "Carbon": 1.7, "RO membrane": 1.4, "Booster pump / motor": 1.3, "UV lamp": 3.2}
PART_COST = {"Sediment": 450, "Carbon": 600, "RO membrane": 3200, "Booster pump / motor": 2400, "UV lamp": 1100}

def _cl(v, a=0.0, b=1.0):
    return max(a, min(b, v))

def evaluate(t: dict, source: str = "municipal") -> dict:
    k = SOURCE_K.get(source, 1.0)
    rej = 1 - t["tds_out"] / max(t["tds_in"], 1)
    low_p, high_p, low_f = t["pressure"] < 3.5, t["pressure"] > 5.5, t["flow"] < 0.8
    cur_dev = abs(t["current"] - 1) / 0.6
    b_dev = _cl((t["build"] - 45) / 60, 0, 1.5)
    dp = max(0.0, t["pressure"] - 4.5)
    health = {
        "Sediment": 100 * (1 - _cl((dp / 3 if high_p else 0) + ((1.2 - t["flow"]) / 1.1 if low_f else 0))),
        "Carbon": 100 * (1 - _cl(dp / 5 + max(0.0, 1.2 - t["flow"]) / 1.6)),
        "RO membrane": _cl((rej - 0.8) / 0.17) * 100,
        "Booster pump / motor": 100 * (1 - _cl(0.55 * cur_dev + 0.5 * b_dev)),
        "UV lamp": 100 * (1 - _cl(t["uv"] / 9000)),
    }
    parts = {}
    for name, h in health.items():
        h = round(h)
        d = max(0, h - 40) * DAYS_PER_PT[name] * (1 if name == "UV lamp" else k)
        parts[name] = {"health": h, "rul_days": [round(d * 0.88), round(d * 1.12)]}

    # Cross-check signals before naming a cause
    sensor = (t["tds_out"] > t["tds_in"] or (low_p and t["flow"] > 1.5)
              or (high_p and t["flow"] > 1.6) or (t["current"] < 0.5 and t["build"] < 30))
    if sensor:
        dx = ("Sensor fault suspected", "Signals disagree. Check the sensor before replacing parts.", "Inspect sensor", "sensor")
    elif low_p and cur_dev > 0.25 and t["build"] > 60:
        dx = ("Pump degradation", "Low pressure, abnormal pump current and slow build-up agree.", "Inspect pump", "Booster pump / motor")
    elif low_p:
        dx = ("Low supply pressure", "Pump current and build-up are normal, so the pump is not blamed.", "Monitor", None)
    elif rej < 0.88 and parts["RO membrane"]["health"] < 70:
        dx = ("Membrane wear", f"TDS rejection is {rej:.0%}.", "Replace" if parts["RO membrane"]["health"] < 50 else "Inspect", "RO membrane")
    elif low_f and high_p:
        dx = ("Filter clogging", "Low flow with high pressure.", "Inspect", "Sediment")
    else:
        worst = min(parts, key=lambda p: parts[p]["health"])
        if parts[worst]["health"] < 60:
            dx = (f"{worst} ageing", "Health is nearing the 40% threshold.", "Monitor", worst)
        else:
            dx = ("All signals agree", "No fault suspected.", "Monitor", None)
    return {"parts": parts, "diagnosis": {"title": dx[0], "why": dx[1], "action": dx[2], "part": dx[3]},
            "rejection": round(rej, 3), "min_health": min(p["health"] for p in parts.values())}

def expected_repair_cost(result: dict) -> int:
    """Failure probability x part cost, summed over parts (warranty planning)."""
    total = 0.0
    for name, p in result["parts"].items():
        prob = _cl((60 - p["health"]) / 60) if p["health"] < 60 else 0.0
        total += prob * PART_COST[name]
    return round(total)
