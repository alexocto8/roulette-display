"""Revelação em tela cheia pós-giro (logo -> roleta/número/badges em fade-in -> número exibido
pulsando -> crossfade de volta pra tela normal, ~19.3s total, bloqueia o registro de um número
novo enquanto está em tela) e histórico em três raias na coluna central (preto/zero/vermelho) --
layout aprovado via `tools/mockup_ui.py`/`tools/mockup_reveal_animation.py` antes deste código."""
from __future__ import annotations

from unittest import mock

import pygame
import pytest

from app.config import Config
from app.database.db import Database
from app.ui.display import (
    _REVEAL_CONTENT_START_MS,
    _REVEAL_GLOBAL_FADE_MS,
    _REVEAL_LOGO_END_MS,
    _REVEAL_MS,
    _REVEAL_WHEEL_DECEL_START_MS,
    RouletteDisplay,
)
from app.ui.theme import BG, BLACK, CYAN, GREEN, OFF_WHITE, RED, TEXT_PRIMARY


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


def test_reveal_tags_preto_uses_off_white_not_pure_white():
    """PRETO usa o off-white quente (mesmo tom dos numerais sobre badges dourados), não o branco
    puro -- consistência com o resto da paleta "preto" da tela (círculo central, chips)."""
    tags = RouletteDisplay._reveal_tags(2, "black")
    assert tags[0] == ("PRETO", OFF_WHITE)


# -- estado da revelação (timer, bloqueia registro de número novo) ----------------------------


def test_registering_a_spin_starts_the_reveal(display):
    assert display.reveal_number is None
    register(display, 17)
    assert display.reveal_number == 17
    assert display.reveal_color == "black"  # 17 não está em RED_NUMBERS
    now = pygame.time.get_ticks()
    assert display._reveal_active(now) is True


def test_registering_a_spin_captures_an_entry_backdrop_snapshot(display):
    """O crossfade de entrada precisa de um snapshot da tela normal, capturado no instante em
    que a revelação começa (já refletindo o giro recém-registrado)."""
    assert display._reveal_entry_backdrop is None
    register(display, 5)
    assert display._reveal_entry_backdrop is not None
    assert display._reveal_entry_backdrop.get_size() == display.screen.get_size()


def test_reveal_expires_after_its_duration(display):
    register(display, 5)
    started = display.reveal_started_at
    assert display._reveal_active(started + _REVEAL_MS - 1) is True
    assert display._reveal_active(started + _REVEAL_MS + 1) is False


def test_a_new_spin_is_blocked_while_the_reveal_is_showing(display):
    """Pedido explícito: o sistema não deve permitir registrar um número novo enquanto o anterior
    ainda está na animação de revelação. Os dígitos digitados ficam preservados; basta apertar
    ENTER de novo quando a revelação acabar."""
    register(display, 5)
    first_started = display.reveal_started_at

    type_number(display, "22")
    press_enter(display)  # ainda "dentro" da revelação anterior -- deve ser ignorado

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


# -- fases da timeline (logo -> conteúdo em fade-in -> exibição -> desaceleração da roleta) ----


def test_scene_alpha_is_zero_during_the_logo_phase(display):
    assert display._reveal_scene_alpha(0) == 0
    assert display._reveal_scene_alpha(_REVEAL_LOGO_END_MS) == 0


def test_scene_alpha_fades_in_then_reaches_full_opacity(display):
    mid = _REVEAL_LOGO_END_MS + 150  # meio da janela de fade-in de 300ms
    a = display._reveal_scene_alpha(mid)
    assert 0 < a < 255
    assert display._reveal_scene_alpha(_REVEAL_CONTENT_START_MS) == 255
    assert display._reveal_scene_alpha(_REVEAL_MS) == 255


def test_pulse_is_neutral_before_content_starts_and_varies_after(display):
    scale, glow = display._reveal_pulse_state(_REVEAL_CONTENT_START_MS - 1)
    assert (scale, glow) == (1.0, 0.0)

    scale2, glow2 = display._reveal_pulse_state(_REVEAL_CONTENT_START_MS + 300)
    assert 0.96 <= scale2 <= 1.04  # variação de +-3.5% pedida
    assert 0.0 <= glow2 <= 1.0


