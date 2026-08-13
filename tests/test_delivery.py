"""Fase 4 (arquitetura de entrega): fila de e-mail persistente, template do recibo térmico, e
status de rede — tudo testado com mocks, já que este ambiente não tem servidor SMTP real,
impressora física nem hardware de rede real para validar contra. Pendências físicas documentadas
no relatório final da tarefa, não escondidas aqui."""
from __future__ import annotations

from unittest import mock

from app.analytics.analytics_service import AnalyticsService
from app.analytics import periods
from app.config import Config
from app.database.db import Database
from app.delivery import delivery_queue, email_service, network_status, printer_service, smtp_credentials
from app.services.spin_service import SpinService


def make_service(tmp_path) -> SpinService:
    config = Config(
        database_path=str(tmp_path / "roulette.db"),
        reports_dir=str(tmp_path / "reports"),
        report_signing_key_path=str(tmp_path / "key.pem"),
        smtp_credentials_path=str(tmp_path / "smtp.yaml"),
        smtp_host="smtp.example.com", smtp_username="bot@example.com",
        email_from="bot@example.com", email_to="gerente@example.com",
    )
    db = Database(tmp_path / "roulette.db")
    db.initialize()
    return SpinService(db, config)


# -- fila de entrega ---------------------------------------------------------------------------


def test_enqueue_report_creates_pending_row(tmp_path):
    service = make_service(tmp_path)
    delivery_queue.enqueue_report(service.db, "/tmp/fake_report_dir", session_id=1)
    pending = service.db.get_pending_deliveries()
    assert len(pending) == 1
    assert pending[0]["status"] == "PENDING"
    assert pending[0]["attempts"] == 0


def test_absence_of_internet_never_breaks_ending_a_session(tmp_path):
    """38. ausência de internet não quebra encerramento — testado ponta a ponta: SMTP não está
    configurado (host vazio) e o e-mail automático está ligado; encerrar a sessão não pode
    levantar exceção nem deixar de gerar/gravar o relatório."""
    config = Config(
        database_path=str(tmp_path / "roulette.db"), reports_dir=str(tmp_path / "reports"),
        report_signing_key_path=str(tmp_path / "key.pem"), email_auto_send="AO_ENCERRAR_SESSAO",
        smtp_host="",  # sem SMTP configurado -- simula "sem internet/sem servidor disponível"
    )
    db = Database(config.database_path)
    db.initialize()
    service = SpinService(db, config)
    service.register_spin(5)

    result = service.end_session(operator="admin")  # não deve levantar
    assert result["closed"]["session_code"]

    from app.reports import report_service

    written = report_service.generate_report(db, config, result["closed"], roulette_id=1)
    assert written["pdf"].exists()  # relatório nunca é perdido por falta de SMTP


def test_send_fails_gracefully_when_smtp_unreachable_and_marks_row_failed(tmp_path):
    """39. envio falho vira PENDING/FAILED."""
    service = make_service(tmp_path)
    service.register_spin(9)
    result = service.end_session(operator="admin")
    from app.reports import report_service

    written = report_service.generate_report(service.db, service.config, result["closed"], roulette_id=1)
    delivery_queue.enqueue_report(service.db, written["directory"], session_id=result["closed"]["id"])

    with mock.patch("smtplib.SMTP", side_effect=ConnectionRefusedError("recusado")):
        outcome = delivery_queue.process_pending(service.db, service.config)

    assert outcome == {"sent": 0, "failed": 1, "skipped": 0}
    deliveries = service.db.list_deliveries()
    assert deliveries[0]["status"] == "FAILED"
    assert deliveries[0]["attempts"] == 1
    assert "recusado" in deliveries[0]["last_error"]


def test_retry_succeeds_after_previous_failure(tmp_path):
    """40. retry funciona — mesma linha, segunda tentativa com SMTP "consertado" tem sucesso."""
    service = make_service(tmp_path)
    service.register_spin(9)
    result = service.end_session(operator="admin")
    from app.reports import report_service

    written = report_service.generate_report(service.db, service.config, result["closed"], roulette_id=1)
    delivery_queue.enqueue_report(service.db, written["directory"], session_id=result["closed"]["id"])

    with mock.patch("smtplib.SMTP", side_effect=OSError("indisponível")):
        delivery_queue.process_pending(service.db, service.config)
    assert service.db.list_deliveries()[0]["status"] == "FAILED"

    mock_smtp = mock.MagicMock()
    with mock.patch("smtplib.SMTP", return_value=mock_smtp):
        outcome = delivery_queue.process_pending(service.db, service.config)

    assert outcome["sent"] == 1
    row = service.db.list_deliveries()[0]
    assert row["status"] == "SENT"
    assert row["sent_at"] is not None
    assert row["attempts"] == 2
    mock_smtp.starttls.assert_called_once()
    mock_smtp.send_message.assert_called_once()


# 41. envio bem sucedido vira SENT --------------------------------------------------------------


