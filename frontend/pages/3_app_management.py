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

    pipeline_mode = PipelineMode.AUTO

    st.divider()

    # ── Source ────────────────────────────────────────────────────────────────
    csv_content = csv_filename = None

    # Radio persists across reruns — tabs reset to first on st.rerun()
    source_mode = st.radio(
        "Source",
        options=["📝 Single App Form", "📤 Upload CSV", "📁 Folder Watcher (auto)"],
        horizontal=True,
        key="app_source_mode",
        label_visibility="collapsed",
    )

    if source_mode == "📝 Single App Form":
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

    elif source_mode == "📤 Upload CSV":
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

    else:  # Folder Watcher monitor
        PROJECT_ROOT_APP = Path(__file__).resolve().parent.parent.parent
        st.info(
            f"The folder watcher automatically processes any CSV placed in:\n\n"
            f"`{APP_INBOX}`\n\n"
            "Start the watcher in a separate terminal:\n"
            "```\npython app_bot/triggers/app_folder_watcher.py\n```\n\n"
            "Once a file is dropped there, processing starts within 3 seconds "
            "— no button clicks needed.",
            icon="📁",
        )
        apps_processing = Path(os.getenv("APP_WATCHER_PROCESSING", str(PROJECT_ROOT_APP / "watched_apps_processing")))
        apps_processed  = Path(os.getenv("APP_WATCHER_PROCESSED",  str(PROJECT_ROOT_APP / "watched_apps_processed")))
        apps_failed     = Path(os.getenv("APP_WATCHER_FAILED",     str(PROJECT_ROOT_APP / "watched_apps_failed")))
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("📥 Inbox",      len(list(APP_INBOX.glob("*.csv"))))
        m2.metric("⚙️ Processing", len(list(apps_processing.glob("*.csv"))) if apps_processing.exists() else 0)
        m3.metric("✅ Processed",  len(list(apps_processed.glob("*.csv")))  if apps_processed.exists()  else 0)
        m4.metric("❌ Failed",     len(list(apps_failed.glob("*.csv")))     if apps_failed.exists()     else 0)
        col_ref2, col_auto2 = st.columns([1, 3])
        if col_ref2.button("🔄 Refresh", key="app_folder_refresh"):
            st.rerun()
        auto_refresh_app = col_auto2.checkbox("Auto-refresh every 5s", key="app_folder_auto_refresh")

        import json, time as _time, csv as _csv2
        st.divider()

        # ── Pipeline progress from watcher log ────────────────────────────────
        log_path_app = Path(__file__).resolve().parent.parent.parent / "app_folder_watcher.log"
        if log_path_app.exists():
            log_lines_app = log_path_app.read_text(encoding="utf-8").splitlines()
            last_run = None
            for i in range(len(log_lines_app)-1, -1, -1):
                if "Starting pipeline for:" in log_lines_app[i]:
                    last_run = i; break

            if last_run is not None:
                run_lines_app = log_lines_app[last_run:]
                fname_app = next((l.split("Starting pipeline for:")[-1].strip() for l in run_lines_app if "Starting pipeline for:" in l), "")
                st.markdown(f"### Last run: `{fname_app}`" if fname_app else "### Last run")

                def _done(kw): return any(kw in l for l in run_lines_app)
                def _fail(kw): return any("ERROR" in l and kw in l for l in run_lines_app)

                a1,a2,a3,a4 = st.columns(4)
                for col,(icon,label,kw) in zip([a1,a2,a3,a4],[
                    ("📥","Agent 1 — Collection","Parsed"),
                    ("🔍","Agent 2 — Analysis","validation"),
                    ("⚖️","Agent 3 — Decision","policy"),
                    ("⚡","Agent 4 — Execution","onboarding"),
                ]):
                    if _fail(kw):   col.error(f"{icon} {label}", icon="❌")
                    elif _done(kw): col.success(f"{icon} {label}", icon="✅")
                    else:           col.caption(f"{icon} {label}")

                if _done("Successfully processed"): st.success("✅ Pipeline completed.")
                elif _done("Failed to process"):    st.error("❌ Pipeline failed — see log.")

                # Results CSV
                apps_processed_dir = Path(os.getenv("APP_WATCHER_PROCESSED", str(Path(__file__).resolve().parent.parent.parent / "watched_apps_processed")))
                if apps_processed_dir.exists():
                    res_files = sorted(
                        [f for f in apps_processed_dir.glob("*.csv") if "_results_" in f.name],
                        key=lambda f: f.stat().st_mtime, reverse=True,
                    )
                    if res_files:
                        latest_app = res_files[0]
                        st.markdown(f"**Results: `{latest_app.name}`**")
                        try:
                            rows_app = list(_csv2.DictReader(latest_app.open(encoding="utf-8")))
                            if rows_app:
                                done_c  = sum(1 for r in rows_app if r.get("status")=="completed")
                                fail_c  = sum(1 for r in rows_app if r.get("status")=="failed")
                                rc1,rc2 = st.columns(2)
                                rc1.metric("✅ Completed", done_c)
                                rc2.metric("❌ Failed",    fail_c)
                                st.dataframe([{
                                    "App": r.get("display_name",""),
                                    "Status": ("✅ " if r.get("status")=="completed" else "❌ ") + r.get("status",""),
                                    "App ID": r.get("app_id",""),
                                    "Duration": f"{r.get('duration_seconds','')}s",
                                    "Error": r.get("error",""),
                                } for r in rows_app], use_container_width=True, hide_index=True)
                                with open(latest_app,"rb") as fh:
                                    st.download_button("⬇️ Download results", fh.read(), latest_app.name, "text/csv", key="app_watcher_dl")
                        except Exception as exc:
                            st.warning(f"Could not read results: {exc}")

                with st.expander("📋 Watcher log (last 30 lines)", expanded=False):
                    st.code("\n".join(log_lines_app[-30:]), language=None)
            else:
                st.caption("No runs yet — drop a CSV into the app inbox folder.")
        else:
            st.caption("App watcher log not found — start app_folder_watcher.py to see live progress.")

        if auto_refresh_app:
            _time.sleep(5)
            st.rerun()

        st.stop()
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

    st.divider()

    # ── Agent 2 ───────────────────────────────────────────────────────────────
    st.markdown("### 🔍 Agent 2 — Analysis")
    if ps.collection_status == AgentStatus.DONE and ps.analysis_status == AgentStatus.IDLE:
        with st.spinner("Validating..."):
            asyncio.run(run_analysis_agent(ps))
            st.session_state[PS_KEY] = ps
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

    st.divider()

    # ── Agent 3 ───────────────────────────────────────────────────────────────
    st.markdown("### ⚖️ Agent 3 — Decision")
    if ps.analysis_status == AgentStatus.DONE and ps.decision_status == AgentStatus.IDLE:
        with st.spinner("Evaluating policies..."):
            asyncio.run(run_decision_agent(ps))
            st.session_state[PS_KEY] = ps
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
            st.warning(
                f"⚠️ **{len(d.approval_rows)} app(s) require approval before registration.**",
                icon="⚠️",
            )
            ba1, ba2, ba3 = st.columns(3)
            approve_all_app = ba1.button("✅ Approve all pending", key="app_approve_all")
            skip_all_app    = ba2.button("⏭ Skip all pending",    key="app_skip_all")
            ba3.caption(f"{len(d.approval_rows)} app(s) pending")

            if approve_all_app:
                d.approved_by_admin = [r.row_num for r in d.approval_rows]
                ps.decision_status  = AgentStatus.DONE
                st.session_state[PS_KEY] = ps
                st.success(f"✅ All {len(d.approval_rows)} app(s) approved."); st.rerun()

            if skip_all_app:
                d.approved_by_admin = []
                ps.decision_status  = AgentStatus.DONE
                st.session_state[PS_KEY] = ps
                st.warning("⏭ All pending apps skipped."); st.rerun()

            st.markdown("**Or approve app by app:**")
            newly = []
            for r in d.approval_rows:
                already = r.row_num in d.approved_by_admin
                col_cb, col_info = st.columns([1, 6])
                checked = col_cb.checkbox("", value=already, key=f"app_approve_{r.row_num}")
                col_info.markdown(
                    f"**Row {r.row_num} — {r.display_name}**  \n"
                    f"<small style='color:gray'>{r.reason}</small>",
                    unsafe_allow_html=True,
                )
                if checked: newly.append(r.row_num)
            if st.button("💾 Save decisions", key="app_save_approvals"):
                d.approved_by_admin = newly
                ps.decision_status  = AgentStatus.DONE
                st.session_state[PS_KEY] = ps
                st.success(f"✅ {len(newly)} approved, {len(d.approval_rows)-len(newly)} skipped.")
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
        else:
            with st.spinner(f"Executing {approved_total} app(s)... (SAML registration takes ~30s per app)"):
                asyncio.run(run_execution_agent(ps))
                st.session_state[PS_KEY] = ps
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

