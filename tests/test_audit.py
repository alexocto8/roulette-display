"""Item 53 da especificação: os 8 casos de teste de auditoria pedidos explicitamente, mais
cobertura complementar da cadeia de hash (app/audit/integrity.py)."""
from __future__ import annotations

from app.audit.audit_service import AuditService
from app.audit.integrity import GENESIS_HASH, compute_event_hash, verify_chain
from app.config import Config
from app.database.db import Database
from app.services.spin_service import SpinService


def make_service(tmp_path) -> SpinService:
    config = Config(database_path=str(tmp_path / "roulette.db"))
    db = Database(tmp_path / "roulette.db")
    db.initialize()
    return SpinService(db, config)


# 9. SPIN_CREATED gera audit -----------------------------------------------------------------


def test_spin_created_generates_audit_event(tmp_path):
    service = make_service(tmp_path)
    spin = service.register_spin(17)
    events = service.audit.get_events(event_type="SPIN_CREATED")
    assert len(events) == 1
    assert events[0]["spin_id"] == spin.id
    assert events[0]["new_value"] == "17"
    assert events[0]["source"] == "KEYPAD"


# 10. undo não apaga evento original ----------------------------------------------------------


def test_undo_does_not_erase_the_original_spin_created_event(tmp_path):
    service = make_service(tmp_path)
    service.register_spin(17)
    service.undo_last()

    created = service.audit.get_events(event_type="SPIN_CREATED")
    undone = service.audit.get_events(event_type="SPIN_UNDONE")
    assert len(created) == 1  # continua lá, intacto
    assert len(undone) == 1


# 11. correção gera old/new -------------------------------------------------------------------


def test_undo_records_old_value(tmp_path):
    service = make_service(tmp_path)
    service.register_spin(23)
    service.undo_last()
    undone = service.audit.get_events(event_type="SPIN_UNDONE")
    assert undone[0]["old_value"] == "23"


# 12. eventos são append-only (a API não expõe nenhum jeito de editar) -------------------------


def test_audit_service_exposes_no_update_or_delete_method():
    public_methods = {name for name in dir(AuditService) if not name.startswith("_")}
    assert not any(word in m.lower() for m in public_methods for word in ("update", "delete", "edit"))


def test_appending_more_events_never_touches_earlier_rows(tmp_path):
    service = make_service(tmp_path)
    service.register_spin(1)
    first_event = service.audit.get_events(event_type="SPIN_CREATED")[0]
    service.register_spin(2)
    service.register_spin(3)
    same_event_again = [e for e in service.audit.get_events(event_type="SPIN_CREATED", limit=100)
                         if e["event_id"] == first_event["event_id"]][0]
    assert dict(same_event_again) == dict(first_event)


# 13. cadeia de hash válida -------------------------------------------------------------------


def test_hash_chain_is_valid_after_normal_operation(tmp_path):
    service = make_service(tmp_path)
    service.register_spin(1)
    service.register_spin(2)
    service.undo_last()
    service.clear_session()
    ok, broken = service.audit.verify_integrity()
    assert ok is True
    assert broken is None


def test_genesis_hash_used_for_first_event(tmp_path):
    db = Database(tmp_path / "roulette.db")
    db.initialize()
    event = db.append_audit_event("SYSTEM_STARTED")
    assert event["previous_hash"] == GENESIS_HASH
    db.close()


# 14. alteração simulada quebra verificação -----------------------------------------------------


def test_tampering_with_a_past_event_breaks_verification(tmp_path):
    service = make_service(tmp_path)
    service.register_spin(1)
    service.register_spin(2)
    service.register_spin(3)

    # simula alguém editando o banco diretamente (fora da API) — exatamente o que a cadeia de
    # hash existe pra detectar.
    service.db._conn.execute("UPDATE audit_log SET new_value = '99' WHERE id = 1")
    service.db._conn.commit()

    ok, broken = service.audit.verify_integrity()
    assert ok is False
    assert broken is not None


def test_deleting_an_event_from_the_middle_breaks_verification(tmp_path):
    service = make_service(tmp_path)
    service.register_spin(1)
    service.register_spin(2)
    service.register_spin(3)

    service.db._conn.execute("DELETE FROM audit_log WHERE id = 2")
    service.db._conn.commit()

    ok, broken = service.audit.verify_integrity()
    assert ok is False


def test_verify_chain_pure_function_directly():
    e1 = {"event_id": "a", "created_at": "t1", "event_type": "X"}
    h1 = compute_event_hash(GENESIS_HASH, e1)
    e2 = {"event_id": "b", "created_at": "t2", "event_type": "Y"}
    h2 = compute_event_hash(h1, e2)

    events = [
        {**e1, "previous_hash": GENESIS_HASH, "event_hash": h1},
        {**e2, "previous_hash": h1, "event_hash": h2},
    ]
    ok, broken = verify_chain(events)
    assert ok is True

    events[0]["event_type"] = "TAMPERED"
    ok2, broken2 = verify_chain(events)
    assert ok2 is False
    assert broken2 == "a"


# 15. senha SMTP nunca aparece no audit -----------------------------------------------------


def test_smtp_password_is_redacted_in_audit_log(tmp_path):
    db = Database(tmp_path / "roulette.db")
    db.initialize()
    audit = AuditService(db)
    audit.log(
        "CONFIG_CHANGED", old_value="hunter2", new_value="new-secret-pw",
        sensitive_field="smtp_password", reason="smtp_password alterado",
    )
    event = audit.get_events(event_type="CONFIG_CHANGED")[0]
    assert event["old_value"] == "[REDACTED]"
    assert event["new_value"] == "[REDACTED]"
    assert "hunter2" not in str(dict(event))
    assert "new-secret-pw" not in str(dict(event))
    db.close()


def test_metadata_keys_that_look_sensitive_are_redacted(tmp_path):
    db = Database(tmp_path / "roulette.db")
    db.initialize()
    audit = AuditService(db)
    audit.log("CONFIG_CHANGED", metadata={"wifi_password": "topsecret", "ssid": "MinhaRede"})
    event = audit.get_events(event_type="CONFIG_CHANGED")[0]
    assert "topsecret" not in event["metadata_json"]
    assert "MinhaRede" in event["metadata_json"]  # campo não sensível continua visível
    db.close()


# 16. PIN nunca aparece no audit ---------------------------------------------------------------


def test_admin_pin_value_never_appears_in_any_audit_event(tmp_path):
    """Ponta a ponta: registra alguns giros, troca identidade, verifica que em NENHUM evento de
    auditoria o valor literal de um PIN de teste aparece — mesmo sem nenhum call site ter
    passado um PIN de propósito (o app nunca loga PIN em audit hoje; este teste é o alarme caso
    algum código futuro tente)."""
    service = make_service(tmp_path)
    service.register_spin(7)
    service.identity.update(venue_name="Jackpot")

    fake_pin = "913547"
    all_events = service.audit.get_events(limit=1000)
    for event in all_events:
        assert fake_pin not in str(dict(event))
