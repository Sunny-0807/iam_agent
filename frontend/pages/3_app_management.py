"""
Page 3 — Application Management
Tabs: Onboarding Pipeline (4 agents) | Decommission App
"""
import asyncio
import csv
import io
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st
from shared.graph_client import GraphClient
from shared.config import config

st.set_page_config(page_title="Application Management — Agentic IAM", page_icon="🖥️", layout="wide")
st.title("🖥️ Application Management")

client = GraphClient()

PROJECT_ROOT  = Path(__file__).resolve().parent.parent.parent
APP_INBOX     = Path(os.getenv("APP_WATCHER_INBOX", str(PROJECT_ROOT / "watched_apps_inbox")))
APP_INBOX.mkdir(parents=True, exist_ok=True)


@st.cache_data(ttl=300, show_spinner="Loading groups...")
def fetch_groups():
    return asyncio.run(client.list_groups())


def _valid_https(url):
    return bool(re.match(r"^https://[a-zA-Z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+$", url or ""))


def _valid_entity(eid):
    return (eid or "").startswith(("https://", "urn:"))


tab_pipeline, tab_decom = st.tabs(["🔄 Onboarding Pipeline", "➖ Decommission App"])


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — ONBOARDING PIPELINE
# ══════════════════════════════════════════════════════════════════════════════
with tab_pipeline:
    st.subheader("Application Onboarding Pipeline")

    from ai_engine.agent_pipeline import (
        PipelineState, PipelineType, PipelineMode, AgentStatus,
        run_collection_agent, run_analysis_agent,
        run_decision_agent, run_execution_agent,
    )

    mode_label = st.radio(
        "Pipeline mode",
        options=["stepwise", "auto"],
        format_func=lambda x: (
            "🔢 Step-by-step (manually advance each agent)"
            if x == "stepwise"
            else "⚡ Auto-run (all agents run automatically)"
        ),
        horizontal=True,
        key="app_pipeline_mode",
    )
    pipeline_mode = PipelineMode.AUTO if mode_label == "auto" else PipelineMode.STEPWISE

    st.divider()

    # ── Source ────────────────────────────────────────────────────────────────
    src_form, src_csv, src_folder = st.tabs(["📝 Single App Form", "📤 Upload CSV", "📁 Inbox Folder"])
    csv_content = csv_filename = None

    with src_form:
        with st.form("single_app_form"):
            c1, c2 = st.columns(2)
            s_name = c1.text_input("Display name *", placeholder="My SAML App")
            s_type = c2.radio("App type *", ["non_gallery", "gallery"], horizontal=True,
                              format_func=lambda x: "Non-gallery" if x=="non_gallery" else "Gallery")
            s_tid  = st.text_input("Template ID (gallery only)") if s_type == "gallery" else ""
            s_eid  = st.text_input("Entity ID *", placeholder="https://saml.example.com")
            s_aurl = st.text_input("Reply URL (ACS) *", placeholder="https://saml.example.com/sso")
            s_surl = st.text_input("Sign-on URL (optional)")
            s_owner_upn = st.text_input("Owner UPN *", placeholder="admin@company.com")
            all_grps    = fetch_groups()
            s_grp_names = st.multiselect("Assigned groups", [g["displayName"] for g in all_grps])
            form_sub    = st.form_submit_button("Build pipeline from form →", type="primary")

        if form_sub:
            errs = []
            if not s_name:                            errs.append("Display name required.")
            if s_type == "gallery" and not s_tid:     errs.append("Template ID required for gallery.")
            if not _valid_entity(s_eid):              errs.append("Valid Entity ID required (https:// or urn:).")
            if not _valid_https(s_aurl):              errs.append("Valid HTTPS Reply URL required.")
            if not s_owner_upn:                       errs.append("Owner UPN required.")
            for e in errs: st.error(e)
            if not errs:
                grp_str  = "|".join(s_grp_names)
                csv_content  = (
                    "display_name,app_type,template_id,entity_id,reply_url,"
                    "sign_on_url,owner_upn,assigned_group_names\n"
                    f"{s_name},{s_type},{s_tid},{s_eid},{s_aurl},{s_surl},{s_owner_upn},{grp_str}"
                )
                csv_filename = f"{s_name.replace(' ','_')}_pipeline.csv"
                st.success(f"✅ **{s_name}** ready for pipeline.")

    with src_csv:
        with st.expander("📄 CSV format", expanded=False):
            st.markdown("""
| Column | Required |
|---|---|
| `display_name` | ✅ |
| `app_type` | ✅ `gallery` or `non_gallery` |
| `entity_id` | ✅ https:// or urn: |
| `reply_url` | ✅ HTTPS |
| `owner_upn` | ✅ |
| `template_id` | gallery only |
| `sign_on_url` | optional |
| `assigned_group_names` | optional, pipe-separated |
            """)
        uploaded = st.file_uploader("Upload app CSV", type=["csv"], key="app_csv_upload")
        if uploaded:
            csv_content  = uploaded.read().decode("utf-8")
            csv_filename = uploaded.name
            st.success(f"✅ {csv_filename} loaded.")

    with src_folder:
        st.info(
            f"Inbox: `{APP_INBOX}`\n\n"
            "For automated processing:\n```\npython app_bot/triggers/app_folder_watcher.py\n```",
            icon="📁",
        )
        inbox_files = sorted(APP_INBOX.glob("*.csv"))
        if inbox_files:
            sel = st.selectbox("Pick inbox file:", ["(select)"]+[f.name for f in inbox_files], key="app_inbox_sel")
            if sel != "(select)":
                csv_content  = (APP_INBOX / sel).read_text(encoding="utf-8")
                csv_filename = sel
                st.success(f"✅ {sel} selected.")

    if not csv_content:
        st.info("Fill the form, upload a CSV, or select an inbox file to start the pipeline.", icon="⬆️")
        st.stop()

    # ── PipelineState ─────────────────────────────────────────────────────────
    PS_KEY = "app_ps"
    FN_KEY = "app_ps_file"
    if PS_KEY not in st.session_state or st.session_state.get(FN_KEY) != csv_filename:
        st.session_state[PS_KEY] = PipelineState(
            pipeline_type=PipelineType.APP,
            mode=pipeline_mode,
        )
        st.session_state[FN_KEY] = csv_filename

    ps: PipelineState = st.session_state[PS_KEY]
    ps.mode = pipeline_mode

    # Status bar
    def _icon(s: AgentStatus) -> str:
        return {"idle":"⬜","running":"⏳","done":"✅","failed":"❌","waiting":"⚠️"}.get(s.value,"⬜")

    c1,c2,c3,c4 = st.columns(4)
    c1.write(f"{_icon(ps.collection_status)} **Agent 1** — Collection")
    c2.write(f"{_icon(ps.analysis_status)}   **Agent 2** — Analysis")
    c3.write(f"{_icon(ps.decision_status)}   **Agent 3** — Decision")
    c4.write(f"{_icon(ps.execution_status)}  **Agent 4** — Execution")
    st.divider()

    if ps.error:
        st.error(f"❌ {ps.error}")
        if st.button("🔄 Reset", key="app_reset_err"):
            del st.session_state[PS_KEY]; st.rerun()
        st.stop()

    # ── Agent 1 ───────────────────────────────────────────────────────────────
    st.markdown("### 📥 Agent 1 — Collection")
    if ps.collection_status == AgentStatus.IDLE:
        if st.button("▶ Run Collection Agent", type="primary", key="app_coll_btn"):
            with st.spinner("Collecting..."):
                asyncio.run(run_collection_agent(ps, "csv_upload", csv_content, csv_filename))
                st.session_state[PS_KEY] = ps
            st.rerun()

    if ps.collection_status == AgentStatus.DONE and ps.collection:
        st.success(f"✅ {ps.collection.row_count} row(s) from `{ps.collection.filename}`")
        with st.expander("Preview", expanded=False):
            st.dataframe(ps.collection.rows[:5], use_container_width=True, hide_index=True)
        if ps.mode == PipelineMode.AUTO and ps.analysis_status == AgentStatus.IDLE:
            with st.spinner("Auto-running Analysis Agent..."):
                asyncio.run(run_analysis_agent(ps)); st.session_state[PS_KEY] = ps
            st.rerun()

    st.divider()

    # ── Agent 2 ───────────────────────────────────────────────────────────────
    st.markdown("### 🔍 Agent 2 — Analysis")
    if ps.collection_status == AgentStatus.DONE and ps.analysis_status == AgentStatus.IDLE:
        if ps.mode == PipelineMode.STEPWISE:
            if st.button("▶ Run Analysis Agent", type="primary", key="app_anal_btn"):
                with st.spinner("Validating..."):
                    asyncio.run(run_analysis_agent(ps)); st.session_state[PS_KEY] = ps
                st.rerun()

    if ps.analysis_status == AgentStatus.DONE and ps.analysis:
        a = ps.analysis
        if a.missing_columns:
            st.error(f"❌ Missing columns: {a.missing_columns}"); st.stop()
        col1,col2,col3 = st.columns(3)
        col1.metric("Total",    ps.collection.row_count)
        col2.metric("🔴 Errors",   len(a.error_rows))
        col3.metric("🟡 Warnings", len(a.warning_rows))
        if a.issues:
            st.dataframe([
                {"Row":i.row,"Identifier":i.upn,
                 "Severity":"🔴 Error" if i.severity=="error" else "🟡 Warning","Message":i.message}
                for i in a.issues
            ], use_container_width=True, hide_index=True)
        if not a.has_errors: st.success("✅ All rows passed validation.")
        if ps.mode == PipelineMode.AUTO and ps.decision_status == AgentStatus.IDLE:
            with st.spinner("Auto-running Decision Agent..."):
                asyncio.run(run_decision_agent(ps)); st.session_state[PS_KEY] = ps
            st.rerun()

    st.divider()

    # ── Agent 3 ───────────────────────────────────────────────────────────────
    st.markdown("### ⚖️ Agent 3 — Decision")
    if ps.analysis_status == AgentStatus.DONE and ps.decision_status == AgentStatus.IDLE:
        if ps.mode == PipelineMode.STEPWISE:
            if st.button("▶ Run Decision Agent", type="primary", key="app_dec_btn"):
                with st.spinner("Evaluating policies..."):
                    asyncio.run(run_decision_agent(ps)); st.session_state[PS_KEY] = ps
                st.rerun()

    if ps.decision_status in (AgentStatus.DONE, AgentStatus.WAITING) and ps.decision:
        d = ps.decision
        col1,col2 = st.columns(2)
        col1.metric("✅ Auto-approved",  len(d.auto_rows))
        col2.metric("⚠ Needs approval", len(d.approval_rows))

        if d.auto_rows:
            with st.expander(f"✅ Auto-approved ({len(d.auto_rows)})", expanded=False):
                for r in d.auto_rows:
                    st.write(f"Row {r.row_num} — **{r.display_name}**")

        if d.approval_rows:
            st.warning(f"⚠️ {len(d.approval_rows)} row(s) require approval:", icon="⚠️")
            newly = []
            for r in d.approval_rows:
                already = r.row_num in d.approved_by_admin
                checked = st.checkbox(
                    f"**Row {r.row_num} — {r.display_name}**  \n_{r.reason}_",
                    value=already, key=f"app_approve_{r.row_num}",
                )
                if checked: newly.append(r.row_num)
            if st.button("💾 Save approvals", key="app_save_approvals"):
                d.approved_by_admin  = newly
                ps.decision_status   = AgentStatus.DONE
                st.session_state[PS_KEY] = ps
                st.success(f"✅ {len(newly)} row(s) approved."); st.rerun()

        if (ps.decision_status == AgentStatus.DONE
                and ps.execution_status == AgentStatus.IDLE
                and ps.mode == PipelineMode.AUTO
                and not d.approval_rows):
            with st.spinner("Auto-running Execution Agent..."):
                asyncio.run(run_execution_agent(ps)); st.session_state[PS_KEY] = ps
            st.rerun()

    st.divider()

    # ── Agent 4 ───────────────────────────────────────────────────────────────
    st.markdown("### ⚡ Agent 4 — Execution")

    approved_total = 0
    if ps.decision and ps.decision_status == AgentStatus.DONE:
        approved_total = len(ps.decision.auto_rows) + len(ps.decision.approved_by_admin)

    if ps.decision_status == AgentStatus.DONE and ps.execution_status == AgentStatus.IDLE:
        if approved_total == 0:
            st.info("No rows approved — nothing to execute.")
        elif ps.mode == PipelineMode.STEPWISE:
            if st.button(f"▶ Run Execution Agent ({approved_total} row(s))", type="primary", key="app_exec_btn"):
                with st.spinner("Executing... (SAML app registration takes 20-60s per app)"):
                    asyncio.run(run_execution_agent(ps)); st.session_state[PS_KEY] = ps
                st.rerun()

    if ps.execution_status == AgentStatus.DONE and ps.execution:
        e       = ps.execution
        results = e.summary or []
        passed  = sum(1 for r in results if r.status=="completed")
        failed  = sum(1 for r in results if r.status=="failed")
        if failed == 0: st.success(f"✅ All {passed} app(s) registered in {e.total_duration:.1f}s.")
        else:           st.warning(f"✅ {passed} done  ❌ {failed} failed — {e.total_duration:.1f}s")

        st.dataframe([
            {"App": r.display_name,
             "Status": "✅ done" if r.status=="completed" else "❌ failed",
             "App ID": r.app_id or "",
             "Cert Thumbprint": r.cert_thumbprint or "",
             "Duration": f"{r.duration_seconds:.1f}s",
             "Error": r.error or ""}
            for r in results
        ], use_container_width=True, hide_index=True)

        out = io.StringIO()
        w   = csv.DictWriter(out, fieldnames=["display_name","status","app_id","cert_thumbprint","duration_seconds","error"])
        w.writeheader()
        for r in results:
            w.writerow({"display_name":r.display_name,"status":r.status,"app_id":r.app_id or "",
                        "cert_thumbprint":r.cert_thumbprint or "","duration_seconds":r.duration_seconds,"error":r.error or ""})
        st.download_button("⬇️ Download results", out.getvalue(), "app_results.csv", "text/csv")
        st.divider()
        if st.button("🔄 New pipeline", key="app_pipeline_reset"):
            del st.session_state[PS_KEY]; st.session_state.pop(FN_KEY,None); st.rerun()

    elif ps.execution_status == AgentStatus.FAILED:
        st.error(f"❌ Execution failed: {ps.error}")
        if st.button("🔄 Reset", key="app_exec_reset"):
            del st.session_state[PS_KEY]; st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — DECOMMISSION