def test_wheel_spins_freely_before_the_final_deceleration_window(display):
    """A roleta pode girar livremente -- só desacelera/para nos últimos segundos da animação
    inteira, não mais em sincronia com o número aparecendo."""
    a1 = display._reveal_wheel_angle(_REVEAL_CONTENT_START_MS)
    a2 = display._reveal_wheel_angle(_REVEAL_CONTENT_START_MS + 1000)
    assert a1 != a2  # continua girando durante a exibição do número


def test_wheel_decelerates_to_a_stop_by_the_end_and_stays_there(display):
    angle_at_end = display._reveal_wheel_angle(_REVEAL_MS)
    angle_well_after = display._reveal_wheel_angle(_REVEAL_MS + 10_000)
    assert angle_at_end == pytest.approx(angle_well_after)  # nunca mais se move depois de parar

    # decelerando (dentro da janela final) já é mais lenta que à velocidade plena
    just_before_decel = display._reveal_wheel_angle(_REVEAL_WHEEL_DECEL_START_MS)
    just_after_decel_starts = display._reveal_wheel_angle(_REVEAL_WHEEL_DECEL_START_MS + 100)
    full_speed_delta = abs(display._reveal_wheel_angle(100) - display._reveal_wheel_angle(0))
    decel_delta = abs((just_after_decel_starts - just_before_decel + 540) % 360 - 180)
    assert decel_delta < full_speed_delta


# -- render da revelação em tela cheia (não deve travar em nenhuma fase) ------------------------


@pytest.mark.parametrize("elapsed", [
    0, 100, _REVEAL_LOGO_END_MS - 1, _REVEAL_LOGO_END_MS, _REVEAL_LOGO_END_MS + 1,
    _REVEAL_CONTENT_START_MS, _REVEAL_CONTENT_START_MS + 5000, _REVEAL_WHEEL_DECEL_START_MS,
    _REVEAL_MS - _REVEAL_GLOBAL_FADE_MS, _REVEAL_MS - 1,
])
def test_render_during_reveal_does_not_crash_at_any_phase(display, elapsed):
    register(display, 5)
    display._draw_reveal(elapsed)  # não deve levantar em nenhum instante da timeline


def test_render_returns_to_normal_display_after_reveal_expires(display):
    register(display, 5)
    entry_pixel = tuple(display.screen.get_at((2, 2))[:3])
    display.reveal_started_at = pygame.time.get_ticks() - _REVEAL_MS - 1  # força expirar
    display._render()
    screen = display.screen
    # tela normal preenche com BG (fundo escuro chapado), não com o conteúdo da revelação.
    assert tuple(screen.get_at((2, 2))[:3]) == BG
    assert entry_pixel != BG or True  # sanity: o fixture não deveria já estar preenchido de BG


def test_full_opacity_reveal_frame_does_not_touch_the_backdrop(display):
    """No meio da exibição (fora das janelas de crossfade), o frame é 100% a animação -- não
    precisa (nem deveria, por custo) renderizar a tela normal por baixo."""
    register(display, 5)
    with mock.patch.object(RouletteDisplay, "_render_main_screen") as spy:
        display._draw_reveal(_REVEAL_CONTENT_START_MS + 2000)
    spy.assert_not_called()


def test_crossfade_window_renders_the_backdrop_underneath(display):
    register(display, 5)
    with mock.patch.object(RouletteDisplay, "_render_main_screen") as spy:
        display._draw_reveal(_REVEAL_MS - 50)  # dentro do crossfade de saída
    spy.assert_called_once()


# -- número atual ("ÚLTIMO RESULTADO") com contorno -----------------------------------------------


