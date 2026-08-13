"""Revelação em tela cheia pós-giro (fundo verde-gramado, círculo colorido, classificação, 5s,
bloqueia o registro de um número novo enquanto está em tela) e histórico em duas colunas na
coluna central (preto/vermelho, zero centralizado) — pedido do usuário para complementar o
placar principal."""
from __future__ import annotations

from unittest import mock

import pygame
import pytest

from app.config import Config
from app.database.db import Database
from app.ui.display import (
    _BOTTOM_BAR_PX,
    _FELT_GREEN,
    _LIMITS_FRACTION,
    _REVEAL_MS,
    RouletteDisplay,
)
from app.ui.theme import BG, BLACK, COLOR_MAP, CYAN, GREEN, RED, TEXT_PRIMARY


@pytest.fixture
def display(tmp_path):
    config = Config(
        fullscreen=False,
        database_path=str(tmp_path / "roulette.db"),
        assets_dir="assets",
        license_path=str(tmp_path / "license.dat"),
        license_state_path=str(tmp_path / ".license_state"),
    )
    db = Database(tmp_path / "roulette.db")
    db.initialize()
    d = RouletteDisplay(config, db)
    yield d
    db.close()
    pygame.quit()


def key_event(key: int) -> pygame.event.Event:
    return pygame.event.Event(pygame.KEYDOWN, key=key, mod=0, unicode="")


def type_number(display: RouletteDisplay, number: str) -> None:
    digit_keys = {str(i): getattr(pygame, f"K_{i}") for i in range(10)}
    for ch in number:
        display._handle_keydown(key_event(digit_keys[ch]))


def press_enter(display: RouletteDisplay) -> None:
    display._handle_keydown(key_event(pygame.K_RETURN))


def register(display: RouletteDisplay, number: int) -> None:
    """Sempre garante que uma revelação anterior (se houver) já tenha "acabado" antes de registrar
    -- simula o ritmo real de uso (giros de roleta ficam minutos, não milissegundos, um do outro;
    ver o bloqueio explícito em `_confirm_input`), sem forçar cada chamador deste helper a lidar
    com isso manualmente."""
    if display._reveal_active(pygame.time.get_ticks()):
        display.reveal_started_at = pygame.time.get_ticks() - _REVEAL_MS - 1
    type_number(display, str(number))
    press_enter(display)


def _column_rects(display: RouletteDisplay) -> tuple[pygame.Rect, pygame.Rect, pygame.Rect]:
    """Reconstrói os três retângulos (FRIO / central / QUENTE) exatamente como `_draw_columns`
    faz, pra chamar `_draw_center_number`/`_draw_badge_column` isoladamente nos testes -- sem
    depender de `_render()` (que desenharia as três colunas juntas e poluiria a lista de chamadas
    capturada pelo spy, já que os badges também usam `_blit_outlined_text` agora)."""
    theme = display.theme
    limits_h = int(theme.height * _LIMITS_FRACTION)
    bottom_h = theme.px(_BOTTOM_BAR_PX)
    columns_h = theme.height - limits_h - bottom_h
    col_w = theme.width // 3
    frio_rect = pygame.Rect(0, limits_h, col_w, columns_h)
    center_rect = pygame.Rect(col_w, limits_h, col_w, columns_h)
    quente_rect = pygame.Rect(col_w * 2, limits_h, theme.width - col_w * 2, columns_h)
    return frio_rect, center_rect, quente_rect


def _center_rect(display: RouletteDisplay) -> pygame.Rect:
    return _column_rects(display)[1]


# -- classificação (_reveal_tags) ---------------------------------------------------------------


def test_reveal_tags_for_zero_is_only_zero():
    tags = RouletteDisplay._reveal_tags(0, "green")
    assert [t[0] for t in tags] == ["ZERO"]


def test_reveal_tags_for_red_odd_low_number():
    tags = RouletteDisplay._reveal_tags(1, "red")  # 1: vermelho, ímpar, 1-18
    assert [t[0] for t in tags] == ["VERMELHO", "ÍMPAR", "MENOR"]


def test_reveal_tags_for_black_even_low_number():
    tags = RouletteDisplay._reveal_tags(2, "black")  # 2: preto, par, 1-18
    assert [t[0] for t in tags] == ["PRETO", "PAR", "MENOR"]


def test_reveal_tags_for_red_even_high_number():
    tags = RouletteDisplay._reveal_tags(36, "red")  # 36: vermelho, par, 19-36
    assert [t[0] for t in tags] == ["VERMELHO", "PAR", "MAIOR"]


# -- estado da revelação (timer, bloqueia registro de número novo) ----------------------------


def test_registering_a_spin_starts_the_reveal(display):
    assert display.reveal_number is None
    register(display, 17)
    assert display.reveal_number == 17
    assert display.reveal_color == "black"  # 17 não está em RED_NUMBERS
    now = pygame.time.get_ticks()
    assert display._reveal_active(now) is True


