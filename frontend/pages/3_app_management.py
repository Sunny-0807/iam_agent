import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st
from shared.graph_client import GraphClient

st.set_page_config(
    page_title="App Management — Agentic IAM", page_icon="🖥️", layout="wide"
)
st.title("🖥️ App Management")

client = GraphClient()


# ── Cached resolvers ──────────────────────────────────────────────────────────

@st.cache_data(ttl=300, show_spinner="Loading groups...")
def fetch_groups() -> list[dict]:
    return asyncio.run(client.list_groups())


def resolve_group_ids(names: list[str], all_groups: list[dict]) -> list[str]:
    m = {g["displayName"]: g["id"] for g in all_groups}
    return [m[n] for n in names if n in m]


# ── Validation helpers ────────────────────────────────────────────────────────

def _is_valid_https_url(url: str) -> bool:
    return bool(re.match(r"^https://[a-zA-Z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+$", url))


def _is_valid_entity_id(entity_id: str) -> bool:
    return entity_id.startswith("https://") or entity_id.startswith("urn:")


def validate_saml_form(
    display_name, app_type, template_id,
    entity_id, reply_url, sign_on_url, owner_upn,
) -> list[str]:
    errors = []
    if not display_name.strip():
        errors.append("Display name is required.")
    if app_type == "gallery" and not template_id.strip():
        errors.append("Template ID is required for gallery apps.")
    if not entity_id.strip():
        errors.append("Entity ID is required.")
    elif not _is_valid_entity_id(entity_id.strip()):
        errors.append("Entity ID must start with 'https://' or 'urn:'.")
    if not reply_url.strip():
        errors.append("Reply URL (ACS URL) is required.")
    elif not _is_valid_https_url(reply_url.strip()):
        errors.append("Reply URL must be a valid HTTPS URL.")
    if sign_on_url.strip() and not _is_valid_https_url(sign_on_url.strip()):
        errors.append("Sign-on URL must be a valid HTTPS URL if provided.")
    if not owner_upn.strip():
        errors.append("Owner is required.")
    return errors


# ── Tabs ──────────────────────────────────────────────────────────────────────
tab1, tab2 = st.tabs(["➕ Register SAML App", "➖ Decommission App"])