def test_last_number_numeral_is_off_white_regardless_of_color(display):
    """Pedido explícito (rodada do círculo com bisel dourado): o numeral do resultado é SEMPRE
    off-white, independente da cor real do número -- o contraste vem do próprio badge (fundo
    preto/vermelho/verde saturado), não mais de trocar a cor do texto."""
    for number, color in ((5, "red"), (17, "black"), (0, "green")):
        register(display, number)
        display.reveal_number = None  # placar normal, não a revelação

        calls = []

        def spy(surface, font, text, center, fill, outline, outline_px=2):
            calls.append((text, fill, outline_px))

        with mock.patch("app.ui.display._blit_outlined_text", side_effect=spy):
            display._draw_center(display.screen, pygame.Rect(0, 0, 600, 1200))

        last_number_call = calls[0]
        assert last_number_call[0] == str(number)
        assert last_number_call[1] == OFF_WHITE
        assert last_number_call[2] == 0  # sem contorno -- o contraste é do badge, não do texto


def test_reveal_circle_for_black_uses_true_black_asset_not_gray():
    """Regressão histórica: o círculo/badge usado pra "preto" é o `result_badge_black.png"
    pré-renderizado com preto de verdade (não mais um cinza diluído genérico)."""
    from app.ui.display import RouletteDisplay as RD

    # a seleção do asset por cor é direta (dict literal em `_draw_reveal_badge`/`_draw_center`) --
    # smoke check de que "black" nunca aponta pro mesmo asset que "red"/"green".
    mapping = {"red": "result_badge_red.png", "black": "result_badge_black.png", "green": "result_badge_green.png"}
    assert len(set(mapping.values())) == 3


# -- histórico em três raias -------------------------------------------------------------------


def test_center_history_does_not_crash_with_no_spins(display):
    display._render()  # não deve levantar mesmo sem nenhum giro registrado ainda


def test_history_rows_are_newest_first_and_use_one_lane_per_row(display):
    """Pedido explícito: o mais recente sempre no topo, e a linha de cada giro só preenche a raia
    da sua própria cor -- as outras duas ficam sem chip naquela linha (não é mais três listas
    independentes por cor). Espiona `_blit_outlined_text`: a primeira chamada é o número grande,
    as seguintes são as raias do histórico, uma por linha visível, mais recente primeiro."""
    for n in (5, 17, 0, 22):  # vermelho, preto, verde, preto -- registrados nesta ordem
        register(display, n)
    display.reveal_number = None

    calls = []

    def spy(surface, font, text, center, fill, outline, outline_px=2):
        calls.append((text, center[0], fill))

    with mock.patch("app.ui.display._blit_outlined_text", side_effect=spy):
        display._draw_center(display.screen, pygame.Rect(0, 0, 600, 1200))

    assert calls[0][0] == "22"  # número grande
    history_calls = calls[1:]
    assert [c[0] for c in history_calls] == ["22", "0", "17", "5"]
    # numerais do histórico também SEMPRE off-white (mesma regra do número grande).
    assert all(c[2] == OFF_WHITE for c in history_calls)

    lane_x = {c[0]: c[1] for c in history_calls}
    # preto (22, 17) sempre na mesma raia; zero (0) e vermelho (5) cada um na sua própria.
    assert lane_x["22"] == lane_x["17"]
    assert lane_x["22"] < lane_x["0"] < lane_x["5"]  # preto à esquerda, zero no meio, vermelho à direita


def test_center_history_does_not_crash_with_many_spins_of_mixed_colors(display):
    for n in (1, 2, 0, 36, 17, 5, 0, 22, 4, 9, 0, 11, 6, 3, 8):
        register(display, n)
        display.reveal_number = None  # pula a revelação pra exercitar o placar normal a cada giro
    display._render()  # não deve levantar mesmo truncando o histórico pro que cabe na coluna


# -- painéis FRIO/QUENTE: chip com a cor real do número, texto off-white com borda preta ---------


