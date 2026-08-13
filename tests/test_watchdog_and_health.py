"""Item 14/15 da auditoria: o heartbeat do watchdog systemd precisa refletir progresso real do
loop principal (não uma thread separada "viva" por conta própria), e o indicador visual de saúde
não pode ficar verde só porque o processo iniciou.

Confirmação estrutural (não só de comportamento): não existe nenhuma `threading.Thread` no app
inteiro além do `threading.Lock` do banco (ver grep no relatório da auditoria) — o heartbeat só é
enviado de dentro do próprio loop síncrono em `RouletteDisplay.run()`, então se qualquer chamada
dentro de uma iteração travar de verdade, o heartbeat simplesmente para de ser enviado e o
`WatchdogSec` do systemd mata o processo. Esse design não pode ser testado diretamente sem travar
o processo de teste também, então o que os testes abaixo verificam é a lógica de decisão em si
(extraída como função pura) e o comportamento do indicador visual.
"""
from __future__ import annotations

import pygame
import pytest

from app.config import Config
from app.database.db import Database
from app.ui.display import RouletteDisplay, _should_heartbeat


def test_should_heartbeat_pure_gate():
    assert _should_heartbeat(now=0, last=0, interval_ms=8000) is False
    assert _should_heartbeat(now=7999, last=0, interval_ms=8000) is False
    assert _should_heartbeat(now=8000, last=0, interval_ms=8000) is True
    assert _should_heartbeat(now=50000, last=0, interval_ms=8000) is True  # travou por muito tempo, ainda detecta


@pytest.fixture
def display(tmp_path):
    config = Config(fullscreen=False, database_path=str(tmp_path / "roulette.db"), assets_dir="assets")
    db = Database(tmp_path / "roulette.db")
    db.initialize()
    d = RouletteDisplay(config, db)
    yield d
    db.close()
    pygame.quit()


def test_system_ok_starts_true_after_a_real_successful_boot_read(display):
    # get_display_state() já foi chamado de verdade no __init__ (leitura real do banco) — não é
    # um "True" assumido sem nenhuma checagem.
    assert display.system_ok is True


def test_system_ok_goes_false_on_write_failure_and_recovers_on_success(display):
    display._mark_write_failed("teste")
    assert display.system_ok is False
    display._mark_write_ok()
    assert display.system_ok is True


def test_system_ok_reflects_db_health_check_independent_of_last_write(display):
    """Mesmo com a última escrita OK, uma checagem de saúde do banco que falha (ex.: SQLite parou
    de responder numa sessão sem giro novo há muito tempo) precisa derrubar o indicador."""
    display._mark_write_ok()
    assert display.system_ok is True

    display.db.close()  # simula o banco ficando inacessível
    display._check_db_health()
    assert display.system_ok is False


def test_health_check_does_not_run_on_every_frame(display):
    """A checagem de saúde só deve rodar quando explicitamente chamada (gate por tempo em
    `run()`), nunca dentro do `_render()` por frame — `_render()` sozinho não deve tocar no
    banco para decidir a cor do indicador."""
    import unittest.mock as mock

    with mock.patch.object(display.db, "is_healthy", wraps=display.db.is_healthy) as spy:
        for _ in range(30):  # 30 frames renderizados
            display._render()
        assert spy.call_count == 0
