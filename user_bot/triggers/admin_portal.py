import logging

from shared.models import (
    UserOffboardingRequest,
    UserOnboardingRequest,
    RiskIsolationRequest,
    RiskSeverity,
)
from user_bot.bot import handle_request

logger = logging.getLogger(__name__)

# Supported actions from the admin portal
_SUPPORTED_ACTIONS = {
    "onboard_user",
    "offboard_user",
    "isolate_user",
}


async def handle_admin_request(request_body: dict) -> dict:
    """
    Trigger: Admin portal / manual request.

    Handles manual IAM actions submitted by an admin through
    an internal portal, API call, or service desk ticket.

    In production this is an Azure Function with an HTTP trigger
    sitting behind Azure API Management.

    Expected request_body shape:
    {
        "action": "onboard_user | offboard_user | isolate_user",
        "requested_by": "admin@org.com",
        "payload": { ... action-specific fields ... }
    }
    """
    action = request_body.get("action", "").lower()
    requested_by = request_body.get("requested_by", "admin_portal")
    payload = request_body.get("payload", {})

    logger.info("Admin portal request received: action=%s by=%s", action, requested_by)

    if action not in _SUPPORTED_ACTIONS:
        return {
            "error": f"Unsupported action: '{action}'. "
                     f"Supported: {sorted(_SUPPORTED_ACTIONS)}",
            "status": "rejected",
        }

    if action == "onboard_user":
        request = UserOnboardingRequest(
            **{**payload, "requested_by": requested_by, "source": "admin_portal"}
        )
        request_text = (
            f"Admin request: onboard user {request.display_name} "
            f"in {request.department}."
        )

    elif action == "offboard_user":
        request = UserOffboardingRequest(
            **{**payload, "requested_by": requested_by}
        )
        request_text = (
            f"Admin request: offboard user {request.user_principal_name}. "
            f"Reason: {request.reason}"
        )

    elif action == "isolate_user":
        severity_raw = payload.get("severity", "high").lower()
        try:
            severity = RiskSeverity(severity_raw)
        except ValueError:
            severity = RiskSeverity.HIGH

        request = RiskIsolationRequest(
            **{
                **payload,
                "severity":      severity,
                "auto_isolate":  payload.get("auto_isolate", severity == RiskSeverity.HIGH),
                "requested_by":  requested_by,
            }
        )
        request_text = (
            f"Admin request: isolate user {request.user_principal_name}. "
            f"Reason: {request.alert_reason}"
        )

    return await handle_request(request_text=request_text, request_payload=request)

