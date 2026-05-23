"""
dashboard/streamlit_app.py
───────────────────────────
Fraud AI Investigator — Streamlit Dashboard (Phase 6)

A production-grade analyst interface connecting to the live FastAPI backend.
All data comes from the API — the dashboard is purely a presentation layer.

Run:
    uv run streamlit run dashboard/streamlit_app.py

Requires the API running at localhost:8000:
    uv run uvicorn app.main:app --reload
"""

import time
import requests
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime

# ── Page configuration ────────────────────────────────────────────────────────
st.set_page_config(
    page_title = "Fraud AI Investigator",
    page_icon  = "🔍",
    layout     = "wide",
    initial_sidebar_state = "expanded",
)

# ── Constants ─────────────────────────────────────────────────────────────────
import os
API_BASE = os.getenv("API_BASE_URL", "http://localhost:8000")

RISK_COLORS = {
    "LOW"     : "#4CAF50",
    "MEDIUM"  : "#FF9800",
    "HIGH"    : "#F44336",
    "CRITICAL": "#880E4F",
}

STATUS_COLORS = {
    "PENDING"         : "#9E9E9E",
    "TRIAGING"        : "#2196F3",
    "INVESTIGATING"   : "#FF9800",
    "AWAITING_HUMAN"  : "#E91E63",
    "AUTO_CLOSED"     : "#4CAF50",
    "FRAUD_CONFIRMED" : "#D32F2F",
    "FALSE_POSITIVE"  : "#8BC34A",
}

# ── API helpers ───────────────────────────────────────────────────────────────

def api_get(path: str, params: dict = None) -> dict | list | None:
    """GET request with error handling. Returns None on failure."""
    try:
        r = requests.get(f"{API_BASE}{path}", params=params, timeout=10)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.ConnectionError:
        st.error("⚠️ Cannot connect to API. Start it with: `uv run uvicorn app.main:app --reload`")
        return None
    except Exception as e:
        st.error(f"API error: {e}")
        return None


