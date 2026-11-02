"""Dynamic repair pricing (build plan section 9).

The price is computed from model outputs and a parts table, so it changes
with the unit's condition. Prices are placeholders; replace them with
Atomberg-style values in PARTS / LABOUR / VISIT_CHARGE.

Rules
  Replace now  component health < 40 %, or failure probability within 30 days > 0.6
  Watch list   failure probability between 0.2 and 0.6 (not billed now)
  Repair price (now) = parts to replace now + labour + visit charge
  Expected cost (warranty planning) = sum(failure probability x part cost)
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from . import schema

PARTS = {
    "filter": ("Sediment/carbon filter", 400),
    "membrane": ("RO membrane", 1800),
    "pump": ("Booster pump", 1500),
    "sensors": ("Sensor/electronics fix", 600),
}
LABOUR = 300
VISIT_CHARGE = 200
REPLACE_PROB = 0.6
WATCH_PROB = 0.2


@dataclass
class QuoteLine:
    component: str
    part: str
    cost: int
    health: float
    failure_prob: float
    decision: str  # "replace" | "watch" | "ok"
    expected_cost: float


def decide(health: float, failure_prob: float) -> str:
    if health < schema.REPLACE_THRESHOLD or failure_prob > REPLACE_PROB:
        return "replace"
    if failure_prob >= WATCH_PROB:
        return "watch"
    return "ok"


def quote(components: list[dict], visit: bool = True) -> dict:
    """components: [{'component': 'pump', 'health': 35, 'failure_prob': 0.8}, ...]"""
    lines: list[QuoteLine] = []
    for c in components:
        comp = c["component"]
        if comp not in PARTS:
            continue
        part, cost = PARTS[comp]
        p = float(max(0.0, min(1.0, c["failure_prob"])))
        lines.append(QuoteLine(comp, part, cost, round(float(c["health"]), 1), round(p, 2),
                               decide(float(c["health"]), p), round(p * cost, 2)))
    replace = [l for l in lines if l.decision == "replace"]
    watch = [l for l in lines if l.decision == "watch"]
    parts_total = sum(l.cost for l in replace)
    labour = LABOUR if replace else 0
    visit_charge = VISIT_CHARGE if (visit or replace) else 0
    return {
        "lines": [asdict(l) for l in lines],
        "replace_now": [asdict(l) for l in replace],
        "watch_list": [asdict(l) for l in watch],
        "parts_total": parts_total,
        "labour": labour,
        "visit_charge": visit_charge,
        "repair_price_now": parts_total + labour + visit_charge,
        "expected_cost": round(sum(l.expected_cost for l in lines)),
        "currency": "INR",
    }


def format_inr(n: float) -> str:
    n = int(round(n))
    s = str(abs(n))
    if len(s) > 3:  # Indian digit grouping: 1,23,456
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups + [tail])
    return ("-" if n < 0 else "") + "₹" + s
