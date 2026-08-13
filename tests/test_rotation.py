"""app/ui/rotation.py: rotação de tela em software (config.screen_rotation) para monitores
paisagem montados em pé quando o driver de vídeo não gira sozinho (ex.: console de VM sem suporte
real a `xrandr --rotate`) -- ver README, seção "Orientação retrato"."""
from __future__ import annotations

import pygame

from app.config import Config
from app.ui.rotation import create_screen


def make_config(rotation: int) -> Config:
    return Config(fullscreen=False, screen_rotation=rotation)


def test_no_rotation_returns_the_real_screen_directly():
    screen, theme = create_screen(make_config(0), "teste", dev_size=(650, 1000))
    real = pygame.display.get_surface()
    assert screen is real
    assert screen.get_size() == (650, 1000)
    assert theme.width == 650 and theme.height == 1000


def test_rotation_90_swaps_logical_dimensions():
    screen, theme = create_screen(make_config(90), "teste", dev_size=(1920, 1080))
    # a superfície física continua 1920x1080; a lógica (que o resto do app desenha) precisa ter
    # os eixos trocados para o Theme detectar retrato corretamente.
    assert screen.get_size() == (1080, 1920)
    assert theme.width == 1080 and theme.height == 1920
    assert theme.portrait is True


def test_rotation_270_swaps_logical_dimensions():
    screen, theme = create_screen(make_config(270), "teste", dev_size=(1920, 1080))
    assert screen.get_size() == (1080, 1920)
    assert theme.portrait is True


def test_rotation_180_keeps_dimensions():
    screen, theme = create_screen(make_config(180), "teste", dev_size=(1920, 1080))
    assert screen.get_size() == (1920, 1080)


def test_invalid_rotation_value_falls_back_to_no_rotation():
    screen, theme = create_screen(make_config(45), "teste", dev_size=(650, 1000))
    real = pygame.display.get_surface()
    assert screen is real  # 45 não é um valor válido -- trata como 0


def test_flip_actually_rotates_the_drawn_frame():
    """O teste que importa de verdade: desenha algo reconhecível na superfície lógica, chama
    pygame.display.flip(), e confirma que o pixel certo aparece no lugar certo da superfície
    física -- não só que as dimensões batem."""
    screen, _theme = create_screen(make_config(90), "teste", dev_size=(400, 200))
    assert screen.get_size() == (200, 400)  # lógica: 200 larg x 400 alt (trocada)

    RED = (255, 0, 0)
    screen.fill((0, 0, 0))
    # canto superior-esquerdo da superfície LÓGICA (retrato)
    screen.fill(RED, pygame.Rect(0, 0, 20, 20))
    pygame.display.flip()

    real = pygame.display.get_surface()
    assert real.get_size() == (400, 200)  # física: continua paisagem
    # pygame.transform.rotate(90) gira sentido anti-horário: o canto superior-esquerdo da
    # superfície lógica (retrato) vai parar no canto INFERIOR-esquerdo da superfície física.
    assert real.get_at((5, 195))[:3] == RED
    assert real.get_at((395, 5))[:3] != RED


def test_create_screen_twice_in_the_same_process_does_not_double_rotate():
    """Reproduz exatamente a sequência real do app: license_screen (com rotação) seguido do
    painel principal (também com rotação), no mesmo processo, sem um pygame.quit() no meio --
    `create_screen` precisa continuar rotacionando uma vez só, não acumular."""
    config = make_config(90)
    screen1, _ = create_screen(config, "primeira tela", dev_size=(400, 200))
    RED = (255, 0, 0)
    screen1.fill((0, 0, 0))
    screen1.fill(RED, pygame.Rect(0, 0, 20, 20))
    pygame.display.flip()
    real = pygame.display.get_surface()
    first_pixel = real.get_at((5, 195))[:3]

    # segunda tela do mesmo processo (ex.: painel principal aberto depois da tela de licença)
    screen2, _ = create_screen(config, "segunda tela", dev_size=(400, 200))
    screen2.fill((0, 0, 0))
    screen2.fill(RED, pygame.Rect(0, 0, 20, 20))
    pygame.display.flip()
    second_pixel = real.get_at((5, 195))[:3]

    assert tuple(first_pixel) == RED
    assert tuple(second_pixel) == RED  # mesmo resultado -- não girou de novo em cima do já girado


def test_screen_rotation_config_field_defaults_to_zero_for_existing_installs():
    assert Config().screen_rotation == 0


def test_hot_cold_panels_survive_rotation(tmp_path):
    """Regressão: se algum método de desenho voltasse a usar `pygame.display.get_surface()` em
    vez do `surface`/`self.screen` recebido -- sem rotação os dois são a mesma superfície (nunca
    pegaria o bug), mas com `screen_rotation` configurado `self.screen` vira uma superfície
    lógica separada, e o conteúdo desenhado na física seria sobrescrito pelo flip rotacionado da
    lógica, sumindo do quadro final. O título "QUENTE" (vermelho) é usado como sonda porque é
    garantidamente desenhado sempre que há pelo menos uma entrada quente."""
    from app.database.db import Database
    from app.services.spin_service import SpinService
    from app.ui.display import RouletteDisplay

    config = Config(
        fullscreen=False, screen_rotation=90,
        database_path=str(tmp_path / "roulette.db"),
        assets_dir="assets",
        license_path=str(tmp_path / "license.dat"),
        license_state_path=str(tmp_path / ".license_state"),
    )
    db = Database(tmp_path / "roulette.db")
    db.initialize()
    display = RouletteDisplay(config, db)
    for n in (17, 22, 5, 17, 22, 5, 17):  # repete pra garantir uma entrada QUENTE de verdade
        display.service.register_spin(n)
    display.state = display.service.get_display_state()
    display._render()

    physical = pygame.display.get_surface()
    from app.ui.theme import RED

    # varre a superfície física inteira -- algum pixel precisa ser exatamente RED (título QUENTE
    # ou um chip vermelho no painel/histórico), senão o conteúdo não sobreviveu ao pipeline de
    # rotação.
    found_red = False
    w, h = physical.get_size()
    for x in range(0, w, 4):
        for y in range(0, h, 4):
            if tuple(physical.get_at((x, y))[:3]) == RED:
                found_red = True
                break
        if found_red:
            break
    assert found_red, "conteúdo vermelho (painel QUENTE) não apareceu na superfície física rotacionada"

    db.close()
    pygame.quit()
