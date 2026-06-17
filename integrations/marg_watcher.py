"""
integrations/marg_watcher.py  —  Marg ERP File-Drop Watcher
=============================================================
Uses watchdog to monitor a folder for new Marg ERP Excel exports.
When a new file appears, it auto-imports the data into the DB.

Marg exports standard Excel files. The watcher:
  1. Detects new .xls / .xlsx files in MARG_WATCH_DIR
  2. Parses them using existing data_loader logic (detect_report_type)
  3. Appends data to the DB with tenant_id from folder mapping
  4. Sends a WhatsApp/email notification on success

Setup
-----
  MARG_WATCH_DIR=/mnt/marg_exports          # root folder
  MARG_TENANT_MAP={"3": "keelkattalai"}     # tenant_id → subfolder name
  (or use flat structure with single tenant)

Watchdog is started by scheduler.py → _start_marg_watcher()
"""

import os
import json
import time
import shutil
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

try:
    from watchdog.events import FileSystemEventHandler, FileCreatedEvent
    from watchdog.observers import Observer
    HAS_WATCHDOG = True
except ImportError:
    HAS_WATCHDOG = False
    # Stub class so import doesn't fail
    class FileSystemEventHandler:          # type: ignore
        pass

MARG_WATCH_DIR  = os.getenv("MARG_WATCH_DIR",   "")
MARG_TENANT_MAP = json.loads(os.getenv("MARG_TENANT_MAP", "{}"))  # {"3":"subfolder"}
PROCESSED_DIR   = os.getenv("MARG_PROCESSED_DIR", "")  # archive folder (optional)
ALLOWED_EXT     = {".xls", ".xlsx", ".csv"}


