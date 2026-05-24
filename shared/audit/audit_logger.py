import json
import logging
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from shared.config import config

logger = logging.getLogger(__name__)

_LOCAL_LOG_PATH = Path(__file__).resolve().parent.parent.parent / "audit.jsonl"


def _derive_bot_type(flow_name: str) -> str:
    """
    Derive the correct bot_type from the flow name.

    This replaces reading from config.bot_type (the BOT_TYPE env var) which
    is only correct in isolated Azure Function deployments where each function
    app runs a single bot. In Streamlit both bots run in the same process, so
    using the env var stamps every entry with the same bot regardless of which
    flow actually ran.

    Falls back to config.bot_type only when the flow name is unrecognised.
    """
    fn = (flow_name.value if hasattr(flow_name, "value") else str(flow_name)).lower()

    _USER_FLOWS = {
        "user_onboarding", "user_offboarding", "risk_isolation",
        "bulk_user_onboarding", "onboard_user", "offboard_user", "isolate_user",
    }
    _APP_FLOWS = {
        "app_onboarding", "app_offboarding", "saml_app_onboarding",
        "bulk_app_onboarding", "register_app", "decommission_app",
    }
    _AI_FLOWS = {
        "add_to_group", "remove_from_group", "assign_license", "remove_license",
        "onboard_user", "offboard_user", "get_user_info", "reset_password",
        "isolate_user", "set_manager", "set_usage_location",
    }

    if fn in _APP_FLOWS:
        return "app_bot"
    if fn in _AI_FLOWS and fn not in _USER_FLOWS:
        return "ai_assistant"
    if fn in _USER_FLOWS:
        return "user_bot"

    # Substring fallback
    if any(x in fn for x in ("app_onboard", "saml", "app_offboard", "decommission")):
        return "app_bot"
    if any(x in fn for x in ("onboard", "offboard", "isolat", "bulk_user")):
        return "user_bot"

    return config.bot_type or "user_bot"


class AuditLogger:
    """
    Writes structured audit log entries to Cosmos DB (prod) or audit.jsonl (local).

    Supports two entry types:
    - flow_entry: full IAM bot flow (onboarding, offboarding etc.)
    - action_entry: targeted actions from AI assistant (add_to_group, assign_license etc.)

    Both types are written to the same log so the audit log tab shows everything.
    """

    CONTAINER_NAME = "audit-log"

    def __init__(self):
        self._use_cosmos = bool(config.cosmos_endpoint and config.cosmos_key)
        if self._use_cosmos:
            from azure.cosmos.aio import CosmosClient
            self._client    = CosmosClient(config.cosmos_endpoint, credential=config.cosmos_key)
            self._db        = self._client.get_database_client(config.cosmos_database)
            self._container = self._db.get_container_client(self.CONTAINER_NAME)
            logger.info("AuditLogger: using Cosmos DB.")
        else:
            logger.warning("AuditLogger: writing to local file %s", _LOCAL_LOG_PATH)

    async def log(
        self,
        flow_name,
        status,
        principal_id: str,
        requested_by: str,
        details: dict | None = None,
        graph_operations: list[str] | None = None,
        error: str | None = None,
    ):
        """Log a full IAM bot flow entry."""
        from shared.models import AuditLogEntry
        entry = AuditLogEntry(
            bot_type=_derive_bot_type(flow_name),
            flow_name=flow_name,
            status=status,
            principal_id=principal_id,
            requested_by=requested_by,
            timestamp=datetime.utcnow(),
            details=details or {},
            graph_operations=graph_operations or [],
            error=error,
        )
        await self._write(entry.model_dump(mode="json"))
        return entry

    async def log_action(
        self,
        action: str,
        status: str,
        principal_id: str,
        requested_by: str,
        details: dict | None = None,
        error: str | None = None,
        session_turn_id: str | None = None,
    ) -> dict:
        """
        Log a targeted AI assistant action (add_to_group, assign_license etc.)
        that doesn't go through the full bot flow pipeline.
        Returns the written entry dict.
        """
        entry = {
            "id":              str(uuid4()),
            "bot_type":        "ai_assistant",
            "flow_name":       action,
            "status":          status,
            "principal_id":    principal_id,
            "requested_by":    requested_by,
            "timestamp":       datetime.utcnow().isoformat(),
            "details":         details or {},
            "graph_operations": [],
            "error":           error,
            "session_turn_id": session_turn_id,
        }
        await self._write(entry)
        logger.info(
            "Audit log [action]: action=%s status=%s principal=%s",
            action, status, principal_id,
        )
        return entry

    async def log_failure(
        self,
        flow_name,
        principal_id: str,
        requested_by: str,
        error: Exception,
        details: dict | None = None,
    ):
        from shared.models import FlowStatus
        return await self.log(
            flow_name=flow_name,
            status=FlowStatus.FAILED,
            principal_id=principal_id,
            requested_by=requested_by,
            details=details,
            error=str(error),
        )

    async def _write(self, entry: dict) -> None:
        if self._use_cosmos:
            try:
                from azure.cosmos.exceptions import CosmosHttpResponseError
                await self._container.create_item(body=entry)
            except Exception as exc:
                logger.error("Failed to write audit log to Cosmos: %s", exc)
        else:
            try:
                with open(_LOCAL_LOG_PATH, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry) + "\n")
            except Exception as exc:
                logger.error("Failed to write audit log to file: %s", exc)

