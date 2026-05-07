import asyncio
import csv
import io
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st

st.set_page_config(
    page_title="Bulk CSV — Agentic IAM", page_icon="📂", layout="wide"
)
st.title("📂 Bulk SAML App Onboarding")
st.caption("Upload a CSV file to onboard multiple SAML SSO applications in one run.")

# ── CSV format reference ──────────────────────────────────────────────────────
with st.expander("📄 Required CSV format", expanded=False):
    st.markdown("""
| Column | Required | Description |
|---|---|---|
| `display_name` | ✅ | App name shown in Entra ID |
| `app_type` | ✅ | `gallery` or `non_gallery` |
| `template_id` | gallery only | GUID from Microsoft app gallery |
| `entity_id` | ✅ | SAML Entity ID / Identifier URI |
| `reply_url` | ✅ | ACS URL (must be HTTPS) |
| `sign_on_url` | optional | SP-initiated login URL |
| `owner_upn` | ✅ | UPN of the app owner e.g. admin@company.com |
| `assigned_group_names` | optional | Pipe-separated group display names e.g. DevOps\|Engineering |
| `requested_by` | optional | Who is requesting — defaults to csv_bulk |

**Group IDs:** separate multiple IDs with a pipe `|` e.g. `grp-001|grp-002`
    """)

# ── File upload ───────────────────────────────────────────────────────────────
uploaded = st.file_uploader(
    "Upload CSV file",
    type=["csv"],
    help="Drag and drop or click to browse",
)

if not uploaded:
    st.info("Upload a CSV file to get started.", icon="📂")
    st.stop()

# ── Parse and preview CSV ─────────────────────────────────────────────────────
raw_content = uploaded.read().decode("utf-8")
reader      = csv.DictReader(io.StringIO(raw_content))
rows        = [{k.strip(): v.strip() for k, v in row.items()} for row in reader]

if not rows:
    st.error("CSV file is empty or has no valid rows.")
    st.stop()

st.success(f"✅ CSV loaded — {len(rows)} app(s) found.")

# Preview table
st.subheader("Preview")
st.dataframe(
    rows,
    use_container_width=True,
    hide_index=True,
)

# ── Run button ────────────────────────────────────────────────────────────────
st.divider()
col_run, col_info = st.columns([1, 3])
run_clicked = col_run.button("🚀 Run Bulk Onboarding", type="primary")
col_info.caption(
    f"Will process {len(rows)} app(s). "
    "Failures are logged and the run continues."
)

if not run_clicked:
    st.stop()

# ── Processing with progress + real-time logs ─────────────────────────────────
from app_bot.triggers.csv_bulk_trigger import _build_request, _require_field
from app_bot.flows.saml_app_onboarding import run_saml_app_onboarding
from shared.models import BulkAppOnboardingResult

st.divider()
st.subheader("Processing")

progress_bar  = st.progress(0, text="Starting...")
log_container = st.empty()
log_lines: list[str] = []

results: list[BulkAppOnboardingResult] = []
overall_start = time.monotonic()


def _append_log(line: str) -> None:
    log_lines.append(line)
    # Show last 20 lines in the log container
    log_container.code("\n".join(log_lines[-20:]), language=None)


for i, row in enumerate(rows, start=1):
    display_name = row.get("display_name", f"Row {i}")
    progress_text = f"Processing {i} of {len(rows)}: **{display_name}**"
    progress_bar.progress(
        (i - 1) / len(rows),
        text=progress_text,
    )
    _append_log(f"[{i}/{len(rows)}] Starting: {display_name} ...")

    row_start = time.monotonic()

    try:
        request = _build_request(row, i)
        result  = asyncio.run(run_saml_app_onboarding(request))
        duration = round(time.monotonic() - row_start, 2)

        results.append(BulkAppOnboardingResult(
            row=i,
            display_name=display_name,
            app_type=row.get("app_type", ""),
            status="completed",
            app_id=result.get("app_id"),
            object_id=result.get("object_id"),
            sp_id=result.get("service_principal_id"),
            cert_thumbprint=result.get("cert_thumbprint"),
            duration_seconds=duration,
        ))
        _append_log(f"  ✓ {display_name} — {duration}s")

    except Exception as exc:
        duration = round(time.monotonic() - row_start, 2)
        results.append(BulkAppOnboardingResult(
            row=i,
            display_name=display_name,
            app_type=row.get("app_type", ""),
            status="failed",
            duration_seconds=duration,
            error=str(exc),
        ))
        _append_log(f"  ✗ {display_name} — {duration}s — {str(exc)[:80]}")

# Complete progress bar
progress_bar.progress(1.0, text="Done.")

# ── Summary banner ────────────────────────────────────────────────────────────
st.divider()
total_duration = round(time.monotonic() - overall_start, 2)
passed = sum(1 for r in results if r.status == "completed")
failed = sum(1 for r in results if r.status == "failed")

if failed == 0:
    st.success(
        f"✅ All {len(results)} app(s) onboarded successfully in {total_duration}s."
    )
else:
    st.warning(
        f"⚠ Completed with issues — "
        f"{passed} passed, {failed} failed — total time {total_duration}s."
    )

# ── Results table ─────────────────────────────────────────────────────────────
st.subheader("Results")

table_data = []
for r in results:
    table_data.append({
        "Row":          r.row,
        "App name":     r.display_name,
        "Type":         r.app_type,
        "Status":       "✅ done" if r.status == "completed" else "❌ failed",
        "Duration (s)": r.duration_seconds,
        "App ID":       r.app_id or "",
        "Cert thumbprint": r.cert_thumbprint or "",
        "Error":        r.error or "",
    })

st.dataframe(table_data, use_container_width=True, hide_index=True)

# ── Download results ──────────────────────────────────────────────────────────
if results:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=[
        "row", "display_name", "app_type", "status",
        "app_id", "object_id", "sp_id",
        "cert_thumbprint", "duration_seconds", "error",
    ])
    writer.writeheader()
    for r in results:
        writer.writerow({
            "row":              r.row,
            "display_name":     r.display_name,
            "app_type":         r.app_type,
            "status":           r.status,
            "app_id":           r.app_id or "",
            "object_id":        r.object_id or "",
            "sp_id":            r.sp_id or "",
            "cert_thumbprint":  r.cert_thumbprint or "",
            "duration_seconds": r.duration_seconds,
            "error":            r.error or "",
        })

    st.download_button(
        label="⬇️ Download results CSV",
        data=output.getvalue(),
        file_name="bulk_results.csv",
        mime="text/csv",
    )