class MargFileHandler(FileSystemEventHandler):
    """
    Watchdog event handler for Marg ERP file-drop folder.

    engine  : SQLAlchemy engine (passed from scheduler)
    """

    def __init__(self, engine: Any = None, tenant_id: Optional[int] = None):
        super().__init__()
        self.engine    = engine
        self.tenant_id = tenant_id
        self._processing: set = set()  # debounce: filenames currently being processed

    def on_created(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix.lower() not in ALLOWED_EXT:
            return
        # Small delay so the file is fully written before we read it
        time.sleep(2)
        self._process_file(str(path))

    def on_moved(self, event):
        """Also handle files moved into the watch folder."""
        if event.is_directory:
            return
        path = Path(event.dest_path)
        if path.suffix.lower() not in ALLOWED_EXT:
            return
        time.sleep(1)
        self._process_file(str(path))

    def _process_file(self, filepath: str) -> None:
        if filepath in self._processing:
            return
        self._processing.add(filepath)
        try:
            logger.info("[marg_watcher] New file detected: %s", filepath)
            self._import_file(filepath)
        except Exception as exc:
            logger.error("[marg_watcher] Error processing %s: %s", filepath, exc)
            self._send_error_alert(filepath, str(exc))
        finally:
            self._processing.discard(filepath)

    def _import_file(self, filepath: str) -> None:
        """Parse and import a Marg export file."""
        from data_loader import detect_report_type, _read_raw, _parse_sales, _parse_purchases
        from sqlalchemy import text

        path      = Path(filepath)
        engine    = self.engine

        # Determine tenant_id from subfolder name (if multi-tenant folder structure)
        tenant_id = self.tenant_id
        if not tenant_id and MARG_TENANT_MAP:
            parent = path.parent.name
            for tid, folder in MARG_TENANT_MAP.items():
                if folder.lower() == parent.lower():
                    tenant_id = int(tid)
                    break

        # Parse raw dataframe
        try:
            raw_df = _read_raw(filepath)
        except Exception as exc:
            raise RuntimeError(f"Could not read file: {exc}") from exc

        # Auto-detect report type
        report_type = detect_report_type(raw_df)
        if not report_type:
            raise ValueError(f"Could not determine report type for {path.name}")

        # Parse into canonical schema
        if report_type == "sales":
            df = _parse_sales(raw_df)
            if "branch" not in df.columns:
                df["branch"] = path.stem          # use filename as branch fallback
        elif report_type == "purchase":
            df = _parse_purchases(raw_df)
            if "branch" not in df.columns:
                df["branch"] = path.stem
        else:
            raise ValueError(f"Unknown report type: {report_type}")

        if df.empty:
            raise ValueError(f"Parsed dataframe is empty for {path.name}")

        # Add tenant_id column
        if tenant_id:
            df["tenant_id"] = tenant_id

        # Add upload_id tracking
        upload_id = self._log_upload(engine, path.name, report_type, len(df), tenant_id)
        df["upload_id"] = upload_id

        # Write to DB
        table = "sales_data" if report_type == "sales" else "purchase_data"
        df.to_sql(table, engine, if_exists="append", index=False)
        logger.info("[marg_watcher] Imported %d rows (%s) from %s → table=%s",
                    len(df), report_type, path.name, table)

        # Archive processed file
        self._archive_file(filepath)

        # Success notification
        self._send_success_alert(filepath, report_type, len(df), tenant_id)

    def _log_upload(self, engine: Any, filename: str, report_type: str,
                    row_count: int, tenant_id: Optional[int]) -> int:
        """Log to upload_history and return the new upload_id."""
        if engine is None:
            return 0
        try:
            from sqlalchemy import text
            with engine.begin() as conn:
                result = conn.execute(text("""
                    INSERT INTO upload_history
                        (filename, report_type, branch, row_count, tenant_id,
                         uploaded_at, status, source)
                    VALUES (:fn, :rt, 'marg_auto', :rc, :tid,
                            datetime('now'), 'active', 'marg_watcher')
                    RETURNING id
                """), {"fn": filename, "rt": report_type, "rc": row_count, "tid": tenant_id})
                row = result.fetchone()
                return row[0] if row else 0
        except Exception as exc:
            logger.warning("[marg_watcher] Could not log upload: %s", exc)
            return 0

    def _archive_file(self, filepath: str) -> None:
        """Move processed file to MARG_PROCESSED_DIR (if configured)."""
        if not PROCESSED_DIR:
            return
        try:
            os.makedirs(PROCESSED_DIR, exist_ok=True)
            dest = os.path.join(
                PROCESSED_DIR,
                f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{Path(filepath).name}"
            )
            shutil.move(filepath, dest)
            logger.info("[marg_watcher] Archived to %s", dest)
        except Exception as exc:
            logger.warning("[marg_watcher] Archive failed: %s", exc)

    def _send_success_alert(self, filepath: str, report_type: str,
                            row_count: int, tenant_id: Optional[int]) -> None:
        """Send success notification via configured alert channels."""
        try:
            from alerts import get_tenant_channels, send_multi_channel
            channels = get_tenant_channels(tenant_id, self.engine) if tenant_id else []
            if not channels:
                return
            fname   = Path(filepath).name
            subject = f"✅ Marg Import Successful — {fname}"
            body    = (
                f"A new Marg ERP file has been imported automatically.\n\n"
                f"  File:        {fname}\n"
                f"  Report Type: {report_type.title()}\n"
                f"  Rows Added:  {row_count:,}\n"
                f"  Imported At: {datetime.now().strftime('%d %b %Y %H:%M')}\n\n"
                f"Your dashboard has been updated."
            )
            send_multi_channel(channels, subject, body, tenant_id=tenant_id,
                               alert_level="success")
        except Exception as exc:
            logger.warning("[marg_watcher] Success alert failed: %s", exc)

    def _send_error_alert(self, filepath: str, error: str) -> None:
        """Send failure notification."""
        try:
            from alerts import get_tenant_channels, send_multi_channel
            channels = get_tenant_channels(self.tenant_id, self.engine) if self.tenant_id else []
            if not channels:
                return
            fname   = Path(filepath).name
            subject = f"❌ Marg Import Failed — {fname}"
            body    = f"Failed to import file: {fname}\n\nError: {error}\n\nPlease check the file format and try again."
            send_multi_channel(channels, subject, body, alert_level="danger")
        except Exception as exc:
            logger.warning("[marg_watcher] Error alert failed: %s", exc)


def start_marg_watcher(engine: Any, watch_dir: str = "",
                        tenant_id: Optional[int] = None) -> Optional[Any]:
    """
    Start a Marg file watcher Observer.
    Returns the Observer instance (call observer.stop() to shut down).
    """
    if not HAS_WATCHDOG:
        logger.error("[marg_watcher] watchdog not installed: pip install watchdog")
        return None

    dir_to_watch = watch_dir or MARG_WATCH_DIR
    if not dir_to_watch:
        logger.warning("[marg_watcher] No watch directory configured.")
        return None
    if not os.path.isdir(dir_to_watch):
        os.makedirs(dir_to_watch, exist_ok=True)
        logger.info("[marg_watcher] Created watch directory: %s", dir_to_watch)

    handler  = MargFileHandler(engine=engine, tenant_id=tenant_id)
    observer = Observer()
    observer.schedule(handler, path=dir_to_watch, recursive=True)
    observer.daemon = True
    observer.start()
    logger.info("[marg_watcher] Watching: %s", dir_to_watch)
    return observer