# ══════════════════════════════════════════════════════════════════════════════
with tab_decom:
    st.subheader("Decommission an application")
    st.warning("Permanently deletes the app registration and service principal.", icon="⚠️")

    app_q = st.text_input("Search app by display name", key="decom_search")
    if app_q and st.button("🔍 Search", key="decom_search_btn"):
        with st.spinner("Searching..."):
            try:
                apps = asyncio.run(client.search_applications(app_q))
                st.session_state["decom_apps"] = apps
                for k in ["decom_sel_app","decom_sel_sp"]: st.session_state.pop(k,None)
            except Exception as exc:
                st.error(f"Search failed: {exc}")

    if "decom_apps" in st.session_state:
        apps = st.session_state["decom_apps"]
        if not apps:
            st.warning("No apps found.")
        else:
            opts = {a["displayName"]: a for a in apps}
            sel  = st.selectbox("Select app *", ["(select)"]+list(opts.keys()), key="decom_app_sel")
            if sel != "(select)":
                app = opts[sel]
                if st.session_state.get("decom_sel_app",{}).get("appId") != app["appId"]:
                    with st.spinner("Resolving SP..."):
                        try:
                            sp = asyncio.run(client.get_service_principal_by_app_id(app["appId"]))
                            st.session_state["decom_sel_app"] = app
                            st.session_state["decom_sel_sp"]  = sp
                        except Exception as exc:
                            st.error(f"SP not found: {exc}")

    if "decom_sel_app" in st.session_state:
        app = st.session_state["decom_sel_app"]
        sp  = st.session_state.get("decom_sel_sp")
        st.success(f"✅ **{app['displayName']}**")
        st.code(
            f"App ID    : {app['appId']}\n"
            f"Object ID : {app['id']}\n"
            f"SP ID     : {sp['id'] if sp else 'NOT FOUND'}",
            language=None,
        )
        if not sp:
            st.error("Service principal not found — cannot decommission.")
        else:
            reason  = st.text_area("Reason *", key="decom_reason")
            revoke  = st.checkbox("Revoke all user assignments before deletion", value=True)
            if st.button("🗑 Decommission App", type="primary", key="decom_submit"):
                if not reason or len(reason.strip()) < 5:
                    st.error("Reason must be at least 5 characters.")
                else:
                    from app_bot.triggers.decom_request import handle_decom_request
                    with st.spinner(f"Decommissioning {app['displayName']}..."):
                        try:
                            result = asyncio.run(handle_decom_request({
                                "app_id": app["appId"], "object_id": app["id"],
                                "service_principal_id": sp["id"],
                                "reason": reason.strip(),
                                "revoke_user_assignments": revoke,
                                "requested_by": "streamlit_admin",
                            }))
                            s = result.get("status")
                            if s == "completed":   st.success("✅ Decommissioned."); st.json(result.get("result",{}))
                            elif s == "escalated": st.warning("⏳ Escalated for approval.")
                            else:                  st.error(f"Status: {s}")
                        except Exception as exc:
                            st.error(f"Error: {exc}")