# ════════════════════════════════════════════════════════════════════════════
# TAB 1 — Register SAML App (Flow D)
# ════════════════════════════════════════════════════════════════════════════
with tab1:
    st.subheader("Register a new SAML SSO application")

    # ── Basic info ────────────────────────────────────────────────────────────
    display_name = st.text_input("Display name *", placeholder="Salesforce CRM")

    app_type = st.radio(
        "App type *",
        options=["non_gallery", "gallery"],
        format_func=lambda x: (
            "Non-gallery (custom app)"
            if x == "non_gallery"
            else "Gallery (Microsoft app catalog)"
        ),
        horizontal=True,
    )

    template_id = ""
    if app_type == "gallery":
        template_id = st.text_input(
            "Template ID *",
            placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
            help="Find in Azure Portal → Entra ID → Enterprise apps → search app → Properties → Application template ID",
        )
    else:
        st.info(
            "Non-gallery app — no template ID needed. "
            "Microsoft's standard non-gallery SAML template will be used automatically.",
            icon="ℹ️",
        )

    # ── SAML URLs ─────────────────────────────────────────────────────────────
    st.markdown("**SAML Configuration**")
    entity_id   = st.text_input(
        "Entity ID *",
        placeholder="https://saml.salesforce.com",
        help="SAML Identifier URI — must start with https:// or urn:",
    )
    reply_url   = st.text_input(
        "Reply URL — ACS URL *",
        placeholder="https://saml.salesforce.com/sso/saml",
        help="Assertion Consumer Service URL — must be HTTPS.",
    )
    sign_on_url = st.text_input(
        "Sign-on URL (optional)",
        placeholder="https://salesforce.com/login",
        help="For SP-initiated SSO. Leave blank for IdP-initiated.",
    )

    # ── Owner — user search ───────────────────────────────────────────────────
    st.markdown("**Owner**")
    st.caption("Search for the user who will own this application registration.")

    owner_query = st.text_input(
        "Search owner by name or UPN",
        placeholder="Type a name or email...",
        key="reg_owner_query",
    )

    owner_upn  = ""
    owner_name = ""

    if owner_query and len(owner_query.strip()) >= 2:
        if st.button("🔍 Search users", key="reg_owner_search"):
            with st.spinner("Searching..."):
                try:
                    users = asyncio.run(client.search_users(owner_query.strip()))
                    st.session_state["reg_owner_results"] = users
                except Exception as exc:
                    st.error(f"User search failed: {exc}")
                    st.session_state["reg_owner_results"] = []

    if "reg_owner_results" in st.session_state:
        results = st.session_state["reg_owner_results"]
        if not results:
            st.warning("No users found. Try a different search term.")
        else:
            options = {
                f"{u['displayName']} ({u['userPrincipalName']})": u
                for u in results
            }
            sel_label = st.selectbox(
                "Select owner *",
                options=["(select)"] + list(options.keys()),
                key="reg_owner_sel",
            )
            if sel_label != "(select)":
                selected_user = options[sel_label]
                owner_upn     = selected_user["userPrincipalName"]
                owner_name    = selected_user["displayName"]
                st.success(
                    f"✅ Owner: **{owner_name}** — `{owner_upn}`"
                )

    # ── Assigned groups — multiselect dropdown ────────────────────────────────
    st.markdown("**Access — assigned groups** (optional)")
    st.caption("Users in these groups will have SSO access to this app.")

    try:
        all_groups = fetch_groups()
        selected_group_names = st.multiselect(
            "Select groups",
            options=[g["displayName"] for g in all_groups],
            key="reg_groups",
        )
    except Exception as e:
        st.error(f"Could not load groups: {e}")
        selected_group_names, all_groups = [], []

    # ── Submit ────────────────────────────────────────────────────────────────
    st.divider()
    if st.button("🚀 Register App", type="primary", key="reg_submit"):
        errors = validate_saml_form(
            display_name, app_type, template_id,
            entity_id, reply_url, sign_on_url, owner_upn,
        )
        if errors:
            for err in errors:
                st.error(err)
        else:
            from app_bot.flows.saml_app_onboarding import run_saml_app_onboarding
            from shared.models import AppType, SAMLAppOnboardingRequest

            request = SAMLAppOnboardingRequest(
                display_name=display_name.strip(),
                app_type=AppType(app_type),
                template_id=template_id.strip() or None,
                entity_id=entity_id.strip(),
                reply_url=reply_url.strip(),
                sign_on_url=sign_on_url.strip() or None,
                owner_upn=owner_upn,
                assigned_group_names=selected_group_names,  # names resolved inside flow
                requested_by="streamlit_admin",
                source="admin_portal",
            )

            with st.spinner("Registering SAML application..."):
                try:
                    result = asyncio.run(run_saml_app_onboarding(request))
                    st.success("✅ Application registered successfully!")
                    st.json({
                        "app_id":               result.get("app_id"),
                        "object_id":            result.get("object_id"),
                        "service_principal_id": result.get("service_principal_id"),
                        "cert_thumbprint":      result.get("cert_thumbprint"),
                        "cert_expiry":          result.get("cert_expiry"),
                        "groups_assigned":      result.get("groups_assigned"),
                    })
                    st.info(
                        "Next step: share the Federation Metadata URL with the "
                        "application team for SP-side configuration.",
                        icon="ℹ️",
                    )
                    # Clear owner search state after success
                    st.session_state.pop("reg_owner_results", None)
                except Exception as exc:
                    st.error(f"Registration failed: {exc}")