def test_successful_send_marks_row_sent_and_attaches_files(tmp_path):
    service = make_service(tmp_path)
    service.register_spin(1)
    result = service.end_session(operator="admin")
    from app.reports import report_service

    written = report_service.generate_report(service.db, service.config, result["closed"], roulette_id=1)
    delivery_queue.enqueue_report(service.db, written["directory"], session_id=result["closed"]["id"])

    mock_smtp = mock.MagicMock()
    with mock.patch("smtplib.SMTP", return_value=mock_smtp):
        outcome = delivery_queue.process_pending(service.db, service.config)

    assert outcome["sent"] == 1
    sent_msg = mock_smtp.send_message.call_args[0][0]
    attachment_names = [part.get_filename() for part in sent_msg.iter_attachments()]
    assert any(n and n.endswith(".pdf") for n in attachment_names)
    assert any(n and n.endswith(".csv") for n in attachment_names)
    assert any(n and n.endswith(".json") for n in attachment_names)


# 42. relatório nunca é perdido por falha SMTP ---------------------------------------------------


def test_report_files_survive_smtp_failure(tmp_path):
    service = make_service(tmp_path)
    service.register_spin(1)
    result = service.end_session(operator="admin")
    from app.reports import report_service

    written = report_service.generate_report(service.db, service.config, result["closed"], roulette_id=1)
    delivery_queue.enqueue_report(service.db, written["directory"], session_id=result["closed"]["id"])

    with mock.patch("smtplib.SMTP", side_effect=OSError("falha")):
        delivery_queue.process_pending(service.db, service.config)

    assert written["pdf"].exists()
    assert written["csv"].exists()
    assert written["json"].exists()


def test_deliveries_exceeding_max_attempts_are_skipped_not_retried_forever(tmp_path):
    service = make_service(tmp_path)
    row = service.db.enqueue_delivery("/tmp/whatever", session_id=None)
    for _ in range(delivery_queue.MAX_ATTEMPTS):
        service.db.mark_delivery_sending(row["id"])
        service.db.mark_delivery_failed(row["id"], "falhou de novo")

    outcome = delivery_queue.process_pending(service.db, service.config)
    assert outcome == {"sent": 0, "failed": 0, "skipped": 1}


# -- smtp password storage -----------------------------------------------------------------------


def test_smtp_password_persists_outside_config_yaml(tmp_path):
    creds_path = tmp_path / "smtp.yaml"
    smtp_credentials.set_smtp_password(creds_path, "hunter2")
    assert smtp_credentials.get_smtp_password(creds_path) == "hunter2"
    assert creds_path.exists()
    mode = oct(creds_path.stat().st_mode)[-3:]
    assert mode == "600"


def test_smtp_password_missing_file_returns_empty_string(tmp_path):
    assert smtp_credentials.get_smtp_password(tmp_path / "nao_existe.yaml") == ""


# -- recibo de impressão (texto puro, sem hardware) ------------------------------------------------


def test_receipt_text_contains_key_fields(tmp_path):
    service = make_service(tmp_path)
    for n in (17, 22, 5):
        service.register_spin(n)
    session = service.session_service.ensure_open_session()
    spins = service.db.get_report_spins_for_session(session["id"])
    snapshot = AnalyticsService(service.db).build_snapshot_from_spins(
        periods.SESSAO_ATUAL, spins, session_id=session["id"], hot_n=3, cold_n=3,
    )
    text = printer_service.build_receipt_text(service.identity.get(), session, snapshot)
    assert session["table_name"].upper() in text
    assert session["session_code"] in text
    assert "GIROS" in text
    assert len(text.splitlines()[0]) <= 40  # cabe numa linha de impressora térmica


def test_print_receipt_without_printer_configured_returns_false_and_never_raises(tmp_path):
    service = make_service(tmp_path)
    assert service.config.printer_type == "NONE"
    assert printer_service.print_receipt(service.config, "qualquer texto") is False


def test_print_receipt_missing_escpos_library_degrades_gracefully(tmp_path):
    service = make_service(tmp_path)
    service.config.printer_type = "ESCPOS_NETWORK"
    service.config.printer_address = "192.168.1.50:9100"
    with mock.patch.dict("sys.modules", {"escpos": None, "escpos.printer": None}):
        assert printer_service.print_receipt(service.config, "texto") is False


# -- status de rede (mockado -- sem hardware real) -------------------------------------------------


def test_network_status_degrades_gracefully_without_nmcli_or_ip():
    with mock.patch("shutil.which", return_value=None), \
         mock.patch("app.delivery.network_status._has_internet", return_value=False):
        status = network_status.get_network_status()
    assert status["ethernet"] == "desconhecido"
    assert status["internet"] == "Indisponível"


def test_network_status_never_raises_even_if_subprocess_times_out():
    import subprocess

    with mock.patch("shutil.which", return_value="/usr/bin/nmcli"), \
         mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="nmcli", timeout=3)):
        status = network_status.get_network_status()  # não deve levantar
    assert status["ethernet"] == "desconhecido"
