"""
4-Agent Pipeline Engine.

Collection -> Analysis -> Decision -> Execution
"""
from __future__ import annotations
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class PipelineMode(str, Enum):
    AUTO     = "auto"
    STEPWISE = "stepwise"


class PipelineType(str, Enum):
    USER = "user"
    APP  = "app"


class AgentStatus(str, Enum):
    IDLE    = "idle"
    RUNNING = "running"
    DONE    = "done"
    FAILED  = "failed"
    WAITING = "waiting"


@dataclass
class CollectionResult:
    source:      str
    filename:    str
    rows:        list[dict]
    row_count:   int
    detected_at: float = field(default_factory=time.time)


@dataclass
class AnalysisResult:
    issues:          list[Any]
    missing_columns: list[str]
    prepared_rows:   list[dict]
    error_rows:      set[int]
    warning_rows:    set[int]
    has_errors:      bool
    has_warnings:    bool


@dataclass
class DecisionRow:
    row_num:      int
    display_name: str
    identifier:   str
    auto_approve: bool
    reason:       str
    prepared:     dict


@dataclass
class DecisionResult:
    auto_rows:         list[DecisionRow]
    approval_rows:     list[DecisionRow]
    approved_by_admin: list[int] = field(default_factory=list)


@dataclass
class ExecutionResult:
    summary:        Any
    pipeline_type:  PipelineType
    total_duration: float = 0.0


@dataclass
class PipelineState:
    pipeline_type:     PipelineType
    mode:              PipelineMode = PipelineMode.STEPWISE

    collection_status: AgentStatus = AgentStatus.IDLE
    analysis_status:   AgentStatus = AgentStatus.IDLE
    decision_status:   AgentStatus = AgentStatus.IDLE
    execution_status:  AgentStatus = AgentStatus.IDLE

    collection: CollectionResult | None = None
    analysis:   AnalysisResult  | None = None
    decision:   DecisionResult  | None = None
    execution:  ExecutionResult | None = None

    error: str | None = None

    def reset(self) -> None:
        self.collection_status = AgentStatus.IDLE
        self.analysis_status   = AgentStatus.IDLE
        self.decision_status   = AgentStatus.IDLE
        self.execution_status  = AgentStatus.IDLE
        self.collection = None
        self.analysis   = None
        self.decision   = None
        self.execution  = None
        self.error      = None

    def is_complete(self) -> bool:
        return self.execution_status == AgentStatus.DONE

    def current_agent(self) -> int:
        if self.execution_status in (AgentStatus.RUNNING, AgentStatus.DONE):
            return 4
        if self.decision_status in (AgentStatus.RUNNING, AgentStatus.DONE, AgentStatus.WAITING):
            return 3
        if self.analysis_status in (AgentStatus.RUNNING, AgentStatus.DONE):
            return 2
        if self.collection_status in (AgentStatus.RUNNING, AgentStatus.DONE):
            return 1
        return 0


# ── Agent 1: Collection ───────────────────────────────────────────────────────

async def run_collection_agent(
    state: PipelineState,
    source: str,
    content: str,
    filename: str,
) -> None:
    state.collection_status = AgentStatus.RUNNING
    logger.info("[Collection] '%s' source=%s", filename, source)
    try:
        if state.pipeline_type == PipelineType.USER:
            from user_bot.triggers.csv_bulk_user_trigger import parse_csv
            rows = parse_csv(content)
        else:
            import csv, io
            def _safe(v): return "" if v is None else str(v).strip()
            reader = csv.DictReader(io.StringIO(content))
            rows   = [{_safe(k): _safe(v) for k, v in row.items()} for row in reader]

        if not rows:
            raise ValueError("CSV is empty or has no data rows.")

        state.collection = CollectionResult(
            source=source, filename=filename,
            rows=rows, row_count=len(rows),
        )
        state.collection_status = AgentStatus.DONE
        logger.info("[Collection] %d row(s) collected.", len(rows))

    except Exception as exc:
        state.collection_status = AgentStatus.FAILED
        state.error = f"Collection Agent failed: {exc}"
        logger.error("[Collection] %s", exc)


# ── Agent 2: Analysis ─────────────────────────────────────────────────────────

