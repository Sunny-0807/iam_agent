import logging

from shared.audit.audit_logger import AuditLogger
from shared.exceptions import GraphAPIError, ResourceNotFoundError
from shared.graph_client import GraphClient
from shared.models import FlowName, FlowStatus, RiskIsolationRequest, RiskSeverity

logger = logging.getLogger(__name__)


async def run_risk_isolation(request: RiskIsolationRequest) -> dict:
    """
    Flow C — Risk Isolation.

    Triggered by a Microsoft Sentinel security alert or UEBA signal.
    Immediately isolates a potentially compromised user account.

    Steps:
        1. Verify user exists
        2. Disable the user account immediately
        3. Revoke all active sessions and refresh tokens
        4. Force password reset on next sign-in
        5. Write audit log entry with full alert context

    This flow is intentionally faster and more aggressive than offboarding —
    it prioritises speed of containment over completeness.
    Group memberships and licenses are NOT removed here (that is done
    in a follow-up offboarding flow if the isolation is confirmed).

    Args:
        request: Validated RiskIsolationRequest from the orchestrator.

    Returns:
        dict with isolation summary and alert context.

    Raises:
        GraphAPIError: if any Graph API call fails after retries.
        ResourceNotFoundError: if the user does not exist.
    """
    client = GraphClient()
    audit = AuditLogger()
    graph_ops = []

    logger.warning(
        "SECURITY: Starting risk isolation for user=%s alert_id=%s severity=%s",
        request.user_principal_name,
        request.alert_id,
        request.severity.value,
    )

    try:
        # ── Step 1: Verify user exists ────────────────────────────────────────
        user = await client.get_user(request.user_id)
        graph_ops.append(f"GET /users/{request.user_id}")

        already_disabled = not user.get("accountEnabled", True)
        if already_disabled:
            logger.warning(
                "User %s is already disabled. Revoking sessions anyway.",
                request.user_principal_name,
            )

        # ── Step 2: Disable account ───────────────────────────────────────────
        if not already_disabled:
            await client.disable_user(request.user_id)
            graph_ops.append(f"PATCH /users/{request.user_id} (disable)")
            logger.warning(
                "SECURITY: Account disabled for user=%s", request.user_principal_name
            )

        # ── Step 3: Revoke all sessions ───────────────────────────────────────
        await client.revoke_user_sessions(request.user_id)
        graph_ops.append(f"POST /users/{request.user_id}/revokeSignInSessions")
        logger.warning(
            "SECURITY: All sessions revoked for user=%s", request.user_principal_name
        )

        # ── Step 4: Force password reset ──────────────────────────────────────
        # Requires User.ReadWrite.All or Directory.AccessAsUser.All at app level.
        # Catch 403 and continue — account disable + session revoke are the
        # critical steps; password reset is best-effort.
        password_reset_forced = False
        try:
            await client.force_password_reset(request.user_id)
            graph_ops.append(f"PATCH /users/{request.user_id} (force password reset)")
            logger.warning(
                "SECURITY: Password reset forced for user=%s", request.user_principal_name
            )
            password_reset_forced = True
        except Exception as _pwr_exc:
            logger.warning(
                "SECURITY: Could not force password reset for user=%s: %s "
                "(requires User.ReadWrite.All — account is still disabled and sessions revoked)",
                request.user_principal_name, _pwr_exc,
            )
            graph_ops.append(
                f"PATCH /users/{request.user_id} (force password reset — SKIPPED: {_pwr_exc})"
            )

        # ── Step 5: Audit log ─────────────────────────────────────────────────
        await audit.log(
            flow_name=FlowName.RISK_ISOLATION,
            status=FlowStatus.COMPLETED,
            principal_id=request.user_id,
            requested_by="sentinel_alert",
            details={
                "upn": request.user_principal_name,
                "alert_id": request.alert_id,
                "severity": request.severity.value,
                "alert_reason": request.alert_reason,
                "sentinel_incident_id": request.sentinel_incident_id,
                "was_already_disabled": already_disabled,
                "actions_taken": [
                    "account_disabled",
                    "sessions_revoked",
                    "password_reset_forced",
                ],
            },
            graph_operations=graph_ops,
        )

        result = {
            "user_id": request.user_id,
            "user_principal_name": request.user_principal_name,
            "alert_id": request.alert_id,
            "severity": request.severity.value,
            "account_disabled": True,
            "sessions_revoked": True,
            "password_reset_forced": password_reset_forced,
            "was_already_disabled": already_disabled,
            "status": "isolated",
        }

        logger.warning(
            "SECURITY: Risk isolation completed for user=%s alert=%s",
            request.user_principal_name, request.alert_id,
        )
        return result

    except ResourceNotFoundError:
        logger.error(
            "SECURITY: User not found during risk isolation: %s", request.user_id
        )
        await audit.log_failure(
            flow_name=FlowName.RISK_ISOLATION,
            principal_id=request.user_id,
            requested_by="sentinel_alert",
            error=ResourceNotFoundError("User", request.user_id),
            details={
                "upn": request.user_principal_name,
                "alert_id": request.alert_id,
                "severity": request.severity.value,
            },
        )
        raise

    except GraphAPIError as exc:
        logger.error(
            "SECURITY: Risk isolation FAILED for user=%s: %s",
            request.user_principal_name, exc,
        )
        await audit.log_failure(
            flow_name=FlowName.RISK_ISOLATION,
            principal_id=request.user_id,
            requested_by="sentinel_alert",
            error=exc,
            details={
                "upn": request.user_principal_name,
                "alert_id": request.alert_id,
                "severity": request.severity.value,
                "graph_ops_completed": graph_ops,
            },
        )
        raise

