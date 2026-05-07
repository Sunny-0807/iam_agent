import asyncio
import csv
import logging
import io
import time
from pathlib import Path

from shared.models import AppType, BulkAppOnboardingResult, SAMLAppOnboardingRequest
from app_bot.flows.saml_app_onboarding import run_saml_app_onboarding

logger = logging.getLogger(__name__)


def _safe(value) -> str:
    """Safely convert any CSV cell value to a stripped string."""
    if value is None:
        return ""
    return str(value).strip()


async def handle_csv_bulk_onboarding(csv_path: str) -> list[BulkAppOnboardingResult]:
    """
    Trigger: Bulk SAML app onboarding from a CSV file.

    CSV columns:
        display_name, app_type, template_id, entity_id, reply_url,
        sign_on_url, owner_upn, assigned_group_names, requested_by

    assigned_group_names: pipe-separated group display names (resolved to IDs automatically).
    """
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    rows = _read_csv(path)
    if not rows:
        logger.warning("CSV file is empty or has no valid rows: %s", csv_path)
        return []

    logger.info("Bulk SAML onboarding started: %d app(s) from '%s'", len(rows), csv_path)

    results: list[BulkAppOnboardingResult] = []

    for i, row in enumerate(rows, start=1):
        display_name = _safe(row.get("display_name")) or f"Row {i}"
        logger.info("Processing row %d/%d: '%s'", i, len(rows), display_name)

        start_time = time.monotonic()

        try:
            request = _build_request(row, i)
            outcome = await run_saml_app_onboarding(request)
            duration = round(time.monotonic() - start_time, 2)

            results.append(BulkAppOnboardingResult(
                row=i,
                display_name=display_name,
                app_type=_safe(row.get("app_type")),
                status="completed",
                app_id=outcome.get("app_id"),
                object_id=outcome.get("object_id"),
                sp_id=outcome.get("service_principal_id"),
                cert_thumbprint=outcome.get("cert_thumbprint"),
                duration_seconds=duration,
            ))
            logger.info("Row %d completed: '%s' in %.2fs", i, display_name, duration)

        except Exception as exc:
            duration = round(time.monotonic() - start_time, 2)
            logger.error("Row %d failed: '%s' after %.2fs — %s", i, display_name, duration, exc)
            results.append(BulkAppOnboardingResult(
                row=i,
                display_name=display_name,
                app_type=_safe(row.get("app_type")),
                status="failed",
                duration_seconds=duration,
                error=str(exc),
            ))

    _log_summary(results)
    return results


def _read_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return [{_safe(k): _safe(v) for k, v in row.items()} for row in reader]


def _build_request(row: dict, row_num: int) -> SAMLAppOnboardingRequest:
    """Build a SAMLAppOnboardingRequest from a CSV row dict."""
    _require_field(row, "display_name", row_num)
    _require_field(row, "app_type",     row_num)
    _require_field(row, "entity_id",    row_num)
    _require_field(row, "reply_url",    row_num)
    _require_field(row, "owner_upn", row_num)

    app_type_raw = _safe(row.get("app_type")).lower()
    if app_type_raw not in ("gallery", "non_gallery"):
        raise ValueError(
            f"Row {row_num}: app_type must be 'gallery' or 'non_gallery', got '{app_type_raw}'"
        )
    app_type = AppType(app_type_raw)

    if app_type == AppType.GALLERY and not _safe(row.get("template_id")):
        raise ValueError(
            f"Row {row_num}: template_id is required for gallery apps "
            f"(display_name='{row.get('display_name')}')"
        )

    # Parse pipe-separated group NAMES (display names, not IDs)
    group_names_raw = _safe(row.get("assigned_group_names"))
    group_names = [g.strip() for g in group_names_raw.split("|") if g.strip()] \
        if group_names_raw else []

    return SAMLAppOnboardingRequest(
        display_name=_safe(row.get("display_name")),
        app_type=app_type,
        template_id=_safe(row.get("template_id")) or None,
        entity_id=_safe(row.get("entity_id")),
        reply_url=_safe(row.get("reply_url")),
        sign_on_url=_safe(row.get("sign_on_url")) or None,
        owner_upn=_safe(row.get("owner_upn")),
        assigned_group_names=group_names,   # names — resolved inside flow
        requested_by=_safe(row.get("requested_by")) or "csv_bulk",
        source="csv_bulk",
    )


def _require_field(row: dict, field: str, row_num: int) -> None:
    if not _safe(row.get(field)):
        raise ValueError(f"Row {row_num}: required field '{field}' is missing or empty.")


def _log_summary(results: list[BulkAppOnboardingResult]) -> None:
    total     = len(results)
    passed    = sum(1 for r in results if r.status == "completed")
    failed    = sum(1 for r in results if r.status == "failed")
    total_dur = round(sum(r.duration_seconds for r in results), 2)

    logger.info(
        "Bulk SAML onboarding complete: total=%d passed=%d failed=%d total_duration=%.2fs",
        total, passed, failed, total_dur,
    )
    for r in results:
        if r.status == "failed":
            logger.error("  FAILED row=%d name='%s' error=%s", r.row, r.display_name, r.error)

