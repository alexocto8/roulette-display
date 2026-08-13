import pytest

from app.database.db import Database


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "test.db")
    database.initialize()
    database.ensure_roulette(1, "Roleta 01")
    yield database
    database.close()


def test_add_spin_assigns_correct_color(db):
    spin = db.add_spin(1, 32)
    assert spin.color == "red"
    spin_zero = db.add_spin(1, 0)
    assert spin_zero.color == "green"


def test_add_spin_rejects_out_of_range(db):
    with pytest.raises(ValueError):
        db.add_spin(1, 37)
    with pytest.raises(ValueError):
        db.add_spin(1, -1)


def test_history_is_chronological_oldest_first(db):
    for n in (5, 12, 0):
        db.add_spin(1, n)
    history = db.get_history(1)
    assert [s.number for s in history] == [5, 12, 0]


def test_history_limit_returns_most_recent(db):
    for n in range(10):
        db.add_spin(1, n % 37)
    history = db.get_history(1, limit=3)
    assert [s.number for s in history] == [7, 8, 9]


def test_undo_last_soft_deletes_and_is_excluded_from_history(db):
    db.add_spin(1, 5)
    db.add_spin(1, 12)
    removed = db.undo_last(1)
    assert removed.number == 12
    assert removed.deleted is True
    history = db.get_history(1)
    assert [s.number for s in history] == [5]
    assert db.total_spins(1) == 1


def test_undo_last_on_empty_history_returns_none(db):
    assert db.undo_last(1) is None


def test_undo_records_original_number_and_operator_in_audit_log(db):
    db.add_spin(1, 17)
    db.undo_last(1, operator="admin")
    entries = db.get_audit_log(1)
    assert len(entries) == 1
    assert entries[0]["original_number"] == 17
    assert entries[0]["operator"] == "admin"
    assert entries[0]["action"] == "undo"


def test_undo_operator_defaults_to_teclado(db):
    db.add_spin(1, 5)
    db.undo_last(1)
    entries = db.get_audit_log(1)
    assert entries[0]["operator"] == "teclado"


def test_clear_session_records_original_number_per_spin(db):
    db.add_spin(1, 1)
    db.add_spin(1, 2)
    db.clear_session(1, operator="admin")
    entries = db.get_audit_log(1)
    assert {e["original_number"] for e in entries} == {1, 2}
    assert all(e["operator"] == "admin" for e in entries)


def test_audit_log_migration_on_pre_existing_schema(tmp_path):
    """A database created before original_number/operator existed must migrate cleanly on the
    next open, keeping existing rows intact — not just work on brand-new databases."""
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
        """
    )
    conn.commit()
    conn.close()

    migrated = Database(db_path)
    migrated.initialize()
    columns = {row["name"] for row in migrated._conn.execute("PRAGMA table_info(spin_audit)")}
    assert {"original_number", "operator"} <= columns
    removed = migrated.undo_last(1)
    assert removed.number == 17
    migrated.close()


def test_undo_is_persisted_soft_delete_not_hard_delete(db):
    db.add_spin(1, 5)
    db.undo_last(1)
    # the row must still physically exist (soft delete), just excluded from queries
    row = db._conn.execute("SELECT COUNT(*) AS c FROM spins WHERE roulette_id = 1").fetchone()
    assert row["c"] == 1


def test_clear_session_removes_all_current_spins(db):
    for n in (1, 2, 3):
        db.add_spin(1, n)
    removed_count = db.clear_session(1)
    assert removed_count == 3
    assert db.total_spins(1) == 0
    assert db.get_history(1) == []


def test_export_csv_writes_expected_rows(db, tmp_path):
    db.add_spin(1, 5)
    db.add_spin(1, 0)
    dest = tmp_path / "export.csv"
    db.export_csv(1, dest)
    content = dest.read_text(encoding="utf-8")
    lines = content.strip().splitlines()
    assert lines[0] == "data,hora,numero,cor,mesa"
    assert len(lines) == 3


def test_backup_and_restore_roundtrip(db, tmp_path):
    db.add_spin(1, 17)
    backup_path = tmp_path / "backup.db"
    db.backup(backup_path)
    assert backup_path.exists()

    target_path = tmp_path / "restored.db"
    Database.restore(backup_path, target_path)
    restored = Database(target_path)
    restored.initialize()
    history = restored.get_history(1)
    assert [s.number for s in history] == [17]
    restored.close()


def test_wal_mode_enabled(db):
    mode = db._conn.execute("PRAGMA journal_mode;").fetchone()[0]
    assert mode.lower() == "wal"
