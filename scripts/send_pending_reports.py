#!/usr/bin/env python3
"""Drains the report delivery queue (report_delivery table) — meant to run periodically via cron,
completely separate from the pygame process. This is what actually sends the e-mails enqueued by
"Encerrar sessão" in the admin panel; the panel itself only ever does the fast INSERT.

Usage (crontab, e.g. every 5 minutes):
    */5 * * * * /caminho/roulette-display/venv/bin/python3 /caminho/roulette-display/scripts/send_pending_reports.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import load_config
from app.database.db import Database
from app.delivery import delivery_queue
from app.logging_setup import setup_logging


def main() -> int:
    config = load_config()
    logger = setup_logging(config)

    db = Database(config.resolve(config.database_path))
    db.initialize()
    try:
        result = delivery_queue.process_pending(db, config)
        logger.info(
            "Fila de relatórios processada: %d enviados, %d falharam, %d ignorados (limite de tentativas)",
            result["sent"], result["failed"], result["skipped"],
        )
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
