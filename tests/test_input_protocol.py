"""Protocolo do numpad físico de referência: 0-36 + ENTER registra, "-" desfaz, "-97" ENTER limpa
a sessão, "+" marca giro. Testes aqui simulam eventos pygame KEYDOWN reais direto contra
`RouletteDisplay._handle_keydown` — sem precisar de Xvfb (SDL_VIDEODRIVER=dummy do conftest.py já
é suficiente pro pygame.display.set_mode funcionar).

Cobre explicitamente a lista de casos-limite pedida na auditoria: 0, 1, 9, 10, 17, 36, 37, 99,
ENTER vazio, ENTER duplicado, tecla mantida, digitação rápida, BACKSPACE, "-", "-97 ENTER", "+",
NumLock, perda/recuperação de foco.
"""
from __future__ import annotations

import pygame
import pytest

from app.config import Config
from app.database.db import Database
from app.ui.display import _REVEAL_MS, RouletteDisplay


@pytest.fixture
def display(tmp_path):
    config = Config(
        fullscreen=False,
        database_path=str(tmp_path / "roulette.db"),
        assets_dir="assets",  # ativos reais do projeto (rodando a partir de roulette-display/)
        license_path=str(tmp_path / "license.dat"),
        license_state_path=str(tmp_path / ".license_state"),
    )
    db = Database(tmp_path / "roulette.db")
    db.initialize()
    d = RouletteDisplay(config, db)
    yield d
    db.close()
    pygame.quit()


def key_event(key: int, mod: int = 0, unicode: str = "") -> pygame.event.Event:
    return pygame.event.Event(pygame.KEYDOWN, key=key, mod=mod, unicode=unicode)


def type_number(display: RouletteDisplay, number: str) -> None:
    digit_keys = {str(i): getattr(pygame, f"K_{i}") for i in range(10)}
    for ch in number:
        display._handle_keydown(key_event(digit_keys[ch]))


def press_enter(display: RouletteDisplay) -> None:
    display._handle_keydown(key_event(pygame.K_RETURN))


# -- registro simples: 0, 1, 9, 10, 17, 36 (todos válidos) ----------------------------------------


@pytest.mark.parametrize("number", ["0", "1", "9", "10", "17", "36"])
def test_valid_numbers_register_correctly(display, number):
    type_number(display, number)
    press_enter(display)
    assert display.state.last_spin is not None
    assert display.state.last_spin.number == int(number)
    assert display.state.total_spins == 1


# -- entrada inválida rejeitada: 37, 99 ------------------------------------------------------------


@pytest.mark.parametrize("number", ["37", "99"])
def test_invalid_numbers_are_rejected_and_not_registered(display, number):
    type_number(display, number)
    press_enter(display)
    assert display.state.total_spins == 0
    assert display.state.last_spin is None


# -- ENTER vazio / duplicado / tecla mantida --------------------------------------------------------


def test_empty_enter_is_a_noop(display):
    press_enter(display)  # nenhum dígito digitado antes
    assert display.state.total_spins == 0


def test_double_enter_does_not_duplicate_the_spin(display):
    """17 ENTER ENTER (acidental) deve registrar um único giro — a proteção depende do estado do
    buffer (já vazio após o primeiro ENTER), não do valor do número."""
    type_number(display, "17")
    press_enter(display)
    press_enter(display)  # segundo ENTER "solto", sem novo dígito antes
    assert display.state.total_spins == 1
    assert display.state.last_spin.number == 17


def test_two_deliberate_entries_of_the_same_number_are_both_kept(display):
    """17 ENTER, depois 17 ENTER de novo (dois lançamentos reais) — a roleta pode repetir número
    consecutivo, então isso TEM que continuar funcionando (não é o mesmo caso do ENTER duplicado).
    Cada giro real só acontece depois que a revelação do giro anterior termina (pedido explícito,
    ver test_reveal_and_history.py) -- simulado avançando o relógio entre as duas entradas."""
    type_number(display, "17")
    press_enter(display)
    display.reveal_started_at = pygame.time.get_ticks() - _REVEAL_MS - 1
    type_number(display, "17")
    press_enter(display)
    assert display.state.total_spins == 2
    assert [s.number for s in display.state.history[:2]] == [17, 17]


def test_held_key_repeat_on_enter_does_not_duplicate(display):
    """Simula o SO gerando vários KEYDOWN de Return por causa de tecla física mantida pressionada
    (key repeat) — mesmo efeito de ENTER duplicado, mas disparado pelo hardware, não pelo dedo."""
    type_number(display, "5")
    for _ in range(5):  # tecla "grudada" gera 5 eventos KEYDOWN seguidos
        press_enter(display)
    assert display.state.total_spins == 1


def test_held_key_repeat_on_digit_stops_at_buffer_cap(display):
    digit_keys = {str(i): getattr(pygame, f"K_{i}") for i in range(10)}
    for _ in range(6):  # tecla "9" grudada
        display._handle_keydown(key_event(digit_keys["9"]))
    assert display.input_buffer == "99"  # trava em 2 dígitos, não vira "999999"


