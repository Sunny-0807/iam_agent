from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


# ── Enums ─────────────────────────────────────────────────────────────────────

class FlowName(str, Enum):
    USER_ONBOARDING   = "user_onboarding"
    USER_OFFBOARDING  = "user_offboarding"
    RISK_ISOLATION    = "risk_isolation"
    APP_ONBOARDING    = "app_onboarding"
    APP_OFFBOARDING   = "app_offboarding"


class FlowStatus(str, Enum):
    PENDING    = "pending"
    APPROVED   = "approved"
    EXECUTING  = "executing"
    COMPLETED  = "completed"
    FAILED     = "failed"
    ESCALATED  = "escalated"


class RiskSeverity(str, Enum):
    LOW    = "low"
    MEDIUM = "medium"
    HIGH   = "high"


class AppType(str, Enum):
    GALLERY     = "gallery"
    NON_GALLERY = "non_gallery"


# ── User Models ───────────────────────────────────────────────────────────────

class UserOnboardingRequest(BaseModel):
    display_name:          str
    mail_nickname:         str
    user_principal_name:   str
    department:            str
    job_title:             str
    # usage_location is required by Entra ID before a license can be assigned.
    # Must be a valid ISO 3166-1 alpha-2 country code e.g. "US", "IN", "GB".
    usage_location:        str = "US"
    manager_id:            str | None = None
    group_ids:             list[str] = Field(default_factory=list)
    license_sku_id:        str | None = None
    app_role_assignments:  list[dict[str, str]] = Field(default_factory=list)
    requested_by:          str
    source:                str = "hr_system"


class UserOffboardingRequest(BaseModel):
    user_id:                  str
    user_principal_name:      str
    requested_by:             str
    reason:                   str
    revoke_sessions:          bool = True
    remove_licenses:          bool = True
    remove_group_memberships: bool = True
    backup_manager_id:        str | None = None


class RiskIsolationRequest(BaseModel):
    user_id:              str
    user_principal_name:  str
    alert_id:             str
    severity:             RiskSeverity
    alert_reason:         str
    sentinel_incident_id: str | None = None
    auto_isolate:         bool = True
    requested_by:         str = "sentinel_alert"


# ── App Models ────────────────────────────────────────────────────────────────

class AppOnboardingRequest(BaseModel):
    display_name:    str
    owner_id:        str
    requested_scopes: list[str]
    redirect_uris:   list[str] = Field(default_factory=list)
    description:     str | None = None
    requested_by:    str


class AppOffboardingRequest(BaseModel):
    app_id:                 str
    object_id:              str
    service_principal_id:   str
    requested_by:           str
    reason:                 str
    revoke_user_assignments: bool = True


class SAMLAppOnboardingRequest(BaseModel):
    display_name:        str
    app_type:            AppType
    template_id:         str | None = None
    entity_id:           str
    reply_url:           str
    sign_on_url:         str | None = None
    owner_upn:           str  # UPN of the app owner — resolved to object ID before Graph API calls
    assigned_group_names: list[str] = Field(default_factory=list)  # group display names — resolved to IDs before Graph API calls
    requested_by:        str
    source:              str = "csv_bulk"


class BulkAppOnboardingResult(BaseModel):
    row:              int
    display_name:     str
    app_type:         str
    status:           str
    app_id:           str | None = None
    object_id:        str | None = None
    sp_id:            str | None = None
    cert_thumbprint:  str | None = None
    duration_seconds: float = 0.0
    error:            str | None = None


# ── Graph API Response Models ─────────────────────────────────────────────────

class GraphUser(BaseModel):
    id:                   str
    display_name:         str = Field(alias="displayName")
    user_principal_name:  str = Field(alias="userPrincipalName")
    account_enabled:      bool = Field(alias="accountEnabled")
    department:           str | None = None
    job_title:            str | None = Field(None, alias="jobTitle")

    class Config:
        populate_by_name = True


class GraphApplication(BaseModel):
    id:           str
    app_id:       str = Field(alias="appId")
    display_name: str = Field(alias="displayName")

    class Config:
        populate_by_name = True


# ── Audit Log Entry ───────────────────────────────────────────────────────────

class AuditLogEntry(BaseModel):
    id:               str = Field(default_factory=lambda: str(uuid4()))
    bot_type:         str
    flow_name:        FlowName
    status:           FlowStatus
    principal_id:     str
    requested_by:     str
    timestamp:        datetime = Field(default_factory=datetime.utcnow)
    details:          dict[str, Any] = Field(default_factory=dict)
    error:            str | None = None
    graph_operations: list[str] = Field(default_factory=list)


# ── AI Assistant Models ───────────────────────────────────────────────────────

class AssistantAction(str, Enum):
    ONBOARD_USER        = "onboard_user"
    OFFBOARD_USER       = "offboard_user"
    ISOLATE_USER        = "isolate_user"
    ADD_TO_GROUP        = "add_to_group"
    REMOVE_FROM_GROUP   = "remove_from_group"
    ASSIGN_LICENSE      = "assign_license"
    REMOVE_LICENSE      = "remove_license"
    UNKNOWN             = "unknown"


class AssistantParseResult(BaseModel):
    """Result of the AI assistant parsing a free-text IAM instruction."""
    action:          AssistantAction
    confidence:      float
    extracted:       dict[str, Any] = Field(default_factory=dict)
    missing_required: list[str]     = Field(default_factory=list)
    reasoning:       str            = ""


# ── Bulk User Onboarding Models ───────────────────────────────────────────────

class BulkUserRowStatus(str, Enum):
    COMPLETED = "completed"
    PARTIAL   = "partial"    # User created but some sub-operations failed
    FAILED    = "failed"     # User was not created at all
    SKIPPED   = "skipped"    # Skipped due to pre-flight validation failure


class BulkUserSubOp(BaseModel):
    """Result of a single sub-operation within a user onboarding row."""
    name:     str            # e.g. "create_account", "assign_group", "assign_license"
    success:  bool
    detail:   str = ""       # Group name, license name, or error message


class BulkUserResult(BaseModel):
    """Result for a single row in a bulk user onboarding run."""
    row:              int
    display_name:     str
    upn:              str
    status:           BulkUserRowStatus
    user_id:          str | None = None     # Set if account was created
    duration_seconds: float = 0.0
    sub_ops:          list[BulkUserSubOp] = Field(default_factory=list)
    error:            str | None = None     # Top-level error if account creation failed

    def summary_line(self) -> str:
        failed_ops = [s for s in self.sub_ops if not s.success]
        if self.status == BulkUserRowStatus.COMPLETED:
            return "All operations completed"
        elif self.status == BulkUserRowStatus.PARTIAL:
            return "; ".join(f"{s.name}: {s.detail}" for s in failed_ops)
        elif self.status == BulkUserRowStatus.FAILED:
            return self.error or "Unknown error"
        elif self.status == BulkUserRowStatus.SKIPPED:
            return self.error or "Pre-flight validation failed"
        return ""


class BulkUserPreflightIssue(BaseModel):
    """A single pre-flight validation issue for a CSV row."""
    row:      int
    upn:      str
    severity: str   # "error" (blocks run) | "warning" (informational)
    message:  str


class BulkUserRunSummary(BaseModel):
    """Full summary of a bulk user onboarding run."""
    total:            int
    completed:        int
    partial:          int
    failed:           int
    skipped:          int
    total_duration:   float
    results:          list[BulkUserResult]
    dry_run:          bool = False