def test_reveal_expires_after_its_duration(display):
    register(display, 5)
    started = display.reveal_started_at
    assert display._reveal_active(started + _REVEAL_MS - 1) is True
    assert display._reveal_active(started + _REVEAL_MS + 1) is False


def test_a_new_spin_is_blocked_while_the_reveal_is_showing(display):
    """Pedido explícito: o sistema não deve permitir registrar um número novo enquanto o anterior
    ainda está na tela de revelação (5s) -- reverte o comportamento anterior desta mesma
    funcionalidade ("puramente visual, nunca bloqueia"). Os dígitos digitados ficam preservados;
    basta apertar ENTER de novo quando a revelação acabar."""
    register(display, 5)
    first_started = display.reveal_started_at

    type_number(display, "22")
    press_enter(display)  # ainda "dentro" dos 5s da revelação anterior -- deve ser ignorado

    assert display.reveal_number == 5  # não trocou
    assert display.reveal_started_at == first_started  # não reiniciou
    assert display.service.db.total_spins(display.config.roulette_id) == 1  # não registrou
    assert display.input_buffer == "22"  # dígitos preservados, não perdidos

    # passada a janela da revelação, o mesmo ENTER (é só apertar de novo) registra normalmente.
    display.reveal_started_at = pygame.time.get_ticks() - _REVEAL_MS - 1
    press_enter(display)
    assert display.reveal_number == 22
    assert display.service.db.total_spins(display.config.roulette_id) == 2


def test_undo_still_works_while_the_reveal_is_showing(display):
    register(display, 8)
    display._handle_keydown(key_event(pygame.K_MINUS))
    display._handle_keydown(key_event(pygame.K_RETURN))  # "-" ENTER desfaz na hora
    assert display.service.db.total_spins(display.config.roulette_id) == 0


# -- render da revelação em tela cheia ----------------------------------------------------------


def test_render_during_reveal_does_not_crash_and_fills_felt_green(display):
    register(display, 5)  # 5: vermelho
    display._render()
    screen = display.screen
    corner = screen.get_at((2, 2))[:3]
    assert tuple(corner) == _FELT_GREEN


def _reveal_circle_edge_pixel(screen, radius_ratio=0.34):
    """Ponto perto da BORDA do círculo (não o centro exato, onde o dígito com contorno é
    desenhado por cima e mascararia a cor do preenchimento) — círculo é sempre centralizado na
    tela agora (horizontal e vertical), independente da cor do número."""
    cx, cy = screen.get_width() // 2, screen.get_height() // 2
    offset = int(min(screen.get_width(), screen.get_height()) * radius_ratio * 0.85)
    return (cx, cy - offset)


def test_render_during_reveal_draws_the_circle_in_the_numbers_color(display):
    register(display, 5)  # vermelho
    display._render()
    screen = display.screen
    assert tuple(screen.get_at(_reveal_circle_edge_pixel(screen))[:3]) == COLOR_MAP["red"]


def test_black_reveal_is_also_centered(display):
    register(display, 17)  # preto
    display._render()
    screen = display.screen
    assert tuple(screen.get_at(_reveal_circle_edge_pixel(screen))[:3]) == COLOR_MAP["black"]


def test_zero_reveal_is_centered_with_green_circle(display):
    register(display, 0)
    display._render()
    screen = display.screen
    assert tuple(screen.get_at(_reveal_circle_edge_pixel(screen))[:3]) == COLOR_MAP["green"]


def test_render_returns_to_normal_display_after_reveal_expires(display):
    register(display, 5)
    display.reveal_started_at = pygame.time.get_ticks() - _REVEAL_MS - 1  # força expirar
    display._render()
    screen = display.screen
    assert tuple(screen.get_at((2, 2))[:3]) != _FELT_GREEN


# -- número grande ("ÚLTIMO RESULTADO") com contorno -----------------------------------------------


def test_last_number_is_drawn_with_outline_twice_as_thick_as_history_rows(display):
    register(display, 5)
    display.reveal_number = None  # placar normal, não a revelação

    calls = []

    def spy(surface, font, text, center, fill, outline, outline_px=2):
        calls.append((text, outline, outline_px))

    with mock.patch("app.ui.display._blit_outlined_text", side_effect=spy):
        display._draw_center_number(_center_rect(display))

    last_number_call = calls[0]
    history_call = calls[1]
    assert last_number_call[0] == "5"
    assert last_number_call[1] == TEXT_PRIMARY  # contorno sempre branco (pedido explícito)
    assert last_number_call[2] == history_call[2] * 2  # dobro da grossura do histórico