async def run_analysis_agent(state: PipelineState) -> None:
    if not state.collection:
        state.analysis_status = AgentStatus.FAILED
        state.error = "Analysis Agent: no collection data."
        return

    state.analysis_status = AgentStatus.RUNNING
    rows = state.collection.rows
    logger.info("[Analysis] Validating %d row(s)...", len(rows))

    try:
        if state.pipeline_type == PipelineType.USER:
            from user_bot.triggers.csv_bulk_user_trigger import (
                preflight_validate, resolve_and_prepare,
            )
            issues, missing_cols = await preflight_validate(rows)
            error_rows   = {i.row for i in issues if i.severity == "error"}
            warning_rows = {i.row for i in issues if i.severity == "warning"} - error_rows
            rows_to_prep = [r for i, r in enumerate(rows, 1) if i not in error_rows]
            prepared     = await resolve_and_prepare(rows_to_prep) if rows_to_prep else []

        else:
            from app_bot.triggers.csv_bulk_trigger import _build_request, _safe
            from shared.models import BulkUserPreflightIssue
            issues, missing_cols, error_rows, warning_rows, prepared = [], [], set(), set(), []
            for i, row in enumerate(rows, 1):
                try:
                    req = _build_request(row, i)
                    prepared.append({
                        "_request":    req,
                        "_row_num":    i,
                        "display_name": _safe(row.get("display_name")),
                    })
                except ValueError as exc:
                    issues.append(BulkUserPreflightIssue(
                        row=i, upn=_safe(row.get("display_name")) or f"row-{i}",
                        severity="error", message=str(exc),
                    ))
                    error_rows.add(i)

        state.analysis = AnalysisResult(
            issues=issues, missing_columns=missing_cols,
            prepared_rows=prepared, error_rows=error_rows,
            warning_rows=warning_rows,
            has_errors=bool(error_rows), has_warnings=bool(warning_rows),
        )
        state.analysis_status = AgentStatus.DONE
        logger.info("[Analysis] %d errors, %d warnings, %d ready.",
                    len(error_rows), len(warning_rows), len(prepared))

    except Exception as exc:
        state.analysis_status = AgentStatus.FAILED
        state.error = f"Analysis Agent failed: {exc}"
        logger.error("[Analysis] %s", exc)


# ── Agent 3: Decision ─────────────────────────────────────────────────────────

async def run_decision_agent(state: PipelineState) -> None:
    if not state.analysis:
        state.decision_status = AgentStatus.FAILED
        state.error = "Decision Agent: no analysis data."
        return

    state.decision_status = AgentStatus.RUNNING
    prepared = state.analysis.prepared_rows
    logger.info("[Decision] Evaluating %d row(s)...", len(prepared))

    try:
        from ai_engine.policy_engine import PolicyEngine
        from ai_engine.approval_gate import ApprovalGate
        from shared.exceptions import PolicyViolationError, ApprovalRequiredError
        from shared.config import config

        policy        = PolicyEngine()
        gate          = ApprovalGate()
        auto_rows:     list[DecisionRow] = []
        approval_rows: list[DecisionRow] = []

        for i, row in enumerate(prepared, 1):
            row_num      = row.get("_row_num", i)
            display_name = row.get("display_name", f"Row {row_num}")
            identifier   = row.get("user_principal_name", display_name)

            if state.pipeline_type == PipelineType.APP:
                # App onboarding always auto-approves
                auto_rows.append(DecisionRow(
                    row_num=row_num, display_name=display_name,
                    identifier=identifier, auto_approve=True,
                    reason="", prepared=row,
                ))
                continue

            # User pipeline
            from shared.models import UserOnboardingRequest, FlowName
            needs_approval = False
            reason         = ""

            try:
                req = UserOnboardingRequest(
                    display_name=row.get("display_name", ""),
                    mail_nickname=row.get("mail_nickname", ""),
                    user_principal_name=row.get("user_principal_name", ""),
                    department=row.get("department", ""),
                    job_title=row.get("job_title", ""),
                    usage_location=row.get("usage_location", "US"),
                    group_ids=row.get("group_ids", []),
                    requested_by="pipeline",
                )
                policy.check(FlowName.USER_ONBOARDING, req)

                if not config.skip_approval:
                    try:
                        gate.evaluate(FlowName.USER_ONBOARDING, {})
                    except ApprovalRequiredError as exc:
                        needs_approval = True
                        reason = exc.reason

            except PolicyViolationError as exc:
                needs_approval = True
                reason = f"Policy violation: {exc}"

            if needs_approval:
                approval_rows.append(DecisionRow(
                    row_num=row_num, display_name=display_name,
                    identifier=identifier, auto_approve=False,
                    reason=reason, prepared=row,
                ))
            else:
                auto_rows.append(DecisionRow(
                    row_num=row_num, display_name=display_name,
                    identifier=identifier, auto_approve=True,
                    reason="", prepared=row,
                ))

        state.decision = DecisionResult(
            auto_rows=auto_rows, approval_rows=approval_rows,
        )

        if approval_rows and state.mode == PipelineMode.STEPWISE:
            state.decision_status = AgentStatus.WAITING
        else:
            state.decision.approved_by_admin = [r.row_num for r in approval_rows]
            state.decision_status = AgentStatus.DONE

        logger.info("[Decision] %d auto, %d pending.", len(auto_rows), len(approval_rows))

    except Exception as exc:
        state.decision_status = AgentStatus.FAILED
        state.error = f"Decision Agent failed: {exc}"
        logger.error("[Decision] %s", exc)


