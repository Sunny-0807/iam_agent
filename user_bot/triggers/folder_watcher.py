"""
Folder Watcher — Automated Bulk User Onboarding Trigger.

Watches a local inbox folder for new CSV files and automatically
runs the bulk user onboarding flow without any human intervention.

Folder structure (all created automatically on startup):
    watched_inbox/          ← place CSV files here
    watched_processing/     ← moved here while running (prevents double-processing)
    watched_processed/      ← moved here after successful run
    watched_failed/         ← moved here if preflight or run fails

Usage:
    python -m user_bot.triggers.folder_watcher

    Or run directly:
    python user_bot/triggers/folder_watcher.py

Stop with Ctrl+C.
"""

import asyncio
import logging
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env", override=False)

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

logger = logging.getLogger("folder_watcher")

# ── Folder configuration ──────────────────────────────────────────────────────
# All folders are relative to the project root.
# Override by setting env vars before running.

PROJECT_ROOT     = Path(__file__).resolve().parent.parent.parent
INBOX_DIR        = Path(os.getenv("WATCHER_INBOX",      str(PROJECT_ROOT / "watched_inbox")))
PROCESSING_DIR   = Path(os.getenv("WATCHER_PROCESSING", str(PROJECT_ROOT / "watched_processing")))
PROCESSED_DIR    = Path(os.getenv("WATCHER_PROCESSED",  str(PROJECT_ROOT / "watched_processed")))
FAILED_DIR       = Path(os.getenv("WATCHER_FAILED",     str(PROJECT_ROOT / "watched_failed")))

# How long to wait after a file appears before processing
# (gives time for large files to finish copying)
FILE_SETTLE_SECONDS = int(os.getenv("WATCHER_SETTLE_SECS", "3"))


def _ensure_folders() -> None:
    """Create all watched folders if they don't exist."""
    for folder in [INBOX_DIR, PROCESSING_DIR, PROCESSED_DIR, FAILED_DIR]:
        folder.mkdir(parents=True, exist_ok=True)
    logger.info("Watched folders ready:")
    logger.info("  Inbox      : %s", INBOX_DIR)
    logger.info("  Processing : %s", PROCESSING_DIR)
    logger.info("  Processed  : %s", PROCESSED_DIR)
    logger.info("  Failed     : %s", FAILED_DIR)


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _results_filename(original_stem: str) -> str:
    return f"{original_stem}_results_{_timestamp()}.csv"