def test_rapid_fire_typing_registers_every_spin_in_order(display):
    """Digitação extremamente rápida = eventos processados um atrás do outro sem nenhum atraso
    real entre eles (é exatamente isso que um dispatch síncrono de eventos simula). Cada giro real
    só é registrado depois que a revelação do anterior termina (pedido explícito) -- simulado
    avançando o relógio entre entradas; o registro em si continua imediato e em ordem."""
    for number in ("17", "34", "0", "12", "22"):
        type_number(display, number)
        press_enter(display)
        display.reveal_started_at = pygame.time.get_ticks() - _REVEAL_MS - 1
    assert display.state.total_spins == 5
    assert [s.number for s in display.state.history] == [22, 12, 0, 34, 17]


# -- BACKSPACE ---------------------------------------------------------------------------------


def test_backspace_corrects_input_before_confirming(display):
    type_number(display, "1")
    type_number(display, "7")
    display._handle_keydown(key_event(pygame.K_BACKSPACE))
    assert display.input_buffer == "1"
    press_enter(display)
    assert display.state.last_spin.number == 1


# -- "-" corrige o último giro ---------------------------------------------------------------------


def test_minus_enter_undoes_last_spin(display):
    type_number(display, "17")
    press_enter(display)
    display._handle_keydown(key_event(pygame.K_MINUS))
    press_enter(display)
    assert display.state.total_spins == 0
    assert display.state.last_spin is None


# -- "-97" ENTER: limpa sessão (com confirmação em dois passos) -----------------------------------


def test_minus_97_enter_arms_confirmation_without_clearing_immediately(display):
    type_number(display, "5")
    press_enter(display)
    digit_keys = {str(i): getattr(pygame, f"K_{i}") for i in range(10)}
    display._handle_keydown(key_event(pygame.K_MINUS))
    display._handle_keydown(key_event(digit_keys["9"]))
    display._handle_keydown(key_event(digit_keys["7"]))
    press_enter(display)
    assert display.clear_all_pending is True
    assert display.state.total_spins == 1  # ainda não limpou — só armou a confirmação


def test_minus_97_enter_enter_clears_the_session(display):
    type_number(display, "5")
    press_enter(display)
    digit_keys = {str(i): getattr(pygame, f"K_{i}") for i in range(10)}
    display._handle_keydown(key_event(pygame.K_MINUS))
    display._handle_keydown(key_event(digit_keys["9"]))
    display._handle_keydown(key_event(digit_keys["7"]))
    press_enter(display)  # arma confirmação
    press_enter(display)  # confirma
    assert display.clear_all_pending is False
    assert display.state.total_spins == 0


def test_minus_97_enter_is_soft_delete_not_physical_erase(display, tmp_path):
    """O ponto crítico da auditoria: -97 ENTER não é `DELETE FROM spins`. Os dados continuam lá,
    fisicamente, disponíveis para auditoria — só o placar em tela zera."""
    type_number(display, "5")
    press_enter(display)
    digit_keys = {str(i): getattr(pygame, f"K_{i}") for i in range(10)}
    display._handle_keydown(key_event(pygame.K_MINUS))
    display._handle_keydown(key_event(digit_keys["9"]))
    display._handle_keydown(key_event(digit_keys["7"]))
    press_enter(display)
    press_enter(display)

    assert display.state.total_spins == 0  # placar visível zerado
    row = display.db._conn.execute("SELECT COUNT(*) AS c FROM spins WHERE number = 5").fetchone()
    assert row["c"] == 1  # a linha original continua fisicamente no banco
    audit = display.db.get_audit_log(display.config.roulette_id)
    assert len(audit) == 1
    assert audit[0]["original_number"] == 5
    assert audit[0]["reason"] == "session_clear"


# -- "+" marca giro (puramente visual) -----------------------------------------------------------


def test_plus_sets_awaiting_flag_and_clears_on_next_registration(display):
    display._handle_keydown(key_event(pygame.K_PLUS))
    assert display.awaiting_spin is True
    type_number(display, "8")
    press_enter(display)
    assert display.awaiting_spin is False


# -- NumLock não deve abortar um comando "-" em andamento -----------------------------------------


def test_numlock_toggle_mid_minus_command_does_not_abort_it(display):
    type_number(display, "5")
    press_enter(display)
    digit_keys = {str(i): getattr(pygame, f"K_{i}") for i in range(10)}
    display._handle_keydown(key_event(pygame.K_MINUS))
    display._handle_keydown(key_event(pygame.K_NUMLOCKCLEAR))  # toque acidental no meio do comando
    display._handle_keydown(key_event(digit_keys["9"]))
    display._handle_keydown(key_event(digit_keys["7"]))
    press_enter(display)
    assert display.clear_all_pending is True  # comando "-97" seguiu intacto


# -- perda e recuperação de foco --------------------------------------------------------------------


def test_stray_keyup_events_are_ignored(display):
    """`_handle_events` só reage a KEYDOWN — um KEYUP perdido (comum ao reganhar foco da janela,
    quando o SO reenvia o estado do teclado) não deve alterar nada."""
    type_number(display, "1")
    pygame.event.post(pygame.event.Event(pygame.KEYUP, key=pygame.K_9))
    display._handle_events()
    assert display.input_buffer == "1"  # não foi afetado pelo KEYUP solto


def test_focus_regain_event_does_not_crash_event_loop(display):
    """pygame.ACTIVEEVENT (perda/ganho de foco do SO) deve ser silenciosamente ignorado, não
    derrubar o loop principal."""
    pygame.event.post(pygame.event.Event(pygame.ACTIVEEVENT, gain=1, state=1))
    display._handle_events()  # não deve levantar exceção
    type_number(display, "7")
    press_enter(display)
    assert display.state.last_spin.number == 7
