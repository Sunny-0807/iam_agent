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

st.set_page_config(page_title="User Management — Agentic IAM", page_icon="", layout="wide")
st.title(" User Management")

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
    " Onboarding Pipeline",
    "➖ Offboard User",
    " Isolate User",
    " Manage Groups",
    "着 Manage Licenses",
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

    # Mode toggle
    mode_label = st.radio(
        "Pipeline mode",
        options=["stepwise", "auto"],
        format_func=lambda x: (
            " Step-by-step (manually advance each agent)"
            if x == "stepwise"
            else "⚡ Auto-run (all agents run automatically)"
        ),
        horizontal=True,
        key="user_pipeline_mode",
    )
    pipeline_mode = PipelineMode.AUTO if mode_label == "auto" else PipelineMode.STEPWISE

    st.divider()

    # ── Source selection ──────────────────────────────────────────────────────
    src_upload, src_folder = st.tabs([" Upload CSV", " Inbox Folder"])
    csv_content = csv_filename = None

    with src_upload:
        with st.expander(" Required CSV columns", expanded=False):
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
            st.success(f"✅ {csv_filename} ready.")

    with src_folder:
        st.info(
            f"Inbox folder: `{INBOX_DIR}`\n\n"
            "For fully automated processing:\n```\npython user_bot/triggers/folder_watcher.py\n```",
            icon="❔",
        )
        inbox_files = sorted(INBOX_DIR.glob("*.csv"))
        if inbox_files:
            sel = st.selectbox("Pick a file from inbox:", ["(select)"] + [f.name for f in inbox_files])
            if sel != "(select)":
                csv_content  = (INBOX_DIR / sel).read_text(encoding="utf-8")
                csv_filename = sel
                st.success(f"✅ {sel} selected.")

    if not csv_content:
        st.info("Upload a CSV or select from inbox to start the pipeline.", icon="⬆️")
        st.stop()

    # ── Initialise or retrieve PipelineState from session ────────────────────
    PS_KEY  = "user_ps"
    FN_KEY  = "user_ps_file"
    if PS_KEY not in st.session_state or st.session_state.get(FN_KEY) != csv_filename:
        st.session_state[PS_KEY] = PipelineState(
            pipeline_type=PipelineType.USER,
            mode=pipeline_mode,
        )
        st.session_state[FN_KEY] = csv_filename

    ps: PipelineState = st.session_state[PS_KEY]
    ps.mode = pipeline_mode   # honour live mode toggle

    # ── Agent status bar ──────────────────────────────────────────────────────
    def _status_icon(status: AgentStatus) -> str:
        return {
            AgentStatus.IDLE:    "⬜",
            AgentStatus.RUNNING: "⏳",
            AgentStatus.DONE:    "✅",
            AgentStatus.FAILED:  "❌",
            AgentStatus.WAITING: "⚠️",
        }.get(status, "⬜")

    c1, c2, c3, c4 = st.columns(4)
    c1.write(f"{_status_icon(ps.collection_status)} **Agent 1** — Collection")
    c2.write(f"{_status_icon(ps.analysis_status)}   **Agent 2** — Analysis")
    c3.write(f"{_status_icon(ps.decision_status)}   **Agent 3** — Decision")
    c4.write(f"{_status_icon(ps.execution_status)}  **Agent 4** — Execution")
    st.divider()

    # ── Global error display ──────────────────────────────────────────────────
    if ps.error:
        st.error(f"❌ Pipeline error: {ps.error}")
        if st.button(" Reset pipeline", key="user_reset_err"):
            del st.session_state[PS_KEY]
            st.rerun()
        st.stop()

    # ─────────────────────────────────────────────────────────────────────────
    # AGENT 1 — Collection
    # ─────────────────────────────────────────────────────────────────────────
    st.markdown("###  Agent 1 — Collection")
    st.caption("Reads and parses the CSV file into rows.")

    if ps.collection_status == AgentStatus.IDLE:
        if st.button("▶ Run Collection Agent", type="primary", key="user_coll_btn"):
            with st.spinner("Collecting data..."):
                asyncio.run(run_collection_agent(ps, "csv_upload", csv_content, csv_filename))
                st.session_state[PS_KEY] = ps
            st.rerun()

    if ps.collection_status == AgentStatus.DONE and ps.collection:
        c = ps.collection
        st.success(f"✅ Collected **{c.row_count}** row(s) from `{c.filename}`")
        with st.expander("Preview (first 5 rows)", expanded=False):
            st.dataframe(c.rows[:5], use_container_width=True, hide_index=True)
            if c.row_count > 5:
                st.caption(f"Showing 5 of {c.row_count} rows.")

        # Auto-advance
        if ps.mode == PipelineMode.AUTO and ps.analysis_status == AgentStatus.IDLE:
            with st.spinner("Auto-running Analysis Agent..."):
                asyncio.run(run_analysis_agent(ps))
                st.session_state[PS_KEY] = ps
            st.rerun()

    st.divider()

    # ─────────────────────────────────────────────────────────────────────────
    # AGENT 2 — Analysis
    # ─────────────────────────────────────────────────────────────────────────
    st.markdown("###  Agent 2 — Analysis")
    st.caption("Validates every row and resolves group/license names to IDs.")

    if ps.collection_status == AgentStatus.DONE and ps.analysis_status == AgentStatus.IDLE:
        if ps.mode == PipelineMode.STEPWISE:
            if st.button("▶ Run Analysis Agent", type="primary", key="user_anal_btn"):
                with st.spinner("Validating and resolving..."):
                    asyncio.run(run_analysis_agent(ps))
                    st.session_state[PS_KEY] = ps
                st.rerun()

    if ps.analysis_status == AgentStatus.DONE and ps.analysis:
        a = ps.analysis
        if a.missing_columns:
            st.error(f"❌ Missing required columns: **{', '.join(a.missing_columns)}**")
            st.stop()

        col1, col2, col3 = st.columns(3)
        col1.metric("Total rows",    ps.collection.row_count if ps.collection else 0)
        col2.metric(" Errors",    len(a.error_rows))
        col3.metric(" Warnings",  len(a.warning_rows))

        if a.issues:
            issue_table = [
                {
                    "Row": i.row,
                    "Identifier": i.upn,
                    "Severity": " Error" if i.severity == "error" else " Warning",
                    "Message": i.message,
                }
                for i in a.issues
            ]
            st.dataframe(issue_table, use_container_width=True, hide_index=True)

        if not a.has_errors and not a.has_warnings:
            st.success("✅ All rows passed validation.")
        elif a.has_errors:
            st.warning(f"⚠ {len(a.error_rows)} row(s) will be skipped. {len(a.prepared_rows)} row(s) ready for decision.")

        # Auto-advance
        if ps.mode == PipelineMode.AUTO and ps.decision_status == AgentStatus.IDLE:
            with st.spinner("Auto-running Decision Agent..."):
                asyncio.run(run_decision_agent(ps))
                st.session_state[PS_KEY] = ps
            st.rerun()

    st.divider()

    # ─────────────────────────────────────────────────────────────────────────
    # AGENT 3 — Decision
    # ─────────────────────────────────────────────────────────────────────────
    st.markdown("### ⚖️ Agent 3 — Decision")
    st.caption("Checks policies and determines which rows need admin approval.")

    if ps.analysis_status == AgentStatus.DONE and ps.decision_status == AgentStatus.IDLE:
        if ps.mode == PipelineMode.STEPWISE:
            if st.button("▶ Run Decision Agent", type="primary", key="user_dec_btn"):
                with st.spinner("Evaluating policies..."):
                    asyncio.run(run_decision_agent(ps))
                    st.session_state[PS_KEY] = ps
                st.rerun()

    if ps.decision_status in (AgentStatus.DONE, AgentStatus.WAITING) and ps.decision:
        d = ps.decision

        col1, col2 = st.columns(2)
        col1.metric("✅ Auto-approved",  len(d.auto_rows))
        col2.metric("⚠ Needs approval", len(d.approval_rows))

        if d.auto_rows:
            with st.expander(f"✅ Auto-approved ({len(d.auto_rows)} rows)", expanded=False):
                for r in d.auto_rows:
                    st.write(f"Row {r.row_num} — **{r.display_name}** (`{r.identifier}`)")

        # ── Approval checklist ────────────────────────────────────────────────
        if d.approval_rows:
            st.warning(
                f"⚠️ **{len(d.approval_rows)} row(s) require your approval before execution.**\n\n"
                "Check the rows you want to approve. Unchecked rows will be skipped and logged.",
                icon="⚠️",
            )
            newly_approved = []
            for r in d.approval_rows:
                already = r.row_num in d.approved_by_admin
                checked = st.checkbox(
                    f"**Row {r.row_num} — {r.display_name}** (`{r.identifier}`)  \n"
                    f"_{r.reason}_",
                    value=already,
                    key=f"user_approve_{r.row_num}",
                )
                if checked:
                    newly_approved.append(r.row_num)

            if st.button(" Save approval decisions", key="user_save_approvals"):
                d.approved_by_admin = newly_approved
                ps.decision_status  = AgentStatus.DONE
                st.session_state[PS_KEY] = ps
                st.success(f"✅ {len(newly_approved)} row(s) approved.")
                st.rerun()

        # Auto-advance (only when no approval needed or WAITING resolved)
        if (ps.decision_status == AgentStatus.DONE
                and ps.execution_status == AgentStatus.IDLE
                and ps.mode == PipelineMode.AUTO
                and not d.approval_rows):
            with st.spinner("Auto-running Execution Agent..."):
                asyncio.run(run_execution_agent(ps))
                st.session_state[PS_KEY] = ps
            st.rerun()

    st.divider()

    # ─────────────────────────────────────────────────────────────────────────
    # AGENT 4 — Execution
    # ─────────────────────────────────────────────────────────────────────────
    st.markdown("### ⚡ Agent 4 — Execution")
    st.caption("Creates user accounts, assigns groups and licenses.")

    approved_total = 0
    if ps.decision and ps.decision_status == AgentStatus.DONE:
        approved_total = len(ps.decision.auto_rows) + len(ps.decision.approved_by_admin)

    if (ps.decision_status == AgentStatus.DONE
            and ps.execution_status == AgentStatus.IDLE
            and approved_total > 0):
        if ps.mode == PipelineMode.STEPWISE:
            if st.button(
                f"▶ Run Execution Agent ({approved_total} row(s) approved)",
                type="primary", key="user_exec_btn",
            ):
                with st.spinner("Executing..."):
                    asyncio.run(run_execution_agent(ps))
                    st.session_state[PS_KEY] = ps
                st.rerun()
    elif ps.decision_status == AgentStatus.DONE and approved_total == 0:
        st.info("No rows approved — nothing to execute.")

    if ps.execution_status == AgentStatus.DONE and ps.execution:
        e       = ps.execution
        summary = e.summary

        from shared.models import BulkUserRowStatus
        completed = sum(1 for r in summary.results if r.status == BulkUserRowStatus.COMPLETED)
        partial   = sum(1 for r in summary.results if r.status == BulkUserRowStatus.PARTIAL)
        failed    = sum(1 for r in summary.results if r.status == BulkUserRowStatus.FAILED)
        skipped   = sum(1 for r in summary.results if r.status == BulkUserRowStatus.SKIPPED)

        if failed == 0 and partial == 0:
            st.success(f"✅ All {completed} user(s) onboarded successfully in {e.total_duration:.1f}s.")
        else:
            st.warning(
                f"✅ Completed: {completed}  ⚠ Partial: {partial}  "
                f"❌ Failed: {failed}  — skipped: {skipped}  ({e.total_duration:.1f}s)"
            )

        status_icons = {
            BulkUserRowStatus.COMPLETED: "✅",
            BulkUserRowStatus.PARTIAL:   "⚠",
            BulkUserRowStatus.FAILED:    "❌",
            BulkUserRowStatus.SKIPPED:   "—",
        }
        table = [
            {
                "Name":     r.display_name,
                "UPN":      r.upn,
                "Status":   f"{status_icons.get(r.status, '')} {r.status.value}",
                "User ID":  r.user_id or "",
                "Duration": f"{r.duration_seconds:.1f}s",
                "Details":  r.summary_line(),
            }
            for r in summary.results
        ]
        st.dataframe(table, use_container_width=True, hide_index=True)

        out = io.StringIO()
        w   = csv.DictWriter(out, fieldnames=["display_name","upn","status","user_id","duration_seconds","details"])
        w.writeheader()
        for r in summary.results:
            w.writerow({
                "display_name": r.display_name, "upn": r.upn,
                "status": r.status.value, "user_id": r.user_id or "",
                "duration_seconds": r.duration_seconds, "details": r.summary_line(),
            })
        st.download_button("⬇️ Download results CSV", out.getvalue(), "user_results.csv", "text/csv")

        st.divider()
        if st.button(" Start new pipeline", key="user_pipeline_reset"):
            del st.session_state[PS_KEY]
            st.session_state.pop(FN_KEY, None)
            st.rerun()

    elif ps.execution_status == AgentStatus.FAILED:
        st.error(f"❌ Execution failed: {ps.error}")
        if st.button(" Reset", key="user_exec_reset"):
            del st.session_state[PS_KEY]
            st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — OFFBOARD
