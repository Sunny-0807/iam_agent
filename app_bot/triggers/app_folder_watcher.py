"""
App Folder Watcher — Automated Bulk App Onboarding Trigger.

Watches watched_apps_inbox/ for new CSV files and automatically
runs the bulk SAML app onboarding pipeline.

Usage:
    python app_bot/triggers/app_folder_watcher.py

Folder structure:
    watched_apps_inbox/
    watched_apps_processing/
    watched_apps_processed/
    watched_apps_failed/
"""


import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import asyncio
import logging
import os
import shutil
import time
from datetime import datetime

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env", override=False)

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

logger = logging.getLogger("app_folder_watcher")

PROJECT_ROOT     = Path(__file__).resolve().parent.parent.parent
INBOX_DIR        = Path(os.getenv("APP_WATCHER_INBOX",      str(PROJECT_ROOT / "watched_apps_inbox")))
PROCESSING_DIR   = Path(os.getenv("APP_WATCHER_PROCESSING", str(PROJECT_ROOT / "watched_apps_processing")))
PROCESSED_DIR    = Path(os.getenv("APP_WATCHER_PROCESSED",  str(PROJECT_ROOT / "watched_apps_processed")))
FAILED_DIR       = Path(os.getenv("APP_WATCHER_FAILED",     str(PROJECT_ROOT / "watched_apps_failed")))
FILE_SETTLE_SECS = int(os.getenv("APP_WATCHER_SETTLE_SECS", "3"))


def _ensure_folders():
    for folder in [INBOX_DIR, PROCESSING_DIR, PROCESSED_DIR, FAILED_DIR]:
        folder.mkdir(parents=True, exist_ok=True)
    logger.info("App watcher folders ready:")
    logger.info("  Inbox      : %s", INBOX_DIR)
    logger.info("  Processing : %s", PROCESSING_DIR)
    logger.info("  Processed  : %s", PROCESSED_DIR)
    logger.info("  Failed     : %s", FAILED_DIR)


def _timestamp():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


async def _process_csv(csv_path: Path) -> bool:
    from ai_engine.agent_pipeline import (
        run_collection_agent, run_analysis_agent,
        run_decision_agent, run_execution_agent,
    )
    from shared.config import config
    import csv as _csv, io

    stem            = csv_path.stem
    processing_path = PROCESSING_DIR / csv_path.name

    try:
        shutil.move(str(csv_path), str(processing_path))
        logger.info("Moved to processing: %s", processing_path.name)
    except Exception as exc:
        logger.error("Could not move to processing: %s", exc)
        return False

    try:
        content = processing_path.read_text(encoding="utf-8")

        # Run pipeline
        collection = await run_collection_agent(content, csv_path.name, "folder_watcher", "app")
        if collection.error:
            raise ValueError(collection.error)

        analysis = await run_analysis_agent(collection, "app")
        if analysis.missing_columns:
            raise ValueError(f"Missing columns: {analysis.missing_columns}")

        decision = await run_decision_agent(analysis, "app", skip_approval=config.skip_approval)
        execution = await run_execution_agent(analysis, decision, "app")

        # ── Save enriched CSV — original columns + result columns ────────────
        RESULT_COLS = ["status", "app_id", "cert_thumbprint", "duration_seconds", "error"]
        results     = execution.summary or []

        # Parse original CSV rows to get original columns
        import csv as _csv2
        orig_rows = list(_csv2.DictReader(io.StringIO(
            processing_path.read_text(encoding="utf-8")
        )))
        original_fieldnames = list(orig_rows[0].keys()) if orig_rows else []
        extra_cols          = [c for c in RESULT_COLS if c not in original_fieldnames]
        all_fieldnames      = original_fieldnames + extra_cols

        # Build lookup: display_name → result
        result_map = {r.display_name.lower(): r for r in results}

        out    = io.StringIO()
        writer = _csv.DictWriter(out, fieldnames=all_fieldnames, extrasaction="ignore")
        writer.writeheader()
        for orig_row in orig_rows:
            out_row = dict(orig_row)
            key     = orig_row.get("display_name", "").lower()
            if key in result_map:
                r = result_map[key]
                out_row["status"]           = r.status
                out_row["app_id"]           = r.app_id or ""
                out_row["cert_thumbprint"]  = r.cert_thumbprint or ""
                out_row["duration_seconds"] = r.duration_seconds
                out_row["error"]            = r.error or ""
            else:
                out_row["status"]           = "unknown"
                out_row["app_id"]           = ""
                out_row["cert_thumbprint"]  = ""
                out_row["duration_seconds"] = 0
                out_row["error"]            = ""
            writer.writerow(out_row)

        enriched_path = PROCESSED_DIR / f"{stem}_{_timestamp()}.csv"
        enriched_path.write_text(out.getvalue(), encoding="utf-8")

        processing_path.unlink()
        if csv_path.exists():
            csv_path.unlink()
            logger.info("Removed residual inbox copy: %s", csv_path.name)
        logger.info("Enriched CSV saved to processed: %s", enriched_path.name)
        return True

    except Exception as exc:
        logger.error("Processing failed for '%s': %s", csv_path.name, exc)
        try:
            dest = FAILED_DIR / f"{stem}_{_timestamp()}_FAILED.csv"
            src  = processing_path if processing_path.exists() else csv_path
            if src.exists():
                shutil.move(str(src), str(dest))
            err_file = FAILED_DIR / f"{stem}_{_timestamp()}_ERROR.txt"
            err_file.write_text(
                f"File: {csv_path.name}\nTime: {datetime.now().isoformat()}\nError: {exc}\n",
                encoding="utf-8",
            )
        except Exception as move_exc:
            logger.error("Could not move failed CSV: %s", move_exc)
        return False