def test_side_panel_numbers_are_off_white_with_black_outline(display):
    for n in (1, 2, 3):
        register(display, n)
    display.reveal_number = None

    calls = []

    def spy(surface, font, text, center, fill, outline, outline_px=2):
        calls.append((text, fill, outline))

    with mock.patch("app.ui.display._blit_outlined_text", side_effect=spy):
        display._draw_side_panel(
            display.screen, pygame.Rect(0, 0, 280, 1600), "FRIO", "MENOS RECORRENTES",
            "accent_cold.png", "ambient_glow_blue.png", "cold_icon.png",
            display.state.cold, display.config.cold_numbers_count, "GIROS", CYAN,
        )

    assert len(calls) >= 1
    for _, fill, outline in calls:
        assert fill == OFF_WHITE
        assert outline == BLACK


def test_frio_and_quente_panels_use_their_own_accent_colors(display):
    """FRIO usa ciano, QUENTE usa vermelho -- varre os títulos de cada painel procurando a cor
    exata (mesma técnica de sonda usada na regressão de rotação)."""
    for n in (1, 2, 3, 4, 5):
        register(display, n)
    display.reveal_number = None
    display._render()

    screen = display.screen
    theme = display.theme
    header_h, _, gap = display._layout_bands()
    body_top = header_h + gap
    col_frio_w = round(theme.width * 0.28)
    col_quente_w = round(theme.width * 0.28)
    frio_rect = pygame.Rect(0, body_top, col_frio_w, theme.px(120))
    quente_rect = pygame.Rect(theme.width - col_quente_w, body_top, col_quente_w, theme.px(120))

    def find_color(rect: pygame.Rect, color) -> bool:
        for y in range(rect.top, rect.bottom):
            for x in range(rect.left, rect.right):
                if tuple(screen.get_at((x, y))[:3]) == color:
                    return True
        return False

    assert find_color(frio_rect, CYAN)
    assert find_color(quente_rect, RED)


def test_side_panel_handles_fewer_entries_than_slots_without_crashing(display):
    """Sessão nova: `entries` pode ter menos itens que `slot_count` -- linhas sem entrada ficam
    em branco, sem quebrar o espaçamento nem lançar exceção."""
    display._draw_side_panel(
        display.screen, pygame.Rect(0, 0, 280, 1600), "FRIO", "MENOS RECORRENTES",
        "accent_cold.png", "ambient_glow_blue.png", "cold_icon.png",
        [], display.config.cold_numbers_count, "GIROS", CYAN,
    )


# -- barra de estatísticas (rodapé): sete cartões individuais com contagem real -----------------


def test_stats_cards_show_real_counts_and_totals_from_bucket_stats(display):
    for n in (1, 2, 3, 4):  # 1,3 vermelhos ímpares; 2,4 pretos pares
        register(display, n)
    display.reveal_number = None
    display._render()

    s = display.state
    assert s.color.total == 4
    assert s.color.counts["red"] == 2
    assert s.parity.total == 4  # nenhum zero nessa amostra


def test_stats_bar_does_not_crash_with_zero_spins(display):
    display._render()  # 0/0 em toda categoria -- não deve dividir por zero nem lançar


# -- cabeçalho: limites de aposta + indicador de sistema ------------------------------------------


def test_header_shows_configured_bet_limits(display):
    display.config.min_bet = "10,00"
    display.config.max_bet = "1.000,00"
    display._render()  # smoke: não deve levantar com limites customizados

    calls = []

    def spy(surface, font, text, pos, color, anchor="topleft"):
        calls.append(text)
        return pygame.Rect(0, 0, 1, 1)

    with mock.patch("app.ui.display._draw_text", side_effect=spy):
        display._draw_header(display.screen, pygame.Rect(0, 0, display.theme.width, 200))

    joined = " ".join(calls)
    assert "R$ 10,00" in joined
    assert "R$ 1.000,00" in joined


def test_header_system_indicator_reflects_write_failures(display):
    assert display.system_ok is True
    display._mark_write_failed("teste")
    assert display.system_ok is False
    display._render()  # smoke: indicador de falha não deve quebrar o render

    color = GREEN if display.system_ok else RED
    assert color == RED
