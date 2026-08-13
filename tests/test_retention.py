"""Retenção de dados (30 dias por padrão): giros com `created_at` além de
`config.data_retention_days` são arquivados (CSV) e então removidos de verdade do banco -- pedido
explícito do cliente pra uma mesa 24/7 não crescer o banco/disco pra sempre (ver README,
"Limitação conhecida: histórico muito longo"). `spin_audit`/`audit_log` nunca são tocados por
essa política -- só a tabela `spins` em si."""
from __future__ import annotations

import csv
from datetime import datetime, timedelta
from unittest import mock

import pygame
import pytest

from app.config import Config
from app.database.db import Database
from app.services.backup_service import BackupService
from app.services.export_service import ExportService
from app.services.retention_service import RetentionService
from app.services.spin_service import SpinService
from app.ui.admin import AdminPanel
from app.ui.display import RouletteDisplay


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "roulette.db")
    database.initialize()
    yield database
    database.close()


def _insert_spin(db: Database, roulette_id: int, number: int, days_old: float, deleted: bool = False) -> int:
    """Insere um giro com `created_at`/`timestamp` no passado -- `register_spin` sempre usa
    "agora", então testar retenção exige controlar a idade diretamente."""
    from app.models.roulette_data import color_of

    db.ensure_roulette(roulette_id, "Mesa Teste")  # spins.roulette_id tem FK pra roulettes.id
    when = (datetime.now() - timedelta(days=days_old)).isoformat()
    with db._lock:
        cur = db._conn.execute(
            "INSERT INTO spins (roulette_id, number, color, timestamp, created_at, deleted, session_id) "
            "VALUES (?, ?, ?, ?, ?, ?, NULL)",
            (roulette_id, number, color_of(number), when, when, int(deleted)),
        )
        db._conn.commit()
        return cur.lastrowid


# -- camada de banco -----------------------------------------------------------------------------


def test_count_spins_older_than_only_counts_past_the_cutoff(db):
    _insert_spin(db, 1, 5, days_old=40)
    _insert_spin(db, 1, 12, days_old=31)
    _insert_spin(db, 1, 20, days_old=10)  # dentro da janela -- não deve contar

    cutoff = (datetime.now() - timedelta(days=30)).isoformat()
    assert db.count_spins_older_than(1, cutoff) == 2


def test_count_spins_older_than_includes_already_soft_deleted_rows(db):
    """Um giro corrigido (soft-delete) continua ocupando espaço no banco -- também deve ser
    contado/arquivado/purgado passados os 30 dias, não só os "ativos"."""
    _insert_spin(db, 1, 5, days_old=40, deleted=True)
    cutoff = (datetime.now() - timedelta(days=30)).isoformat()
    assert db.count_spins_older_than(1, cutoff) == 1