def api_post(path: str, body: dict = None, timeout: int = 120) -> dict | None:
    """POST request with error handling."""
    try:
        r = requests.post(f"{API_BASE}{path}", json=body or {}, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.ConnectionError:
        st.error("⚠️ Cannot connect to API.")
        return None
    except Exception as e:
        st.error(f"API error: {e}")
        return None


# ── Sidebar ───────────────────────────────────────────────────────────────────

def render_sidebar():
    with st.sidebar:
        st.markdown("## 🔍 Fraud AI Investigator")
        st.caption("UAE/MENA AML Platform")
        st.divider()

        # API health indicator
        health = api_get("/health")
        if health:
            st.success(f"API Online — v{health.get('version', '?')}")
            providers = health.get("llm_providers", {})
            col1, col2 = st.columns(2)
            col1.metric("Gemini", "✓" if providers.get("gemini") else "✗")
            col2.metric("Groq",   "✓" if providers.get("groq")   else "✗")
        else:
            st.error("API Offline")

        st.divider()
        page = st.radio(
            "Navigation",
            ["📊 Dashboard", "🚨 Alerts", "🤖 Investigate", "👤 HITL Review",
             "🧠 Fraud Memory", "₿ Crypto", "⚙️ Settings"],
            label_visibility="collapsed",
        )
        st.divider()
        st.caption("Phase 6 — Streamlit Dashboard")
        st.caption("[API Docs →](http://localhost:8000/docs)")

    return page


# ── Page: Dashboard ───────────────────────────────────────────────────────────

def page_dashboard():
    st.title("📊 Dashboard")
    st.caption(f"Live system overview · Refreshed at {datetime.now().strftime('%H:%M:%S')}")

    # Top metrics row
    inv_stats = api_get("/v1/investigate/stats") or {}
    mem_stats = api_get("/v1/hitl/memory/stats") or {}

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Total Alerts",      inv_stats.get("total_alerts", 0))
    col2.metric("Awaiting Review",   inv_stats.get("awaiting_human", 0),  delta_color="inverse")
    col3.metric("Auto Closed",       inv_stats.get("auto_closed", 0))
    col4.metric("Confirmed Fraud",   mem_stats.get("confirmed_fraud", 0), delta_color="inverse")
    col5.metric("Avg Risk Score",    inv_stats.get("average_risk_score") or "—")

    st.divider()

    col_left, col_right = st.columns(2)

    with col_left:
        st.subheader("Alert Status Distribution")
        status_data = {
            k: inv_stats.get(k.lower(), 0)
            for k in ["PENDING", "INVESTIGATING", "AWAITING_HUMAN",
                      "AUTO_CLOSED", "FRAUD_CONFIRMED", "FALSE_POSITIVE"]
        }
        status_data = {k: v for k, v in status_data.items() if v > 0}
        if status_data:
            fig = px.bar(
                x=list(status_data.keys()),
                y=list(status_data.values()),
                color=list(status_data.keys()),
                color_discrete_map=STATUS_COLORS,
            )
            fig.update_layout(showlegend=False, height=300,
                              margin=dict(t=10, b=10, l=10, r=10))
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No alert data yet. Generate alerts to see distribution.")

    with col_right:
        st.subheader("Fraud Memory Outcomes")
        total = mem_stats.get("total_cases", 0)
        if total > 0:
            labels = ["Confirmed Fraud", "False Positive", "Escalated"]
            values = [
                mem_stats.get("confirmed_fraud", 0),
                mem_stats.get("false_positives", 0),
                mem_stats.get("escalated", 0),
            ]
            fig2 = px.pie(
                names=labels, values=values,
                color=labels,
                color_discrete_sequence=["#D32F2F", "#8BC34A", "#FF9800"],
            )
            fig2.update_layout(height=300, margin=dict(t=10, b=10, l=10, r=10))
            st.plotly_chart(fig2, use_container_width=True)
        else:
            st.info("No HITL decisions recorded yet.")

    # HITL queue preview
    st.subheader("🚨 Alerts Awaiting Review")
    queue = api_get("/v1/hitl/queue")
    if queue and queue.get("alerts"):
        for a in queue["alerts"][:5]:
            band  = a.get("risk_band", "MEDIUM")
            color = RISK_COLORS.get(band, "#888")
            st.markdown(
                f'<div style="border-left: 4px solid {color}; padding: 8px 12px; '
                f'margin: 4px 0; background: #1e1e1e; border-radius: 4px;">'
                f'<b>{a["trigger"]}</b> · Customer {a["customer_id"]} · '
                f'Score <b>{a.get("risk_score", "?")}</b> · {band}</div>',
                unsafe_allow_html=True,
            )
    else:
        st.success("✓ No alerts awaiting review")


# ── Page: Alerts ──────────────────────────────────────────────────────────────

def page_alerts():
    st.title("🚨 Alert Management")

    # Controls
    col1, col2, col3 = st.columns([2, 2, 1])
    with col1:
        status_filter = st.selectbox(
            "Filter by status",
            ["All", "PENDING", "INVESTIGATING", "AWAITING_HUMAN",
             "AUTO_CLOSED", "FRAUD_CONFIRMED", "FALSE_POSITIVE"],
        )
    with col2:
        trigger_filter = st.selectbox(
            "Filter by trigger",
            ["All", "HIGH_VALUE", "SANCTIONED_CORRIDOR",
             "DEVICE_MISMATCH", "NEW_ACCOUNT"],
        )
    with col3:
        limit = st.number_input("Limit", min_value=10, max_value=200,
                                value=50, step=10)

    st.divider()

    # Generate alerts section
    with st.expander("⚡ Generate Alerts from Transactions"):
        col_a, col_b, col_c = st.columns(3)
        gen_limit = col_a.number_input("Transactions to evaluate", 5, 100, 20)
        source    = col_b.selectbox("Data source", ["auto", "ieee", "synthetic"])
        flagged   = col_c.checkbox("Flagged only", value=False)
        if st.button("Generate Alerts", type="primary"):
            with st.spinner("Running alert engine..."):
                result = api_post(
                    "/v1/alerts/generate",
                    {"limit": gen_limit, "source": source, "flagged_only": flagged},
                )
            if result:
                st.success(
                    f"✓ Created {result['alerts_created']} alerts "
                    f"from {result['data_source']} data"
                )
                st.rerun()

    # Alert list
    params = {"limit": limit}
    if status_filter  != "All": params["status"]  = status_filter
    if trigger_filter != "All": params["trigger"] = trigger_filter

    data = api_get("/v1/alerts", params=params)
    if not data:
        return

    alerts = data.get("alerts", [])
    st.caption(f"Showing {len(alerts)} of {data.get('total', 0)} total alerts")

    if not alerts:
        st.info("No alerts match the current filter.")
        return

    df = pd.DataFrame(alerts)
    df["created_at"] = pd.to_datetime(df["created_at"]).dt.strftime("%Y-%m-%d %H:%M")

    # Colour-coded status column
    def colour_status(val):
        color = STATUS_COLORS.get(val, "#888")
        return f"background-color: {color}22; color: {color}"

    display_cols = [c for c in
                    ["alert_id", "trigger", "customer_id", "status",
                     "risk_score", "risk_band", "created_at"]
                    if c in df.columns]

    styled = df[display_cols].style.map(colour_status, subset=["status"])
    st.dataframe(styled, use_container_width=True, hide_index=True)


# ── Page: Investigate ─────────────────────────────────────────────────────────

def page_investigate():
    st.title("🤖 LangGraph Investigation")
    st.caption("Run 5-agent parallel investigation on alerts")

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Batch Investigation")
        max_alerts = st.slider("Max alerts to investigate", 1, 5, 2)
        wallet     = st.text_input(
            "Wallet address (optional)",
            placeholder="0x... — adds crypto agent to graph",
        )
        if st.button("Run Batch Investigation", type="primary"):
            with st.spinner(
                f"Running 5-agent LangGraph investigation on {max_alerts} alert(s)... "
                f"(~{max_alerts * 5}s)"
            ):
                result = api_post(
                    "/v1/investigate/batch",
                    {
                        "max_alerts"    : max_alerts,
                        "wallet_address": wallet or None,
                    },
                    timeout=180,
                )
            if result:
                st.success(
                    f"✓ Investigated {result['investigated']} alerts — "
                    f"{result['succeeded']} succeeded"
                )
                for r in result.get("results", []):
                    status = "✓" if r["status"] == "completed" else "✗"
                    score  = r.get("final_risk_score", "?")
                    rec    = r.get("recommendation", "?")
                    st.markdown(
                        f"{status} **{r['alert_id'][:8]}...** — "
                        f"Score: `{score}` · Recommendation: `{rec}`"
                    )

    with col2:
        st.subheader("Investigation Stats")
        stats = api_get("/v1/investigate/stats")
        if stats:
            st.metric("Graph Compiled", "✓ Yes" if stats.get("graph_compiled") else "✗ No")
            st.metric("Total Investigated", stats.get("investigated_count", 0))
            st.metric("Avg Risk Score", stats.get("average_risk_score") or "—")

    st.divider()

    # Single alert investigation
    st.subheader("Investigate Single Alert")
    alert_id = st.text_input("Alert ID", placeholder="Paste an alert UUID")
    if alert_id and st.button("Investigate This Alert"):
        with st.spinner("Running investigation..."):
            result = api_post(f"/v1/investigate/{alert_id}", {}, timeout=60)
        if result:
            st.success(f"Investigation complete!")
            col_a, col_b, col_c = st.columns(3)
            col_a.metric("Risk Score",    result.get("final_risk_score", "?"))
            col_b.metric("Risk Band",     result.get("final_risk_band",  "?"))
            col_c.metric("Recommendation",result.get("recommendation",   "?"))
            st.markdown("**Investigation Summary:**")
            st.info(result.get("investigation_summary", "No summary available."))
            st.caption(f"Agents: {', '.join(result.get('agents_completed', []))}")


# ── Page: HITL Review ─────────────────────────────────────────────────────────

def page_hitl():
    st.title("👤 HITL Analyst Review")
    st.caption("Human-in-the-loop verdict submission")

    # Queue
    queue = api_get("/v1/hitl/queue")
    if not queue:
        return

    q_len   = queue.get("queue_length", 0)
    q_alerts= queue.get("alerts", [])

    if q_len == 0:
        st.success("✓ Review queue is empty — no alerts awaiting analyst decision.")
        return

    st.warning(f"⚠️ {q_len} alert(s) awaiting analyst review")

    # Select alert from queue
    alert_options = {
        f"{a['alert_id'][:8]}... | {a['trigger']} | Score {a.get('risk_score','?')}": a["alert_id"]
        for a in q_alerts
    }
    selected_label = st.selectbox("Select alert to review", list(alert_options.keys()))
    selected_id    = alert_options[selected_label]

    # Load context
    context = api_get(f"/v1/hitl/{selected_id}/context")
    if not context:
        return

    alert = context.get("alert", {})

    # Alert details
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Risk Score",   alert.get("risk_score", "?"))
    col2.metric("Risk Band",    alert.get("risk_band",  "?"))
    col3.metric("Trigger",      alert.get("trigger",    "?"))
    col4.metric("Customer",     alert.get("customer_id","?"))

    st.markdown("**Investigation Summary:**")
    st.info(alert.get("investigation_summary") or "Not yet investigated — run investigation first.")

    # Similar past cases
    past = context.get("similar_past_cases", [])
    if past:
        st.subheader(f"📁 Similar Past Cases ({len(past)})")
        for case in past:
            verdict = case["verdict"]
            color   = "#D32F2F" if verdict == "CONFIRMED_FRAUD" else "#8BC34A"
            st.markdown(
                f'<div style="border-left: 3px solid {color}; padding: 6px 10px; '
                f'margin: 4px 0; background: #1a1a1a; border-radius: 3px; font-size: 13px;">'
                f'<b>{verdict}</b> · Customer {case["customer_id"]} · '
                f'Score {case.get("risk_score","?")} · {case.get("recorded_at","")[:10]}<br>'
                f'<i>{case.get("analyst_notes","")[:100]}</i></div>',
                unsafe_allow_html=True,
            )

    # Regulatory guidance
    guidance = context.get("regulatory_guidance", [])
    if guidance:
        with st.expander("⚖️ Regulatory Guidance"):
            for g in guidance:
                st.markdown(f"• {g}")

    st.divider()

    # Verdict form
    st.subheader("Submit Verdict")
    with st.form("hitl_form"):
        verdict  = st.selectbox(
            "Verdict *",
            ["CONFIRMED_FRAUD", "FALSE_POSITIVE", "ESCALATED", "NEEDS_MORE_INFO"],
        )
        analyst  = st.text_input("Analyst ID *", placeholder="your.name@bank.ae")
        notes    = st.text_area(
            "Decision Notes * (min 20 characters)",
            placeholder=(
                "Explain your decision — signals considered, "
                "evidence reviewed, regulatory basis..."
            ),
            height=120,
        )
        submitted = st.form_submit_button("Submit Verdict", type="primary")

    if submitted:
        if not analyst:
            st.error("Analyst ID is required.")
        elif len(notes.strip()) < 20:
            st.error("Notes must be at least 20 characters.")
        else:
            with st.spinner("Submitting verdict..."):
                result = api_post(
                    f"/v1/hitl/{selected_id}/decision",
                    {
                        "verdict": verdict,
                        "analyst": analyst,
                        "notes"  : notes,
                    },
                )
            if result:
                if result.get("str_required"):
                    st.error(
                        f"⚠️ STR REQUIRED — file within 2 working days per CBUAE AML/CFT. "
                        f"Memory ID: {result['memory_id']}"
                    )
                else:
                    st.success(
                        f"✓ Verdict recorded — {result['verdict']} · "
                        f"Memory ID: {result['memory_id']}"
                    )
                time.sleep(1)
                st.rerun()


# ── Page: Fraud Memory ────────────────────────────────────────────────────────

def page_memory():
    st.title("🧠 Fraud Memory")
    st.caption("Institutional learning from confirmed analyst decisions")

    stats = api_get("/v1/hitl/memory/stats") or {}
    cases_data = api_get("/v1/hitl/memory/cases") or {}

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Cases",      stats.get("total_cases", 0))
    col2.metric("Confirmed Fraud",  stats.get("confirmed_fraud", 0))
    col3.metric("False Positives",  stats.get("false_positives", 0))
    col4.metric("Unique Customers", stats.get("unique_customers", 0))

    cases = cases_data.get("cases", [])
    if not cases:
        st.info(
            "No cases in fraud memory yet. Submit HITL verdicts to populate memory. "
            "Past cases inform future LangGraph investigations."
        )
        return

    st.subheader("Case History")
    df = pd.DataFrame(cases)
    if "recorded_at" in df.columns:
        df["recorded_at"] = pd.to_datetime(df["recorded_at"]).dt.strftime("%Y-%m-%d %H:%M")

    display = [c for c in
               ["memory_id","verdict","customer_id","trigger",
                "risk_score","analyst","recorded_at"]
               if c in df.columns]
    st.dataframe(df[display], use_container_width=True, hide_index=True)


# ── Page: Crypto ──────────────────────────────────────────────────────────────

def page_crypto():
    st.title("₿ Crypto Monitoring")
    st.caption("On-chain mixer detection via Etherscan V2 API")

    status = api_get("/v1/crypto/status") or {}
    key_ok = status.get("etherscan_api_configured", False)

    if not key_ok:
        st.warning(
            "⚠️ Etherscan API key not configured. "
            "Add `ETHERSCAN_API_KEY` to `.env` — free at etherscan.io/myapikey"
        )

    col1, col2, col3 = st.columns(3)
    col1.metric("API Configured",  "✓" if key_ok else "✗")
    col2.metric("Score Threshold", status.get("score_threshold", "?"))
    col3.metric("Known Mixers",    status.get("known_mixer_addresses", "?"))

    st.divider()

    # Wallet screening
    st.subheader("Screen Wallet Address")
    wallet = st.text_input("Ethereum address", placeholder="0x...")
    cust   = st.text_input("Customer ID (optional)", value="MANUAL-SCREEN")

    if wallet and st.button("Screen Address", type="primary", disabled=not key_ok):
        with st.spinner(f"Screening {wallet[:10]}... via Etherscan..."):
            result = api_post(
                "/v1/crypto/screen",
                {"address": wallet, "customer_id": cust, "limit": 50},
                timeout=30,
            )
        if result:
            screening = result.get("screening", {})
            score     = screening.get("risk_score", 0)
            flagged   = screening.get("is_flagged", False)
            severity  = screening.get("severity", "LOW")
            color     = RISK_COLORS.get(severity, "#888")

            st.markdown(
                f'<div style="border-left: 5px solid {color}; padding: 12px 16px; '
                f'background: #1a1a1a; border-radius: 4px; margin: 8px 0;">'
                f'<b>{"🔴 FLAGGED" if flagged else "🟢 CLEAR"}</b> — '
                f'Score: <b>{score}/100</b> · Severity: {severity} · '
                f'Alert created: {result.get("alert_created", False)}</div>',
                unsafe_allow_html=True,
            )

            signals = screening.get("signals", [])
            if signals:
                st.subheader("Detected Signals")
                for s in signals:
                    st.markdown(f"**{s['type']}** (score: {s['score']}): {s['description']}")

    # Known mixers
    st.divider()
    st.subheader("Known Sanctioned Addresses")
    mixers = api_get("/v1/crypto/mixers")
    if mixers:
        for m in mixers.get("mixers", []):
            st.markdown(
                f"🚫 **{m['name']}** · `{m['address']}` · "
                f"{m['sanction']} ({m['date_listed']})"
            )


# ── Page: Settings ────────────────────────────────────────────────────────────

def page_settings():
    st.title("⚙️ Settings")

    health = api_get("/health") or {}
    ds     = api_get("/v1/alerts/datasource") or {}

    st.subheader("System Status")
    col1, col2 = st.columns(2)
    with col1:
        st.json({
            "api_status"   : health.get("status", "unknown"),
            "api_version"  : health.get("version", "?"),
            "environment"  : health.get("environment", "?"),
            "llm_providers": health.get("llm_providers", {}),
        })
    with col2:
        st.json({
            "active_source"     : ds.get("active_source", "unknown"),
            "ieee_cis_available": ds.get("ieee_cis_available", False),
            "ieee_cis_count"    : ds.get("ieee_cis_count", 0),
            "synthetic_count"   : ds.get("synthetic_count", 0),
        })

    st.subheader("Quick Actions")
    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("Verify OFAC Data"):
            from pathlib import Path
            sdn = Path("app/data/sanctions/ofac_sdn.json")
            if sdn.exists():
                import json
                data = json.loads(sdn.read_text())
                st.success(f"✓ OFAC SDN loaded — {len(data):,} entities")
            else:
                st.warning("OFAC SDN not found. Run: `uv run python scripts/load_ofac_data.py --sample`")
    with col_b:
        if st.button("API Docs"):
            st.markdown("[Open Swagger UI →](http://localhost:8000/docs)")


# ── Router ────────────────────────────────────────────────────────────────────

def main():
    page = render_sidebar()

    if   page == "📊 Dashboard":   page_dashboard()
    elif page == "🚨 Alerts":      page_alerts()
    elif page == "🤖 Investigate": page_investigate()
    elif page == "👤 HITL Review": page_hitl()
    elif page == "🧠 Fraud Memory":page_memory()
    elif page == "₿ Crypto":       page_crypto()
    elif page == "⚙️ Settings":    page_settings()


if __name__ == "__main__":
    main()
