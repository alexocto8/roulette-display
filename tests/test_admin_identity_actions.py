"""Testes de integração das novas ações do painel admin (identidade, logo, sessão, auditoria
completa) via chamadas diretas de método — mais confiável e rápido que simular teclas via
xdotool, e é exatamente o tipo de teste que teria pego, antes de qualquer verificação visual, o
bug real encontrado nesta rodada: `IdentityService.update(venue_logo_path=...)` levantava
`ValueError` porque `venue_logo_path` não estava em `MAX_LENGTHS` — o fluxo de "Importar logo"
inteiro (validar -> salvar arquivo -> gravar path na identidade) crashava o processo."""
from __future__ import annotations

from PIL import Image

import pygame

from app.config import Config
from app.database.db import Database
from app.services.backup_service import BackupService
from app.services.export_service import ExportService
from app.services.retention_service import RetentionService
from app.services.spin_service import SpinService
from app.ui.admin import AdminPanel


def make_panel(tmp_path) -> AdminPanel:
    config = Config(
        database_path=str(tmp_path / "roulette.db"),
        branding_dir=str(tmp_path / "branding"),
        reports_dir=str(tmp_path / "reports"),
        report_signing_key_path=str(tmp_path / "report_signing_key.pem"),
        smtp_credentials_path=str(tmp_path / "smtp_credentials.yaml"),
    )
    db = Database(tmp_path / "roulette.db")
    db.initialize()
    service = SpinService(db, config)
    return AdminPanel(config, service, BackupService(db, config), ExportService(db, config),
                       RetentionService(db, config), config_path=str(tmp_path / "config.yaml"))


def key_event(key, unicode=""):
    return pygame.event.Event(pygame.KEYDOWN, key=key, mod=0, unicode=unicode)


# -- identidade: editar campos de texto -------------------------------------------------------


def test_edit_table_code_saves_through_identity_service(tmp_path):
    panel = make_panel(tmp_path)
    panel._activate("table_code")
    assert panel.state == "edit_text"
    panel.edit_buffer = "JP-M01"
    panel._save_edit()
    assert panel.state == "menu"
    assert panel.service.identity.get()["table_code"] == "JP-M01"


def test_edit_table_location_and_device_name(tmp_path):
    panel = make_panel(tmp_path)
    panel._activate("table_location")
    panel.edit_buffer = "Salao Principal"
    panel._save_edit()
    panel._activate("device_name")
    panel.edit_buffer = "roulette-jp-01"
    panel._save_edit()

    identity = panel.service.identity.get()
    assert identity["table_location"] == "Salao Principal"
    assert identity["device_name"] == "roulette-jp-01"


def test_casino_name_edit_writes_through_to_identity_venue_name(tmp_path):
    panel = make_panel(tmp_path)
    panel._activate("casino_name")
    panel.edit_buffer = "Jackpot Poker Club"
    panel._save_edit()
    assert panel.service.identity.get()["venue_name"] == "Jackpot Poker Club"
    assert panel.config.casino_name == "Jackpot Poker Club"


def test_table_id_info_shows_the_permanent_id(tmp_path):
    panel = make_panel(tmp_path)
    table_id = panel.service.identity.get()["table_id"]
    panel._activate("table_id_info")
    assert panel.state == "message"
    assert table_id in panel.message


# -- logo: importar / remover -----------------------------------------------------------------


def test_import_logo_with_no_staged_files_shows_a_helpful_message(tmp_path):
    panel = make_panel(tmp_path)
    panel._activate("import_logo")
    assert panel.state == "message"
    assert "Nenhuma imagem encontrada" in panel.message


def test_import_logo_end_to_end_does_not_crash_and_updates_identity(tmp_path):
    """Este é o teste que reproduz o bug real: sem o fix em MAX_LENGTHS, a linha
    `self._save_edit()`-equivalente (aqui, `_handle_logo_pick` -> `identity.update(...)`) levanta
    ValueError sem ser capturado por `_handle_logo_pick`, que se propagaria pro loop principal."""
    panel = make_panel(tmp_path)
    staging = panel.config.resolve(panel.config.branding_dir) / "incoming"
    staging.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (300, 200), (255, 0, 0)).save(staging / "logo_teste.png")

    panel._activate("import_logo")
    assert panel.state == "logo_pick"
    assert len(panel.logo_files) == 1

    panel._handle_logo_pick(pygame.K_RETURN)  # não deve levantar exceção

    assert panel.state == "message"
    assert "sucesso" in panel.message.lower()
    stored_path = panel.service.identity.get()["venue_logo_path"]
    assert stored_path
    from pathlib import Path

    assert Path(stored_path).exists()
    assert Path(stored_path).name == "logo.png"


def test_import_logo_rejects_invalid_staged_file_without_crashing(tmp_path):
    panel = make_panel(tmp_path)
    staging = panel.config.resolve(panel.config.branding_dir) / "incoming"
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "nao_e_imagem.png").write_bytes(b"lixo")

    panel._activate("import_logo")
    panel._handle_logo_pick(pygame.K_RETURN)

    assert panel.state == "message"
    assert "não foi possível" in panel.message.lower() or "nao foi possivel" in panel.message.lower()
    assert panel.service.identity.get()["venue_logo_path"] in ("", None)


def test_remove_logo_clears_the_stored_path(tmp_path):
    panel = make_panel(tmp_path)
    staging = panel.config.resolve(panel.config.branding_dir) / "incoming"
    staging.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (300, 200), (0, 255, 0)).save(staging / "logo.png")
    panel._activate("import_logo")
    panel._handle_logo_pick(pygame.K_RETURN)
    assert panel.service.identity.get()["venue_logo_path"]

    panel._activate("remove_logo")
    assert panel.service.identity.get()["venue_logo_path"] == ""


