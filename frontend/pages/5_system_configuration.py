"""
Page 5 — System Configuration
Tabs: ⚙️ Configuration | 📋 Audit Log
"""
import asyncio
import csv
import io
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st

st.set_page_config(
    page_title="System Configuration — Agentic IAM",
    page_icon="⚙️",
    layout="wide",
)
st.title("⚙️ System Configuration")

from shared.config import config

tab_config, tab_audit = st.tabs(["⚙️ Configuration", "📋 Audit Log"])


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════
with tab_config:

    # ── Environment ───────────────────────────────────────────────────────────
    st.subheader("Environment")
    c1, c2, c3 = st.columns(3)
    c1.metric("Environment",  config.environment.upper())
    c2.metric("Bot type",     config.bot_type)
    c3.metric("OpenAI model", config.openai_model)
    st.divider()

    # ── Approval gate ─────────────────────────────────────────────────────────
    st.subheader("Approval gate")
    if config.skip_approval:
        st.error(
            "⚠️ SKIP_APPROVAL is **ON** — the approval gate is bypassed. "
            "All flows auto-execute without human review. "
            "Set `SKIP_APPROVAL=false` in `.env` to re-enable.",
            icon="⚠️",
        )
    else:
        st.success(
            "✅ Approval gate is active. Destructive flows (offboarding, decommission) "
            "require human approval before executing.",
            icon="✅",
        )
    st.divider()

    # ── Connection tests ──────────────────────────────────────────────────────
    st.subheader("Connection tests")

    if st.button("🔌 Test Graph API"):
        from shared.graph_client import GraphClient
        with st.spinner("Testing..."):
            try:
                result = asyncio.run(GraphClient()._get("organization", params={"$select": "displayName,id"}))
                org    = result.get("value", [{}])[0]
                st.success(f"✅ Graph API connected — Tenant: **{org.get('displayName', 'unknown')}**")
            except Exception as exc:
                st.error(f"❌ Graph API failed: {exc}")

    if st.button("🤖 Test OpenAI"):
        with st.spinner("Testing..."):
            try:
                from ai_engine._openai_client import build_openai_client
                client   = build_openai_client()
                response = asyncio.run(client.chat.completions.create(
                    model=config.openai_model,
                    messages=[{"role": "user", "content": "Reply with: ok"}],
                    max_tokens=5,
                ))
                reply = response.choices[0].message.content.strip()
                st.success(f"✅ OpenAI connected — model: **{config.openai_model}** — reply: `{reply}`")
            except Exception as exc:
                st.error(f"❌ OpenAI failed: {exc}")

    if st.button("🗄 Test Cosmos DB"):
        if not config.cosmos_endpoint:
            st.warning("⚠ COSMOS_DB_ENDPOINT not set — audit logs writing to local `audit.jsonl`.")
        else:
            with st.spinner("Testing..."):
                try:
                    from azure.cosmos import CosmosClient
                    c = CosmosClient(config.cosmos_endpoint, credential=config.cosmos_key)
                    c.get_database_client(config.cosmos_database).read()
                    st.success(f"✅ Cosmos DB connected — database: **{config.cosmos_database}**")
                except Exception as exc:
                    st.error(f"❌ Cosmos DB failed: {exc}")

    st.divider()

    # ── Folder watcher paths ──────────────────────────────────────────────────
    PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
    st.subheader("Folder watcher paths")
    watchers = [
        ("User inbox",     os.getenv("WATCHER_INBOX",         str(PROJECT_ROOT / "watched_inbox"))),
        ("User processed", os.getenv("WATCHER_PROCESSED",     str(PROJECT_ROOT / "watched_processed"))),
        ("User failed",    os.getenv("WATCHER_FAILED",        str(PROJECT_ROOT / "watched_failed"))),
        ("App inbox",      os.getenv("APP_WATCHER_INBOX",     str(PROJECT_ROOT / "watched_apps_inbox"))),
        ("App processed",  os.getenv("APP_WATCHER_PROCESSED", str(PROJECT_ROOT / "watched_apps_processed"))),
        ("App failed",     os.getenv("APP_WATCHER_FAILED",    str(PROJECT_ROOT / "watched_apps_failed"))),
    ]
    for label, path in watchers:
        icon = "✅" if Path(path).exists() else "⬜"
        st.write(f"{icon} **{label}**: `{path}`")

    st.divider()

    # ── .env variable status ──────────────────────────────────────────────────
    st.subheader(".env variable status")
    st.caption("Shows which variables are set — values are hidden.")
    env_vars = {
        "AZURE_TENANT_ID":     bool(config.tenant_id),
        "AZURE_CLIENT_ID":     bool(config.client_id),
        "AZURE_CLIENT_SECRET": bool(config.client_secret),
        "OPENAI_ENDPOINT":     bool(config.openai_endpoint),
        "OPENAI_API_KEY":      bool(config.openai_api_key),
        "COSMOS_DB_ENDPOINT":  bool(config.cosmos_endpoint),
        "SERVICE_BUS_CONN":    bool(config.servicebus_conn),
        "SKIP_APPROVAL":       config.skip_approval,
    }
    for var, is_set in env_vars.items():
        icon = "✅" if is_set else "⬜"
        st.write(f"{icon} `{var}`")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — AUDIT LOG
