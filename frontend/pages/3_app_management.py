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
    import shutil as _ashutil
    import time   as _atime

    pipeline_mode  = PipelineMode.AUTO
    PS_KEY         = "app_ps"
    FN_KEY         = "app_ps_file"
    POLL_KEY       = "app_inbox_poll"
    APP_PROC_DIR   = Path(os.getenv("APP_WATCHER_PROCESSING", str(PROJECT_ROOT / "watched_apps_processing")))
    APP_PROCD_DIR  = Path(os.getenv("APP_WATCHER_PROCESSED",  str(PROJECT_ROOT / "watched_apps_processed")))
    APP_FAILED_DIR = Path(os.getenv("APP_WATCHER_FAILED",     str(PROJECT_ROOT / "watched_apps_failed")))
    for _d in [APP_PROC_DIR, APP_PROCD_DIR, APP_FAILED_DIR]:
        _d.mkdir(parents=True, exist_ok=True)

    # ── Source mode radio ─────────────────────────────────────────────────────
    source_mode = st.radio(
        "Source",
        options=["📤 Upload CSV", "📁 Folder Watcher (auto)"],
        horizontal=True,
        key="app_source_mode",
        label_visibility="collapsed",
    )
    st.divider()

    # ─────────────────────────────────────────────────────────────────────────
    # FOLDER WATCHER MODE
    # ─────────────────────────────────────────────────────────────────────────
    if source_mode == "📁 Folder Watcher (auto)":
        inbox_files      = sorted(APP_INBOX.glob("*.csv"), key=lambda f: f.stat().st_mtime)
        processing_files = list(APP_PROC_DIR.glob("*.csv"))

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("📥 Inbox",      len(inbox_files))
        m2.metric("⚙️ Processing", len(processing_files))
        m3.metric("✅ Processed",  len(list(APP_PROCD_DIR.glob("*.csv"))))
        m4.metric("❌ Failed",     len(list(APP_FAILED_DIR.glob("*.csv"))))

        st.info(
            f"Drop a CSV into `{APP_INBOX}` — detected within 5 seconds, "
            "pipeline runs automatically with full visualization.",
            icon="📁",
        )
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

        col_r, col_a = st.columns([1, 3])
        refresh_clicked = col_r.button("🔄 Refresh", key="app_folder_refresh")
        auto_poll = col_a.checkbox("Auto-poll every 5s", key="app_folder_auto", value=True)
        st.divider()

        # Detect and claim new inbox file
        active_inbox       = inbox_files[0] if inbox_files else None
        currently_watching = st.session_state.get(POLL_KEY)

        if active_inbox and str(active_inbox) != currently_watching:
            processing_dest = APP_PROC_DIR / active_inbox.name
            try:
                _ashutil.move(str(active_inbox), str(processing_dest))
                st.session_state[POLL_KEY] = str(processing_dest)
                st.session_state[PS_KEY]   = PipelineState(
                    pipeline_type=PipelineType.APP, mode=pipeline_mode,
                )
                st.session_state[FN_KEY] = active_inbox.name
                st.rerun()
            except Exception as _mv_exc:
                st.error(f"Could not claim inbox file: {_mv_exc}")

        csv_content   = None
        csv_filename  = None
        _run_pipeline = False

        if currently_watching:
            proc_path = Path(currently_watching)
            if proc_path.exists():
                try:
                    csv_content   = proc_path.read_text(encoding="utf-8")
                    csv_filename  = st.session_state.get(FN_KEY, proc_path.name)
                    _run_pipeline = True
                except Exception as _re:
                    st.error(f"Could not read processing file: {_re}")
            else:
                csv_filename = st.session_state.get(FN_KEY)
                if csv_filename and PS_KEY in st.session_state:
                    csv_content   = "__already_processed__"
                    _run_pipeline = True

        if not _run_pipeline:
            if processing_files:
                st.info("⚙️ Processing a file — refresh to see progress.", icon="⏳")
            else:
                st.info("Drop a CSV into the inbox to start the pipeline.", icon="📁")
            ea1, ea2, ea3, ea4 = st.columns(4)
            for _c, _l in zip([ea1,ea2,ea3,ea4],
                               ["Agent 1 — Collection","Agent 2 — Analysis",
                                "Agent 3 — Decision","Agent 4 — Execution"]):
                _c.caption(f"⬜ {_l}")

    # ─────────────────────────────────────────────────────────────────────────
    # UPLOAD CSV MODE
    # ─────────────────────────────────────────────────────────────────────────
    else:
        _run_pipeline  = False
        csv_content    = None
        csv_filename   = None
        currently_watching = None

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
            # Fresh state when new file uploaded
            if st.session_state.get(FN_KEY) != csv_filename:
                st.session_state[PS_KEY] = PipelineState(
                    pipeline_type=PipelineType.APP, mode=pipeline_mode,
                )
                st.session_state[FN_KEY] = csv_filename
            _run_pipeline = True

        if not _run_pipeline:
            st.info("Upload a CSV file above to start the pipeline.", icon="⬆️")
            ea1, ea2, ea3, ea4 = st.columns(4)
            for _c, _l in zip([ea1,ea2,ea3,ea4],
                               ["Agent 1 — Collection","Agent 2 — Analysis",
                                "Agent 3 — Decision","Agent 4 — Execution"]):
                _c.caption(f"⬜ {_l}")

    # ─────────────────────────────────────────────────────────────────────────
    # SHARED PIPELINE VISUALIZATION
    # ─────────────────────────────────────────────────────────────────────────
    if _run_pipeline and csv_content:

        ps: PipelineState = st.session_state[PS_KEY]
        ps.mode = pipeline_mode

        # Status bar
        def _si(s: AgentStatus) -> str:
            return {AgentStatus.IDLE:"⬜",AgentStatus.RUNNING:"⏳",
                    AgentStatus.DONE:"✅",AgentStatus.FAILED:"❌",
                    AgentStatus.WAITING:"⚠️"}.get(s,"⬜")

        sb1,sb2,sb3,sb4 = st.columns(4)
        sb1.write(f"{_si(ps.collection_status)} **Agent 1** — Collection")
        sb2.write(f"{_si(ps.analysis_status)}   **Agent 2** — Analysis")
        sb3.write(f"{_si(ps.decision_status)}   **Agent 3** — Decision")
        sb4.write(f"{_si(ps.execution_status)}  **Agent 4** — Execution")
        st.divider()

        if ps.error:
            st.error(f"❌ Pipeline error: {ps.error}")
            if st.button("🔄 Reset", key="app_reset_err"):
                del st.session_state[PS_KEY]
                st.session_state.pop(FN_KEY, None)
                st.session_state.pop(POLL_KEY, None)
                st.rerun()

        # ── Agent 1 ───────────────────────────────────────────────────────────
        st.markdown("### 📥 Agent 1 — Collection")
        st.caption("Reads and parses the CSV into rows.")

        if ps.collection_status == AgentStatus.IDLE and not ps.error:
            if csv_content != "__already_processed__":
                with st.spinner("Agent 1 collecting data..."):
                    asyncio.run(run_collection_agent(ps, source_mode, csv_content, csv_filename))
                    st.session_state[PS_KEY] = ps
                st.rerun()

        if ps.collection_status == AgentStatus.DONE and ps.collection:
            c = ps.collection
            st.success(f"✅ Collected **{c.row_count}** app(s) from `{c.filename}`")
            with st.expander("Preview (first 5 rows)", expanded=True):
                st.dataframe(c.rows[:5], use_container_width=True, hide_index=True)
                if c.row_count > 5:
                    st.caption(f"Showing 5 of {c.row_count} rows.")
        elif ps.collection_status == AgentStatus.FAILED:
            st.error(f"❌ Collection failed: {ps.error}")

        st.divider()

        # ── Agent 2 ───────────────────────────────────────────────────────────
        st.markdown("### 🔍 Agent 2 — Analysis")
        st.caption("Validates every row — entity ID, reply URL, required fields.")

        if ps.collection_status == AgentStatus.DONE and ps.analysis_status == AgentStatus.IDLE:
            with st.spinner("Agent 2 validating..."):
                asyncio.run(run_analysis_agent(ps))
                st.session_state[PS_KEY] = ps
            st.rerun()

        if ps.analysis_status == AgentStatus.DONE and ps.analysis:
            a = ps.analysis
            if a.missing_columns:
                st.error(f"❌ Missing required columns: **{', '.join(a.missing_columns)}**")
            else:
                c1, c2, c3 = st.columns(3)
                c1.metric("Total apps",   ps.collection.row_count if ps.collection else 0)
                c2.metric("🔴 Errors",    len(a.error_rows))
                c3.metric("🟡 Warnings",  len(a.warning_rows))
                if a.issues:
                    st.dataframe(
                        [{"Row": i.row, "App": i.upn,
                          "Severity": "🔴 Error" if i.severity=="error" else "🟡 Warning",
                          "Message": i.message}
                         for i in a.issues],
                        use_container_width=True, hide_index=True,
                    )
                if not a.has_errors and not a.has_warnings:
                    st.success("✅ All rows passed validation.")
                elif a.has_errors:
                    st.warning(f"⚠ {len(a.error_rows)} row(s) skipped. {len(a.prepared_rows)} ready.")
        elif ps.analysis_status == AgentStatus.FAILED:
            st.error(f"❌ Analysis failed: {ps.error}")

        st.divider()

        # ── Agent 3 ───────────────────────────────────────────────────────────
        st.markdown("### ⚖️ Agent 3 — Decision")
        st.caption("Checks app registration policies and determines if approval is needed.")

        if ps.analysis_status == AgentStatus.DONE and ps.decision_status == AgentStatus.IDLE:
            with st.spinner("Agent 3 checking policies..."):
                asyncio.run(run_decision_agent(ps))
                st.session_state[PS_KEY] = ps
            st.rerun()

        if ps.decision_status in (AgentStatus.DONE, AgentStatus.WAITING) and ps.decision:
            d = ps.decision
            c1, c2 = st.columns(2)
            c1.metric("✅ Auto-approved",  len(d.auto_rows))
            c2.metric("⚠ Needs approval", len(d.approval_rows))

            if d.auto_rows:
                with st.expander(f"✅ Auto-approved ({len(d.auto_rows)} apps)", expanded=False):
                    for r in d.auto_rows:
                        st.write(f"Row {r.row_num} — **{r.display_name}**")

            if d.approval_rows and ps.decision_status != AgentStatus.DONE:
                st.warning(f"⚠️ **{len(d.approval_rows)} app(s) require approval.**", icon="⚠️")
                ba1, ba2, ba3 = st.columns(3)
                if ba1.button("✅ Approve all", key="app_approve_all"):
                    d.approved_by_admin = [r.row_num for r in d.approval_rows]
                    ps.decision_status  = AgentStatus.DONE
                    st.session_state[PS_KEY] = ps
                    st.rerun()
                if ba2.button("⏭ Skip all", key="app_skip_all"):
                    d.approved_by_admin = []
                    ps.decision_status  = AgentStatus.DONE
                    st.session_state[PS_KEY] = ps
                    st.rerun()
                ba3.caption(f"{len(d.approval_rows)} pending")
                st.markdown("**App by app:**")
                newly = []
                for r in d.approval_rows:
                    col_cb, col_info = st.columns([1, 6])
                    checked = col_cb.checkbox("", value=r.row_num in d.approved_by_admin,
                                              key=f"app_approve_{r.row_num}")
                    col_info.markdown(
                        f"**Row {r.row_num} — {r.display_name}**\n<small style='color:gray'>{r.reason}</small>",
                        unsafe_allow_html=True,
                    )
                    if checked:
                        newly.append(r.row_num)
                if st.button("💾 Save decisions", key="app_save_approvals"):
                    d.approved_by_admin = newly
                    ps.decision_status  = AgentStatus.DONE
                    st.session_state[PS_KEY] = ps
                    st.rerun()
        elif ps.decision_status == AgentStatus.FAILED:
            st.error(f"❌ Decision failed: {ps.error}")

        st.divider()

        # ── Agent 4 ───────────────────────────────────────────────────────────
        st.markdown("### ⚡ Agent 4 — Execution")
        st.caption("Registers apps in Entra ID — SAML config, cert, owner, groups. ~30-60s per app.")

        approved_total = 0
        if ps.decision and ps.decision_status == AgentStatus.DONE:
            approved_nums  = {r.row_num for r in ps.decision.auto_rows} | set(ps.decision.approved_by_admin)
            approved_total = len(approved_nums)

        if (ps.decision_status == AgentStatus.DONE
                and ps.execution_status == AgentStatus.IDLE
                and approved_total > 0):
            with st.spinner(f"Agent 4 registering {approved_total} app(s) — ~30-60s per app..."):
                asyncio.run(run_execution_agent(ps))
                st.session_state[PS_KEY] = ps
                # Save enriched CSV for folder watcher mode
                if source_mode == "📁 Folder Watcher (auto)" and st.session_state.get(POLL_KEY):
                    _proc_path = Path(st.session_state[POLL_KEY])
                    if _proc_path.exists():
                        import time as _at, csv as _acsv
                        _ts = _at.strftime("%Y%m%d_%H%M%S")
                        try:
                            _orig = list(_acsv.DictReader(_proc_path.open(encoding="utf-8")))
                            _res  = ps.execution.summary or []
                            _rmap = {r.display_name.lower(): r for r in _res}
                            RCOLS = ["status","app_id","cert_thumbprint","duration_seconds","error"]
                            _of   = list(_orig[0].keys()) if _orig else []
                            _ef   = [c for c in RCOLS if c not in _of]
                            _af   = _of + _ef
                            _buf  = io.StringIO()
                            _w    = _acsv.DictWriter(_buf, fieldnames=_af, extrasaction="ignore")
                            _w.writeheader()
                            for _or in _orig:
                                _out = dict(_or)
                                _key = _or.get("display_name","").lower()
                                if _key in _rmap:
                                    _r = _rmap[_key]
                                    _out["status"]           = _r.status
                                    _out["app_id"]           = _r.app_id or ""
                                    _out["cert_thumbprint"]  = _r.cert_thumbprint or ""
                                    _out["duration_seconds"] = _r.duration_seconds
                                    _out["error"]            = _r.error or ""
                                else:
                                    _out.update({"status":"unknown","app_id":"",
                                                 "cert_thumbprint":"","duration_seconds":0,"error":""})
                                _w.writerow(_out)
                            _dest = APP_PROCD_DIR / f"{_proc_path.stem}_{_ts}.csv"
                            _dest.write_text(_buf.getvalue(), encoding="utf-8")
                            _proc_path.unlink()
                        except Exception:
                            _dest = APP_PROCD_DIR / f"{_proc_path.stem}_{_ts}.csv"
                            _ashutil.copy2(str(_proc_path), str(_dest))
                            _proc_path.unlink()
            st.rerun()

        if ps.execution_status == AgentStatus.DONE and ps.execution:
            e       = ps.execution
            results = e.summary or []
            passed  = sum(1 for r in results if r.status == "completed")
            failed  = sum(1 for r in results if r.status == "failed")

            if failed == 0:
                st.success(f"✅ All {passed} app(s) registered in {e.total_duration:.1f}s.")
            else:
                st.warning(f"✅ {passed} done  ❌ {failed} failed — {e.total_duration:.1f}s")

            st.dataframe(
                [{"App": r.display_name,
                  "Status": "✅ done" if r.status=="completed" else "❌ failed",
                  "App ID": r.app_id or "",
                  "Cert Thumbprint": r.cert_thumbprint or "",
                  "Duration": f"{r.duration_seconds:.1f}s",
                  "Error": r.error or ""}
                 for r in results],
                use_container_width=True, hide_index=True,
            )
            out = io.StringIO()
            w   = csv.DictWriter(out, fieldnames=["display_name","status","app_id",
                                                   "cert_thumbprint","duration_seconds","error"])
            w.writeheader()
            for r in results:
                w.writerow({"display_name": r.display_name, "status": r.status,
                             "app_id": r.app_id or "", "cert_thumbprint": r.cert_thumbprint or "",
                             "duration_seconds": r.duration_seconds, "error": r.error or ""})
            st.download_button("⬇️ Download results CSV", out.getvalue(),
                               "app_results.csv", "text/csv")
            st.divider()
            if st.button("🔄 Start new pipeline", key="app_pipeline_reset"):
                del st.session_state[PS_KEY]
                st.session_state.pop(FN_KEY,   None)
                st.session_state.pop(POLL_KEY, None)
                st.rerun()

        elif (ps.execution_status == AgentStatus.IDLE
              and approved_total == 0
              and ps.decision_status == AgentStatus.DONE):
            st.info("No apps approved — nothing to execute.")
            if st.button("🔄 Start new pipeline", key="app_pipeline_reset2"):
                del st.session_state[PS_KEY]
                st.session_state.pop(FN_KEY,   None)
                st.session_state.pop(POLL_KEY, None)
                st.rerun()

        elif ps.execution_status == AgentStatus.FAILED:
            st.error(f"❌ Execution failed: {ps.error}")
            if st.button("🔄 Reset", key="app_exec_reset"):
                del st.session_state[PS_KEY]
                st.session_state.pop(POLL_KEY, None)
                st.rerun()

    # ── Auto-poll (folder watcher only, stops when results shown) ─────────────
    _app_ps_now    = st.session_state.get(PS_KEY)
    _app_exec_done = _app_ps_now and _app_ps_now.execution_status == AgentStatus.DONE
    _app_watching  = bool(st.session_state.get(POLL_KEY))

    if (source_mode == "📁 Folder Watcher (auto)"
            and st.session_state.get("app_folder_auto", True)
            and not _app_exec_done
            and not _app_watching):
        _atime.sleep(5)
        st.rerun()


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
            # Normalise to camelCase — graph_client may return either format
            def _norm(a: dict) -> dict:
                return {
                    "displayName": a.get("displayName") or a.get("display_name", ""),
                    "appId":       a.get("appId")       or a.get("app_id", ""),
                    "id":          a.get("id")           or a.get("object_id", ""),
                }
            normed = [_norm(a) for a in apps]
            normed = [a for a in normed if a["displayName"]]  # skip blank names
            if not normed:
                st.warning("No apps found with a display name.")
            else:
                opts = {a["displayName"]: a for a in normed}
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
            st.warning(
                "⚠ Service principal not found — it may have already been deleted. "
                "You can still delete the app registration below.",
                icon="⚠️",
            )

        reason  = st.text_area("Reason *", key="decom_reason")
        revoke  = st.checkbox("Revoke all user assignments before deletion",
                              value=True, disabled=not sp)
        if st.button("🗑 Decommission App", type="primary", key="decom_submit"):
            if not reason or len(reason.strip()) < 5:
                st.error("Reason must be at least 5 characters.")
            else:
                from app_bot.triggers.decom_request import handle_decom_request
                with st.spinner(f"Decommissioning {app['displayName']}..."):
                    try:
                        result = asyncio.run(handle_decom_request({
                            "app_id": app["appId"],
                            "object_id": app["id"],
                            "service_principal_id": sp["id"] if sp else "",
                            "reason": reason.strip(),
                            "revoke_user_assignments": revoke,
                            "requested_by": "streamlit_admin",
                        }))
                        s = result.get("status")
                        if s == "completed":
                            _res     = result.get("result", {})
                            _app_del = _res.get("application_deleted", True)
                            _sp_del  = _res.get("service_principal_deleted", True)
                            if _app_del and _sp_del:
                                st.success("✅ Fully decommissioned — SP and app registration deleted.")
                            elif _sp_del and not _app_del:
                                st.warning(
                                    "⚠️ SP deleted but app registration still exists. "
                                    "Delete it manually from Entra ID → App Registrations.",
                                    icon="⚠️",
                                )
                            else:
                                st.success("✅ Decommissioned.")
                            st.json(_res)
                        elif s == "escalated":
                            st.warning("⏳ Escalated for approval.")
                        else:
                            st.error(f"Status: {s}")
                    except Exception as exc:
                        st.error(f"Error: {exc}")

