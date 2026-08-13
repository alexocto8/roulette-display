"""Item 20 da auditoria: o app nunca pode cair silenciosamente pro desktop/console num erro
comum. Confirma que a tela de failsafe renderiza (sem crashar) e respeita QUIT/duração — não
testa a mensagem em si por OCR de pixel, só que o caminho de código roda de ponta a ponta."""
from __future__ import annotations

import time

import pygame

from app.config import Config
from app.ui.failsafe_screen import show_failsafe


def test_failsafe_screen_renders_and_returns_after_hold_duration():
    config = Config(fullscreen=False, hide_cursor=False)
    start = time.time()
    show_failsafe(config, "SISTEMA TEMPORARIAMENTE INDISPONÍVEL", "Tente novamente em instantes.", hold_seconds=0.2)
    elapsed = time.time() - start
    assert elapsed >= 0.15  # segurou pelo tempo pedido (com folga pro tick do clock)
    pygame.quit()


def test_failsafe_screen_exits_early_on_quit_event():
    config = Config(fullscreen=False)
    pygame.init()
    pygame.event.post(pygame.event.Event(pygame.QUIT))
    start = time.time()
    show_failsafe(config, "ERRO DO SISTEMA", "Contate o suporte técnico.", hold_seconds=30)
    elapsed = time.time() - start
    assert elapsed < 5  # não esperou os 30s inteiros — saiu no QUIT
