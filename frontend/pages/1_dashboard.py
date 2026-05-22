import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st

st.set_page_config(page_title="Dashboard — Agentic IAM", page_icon="📊", layout="wide")
st.title("📊 Dashboard")


@st.cache_data(ttl=30)
def load_entries() -> list[dict]:
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


entries = load_entries()

# ── Summary cards ─────────────────────────────────────────────────────────────
col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Total Actions",   len(entries))
col2.metric("Completed",       sum(1 for e in entries if e.get("status") == "completed"))
col3.metric("Failed",          sum(1 for e in entries if e.get("status") == "failed"))
col4.metric("Pending Approval",sum(1 for e in entries if e.get("status") == "escalated"))
col5.metric("AI Assistant",    sum(1 for e in entries if e.get("requested_by") == "ai_assistant"))

st.divider()

# ── Pending approvals ─────────────────────────────────────────────────────────
escalated = [e for e in entries if e.get("status") == "escalated"]
if escalated:
    st.subheader(f"⏳ Pending Approvals ({len(escalated)})")
    st.caption("These flows are waiting for admin approval. Use SKIP_APPROVAL=true for local testing, or build the approval callback in Phase 4.")
    for e in escalated[:5]:
        flow      = e.get("flow_name", "").replace("_", " ").title()
        principal = e.get("principal_id", "")[:36]
        ts        = e.get("timestamp", "")[:19].replace("T", " ")
        requested = e.get("requested_by", "")
        with st.container(border=True):
            c1, c2, c3 = st.columns([2, 3, 3])
            c1.write(f"**{flow}**")
            c2.caption(f"`{principal}`")
            c3.caption(f"{ts} — {requested}")
    if len(escalated) > 5:
        st.caption(f"... and {len(escalated) - 5} more. See Audit Log for full list.")
    st.divider()

# ── Actions by bot ────────────────────────────────────────────────────────────
st.subheader("Actions by bot")

BOT_META = {
    "user_bot":     {"icon": "👤", "label": "User Bot",      "desc": "Onboarding, offboarding, isolation"},
    "app_bot":      {"icon": "🖥️", "label": "App Bot",       "desc": "SAML app registration, decommission"},
    "ai_assistant": {"icon": "🤖", "label": "Action AI Agent","desc": "Natural language IAM actions"},
}

# Derive bot from bot_type field; fall back to requested_by for AI assistant entries
def _bot_key(e: dict) -> str:
    bt = e.get("bot_type", "")
    if bt in BOT_META:
        return bt
    if e.get("requested_by") == "ai_assistant":
        return "ai_assistant"
    return bt or "unknown"

bot_counts: dict[str, int] = {}
for e in entries:
    key = _bot_key(e)
    bot_counts[key] = bot_counts.get(key, 0) + 1

if bot_counts:
    # Show known bots in fixed order, then any unknowns
    ordered = [k for k in BOT_META if k in bot_counts]
    ordered += [k for k in bot_counts if k not in BOT_META]
    cols = st.columns(len(ordered))
    for col, key in zip(cols, ordered):
        meta  = BOT_META.get(key, {"icon": "•", "label": key, "desc": ""})
        count = bot_counts[key]
        col.metric(f"{meta['icon']} {meta['label']}", count)
        col.caption(meta["desc"])
else:
    st.info("No activity recorded yet.")

st.divider()

# ── Recent activity ───────────────────────────────────────────────────────────
st.subheader("Recent activity")
if not entries:
    st.info("No audit entries yet.")
else:
    for e in entries[:15]:
        status = e.get("status", "")
        icon   = {"completed": "✅", "failed": "❌", "escalated": "⏳"}.get(status, "•")
        details = e.get("details", {})
        action  = details.get("assistant_action") or e.get("flow_name", "")
        action  = action.replace("_", " ").title()
        pid     = (e.get("principal_id") or "")[:28]
        ts      = e.get("timestamp", "")[:19].replace("T", " ")
        src     = e.get("requested_by", "")
        c1, c2, c3, c4 = st.columns([1, 3, 3, 3])
        c1.write(icon)
        c2.write(f"**{action}**")
        c3.write(f"`{pid}`")
        c4.caption(f"{ts} — {src}")

    if st.button("🔄 Refresh"):
        st.cache_data.clear()
        st.rerun()