async def _process_csv(csv_path: Path) -> bool:
    """
    Run the full bulk user onboarding pipeline on a single CSV file.

    Steps:
        1. Move to processing/ (prevents double-processing)
        2. Parse CSV
        3. Pre-flight validation
        4. Resolve names to IDs
        5. Run bulk onboarding
        6. Save results CSV
        7. Move original to processed/ or failed/

    Returns True on success (all rows completed or partial),
            False if pre-flight had blocking errors or an exception occurred.
    """
    from user_bot.triggers.csv_bulk_user_trigger import (
        parse_csv, preflight_validate, resolve_and_prepare,
    )
    from user_bot.flows.bulk_user_onboarding import run_bulk_user_onboarding
    from shared.models import BulkUserRowStatus
    import csv as csv_module
    import io

    stem         = csv_path.stem
    processing_path = PROCESSING_DIR / csv_path.name

    # ── Step 1: Move to processing ────────────────────────────────────────────
    try:
        shutil.move(str(csv_path), str(processing_path))
        logger.info("Moved to processing: %s", processing_path.name)
    except Exception as exc:
        logger.error("Could not move file to processing: %s", exc)
        return False

    try:
        content = processing_path.read_text(encoding="utf-8")

        # ── Step 2: Parse ─────────────────────────────────────────────────────
        rows = parse_csv(content)
        if not rows:
            raise ValueError("CSV file is empty or has no data rows.")
        logger.info("Parsed %d row(s) from '%s'.", len(rows), csv_path.name)

        # ── Step 3: Pre-flight validation ─────────────────────────────────────
        logger.info("Running pre-flight validation...")
        issues, missing_cols = await preflight_validate(rows)

        if missing_cols:
            raise ValueError(
                f"Required columns missing from CSV: {missing_cols}. "
                "Please fix the CSV and place it in the inbox again."
            )

        errors   = [i for i in issues if i.severity == "error"]
        warnings = [i for i in issues if i.severity == "warning"]

        if warnings:
            for w in warnings:
                logger.warning("Pre-flight warning row %d (%s): %s", w.row, w.upn, w.message)

        if errors:
            for e in errors:
                logger.error("Pre-flight error row %d (%s): %s", e.row, e.upn, e.message)
            logger.warning(
                "%d error(s) found — %d row(s) will be skipped during run.",
                len(errors), len(errors),
            )

        # ── Step 4: Resolve names to IDs ──────────────────────────────────────
        error_rows  = {i.row for i in errors}
        rows_to_run = [r for i, r in enumerate(rows, 1) if i not in error_rows]

        if not rows_to_run:
            raise ValueError(
                "All rows have pre-flight errors. No rows to process. "
                "Fix the CSV and place it in the inbox again."
            )

        logger.info(
            "Resolving names to IDs for %d row(s) (%d skipped)...",
            len(rows_to_run), len(error_rows),
        )
        prepared = await resolve_and_prepare(rows_to_run)

        # ── Step 5: Run bulk onboarding ───────────────────────────────────────
        logger.info("Starting bulk onboarding for %d user(s)...", len(prepared))
        summary = await run_bulk_user_onboarding(prepared, dry_run=False)

        logger.info(
            "Bulk onboarding complete: total=%d completed=%d partial=%d "
            "failed=%d skipped=%d duration=%.2fs",
            summary.total, summary.completed, summary.partial,
            summary.failed, summary.skipped, summary.total_duration,
        )

        # ── Step 6: Save results CSV ──────────────────────────────────────────
        results_filename = _results_filename(stem)
        results_path     = PROCESSED_DIR / results_filename

        output = io.StringIO()
        writer = csv_module.DictWriter(output, fieldnames=[
            "row", "display_name", "upn", "status",
            "user_id", "duration_seconds", "details",
        ])
        writer.writeheader()
        for r in summary.results:
            writer.writerow({
                "row":              r.row,
                "display_name":     r.display_name,
                "upn":              r.upn,
                "status":           r.status.value,
                "user_id":          r.user_id or "",
                "duration_seconds": r.duration_seconds,
                "details":          r.summary_line(),
            })

        # Add skipped rows from pre-flight errors
        for i, row in enumerate(rows, 1):
            if i in error_rows:
                upn = row.get("user_principal_name", f"row-{i}")
                err = next((e.message for e in errors if e.row == i), "Pre-flight error")
                writer.writerow({
                    "row": i, "display_name": row.get("display_name", upn),
                    "upn": upn, "status": "skipped",
                    "user_id": "", "duration_seconds": 0, "details": err,
                })

        results_path.write_text(output.getvalue(), encoding="utf-8")
        logger.info("Results saved: %s", results_path.name)

        # ── Step 7: Move original to processed ───────────────────────────────
        dest = PROCESSED_DIR / f"{stem}_{_timestamp()}.csv"
        shutil.move(str(processing_path), str(dest))
        logger.info("Original CSV moved to processed: %s", dest.name)

        return True

    except Exception as exc:
        logger.error("Processing failed for '%s': %s", csv_path.name, exc)

        # Move to failed folder with timestamp
        try:
            dest = FAILED_DIR / f"{stem}_{_timestamp()}_FAILED.csv"
            if processing_path.exists():
                shutil.move(str(processing_path), str(dest))
            elif csv_path.exists():
                shutil.move(str(csv_path), str(dest))
            logger.info("CSV moved to failed: %s", dest.name)

            # Write an error report alongside the failed CSV
            error_report = FAILED_DIR / f"{stem}_{_timestamp()}_ERROR.txt"
            error_report.write_text(
                f"File: {csv_path.name}\n"
                f"Time: {datetime.now().isoformat()}\n"
                f"Error: {exc}\n",
                encoding="utf-8",
            )
        except Exception as move_exc:
            logger.error("Could not move failed CSV: %s", move_exc)

        return False


