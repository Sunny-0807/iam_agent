import logging

from shared.audit.audit_logger import AuditLogger
from shared.exceptions import GraphAPIError
from shared.graph_client import GraphClient
from shared.models import AppType, FlowName, FlowStatus, SAMLAppOnboardingRequest

logger = logging.getLogger(__name__)

# Microsoft's standard non-gallery app template ID
# Used when creating a custom enterprise app with SAML SSO
NON_GALLERY_TEMPLATE_ID = "8adf8e6e-67b2-4cf2-a259-e3dc5476c621"


async def run_saml_app_onboarding(request: SAMLAppOnboardingRequest) -> dict:
    """
    SAML SSO Application Onboarding — supports both gallery and non-gallery apps.

    Gallery app flow (app_type=gallery):
        1. Instantiate from gallery template  -> creates app + SP in one call
        2. Set SSO mode to SAML
        3. Set SAML URLs (Entity ID, Reply URL, Sign-on URL)
        4. Generate SAML signing certificate
        5. Assign groups
        6. Add owner
        7. Audit log

    Non-gallery app flow (app_type=non_gallery):
        1. Create app registration
        2. Create service principal
        3. Set SSO mode to SAML
        4. Set SAML URLs
        5. Generate SAML signing certificate
        6. Assign groups
        7. Add owner
        8. Audit log

    Steps 2-7 are identical for both types — only Step 1 differs.

    Args:
        request: Validated SAMLAppOnboardingRequest.

    Returns:
        dict with app details, SAML config summary, and certificate thumbprint.
    """
    client = GraphClient()
    audit  = AuditLogger()
    graph_ops: list[str] = []

    logger.info(
        "Starting SAML app onboarding: name='%s' type=%s",
        request.display_name, request.app_type.value,
    )

    try:
        # ── Step 1: Create app — path differs by type ─────────────────────────
        object_id, app_id, sp_id = await _create_app(
            client, request, graph_ops
        )

        # ── Step 2: Set SSO mode to SAML ──────────────────────────────────────
        # Small safety wait for gallery apps where SP is returned immediately
        # but may still not be fully propagated.
        import asyncio as _asyncio
        if request.app_type.value == "gallery":
            logger.info("Gallery app: waiting 5s for SP propagation before SAML config...")
            await _asyncio.sleep(5)

        await client.set_saml_sso_mode(sp_id)
        graph_ops.append(
            f"PATCH /servicePrincipals/{sp_id} (preferredSingleSignOnMode=saml)"
        )
        logger.info("SSO mode set to SAML for SP: %s", sp_id)

        # ── Step 3: Set SAML URLs ─────────────────────────────────────────────
        await client.set_saml_urls(
            object_id=object_id,
            sp_id=sp_id,
            entity_id=request.entity_id,
            reply_url=request.reply_url,
            sign_on_url=request.sign_on_url,
        )
        graph_ops.append(f"PATCH /applications/{object_id} (SAML URLs)")
        if request.sign_on_url:
            graph_ops.append(
                f"PATCH /servicePrincipals/{sp_id} (loginUrl)"
            )
        logger.info(
            "SAML URLs set: entity_id=%s reply_url=%s",
            request.entity_id, request.reply_url,
        )

        # ── Step 4: Generate SAML signing certificate ─────────────────────────
        cert = await client.add_token_signing_certificate(sp_id)
        cert_thumbprint = cert.get("thumbprint", "")
        cert_expiry     = cert.get("endDateTime", "")
        graph_ops.append(
            f"POST /servicePrincipals/{sp_id}/addTokenSigningCertificate"
        )
        logger.info(
            "SAML certificate generated: thumbprint=%s expiry=%s",
            cert_thumbprint, cert_expiry,
        )

        # ── Step 5: Assign groups ─────────────────────────────────────────────
        # Resolve group display names to object IDs before assigning.
        # Names that cannot be resolved are skipped and logged as warnings.
        groups_assigned   = 0
        groups_not_found  = []

        for group_name in request.assigned_group_names:
            group_name = group_name.strip()
            if not group_name:
                continue
            try:
                group = await client.get_group_by_name(group_name)
                if not group:
                    logger.warning("Group not found, skipping: '%s'", group_name)
                    groups_not_found.append(group_name)
                    continue
                await client.assign_group_to_app(sp_id, group["id"])
                graph_ops.append(
                    f"POST /servicePrincipals/{sp_id}/appRoleAssignments "
                    f"(group='{group_name}' id={group['id']})"
                )
                groups_assigned += 1
                logger.info("Group '%s' assigned to app.", group_name)
            except Exception as exc:
                logger.warning("Failed to assign group '%s': %s", group_name, exc)
                groups_not_found.append(group_name)

        if request.assigned_group_names:
            logger.info(
                "Groups: %d assigned, %d not found/failed.",
                groups_assigned, len(groups_not_found),
            )

        # ── Step 6: Add owner ─────────────────────────────────────────────────
        # Resolve owner UPN to object ID before adding.
        owner_id: str | None = None
        if request.owner_upn:
            try:
                owner_user = await client.get_user_by_upn(request.owner_upn)
                owner_id   = owner_user["id"]
                await client.add_application_owner(object_id, owner_id)
                graph_ops.append(
                    f"POST /applications/{object_id}/owners/$ref (owner={request.owner_upn})"
                )
                logger.info("Owner '%s' (id=%s) added to application.", request.owner_upn, owner_id)
            except Exception as exc:
                logger.warning(
                    "Could not resolve or add owner '%s': %s — proceeding without owner.",
                    request.owner_upn, exc,
                )

        # ── Step 7: Audit log ─────────────────────────────────────────────────
        await audit.log(
            flow_name=FlowName.APP_ONBOARDING,
            status=FlowStatus.COMPLETED,
            principal_id=object_id,
            requested_by=request.requested_by,
            details={
                "display_name":       request.display_name,
                "app_type":           request.app_type.value,
                "app_id":             app_id,
                "object_id":          object_id,
                "service_principal_id": sp_id,
                "entity_id":          request.entity_id,
                "reply_url":          request.reply_url,
                "sign_on_url":        request.sign_on_url,
                "cert_thumbprint":    cert_thumbprint,
                "cert_expiry":        cert_expiry,
                "groups_assigned":    groups_assigned,
                "groups_not_found":  groups_not_found,
                "source":             request.source,
            },
            graph_operations=graph_ops,
        )

        result = {
            "display_name":         request.display_name,
            "app_type":             request.app_type.value,
            "app_id":               app_id,
            "object_id":            object_id,
            "service_principal_id": sp_id,
            "saml_entity_id":       request.entity_id,
            "saml_reply_url":       request.reply_url,
            "saml_sign_on_url":     request.sign_on_url,
            "cert_thumbprint":      cert_thumbprint,
            "cert_expiry":          cert_expiry,
            "groups_assigned":      groups_assigned,
            "groups_not_found":     groups_not_found,
            "status":               "completed",
        }

        logger.info(
            "SAML app onboarding completed: name='%s' appId=%s",
            request.display_name, app_id,
        )
        return result

    except GraphAPIError as exc:
        logger.error(
            "SAML app onboarding failed for '%s': %s",
            request.display_name, exc,
        )
        await audit.log_failure(
            flow_name=FlowName.APP_ONBOARDING,
            principal_id=request.display_name,
            requested_by=request.requested_by,
            error=exc,
            details={
                "display_name":       request.display_name,
                "app_type":           request.app_type.value,
                "graph_ops_completed": graph_ops,
            },        )
        raise


