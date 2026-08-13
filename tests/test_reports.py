"""Item 55 da especificação: os 9 casos de teste de relatórios pedidos, mais cobertura
complementar de assinatura/integridade. A revisão visual real do PDF (item 57 — gerar um exemplo
e olhar de verdade, não só confiar em "não deu exception") foi feita manualmente nesta sessão via
pdftoppm + inspeção das páginas geradas; achados dessa revisão (rodapé bilíngue por engano, texto
"MESAMESA01" duplicado — este numa fase anterior) já foram corrigidos no código."""
from __future__ import annotations

import json

from PIL import Image

from app.analytics.analytics_service import AnalyticsService
from app.analytics import periods
from app.config import Config
from app.database.db import Database
from app.reports import filenames, json_export, pdf_report, report_service, session_csv, signing
from app.services.spin_service import SpinService


def make_service(tmp_path) -> SpinService:
    config = Config(
        database_path=str(tmp_path / "roulette.db"),
        branding_dir=str(tmp_path / "branding"),
        reports_dir=str(tmp_path / "reports"),
        report_signing_key_path=str(tmp_path / "key.pem"),
    )
    db = Database(tmp_path / "roulette.db")
    db.initialize()
    return SpinService(db, config)


# 29. PDF é gerado ---------------------------------------------------------------------------


def test_pdf_is_generated(tmp_path):
    service = make_service(tmp_path)
    for n in (17, 22, 5, 0, 34):
        service.register_spin(n)
    result = service.end_session(operator="admin")

    written = report_service.generate_report(service.db, service.config, result["closed"], roulette_id=1)
    assert written["pdf"].exists()
    assert written["pdf"].stat().st_size > 500  # não é um arquivo vazio/quebrado


# 30. PDF funciona sem logo ------------------------------------------------------------------


def test_pdf_works_without_a_logo(tmp_path):
    service = make_service(tmp_path)
    service.register_spin(7)
    result = service.end_session(operator="admin")
    # identity.venue_logo_path continua "" (nunca configurado)
    written = report_service.generate_report(service.db, service.config, result["closed"], roulette_id=1)
    assert written["pdf"].exists()


# 31. PDF funciona com logo -------------------------------------------------------------------


def test_pdf_works_with_a_valid_logo(tmp_path):
    from app.reports import branding

    service = make_service(tmp_path)
    source = tmp_path / "source_logo.png"
    Image.new("RGB", (400, 200), (10, 20, 200)).save(source)
    dest = branding.validate_and_store(source, service.config.resolve(service.config.branding_dir))
    service.identity.update(venue_logo_path=str(dest))

    service.register_spin(9)
    result = service.end_session(operator="admin")
    written = report_service.generate_report(service.db, service.config, result["closed"], roulette_id=1)
    assert written["pdf"].exists()
    assert written["pdf"].stat().st_size > 500


# 32. logo inválido não quebra geração ---------------------------------------------------------


def test_invalid_logo_path_does_not_break_pdf_generation(tmp_path):
    service = make_service(tmp_path)
    # aponta pra um path que nunca foi validado por branding.validate_and_store (simula um
    # registro de banco apontando pra um arquivo que sumiu/corrompeu depois)
    service.db.update_identity(venue_logo_path=str(tmp_path / "nao_existe.png"))
    service.register_spin(3)
    result = service.end_session(operator="admin")
    written = report_service.generate_report(service.db, service.config, result["closed"], roulette_id=1)
    assert written["pdf"].exists()  # não quebrou -- caiu pro fallback texto-only


# 33. CSV contém identificação -----------------------------------------------------------------


def test_csv_contains_identification_fields(tmp_path):
    service = make_service(tmp_path)
    service.identity.update(venue_name="Jackpot", table_code="JP-M01", table_name="Mesa 01")
    # sessão já foi aberta no __init__ do SpinService, com o snapshot antigo (vazio) — precisa de
    # uma nova sessão pra refletir a identidade recém-configurada (mesmo comportamento coberto em
    # test_report_reflects_the_identity_snapshot_frozen_at_session_open_not_current).
    service.session_service.close_current_session(actor_type="admin")
    service.register_spin(11)
    result = service.end_session(operator="admin")
    written = report_service.generate_report(service.db, service.config, result["closed"], roulette_id=1)

    content = written["csv"].read_text(encoding="utf-8")
    assert "Jackpot" in content
    assert "JP-M01" in content
    assert result["closed"]["table_id"] in content
    assert result["closed"]["session_code"] in content


# 34. JSON contém identificação ------------------------------------------------------------------


def test_json_contains_identification_fields(tmp_path):
    service = make_service(tmp_path)
    service.identity.update(venue_name="Jackpot", table_code="JP-M01")
    service.session_service.close_current_session(actor_type="admin")
    service.register_spin(11)
    result = service.end_session(operator="admin")
    written = report_service.generate_report(service.db, service.config, result["closed"], roulette_id=1)

    data = json.loads(written["json"].read_text(encoding="utf-8"))
    assert data["venue"]["name"] == "Jackpot"
    assert data["table"]["code"] == "JP-M01"
    assert data["table"]["id"] == result["closed"]["table_id"]
    assert data["session"]["id"] == result["closed"]["session_code"]
    assert len(data["spins"]) == 1