# -- auditoria completa: filtro + paginação -----------------------------------------------------


def test_audit_full_screen_loads_events_and_filters_by_type(tmp_path):
    panel = make_panel(tmp_path)
    panel.service.register_spin(17)
    panel.service.register_spin(22)

    panel._activate("audit_full")
    assert panel.state == "audit_full"
    assert len(panel.audit_events) >= 2  # SYSTEM_STARTED/SESSION_STARTED + os 2 SPIN_CREATED

    from app.ui.admin import _AUDIT_EVENT_TYPE_FILTERS

    spin_created_index = _AUDIT_EVENT_TYPE_FILTERS.index("SPIN_CREATED")
    panel.audit_type_index = spin_created_index - 1  # posição antes de "SPIN_CREATED"
    panel._handle_audit_full(pygame.K_RIGHT)
    assert panel.audit_type_index == spin_created_index
    assert all(e["event_type"] == "SPIN_CREATED" for e in panel.audit_events)
    assert len(panel.audit_events) == 2


def test_audit_integrity_check_reports_valid(tmp_path):
    panel = make_panel(tmp_path)
    panel.service.register_spin(5)
    panel._activate("audit_integrity")
    assert panel.state == "message"
    assert "VALID" in panel.message


def test_audit_integrity_check_detects_tampering(tmp_path):
    panel = make_panel(tmp_path)
    panel.service.register_spin(5)
    panel.service.db._conn.execute("UPDATE audit_log SET new_value = 'hacked' WHERE id = 1")
    panel.service.db._conn.commit()

    panel._activate("audit_integrity")
    assert "BROKEN" in panel.message


# -- SMTP / rede (Fase 4) -------------------------------------------------------------------------


def test_smtp_host_edit_saves_to_config(tmp_path):
    panel = make_panel(tmp_path)
    panel._activate("smtp_host")
    panel.edit_buffer = "smtp.gmail.com"
    panel._save_edit()
    assert panel.config.smtp_host == "smtp.gmail.com"


def test_smtp_password_edit_saves_outside_config_yaml_not_in_memory_config(tmp_path):
    panel = make_panel(tmp_path)
    panel._activate("smtp_password")
    panel.edit_buffer = "segredo123"
    panel._save_edit()

    from app.delivery import smtp_credentials

    stored = smtp_credentials.get_smtp_password(panel.config.resolve(panel.config.smtp_credentials_path))
    assert stored == "segredo123"
    assert not hasattr(panel.config, "smtp_password")  # nunca vira campo do Config/config.yaml


def test_smtp_test_action_reports_not_configured_without_crashing(tmp_path):
    panel = make_panel(tmp_path)
    panel._activate("smtp_test")
    assert panel.state == "message"
    assert "não configurado" in panel.message.lower()


def test_network_status_action_does_not_crash_without_real_network_tools(tmp_path):
    from unittest import mock

    panel = make_panel(tmp_path)
    with mock.patch("shutil.which", return_value=None), \
         mock.patch("app.delivery.network_status._has_internet", return_value=False):
        panel._activate("network_status")
    assert panel.state == "message"
    assert "Internet:" in panel.message


# -- encerrar sessão ----------------------------------------------------------------------------


# -- analytics no admin --------------------------------------------------------------------


def test_analytics_session_action_does_not_crash_with_no_spins(tmp_path):
    panel = make_panel(tmp_path)
    panel._activate("analytics_session")
    assert panel.state == "message"
    assert "nenhum giro" in panel.message.lower()


def test_analytics_session_action_summarizes_registered_spins(tmp_path):
    panel = make_panel(tmp_path)
    for n in (17, 22, 5):
        panel.service.register_spin(n)
    panel._activate("analytics_session")
    assert "Giros: 3" in panel.message


def test_analytics_today_action_does_not_crash(tmp_path):
    panel = make_panel(tmp_path)
    panel.service.register_spin(9)
    panel._activate("analytics_today")
    assert panel.state == "message"
    assert "Giros: 1" in panel.message


def test_end_session_confirm_flow_closes_and_opens_a_new_session(tmp_path):
    panel = make_panel(tmp_path)
    panel.service.register_spin(17)
    panel.service.register_spin(34)
    old_session_code = panel.service.db.get_open_session(1)["session_code"]

    panel._activate("end_session")
    assert panel.state == "confirm"
    assert panel.pending_action == "end_session"

    panel._handle_confirm(pygame.K_RETURN)

    assert panel.state == "message"
    assert old_session_code in panel.message
    assert panel.service.db.total_spins(1) == 0  # painel zerado
    new_session = panel.service.db.get_open_session(1)
    assert new_session["session_code"] != old_session_code

    # o relatório é gerado como parte do mesmo fluxo de "Encerrar sessão"
    assert "Relatório gerado" in panel.message
    from pathlib import Path

    report_dir = panel.config.resolve(panel.config.reports_dir)
    pdf_files = list(report_dir.rglob("*.pdf"))
    json_files = list(report_dir.rglob("*.json"))
    csv_files = list(report_dir.rglob("*.csv"))
    assert len(pdf_files) == 1
    assert len(json_files) == 1
    assert len(csv_files) == 1
    assert list(report_dir.rglob("report.sha256"))
    assert list(report_dir.rglob("report.sig"))
