import logging
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from ai_engine.approval_gate import ApprovalGate
from ai_engine.intent_classifier import IntentClassifier
from ai_engine.policy_engine import PolicyEngine
from ai_engine.risk_scorer import RiskScorer
from shared.config import config
from shared.exceptions import (
    ApprovalRequiredError,
    IntentClassificationError,
    PolicyViolationError,
    RiskScoringError,
)
from shared.models import FlowName, FlowStatus, RiskIsolationRequest

logger = logging.getLogger(__name__)


# ── LangGraph State ───────────────────────────────────────────────────────────

class IAMState(TypedDict):
    """
    Shared state passed between all nodes in the LangGraph workflow.
    Every node reads from and writes to this dict.
    """
    # Input
    request_text: str
    request_payload: Any

    # AI engine outputs
    intent: FlowName | None
    intent_confidence: float
    risk_severity: str | None
    risk_auto_isolate: bool

    # Flow control
    status: FlowStatus
    requires_approval: bool
    approval_reason: str | None
    error: str | None

    # Result
    result: dict | None


# ── Orchestrator ──────────────────────────────────────────────────────────────

class IAMOrchestrator:
    """
    LangGraph-based orchestrator that routes IAM requests through the full
    decision pipeline: classify -> policy check -> approval gate -> execute.

    SKIP_APPROVAL mode (local dev only):
        When SKIP_APPROVAL=true in .env, the approval gate is bypassed and
        all flows execute directly. Never use this in production.
    """

    def __init__(self, bot_registry: dict[FlowName, Any]):
        self._classifier  = IntentClassifier()
        self._risk_scorer = RiskScorer()
        self._policy      = PolicyEngine()
        self._gate        = ApprovalGate()
        self._registry    = bot_registry
        self._graph       = self._build_graph()

        # Warn loudly if skip_approval is active
        if config.skip_approval:
            logger.warning(
                "SKIP_APPROVAL=true — approval gate is DISABLED. "
                "All flows will auto-execute. Never use this in production."
            )

    def _build_graph(self) -> Any:
        graph = StateGraph(IAMState)

        graph.add_node("classify_intent",   self._node_classify_intent)
        graph.add_node("score_risk",        self._node_score_risk)
        graph.add_node("check_policy",      self._node_check_policy)
        graph.add_node("evaluate_approval", self._node_evaluate_approval)
        graph.add_node("execute_flow",      self._node_execute_flow)
        graph.add_node("escalate",          self._node_escalate)
        graph.add_node("handle_error",      self._node_handle_error)

        graph.set_entry_point("classify_intent")

        graph.add_conditional_edges(
            "classify_intent",
            self._route_after_classification,
            {
                "score_risk":   "score_risk",
                "check_policy": "check_policy",
                "handle_error": "handle_error",
            },
        )

        graph.add_edge("score_risk", "check_policy")

        graph.add_conditional_edges(
            "check_policy",
            self._route_after_policy,
            {
                "evaluate_approval": "evaluate_approval",
                "handle_error":      "handle_error",
            },
        )

        graph.add_conditional_edges(
            "evaluate_approval",
            self._route_after_approval,
            {
                "execute_flow": "execute_flow",
                "escalate":     "escalate",
                "handle_error": "handle_error",
            },
        )

        graph.add_edge("execute_flow", END)
        graph.add_edge("escalate",     END)
        graph.add_edge("handle_error", END)

        return graph.compile()

    # ── Nodes ─────────────────────────────────────────────────────────────────

    async def _node_classify_intent(self, state: IAMState) -> IAMState:
        try:
            result = await self._classifier.classify(state["request_text"])
            return {
                **state,
                "intent":           result["intent"],
                "intent_confidence": result["confidence"],
                "status":           FlowStatus.PENDING,
                "error":            None,
            }
        except IntentClassificationError as exc:
            logger.error("Intent classification failed: %s", exc)
            return {**state, "error": str(exc), "status": FlowStatus.FAILED}

    async def _node_score_risk(self, state: IAMState) -> IAMState:
        payload: RiskIsolationRequest = state["request_payload"]
        try:
            result = await self._risk_scorer.score(
                user_principal_name=payload.user_principal_name,
                alert_reason=payload.alert_reason,
                sentinel_incident_id=payload.sentinel_incident_id,
            )
            return {
                **state,
                "risk_severity":     result["severity"].value,
                "risk_auto_isolate": result["auto_isolate"],
                "request_payload":   RiskIsolationRequest(
                    **{**payload.model_dump(), "auto_isolate": result["auto_isolate"]}
                ),
            }
        except RiskScoringError as exc:
            logger.error("Risk scoring failed: %s", exc)
            return {**state, "error": str(exc), "status": FlowStatus.FAILED}

    async def _node_check_policy(self, state: IAMState) -> IAMState:
        try:
            self._policy.check(state["intent"], state["request_payload"])
            return {**state, "error": None}
        except PolicyViolationError as exc:
            logger.warning("Policy violation: %s", exc)
            return {**state, "error": str(exc), "status": FlowStatus.FAILED}

    async def _node_evaluate_approval(self, state: IAMState) -> IAMState:
        """
        Determine whether the flow auto-executes or requires human approval.

        If SKIP_APPROVAL=true in .env, bypasses the approval gate entirely
        and marks all flows as auto-execute. Local dev only.
        """
        # ── SKIP_APPROVAL bypass (local dev only) ─────────────────────────────
        if config.skip_approval:
            logger.warning(
                "SKIP_APPROVAL active — bypassing approval gate for flow '%s'.",
                state.get("intent"),
            )
            return {**state, "requires_approval": False, "approval_reason": None}

        # ── Normal approval gate evaluation ───────────────────────────────────
        context = {}
        if state["intent"] == FlowName.RISK_ISOLATION:
            from shared.models import RiskSeverity
            context["severity"] = RiskSeverity(state["risk_severity"])

        try:
            self._gate.evaluate(state["intent"], context)
            return {**state, "requires_approval": False, "approval_reason": None}
        except ApprovalRequiredError as exc:
            logger.info("Approval required: %s", exc.reason)
            return {
                **state,
                "requires_approval": True,
                "approval_reason":   exc.reason,
            }

    async def _node_execute_flow(self, state: IAMState) -> IAMState:
        flow = self._registry.get(state["intent"])
        if not flow:
            return {
                **state,
                "error":  f"No bot registered for flow: {state['intent']}",
                "status": FlowStatus.FAILED,
            }
        try:
            logger.info("Executing flow: %s", state["intent"].value)
            result = await flow(state["request_payload"])
            return {**state, "result": result, "status": FlowStatus.COMPLETED}
        except Exception as exc:
            logger.error("Flow execution failed: %s", exc)
            return {**state, "error": str(exc), "status": FlowStatus.FAILED}

    async def _node_escalate(self, state: IAMState) -> IAMState:
        """
        Route to human approval.
        Currently logs only — notifier.py will be wired here in the next phase.
        """
        logger.info(
            "Escalating flow '%s' for human approval: %s",
            state.get("intent"), state.get("approval_reason"),
        )
        return {
            **state,
            "status": FlowStatus.ESCALATED,
            "result": {
                "escalated": True,
                "reason":    state.get("approval_reason"),
                "flow":      state.get("intent").value if state.get("intent") else "unknown",
            },
        }

    async def _node_handle_error(self, state: IAMState) -> IAMState:
        logger.error("Flow failed: %s", state.get("error"))
        return {**state, "status": FlowStatus.FAILED}

    # ── Routing ───────────────────────────────────────────────────────────────

    def _route_after_classification(self, state: IAMState) -> str:
        if state.get("error"):
            return "handle_error"
        if state.get("intent") == FlowName.RISK_ISOLATION:
            # Admin-initiated isolations (from admin_portal or Streamlit) already
            # have the severity set by the admin — skip the AI risk scorer and
            # go straight to policy check. Only Sentinel-triggered isolations
            # (requested_by="sentinel_alert") need AI risk scoring.
            payload = state.get("request_payload")
            requested_by = getattr(payload, "requested_by", "") or ""
            if requested_by in ("streamlit_admin", "admin_portal"):
                return "check_policy"
            return "score_risk"
        return "check_policy"

    def _route_after_policy(self, state: IAMState) -> str:
        if state.get("error"):
            return "handle_error"
        return "evaluate_approval"

    def _route_after_approval(self, state: IAMState) -> str:
        if state.get("error"):
            return "handle_error"
        if state.get("requires_approval"):
            return "escalate"
        return "execute_flow"

    # ── Public entry point ────────────────────────────────────────────────────

    async def run(self, request_text: str, request_payload: Any) -> IAMState:
        """
        Run the full IAM orchestration pipeline.

        Args:
            request_text    : Free-text description (used by intent classifier).
            request_payload : Typed Pydantic request model.

        Returns:
            Final IAMState with status, result, or error populated.
        """
        initial_state: IAMState = {
            "request_text":      request_text,
            "request_payload":   request_payload,
            "intent":            None,
            "intent_confidence": 0.0,
            "risk_severity":     None,
            "risk_auto_isolate": False,
            "status":            FlowStatus.PENDING,
            "requires_approval": False,
            "approval_reason":   None,
            "error":             None,
            "result":            None,
        }

        logger.info("IAM orchestrator started for request: %.80s...", request_text)
        final_state = await self._graph.ainvoke(initial_state)
        logger.info(
            "IAM orchestrator completed. status=%s intent=%s",
            final_state.get("status"), final_state.get("intent"),
        )
        return final_state
    
