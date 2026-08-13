"""Item 22 da auditoria: logs para operação 24/7 — rotação limitada (sem crescimento infinito),
timestamps, e nenhum dado sensível (PIN, material criptográfico) em texto puro."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from app.config import Config
from app.logging_setup import setup_logging


def test_file_handler_is_bounded_rotating_handler(tmp_path):
    config = Config(logs_dir=str(tmp_path / "logs"), log_max_bytes=123_456, log_backup_count=3)
    logger = setup_logging(config)

    file_handlers = [h for h in logger.handlers if isinstance(h, RotatingFileHandler)]
    assert len(file_handlers) == 1
    handler = file_handlers[0]
    assert handler.maxBytes == 123_456
    assert handler.backupCount == 3
    # Espaço em disco no pior caso é limitado: (backupCount + 1) * maxBytes, nunca ilimitado.


def test_log_lines_include_timestamp_and_level(tmp_path):
    config = Config(logs_dir=str(tmp_path / "logs"))
    logger = setup_logging(config)
    logger.info("evento de teste")
    for handler in logger.handlers:
        handler.flush()

    content = (tmp_path / "logs" / "app.log").read_text(encoding="utf-8")
    assert "INFO" in content
    assert "evento de teste" in content
    # Formato "%(asctime)s ..." -> começa com um ano de 4 dígitos.
    first_line = content.strip().splitlines()[0]
    assert first_line[:4].isdigit()


def test_admin_pin_value_is_never_written_to_the_log_file(tmp_path):
    """A ação de trocar o PIN é logada (auditoria de ações administrativas) — o VALOR do PIN
    nunca pode aparecer, nem o antigo nem o novo."""
    from app.config import Config as ConfigClass
    from app.database.db import Database
    from app.services.backup_service import BackupService
    from app.services.export_service import ExportService
    from app.services.spin_service import SpinService
    from app.ui.admin import AdminPanel

    logs_dir = tmp_path / "logs"
    config = ConfigClass(
        logs_dir=str(logs_dir), database_path=str(tmp_path / "roulette.db"), admin_pin="1234",
    )
    setup_logging(config)
    logger = logging.getLogger("roulette")
    for handler in logger.handlers:
        handler.flush()

    db = Database(tmp_path / "roulette.db")
    db.initialize()
    service = SpinService(db, config)
    panel = AdminPanel(config, service, BackupService(db, config), ExportService(db, config),
                        config_path=str(tmp_path / "config.yaml"))
    panel.state = "menu"
    panel._activate("admin_pin")  # entra no fluxo de troca de PIN (loga só o nome da ação)
    panel.edit_buffer = "9999"
    panel._save_edit()  # troca de fato o PIN pra "9999"

    for handler in logging.getLogger("roulette").handlers:
        handler.flush()
    content = (logs_dir / "app.log").read_text(encoding="utf-8")

    assert "1234" not in content  # PIN antigo
    assert "9999" not in content  # PIN novo
    db.close()