# ── Agent 4: Execution ────────────────────────────────────────────────────────

async def run_execution_agent(
    state: PipelineState,
    dry_run: bool = False,
) -> None:
    if not state.decision:
        state.execution_status = AgentStatus.FAILED
        state.error = "Execution Agent: no decision data."
        return

    state.execution_status = AgentStatus.RUNNING
    decision      = state.decision
    approved_nums = {r.row_num for r in decision.auto_rows} | set(decision.approved_by_admin)
    all_rows      = decision.auto_rows + decision.approval_rows
    rows_to_run   = [r.prepared for r in all_rows if r.row_num in approved_nums]

    logger.info("[Execution] Running %d row(s) dry_run=%s...", len(rows_to_run), dry_run)
    start = time.monotonic()

    try:
        if state.pipeline_type == PipelineType.USER:
            from user_bot.flows.bulk_user_onboarding import run_bulk_user_onboarding
            summary = await run_bulk_user_onboarding(rows_to_run, dry_run=dry_run)
            state.execution = ExecutionResult(
                summary=summary, pipeline_type=PipelineType.USER,
                total_duration=round(time.monotonic() - start, 2),
            )

        else:
            from app_bot.flows.saml_app_onboarding import run_saml_app_onboarding
            from shared.models import BulkAppOnboardingResult
            results = []
            for row in rows_to_run:
                req   = row.get("_request")
                rnum  = row.get("_row_num", 0)
                dname = row.get("display_name", f"Row {rnum}")
                rs    = time.monotonic()
                try:
                    out = await run_saml_app_onboarding(req)
                    results.append(BulkAppOnboardingResult(
                        row=rnum, display_name=dname,
                        app_type=req.app_type.value, status="completed",
                        app_id=out.get("app_id"), object_id=out.get("object_id"),
                        sp_id=out.get("service_principal_id"),
                        cert_thumbprint=out.get("cert_thumbprint"),
                        duration_seconds=round(time.monotonic() - rs, 2),
                    ))
                except Exception as exc:
                    results.append(BulkAppOnboardingResult(
                        row=rnum, display_name=dname,
                        app_type=req.app_type.value if req else "",
                        status="failed",
                        duration_seconds=round(time.monotonic() - rs, 2),
                        error=str(exc),
                    ))
            state.execution = ExecutionResult(
                summary=results, pipeline_type=PipelineType.APP,
                total_duration=round(time.monotonic() - start, 2),
            )

        state.execution_status = AgentStatus.DONE
        logger.info("[Execution] Done in %.2fs.", state.execution.total_duration)

    except Exception as exc:
        state.execution_status = AgentStatus.FAILED
        state.error = f"Execution Agent failed: {exc}"
        logger.error("[Execution] %s", exc)


# ── Full auto pipeline ────────────────────────────────────────────────────────

async def run_full_pipeline(
    state: PipelineState,
    source: str,
    content: str,
    filename: str,
    dry_run: bool = False,
) -> None:
    await run_collection_agent(state, source, content, filename)
    if state.collection_status != AgentStatus.DONE:
        return
    await run_analysis_agent(state)
    if state.analysis_status != AgentStatus.DONE:
        return
    await run_decision_agent(state)
    if state.decision_status not in (AgentStatus.DONE, AgentStatus.WAITING):
        return
    await run_execution_agent(state, dry_run=dry_run)

