import logging

from shared.audit.audit_logger import AuditLogger
from shared.exceptions import DependencyCheckError, GraphAPIError, ResourceNotFoundError
from shared.graph_client import GraphClient
from shared.models import AppOffboardingRequest, FlowName, FlowStatus

logger = logging.getLogger(__name__)

# Maximum number of active user assignments allowed before blocking offboarding.
# If an app has more active users than this, the flow raises DependencyCheckError
# and requires manual review before proceeding.
_MAX_ACTIVE_ASSIGNMENTS_THRESHOLD = 0


async def run_app_offboarding(request: AppOffboardingRequest) -> dict:
    """
    Flow E — Application Offboarding.

    Steps:
        1. Verify the application exists in Entra ID
        2. Dependency check — ensure no active user assignments remain
        3. Revoke all user app role assignments
        4. Delete the service principal (enterprise app)
        5. Delete the application registration
        6. Write audit log entry

    The dependency check (Step 2) is the critical safety gate unique to app offboarding.
    If active users are still assigned, the flow halts and raises DependencyCheckError
    rather than silently breaking those users' access.

    Args:
        request: Validated AppOffboardingRequest from the orchestrator.

    Returns:
        dict with decommission summary.

    Raises:
        DependencyCheckError: if active user assignments exist and block offboarding.
        GraphAPIError: if any Graph API call fails after retries.
        ResourceNotFoundError: if the application does not exist.
    """
    client = GraphClient()
    audit = AuditLogger()
    graph_ops = []

    logger.info(
        "Starting app offboarding for app_id=%s object_id=%s",
        request.app_id, request.object_id,
    )

    try:
        # ── Step 1: Verify application exists ─────────────────────────────────
        # Try to find the app registration by object_id first.
        # If not found, fall back to searching by appId — handles cases where
        # the object_id passed is stale (e.g. from a previous failed attempt).
        display_name = request.app_id   # fallback
        app = None

        try:
            app = await client.get_application(request.object_id)
            graph_ops.append(f"GET /applications/{request.object_id}")
            display_name = app.get("displayName", request.app_id)
            logger.info("Application found by objectId: %s", display_name)
        except ResourceNotFoundError:
            logger.warning(
                "App registration %s not found by objectId — "
                "searching by appId=%s as fallback...",
                request.object_id, request.app_id,
            )
            # Fallback: search by appId to get the real object_id
            try:
                results = await client.search_applications(display_name or request.app_id)
                for r in results:
                    if r.get("appId") == request.app_id:
                        real_object_id = r.get("id", "")
                        if real_object_id and real_object_id != request.object_id:
                            logger.info(
                                "Found app by appId — real objectId=%s (was %s)",
                                real_object_id, request.object_id,
                            )
                            # Use the correct object_id for deletion
                            request = request.model_copy(
                                update={"object_id": real_object_id}
                            )
                        app = r
                        display_name = r.get("displayName", request.app_id)
                        break
            except Exception as _search_exc:
                logger.warning("Fallback search failed: %s", _search_exc)

            if not app:
                logger.warning(
                    "App registration not found by objectId or appId. "
                    "Proceeding with SP deletion only."
                )

        # ── Steps 2-4: SP operations (skipped if SP ID not provided) ────────────
        revoked_count = 0
        has_sp = bool(request.service_principal_id)

        if has_sp:
            # ── Step 2: Dependency check ──────────────────────────────────────
            assignments = await client.get_app_role_assignments(request.service_principal_id)
            graph_ops.append(
                f"GET /servicePrincipals/{request.service_principal_id}/appRoleAssignedTo"
            )
            active_count = len(assignments)

            if active_count > _MAX_ACTIVE_ASSIGNMENTS_THRESHOLD:
                assigned_users = [
                    a.get("principalDisplayName", a.get("principalId"))
                    for a in assignments
                ]
                logger.warning(
                    "App offboarding blocked: %d active user(s) still assigned: %s",
                    active_count, assigned_users,
                )
                raise DependencyCheckError(
                    f"Cannot offboard '{display_name}' — {active_count} user(s) still "
                    f"assigned: {assigned_users}. Revoke all assignments first."
                )
            logger.info("Dependency check passed — no active user assignments.")

            # ── Step 3: Revoke all user assignments ───────────────────────────
            if request.revoke_user_assignments and assignments:
                revoked_count = await client.revoke_all_app_assignments(
                    request.service_principal_id
                )
                graph_ops.append(
                    f"DELETE /servicePrincipals/{request.service_principal_id}"
                    f"/appRoleAssignedTo/* ({revoked_count} revoked)"
                )
                logger.info("Revoked %d assignment(s) from %s.", revoked_count, display_name)

            # ── Step 3a: Clear identifierUris before deleting SP ──────────────
            # Microsoft blocks SP deletion when identifierUris has an unverified
            # external domain. Clear it first.
            try:
                if app is not None:
                    await client._patch(f"applications/{request.object_id}", {
                        "identifierUris": [],
                    })
                    graph_ops.append(
                        f"PATCH /applications/{request.object_id} (clear identifierUris)"
                    )
                    logger.info("identifierUris cleared: %s", request.object_id)
            except Exception as _exc:
                logger.warning("Could not clear identifierUris: %s", _exc)

            # ── Step 4: Delete service principal ──────────────────────────────
            try:
                await client.delete_service_principal(request.service_principal_id)
                graph_ops.append(f"DELETE /servicePrincipals/{request.service_principal_id}")
                logger.info("Service principal deleted: %s", request.service_principal_id)
            except ResourceNotFoundError:
                logger.warning("SP %s already deleted — skipping.", request.service_principal_id)
        else:
            logger.info("No service_principal_id provided — skipping SP steps.")

        # ── Step 5: Delete application registration ───────────────────────────
        # Strategy:
        #   1. Try DELETE with request.object_id directly
        #   2. If 404: search by appId to find current object_id and retry
        #   3. If still 404: app is genuinely gone — mark as deleted
        application_deleted = False
        object_ids_to_try = [request.object_id]

        # Pre-fetch any additional object_id from search (different from what we have)
        try:
            search_results = await client.search_applications(display_name or "")
            for r in search_results:
                if r.get("appId") == request.app_id:
                    found_oid = r.get("id", "")
                    if found_oid and found_oid not in object_ids_to_try:
                        object_ids_to_try.append(found_oid)
                        logger.info(
                            "Found additional object_id from search: %s", found_oid
                        )
                    break
        except Exception as _se:
            logger.debug("Pre-search for object_id failed: %s", _se)

        for oid in object_ids_to_try:
            try:
                await client.delete_application(oid)
                graph_ops.append(f"DELETE /applications/{oid}")
                logger.info("Application registration deleted: objectId=%s", oid)
                application_deleted = True
                break
            except ResourceNotFoundError:
                logger.warning(
                    "DELETE /applications/%s returned 404 — trying next object_id if available.",
                    oid,
                )

        if not application_deleted:
            # All object_ids returned 404 — app is genuinely gone from Entra
            logger.info(
                "App registration not found via any object_id — "
                "already deleted or never fully created. Marking as deleted."
            )
            application_deleted = True

        # ── Step 6: Audit log ─────────────────────────────────────────────────
        await audit.log(
            flow_name=FlowName.APP_OFFBOARDING,
            status=FlowStatus.COMPLETED,
            principal_id=request.object_id,
            requested_by=request.requested_by,
            details={
                "display_name": display_name,
                "app_id": request.app_id,
                "object_id": request.object_id,
                "service_principal_id": request.service_principal_id,
                "reason": request.reason,
                "assignments_revoked": revoked_count,
            },
            graph_operations=graph_ops,
        )

        result = {
            "display_name": display_name,
            "app_id": request.app_id,
            "object_id": request.object_id,
            "service_principal_deleted": True,
            "application_deleted": application_deleted,
            "assignments_revoked": revoked_count,
            "status": "completed",
        }
        logger.info(
            "App offboarding completed for: %s (appId=%s)", display_name, request.app_id
        )
        return result

    except DependencyCheckError:
        await audit.log_failure(
            flow_name=FlowName.APP_OFFBOARDING,
            principal_id=request.object_id,
            requested_by=request.requested_by,
            error=DependencyCheckError("Active user assignments block offboarding."),
            details={
                "app_id": request.app_id,
                "reason": "dependency_check_failed",
            },
        )
        raise

    except ResourceNotFoundError:
        logger.error("Application not found: %s", request.object_id)
        await audit.log_failure(
            flow_name=FlowName.APP_OFFBOARDING,
            principal_id=request.object_id,
            requested_by=request.requested_by,
            error=ResourceNotFoundError("Application", request.object_id),
            details={"app_id": request.app_id},
        )
        raise

    except GraphAPIError as exc:
        logger.error(
            "App offboarding failed for app_id=%s: %s", request.app_id, exc
        )
        await audit.log_failure(
            flow_name=FlowName.APP_OFFBOARDING,
            principal_id=request.object_id,
            requested_by=request.requested_by,
            error=exc,
            details={
                "app_id": request.app_id,
                "graph_ops_completed": graph_ops,
            },
        )
        raise

