from aquasense import pricing, rul


def test_worked_example_from_build_plan():
    """Build plan section 9: pump 35 % p=0.8, filter clogged p=0.9, membrane p=0.1 -> Rs 2,400."""
    q = pricing.quote([
        {"component": "pump", "health": 35, "failure_prob": 0.8},
        {"component": "filter", "health": 45, "failure_prob": 0.9},
        {"component": "membrane", "health": 90, "failure_prob": 0.1},
    ])
    assert [l["part"] for l in q["replace_now"]] == ["Booster pump", "Sediment/carbon filter"]
    assert q["repair_price_now"] == 1500 + 400 + 300 + 200 == 2400
    membrane = next(l for l in q["lines"] if l["component"] == "membrane")
    assert membrane["expected_cost"] == 180


def test_rules():
    assert pricing.decide(39, 0.0) == "replace"   # health below 40 %
    assert pricing.decide(80, 0.61) == "replace"  # failure probability above 0.6
    assert pricing.decide(80, 0.3) == "watch"
    assert pricing.decide(80, 0.1) == "ok"
    q = pricing.quote([{"component": "membrane", "health": 80, "failure_prob": 0.3}])
    assert q["repair_price_now"] == pricing.VISIT_CHARGE and q["labour"] == 0
    assert len(q["watch_list"]) == 1


def test_inr_format():
    assert pricing.format_inr(2400) == "₹2,400"
    assert pricing.format_inr(123456) == "₹1,23,456"


def test_prob_fail_within_monotone():
    r = {"low": 20, "mid": 40, "high": 70}
    ps = [rul.prob_fail_within(r, d) for d in (5, 20, 40, 70, 200)]
    assert ps == sorted(ps) and abs(ps[1] - 0.05) < 1e-6 and abs(ps[2] - 0.5) < 1e-6
    assert rul.prob_fail_within({"low": 0, "mid": 0, "high": 0}) == 1.0