# ── Internal helpers ──────────────────────────────────────────────────────────

async def _create_app(
    client: GraphClient,
    request: SAMLAppOnboardingRequest,
    graph_ops: list[str],
) -> tuple[str, str, str]:
    """
    Creates the app registration and service principal.
    Returns (object_id, app_id, sp_id).

    Gallery     : single API call via applicationTemplates/{id}/instantiate
    Non-gallery : two separate API calls (POST /applications + POST /servicePrincipals)
    """
    if request.app_type == AppType.GALLERY:
        return await _create_gallery_app(client, request, graph_ops)
    return await _create_non_gallery_app(client, request, graph_ops)


async def _create_gallery_app(
    client: GraphClient,
    request: SAMLAppOnboardingRequest,
    graph_ops: list[str],
) -> tuple[str, str, str]:
    """
    Gallery app — instantiate from Microsoft app gallery template.
    Creates app registration + service principal in one API call.
    """
    if not request.template_id:
        raise GraphAPIError(
            f"template_id is required for gallery app '{request.display_name}'.",
            status_code=400,
        )

    logger.info(
        "Gallery app: instantiating template=%s name='%s'",
        request.template_id, request.display_name,
    )

    result    = await client.instantiate_gallery_app(
        request.template_id, request.display_name
    )
    object_id = result["application"]["id"]
    app_id    = result["application"]["appId"]
    sp_id     = result["servicePrincipal"]["id"]

    graph_ops.append(
        f"POST /applicationTemplates/{request.template_id}/instantiate"
        f" -> objectId={object_id} appId={app_id} spId={sp_id}"
    )
    logger.info(
        "Gallery app created: objectId=%s appId=%s spId=%s",
        object_id, app_id, sp_id,
    )
    return object_id, app_id, sp_id


