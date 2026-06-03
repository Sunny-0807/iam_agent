"""
Page 2 — User Management
Tabs: Onboarding Pipeline (4 agents) | Offboard | Isolate | Manage Groups | Manage Licenses
"""
import asyncio
import csv
import io
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st
from shared.graph_client import GraphClient
from shared.config import config

st.set_page_config(page_title="User Management — Agentic IAM", page_icon="👤", layout="wide")
st.title("👤 User Management")

client = GraphClient()

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
INBOX_DIR    = Path(os.getenv("WATCHER_INBOX", str(PROJECT_ROOT / "watched_inbox")))
INBOX_DIR.mkdir(parents=True, exist_ok=True)


@st.cache_data(ttl=300, show_spinner="Loading groups...")
def fetch_groups():
    return asyncio.run(client.list_groups())


@st.cache_data(ttl=300, show_spinner="Loading licenses...")
def fetch_licenses():
    return asyncio.run(client.list_licenses())


def _lic_label(l):
    c = l.get("consumedUnits", 0)
    a = l.get("prepaidUnits", {}).get("enabled", 0)
    return f"{l['skuPartNumber']} ({c} of {a} used)"


# ── Tabs ──────────────────────────────────────────────────────────────────────
tab_pipeline, tab_offboard, tab_isolate, tab_groups, tab_licenses = st.tabs([
    "🔄 Onboarding Pipeline",
    "➖ Offboard User",
    "🚨 Isolate User",
    "👥 Manage Groups",
    "🪪 Manage Licenses",
])


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — ONBOARDING PIPELINE
# ══════════════════════════════════════════════════════════════════════════════
with tab_pipeline:
    st.subheader("User Onboarding Pipeline")

    from ai_engine.agent_pipeline import (
        PipelineState, PipelineType, PipelineMode, AgentStatus,
        run_collection_agent, run_analysis_agent,
        run_decision_agent, run_execution_agent,
    )
    import shutil as _shutil
    import time   as _wtime

    pipeline_mode  = PipelineMode.AUTO
    PS_KEY         = "user_ps"
    FN_KEY         = "user_ps_file"
    POLL_KEY       = "user_inbox_poll"      # tracks which inbox file we're watching
    PROCESSING_DIR = Path(os.getenv("WATCHER_PROCESSING", str(PROJECT_ROOT / "watched_processing")))
    PROCESSED_DIR  = Path(os.getenv("WATCHER_PROCESSED",  str(PROJECT_ROOT / "watched_processed")))
    FAILED_DIR_W   = Path(os.getenv("WATCHER_FAILED",     str(PROJECT_ROOT / "watched_failed")))
    for _d in [PROCESSING_DIR, PROCESSED_DIR, FAILED_DIR_W]:
        _d.mkdir(parents=True, exist_ok=True)

    # ── Source mode radio ─────────────────────────────────────────────────────
    source_mode = st.radio(
        "Source",
        options=["📤 Upload CSV", "📁 Folder Watcher (auto)"],
        horizontal=True,
        key="user_source_mode",
        label_visibility="collapsed",
    )
    st.divider()

    # ─────────────────────────────────────────────────────────────────────────
    # FOLDER WATCHER MODE
    # Streamlit polls watched_inbox/ and runs the pipeline itself — full
    # real-time visualization without needing the terminal watcher.
    # ─────────────────────────────────────────────────────────────────────────
    if source_mode == "📁 Folder Watcher (auto)":

        # ── Inbox status metrics ──────────────────────────────────────────────
        inbox_files      = sorted(INBOX_DIR.glob("*.csv"), key=lambda f: f.stat().st_mtime)
        processing_files = list(PROCESSING_DIR.glob("*.csv"))

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("📥 Inbox",      len(inbox_files))
        m2.metric("⚙️ Processing", len(processing_files))
        m3.metric("✅ Processed",  len(list(PROCESSED_DIR.glob("*.csv"))))
        m4.metric("❌ Failed",     len(list(FAILED_DIR_W.glob("*.csv"))))

        st.info(
            f"Drop a CSV into `{INBOX_DIR}` — this page detects it within 5 seconds "
            "and runs the pipeline automatically with full visualization.",
            icon="📁",
        )

        col_r, col_a = st.columns([1, 3])
        refresh_clicked = col_r.button("🔄 Refresh", key="user_folder_refresh")
        auto_poll = col_a.checkbox("Auto-poll every 5s (detects new files)", key="user_folder_auto", value=True)

        st.divider()

        # ── Detect new file in inbox ──────────────────────────────────────────
        # Pick oldest file (FIFO) so files process in the order they were dropped
        active_inbox = inbox_files[0] if inbox_files else None
        currently_watching = st.session_state.get(POLL_KEY)

        if active_inbox and str(active_inbox) != currently_watching:
            # New file detected — claim it by moving to processing/
            processing_dest = PROCESSING_DIR / active_inbox.name
            try:
                _shutil.move(str(active_inbox), str(processing_dest))
                st.session_state[POLL_KEY] = str(processing_dest)
                # Reset pipeline state for this new file
                st.session_state[PS_KEY] = PipelineState(
                    pipeline_type=PipelineType.USER,
                    mode=pipeline_mode,
                )
                st.session_state[FN_KEY] = active_inbox.name
                st.rerun()
            except Exception as _mv_exc:
                st.error(f"Could not claim inbox file: {_mv_exc}")

        # ── Resolve csv_content from processing folder ────────────────────────
        csv_content  = None
        csv_filename = None
        _run_pipeline = False   # default — set True below if we have content

        if currently_watching:
            proc_path = Path(currently_watching)
            if proc_path.exists():
                # File still in processing — read it
                try:
                    csv_content  = proc_path.read_text(encoding="utf-8")
                    csv_filename = st.session_state.get(FN_KEY, proc_path.name)
                except Exception as _re:
                    st.error(f"Could not read processing file: {_re}")
            else:
                # File was moved to watched_processed/ after execution finished.
                # The PipelineState is still in session — keep csv_filename so
                # the pipeline visualization stays visible. We don't need the raw
                # CSV content anymore because all agents have already completed.
                csv_filename = st.session_state.get(FN_KEY)
                # Use a sentinel so _run_pipeline is True but agents don't re-run
                # (they check their own status before running)
                if csv_filename and PS_KEY in st.session_state:
                    csv_content = "__already_processed__"

        if not csv_content:
            # No active file — waiting for inbox
            st.markdown("#### ⬜ Waiting for a file in the inbox")
            ea1, ea2, ea3, ea4 = st.columns(4)
            ea1.caption("⬜ Agent 1 — Collection")
            ea2.caption("⬜ Agent 2 — Analysis")
            ea3.caption("⬜ Agent 3 — Decision")
            ea4.caption("⬜ Agent 4 — Execution")

            if auto_poll and not refresh_clicked:
                _wtime.sleep(5)
                st.rerun()

        else:
            # ── Active file in processing — run pipeline ──────────────────────
            if st.session_state.get(FN_KEY) != csv_filename:
                st.session_state[PS_KEY] = PipelineState(
                    pipeline_type=PipelineType.USER,
                    mode=pipeline_mode,
                )
                st.session_state[FN_KEY] = csv_filename

            ps: PipelineState = st.session_state[PS_KEY]
            ps.mode = pipeline_mode

            st.info(f"⚙️ Processing: **`{csv_filename}`**", icon="⚙️")

            # Shared pipeline rendering (same block used by upload mode below)
            _run_pipeline = True

    # ─────────────────────────────────────────────────────────────────────────
    # UPLOAD CSV MODE
    # ─────────────────────────────────────────────────────────────────────────
    else:
        _run_pipeline = False
        csv_content = csv_filename = None

        with st.expander("📄 Required CSV columns", expanded=False):
            st.markdown("""
| Column | Required |
|---|---|
| `display_name` | ✅ |
| `user_principal_name` | ✅ |
| `department` | ✅ |
| `job_title` | ✅ |
| `usage_location` | ✅ |
| `mail_nickname` | optional |
| `manager_upn` | optional |
| `group_names` | optional (pipe-separated) |
| `license_name` | optional |
            """)
        uploaded = st.file_uploader("Upload user CSV", type=["csv"], key="user_csv_upload")
        if uploaded:
            csv_content  = uploaded.read().decode("utf-8")
            csv_filename = uploaded.name
            _run_pipeline = True

            if st.session_state.get(FN_KEY) != csv_filename:
                st.session_state[PS_KEY] = PipelineState(
                    pipeline_type=PipelineType.USER,
                    mode=pipeline_mode,
                )
                st.session_state[FN_KEY] = csv_filename

            ps: PipelineState = st.session_state[PS_KEY]
            ps.mode = pipeline_mode

        if not _run_pipeline:
            st.info("Upload a CSV file above to start the pipeline.", icon="⬆️")
            ea1, ea2, ea3, ea4 = st.columns(4)
            ea1.caption("⬜ Agent 1 — Collection")
            ea2.caption("⬜ Agent 2 — Analysis")
            ea3.caption("⬜ Agent 3 — Decision")
            ea4.caption("⬜ Agent 4 — Execution")

    # ─────────────────────────────────────────────────────────────────────────
    # SHARED PIPELINE VISUALIZATION
    # Runs for both source modes once csv_content is available
    # ─────────────────────────────────────────────────────────────────────────
    if _run_pipeline and csv_content:

        # ── Agent status bar ──────────────────────────────────────────────────
        def _si(s: AgentStatus) -> str:
            return {AgentStatus.IDLE:"⬜", AgentStatus.RUNNING:"⏳",
                    AgentStatus.DONE:"✅", AgentStatus.FAILED:"❌",
                    AgentStatus.WAITING:"⚠️"}.get(s,"⬜")

        sb1,sb2,sb3,sb4 = st.columns(4)
        sb1.write(f"{_si(ps.collection_status)} **Agent 1** — Collection")
        sb2.write(f"{_si(ps.analysis_status)}   **Agent 2** — Analysis")
        sb3.write(f"{_si(ps.decision_status)}   **Agent 3** — Decision")
        sb4.write(f"{_si(ps.execution_status)}  **Agent 4** — Execution")
        st.divider()

        # Pipeline error
        if ps.error:
            st.error(f"❌ Pipeline error: {ps.error}")
            if st.button("🔄 Reset", key="user_reset_err"):
                del st.session_state[PS_KEY]
                st.session_state.pop(FN_KEY, None)
                st.session_state.pop(POLL_KEY, None)
                st.rerun()

        # ── AGENT 1 — Collection ──────────────────────────────────────────────
        st.markdown("### 📥 Agent 1 — Collection")
        st.caption("Reads and parses the CSV into rows.")

        if ps.collection_status == AgentStatus.IDLE and not ps.error:
            if csv_content and csv_content != "__already_processed__":
                with st.spinner("Agent 1 collecting data..."):
                    asyncio.run(run_collection_agent(ps, source_mode, csv_content, csv_filename))
                    st.session_state[PS_KEY] = ps
                st.rerun()

        if ps.collection_status == AgentStatus.DONE and ps.collection:
            c = ps.collection
            st.success(f"✅ Collected **{c.row_count}** row(s) from `{c.filename}`")
            with st.expander("Preview (first 5 rows)", expanded=True):
                st.dataframe(c.rows[:5], use_container_width=True, hide_index=True)
                if c.row_count > 5:
                    st.caption(f"Showing 5 of {c.row_count} rows.")
        elif ps.collection_status == AgentStatus.FAILED:
            st.error(f"❌ Collection failed: {ps.error}")

        st.divider()

        # ── AGENT 2 — Analysis ────────────────────────────────────────────────
        st.markdown("### 🔍 Agent 2 — Analysis")
        st.caption("Validates every row and resolves group/license names to IDs.")

        if ps.collection_status == AgentStatus.DONE and ps.analysis_status == AgentStatus.IDLE:
            with st.spinner("Agent 2 validating and resolving..."):
                asyncio.run(run_analysis_agent(ps))
                st.session_state[PS_KEY] = ps
            st.rerun()

        if ps.analysis_status == AgentStatus.DONE and ps.analysis:
            a = ps.analysis
            if a.missing_columns:
                st.error(f"❌ Missing required columns: **{', '.join(a.missing_columns)}**")
            else:
                c1, c2, c3 = st.columns(3)
                c1.metric("Total rows",  ps.collection.row_count if ps.collection else 0)
                c2.metric("🔴 Errors",   len(a.error_rows))
                c3.metric("🟡 Warnings", len(a.warning_rows))
                if a.issues:
                    st.dataframe(
                        [{"Row": i.row, "Identifier": i.upn,
                          "Severity": "🔴 Error" if i.severity=="error" else "🟡 Warning",
                          "Message": i.message}
                         for i in a.issues],
                        use_container_width=True, hide_index=True,
                    )
                if not a.has_errors and not a.has_warnings:
                    st.success("✅ All rows passed validation.")
                elif a.has_errors:
                    st.warning(f"⚠ {len(a.error_rows)} row(s) skipped. "
                               f"{len(a.prepared_rows)} ready.")
        elif ps.analysis_status == AgentStatus.FAILED:
            st.error(f"❌ Analysis failed: {ps.error}")

        st.divider()

        # ── AGENT 3 — Decision ────────────────────────────────────────────────
        st.markdown("### ⚖️ Agent 3 — Decision")
        st.caption("Checks policies and determines which rows need approval.")

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
                with st.expander(f"✅ Auto-approved ({len(d.auto_rows)} rows)", expanded=False):
                    for r in d.auto_rows:
                        st.write(f"Row {r.row_num} — **{r.display_name}** (`{r.identifier}`)")

            if d.approval_rows and ps.decision_status != AgentStatus.DONE:
                st.warning(
                    f"⚠️ **{len(d.approval_rows)} row(s) require approval.**",
                    icon="⚠️",
                )
                ba1, ba2, ba3 = st.columns(3)
                if ba1.button("✅ Approve all", key="user_approve_all"):
                    d.approved_by_admin = [r.row_num for r in d.approval_rows]
                    ps.decision_status  = AgentStatus.DONE
                    st.session_state[PS_KEY] = ps
                    st.rerun()
                if ba2.button("⏭ Skip all", key="user_skip_all"):
                    d.approved_by_admin = []
                    ps.decision_status  = AgentStatus.DONE
                    st.session_state[PS_KEY] = ps
                    st.rerun()
                ba3.caption(f"{len(d.approval_rows)} pending")
                st.markdown("**Row-by-row:**")
                newly = []
                for r in d.approval_rows:
                    col_cb, col_info = st.columns([1, 6])
                    checked = col_cb.checkbox("", value=r.row_num in d.approved_by_admin,
                                              key=f"user_approve_{r.row_num}")
                    col_info.markdown(
                        f"**Row {r.row_num} — {r.display_name}** &nbsp;`{r.identifier}`  \n"
                        f"<small style='color:gray'>{r.reason}</small>",
                        unsafe_allow_html=True,
                    )
                    if checked:
                        newly.append(r.row_num)
                if st.button("💾 Save decisions", key="user_save_approvals"):
                    d.approved_by_admin = newly
                    ps.decision_status  = AgentStatus.DONE
                    st.session_state[PS_KEY] = ps
                    st.rerun()
        elif ps.decision_status == AgentStatus.FAILED:
            st.error(f"❌ Decision failed: {ps.error}")

        st.divider()

        # ── AGENT 4 — Execution ───────────────────────────────────────────────
        st.markdown("### ⚡ Agent 4 — Execution")
        st.caption("Creates user accounts, assigns groups and licenses.")

        approved_total = 0
        if ps.decision and ps.decision_status == AgentStatus.DONE:
            approved_nums  = {r.row_num for r in ps.decision.auto_rows} | set(ps.decision.approved_by_admin)
            approved_total = len(approved_nums)

        if (ps.decision_status == AgentStatus.DONE
                and ps.execution_status == AgentStatus.IDLE
                and approved_total > 0):
            with st.spinner(f"Agent 4 executing {approved_total} row(s)..."):
                asyncio.run(run_execution_agent(ps))
                st.session_state[PS_KEY] = ps
                # Save enriched CSV to processed/ then remove from processing/
                if source_mode == "📁 Folder Watcher (auto)" and currently_watching:
                    proc_path = Path(currently_watching)
                    if proc_path.exists():
                        import time as _t
                        import csv as _ecsv
                        ts = _t.strftime("%Y%m%d_%H%M%S")
                        try:
                            # Read original rows from the processing file
                            orig_rows = list(_ecsv.DictReader(
                                proc_path.open(encoding="utf-8")
                            ))
                            # Build result lookup: upn → BulkUserResult
                            _exec_summary = st.session_state[PS_KEY].execution.summary
                            _res_map = {
                                r.upn.lower(): r
                                for r in _exec_summary.results
                            }
                            # Merge columns
                            RCOLS = ["status", "user_id", "duration_seconds", "details"]
                            orig_fields  = list(orig_rows[0].keys()) if orig_rows else []
                            extra_fields = [c for c in RCOLS if c not in orig_fields]
                            all_fields   = orig_fields + extra_fields

                            _buf = io.StringIO()
                            _w   = _ecsv.DictWriter(
                                _buf, fieldnames=all_fields, extrasaction="ignore"
                            )
                            _w.writeheader()
                            for _orig in orig_rows:
                                _out = dict(_orig)
                                _upn = _orig.get("user_principal_name", "").lower()
                                if _upn in _res_map:
                                    _r = _res_map[_upn]
                                    _out["status"]           = _r.status.value
                                    _out["user_id"]          = _r.user_id or ""
                                    _out["duration_seconds"] = _r.duration_seconds
                                    _out["details"]          = _r.summary_line()
                                else:
                                    _out["status"]           = "unknown"
                                    _out["user_id"]          = ""
                                    _out["duration_seconds"] = 0
                                    _out["details"]          = ""
                                _w.writerow(_out)

                            dest = PROCESSED_DIR / f"{proc_path.stem}_{ts}.csv"
                            dest.write_text(_buf.getvalue(), encoding="utf-8")
                            proc_path.unlink()
                        except Exception as _e:
                            # Fallback: save plain copy so file isn't lost
                            dest = PROCESSED_DIR / f"{proc_path.stem}_{ts}.csv"
                            _shutil.copy2(str(proc_path), str(dest))
                            proc_path.unlink()
            st.rerun()

        if ps.execution_status == AgentStatus.DONE and ps.execution:
            e       = ps.execution
            summary = e.summary
            from shared.models import BulkUserRowStatus
            completed = sum(1 for r in summary.results if r.status == BulkUserRowStatus.COMPLETED)
            partial   = sum(1 for r in summary.results if r.status == BulkUserRowStatus.PARTIAL)
            failed    = sum(1 for r in summary.results if r.status == BulkUserRowStatus.FAILED)
            skipped   = sum(1 for r in summary.results if r.status == BulkUserRowStatus.SKIPPED)

            if failed == 0 and partial == 0:
                st.success(f"✅ All {completed} user(s) onboarded in {e.total_duration:.1f}s.")
            else:
                st.warning(f"✅ {completed} done  ⚠ {partial} partial  "
                           f"❌ {failed} failed  — {skipped} skipped  ({e.total_duration:.1f}s)")

            sicons = {BulkUserRowStatus.COMPLETED:"✅", BulkUserRowStatus.PARTIAL:"⚠",
                      BulkUserRowStatus.FAILED:"❌", BulkUserRowStatus.SKIPPED:"—"}
            st.dataframe(
                [{"Name": r.display_name, "UPN": r.upn,
                  "Status": f"{sicons.get(r.status,'')} {r.status.value}",
                  "User ID": r.user_id or "", "Duration": f"{r.duration_seconds:.1f}s",
                  "Details": r.summary_line()}
                 for r in summary.results],
                use_container_width=True, hide_index=True,
            )
            out = io.StringIO()
            w   = csv.DictWriter(out, fieldnames=["display_name","upn","status",
                                                   "user_id","duration_seconds","details"])
            w.writeheader()
            for r in summary.results:
                w.writerow({"display_name": r.display_name, "upn": r.upn,
                             "status": r.status.value, "user_id": r.user_id or "",
                             "duration_seconds": r.duration_seconds,
                             "details": r.summary_line()})
            st.download_button("⬇️ Download results CSV", out.getvalue(),
                               "user_results.csv", "text/csv")
            st.divider()
            if st.button("🔄 Start new pipeline", key="user_pipeline_reset"):
                del st.session_state[PS_KEY]
                st.session_state.pop(FN_KEY,   None)
                st.session_state.pop(POLL_KEY, None)
                st.rerun()

        elif ps.execution_status == AgentStatus.IDLE and approved_total == 0 \
                and ps.decision_status == AgentStatus.DONE:
            st.info("No rows approved — nothing to execute.")
            if st.button("🔄 Start new pipeline", key="user_pipeline_reset2"):
                del st.session_state[PS_KEY]
                st.session_state.pop(FN_KEY,   None)
                st.session_state.pop(POLL_KEY, None)
                st.rerun()

        elif ps.execution_status == AgentStatus.FAILED:
            st.error(f"❌ Execution failed: {ps.error}")
            if st.button("🔄 Reset", key="user_exec_reset"):
                del st.session_state[PS_KEY]
                st.session_state.pop(POLL_KEY, None)
                st.rerun()

    # ── Auto-poll when in folder watcher mode ────────────────────────────────
    # Only poll when waiting for a new file (no active pipeline running or showing results)
    _ps_now = st.session_state.get(PS_KEY)
    _exec_done = _ps_now and _ps_now.execution_status == AgentStatus.DONE
    _watching_now = bool(st.session_state.get(POLL_KEY))

    if (source_mode == "📁 Folder Watcher (auto)"
            and st.session_state.get("user_folder_auto", True)
            and not _exec_done          # stop polling once results are shown
            and not _watching_now):     # stop polling while a file is being processed
        _wtime.sleep(5)
        st.rerun()