# ── Watchdog event handler ────────────────────────────────────────────────────

class CSVHandler(FileSystemEventHandler):
    """
    Handles file system events in the inbox folder.
    Only processes .csv files. Ignores temp files, hidden files,
    and files already being processed.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop):
        self._loop      = loop
        self._seen: set = set()   # Track files being processed to avoid duplicates

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        path = Path(event.src_path)
        self._handle_file(path)

    def on_moved(self, event: FileSystemEvent) -> None:
        """Handles files moved/renamed into the inbox folder."""
        if event.is_directory:
            return
        path = Path(event.dest_path)
        self._handle_file(path)

    def _handle_file(self, path: Path) -> None:
        # Only process CSV files
        if path.suffix.lower() != ".csv":
            return
        # Ignore hidden/temp files
        if path.name.startswith(".") or path.name.startswith("~"):
            return
        # Ignore files not in the inbox (e.g. watchdog fires for subfolders)
        if path.parent.resolve() != INBOX_DIR.resolve():
            return
        # Avoid double-processing
        if str(path) in self._seen:
            return

        self._seen.add(str(path))
        logger.info("New CSV detected: %s", path.name)

        # Schedule async processing on the event loop
        asyncio.run_coroutine_threadsafe(
            self._process_with_settle(path), self._loop
        )

    async def _process_with_settle(self, path: Path) -> None:
        """Wait for file to finish copying before processing."""
        logger.info(
            "Waiting %ds for file to settle: %s",
            FILE_SETTLE_SECONDS, path.name,
        )
        await asyncio.sleep(FILE_SETTLE_SECONDS)

        # Check file still exists and is not empty
        if not path.exists():
            logger.warning("File disappeared before processing: %s", path.name)
            self._seen.discard(str(path))
            return

        if path.stat().st_size == 0:
            logger.warning("File is empty, skipping: %s", path.name)
            self._seen.discard(str(path))
            return

        success = await _process_csv(path)
        self._seen.discard(str(path))

        if success:
            logger.info("✓ Successfully processed: %s", path.name)
        else:
            logger.error("✗ Failed to process: %s — check watched_failed/", path.name)


# ── Main entry point ──────────────────────────────────────────────────────────

def run_watcher() -> None:
    """
    Start the folder watcher. Runs indefinitely until Ctrl+C.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("folder_watcher.log", encoding="utf-8"),
        ],
    )

    _ensure_folders()

    loop    = asyncio.new_event_loop()
    handler = CSVHandler(loop)

    observer = Observer()
    observer.schedule(handler, str(INBOX_DIR), recursive=False)
    observer.start()

    logger.info("=" * 60)
    logger.info("Folder watcher started.")
    logger.info("Drop CSV files into: %s", INBOX_DIR)
    logger.info("Press Ctrl+C to stop.")
    logger.info("=" * 60)

    # Process any CSV files already in the inbox on startup
    existing = list(INBOX_DIR.glob("*.csv"))
    if existing:
        logger.info(
            "Found %d existing CSV file(s) in inbox — processing now...",
            len(existing),
        )
        for csv_file in existing:
            asyncio.run_coroutine_threadsafe(
                handler._process_with_settle(csv_file), loop
            )

    # Run the async event loop in a separate thread
    import threading
    loop_thread = threading.Thread(target=loop.run_forever, daemon=True)
    loop_thread.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Stopping watcher...")
    finally:
        observer.stop()
        observer.join()
        loop.call_soon_threadsafe(loop.stop)
        loop_thread.join(timeout=5)
        logger.info("Folder watcher stopped.")


if __name__ == "__main__":
    run_watcher()

