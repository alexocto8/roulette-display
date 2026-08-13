"""Item 52 da especificação de identidade/analytics/relatórios: os 8 casos de teste pedidos
explicitamente, mais alguns complementares (IdentityService validação/normalização)."""
from __future__ import annotations

from app.audit.audit_service import AuditService
from app.config import Config
from app.database.db import Database
from app.identity.identity_service import IdentityService, normalize_table_code
from app.services.spin_service import SpinService


def make_db(tmp_path) -> Database:
    db = Database(tmp_path / "roulette.db")
    db.initialize()
    return db


# 1. Instalação nova gera table_id -----------------------------------------------------------


def test_fresh_installation_generates_a_table_id(tmp_path):
    db = make_db(tmp_path)
    identity = db.get_identity()
    assert identity["table_id"]
    import uuid

    uuid.UUID(identity["table_id"])  # não levanta -> é um UUID válido
    db.close()


# 2. table_id permanece após reboot (reabrir o mesmo banco) -----------------------------------


def test_table_id_survives_reopening_the_database(tmp_path):
    db = make_db(tmp_path)
    original_id = db.get_identity()["table_id"]
    db.close()

    reopened = Database(tmp_path / "roulette.db")
    reopened.initialize()
    assert reopened.get_identity()["table_id"] == original_id
    reopened.close()


# 3. table_id permanece após rename --------------------------------------------------------


def test_table_id_survives_renaming_the_table(tmp_path):
    db = make_db(tmp_path)
    identity_service = IdentityService(db)
    original_id = identity_service.get()["table_id"]

    identity_service.update(table_name="Mesa VIP")
    identity_service.update(table_name="Mesa Premium")

    assert identity_service.get()["table_id"] == original_id
    db.close()


def test_database_refuses_to_change_table_id_directly(tmp_path):
    """Segunda camada da mesma garantia: mesmo chamando `Database.update_identity` direto (sem
    passar pelo IdentityService), `table_id` não está entre os campos aceitos."""
    db = make_db(tmp_path)
    try:
        db.update_identity(table_id="algum-outro-uuid")
        assert False, "deveria ter levantado ValueError"
    except ValueError:
        pass
    db.close()


# 4. migration existente preserva dados ------------------------------------------------------


