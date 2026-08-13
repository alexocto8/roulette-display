from app.config import Config
from app.database.db import Database
from app.services.backup_service import BackupService


def test_prune_keeps_only_the_most_recent_backups(tmp_path):
    backups_dir = tmp_path / "backups"
    backups_dir.mkdir()
    # Nomes com timestamp crescente, do mais antigo pro mais recente — sem precisar esperar
    # segundos reais passarem entre criações pra ter nomes de arquivo distintos.
    for stamp in ("20260101-000000", "20260102-000000", "20260103-000000", "20260104-000000", "20260105-000000"):
        (backups_dir / f"roulette-backup-{stamp}.db").write_bytes(b"fake")

    db = Database(tmp_path / "roulette.db")
    db.initialize()
    config = Config(backups_dir=str(backups_dir), backup_retention_count=3)
    service = BackupService(db, config)

    service._prune_old_backups()

    remaining = {p.name for p in service.list_backups()}
    assert remaining == {
        "roulette-backup-20260105-000000.db",
        "roulette-backup-20260104-000000.db",
        "roulette-backup-20260103-000000.db",
    }
    db.close()


def test_create_backup_triggers_pruning(tmp_path):
    backups_dir = tmp_path / "backups"
    backups_dir.mkdir()
    for stamp in ("20200101-000000", "20200102-000000"):
        (backups_dir / f"roulette-backup-{stamp}.db").write_bytes(b"fake")

    db = Database(tmp_path / "roulette.db")
    db.initialize()
    db.ensure_roulette(1, "Roleta 01")
    config = Config(backups_dir=str(backups_dir), backup_retention_count=1)
    service = BackupService(db, config)

    service.create_backup()

    # retention_count=1: só o backup recém-criado deve sobrar (o mais recente de todos).
    remaining = service.list_backups()
    assert len(remaining) == 1
    assert "2020" not in remaining[0].name
    db.close()