def test_black_last_number_fills_black_not_white(display):
    """Pedido explícito: número preto continua preto (com borda branca), não branco -- antes
    disso, NUMBER_COLOR_MAP fazia "preto" virar branco (só ilegível sem essa troca porque não
    havia contorno; agora que há contorno branco, o preenchimento pode voltar a ser preto de
    verdade)."""
    register(display, 17)  # 17 não está em RED_NUMBERS -> preto
    display.reveal_number = None

    calls = []

    def spy(surface, font, text, center, fill, outline, outline_px=2):
        calls.append((text, fill))

    with mock.patch("app.ui.display._blit_outlined_text", side_effect=spy):
        display._draw_center_number(_center_rect(display))

    assert calls[0] == ("17", BLACK)


def test_reveal_circle_for_black_uses_gray_85_not_pure_black():
    """Pedido explícito: círculos/fundos que representam "preto" usam um cinza 85% (38,38,38),
    não BLACK puro (25,25,28) -- as duas cores são próximas mas distintas de propósito."""
    from app.ui.theme import GRAY_85

    assert COLOR_MAP["black"] == GRAY_85
    assert COLOR_MAP["black"] != BLACK


# -- histórico em três raias -------------------------------------------------------------------


def test_center_history_does_not_crash_with_no_spins(display):
    display._render()  # não deve levantar mesmo sem nenhum giro registrado ainda


def test_history_rows_are_newest_first_and_use_one_lane_per_row(display):
    """Pedido explícito do usuário: o mais recente sempre no topo, e a linha de cada giro só
    preenche a raia da sua própria cor — as outras duas ficam em branco naquela linha (não é mais
    três colunas independentes por cor). Espiona `_blit_outlined_text` em vez de ler pixel a
    pixel: como a revelação está desligada neste teste, as chamadas capturadas vêm do número
    grande (agora também com contorno) seguido das linhas do histórico, uma por linha, na ordem em
    que são desenhadas."""
    for n in (5, 17, 0, 22):  # vermelho, preto, verde, preto -- registrados nesta ordem
        register(display, n)
    display.reveal_number = None  # quero ver o placar normal, não a revelação em tela cheia

    calls = []

    def spy(surface, font, text, center, fill, outline, outline_px=2):
        calls.append((text, center[0], fill))

    with mock.patch("app.ui.display._blit_outlined_text", side_effect=spy):
        display._draw_center_number(_center_rect(display))

    # primeira chamada é o número grande ("ÚLTIMO RESULTADO", agora com contorno também);
    # depois o histórico, mais recente primeiro: 22(preto), 0(verde), 17(preto), 5(vermelho).
    assert calls[0][0] == "22"
    history_calls = calls[1:]
    assert [c[0] for c in history_calls] == ["22", "0", "17", "5"]
    assert history_calls[0][2] == BLACK
    assert history_calls[1][2] == GREEN
    assert history_calls[2][2] == BLACK
    assert history_calls[3][2] == RED

    black_x = {c[1] for c in history_calls if c[2] == BLACK}
    red_x = {c[1] for c in history_calls if c[2] == RED}
    green_x = {c[1] for c in history_calls if c[2] == GREEN}
    assert len(black_x) == 1 and len(red_x) == 1 and len(green_x) == 1  # cada cor sempre na mesma raia
    (bx,), (rx,), (gx,) = black_x, red_x, green_x
    assert bx < gx < rx  # preto à esquerda, zero no meio, vermelho à direita


# -- badges FRIO/QUENTE: número branco com borda preta + linha dourada/prateada -----------------


def test_trapezoid_draws_accent_line_top_and_bottom_when_given(display):
    from app.ui.theme import GOLD

    rect = pygame.Rect(40, 40, 120, 60)
    display._draw_trapezoid(rect, RED, accent_line=GOLD)
    screen = display.screen
    assert tuple(screen.get_at((rect.centerx, rect.top))[:3]) == GOLD
    assert tuple(screen.get_at((rect.centerx, rect.bottom))[:3]) == GOLD


def test_trapezoid_without_accent_line_has_no_gold_or_silver_edge(display):
    from app.ui.theme import GOLD, SILVER

    rect = pygame.Rect(40, 40, 120, 60)
    display._draw_trapezoid(rect, RED)  # accent_line=None (padrão)
    screen = display.screen
    top_pixel = tuple(screen.get_at((rect.centerx, rect.top))[:3])
    assert top_pixel == RED
    assert top_pixel not in (GOLD, SILVER)


