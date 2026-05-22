"""
Page 8 — AI Assistant (Context-aware, Memory-enabled)

Chat interface for natural language IAM management.
Maintains conversation history across turns, resolves entity references,
validates entities, confirms before executing, and audits everything.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st
from ai_engine.iam_assistant import IAMAssistant, get_param_label
from ai_engine.conversation_memory import ConversationMemory
from shared.models import AssistantAction

st.set_page_config(
    page_title="Action AI Agent — Agentic IAM", page_icon="🤖", layout="wide"
)

# ── Page header ───────────────────────────────────────────────────────────────
st.title("🤖 Action AI Agent")
st.caption(
    "Natural language IAM management. Remembers context across turns — "
    "say 'add him to DevOps group' after onboarding a user and it knows who 'him' is."
)

assistant = IAMAssistant()
memory    = ConversationMemory.get()   # Always get from session_state — safe across reruns

# ── Country codes for usage_location prompt ───────────────────────────────────
COUNTRY_CODES = {
    "India (IN)":          "IN",
    "United States (US)":  "US",
    "United Kingdom (GB)": "GB",
    "Australia (AU)":      "AU",
    "Canada (CA)":         "CA",
    "Germany (DE)":        "DE",
    "France (FR)":         "FR",
    "Singapore (SG)":      "SG",
    "UAE (AE)":            "AE",
    "Japan (JP)":          "JP",
}

# ── Session state keys ────────────────────────────────────────────────────────
# We use explicit keys to survive Streamlit reruns cleanly.
# The stage machine lives entirely in st.session_state.

def _s(key: str, default=None):
    """Get a session state value with a default."""
    return st.session_state.get(key, default)

def _sset(key: str, value) -> None:
    """Set a session state value."""
    st.session_state[key] = value

def _reset_turn() -> None:
    """Clear all per-turn state — keeps memory and chat log intact."""
    for k in ["turn_stage", "turn_parse_result", "turn_missing_values",
              "turn_final_params", "turn_entity_warnings", "turn_instruction"]:
        st.session_state.pop(k, None)

def _reset_all() -> None:
    """Clear everything including memory and chat log."""
    memory.clear()
    for k in list(st.session_state.keys()):
        if k.startswith("turn_") or k == "chat_log":
            del st.session_state[k]
    st.rerun()

# Initialise chat log (list of rendered message dicts)
if "chat_log" not in st.session_state:
    st.session_state["chat_log"] = []


# ── Sidebar — memory status ───────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### 🧠 Conversation Memory")
    st.caption(f"{memory.count} / 10 turns stored")

    if memory.count > 0:
        last_user = memory.last_user()
        if last_user:
            st.success(
                f"Last user: **{last_user.get('display_name') or last_user.get('user_principal_name', 'unknown')}**"
            )

        with st.expander("View history", expanded=False):
            for i, turn in enumerate(memory.turns, start=1):
                st.caption(
                    f"{i}. [{turn.timestamp.strftime('%H:%M')}] "
                    f"**{turn.action}** — {turn.status}"
                )
                st.caption(f"   _{turn.instruction[:60]}..._")

        if st.button("🗑 Clear memory", type="secondary"):
            _reset_all()

    known_users = memory.get_all_known_users()
    if known_users:
        st.divider()
        st.markdown("**Known users this session**")
        for u in known_users:
            st.caption(f"• {u.get('display_name', '')} `{u.get('user_principal_name', '')}`")

    st.divider()
    with st.expander("💡 Example instructions"):
        st.markdown("""
**Multi-turn (uses memory)**
- *Onboard Jane Doe as Engineer in Engineering, UPN: jane@co.com, India*
- *Add her to the DevOps group*
- *Assign Microsoft 365 E3 to her*
- *Offboard john@co.com — resigned*
- *Now isolate him — suspicious login*

