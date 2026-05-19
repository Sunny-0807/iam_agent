import logging
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from shared.config import config
from shared.exceptions import GraphAPIError, ResourceNotFoundError
from shared.graph_client.auth import get_access_token

logger = logging.getLogger(__name__)


class GraphClient:
    """
    Authenticated Microsoft Graph API client.

    - All requests use a bearer token from managed identity (or service principal in dev).
    - Retries automatically on transient errors (429, 503, network failures).
    - Raises typed exceptions so callers don't need to inspect raw HTTP responses.

    Usage:
        client = GraphClient()

        # Create a user
        user = await client.create_user({...})

        # Disable a user
        await client.disable_user("object-id-here")
    """

    def __init__(self):
        self._base_url = config.graph_base_url

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {get_access_token()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "ConsistencyLevel": "eventual",  # Required for advanced queries
        }

    # ── Core HTTP methods ─────────────────────────────────────────────────────

    @retry(
        retry=retry_if_exception_type(GraphAPIError),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def _get(self, path: str, params: dict | None = None) -> dict[str, Any]:
        url = f"{self._base_url}/{path.lstrip('/')}"
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(url, headers=self._headers(), params=params)
            return self._handle_response(response, "GET", url)

    @retry(
        retry=retry_if_exception_type(GraphAPIError),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def _post(self, path: str, body: dict) -> dict[str, Any]:
        url = f"{self._base_url}/{path.lstrip('/')}"
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(url, headers=self._headers(), json=body)
            return self._handle_response(response, "POST", url)

    @retry(
        retry=retry_if_exception_type(GraphAPIError),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def _patch(self, path: str, body: dict) -> None:
        url = f"{self._base_url}/{path.lstrip('/')}"
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.patch(url, headers=self._headers(), json=body)
            self._handle_response(response, "PATCH", url, expect_body=False)

    @retry(
        retry=retry_if_exception_type(GraphAPIError),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def _delete(self, path: str) -> None:
        url = f"{self._base_url}/{path.lstrip('/')}"
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.delete(url, headers=self._headers())
            self._handle_response(response, "DELETE", url, expect_body=False)

    def _handle_response(
        self,
        response: httpx.Response,
        method: str,
        url: str,
        expect_body: bool = True,
    ) -> dict[str, Any] | None:
        logger.debug("%s %s → %d", method, url, response.status_code)

        if response.status_code == 404:
            raise ResourceNotFoundError("Graph resource", url)

        if response.status_code == 429:
            # Respect Retry-After header when rate limited
            retry_after = int(response.headers.get("Retry-After", 10))
            logger.warning("Rate limited by Graph API. Retry after %ds.", retry_after)
            raise GraphAPIError("Rate limited", status_code=429, endpoint=url)

        if response.status_code >= 400:
            error_body = response.json() if response.content else {}
            error_msg = error_body.get("error", {}).get("message", response.text)
            raise GraphAPIError(error_msg, status_code=response.status_code, endpoint=url)

        if expect_body and response.content:
            return response.json()
        return {}

    # =========================================================================
    # USER OPERATIONS
    # =========================================================================

    async def create_user(self, payload: dict) -> dict[str, Any]:
        """
        POST /users
        Creates a new user in Entra ID.
        Returns the created user object including the object ID.
        """
        logger.info("Creating user: %s", payload.get("userPrincipalName"))
        result = await self._post("users", payload)
        logger.info("User created with ID: %s", result.get("id"))
        return result

    async def get_user(self, user_id: str) -> dict[str, Any]:
        """
        GET /users/{id}
        Fetch a user by object ID or userPrincipalName.
        """
        return await self._get(f"users/{user_id}")

    async def disable_user(self, user_id: str) -> None:
        """
        PATCH /users/{id}
        Disables the user account (accountEnabled = false).
        """
        logger.info("Disabling user: %s", user_id)
        await self._patch(f"users/{user_id}", {"accountEnabled": False})

    async def revoke_user_sessions(self, user_id: str) -> None:
        """
        POST /users/{id}/revokeSignInSessions
        Immediately invalidates all active sessions and refresh tokens.
        """
        logger.info("Revoking sessions for user: %s", user_id)
        await self._post(f"users/{user_id}/revokeSignInSessions", {})

    async def delete_user(self, user_id: str) -> None:
        """
        DELETE /users/{id}
        Soft-deletes the user (moves to deleted items, recoverable for 30 days).
        """
        logger.info("Deleting user: %s", user_id)
        await self._delete(f"users/{user_id}")

    async def assign_license(self, user_id: str, sku_id: str) -> dict[str, Any]:
        """
        POST /users/{id}/assignLicense
        Assigns a Microsoft 365 license to the user.
        """
        logger.info("Assigning license %s to user %s", sku_id, user_id)
        payload = {
            "addLicenses": [{"skuId": sku_id}],
            "removeLicenses": [],
        }
        return await self._post(f"users/{user_id}/assignLicense", payload)

    async def remove_all_licenses(self, user_id: str) -> None:
        """
        POST /users/{id}/assignLicense
        Removes all licenses from the user (required before deletion).
        """
        logger.info("Removing all licenses from user: %s", user_id)
        user = await self._get(f"users/{user_id}", params={"$select": "assignedLicenses"})
        sku_ids = [lic["skuId"] for lic in user.get("assignedLicenses", [])]
        if sku_ids:
            await self._post(f"users/{user_id}/assignLicense", {
                "addLicenses": [],
                "removeLicenses": sku_ids,
            })

    async def get_user_memberships(self, user_id: str) -> list[dict]:
        """
        GET /users/{id}/memberOf
        Returns all groups and directory roles the user belongs to.
        """
        result = await self._get(f"users/{user_id}/memberOf")
        return result.get("value", [])

    async def force_password_reset(self, user_id: str) -> None:
        """
        PATCH /users/{id}
        Forces the user to reset their password at next sign-in.
        """
        logger.info("Forcing password reset for user: %s", user_id)
        await self._patch(f"users/{user_id}", {
            "passwordProfile": {
                "forceChangePasswordNextSignIn": True,
                "forceChangePasswordNextSignInWithMfa": True,
            }
        })

    # =========================================================================
    # GROUP OPERATIONS
    # =========================================================================

    async def add_user_to_group(self, group_id: str, user_id: str) -> None:
        """
        POST /groups/{id}/members/$ref
        Adds a user to a security or Microsoft 365 group.
        """
        logger.info("Adding user %s to group %s", user_id, group_id)
        payload = {
            "@odata.id": f"https://graph.microsoft.com/v1.0/directoryObjects/{user_id}"
        }
        await self._post(f"groups/{group_id}/members/$ref", payload)

    async def remove_user_from_group(self, group_id: str, user_id: str) -> None:
        """
        DELETE /groups/{id}/members/{user_id}/$ref
        Removes a user from a group.
        """
        logger.info("Removing user %s from group %s", user_id, group_id)
        await self._delete(f"groups/{group_id}/members/{user_id}/$ref")

    async def remove_user_from_all_groups(self, user_id: str) -> list[str]:
        """
        Removes a user from all groups they belong to.
        Returns list of group IDs removed from.
        """
        memberships = await self.get_user_memberships(user_id)
        group_ids = [
            m["id"] for m in memberships
            if m.get("@odata.type") == "#microsoft.graph.group"
        ]
        for group_id in group_ids:
            await self.remove_user_from_group(group_id, user_id)
        logger.info("Removed user %s from %d groups.", user_id, len(group_ids))
        return group_ids

    # =========================================================================
    # ROLE OPERATIONS
    # =========================================================================

    async def assign_directory_role(self, user_id: str, role_definition_id: str) -> dict:
        """
        POST /roleManagement/directory/roleAssignments
        Assigns an Entra ID directory role to a user.
        """
        logger.info("Assigning role %s to user %s", role_definition_id, user_id)
        payload = {
            "principalId": user_id,
            "roleDefinitionId": role_definition_id,
            "directoryScopeId": "/",
        }
        return await self._post("roleManagement/directory/roleAssignments", payload)

    async def assign_app_role(
        self,
        service_principal_id: str,
        user_id: str,
        app_role_id: str,
    ) -> dict:
        """
        POST /servicePrincipals/{id}/appRoleAssignments
        Assigns an app role to a user for a specific application.
        """
        logger.info("Assigning app role to user %s on SP %s", user_id, service_principal_id)
        payload = {
            "principalId": user_id,
            "resourceId": service_principal_id,
            "appRoleId": app_role_id,
        }
        return await self._post(
            f"servicePrincipals/{service_principal_id}/appRoleAssignments",
            payload,
        )

    # =========================================================================
    # APPLICATION OPERATIONS
    # =========================================================================

    async def create_application(self, payload: dict) -> dict[str, Any]:
        """
        POST /applications
        Registers a new application in Entra ID.
        Returns the application object including appId and object ID.
        """
        logger.info("Registering application: %s", payload.get("displayName"))
        result = await self._post("applications", payload)
        logger.info("Application registered. appId: %s, objectId: %s",
                    result.get("appId"), result.get("id"))
        return result

    async def get_application(self, object_id: str) -> dict[str, Any]:
        """
        GET /applications/{id}
        Fetch application by object ID.
        """
        return await self._get(f"applications/{object_id}")

    async def delete_application(self, object_id: str) -> None:
        """
        DELETE /applications/{id}
        Soft-deletes the application registration.
        """
        logger.info("Deleting application object: %s", object_id)
        await self._delete(f"applications/{object_id}")

    async def create_service_principal(self, app_id: str) -> dict[str, Any]:
        """
        POST /servicePrincipals
        Creates a service principal (enterprise app) for a registered application.

        Tags explanation:
        - "WindowsAzureActiveDirectoryIntegratedApp" — makes the SP appear
          in the Enterprise Applications blade in the Entra ID portal.
        - "HideApp" is intentionally NOT included — we want it visible.

        Without these tags, the SP is created in the directory but does NOT
        appear under Enterprise Applications, which is the common symptom of
        a missing tag on the service principal.
        """
        logger.info("Creating service principal for appId: %s", app_id)
        return await self._post("servicePrincipals", {
            "appId": app_id,
            "tags": [
                "WindowsAzureActiveDirectoryIntegratedApp",
            ],
        })

    async def delete_service_principal(self, sp_id: str) -> None:
        """
        DELETE /servicePrincipals/{id}
        Deletes the service principal associated with an application.
        """
        logger.info("Deleting service principal: %s", sp_id)
        await self._delete(f"servicePrincipals/{sp_id}")

    async def get_app_role_assignments(self, sp_id: str) -> list[dict]:
        """
        GET /servicePrincipals/{id}/appRoleAssignedTo
        Returns all users assigned to this application.
        """
        result = await self._get(f"servicePrincipals/{sp_id}/appRoleAssignedTo")
        return result.get("value", [])

    async def revoke_app_role_assignment(self, sp_id: str, assignment_id: str) -> None:
        """
        DELETE /servicePrincipals/{id}/appRoleAssignedTo/{assignmentId}
        Revokes a user's assignment to an application.
        """
        await self._delete(f"servicePrincipals/{sp_id}/appRoleAssignedTo/{assignment_id}")

    async def revoke_all_app_assignments(self, sp_id: str) -> int:
        """
        Revokes all user assignments for a given service principal.
        Returns the count of revoked assignments.
        """
        assignments = await self.get_app_role_assignments(sp_id)
        for assignment in assignments:
            await self.revoke_app_role_assignment(sp_id, assignment["id"])
        logger.info("Revoked %d assignments for SP %s.", len(assignments), sp_id)
        return len(assignments)

    async def add_application_owner(self, object_id: str, owner_id: str) -> None:
        """
        POST /applications/{id}/owners/$ref
        Adds an owner to an application registration.
        """
        logger.info("Adding owner %s to application %s", owner_id, object_id)
        payload = {
            "@odata.id": f"https://graph.microsoft.com/v1.0/directoryObjects/{owner_id}"
        }
        await self._post(f"applications/{object_id}/owners/$ref", payload)

    # =========================================================================
    # SAML SSO OPERATIONS
    # =========================================================================

    async def instantiate_gallery_app(
        self, template_id: str, display_name: str
    ) -> dict[str, Any]:
        """
        POST /applicationTemplates/{id}/instantiate
        Creates both the app registration and service principal in one call
        from a Microsoft gallery app template.

        Returns dict with both 'application' and 'servicePrincipal' objects.
        """
        logger.info(
            "Instantiating gallery app: template=%s name='%s'",
            template_id, display_name,
        )
        result = await self._post(
            f"applicationTemplates/{template_id}/instantiate",
            {"displayName": display_name},
        )
        logger.info(
            "Gallery app instantiated: appId=%s spId=%s",
            result.get("application", {}).get("appId"),
            result.get("servicePrincipal", {}).get("id"),
        )
        return result

    async def set_saml_sso_mode(self, sp_id: str) -> None:
        """
        PATCH /servicePrincipals/{id}
        Sets the SSO mode to SAML on the service principal.
        Must be called before configuring SAML URLs or generating a certificate.
        """
        logger.info("Setting SAML SSO mode for SP: %s", sp_id)
        await self._patch(
            f"servicePrincipals/{sp_id}",
            {"preferredSingleSignOnMode": "saml"},
        )

    async def set_saml_urls(
        self,
        object_id: str,
        sp_id: str,
        entity_id: str,
        reply_url: str,
        sign_on_url: str | None = None,
    ) -> None:
        """
        PATCH /applications/{id}  +  PATCH /servicePrincipals/{id}
        Sets the SAML basic configuration:
            - Entity ID (Identifier)   -> identifierUris on app registration
            - Reply URL (ACS URL)      -> replyUrls on app + web.redirectUris
            - Sign-on URL (optional)   -> on service principal

        Both the app registration and service principal need to be updated
        for the config to reflect correctly in the Entra ID portal.
        """
        logger.info(
            "Setting SAML URLs for app objectId=%s entity_id=%s reply_url=%s",
            object_id, entity_id, reply_url,
        )

        # Update app registration
        app_patch: dict[str, Any] = {
            "identifierUris": [entity_id],
            "web": {
                "redirectUris": [reply_url],
            },
        }
        await self._patch(f"applications/{object_id}", app_patch)

        # Update service principal with login URL if provided
        if sign_on_url:
            await self._patch(
                f"servicePrincipals/{sp_id}",
                {"loginUrl": sign_on_url},
            )

    async def add_token_signing_certificate(self, sp_id: str) -> dict[str, Any]:
        """
        POST /servicePrincipals/{id}/addTokenSigningCertificate
        Auto-generates a self-signed SAML token signing certificate.
        Entra ID uses this certificate to sign SAML assertions sent to the SP.

        Returns the certificate object including:
            - thumbprint   : used to identify the certificate
            - endDateTime  : certificate expiry (3 years by default)
            - keyId        : unique ID of the key credential
        """
        logger.info("Generating SAML signing certificate for SP: %s", sp_id)
        result = await self._post(
            f"servicePrincipals/{sp_id}/addTokenSigningCertificate",
            {
                "displayName": "CN=SAML Signing Certificate",
                "endDateTime": _cert_expiry_date(),
            },
        )
        logger.info(
            "SAML certificate generated: thumbprint=%s expiry=%s",
            result.get("thumbprint"), result.get("endDateTime"),
        )
        return result

    async def assign_group_to_app(
        self, sp_id: str, group_id: str
    ) -> dict[str, Any]:
        """
        POST /servicePrincipals/{id}/appRoleAssignments
        Assigns a group to an enterprise application.
        Uses the default app role (all zeros UUID) since SAML apps
        typically don't define custom roles.
        """
        logger.info("Assigning group %s to SP %s", group_id, sp_id)
        payload = {
            "principalId": group_id,
            "resourceId":  sp_id,
            "appRoleId":   "00000000-0000-0000-0000-000000000000",
        }
        return await self._post(
            f"servicePrincipals/{sp_id}/appRoleAssignments", payload
        )
    

    # =========================================================================
    # RESOLVER METHODS — groups and licenses
    # =========================================================================

    async def list_groups(self) -> list[dict]:
        """
        GET /groups?$select=id,displayName&$orderby=displayName
        Returns all security and M365 groups in the tenant.
        Used to populate group dropdowns in the Streamlit UI.
        """
        result = await self._get(
            "groups",
            params={
                "$select": "id,displayName",
                "$orderby": "displayName",
                "$top": "999",
            },
        )
        return result.get("value", [])

    async def get_group_by_name(self, name: str) -> dict | None:
        """
        GET /groups?$filter=displayName eq '{name}'
        Resolves a group display name to its object ID.
        Returns the group dict or None if not found.
        """
        result = await self._get(
            "groups",
            params={
                "$filter": f"displayName eq '{name}'",
                "$select": "id,displayName",
            },
        )
        groups = result.get("value", [])
        return groups[0] if groups else None

    async def list_licenses(self) -> list[dict]:
        """
        GET /subscribedSkus
        Returns all licenses subscribed to in the tenant with
        consumed and available seat counts.
        Used to populate license dropdowns in the Streamlit UI.
        """
        result = await self._get(
            "subscribedSkus",
            params={"$select": "skuId,skuPartNumber,consumedUnits,prepaidUnits"},
        )
        return result.get("value", [])

    async def get_license_sku_by_name(self, name: str) -> dict | None:
        """
        Resolves a license name to its full license object (including skuId).

        Tries three strategies in order, stopping at the first match:

        1. Exact skuPartNumber match (e.g. "FLOW_FREE" == "FLOW_FREE")
        2. Friendly name mapping — looks up the input in a known map of
           human-readable names to skuPartNumber (e.g. "Microsoft Power Automate Free" -> "FLOW_FREE")
        3. Normalised substring match — strips spaces/punctuation and checks
           if the normalised input is contained in the normalised skuPartNumber
           or vice versa. Handles partial names like "Power Automate Free".

        Args:
            name: Human-friendly license name or skuPartNumber.

        Returns:
            License dict with skuId, skuPartNumber etc., or None if not found.
        """
        licenses  = await self.list_licenses()
        name_up   = name.strip().upper()
        name_norm = _normalise_sku(name)

        # Strategy 1 — exact skuPartNumber match
        for lic in licenses:
            if lic.get("skuPartNumber", "").upper() == name_up:
                logger.info("License matched by skuPartNumber: %s", lic["skuPartNumber"])
                return lic

        # Strategy 2 — friendly name map lookup
        sku_from_map = _FRIENDLY_NAME_TO_SKU.get(name_up)
        if sku_from_map:
            for lic in licenses:
                if lic.get("skuPartNumber", "").upper() == sku_from_map.upper():
                    logger.info(
                        "License matched via friendly name map: '%s' -> %s",
                        name, lic["skuPartNumber"],
                    )
                    return lic

        # Strategy 3 — normalised substring match
        for lic in licenses:
            sku_norm = _normalise_sku(lic.get("skuPartNumber", ""))
            if name_norm in sku_norm or sku_norm in name_norm:
                logger.info(
                    "License matched by normalised substring: '%s' ~ %s",
                    name, lic["skuPartNumber"],
                )
                return lic

        logger.warning(
            "License not found for name='%s'. "
            "Available SKUs: %s",
            name,
            [l.get("skuPartNumber") for l in licenses],
        )
        return None

    async def get_user_by_upn(self, upn: str) -> dict[str, Any]:
        """
        GET /users/{userPrincipalName}
        Fetch a user by their UPN — useful when only the UPN is known.
        Returns the user object including id, displayName, assignedLicenses.
        """
        return await self._get(
            f"users/{upn}",
            params={"$select": "id,displayName,userPrincipalName,accountEnabled,assignedLicenses,department,jobTitle"},
        )

    async def get_user_licenses(self, user_id: str) -> list[dict]:
        """
        GET /users/{id}?$select=assignedLicenses
        Returns the list of license objects assigned to the user.
        Each object contains skuId which can be matched against subscribedSkus.
        """
        result = await self._get(
            f"users/{user_id}",
            params={"$select": "assignedLicenses"},
        )
        return result.get("assignedLicenses", [])

    async def remove_license(self, user_id: str, sku_id: str) -> None:
        """
        POST /users/{id}/assignLicense
        Removes a specific license from the user by SKU ID.
        Unlike remove_all_licenses(), this targets a single SKU.
        """
        logger.info("Removing license %s from user %s", sku_id, user_id)
        await self._post(f"users/{user_id}/assignLicense", {
            "addLicenses":    [],
            "removeLicenses": [sku_id],
        })

    async def set_usage_location(self, user_id: str, country_code: str) -> None:
        """
        PATCH /users/{id}
        Sets the usageLocation on a user — required before any license can be assigned.
        country_code must be a valid ISO 3166-1 alpha-2 code e.g. 'US', 'IN', 'GB'.
        """
        logger.info("Setting usageLocation=%s for user %s", country_code, user_id)
        await self._patch(f"users/{user_id}", {"usageLocation": country_code})

    async def get_usage_location(self, user_id: str) -> str | None:
        """
        GET /users/{id}?$select=usageLocation
        Returns the current usageLocation of the user, or None if not set.
        """
        result = await self._get(
            f"users/{user_id}",
            params={"$select": "usageLocation"},
        )
        return result.get("usageLocation") or None

    # =========================================================================
    # SEARCH METHODS
    # =========================================================================

    async def search_users(self, query: str) -> list[dict]:
        """
        GET /users?$search="displayName:{query}" OR "userPrincipalName:{query}"
        Searches users by display name or UPN prefix.
        Returns up to 10 matching users with id, displayName, userPrincipalName.
        Requires ConsistencyLevel: eventual header (already set in _headers).
        """
        result = await self._get(
            "users",
            params={
                "$search":  f'"displayName:{query}" OR "userPrincipalName:{query}"',
                "$select":  "id,displayName,userPrincipalName",
                "$top":     "10",
                "$orderby": "displayName",
            },
        )
        return result.get("value", [])

    async def search_applications(self, query: str) -> list[dict]:
        """
        GET /applications?$search="displayName:{query}"
        Searches app registrations by display name.
        Returns matching apps with id (objectId), appId, displayName.
        """
        result = await self._get(
            "applications",
            params={
                "$search":  f'"displayName:{query}"',
                "$select":  "id,appId,displayName",
                "$top":     "10",
                "$orderby": "displayName",
            },
        )
        return result.get("value", [])

    async def get_service_principal_by_app_id(self, app_id: str) -> dict | None:
        """
        GET /servicePrincipals?$filter=appId eq '{app_id}'
        Resolves an application's appId to its service principal object ID.
        Returns the service principal dict or None if not found.
        """
        result = await self._get(
            "servicePrincipals",
            params={
                "$filter": f"appId eq '{app_id}'",
                "$select": "id,appId,displayName",
            },
        )
        sps = result.get("value", [])
        return sps[0] if sps else None

    async def search_users(self, query: str) -> list[dict]:
        """
        GET /users?$search="displayName:{query}" OR startsWith(userPrincipalName)
        Searches users by display name or UPN prefix.
        Returns list of {id, displayName, userPrincipalName}.
        Requires ConsistencyLevel: eventual header (already set in _headers()).
        """
        if not query or len(query.strip()) < 2:
            return []
        result = await self._get(
            "users",
            params={
                "$search":  f'"displayName:{query}" OR "userPrincipalName:{query}"',
                "$select":  "id,displayName,userPrincipalName",
                "$top":     "15",
                "$orderby": "displayName",
            },
        )
        return result.get("value", [])

    async def search_applications(self, query: str) -> list[dict]:
        """
        GET /applications?$search="displayName:{query}"
        Searches app registrations by display name, then resolves the
        service principal ID for each result.
        Returns list of {object_id, app_id, display_name, service_principal_id}.
        """
        if not query or len(query.strip()) < 2:
            return []

        result = await self._get(
            "applications",
            params={
                "$search": f'"displayName:{query}"',
                "$select": "id,appId,displayName",
                "$top":    "15",
            },
        )
        apps = result.get("value", [])

        resolved = []
        for app in apps:
            sp_id = await self._resolve_sp_id(app["appId"])
            resolved.append({
                "object_id":            app["id"],
                "app_id":               app["appId"],
                "display_name":         app["displayName"],
                "service_principal_id": sp_id or "",
            })
        return resolved

    async def _resolve_sp_id(self, app_id: str) -> str | None:
        """
        GET /servicePrincipals?$filter=appId eq '{app_id}'
        Resolves an application's appId to its service principal object ID.
        Returns the SP object ID or None if not found.
        """
        try:
            result = await self._get(
                "servicePrincipals",
                params={
                    "$filter": f"appId eq '{app_id}'",
                    "$select": "id",
                },
            )
            sps = result.get("value", [])
            return sps[0]["id"] if sps else None
        except Exception:
            return None

    async def set_manager(self, user_id: str, manager_id: str) -> None:
        """
        PUT /users/{id}/manager/$ref
        Sets the manager for a user.
        manager_id must be the object ID of the manager.
        """
        logger.info("Setting manager %s for user %s", manager_id, user_id)
        await self._post(
            f"users/{user_id}/manager/$ref",
            {"@odata.id": f"https://graph.microsoft.com/v1.0/directoryObjects/{manager_id}"},
        )

    async def tag_as_enterprise_app(self, sp_id: str) -> None:
        """
        PATCH /servicePrincipals/{id}
        Adds the WindowsAzureActiveDirectoryIntegratedApp tag to an existing
        service principal so it appears in the Enterprise Applications blade.

        Use this to fix service principals created without the correct tags.
        """
        logger.info("Tagging SP %s as enterprise app...", sp_id)
        await self._patch(
            f"servicePrincipals/{sp_id}",
            {"tags": ["WindowsAzureActiveDirectoryIntegratedApp"]},
        )
        logger.info("SP %s tagged as enterprise app successfully.", sp_id)
        


# ── Helpers ───────────────────────────────────────────────────────────────────

def _cert_expiry_date() -> str:
    """Returns an ISO 8601 date string 3 years from now — default cert lifetime."""
    from datetime import datetime, timezone, timedelta
    expiry = datetime.now(timezone.utc) + timedelta(days=3 * 365)
    return expiry.strftime("%Y-%m-%dT%H:%M:%SZ")


# ── License name helpers ──────────────────────────────────────────────────────

def _normalise_sku(s: str) -> str:
    """
    Normalise a string for fuzzy SKU matching.
    Uppercases, removes spaces, underscores, hyphens, and common filler words.
    e.g. "Microsoft Power Automate Free" -> "POWRAUTOMATEFREE"
    """
    import re
    s = s.upper()
    # Remove common Microsoft prefixes that don't appear in SKU codes
    for prefix in ["MICROSOFT ", "MS ", "AZURE ", "OFFICE ", "DYNAMICS "]:
        s = s.replace(prefix, "")
    # Remove all non-alphanumeric characters
    s = re.sub(r"[^A-Z0-9]", "", s)
    return s


# Map of human-friendly license display names to their skuPartNumber codes.
# Keys are UPPERCASED for case-insensitive lookup.
# Add entries here as needed for licenses in your tenant.
_FRIENDLY_NAME_TO_SKU: dict[str, str] = {
    # Power Platform
    "MICROSOFT POWER AUTOMATE FREE":          "FLOW_FREE",
    "POWER AUTOMATE FREE":                    "FLOW_FREE",
    "MICROSOFT FLOW FREE":                    "FLOW_FREE",
    "POWER AUTOMATE PER USER":                "FLOW_PER_USER",
    "POWER AUTOMATE PER USER PLAN":           "FLOW_PER_USER",
    "POWER APPS PER USER":                    "POWERAPPS_PER_USER",
    "POWER APPS PER USER PLAN":               "POWERAPPS_PER_USER",

    # Microsoft 365
    "MICROSOFT 365 BUSINESS BASIC":           "O365_BUSINESS_ESSENTIALS",
    "MICROSOFT 365 BUSINESS STANDARD":        "O365_BUSINESS_PREMIUM",
    "MICROSOFT 365 BUSINESS PREMIUM":         "SPB",
    "MICROSOFT 365 E3":                       "SPE_E3",
    "MICROSOFT 365 E5":                       "SPE_E5",
    "MICROSOFT 365 F1":                       "M365_F1",
    "MICROSOFT 365 F3":                       "SPE_F1",
    "OFFICE 365 E1":                          "STANDARDPACK",
    "OFFICE 365 E3":                          "ENTERPRISEPACK",
    "OFFICE 365 E5":                          "ENTERPRISEPREMIUM",
    "OFFICE 365 F3":                          "DESKLESSPACK",

    # Azure AD / Entra
    "AZURE ACTIVE DIRECTORY PREMIUM P1":      "AAD_PREMIUM",
    "AZURE ACTIVE DIRECTORY PREMIUM P2":      "AAD_PREMIUM_P2",
    "MICROSOFT ENTRA ID P1":                  "AAD_PREMIUM",
    "MICROSOFT ENTRA ID P2":                  "AAD_PREMIUM_P2",
    "ENTRA ID P1":                            "AAD_PREMIUM",
    "ENTRA ID P2":                            "AAD_PREMIUM_P2",

    # Intune
    "MICROSOFT INTUNE":                       "INTUNE_A",
    "INTUNE":                                 "INTUNE_A",

    # Dynamics 365
    "DYNAMICS 365 SALES ENTERPRISE":          "DYN365_ENTERPRISE_SALES",
    "DYNAMICS 365 CUSTOMER SERVICE ENTERPRISE": "DYN365_ENTERPRISE_CUSTOMER_SERVICE",

    # Power BI
    "POWER BI PRO":                           "POWER_BI_PRO",
    "POWER BI PREMIUM PER USER":              "POWER_BI_PREMIUM_PER_USER",
    "POWER BI FREE":                          "POWER_BI_STANDARD",

    # Defender / Security
    "MICROSOFT DEFENDER FOR ENDPOINT P1":     "MDATP_Server",
    "MICROSOFT DEFENDER FOR ENDPOINT P2":     "WIN_DEF_ATP",
    "MICROSOFT DEFENDER FOR OFFICE 365 P1":   "ATP_ENTERPRISE",
    "MICROSOFT DEFENDER FOR OFFICE 365 P2":   "THREAT_INTELLIGENCE",

    # Visio / Project
    "VISIO PLAN 1":                           "VISIOONLINE_PLAN1",
    "VISIO PLAN 2":                           "VISIOCLIENT",
    "PROJECT PLAN 1":                         "PROJECTESSENTIALS",
    "PROJECT PLAN 3":                         "PROJECTPREMIUM",
    "PROJECT PLAN 5":                         "PROJECTPROFESSIONAL",

    # Teams
    "MICROSOFT TEAMS ESSENTIALS":             "TEAMS_ESSENTIALS",
    "MICROSOFT TEAMS EXPLORATORY":            "TEAMS_EXPLORATORY",
    "MICROSOFT TEAMS ROOMS PRO":              "MTR_PREM",
}

