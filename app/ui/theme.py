"""Colors, fonts and responsive-scaling helpers shared by every screen.

Everything is derived from the current display size (`Theme.scale`) rather than hardcoded pixel
coordinates, so the same layout code works at 1920x1080, 1080x1920 (portrait), 1366x768, etc, per
the "must be responsive" requirement.
"""
from __future__ import annotations

import logging

import pygame

logger = logging.getLogger("roulette.ui")

# Base resolution the design was proportioned against; `scale` below is relative to this.
_BASE_WIDTH = 1080

# Paleta exata do contrato visual do cliente (hex fornecidos por eles).
BG = (0x0D, 0x11, 0x17)  # Fundo
CYAN = (0x00, 0x84, 0xFF)  # Azul (Frios)
ORANGE = (0xFF, 0x8A, 0x00)  # Laranja (Quentes)
RED = (0xFF, 0x3B, 0x30)  # Vermelho (Números)
GREEN = (0x00, 0xE6, 0x76)  # Verde (Zero)
TEXT_PRIMARY = (0xFF, 0xFF, 0xFF)  # Branco (Texto)
TEXT_SECONDARY = (0xA0, 0xA0, 0xA0)  # Cinza (Secundário)

# Não fornecidos explicitamente na paleta do cliente — derivados com o mesmo espírito (fundo
# escuro, divisores discretos):
PANEL_BG = (19, 24, 31)  # levemente mais claro que BG, para células "neutras" da barra inferior
PANEL_BORDER = (54, 60, 68)  # "divisores cinza claro discreto"
TEXT_MUTED = (120, 126, 132)
BLACK = (25, 25, 28)
GOLD = (198, 161, 91)  # linha de destaque nos badges QUENTE (vermelho) + usado no admin
SILVER = (192, 192, 192)  # linha de destaque nos badges FRIO (ciano)
# Off-white quente (não o branco puro de TEXT_PRIMARY) -- usado só nos NUMERAIS desenhados em cima
# dos badges/chips dourados (histórico, FRIO/QUENTE, círculo central, revelação): branco puro
# contra o brilho do bisel dourado lê como "estourado"/sem contraste; esse tom levemente creme
# funciona melhor visualmente sobre ouro sem perder legibilidade sobre as cores de preenchimento.
OFF_WHITE = (0xF2, 0xF0, 0xEC)
# Cinza 85% (85% preto/ink) — usado como preenchimento de CÍRCULOS/fundos que representam a
# categoria "preto": BLACK puro é quase invisível contra o fundo escuro do app (sem nada que dê
# contraste), então essas formas usam este cinza escuro deliberadamente mais claro que BLACK, mas
# ainda lido como "preto/escuro", não confundível com as outras cores. Diferente de BLACK, que
# continua sendo o preenchimento do NÚMERO em si (com contorno claro dando o contraste).
GRAY_85 = (38, 38, 38)

COLOR_MAP = {"red": RED, "black": GRAY_85, "green": GREEN}

# Cor do NÚMERO em si (placar central): precisa ser a cor real da roleta (vermelho/preto/verde),
# nunca um acento genérico. "Preto" usa branco (a única cor clara da paleta) em vez de BLACK puro,
# que seria invisível sobre o fundo escuro sem nenhum chip atrás para dar contraste.
NUMBER_COLOR_MAP = {"red": RED, "black": TEXT_PRIMARY, "green": GREEN}


class Theme:
    def __init__(self, screen_width: int, screen_height: int):
        self.width = screen_width
        self.height = screen_height
        self.scale = max(0.5, min(3.0, screen_width / _BASE_WIDTH))
        self.portrait = screen_height > screen_width
        self._fonts: dict[tuple[int, bool], pygame.font.Font] = {}

    def px(self, base_size: int) -> int:
        """Scale a pixel value proportionally to the current resolution."""
        return round(base_size * self.scale)

    def font(self, base_size: int, bold: bool = False) -> pygame.font.Font:
        size = self.px(base_size)
        key = (size, bold)
        f = self._fonts.get(key)
        if f is None:
            # SysFont with an empty name falls back to pygame's bundled default font, which is
            # always available (no external .ttf asset required, keeps install trivial).
            f = pygame.font.SysFont(None, size, bold=bold)
            self._fonts[key] = f
        return f