# ══════════════════════════════════════════════════════════════════════════════
with tab_offboard:
    st.subheader("Offboard an existing user")
    with st.form("offboard_form"):
        off_uid    = st.text_input("User object ID *")
        off_upn    = st.text_input("UPN *")
        off_reason = st.text_area("Reason *")
        off_sub    = st.form_submit_button(" Offboard User", type="primary")

    if off_sub:
        errors = []
        if not off_uid:    errors.append("User object ID is required.")
        if not off_upn:    errors.append("UPN is required.")
        if not off_reason or len(off_reason.strip()) < 5:
            errors.append("Reason must be at least 5 characters.")
        for e in errors: st.error(e)
        if not errors:
            from user_bot.triggers.admin_portal import handle_admin_request
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
        iso_uid = st.text_input("User object ID *")
        iso_upn = st.text_input("UPN *")
        iso_aid = st.text_input("Alert ID *")
        iso_rsn = st.text_area("Alert reason *")
        iso_sev = st.selectbox("Severity *", ["high", "medium", "low"])
        iso_sub = st.form_submit_button(" Isolate User", type="primary")

    if iso_sub:
        errors = []
        if not iso_uid: errors.append("User object ID is required.")
        if not iso_upn: errors.append("UPN is required.")
        if not iso_aid: errors.append("Alert ID is required.")
        if not iso_rsn: errors.append("Alert reason is required.")
        for e in errors: st.error(e)
        if not errors:
            from user_bot.triggers.admin_portal import handle_admin_request
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
    if grp_q and st.button(" Look up user", key="grp_lookup"):
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
    if lic_q and st.button(" Look up user", key="lic_lookup"):
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
            st.caption(f" Usage location: **{usage_loc}**")
        else:
            st.warning("⚠ No usage location set — required before assigning a license.", icon="⚠️")
            loc_opts = {"India (IN)":"IN","United States (US)":"US","United Kingdom (GB)":"GB",
                        "Australia (AU)":"AU","Canada (CA)":"CA"}
            loc_sel  = st.selectbox("Set usage location", list(loc_opts.keys()), key="lic_loc_sel")
            if st.button(" Set location", key="lic_set_loc"):
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