# ════════════════════════════════════════════════════════════════════════════
# TAB 2 — Decommission App (Flow E)
# ════════════════════════════════════════════════════════════════════════════
with tab2:
    st.subheader("Decommission an application")
    st.warning(
        "This permanently deletes the app registration and service principal. "
        "All user SSO access will be removed.",
        icon="⚠️",
    )

    # ── App search — resolves all 3 IDs automatically ─────────────────────────
    st.markdown("**Find application**")
    st.caption(
        "Search by display name. All required IDs are resolved automatically."
    )

    app_query = st.text_input(
        "Search application by name",
        placeholder="Type the app display name...",
        key="decom_app_query",
    )

    # State for resolved app
    decom_app_id  = ""
    decom_obj_id  = ""
    decom_sp_id   = ""
    decom_name    = ""

    if app_query and len(app_query.strip()) >= 2:
        if st.button("🔍 Search apps", key="decom_app_search"):
            with st.spinner("Searching applications..."):
                try:
                    apps = asyncio.run(client.search_applications(app_query.strip()))
                    st.session_state["decom_app_results"] = apps
                except Exception as exc:
                    st.error(f"App search failed: {exc}")
                    st.session_state["decom_app_results"] = []

    if "decom_app_results" in st.session_state:
        results = st.session_state["decom_app_results"]
        if not results:
            st.warning("No applications found. Try a different search term.")
        else:
            options = {
                f"{a['display_name']} (appId: ...{a['app_id'][-6:]})": a
                for a in results
            }
            sel_label = st.selectbox(
                "Select application *",
                options=["(select)"] + list(options.keys()),
                key="decom_app_sel",
            )
            if sel_label != "(select)":
                selected_app = options[sel_label]
                decom_app_id  = selected_app["app_id"]
                decom_obj_id  = selected_app["object_id"]
                decom_sp_id   = selected_app["service_principal_id"]
                decom_name    = selected_app["display_name"]

                # Show resolved IDs so admin can verify
                st.success(f"✅ Selected: **{decom_name}**")
                with st.expander("Resolved IDs", expanded=False):
                    st.code(
                        f"App ID (client ID)    : {decom_app_id}\n"
                        f"Object ID             : {decom_obj_id}\n"
                        f"Service Principal ID  : {decom_sp_id}",
                        language=None,
                    )

                if not decom_sp_id:
                    st.warning(
                        "⚠ Service principal not found for this app. "
                        "It may have been deleted already or not yet provisioned.",
                        icon="⚠️",
                    )

    # ── Decommission form ─────────────────────────────────────────────────────
    st.divider()
    st.markdown("**Decommission details**")

    decom_reason = st.text_area(
        "Reason *",
        placeholder="App retired — replaced by new platform",
        key="decom_reason",
    )
    decom_revoke = st.checkbox(
        "Revoke all user assignments before deletion",
        value=True,
        key="decom_revoke",
    )

    if st.button("🗑 Decommission App", type="primary", key="decom_submit"):
        errors = []
        if not decom_app_id:
            errors.append("No application selected. Search and select an app above.")
        if not decom_reason or len(decom_reason.strip()) < 5:
            errors.append("Reason must be at least 5 characters.")
        if not decom_sp_id:
            errors.append("Service principal ID could not be resolved for this app.")

        if errors:
            for err in errors:
                st.error(err)
        else:
            from app_bot.triggers.decom_request import handle_decom_request
            with st.spinner(f"Decommissioning {decom_name}..."):
                try:
                    state = asyncio.run(handle_decom_request({
                        "app_id":                 decom_app_id,
                        "object_id":              decom_obj_id,
                        "service_principal_id":   decom_sp_id,
                        "reason":                 decom_reason.strip(),
                        "revoke_user_assignments": decom_revoke,
                        "requested_by":           "streamlit_admin",
                    }))

                    status = state.get("status")
                    if status == "completed":
                        st.success(f"✅ **{decom_name}** decommissioned successfully.")
                        st.json(state.get("result", {}))
                        # Clear search state after success
                        st.session_state.pop("decom_app_results", None)
                    elif status == "escalated":
                        st.warning("⏳ Decommission escalated for approval. Check Approvals page.")
                    else:
                        st.error(f"Flow ended with status: {status}")
                        if state.get("error"):
                            st.exception(state["error"])
                except Exception as exc:
                    st.error(f"Decommission failed: {exc}")