def test_export_spins_older_than_writes_only_the_old_rows_with_status_column(db, tmp_path):
    _insert_spin(db, 1, 5, days_old=40, deleted=False)
    _insert_spin(db, 1, 12, days_old=35, deleted=True)
    _insert_spin(db, 1, 20, days_old=5)  # recente -- não deve aparecer no arquivo

    cutoff = (datetime.now() - timedelta(days=30)).isoformat()
    dest = tmp_path / "arquivo.csv"
    db.export_spins_older_than(1, cutoff, dest)

    with open(dest, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 2
    assert {r["numero"] for r in rows} == {"5", "12"}
    statuses = {r["numero"]: r["status"] for r in rows}
    assert statuses["5"] == "ativo"
    assert statuses["12"] == "removido_pelo_operador"


def test_purge_spins_older_than_hard_deletes_and_returns_count(db):
    _insert_spin(db, 1, 5, days_old=40)
    _insert_spin(db, 1, 12, days_old=35)
    recent_id = _insert_spin(db, 1, 20, days_old=5)

    cutoff = (datetime.now() - timedelta(days=30)).isoformat()
    purged = db.purge_spins_older_than(1, cutoff)

    assert purged == 2
    with db._lock:
        remaining = db._conn.execute("SELECT id FROM spins WHERE roulette_id = 1").fetchall()
    assert [r["id"] for r in remaining] == [recent_id]


# -- RetentionService -----------------------------------------------------------------------------


def test_enforce_retention_is_a_noop_when_nothing_is_past_the_cutoff(db, tmp_path):
    _insert_spin(db, 1, 5, days_old=5)
    config = Config(database_path=str(tmp_path / "roulette.db"), exports_dir=str(tmp_path / "exports"),
                     data_retention_days=30)
    service = RetentionService(db, config)

    assert service.enforce_retention() == 0
    assert not (tmp_path / "exports").exists() or list((tmp_path / "exports").glob("*.csv")) == []


def test_enforce_retention_archives_then_purges_spins_past_the_cutoff(db, tmp_path):
    _insert_spin(db, 1, 5, days_old=40)
    _insert_spin(db, 1, 12, days_old=35)
    recent_id = _insert_spin(db, 1, 20, days_old=5)

    config = Config(database_path=str(tmp_path / "roulette.db"), exports_dir=str(tmp_path / "exports"),
                     roulette_id=1, data_retention_days=30)
    service = RetentionService(db, config)

    purged = service.enforce_retention()

    assert purged == 2
    archive_files = list((tmp_path / "exports").glob("arquivo-retencao-*.csv"))
    assert len(archive_files) == 1
    with open(archive_files[0], newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 2  # os dois giros arquivados antes de serem purgados

    with db._lock:
        remaining = db._conn.execute("SELECT id FROM spins WHERE roulette_id = 1").fetchall()
    assert [r["id"] for r in remaining] == [recent_id]


def test_enforce_retention_respects_a_custom_retention_window(db, tmp_path):
    """`data_retention_days` é configurável -- um valor menor deve purgar giros mais recentes
    também, não só o padrão de 30."""
    _insert_spin(db, 1, 5, days_old=10)
    config = Config(database_path=str(tmp_path / "roulette.db"), exports_dir=str(tmp_path / "exports"),
                     roulette_id=1, data_retention_days=7)
    service = RetentionService(db, config)

    assert service.enforce_retention() == 1


def test_enforce_retention_never_touches_spin_audit_or_audit_log(db, tmp_path):
    """A trilha de auditoria (spin_audit + audit_log, esta com cadeia de hash) precisa sobreviver
    mesmo depois que o giro em si foi purgado -- só a linha "viva" em `spins` é removida."""
    spin_id = _insert_spin(db, 1, 5, days_old=40)
    with db._lock:
        db._conn.execute(
            "INSERT INTO spin_audit (spin_id, action, reason, at, original_number, operator) "
            "VALUES (?, 'undo', 'correção manual', ?, 5, 'teclado')",
            (spin_id, datetime.now().isoformat()),
        )
        db._conn.commit()

    config = Config(database_path=str(tmp_path / "roulette.db"), exports_dir=str(tmp_path / "exports"),
                     roulette_id=1, data_retention_days=30)
    RetentionService(db, config).enforce_retention()

    with db._lock:
        audit_rows = db._conn.execute("SELECT * FROM spin_audit WHERE spin_id = ?", (spin_id,)).fetchall()
    assert len(audit_rows) == 1  # a entrada de auditoria sobrevive mesmo com o spin já purgado


def test_enforce_retention_is_idempotent(db, tmp_path):
    _insert_spin(db, 1, 5, days_old=40)
    config = Config(database_path=str(tmp_path / "roulette.db"), exports_dir=str(tmp_path / "exports"),
                     roulette_id=1, data_retention_days=30)
    service = RetentionService(db, config)

    assert service.enforce_retention() == 1
    assert service.enforce_retention() == 0  # nada além do corte na segunda chamada
    archive_files = list((tmp_path / "exports").glob("arquivo-retencao-*.csv"))
    assert len(archive_files) == 1  # não criou um segundo arquivo à toa


# -- wiring no loop principal (RouletteDisplay) --------------------------------------------------


@pytest.fixture
def display(tmp_path):
    config = Config(fullscreen=False, database_path=str(tmp_path / "roulette.db"), assets_dir="assets")
    database = Database(tmp_path / "roulette.db")
    database.initialize()
    d = RouletteDisplay(config, database)
    yield d
    database.close()
    pygame.quit()


def test_enforce_retention_wrapper_swallows_exceptions(display):
    """Uma falha na retenção (ex.: disco cheio na hora de exportar o arquivo) não pode derrubar o
    painel -- só vai pro log, a próxima tentativa (algumas horas depois) tenta de novo."""
    with mock.patch.object(display.retention_service, "enforce_retention",
                            side_effect=RuntimeError("disco cheio")):
        display._enforce_retention()  # não deve levantar


def test_enforce_retention_not_called_on_every_render_frame(display):
    """Mesmo espírito de `test_health_check_does_not_run_on_every_frame`: a retenção só deve
    rodar quando explicitamente chamada pelo gate por tempo em `run()`, nunca dentro de
    `_render()` por frame."""
    with mock.patch.object(display.retention_service, "enforce_retention") as spy:
        for _ in range(30):
            display._render()
        assert spy.call_count == 0


# -- ação manual no painel administrativo ---------------------------------------------------------


def _make_admin_panel(database: Database, config: Config, tmp_path) -> AdminPanel:
    service = SpinService(database, config)
    return AdminPanel(config, service, BackupService(database, config), ExportService(database, config),
                       RetentionService(database, config), config_path=str(tmp_path / "config.yaml"))


def test_admin_run_retention_now_reports_zero_when_nothing_to_purge(db, tmp_path):
    config = Config(database_path=str(tmp_path / "roulette.db"), exports_dir=str(tmp_path / "exports"))
    panel = _make_admin_panel(db, config, tmp_path)

    assert "Nenhum giro" in panel._run_retention_text()


def test_admin_run_retention_now_reports_purged_count(db, tmp_path):
    _insert_spin(db, 1, 5, days_old=40)
    config = Config(database_path=str(tmp_path / "roulette.db"), exports_dir=str(tmp_path / "exports"),
                     roulette_id=1, data_retention_days=30)
    panel = _make_admin_panel(db, config, tmp_path)

    text = panel._run_retention_text()
    assert "1 giro" in text
    assert "arquivados" in text