def test_badge_column_frio_uses_silver_accent_and_quente_uses_gold(display):
    """Pedido explícito: linha dourada no topo/base dos badges QUENTE (vermelhos), prateada nos
    FRIO (ciano) -- escaneia verticalmente pelo centro de cada coluna procurando a cor."""
    from app.ui.theme import GOLD, SILVER

    for n in (1, 2, 3, 4, 5):
        register(display, n)
    display.reveal_number = None
    display._render()

    screen = display.screen
    frio_rect, _, quente_rect = _column_rects(display)

    def find_accent_color(rect: pygame.Rect):
        for y in range(rect.top, rect.bottom):
            px = tuple(screen.get_at((rect.centerx, y))[:3])
            if px in (GOLD, SILVER):
                return px
        return None

    assert find_accent_color(frio_rect) == SILVER
    assert find_accent_color(quente_rect) == GOLD


def test_badge_numbers_are_drawn_white_with_black_outline(display):
    """Pedido explícito: números dentro dos badges FRIO/QUENTE maiores, brancos com borda preta --
    antes disso usavam `TEXT_ON_ACCENT` (texto escuro sólido, sem contorno)."""
    for n in (1, 2, 3):
        register(display, n)
    display.reveal_number = None

    calls = []

    def spy(surface, font, text, center, fill, outline, outline_px=2):
        calls.append((text, fill, outline))

    frio_rect, _, _ = _column_rects(display)
    with mock.patch("app.ui.display._blit_outlined_text", side_effect=spy):
        display._draw_badge_column(frio_rect, "FRIO", "GIROS SEM SAIR", CYAN, display.state.cold,
                                    display.config.cold_numbers_count, unit="GIROS")

    assert len(calls) >= 1
    for _, fill, outline in calls:
        assert fill == TEXT_PRIMARY
        assert outline == BLACK


def test_center_history_does_not_crash_with_many_spins_of_mixed_colors(display):
    for n in (1, 2, 0, 36, 17, 5, 0, 22, 4, 9, 0, 11, 6, 3, 8):
        register(display, n)
        display.reveal_number = None  # pula a revelação pra exercitar o placar normal a cada giro
    display._render()  # não deve levantar mesmo truncando o histórico pro que cabe na coluna


# -- sombras suaves + brilho no número recém-trocado -- pedido explícito: "as melhorias visuais
# devem ser aplicadas em todas as telas, para tornar a imersão mais real e sofisticada" -----------


def test_trapezoid_draws_a_soft_shadow_offset_from_the_shape(display):
    display.screen.fill(BG)
    rect = pygame.Rect(40, 40, 120, 60)
    display._draw_trapezoid(rect, RED)
    screen = display.screen

    shadow_offset = display.theme.px(5)
    # topo do trapézio não tem afunilamento (só a base) -- um ponto logo à direita do canto
    # superior direito, dentro da faixa deslocada da sombra, nunca é coberto pelo preenchimento.
    probe = (rect.right + shadow_offset - 1, rect.top + shadow_offset + 1)
    px = tuple(screen.get_at(probe)[:3])
    assert px != BG
    assert sum(px) < sum(BG)  # mais escuro que o fundo puro -- é a sombra, não o vazio


def test_trapezoid_shadow_surface_is_cached_by_size(display):
    s1 = display._trapezoid_shadow_surface(100, 50, 0.16)
    s2 = display._trapezoid_shadow_surface(100, 50, 0.16)
    assert s1 is s2


def test_rect_shadow_surface_is_cached_by_size(display):
    s1 = display._rect_shadow_surface(80, 40, 12)
    s2 = display._rect_shadow_surface(80, 40, 12)
    assert s1 is s2


def test_bottom_bar_cards_have_a_soft_shadow(display):
    for n in (1, 2, 3):
        register(display, n)
    display.reveal_number = None
    display._render()

    screen = display.screen
    theme = display.theme
    band_top = theme.height - theme.px(_BOTTOM_BAR_PX)

    found_darker = False
    for y in range(band_top, theme.height, 2):
        for x in range(0, theme.width, 3):
            px = tuple(screen.get_at((x, y))[:3])
            if px != BG and sum(px) < sum(BG):
                found_darker = True
                break
        if found_darker:
            break
    assert found_darker


def test_glow_is_only_drawn_during_the_number_pop_window(display):
    """Brilho suave atrás do número reforça visualmente "acabou de mudar" -- só deve existir
    enquanto o "pop" do número ainda está em andamento (`_NUMBER_POP_MS`), nunca depois."""
    register(display, 5)
    display.reveal_number = None
    rect = _center_rect(display)

    calls = []
    original = RouletteDisplay._glow_surface

    def spy(self, radius):
        calls.append(radius)
        return original(self, radius)

    display.number_anim_start = pygame.time.get_ticks()  # recém trocado
    with mock.patch.object(RouletteDisplay, "_glow_surface", spy):
        display._draw_center_number(rect)
    assert len(calls) == 1

    calls.clear()
    display.number_anim_start = pygame.time.get_ticks() - 10_000  # bem depois do pop
    with mock.patch.object(RouletteDisplay, "_glow_surface", spy):
        display._draw_center_number(rect)
    assert len(calls) == 0