**Single actions**
- *Add jane@co.com to Finance group*
- *Remove Power Automate Free license from john@co.com*
- *Remove john from all groups*
        """)


# ── Chat log display ──────────────────────────────────────────────────────────
chat_container = st.container()
with chat_container:
    for msg in st.session_state["chat_log"]:
        role  = msg["role"]
        text  = msg["text"]
        extra = msg.get("extra")   # dict with extra st.json data

        if role == "user":
            with st.chat_message("user"):
                st.write(text)
        elif role == "assistant":
            with st.chat_message("assistant"):
                st.write(text)
                if extra:
                    st.json(extra)
        elif role == "warning":
            with st.chat_message("assistant"):
                st.warning(text, icon="⚠️")
        elif role == "error":
            with st.chat_message("assistant"):
                st.error(text)
        elif role == "success":
            with st.chat_message("assistant"):
                st.success(text)
                if extra:
                    st.json(extra)


def _log(role: str, text: str, extra: dict = None) -> None:
    """Append a message to the chat log and session_state."""
    st.session_state["chat_log"].append({"role": role, "text": text, "extra": extra})


# ═══════════════════════════════════════════════════════════════════════════════
# STAGE MACHINE
# stage: None | "parsed" | "filling" | "confirm" | "executing"
# ═══════════════════════════════════════════════════════════════════════════════

stage = _s("turn_stage")


# ── No active turn — show input ───────────────────────────────────────────────
if stage is None:
    instruction = st.chat_input("Type an IAM instruction...")

    if instruction and instruction.strip():
        _sset("turn_instruction", instruction.strip())
        _log("user", instruction.strip())

        # Parse with memory context
        with st.spinner("Analysing..."):
            result = asyncio.run(assistant.parse(instruction.strip(), memory=memory))
            _sset("turn_parse_result", result)

        if result.action == AssistantAction.UNKNOWN:
            _log("error",
                 f"❓ Could not identify a supported IAM action. "
                 f"Reason: {result.reasoning}\n\n"
                 "Try rephrasing. Check the sidebar for example instructions.")
            _reset_turn()
            st.rerun()

        # Entity resolution — resolve pronouns/partial names using memory
        with st.spinner("Resolving entities..."):
            resolved_extracted, entity_warnings = asyncio.run(
                assistant.resolve_entities(
                    result.action.value, dict(result.extracted), memory=memory
                )
            )
            result.extracted.update(resolved_extracted)
            _sset("turn_entity_warnings", entity_warnings)

        # Show entity warnings (ambiguous references etc.)
        for w in entity_warnings:
            _log("warning", f"⚠ {w}")

        # Recompute missing after entity resolution
        from ai_engine.iam_assistant import _REQUIRED_PARAMS, _compute_missing
        required = _REQUIRED_PARAMS.get(result.action, [])
        result.missing_required = _compute_missing(required, result.extracted)
        _sset("turn_parse_result", result)

        # Show what was parsed
        conf_emoji = "✅" if result.confidence >= 0.8 else "⚠" if result.confidence >= 0.6 else "❓"
        parsed_msg = (
            f"{conf_emoji} **{result.action.value.replace('_', ' ').title()}** "
            f"(confidence: {result.confidence:.0%})\n\n"
            f"*{result.reasoning}*"
        )
        if result.extracted:
            parsed_msg += "\n\n**Extracted:**\n" + "\n".join(
                f"- **{k.replace('_', ' ').title()}**: `{v}`"
                for k, v in result.extracted.items()
                if v and v not in (None, "None", "")
            )
        _log("assistant", parsed_msg)

        if result.missing_required:
            _log("warning",
                 f"Missing required: **{', '.join(p.replace('_', ' ') for p in result.missing_required)}**. "
                 "Please provide them below.")
            _sset("turn_stage", "filling")
        else:
            # No missing params — set final_params now so confirm/executing stages have it
            _sset("turn_final_params", dict(result.extracted))
            _sset("turn_stage", "confirm")

        st.rerun()


# ── FILLING — collect missing params ──────────────────────────────────────────
elif stage == "filling":
    result = _s("turn_parse_result")
    if not result:
        _reset_turn(); st.rerun()

    st.markdown("**Provide missing information:**")
    with st.form("fill_missing", clear_on_submit=False):
        missing_inputs: dict = {}
        for param in result.missing_required:
            label = get_param_label(param)
            if param == "usage_location":
                sel = st.selectbox(f"{label} *", options=list(COUNTRY_CODES.keys()))
                missing_inputs[param] = COUNTRY_CODES[sel]
            elif param == "severity":
                missing_inputs[param] = st.selectbox(f"{label} *", ["high", "medium", "low"])
            elif "reason" in param:
                missing_inputs[param] = st.text_area(f"{label} *", placeholder="Enter reason...")
            else:
                missing_inputs[param] = st.text_input(
                    f"{label} *", placeholder=f"Enter {param.replace('_', ' ')}..."
                )

        c1, c2 = st.columns([1, 5])
        submitted = c1.form_submit_button("Continue ➡", type="primary")
        cancelled = c2.form_submit_button("✖ Cancel")

    if cancelled:
        _log("assistant", "↩ Cancelled.")
        _reset_turn(); st.rerun()

    if submitted:
        # Validate no field is still empty (selectboxes always have a value)
        still_empty = [
            p for p in result.missing_required
            if not missing_inputs.get(p)
            or (isinstance(missing_inputs[p], str) and not missing_inputs[p].strip())
        ]
        if still_empty:
            st.error(f"Still required: {', '.join(get_param_label(p) for p in still_empty)}")
        else:
            _sset("turn_missing_values", missing_inputs)
            final = {**result.extracted, **missing_inputs}
            _sset("turn_final_params", final)
            _sset("turn_stage", "confirm")

            # Log what was filled
            filled_msg = "✅ Information provided:\n" + "\n".join(
                f"- **{k.replace('_', ' ').title()}**: `{v}`"
                for k, v in missing_inputs.items()
            )
            _log("user", filled_msg)
            st.rerun()


# ── CONFIRM — show full params and ask to execute ─────────────────────────────
elif stage == "confirm":
    result      = _s("turn_parse_result")
    final       = _s("turn_final_params")
    warnings    = _s("turn_entity_warnings", [])

    if not result:
        _reset_turn(); st.rerun()

    # Ensure final is always a valid dict — fallback to extracted if somehow missing
    if final is None:
        final = dict(result.extracted) if result.extracted else {}
        _sset("turn_final_params", final)

    with st.form("confirm_form", clear_on_submit=True):
        st.markdown(
            f"**Ready to execute: {result.action.value.replace('_', ' ').upper()}**\n\n"
            + "\n".join(
                f"- **{k.replace('_', ' ').title()}**: `{v}`"
                for k, v in final.items()
                if v and v not in (None, "None", "")
                and not k.startswith("_")
            )
        )
        if warnings:
            for w in warnings:
                st.warning(w, icon="⚠️")

        c1, c2 = st.columns([1, 5])
        confirmed = c1.form_submit_button("🚀 Execute", type="primary")
        cancelled = c2.form_submit_button("✖ Cancel")

    if cancelled:
        _log("assistant", "↩ Cancelled.")
        _reset_turn(); st.rerun()

    if confirmed:
        _log("assistant",
             f"🚀 Executing **{result.action.value.replace('_', ' ').title()}**...")
        _sset("turn_final_params", final)
        _sset("turn_stage", "executing")
        st.rerun()


# ── EXECUTING — run the action ────────────────────────────────────────────────
elif stage == "executing":
    result = _s("turn_parse_result")
    final  = _s("turn_final_params", {})

    if not result:
        _reset_turn(); st.rerun()

    # Pass instruction to dispatcher for audit logging
    final["_instruction"] = _s("turn_instruction", "")

    with st.spinner(f"Running {result.action.value.replace('_', ' ')}..."):
        try:
            outcome = asyncio.run(
                assistant.dispatch(result, final, memory=memory)
            )
            if outcome is None:
                outcome = {"status": "failed", "error": "Dispatcher returned no result."}
        except Exception as exc:
            outcome = {"status": "failed", "error": str(exc), "resolved_entities": {}}

    status = outcome.get("status", "failed")

    if status == "completed":
        # Build a clean result summary (exclude internal keys)
        clean = {k: v for k, v in outcome.items()
                 if k not in ("resolved_entities", "status", "error", "result")
                 and v is not None}
        if outcome.get("result"):
            clean.update(outcome["result"])
        _log("success", f"✅ **{result.action.value.replace('_', ' ').title()}** completed successfully.", clean or None)

    elif status == "escalated":
        _log("warning",
             "⏳ Action escalated for admin approval. Check the Approvals page.",
             outcome.get("result"))

    elif status == "failed":
        _log("error", f"❌ Failed: {outcome.get('error', 'Unknown error')}")

    else:
        _log("assistant", f"Unexpected status: {status}", outcome)

    _reset_turn()   # Clear per-turn state — memory already updated by dispatcher
    st.rerun()