# 35. nome do arquivo é sanitizado --------------------------------------------------------------


def test_filename_sanitization():
    assert filenames.sanitize_filename("Jackpot Poker Club!!") == "Jackpot_Poker_Club"
    assert filenames.sanitize_filename("Mesa/01\\VIP") == "Mesa_01_VIP"
    assert filenames.sanitize_filename("") == "arquivo"
    assert filenames.sanitize_filename("!!!@@@") == "arquivo"  # nada sobra -> cai no fallback


def test_report_basename_uses_sanitized_fields(tmp_path):
    service = make_service(tmp_path)
    service.identity.update(venue_name="Jackpot / Poker!", table_name="Mesa VIP #1")
    service.session_service.close_current_session(actor_type="admin")
    service.register_spin(1)
    result = service.end_session(operator="admin")
    basename = filenames.report_basename(result["closed"])
    assert "/" not in basename
    assert "!" not in basename
    assert "#" not in basename
    assert basename.startswith("Jackpot_Poker_Mesa_VIP_1_")


# 36. snapshot histórico permanece correto -------------------------------------------------------


def test_report_reflects_the_identity_snapshot_frozen_at_session_open_not_current(tmp_path):
    service = make_service(tmp_path)
    service.identity.update(table_name="Mesa 01", table_code="JP-M01")
    service.session_service.close_current_session(actor_type="admin")  # abre sessão já c/ esse nome
    service.register_spin(5)

    service.identity.update(table_name="Mesa VIP", table_code="JP-VIP")  # rename DEPOIS do giro

    result = service.end_session(operator="admin")
    written = report_service.generate_report(service.db, service.config, result["closed"], roulette_id=1)

    data = json.loads(written["json"].read_text(encoding="utf-8"))
    assert data["table"]["name"] == "Mesa 01"  # não "Mesa VIP"
    assert data["table"]["code"] == "JP-M01"


# 37. relatório utiliza dados daquela sessão e não identidade atual -------------------------------


def test_report_only_includes_spins_from_its_own_session(tmp_path):
    service = make_service(tmp_path)
    service.register_spin(1)
    service.register_spin(2)
    first_close = service.end_session(operator="admin")  # sessão 1: giros 1, 2

    service.register_spin(3)
    service.register_spin(4)
    service.register_spin(5)
    second_close = service.end_session(operator="admin")  # sessão 2: giros 3, 4, 5

    written1 = report_service.generate_report(service.db, service.config, first_close["closed"], roulette_id=1)
    written2 = report_service.generate_report(service.db, service.config, second_close["closed"], roulette_id=1)

    data1 = json.loads(written1["json"].read_text(encoding="utf-8"))
    data2 = json.loads(written2["json"].read_text(encoding="utf-8"))
    assert [s["number"] for s in data1["spins"]] == [1, 2]
    assert [s["number"] for s in data2["spins"]] == [3, 4, 5]


# -- assinatura/integridade (complementar) -------------------------------------------------------


def test_report_signature_verifies_and_detects_tampering(tmp_path):
    service = make_service(tmp_path)
    service.register_spin(17)
    result = service.end_session(operator="admin")
    written = report_service.generate_report(service.db, service.config, result["closed"], roulette_id=1)

    session_dict = json.loads(written["json"].read_text(encoding="utf-8"))
    canonical = json_export.canonical_bytes(session_dict)
    sha_stored = written["sha256_path"].read_text().strip()
    sig_bytes = bytes.fromhex(written["sig_path"].read_text().strip())

    import hashlib

    assert hashlib.sha256(canonical).hexdigest() == sha_stored

    pub_hex = signing.public_key_hex(service.config.resolve(service.config.report_signing_key_path))
    assert signing.verify_signature(pub_hex, canonical, sig_bytes) is True
    assert signing.verify_signature(pub_hex, canonical.replace(b"17", b"99", 1), sig_bytes) is False


def test_report_signing_key_is_separate_from_license_key(tmp_path):
    """Nunca deve existir sobreposição entre a chave de assinatura de relatórios e a de
    licenciamento — reaproveitar a mesma chave exigiria colocar a chave privada de licença no
    Pi, exatamente o que a arquitetura de licenciamento existe pra evitar."""
    from app.license.public_key import PUBLIC_KEY_HEX as license_public_key_hex

    service = make_service(tmp_path)
    report_public_key_hex = signing.public_key_hex(
        service.config.resolve(service.config.report_signing_key_path)
    )
    assert report_public_key_hex != license_public_key_hex


def test_report_generation_logs_audit_event(tmp_path):
    service = make_service(tmp_path)
    service.register_spin(1)
    result = service.end_session(operator="admin")
    report_service.generate_report(service.db, service.config, result["closed"], roulette_id=1)

    events = service.audit.get_events(event_type="REPORT_GENERATED")
    assert len(events) == 1
    assert events[0]["session_id"] == result["closed"]["id"]
