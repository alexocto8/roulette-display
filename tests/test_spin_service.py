"""SpinService.get_display_state() é o que a tela realmente usa — a auditoria pediu confirmação
explícita de que, depois de um "-" (desfazer) ou "-97" (limpar sessão), TUDO que depende do
histórico é recalculado: contador, histórico exibido, FRIO e QUENTE. Como tudo isso é derivado do
zero (recomputado a partir do histórico não-deletado a cada chamada, sem cache), o teste é
essencialmente uma confirmação de que não existe estado obsoleto guardado em algum lugar."""
from __future__ import annotations

from app.config import Config
from app.database.db import Database
from app.services.spin_service import SpinService


def make_service(tmp_path) -> SpinService:
    config = Config(database_path=str(tmp_path / "roulette.db"), cold_numbers_count=3, hot_numbers_count=3)
    db = Database(tmp_path / "roulette.db")
    db.initialize()
    return SpinService(db, config)


def test_undo_recalculates_counter_history_hot_and_cold(tmp_path):
    service = make_service(tmp_path)
    for n in (5, 5, 12):
        service.register_spin(n)
    state_before = service.get_display_state()
    assert state_before.total_spins == 3
    assert state_before.hot[0] == (5, 2)

    service.undo_last()
    state_after = service.get_display_state()

    assert state_after.total_spins == 2
    assert [s.number for s in state_after.history] == [5, 5]
    assert state_after.hot[0] == (5, 2)  # o "12" desfeito não conta mais para QUENTE
    # FRIO: recalculado sobre o histórico ativo (sem o "12" desfeito) — 5 é o número mais recente,
    # então "giros sem sair" dele tem que ser 0 (não o valor antigo de antes do undo).
    from app.statistics import engine as stats

    since_last = stats.spins_since_last_occurrence([s.number for s in state_after.history[::-1]])
    assert since_last[5] == 0


def test_clear_session_recalculates_everything_to_empty(tmp_path):
    service = make_service(tmp_path)
    for n in (1, 2, 3):
        service.register_spin(n)
    service.clear_session()
    state = service.get_display_state()

    assert state.total_spins == 0
    assert state.history == []
    assert state.last_spin is None
    assert state.hot == []
    assert state.color.total == 0


def test_get_audit_log_includes_original_timestamp_from_the_untouched_spin_row(tmp_path):
    service = make_service(tmp_path)
    service.register_spin(9)
    service.undo_last(operator="admin")
    entries = service.get_audit_log()
    assert len(entries) == 1
    assert entries[0]["original_number"] == 9
    assert entries[0]["operator"] == "admin"
    assert entries[0]["original_timestamp"]  # preservado (soft delete, não hard delete)
    assert entries[0]["spin_id"] is not None
