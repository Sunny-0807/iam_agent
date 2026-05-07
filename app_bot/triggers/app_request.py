import logging

from shared.models import SAMLAppOnboardingRequest, AppType
from app_bot.bot import handle_request

logger = logging.getLogger(__name__)


async def handle_app_request(request_body: dict) -> dict:
    """
    Trigger: New application registration request.

    Expected request_body shape:
    {
        "display_name":          "My Internal App",
        "app_type":              "non_gallery" | "gallery",
        "template_id":           "guid (gallery only)",
        "entity_id":             "https://...",
        "reply_url":             "https://.../saml/acs",
        "sign_on_url":           "https://..." (optional),
        "owner_id":              "user-object-id",
        "assigned_group_names":  ["Group A", "Group B"],  # display names resolved to IDs
        "requested_by":          "admin@org.com"
    }
    """
    logger.info(
        "App registration request received for: %s by %s",
        request_body.get("display_name"),
        request_body.get("requested_by"),
    )

    app_type_raw = request_body.get("app_type", "non_gallery").lower()
    try:
        app_type = AppType(app_type_raw)
    except ValueError:
        app_type = AppType.NON_GALLERY

    # Accept group names (display names) — resolved to IDs inside the flow
    group_names_raw = request_body.get("assigned_group_names", [])
    if isinstance(group_names_raw, str):
        # Handle pipe-separated string format from older callers
        group_names = [g.strip() for g in group_names_raw.split("|") if g.strip()]
    else:
        group_names = [g.strip() for g in group_names_raw if g.strip()]

    request = SAMLAppOnboardingRequest(
        display_name=request_body["display_name"],
        app_type=app_type,
        template_id=request_body.get("template_id") or None,
        entity_id=request_body["entity_id"],
        reply_url=request_body["reply_url"],
        sign_on_url=request_body.get("sign_on_url") or None,
        owner_upn=request_body["owner_upn"],
        assigned_group_names=group_names,
        requested_by=request_body.get("requested_by", "admin_portal"),
    )

    request_text = (
        f"Register new application '{request.display_name}' "
        f"with entity_id: {request.entity_id}."
    )

    return await handle_request(request_text=request_text, request_payload=request)

