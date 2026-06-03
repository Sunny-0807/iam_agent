import logging

from shared.exceptions import PolicyViolationError
from shared.models import (
    AppOffboardingRequest,
    AppOnboardingRequest,
    FlowName,
    RiskIsolationRequest,
    RiskSeverity,
    UserOffboardingRequest,
    UserOnboardingRequest,
)

logger = logging.getLogger(__name__)

_FORBIDDEN_SCOPES = {
    "Directory.ReadWrite.All",
    "RoleManagement.ReadWrite.Directory",
    "User.ReadWrite.All",
}

_HIGH_PRIVILEGE_DEPARTMENTS = {"security", "it", "infrastructure", "devops"}


class PolicyEngine:
    """
    Enforces IAM access policies before any Graph API action is executed.
    """

    def check(self, flow_name: FlowName, request: object) -> None:
        checker = {
            FlowName.USER_ONBOARDING:  self._check_user_onboarding,
            FlowName.USER_OFFBOARDING: self._check_user_offboarding,
            FlowName.RISK_ISOLATION:   self._check_risk_isolation,
            FlowName.APP_ONBOARDING:   self._check_app_onboarding,
            FlowName.APP_OFFBOARDING:  self._check_app_offboarding,
        }.get(flow_name)

        if not checker:
            raise PolicyViolationError(f"No policy defined for flow: {flow_name}")

        logger.info("Running policy checks for flow: %s", flow_name.value)
        checker(request)
        logger.info("Policy checks passed for flow: %s", flow_name.value)

    def _check_user_onboarding(self, request: UserOnboardingRequest) -> None:
        if "@" not in request.user_principal_name:
            raise PolicyViolationError(
                f"Invalid userPrincipalName format: '{request.user_principal_name}'. "
                "Must include an @ domain."
            )

        dept = request.department.lower()
        if dept in _HIGH_PRIVILEGE_DEPARTMENTS and not request.manager_id:
            raise PolicyViolationError(
                f"Onboarding into department '{request.department}' requires a manager_id "
                "for approval. This is a high-privilege department."
            )

        # Groups are OPTIONAL — can be assigned later via Manage Groups tab or AI assistant.
        # Do NOT enforce group_ids here.
        if not request.group_ids:
            logger.info(
                "User %s onboarded with no group assignments. "
                "Groups can be assigned later via Manage Groups.",
                request.user_principal_name,
            )

        logger.debug("User onboarding policy passed for: %s", request.user_principal_name)

    def _check_user_offboarding(self, request: UserOffboardingRequest) -> None:
        if not request.reason or len(request.reason.strip()) < 5:
            raise PolicyViolationError(
                "User offboarding requires a reason of at least 5 characters."
            )
        if not request.revoke_sessions:
            raise PolicyViolationError(
                "revoke_sessions cannot be False for offboarding."
            )
        logger.debug("User offboarding policy passed for: %s", request.user_principal_name)

    def _check_risk_isolation(self, request: RiskIsolationRequest) -> None:
        if not request.alert_id:
            raise PolicyViolationError(
                "Risk isolation requires an alert_id for audit traceability."
            )
        if request.severity == RiskSeverity.HIGH and not request.auto_isolate:
            raise PolicyViolationError(
                "HIGH severity alerts must always auto-isolate."
            )
        logger.debug(
            "Risk isolation policy passed for: %s (severity=%s)",
            request.user_principal_name, request.severity.value,
        )

    def _check_app_onboarding(self, request: AppOnboardingRequest) -> None:
        requested = set(request.requested_scopes)
        forbidden = requested & _FORBIDDEN_SCOPES
        if forbidden:
            raise PolicyViolationError(
                f"Application requested forbidden scopes: {sorted(forbidden)}."
            )
        if not request.owner_id:
            raise PolicyViolationError(
                "App onboarding requires an owner_id."
            )
        if not request.requested_scopes:
            raise PolicyViolationError(
                "App onboarding request must include at least one requested scope."
            )
        logger.debug("App onboarding policy passed for: %s", request.display_name)

    def _check_app_offboarding(self, request: AppOffboardingRequest) -> None:
        if not request.reason or len(request.reason.strip()) < 5:
            raise PolicyViolationError(
                "App offboarding requires a reason of at least 5 characters."
            )
        if not request.object_id:
            raise PolicyViolationError(
                "App offboarding requires object_id (app registration ID)."
            )
        if not request.service_principal_id:
            logger.info(
                "App offboarding: no service_principal_id provided — "
                "SP may already be deleted. Proceeding with app registration cleanup only."
            )
        logger.debug("App offboarding policy passed for app_id: %s", request.app_id)

