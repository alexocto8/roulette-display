"""Timestamped backup/restore helpers on top of Database.backup/restore."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app.config import Config
from app.database.db import Database


class BackupService:
    def __init__(self, db: Database, config: Config):
        self.db = db
        self.config = config

    def create_backup(self) -> Path:
        backups_dir = self.config.resolve(self.config.backups_dir)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = backups_dir / f"roulette-backup-{stamp}.db"
        result = self.db.backup(dest)
        self._prune_old_backups()
        return result

    def _prune_old_backups(self) -> None:
        """Keeps only the `backup_retention_count` most recent backups — 24/7 sem limpeza manual
        faz esse diretório crescer sem parar, senão (cada backup é uma cópia inteira do banco)."""
        keep = max(1, self.config.backup_retention_count)
        backups = self.list_backups()
        for stale in backups[keep:]:
            try:
                stale.unlink()
            except OSError:
                pass

    def list_backups(self) -> list[Path]:
        backups_dir = self.config.resolve(self.config.backups_dir)
        if not backups_dir.exists():
            return []
        return sorted(backups_dir.glob("roulette-backup-*.db"), reverse=True)

    def restore_backup(self, backup_path: str | Path) -> None:
        """Restores a backup file over the live database file. The caller (admin panel) is
        responsible for closing the current Database connection and reopening a new one, and for
        making the operator confirm this is destructive before calling it."""
        Database.restore(backup_path, self.config.resolve(self.config.database_path))
