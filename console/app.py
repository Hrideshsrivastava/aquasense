"""AquaSense Company Console (Unit C, build plan section 6).

    streamlit run console/app.py

Screens: Fleet view, Unit detail (live charts, health, RUL, why), Alerts and
tickets (Contact customer -> job card, Scenario 1), Warranty and parts, Model.
The console talks to the Flask server's JSON API (default http://localhost:5000).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from aquasense import pricing, schema  # noqa: E402

st.set_page_config(page_title="AquaSense Console", page_icon="💧", layout="wide")

# Reference palette (dataviz skill): categorical slots + reserved status colours.
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
STATUS = {"normal": ("#0ca30c", "✅", "Normal"), "warning": ("#fab219", "⚠️", "Warning"),
          "replace": ("#d03b3b", "⛔", "Replace"), "no_data": ("#8a8a85", "⏳", "No data")}
GRID = "rgba(128,128,128,0.18)"

DEFAULT_SERVER = os.environ.get("AQUASENSE_SERVER", "http://localhost:5000")


# ------------------------------------------------------------------ API
def api(path: str, method: str = "GET", **kw):
    url = st.session_state.get("server", DEFAULT_SERVER).rstrip("/") + path
    r = requests.request(method, url, timeout=15, **kw)
    r.raise_for_status()
    return r.json() if "json" in r.headers.get("content-type", "") else r.text


def status_badge(status: str) -> str:
    color, icon, label = STATUS.get(status, STATUS["no_data"])
    return f"{icon} :{'green' if status == 'normal' else 'orange' if status == 'warning' else 'red' if status == 'replace' else 'gray'}[**{label}**]"


def rul_text(r: dict | None) -> str:
    if not r:
        return "n/a"
    if r.get("high", 0) <= 0:
        return "replace now"
    return f"{r['low']}–{r['high']} days"


def base_layout(fig: go.Figure, title: str, height: int = 230, ytitle: str = ""):
    fig.update_layout(title=dict(text=title, font=dict(size=14)), height=height, margin=dict(l=10, r=10, t=36, b=10),
                      hovermode="x unified", showlegend=False, yaxis_title=ytitle)
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(gridcolor=GRID, zeroline=False)
    return fig


# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.markdown("## 💧 AquaSense")
    st.caption("Atomberg service console · prototype")
    st.session_state["server"] = st.text_input("Cloud server", st.session_state.get("server", DEFAULT_SERVER))
    page = st.radio("Screen", ["Fleet view", "Unit detail", "Alerts & tickets", "Warranty & parts", "Model"],
                    key="page")
    live = st.toggle("Live refresh (5 s)", value=True)
    st.divider()
    st.caption("Simulated fleet units and all RUL ranges use simulated degradation data, calibrated from the rig.")

try:
    health = api("/api/health")
except Exception as e:  # pragma: no cover - UI path
    st.error(f"Cannot reach the cloud server at {st.session_state['server']}: {e}\n\nStart it with `python -m cloud.server`.")
    st.stop()

RUN_EVERY = 5 if live else None


# ------------------------------------------------------------------ fleet
def fleet_view():
    st.title("Fleet view")
    units = api("/api/units")
    if not units:
        st.info("No units yet. Start the rig (or `python -m simulator.virtual_rig`) and seed the fleet with "
                "`python -m simulator.seed_fleet`.")
        return
    alerts = [u["open_alerts"] for u in units]
    c = st.columns(4)
    c[0].metric("Units", len(units), f"{sum(not u['simulated'] for u in units)} live rig")
    c[1].metric("Open alerts", sum(alerts))
    c[2].metric("Replace now", sum(u.get("status") == "replace" for u in units))
    c[3].metric("Expected repair cost", pricing.format_inr(sum(u.get("expected_cost") or 0 for u in units)))

    rows = []
    for u in units:
        color, icon, label = STATUS.get(u.get("status", "no_data"), STATUS["no_data"])
        rows.append({
            "Status": f"{icon} {label}",
            "Unit": u["id"],
            "Customer": u["customer"],
            "City": u["city"],
            "Source": u["water_source"],
            "Diagnosis": u.get("fault_name", ""),
            "Confidence": u.get("confidence"),
            "Lowest health": u.get("min_health"),
            "Weakest part": u.get("worst_component", ""),
            "RUL (weakest)": rul_text(u.get("worst_rul")),
            "Repair now": pricing.format_inr(u.get("repair_price_now") or 0),
            "Alerts": u["open_alerts"],
            "Link": ("🟢 live" if u["online"] else "⚪ offline") if not u["simulated"] else "simulated",
        })
    df = pd.DataFrame(rows)
    st.dataframe(df, hide_index=True, width="stretch", column_config={
        "Lowest health": st.column_config.ProgressColumn("Lowest health", min_value=0, max_value=100, format="%.0f%%"),
        "Confidence": st.column_config.NumberColumn(format="percent"),
    })
    pick = st.selectbox("Open unit", [u["id"] for u in units], key="fleet_pick")
    st.button("Open unit detail →", on_click=lambda: st.session_state.update(unit=pick, page="Unit detail"))


# ------------------------------------------------------------------ unit detail
def health_bars(components: list[dict]):
    for comp in components:
        color, icon, label = STATUS[comp["status"]]
        cols = st.columns([2.2, 4, 1.2, 2.2])
        cols[0].markdown(f"**{comp['name']}**")
        cols[1].progress(min(1.0, max(0.0, comp["health"] / 100)))
        verdict = {"replace": "replace", "watch": "watch", "ok": "ok"}.get(comp.get("decision"), label.lower())
        cols[2].markdown(f"{comp['health']:.0f}% {icon} {verdict}")
        cols[3].markdown(f"RUL {rul_text(comp['rul'])}" if comp["component"] != "sensors" else
                         f"fault p={comp['failure_prob']:.2f}")


def live_charts(unit_id: str):
    rd = api(f"/api/units/{unit_id}/readings", params={"minutes": 15})
    if not rd:
        st.info("No readings yet.")
        return
    df = pd.DataFrame(rd)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms") if df["timestamp"].dtype != object else pd.to_datetime(df["timestamp"])
    cols = st.columns(3)
    for col, (sig, title, unit, color) in zip(cols, [("pressure_bar", "Pressure", "bar", BLUE),
                                                     ("flow_lpm", "Reject flow", "L/min", BLUE),
                                                     ("pump_current_a", "Pump current", "A", BLUE)]):
        fig = go.Figure(go.Scatter(x=df["timestamp"], y=df[sig], mode="lines", line=dict(color=color, width=2),
                                   name=title, hovertemplate=f"%{{y:.2f}} {unit}<extra></extra>"))
        col.plotly_chart(base_layout(fig, f"{title} ({unit})"), width="stretch", key=f"c_{sig}")
    cols = st.columns(2)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df["timestamp"], y=df["tds_in"], mode="lines", name="TDS in",
                             line=dict(color=BLUE, width=2), hovertemplate="%{y:.0f} ppm"))
    fig.add_trace(go.Scatter(x=df["timestamp"], y=df["tds_out"], mode="lines", name="TDS out",
                             line=dict(color=ORANGE, width=2), hovertemplate="%{y:.0f} ppm"))
    fig = base_layout(fig, "TDS in and out (ppm)")
    fig.update_layout(showlegend=True, legend=dict(orientation="h", y=1.15, x=1, xanchor="right"))
    cols[0].plotly_chart(fig, width="stretch", key="c_tds")
    on = df[df["pump_on"] == 1]
    rej = (1 - on["tds_out"] / on["tds_in"].clip(lower=1)) * 100
    fig = go.Figure(go.Scatter(x=on["timestamp"], y=rej, mode="markers", marker=dict(color=BLUE, size=8),
                               name="Rejection", hovertemplate="%{y:.1f}%<extra></extra>"))
    cols[1].plotly_chart(base_layout(fig, "Salt rejection % (pump on)"), width="stretch", key="c_rej")


def rul_chart(unit_id: str, components: list[dict]):
    h = api(f"/api/units/{unit_id}/history")
    comp_names = {c["component"]: c for c in components if c["component"] != "sensors"}
    pick = st.segmented_control("Component", list(comp_names), default=min(comp_names, key=lambda k: comp_names[k]["health"]),
                                format_func=lambda k: schema.COMPONENT_NAMES[k], key=f"rulpick_{unit_id}")
    if not pick:
        return
    series = h["series"].get(pick, [])
    c = comp_names[pick]
    fig = go.Figure()
    if series:
        days = pd.to_datetime([p["day"] for p in series])
        fig.add_trace(go.Scatter(x=days, y=[p["health"] for p in series], mode="lines", name="Observed",
                                 line=dict(color=BLUE, width=2), hovertemplate="%{y:.0f}%<extra>observed</extra>"))
        today = days[-1]
    else:
        today = pd.Timestamp.now().normalize()
    r = c["rul"]
    if r and r.get("high", 0) > 0:
        h0 = c["health"]
        fig.add_trace(go.Scatter(
            x=[today, today + pd.Timedelta(days=r["high"]), today + pd.Timedelta(days=r["low"]), today],
            y=[h0, schema.REPLACE_THRESHOLD, schema.REPLACE_THRESHOLD, h0], fill="toself",
            fillcolor="rgba(42,120,214,0.15)", line=dict(width=0), hoverinfo="skip", name="Forecast range"))
        fig.add_trace(go.Scatter(x=[today, today + pd.Timedelta(days=r["mid"])], y=[h0, schema.REPLACE_THRESHOLD],
                                 mode="lines", line=dict(color=BLUE, width=2, dash="dot"), name="Forecast",
                                 hovertemplate="%{y:.0f}%<extra>forecast</extra>"))
    fig.add_hline(y=schema.REPLACE_THRESHOLD, line=dict(color="#d03b3b", width=1, dash="dash"),
                  annotation_text="Replacement threshold (40%)", annotation_position="bottom left")
    fig = base_layout(fig, f"{schema.COMPONENT_NAMES[pick]}: RUL {rul_text(r)}", height=280, ytitle="Health %")
    fig.update_yaxes(range=[0, 105])
    st.plotly_chart(fig, width="stretch", key=f"rul_{unit_id}_{pick}")
    st.caption(("History: simulated degradation. " if h.get("simulated") else "History: daily average of live windows. ")
               + f"RUL method: {r.get('method') if r else 'n/a'} (trained on simulated run-to-failure data).")


def unit_detail():
    units = api("/api/units")
    if not units:
        st.info("No units yet.")
        return
    ids = [u["id"] for u in units]
    cur = st.session_state.get("unit", ids[0])
    unit_id = st.selectbox("Unit", ids, index=ids.index(cur) if cur in ids else 0,
                           format_func=lambda i: next(f"{u['id']} · {u['customer']} · {u['city']}" for u in units if u["id"] == i))
    st.session_state["unit"] = unit_id

    @st.fragment(run_every=RUN_EVERY)
    def body():
        d = api(f"/api/units/{unit_id}")
        u, a = d["unit"], d["assessment"]
        tag = "SIMULATED unit" if u["simulated"] else ("🟢 Live: receiving data" if d["online"] else "⚪ Offline: no data in the last 45 s")
        st.title(f"{u['id']} · {u['name']}")
        st.caption(f"{u['customer']} · {u['phone']} · {u['address']}, {u['city']} · source: {u['water_source']} · {tag}")
        if a.get("status") == "no_data":
            st.info(a["message"])
            return
        top = st.columns([3, 3])
        with top[0]:
            st.markdown(f"### {status_badge(a['status'])} &nbsp; {a['fault_name']} · {a['confidence']:.0%} confidence")
            st.write(a["reason"])
            for e in a["evidence"][:4]:
                st.markdown(f"- {e}")
            if a.get("explanation"):
                st.caption("Signals that drove the model (SHAP): " + ", ".join(x["label"] for x in a["explanation"]))
        with top[1]:
            q = a["quote"]
            m = st.columns(3)
            m[0].metric("TDS in (ppm)", f"{a['tds_in']:.0f}")
            m[1].metric("Rejection", f"{a['rejection_pct']:.1f}%")
            m[2].metric("Repair now", pricing.format_inr(q["repair_price_now"]))
            if a.get("counters"):
                k = a["counters"]
                st.caption(f"Pump {k['pump_runtime_h']:.0f} h · {k['pump_cycles']} starts · UV {k['uv_hours']:.0f} h · "
                           f"filter {k['filter_hours']:.0f} h")
        st.subheader("Component health")
        health_bars(a["components"])

        open_alerts = [x for x in d["alerts"] if x["status"] == "open"]
        if open_alerts:
            al = open_alerts[0]
            st.error(f"**ALERT** {al['fault_name']} ({al['confidence']:.0%}) raised {al['created_at']}: {al['reason']}")
        cc = st.columns([1, 1, 3])
        if cc[0].button("📞 Contact customer", type="primary", key=f"contact_{unit_id}"):
            t = api(f"/api/units/{unit_id}/contact", "POST", json={"alert_id": open_alerts[0]["id"] if open_alerts else None})
            st.session_state["last_ticket"] = t
        if open_alerts and cc[1].button("Acknowledge alert", key=f"ack_{unit_id}"):
            api(f"/api/alerts/{open_alerts[0]['id']}/ack", "POST")
        t = st.session_state.get("last_ticket")
        if t and t["unit_id"] == unit_id:
            st.success(f"Ticket #{t['id']} created · carry: {t['part_to_carry']} · estimate "
                       f"{pricing.format_inr(t['price_estimate'])} · {t['visit_window']}")
            st.code(t["job_card"], language=None)
            st.download_button("Download job card", t["job_card"], file_name=f"jobcard_{t['id']}.txt", key=f"dl_{t['id']}")

        st.subheader("Live sensors (last 15 min)")
        live_charts(unit_id)

    body()
    st.subheader("Remaining useful life")
    d = api(f"/api/units/{unit_id}")
    if d["assessment"].get("components"):
        rul_chart(unit_id, d["assessment"]["components"])


# ------------------------------------------------------------------ alerts & tickets
def alerts_tickets():
    st.title("Alerts & tickets")
    st.caption(f"An alert is raised when a fault is predicted with probability > {schema.ALERT_PROB} "
               f"for {schema.ALERT_CONSECUTIVE} consecutive windows.")

    @st.fragment(run_every=RUN_EVERY)
    def alerts_part():
        al = api("/api/alerts")
        if not al:
            st.info("No alerts yet.")
        else:
            df = pd.DataFrame(al)[["id", "status", "unit_id", "customer", "fault_name", "confidence", "reason", "created_at"]]
            st.dataframe(df, hide_index=True, width="stretch",
                         column_config={"confidence": st.column_config.NumberColumn(format="percent")})
    alerts_part()

    al = [a for a in api("/api/alerts") if a["status"] == "open"]
    if al:
        c = st.columns([3, 1, 1])
        pick = c[0].selectbox("Open alert", al, format_func=lambda a: f"#{a['id']} {a['unit_id']} · {a['fault_name']}")
        if c[1].button("📞 Contact customer", type="primary"):
            t = api(f"/api/units/{pick['unit_id']}/contact", "POST", json={"alert_id": pick["id"]})
            st.session_state["last_ticket"] = t
            st.rerun()
        if c[2].button("Acknowledge"):
            api(f"/api/alerts/{pick['id']}/ack", "POST")
            st.rerun()

    st.subheader("Service tickets")
    tk = api("/api/tickets")
    if not tk:
        st.info("No tickets yet. Press Contact customer on an alert.")
        return
    df = pd.DataFrame(tk)[["id", "status", "unit_id", "customer", "phone", "fault", "part_to_carry", "price_estimate",
                           "visit_window", "created_at"]]
    df["price_estimate"] = df["price_estimate"].map(pricing.format_inr)
    st.dataframe(df, hide_index=True, width="stretch")
    c = st.columns([3, 2, 1])
    t = c[0].selectbox("Ticket", tk, format_func=lambda t: f"#{t['id']} {t['unit_id']} · {t['fault']} · {t['status']}")
    new = c[1].selectbox("Set status", ["open", "scheduled", "done"], index=["open", "scheduled", "done"].index(t["status"]))
    if c[2].button("Update"):
        api(f"/api/tickets/{t['id']}", "PATCH", json={"status": new})
        st.rerun()
    with st.expander(f"Job card #{t['id']}", expanded=False):
        st.code(t["job_card"], language=None)
        st.download_button("Download job card", t["job_card"], file_name=f"jobcard_{t['id']}.txt")


# ------------------------------------------------------------------ warranty
def warranty_view():
    st.title("Warranty & parts")
    st.caption("Same pricing logic as the technician handheld: expected cost = Σ failure probability × part cost.")
    w = api("/api/warranty")
    c = st.columns(3)
    c[0].metric("Expected repair cost (fleet)", pricing.format_inr(w["expected_cost_total"]))
    c[1].metric("Repairs due now", pricing.format_inr(w["repair_now_total"]))
    c[2].metric("Units", len(w["units"]))
    parts = pd.DataFrame(w["parts"])
    cols = st.columns(2)
    fig = go.Figure(go.Bar(x=parts["part"], y=parts["expected_units"], marker=dict(color=BLUE, cornerradius=4),
                           hovertemplate="%{x}: %{y:.2f} expected<extra></extra>", text=parts["likely_needed"],
                           texttemplate="%{text} due now", textposition="outside"))
    fig = base_layout(fig, "Parts forecast: expected units (Σ failure probability)", height=320)
    fig.update_layout(hovermode="closest", bargap=0.45)
    cols[0].plotly_chart(fig, width="stretch")
    fig = go.Figure(go.Bar(x=parts["part"], y=parts["expected_cost"], marker=dict(color=BLUE, cornerradius=4),
                           hovertemplate="%{x}: ₹%{y:,.0f}<extra></extra>"))
    fig = base_layout(fig, "Expected cost by part (₹)", height=320)
    fig.update_layout(hovermode="closest", bargap=0.45)
    cols[1].plotly_chart(fig, width="stretch")
    st.subheader("Per unit")
    df = pd.DataFrame(w["units"])
    if not df.empty:
        df["replace_now"] = df["replace_now"].map(lambda x: ", ".join(x) or "—")
        df["watch_list"] = df["watch_list"].map(lambda x: ", ".join(x) or "—")
        df["repair_price_now"] = df["repair_price_now"].map(pricing.format_inr)
        df["expected_cost"] = df["expected_cost"].map(pricing.format_inr)
        st.dataframe(df, hide_index=True, width="stretch")
    st.subheader("Parts table (placeholder prices)")
    st.dataframe(pd.DataFrame([{"Part": v[0], "Cost": pricing.format_inr(v[1])} for v in pricing.PARTS.values()] +
                              [{"Part": "Labour", "Cost": pricing.format_inr(pricing.LABOUR)},
                               {"Part": "Visit charge", "Cost": pricing.format_inr(pricing.VISIT_CHARGE)}]),
                 hide_index=True)


# ------------------------------------------------------------------ model
def model_view():
    st.title("Model card")
    card = health["model"]
    if card.get("simulated"):
        st.warning("Models are currently trained on SYNTHETIC rig data. Retrain with real recordings: "
                   "`python -m ml.train --real data/rig_runs`.")
    c = st.columns(4)
    c[0].metric("Macro F1 (held-out runs)", f"{card.get('macro_f1_with_cross_check', 0):.2f}")
    c[1].metric("Run-level accuracy", f"{card.get('run_level_accuracy', 0):.0%}")
    c[2].metric("High-TDS false alarms", f"{(card.get('high_tds_normal_false_alarm_rate') or 0):.0%}")
    c[3].metric("Runs", card.get("runs", 0))
    rep = ROOT / "reports" / "training_report.md"
    if rep.exists():
        st.markdown(rep.read_text())


{"Fleet view": lambda: st.fragment(fleet_view, run_every=RUN_EVERY)(), "Unit detail": unit_detail,
 "Alerts & tickets": alerts_tickets, "Warranty & parts": warranty_view, "Model": model_view}[page]()
