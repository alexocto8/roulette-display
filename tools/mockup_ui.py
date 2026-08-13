"""Mockup estático (FASE 3 do redesign visual) — gera um PNG 1080x1920 usando exatamente as
técnicas gráficas disponíveis na implementação real (`app/ui/display.py` em Pygame/SDL, Raspberry
Pi 3): formas geométricas simples (retângulo, círculo, linha), texto com contorno via múltiplos
blits deslocados (mesma técnica de `_blit_outlined_text`), fontes cacheadas por tamanho, e alpha
simples via `set_alpha()` — nada que precisaria ser removido ou simplificado depois para caber no
hardware real.

Reutiliza deliberadamente `app.ui.theme.Theme`/paleta (mesmo módulo que a tela real usa) em vez de
redefinir cores/escala à parte — a fidelidade de cor e proporção do mockup depende diretamente do
mesmo código de produção, não de uma cópia que poderia divergir.

Explicitamente NÃO usado (nem seria reproduzível no Pi 3 sem simplificação): blur em tempo real,
glow via superfícies grandes com muitas camadas, shaders, filtros de imagem, sombras compostas por
múltiplas camadas semitransparentes, vídeo. Um único `alpha` plano por elemento (ex.: preenchimento
sutil de card) é o teto de complexidade usado aqui — é o mesmo teto já usado hoje em produção
(`_tint`, cards da barra de estatística).

Uso:
    python tools/mockup_ui.py [--out CAMINHO.png]

Roda com `SDL_VIDEODRIVER=dummy` (sem precisar de tela real) — não depende de X11/KMSDRM, não abre
janela, não toca em nenhum estado do app real (não abre banco, não lê config.yaml)."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pygame

from app.ui.theme import (
    BG, CYAN, RED, GREEN, ORANGE, TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED,
    PANEL_BORDER, PANEL_BG, GRAY_85, Theme,
)

W, H = 1080, 1920

# Mapa de cor por número (regra real da roleta europeia, igual a app/services/roulette_data) —
# duplicado aqui de propósito simples (não importa o serviço real) porque o mockup só precisa da
# cor pra desenhar, não da lógica de giro/estatística de verdade.
_RED_NUMBERS = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}


def color_of(n: int) -> str:
    if n == 0:
        return "green"
    return "red" if n in _RED_NUMBERS else "black"


def blit_outlined(surface, font, text, center, fill, outline, outline_px=2):
    """Mesma técnica de `display.py::_blit_outlined_text` — renderiza o contorno uma vez e
    replica em cada offset ao redor, depois o preenchimento por cima. Nenhum filtro/blur real."""
    o = font.render(text, True, outline)
    for dx in range(-outline_px, outline_px + 1):
        for dy in range(-outline_px, outline_px + 1):
            if dx == 0 and dy == 0:
                continue
            surface.blit(o, o.get_rect(center=(center[0] + dx, center[1] + dy)))
    surface.blit(font.render(text, True, fill), font.render(text, True, fill).get_rect(center=center))


def draw_text(surface, font, text, pos, color, anchor="topleft"):
    s = font.render(text, True, color)
    r = s.get_rect(**{anchor: pos})
    surface.blit(s, r)
    return r


def draw_top_bar(screen, theme: Theme) -> int:
    """APOSTA MÍN. / APOSTA MÁX. + indicador discreto SISTEMA OK — únicos elementos permitidos
    no topo (nada de horário, data, assinatura, retenção, backup)."""
    limits_h = int(theme.height * 0.10)

    draw_text(screen, theme.font(26, True), "APOSTA MÍN.", (theme.px(40), theme.px(46)), TEXT_SECONDARY)
    draw_text(screen, theme.font(58, True), "R$ 5,00", (theme.px(40), theme.px(82)), TEXT_PRIMARY)

    lbl = draw_text(screen, theme.font(26, True), "APOSTA MÁX.",
                     (theme.width - theme.px(40), theme.px(46)), TEXT_SECONDARY, anchor="topright")
    draw_text(screen, theme.font(58, True), "R$ 500,00",
              (theme.width - theme.px(40), theme.px(82)), TEXT_PRIMARY, anchor="topright")

    dot_c = (theme.width - theme.px(40) - lbl.width - theme.px(18), theme.px(30))
    pygame.draw.circle(screen, GREEN, dot_c, theme.px(6))
    draw_text(screen, theme.font(16, True), "SISTEMA OK",
              (dot_c[0] - theme.px(12), dot_c[1] - theme.px(8)), TEXT_MUTED, anchor="topright")

    pygame.draw.line(screen, PANEL_BORDER, (0, limits_h), (theme.width, limits_h), 1)
    return limits_h


def draw_column_header(screen, theme: Theme, rect, title, subtitle, caption, color) -> int:
    y = rect.top + theme.px(22)
    draw_text(screen, theme.font(32, True), title, (rect.centerx, y), color, anchor="midtop")
    y += theme.px(40)
    draw_text(screen, theme.font(19, True), subtitle, (rect.centerx, y), TEXT_PRIMARY, anchor="midtop")
    y += theme.px(26)
    draw_text(screen, theme.font(14, True), caption, (rect.centerx, y), TEXT_MUTED, anchor="midtop")
    return y + theme.px(24)


def draw_slim_rows(screen, theme: Theme, rect, entries, accent, unit, avail_top, avail_bottom) -> None:
    """FRIO/QUENTE: linha fina com barra de cor de 3px (não trapézio cheio com sombra) — mesmo
    número de posições/dados de hoje, custo de desenho menor (um retângulo de 3px de largura em
    vez de um polígono + superfície de sombra cacheada por tamanho)."""
    row_h, row_gap = theme.px(74), theme.px(16)
    row_w = int(rect.width * 0.86)
    row_x = rect.left + (rect.width - row_w) // 2
    block_h = len(entries) * row_h + (len(entries) - 1) * row_gap
    y = avail_top + max(0, (avail_bottom - avail_top - block_h) // 2)

    num_font = theme.font(38, True)
    count_font = theme.font(24, True)
    unit_font = theme.font(13, True)

    for i, (num, count) in enumerate(entries):
        row = pygame.Rect(row_x, y, row_w, row_h)
        pygame.draw.rect(screen, accent, (row.left, row.top, theme.px(3), row.height))
        draw_text(screen, num_font, str(num), (row.left + theme.px(22), row.centery), TEXT_PRIMARY, anchor="midleft")
        draw_text(screen, count_font, str(count), (row.right, row.centery - theme.px(12)), accent, anchor="midright")
        draw_text(screen, unit_font, unit, (row.right, row.centery + theme.px(14)), TEXT_MUTED, anchor="midright")
        if i < len(entries) - 1:
            ly = row.bottom + row_gap // 2
            pygame.draw.line(screen, PANEL_BORDER, (row_x, ly), (row_x + row_w, ly), 1)
        y = row.bottom + row_gap


def draw_center(screen, theme: Theme, rect, last_number: int, history: list[int]) -> None:
    y = rect.top + theme.px(22)
    draw_text(screen, theme.font(32, True), "ÚLTIMO RESULTADO", (rect.centerx, y), TEXT_PRIMARY, anchor="midtop")
    y += theme.px(46)

    last_color = color_of(last_number)
    circle_fill = {"red": RED, "black": GRAY_85, "green": GREEN}[last_color]
    # Anel fino em torno do círculo -- um segundo `pygame.draw.circle` com `width=` (contorno, não
    # preenchido), não uma superfície de glow separada com alpha. Cor um pouco mais clara que o
    # preenchimento, fixa (sem gradiente).
    ring_color = {"red": (255, 110, 100), "black": (90, 92, 96), "green": (110, 255, 170)}[last_color]

    radius = theme.px(138)
    cx, cy = rect.centerx, y + radius
    pygame.draw.circle(screen, circle_fill, (cx, cy), radius)
    pygame.draw.circle(screen, ring_color, (cx, cy), radius + theme.px(12), width=theme.px(3))
    blit_outlined(screen, theme.font(190, True), str(last_number), (cx, cy),
                  fill=TEXT_PRIMARY, outline=(0, 0, 0), outline_px=2)

    tag_y = cy + radius + theme.px(34)
    tags = [("PRETO" if last_color == "black" else "VERMELHO" if last_color == "red" else "ZERO",
             TEXT_PRIMARY if last_color == "black" else RED if last_color == "red" else GREEN)]
    if last_number != 0:
        tags.append(("ÍMPAR" if last_number % 2 else "PAR", CYAN))
        tags.append(("MENOR" if last_number <= 18 else "MAIOR", ORANGE))

    pill_font = theme.font(20, True)
    pill_gap = theme.px(12)
    widths = [pill_font.size(label)[0] + theme.px(34) for label, _ in tags]
    px = rect.centerx - (sum(widths) + pill_gap * (len(tags) - 1)) // 2
    for (label, color), pw in zip(tags, widths):
        pill = pygame.Rect(px, tag_y, pw, theme.px(40))
        pygame.draw.rect(screen, PANEL_BG, pill, border_radius=theme.px(20))
        pygame.draw.rect(screen, color, pill, width=2, border_radius=theme.px(20))
        draw_text(screen, pill_font, label, pill.center, color, anchor="center")
        px += pw + pill_gap

    divider_y = tag_y + theme.px(40) + theme.px(26)
    pygame.draw.line(screen, PANEL_BORDER, (rect.left + theme.px(16), divider_y),
                      (rect.right - theme.px(16), divider_y), 1)

    draw_center_history(screen, theme, pygame.Rect(rect.left, divider_y + theme.px(22), rect.width,
                                                     rect.bottom - (divider_y + theme.px(22))), history)


def draw_center_history(screen, theme: Theme, rect, history: list[int]) -> None:
    """REGRA CRÍTICA preservada: três raias verticais -- preto esquerda, zero centro, vermelho
    direita -- mais recente no topo, descendo. Nunca duas listas independentes por cor."""
    lane_x = {
        "black": rect.left + rect.width // 4,
        "green": rect.centerx,
        "red": rect.right - rect.width // 4,
    }
    lane_fill = {"black": TEXT_PRIMARY, "green": GREEN, "red": RED}
    lane_ring = {"black": GRAY_85, "green": (0, 90, 55), "red": (110, 30, 26)}

    row_h = theme.px(60)
    r = theme.px(22)
    pygame.draw.line(screen, (32, 36, 42), (rect.centerx, rect.top), (rect.centerx, rect.bottom - theme.px(10)), 1)

    hist_font = theme.font(26, True)
    for i, n in enumerate(history):
        yy = rect.top + i * row_h + row_h // 2
        if yy > rect.bottom - theme.px(10):
            break
        c = color_of(n)
        x = lane_x[c]
        pygame.draw.circle(screen, lane_ring[c], (x, yy), r + theme.px(4), width=2)
        pygame.draw.circle(screen, PANEL_BG, (x, yy), r)
        blit_outlined(screen, hist_font, str(n), (x, yy), fill=lane_fill[c], outline=(0, 0, 0), outline_px=1)


def draw_bottom_bar(screen, theme: Theme, bottom_h: int) -> None:
    """7 categorias (mesmas de hoje) em cards discretos: fundo plano + uma linha de 3px colorida
    no topo do card (não moldura cheia com 2px em todo o contorno + preenchimento tingido)."""
    top = theme.height - bottom_h
    pygame.draw.line(screen, PANEL_BORDER, (0, top), (theme.width, top), 1)

    cells = [
        ("ÍMPAR", "47%", CYAN), ("PAR", "51%", CYAN), ("VERMELHO", "49%", RED),
        ("ZERO", "3%", GREEN), ("PRETO", "48%", TEXT_PRIMARY),
        ("MENOR", "46%", ORANGE), ("MAIOR", "51%", ORANGE),
    ]
    cell_w = theme.width / len(cells)
    gutter = theme.px(6)
    value_font = theme.font(42, True)
    label_font = theme.font(20, True)

    for i, (label, value, accent) in enumerate(cells):
        outer = pygame.Rect(int(i * cell_w), top, int(cell_w) + 1, bottom_h)
        card = outer.inflate(-gutter * 2, -theme.px(22))
        pygame.draw.rect(screen, PANEL_BG, card, border_radius=theme.px(10))
        pygame.draw.rect(screen, accent, (card.left, card.top, card.width, theme.px(3)))
        draw_text(screen, value_font, value, (card.centerx, card.top + theme.px(44)), accent, anchor="center")
        draw_text(screen, label_font, label, (card.centerx, card.top + theme.px(82)), TEXT_SECONDARY, anchor="center")


def build_mockup() -> pygame.Surface:
    pygame.init()
    theme = Theme(W, H)
    screen = pygame.Surface((theme.width, theme.height))
    screen.fill(BG)

    limits_h = draw_top_bar(screen, theme)
    bottom_h = 270
    columns_top = limits_h
    columns_h = theme.height - limits_h - bottom_h
    col_w = theme.width // 3

    frio_rect = pygame.Rect(0, columns_top, col_w, columns_h)
    center_rect = pygame.Rect(col_w, columns_top, col_w, columns_h)
    quente_rect = pygame.Rect(col_w * 2, columns_top, theme.width - col_w * 2, columns_h)

    pad_v = theme.px(16)
    pygame.draw.line(screen, PANEL_BORDER, (col_w, columns_top + pad_v), (col_w, columns_top + columns_h - pad_v), 1)
    pygame.draw.line(screen, PANEL_BORDER, (col_w * 2, columns_top + pad_v), (col_w * 2, columns_top + columns_h - pad_v), 1)

    # -- FRIO (dados simulados, fictícios porém plausíveis) --
    frio_entries = [(9, 41), (3, 38), (28, 36), (14, 33), (31, 30)]
    header_bottom = draw_column_header(screen, theme, frio_rect, "FRIO", "GIROS SEM SAIR", "últimos 300 giros", CYAN)
    draw_slim_rows(screen, theme, frio_rect, frio_entries, CYAN, "GIROS", header_bottom, frio_rect.bottom - theme.px(24))

    # -- QUENTE + logo pré-cacheada/pré-escalada (mesma técnica de `_prepare_logo`) --
    logo_w, logo_h = int(col_w * 0.62), int(col_w * 0.62 * 0.32)
    logo_rect = pygame.Rect(0, 0, logo_w, logo_h)
    logo_rect.midbottom = (quente_rect.centerx, quente_rect.bottom - theme.px(28))

    quente_entries = [(7, 9), (23, 8), (0, 7), (16, 6), (34, 5)]
    header_bottom = draw_column_header(screen, theme, quente_rect, "QUENTE", "OCORRÊNCIAS", "últimos 300 giros", RED)
    draw_slim_rows(screen, theme, quente_rect, quente_entries, RED, "VEZES", header_bottom, logo_rect.top - theme.px(20))

    pygame.draw.rect(screen, PANEL_BG, logo_rect, border_radius=theme.px(8))
    pygame.draw.rect(screen, PANEL_BORDER, logo_rect, width=1, border_radius=theme.px(8))
    draw_text(screen, theme.font(16, True), "LOGO DO CASSINO", logo_rect.center, TEXT_MUTED, anchor="center")

    # -- último resultado + histórico (dados de exemplo do pedido do cliente) --
    draw_center(screen, theme, center_rect, last_number=17, history=[17, 32, 22, 0, 11, 5, 14])

    draw_bottom_bar(screen, theme, bottom_h)
    return screen


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(Path(__file__).resolve().parent / "mockup_output.png"))
    args = parser.parse_args()

    surface = build_mockup()
    pygame.image.save(surface, args.out)
    print(f"mockup salvo em {args.out} ({surface.get_width()}x{surface.get_height()})")


if __name__ == "__main__":
    main()
