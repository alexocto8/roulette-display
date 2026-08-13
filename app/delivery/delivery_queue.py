"""Persistent, retryable e-mail delivery queue (section 47) — the actual mechanism that keeps a
report from ever being lost to "the Pi didn't have internet right then". Enqueuing (called from
the admin panel right after a report is generated) is a single fast INSERT; sending happens later,
from a separate process (scripts/send_pending_reports.py), so a slow/unreachable SMTP server can
never make the panel feel stuck.
"""
from __future__ import annotations

import logging
from pathlib import Path

from app.config import Config
from app.database.db import Database
from app.delivery import email_service

logger = logging.getLogger("roulette.delivery")

MAX_ATTEMPTS = 5


def enqueue_report(db: Database, report_dir: str | Path, session_id: int | None = None) -> None:
    db.enqueue_delivery(str(report_dir), session_id=session_id)


def _report_paths_in(report_dir: Path) -> dict:
    paths = {}
    for kind, ext in (("pdf", ".pdf"), ("csv", ".csv"), ("json", ".json")):
        matches = list(report_dir.glob(f"*{ext}"))
        if matches:
            paths[kind] = matches[0]
    return paths


def process_pending(db: Database, config: Config, limit: int = 20) -> dict:
    """Attempts every PENDING/FAILED delivery once, up to `limit` rows. Returns
    {"sent": n, "failed": n, "skipped": n} — `skipped` counts rows that already exhausted
    MAX_ATTEMPTS (left as FAILED permanently, not retried forever)."""
    result = {"sent": 0, "failed": 0, "skipped": 0}
    for delivery in db.get_pending_deliveries(limit=limit):
        if delivery["attempts"] >= MAX_ATTEMPTS:
            result["skipped"] += 1
            continue

        db.mark_delivery_sending(delivery["id"])
        try:
            report_dir = Path(delivery["report_dir"])
            session_row = db.get_session(delivery["session_id"]) if delivery["session_id"] else None
            if session_row is None:
                raise RuntimeError(f"sessão {delivery['session_id']} não encontrada")
            report_paths = _report_paths_in(report_dir)
            email_service.send_report_email(config, session_row, report_paths)
            db.mark_delivery_sent(delivery["id"])
            result["sent"] += 1
        except Exception as exc:
            logger.warning("Falha ao enviar relatório (delivery id=%s): %s", delivery["id"], exc, exc_info=True)
            db.mark_delivery_failed(delivery["id"], str(exc)[:500])
            result["failed"] += 1
    return result
