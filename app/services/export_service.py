"""Timestamped CSV export helper on top of Database.export_csv."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app.config import Config
from app.database.db import Database


class ExportService:
    def __init__(self, db: Database, config: Config):
        self.db = db
        self.config = config

    def export_csv(self) -> Path:
        exports_dir = self.config.resolve(self.config.exports_dir)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = exports_dir / f"resultados-{stamp}.csv"
        return self.db.export_csv(self.config.roulette_id, dest)