with tab_offboard:
    st.subheader("Offboard an existing user")
    with st.form("offboard_form"):
        off_upn    = st.text_input("User UPN *", placeholder="jane@yourdomain.com")
        off_reason = st.text_area("Reason *", placeholder="Employee resigned")
        off_sub    = st.form_submit_button("🗑 Offboard User", type="primary")

    if off_sub:
        errors = []
        if not off_upn:    errors.append("UPN is required.")
        if not off_reason or len(off_reason.strip()) < 5:
            errors.append("Reason must be at least 5 characters.")
        for e in errors: st.error(e)
        if not errors:
            from user_bot.triggers.admin_portal import handle_admin_request
            with st.spinner("Looking up user..."):
                try:
                    u       = asyncio.run(client.get_user_by_upn(off_upn))
                    off_uid = u["id"]
                except Exception as exc:
                    st.error(f"User not found: {exc}")
                    off_uid = None
            if off_uid:
                with st.spinner("Running offboarding..."):
                    try:
                        state = asyncio.run(handle_admin_request({
                            "action": "offboard_user", "requested_by": "streamlit_admin",
                            "payload": {
                                "user_id": off_uid, "user_principal_name": off_upn,
                                "reason": off_reason, "revoke_sessions": True,
                                "remove_licenses": True, "remove_group_memberships": True,
                            },
                        }))
                        s = state.get("status")
                        if s == "completed":   st.success("✅ Offboarded successfully."); st.json(state.get("result", {}))
                        elif s == "escalated": st.warning("⏳ Escalated for approval.")
                        else:                  st.error(f"Unexpected status: {s}")
                    except Exception as exc:
                        st.error(f"Error: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — ISOLATE
# ══════════════════════════════════════════════════════════════════════════════
with tab_isolate:
    st.subheader("Isolate a compromised user")
    st.warning("Immediately disables account, revokes sessions, forces password reset.", icon="⚠️")
    with st.form("isolate_form"):
        iso_upn = st.text_input("User UPN *", placeholder="jane@yourdomain.com")
        iso_aid = st.text_input("Alert ID *", placeholder="ALERT-001")
        iso_rsn = st.text_area("Alert reason *", placeholder="Suspicious sign-in detected")
        iso_sev = st.selectbox("Severity *", ["high", "medium", "low"])
        iso_sub = st.form_submit_button("🚨 Isolate User", type="primary")

    if iso_sub:
        errors = []
        if not iso_upn: errors.append("UPN is required.")
        if not iso_aid: errors.append("Alert ID is required.")
        if not iso_rsn: errors.append("Alert reason is required.")
        for e in errors: st.error(e)
        if not errors:
            from user_bot.triggers.admin_portal import handle_admin_request
            with st.spinner("Looking up user..."):
                try:
                    u       = asyncio.run(client.get_user_by_upn(iso_upn))
                    iso_uid = u["id"]
                except Exception as exc:
                    st.error(f"User not found: {exc}")
                    iso_uid = None
            if iso_uid:
                with st.spinner("Isolating..."):
                    try:
                        state = asyncio.run(handle_admin_request({
                            "action": "isolate_user", "requested_by": "streamlit_admin",
                            "payload": {
                                "user_id": iso_uid, "user_principal_name": iso_upn,
                                "alert_id": iso_aid, "alert_reason": iso_rsn,
                                "severity": iso_sev, "auto_isolate": iso_sev == "high",
                            },
                        }))
                        s = state.get("status")
                        if s in ("completed","isolated"): st.success("✅ User isolated."); st.json(state.get("result", {}))
                        elif s == "escalated":            st.warning("⏳ Escalated for approval.")
                        else:                             st.error(f"Status: {s}")
                    except Exception as exc:
                        st.error(f"Error: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — MANAGE GROUPS
# ══════════════════════════════════════════════════════════════════════════════
with tab_groups:
    st.subheader("Manage group memberships")
    grp_q = st.text_input("User UPN or object ID", key="grp_id")
    if grp_q and st.button("🔍 Look up user", key="grp_lookup"):
        with st.spinner("Looking up..."):
            try:
                u    = asyncio.run(client.get_user_by_upn(grp_q))
                mems = asyncio.run(client.get_user_memberships(u["id"]))
                st.session_state["grp_user"] = u
                st.session_state["grp_mems"] = [m for m in mems if m.get("@odata.type") == "#microsoft.graph.group"]
            except Exception as exc:
                st.error(f"User not found: {exc}")
                st.session_state.pop("grp_user", None)

    if "grp_user" in st.session_state:
        u    = st.session_state["grp_user"]
        mems = st.session_state.get("grp_mems", [])
        st.success(f"**{u['displayName']}** — `{u['userPrincipalName']}`")
        st.markdown(f"**Current group memberships ({len(mems)})**")
        for m in mems: st.caption(f"• {m.get('displayName', m['id'])}")
        if not mems: st.caption("No group memberships.")
        st.divider()
        ca, cr = st.columns(2)
        with ca:
            st.markdown("**Add to group**")
            try:
                all_grps   = fetch_groups()
                cur_ids    = {m["id"] for m in mems}
                avail_grps = [g for g in all_grps if g["id"] not in cur_ids]
                add_sel    = st.selectbox("Group", ["(select)"] + [g["displayName"] for g in avail_grps], key="grp_add")
                if st.button("➕ Add", key="grp_add_btn") and add_sel != "(select)":
                    with st.spinner(f"Adding to {add_sel}..."):
                        g = asyncio.run(client.get_group_by_name(add_sel))
                        asyncio.run(client.add_user_to_group(g["id"], u["id"]))
                        st.success(f"✅ Added to **{add_sel}**.")
                        st.cache_data.clear(); st.rerun()
            except Exception as exc:
                st.error(f"Error: {exc}")
        with cr:
            st.markdown("**Remove from group**")
            if mems:
                rem_sel = st.selectbox("Group", ["(select)"] + [m.get("displayName","") for m in mems], key="grp_rem")
                if st.button("➖ Remove", key="grp_rem_btn") and rem_sel != "(select)":
                    with st.spinner(f"Removing from {rem_sel}..."):
                        g = asyncio.run(client.get_group_by_name(rem_sel))
                        asyncio.run(client.remove_user_from_group(g["id"], u["id"]))
                        st.success(f"✅ Removed from **{rem_sel}**.")
                        st.cache_data.clear(); st.rerun()
            else:
                st.caption("No groups to remove from.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 5 — MANAGE LICENSES
# ══════════════════════════════════════════════════════════════════════════════
with tab_licenses:
    st.subheader("Manage license assignments")
    lic_q = st.text_input("User UPN or object ID", key="lic_id")
    if lic_q and st.button("🔍 Look up user", key="lic_lookup"):
        with st.spinner("Looking up..."):
            try:
                u         = asyncio.run(client.get_user_by_upn(lic_q))
                all_lics  = fetch_licenses()
                sku_map   = {l["skuId"]: l["skuPartNumber"] for l in all_lics}
                assigned  = asyncio.run(client.get_user_licenses(u["id"]))
                usage_loc = asyncio.run(client.get_usage_location(u["id"]))
                st.session_state["lic_user"]      = u
                st.session_state["lic_assigned"]  = assigned
                st.session_state["lic_sku_map"]   = sku_map
                st.session_state["lic_all"]        = all_lics
                st.session_state["lic_usage_loc"] = usage_loc
            except Exception as exc:
                st.error(f"User not found: {exc}")
                st.session_state.pop("lic_user", None)

    if "lic_user" in st.session_state:
        u         = st.session_state["lic_user"]
        assigned  = st.session_state.get("lic_assigned", [])
        sku_map   = st.session_state.get("lic_sku_map", {})
        all_lics  = st.session_state.get("lic_all", [])
        usage_loc = st.session_state.get("lic_usage_loc")

        st.success(f"**{u['displayName']}** — `{u['userPrincipalName']}`")
        if usage_loc:
            st.caption(f"📍 Usage location: **{usage_loc}**")
        else:
            st.warning("⚠ No usage location set — required before assigning a license.", icon="⚠️")
            loc_opts = {"India (IN)":"IN","United States (US)":"US","United Kingdom (GB)":"GB",
                        "Australia (AU)":"AU","Canada (CA)":"CA"}
            loc_sel  = st.selectbox("Set usage location", list(loc_opts.keys()), key="lic_loc_sel")
            if st.button("💾 Set location", key="lic_set_loc"):
                with st.spinner("Setting..."):
                    asyncio.run(client.set_usage_location(u["id"], loc_opts[loc_sel]))
                    st.session_state["lic_usage_loc"] = loc_opts[loc_sel]
                    st.success(f"✅ Set to **{loc_opts[loc_sel]}**."); st.rerun()

        st.markdown(f"**Assigned licenses ({len(assigned)})**")
        for a in assigned: st.caption(f"• {sku_map.get(a['skuId'], a['skuId'])}")
        if not assigned: st.caption("No licenses assigned.")
        st.divider()
        ca, cr = st.columns(2)
        with ca:
            st.markdown("**Assign license**")
            if not usage_loc:
                st.info("Set usage location first.", icon="ℹ️")
            else:
                assigned_ids = {a["skuId"] for a in assigned}
                avail_lics   = [l for l in all_lics if l["skuId"] not in assigned_ids]
                if avail_lics:
                    asgn_sel = st.selectbox("License", ["(select)"] + [_lic_label(l) for l in avail_lics], key="lic_asgn")
                    if st.button("➕ Assign", key="lic_asgn_btn") and asgn_sel != "(select)":
                        sku = next((l["skuId"] for l in avail_lics if _lic_label(l)==asgn_sel), None)
                        if sku:
                            with st.spinner("Assigning..."):
                                asyncio.run(client.assign_license(u["id"], sku))
                                st.success("✅ License assigned."); st.cache_data.clear(); st.rerun()
                else:
                    st.caption("All available licenses already assigned.")
        with cr:
            st.markdown("**Remove license**")
            if assigned:
                rem_opts = {sku_map.get(a["skuId"], a["skuId"]): a["skuId"] for a in assigned}
                rem_sel  = st.selectbox("License", ["(select)"] + list(rem_opts.keys()), key="lic_rem")
                if st.button("➖ Remove", key="lic_rem_btn") and rem_sel != "(select)":
                    with st.spinner("Removing..."):
                        asyncio.run(client.remove_license(u["id"], rem_opts[rem_sel]))
                        st.success("✅ License removed."); st.cache_data.clear(); st.rerun()
            else:
                st.caption("No licenses to remove.")