def test_migration_on_pre_existing_database_preserves_spins(tmp_path):
    import sqlite3

    db_path = tmp_path / "old.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE roulettes (id INTEGER PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE spins (
            id INTEGER PRIMARY KEY AUTOINCREMENT, roulette_id INTEGER NOT NULL,
            number INTEGER NOT NULL, color TEXT NOT NULL, timestamp TEXT NOT NULL,
            created_at TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE spin_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT, spin_id INTEGER NOT NULL,
            action TEXT NOT NULL, reason TEXT, at TEXT NOT NULL
        );
        INSERT INTO roulettes VALUES (1, 'Roleta 01', '2026-01-01T00:00:00');
        INSERT INTO spins VALUES (1, 1, 17, 'black', '2026-01-01T00:00:00', '2026-01-01T00:00:00', 0);
        INSERT INTO spins VALUES (2, 1, 5, 'red', '2026-01-01T00:05:00', '2026-01-01T00:05:00', 0);
        """
    )
    conn.commit()
    conn.close()

    db = Database(db_path)
    db.initialize()

    assert db.get_history(1) and [s.number for s in db.get_history(1)] == [17, 5]
    identity = db.get_identity()
    assert identity["table_id"]

    sessions = db.list_sessions(1)
    assert len(sessions) == 1  # sessão "legada" criada pra não deixar os giros órfãos
    assert sessions[0]["session_code"].startswith("LEGACY-")
    legacy_spins = db.get_spins_for_session(sessions[0]["id"])
    assert [s.number for s in legacy_spins] == [17, 5]
    db.close()


def test_migration_is_idempotent_on_repeated_initialize(tmp_path):
    db_path = tmp_path / "roulette.db"
    db = make_db(tmp_path)
    original_table_id = db.get_identity()["table_id"]
    db.initialize()  # chamar de novo não deve recriar nada
    db.initialize()
    assert db.get_identity()["table_id"] == original_table_id
    assert len(db.list_sessions(1)) <= 1  # sem sessões duplicadas
    db.close()


# 5. alteração de identificação gera audit ---------------------------------------------------


def test_identity_change_generates_audit_event(tmp_path):
    db = make_db(tmp_path)
    audit = AuditService(db)
    identity_service = IdentityService(db, audit=audit)

    identity_service.update(venue_name="Jackpot Poker Club", actor_id="admin")

    events = audit.get_events(event_type="IDENTITY_CHANGED")
    assert len(events) == 1
    assert "venue_name" in events[0]["old_value"]
    assert "Jackpot Poker Club" in events[0]["new_value"]
    assert events[0]["actor_id"] == "admin"
    db.close()


def test_identity_update_with_no_actual_change_does_not_log_a_spurious_event(tmp_path):
    db = make_db(tmp_path)
    audit = AuditService(db)
    identity_service = IdentityService(db, audit=audit)
    identity_service.update(venue_name="Jackpot Poker Club")

    before = len(audit.get_events(event_type="IDENTITY_CHANGED", limit=100))
    identity_service.update(venue_name="Jackpot Poker Club")  # mesmo valor
    after = len(audit.get_events(event_type="IDENTITY_CHANGED", limit=100))
    assert before == after
    db.close()


# 6. sessão captura snapshot ----------------------------------------------------------------


def test_session_captures_identity_snapshot_at_open_time(tmp_path):
    db = make_db(tmp_path)
    identity_service = IdentityService(db)
    identity_service.update(venue_name="Jackpot", table_name="Mesa 01", table_code="JP-M01")

    config = Config(database_path=str(tmp_path / "roulette.db"))
    service = SpinService(db, config)
    session = db.get_open_session(1)

    assert session["venue_name"] == "Jackpot"
    assert session["table_name"] == "Mesa 01"
    assert session["table_code"] == "JP-M01"


# 7. rename não altera sessão antiga ---------------------------------------------------------


def test_renaming_the_table_does_not_change_a_past_sessions_snapshot(tmp_path):
    db = make_db(tmp_path)
    identity_service = IdentityService(db)
    identity_service.update(table_name="Mesa 01", table_code="JP-M01")

    config = Config(database_path=str(tmp_path / "roulette.db"))
    service = SpinService(db, config)
    old_session = service.session_service.ensure_open_session()

    identity_service.update(table_name="Mesa VIP", table_code="JP-VIP")

    reloaded = db.get_session(old_session["id"])
    assert reloaded["table_name"] == "Mesa 01"  # não "Mesa VIP" — imutável após aberta
    assert reloaded["table_code"] == "JP-M01"


# 8. nova sessão utiliza novo nome ------------------------------------------------------------


def test_new_session_after_rename_uses_the_new_name(tmp_path):
    db = make_db(tmp_path)
    identity_service = IdentityService(db)
    identity_service.update(table_name="Mesa 01", table_code="JP-M01")

    config = Config(database_path=str(tmp_path / "roulette.db"))
    service = SpinService(db, config)
    service.session_service.ensure_open_session()

    identity_service.update(table_name="Mesa VIP", table_code="JP-VIP")
    result = service.end_session(operator="admin")

    assert result["opened"]["table_name"] == "Mesa VIP"
    assert result["opened"]["table_code"] == "JP-VIP"
    assert result["opened"]["session_code"].startswith("JP-VIP-")


# -- IdentityService: validação/normalização (complementar) -----------------------------------


def test_normalize_table_code_strips_disallowed_characters():
    assert normalize_table_code("JP-M01!!") == "JP-M01"
    assert normalize_table_code("  jp m01  ") == "jpm01"


def test_identity_fields_are_truncated_to_max_length(tmp_path):
    db = make_db(tmp_path)
    identity_service = IdentityService(db)
    long_name = "A" * 200
    identity_service.update(venue_name=long_name)
    assert len(identity_service.get()["venue_name"]) == 80  # MAX_LENGTHS["venue_name"]
    db.close()


def test_identity_service_rejects_unknown_field(tmp_path):
    db = make_db(tmp_path)
    identity_service = IdentityService(db)
    try:
        identity_service.update(not_a_real_field="x")
        assert False, "deveria ter levantado ValueError"
    except ValueError:
        pass
    db.close()