class AppCSVHandler(FileSystemEventHandler):
    def __init__(self, loop):
        self._loop = loop
        self._seen: set = set()

    def on_created(self, event: FileSystemEvent):
        if not event.is_directory:
            self._handle(Path(event.src_path))

    def on_moved(self, event: FileSystemEvent):
        if not event.is_directory:
            self._handle(Path(event.dest_path))

    def _handle(self, path: Path):
        if path.suffix.lower() != ".csv": return
        if path.name.startswith(".") or path.name.startswith("~"): return
        if path.parent.resolve() != INBOX_DIR.resolve(): return
        if str(path) in self._seen: return
        self._seen.add(str(path))
        logger.info("New app CSV detected: %s", path.name)
        asyncio.run_coroutine_threadsafe(self._settle_and_process(path), self._loop)

    async def _settle_and_process(self, path: Path):
        try:
            await asyncio.sleep(FILE_SETTLE_SECS)
            if not path.exists() or path.stat().st_size == 0:
                return
            logger.info("Starting pipeline for: %s", path.name)
            success = await _process_csv(path)
            if success:
                logger.info("✓ Successfully processed: %s", path.name)
            else:
                logger.error("✗ Failed — check watched_apps_failed/: %s", path.name)
        except Exception as exc:
            import traceback
            logger.error(
                "Unhandled exception processing '%s': %s\n%s",
                path.name, exc, traceback.format_exc(),
            )
        finally:
            self._seen.discard(str(path))


def run_watcher():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("app_folder_watcher.log", encoding="utf-8"),
        ],
    )
    _ensure_folders()
    loop     = asyncio.new_event_loop()
    handler  = AppCSVHandler(loop)
    observer = Observer()
    observer.schedule(handler, str(INBOX_DIR), recursive=False)
    observer.start()

    logger.info("=" * 60)
    logger.info("App folder watcher started.")
    logger.info("Drop CSV files into: %s", INBOX_DIR)
    logger.info("Press Ctrl+C to stop.")
    logger.info("=" * 60)

    existing = list(INBOX_DIR.glob("*.csv"))
    if existing:
        logger.info("Found %d existing file(s) in inbox — processing...", len(existing))
        for f in existing:
            asyncio.run_coroutine_threadsafe(handler._settle_and_process(f), loop)

    import threading
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Stopping...")
    finally:
        observer.stop()
        observer.join()
        loop.call_soon_threadsafe(loop.stop)
        t.join(timeout=5)
        logger.info("App folder watcher stopped.")


if __name__ == "__main__":
    run_watcher()