# ══════════════════════════════════════════════════════════════════════════════
with tab_audit:

    @st.cache_data(ttl=15)
    def _load_entries() -> list[dict]:
        log_path = Path(__file__).resolve().parent.parent.parent / "audit.jsonl"
        if not log_path.exists():
            return []
        entries = []
        with open(log_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        entries.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
        return entries

    def _action_label(e: dict) -> str:
        aa = e.get("details", {}).get("assistant_action")
        if aa:
            return f"🤖 {aa.replace('_', ' ').title()}"
        return e.get("flow_name", "").replace("_", " ").title()

    def _source(e: dict) -> str:
        rb = e.get("requested_by", "")
        return {
            "ai_assistant":   "AI Assistant",
            "streamlit_admin":"Admin UI",
            "hr_system":      "HR System",
            "csv_bulk":       "Bulk CSV",
            "csv_bulk_users": "Bulk Users",
            "pipeline":       "Pipeline",
        }.get(rb, rb or "unknown")

    all_entries = _load_entries()

    if not all_entries:
        st.info("No audit entries yet. Run some flows to see activity here.")
    else:
        # ── Summary metrics ───────────────────────────────────────────────────
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Total",         len(all_entries))
        c2.metric("✅ Completed",  sum(1 for e in all_entries if e.get("status") == "completed"))
        c3.metric("❌ Failed",     sum(1 for e in all_entries if e.get("status") == "failed"))
        c4.metric("⏳ Escalated",  sum(1 for e in all_entries if e.get("status") == "escalated"))
        c5.metric("🤖 AI Actions", sum(1 for e in all_entries if e.get("requested_by") == "ai_assistant"))

        st.divider()

        # ── Filters ───────────────────────────────────────────────────────────
        st.subheader("Filters")
        fc1, fc2, fc3, fc4 = st.columns(4)

        all_sources  = sorted(set(_source(e) for e in all_entries))
        all_statuses = sorted(set(e.get("status", "") for e in all_entries))
        sel_sources  = fc1.multiselect("Source",     all_sources,  default=all_sources,  key="al_sources")
        sel_statuses = fc2.multiselect("Status",     all_statuses, default=all_statuses, key="al_statuses")
        sel_range    = fc3.selectbox("Time range",   ["All time", "Last hour", "Last 24h", "Last 7 days"], key="al_range")
        search       = fc4.text_input("Search",      placeholder="principal ID, action...", key="al_search")

        now     = datetime.utcnow()
        cutoffs = {
            "Last hour":   now - timedelta(hours=1),
            "Last 24h":    now - timedelta(days=1),
            "Last 7 days": now - timedelta(days=7),
        }

        filtered = []
        for e in all_entries:
            if _source(e) not in sel_sources:       continue
            if e.get("status") not in sel_statuses: continue
            if sel_range != "All time":
                try:
                    ts = datetime.fromisoformat(e.get("timestamp", "").replace("Z", ""))
                    if ts < cutoffs[sel_range]:      continue
                except Exception:
                    pass
            if search:
                sl = search.lower()
                if (sl not in (e.get("principal_id") or "").lower()
                        and sl not in _action_label(e).lower()
                        and sl not in json.dumps(e.get("details", {})).lower()):
                    continue
            filtered.append(e)

        col_count, col_refresh = st.columns([3, 1])
        col_count.caption(f"Showing **{len(filtered)}** of **{len(all_entries)}** entries.")
        if col_refresh.button("🔄 Refresh", key="al_refresh"):
            st.cache_data.clear()
            st.rerun()

        st.divider()

        if not filtered:
            st.info("No entries match the current filters.")
        else:
            # ── Table ─────────────────────────────────────────────────────────
            icons = {"completed": "✅", "failed": "❌", "escalated": "⏳"}
            table = [
                {
                    "Timestamp":    e.get("timestamp", "")[:19].replace("T", " "),
                    "Action":       _action_label(e),
                    "Status":       f"{icons.get(e.get('status',''), '•')} {e.get('status','')}",
                    "Source":       _source(e),
                    "Principal ID": (e.get("principal_id") or "")[:36],
                }
                for e in filtered
            ]
            st.dataframe(table, use_container_width=True, hide_index=True)

            # ── Entry detail ──────────────────────────────────────────────────
            st.divider()
            st.subheader("Entry detail")
            idx   = st.number_input(
                "Entry index (0 = most recent)", min_value=0,
                max_value=max(len(filtered) - 1, 0), value=0, step=1, key="al_idx",
            )
            entry = filtered[int(idx)]
            ca, cb = st.columns(2)
            with ca:
                st.markdown("**Metadata**")
                st.json({
                    "id":           entry.get("id"),
                    "flow_name":    entry.get("flow_name"),
                    "status":       entry.get("status"),
                    "bot_type":     entry.get("bot_type"),
                    "principal_id": entry.get("principal_id"),
                    "requested_by": entry.get("requested_by"),
                    "timestamp":    entry.get("timestamp"),
                    "error":        entry.get("error"),
                })
            with cb:
                st.markdown("**Details**")
                st.json(entry.get("details", {}))
                ops = entry.get("graph_operations", [])
                st.markdown("**Graph API operations**")
                if ops:
                    for op in ops:
                        st.code(op, language=None)
                else:
                    st.caption("No Graph API operations recorded.")

            # ── Export ────────────────────────────────────────────────────────
            st.divider()
            out = io.StringIO()
            w   = csv.DictWriter(out, fieldnames=[
                "timestamp", "action", "status", "source",
                "principal_id", "requested_by", "error",
            ])
            w.writeheader()
            for e in filtered:
                w.writerow({
                    "timestamp":    e.get("timestamp", ""),
                    "action":       _action_label(e),
                    "status":       e.get("status", ""),
                    "source":       _source(e),
                    "principal_id": e.get("principal_id", ""),
                    "requested_by": e.get("requested_by", ""),
                    "error":        e.get("error", ""),
                })
            st.download_button("⬇️ Export audit CSV", out.getvalue(), "audit_log.csv", "text/csv")