async def _create_non_gallery_app(
    client: GraphClient,
    request: SAMLAppOnboardingRequest,
    graph_ops: list[str],
) -> tuple[str, str, str]:
    """
    Non-gallery app — create app registration then service principal separately.

    Entra ID has eventual consistency — after POST /applications, the appId
    is not immediately visible to POST /servicePrincipals. We wait briefly
    and retry with exponential backoff to handle this propagation delay.
    """
    import asyncio

    logger.info(
        "Non-gallery app: creating registration for '%s'", request.display_name
    )

    # Create app registration
    app = await client.create_application({
        "displayName":    request.display_name,
        "signInAudience": "AzureADMyOrg",
    })
    object_id = app["id"]
    app_id    = app["appId"]
    graph_ops.append(
        f"POST /applications -> objectId={object_id} appId={app_id}"
    )
    logger.info(
        "App registration created: objectId=%s appId=%s — waiting for propagation...",
        object_id, app_id,
    )

    # ── Step 2: Create service principal with propagation retry ─────────────
    # Entra ID is eventually consistent. After POST /applications, the appId
    # may not yet be visible on all directory replicas, causing POST
    # /servicePrincipals to return 400. After SP creation, a GET is used
    # to confirm the SP is fully reachable before returning — preventing
    # 404 errors on subsequent PATCH/POST calls that use the SP ID.
    sp_id    = None
    last_exc = None

    # Phase A: Create the service principal (retry on 400 propagation errors)
    sp_create_delays = [5, 10, 15]
    for attempt, delay in enumerate(sp_create_delays, start=1):
        logger.info(
            "SP creation attempt %d/%d — waiting %ds for appId propagation...",
            attempt, len(sp_create_delays), delay,
        )
        await asyncio.sleep(delay)
        try:
            sp    = await client.create_service_principal(app_id)
            sp_id = sp["id"]
            logger.info("Service principal created: spId=%s", sp_id)
            break
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "SP creation attempt %d failed: %s — %s",
                attempt, type(exc).__name__, exc,
            )
            if attempt == len(sp_create_delays):
                # All retries exhausted — clean up orphaned app registration
                logger.error(
                    "All SP creation attempts failed. "
                    "Cleaning up orphaned app registration objectId=%s.", object_id,
                )
                try:
                    await client.delete_application(object_id)
                    logger.info("Orphaned app registration deleted: %s", object_id)
                except Exception as cleanup_exc:
                    logger.error(
                        "Failed to clean up orphaned app %s: %s",
                        object_id, cleanup_exc,
                    )
                raise last_exc

    # Phase B: Confirm SP is reachable before returning
    # Even after creation succeeds, subsequent PATCH/POST calls on the SP
    # can return 404 if the SP hasn't propagated across all replicas yet.
    # We verify with a GET and retry until reachable.
    sp_verify_delays = [4, 6, 8, 10]
    sp_reachable     = False
    for attempt, delay in enumerate(sp_verify_delays, start=1):
        logger.info(
            "SP reachability check %d/%d — waiting %ds...",
            attempt, len(sp_verify_delays), delay,
        )
        await asyncio.sleep(delay)
        try:
            await client._get(f"servicePrincipals/{sp_id}",
                              params={"$select": "id,appId"})
            sp_reachable = True
            logger.info("SP %s confirmed reachable after %d attempt(s).", sp_id, attempt)
            break
        except Exception as exc:
            logger.warning(
                "SP reachability check %d failed: %s", attempt, exc
            )
            if attempt == len(sp_verify_delays):
                logger.error(
                    "SP %s not reachable after all checks. "
                    "Proceeding anyway — subsequent steps may retry.", sp_id,
                )

    graph_ops.append(f"POST /servicePrincipals -> spId={sp_id}")
    logger.info(
        "Non-gallery app fully ready: objectId=%s appId=%s spId=%s reachable=%s",
        object_id, app_id, sp_id, sp_reachable,
    )
    return object_id, app_id, sp_id

