"""Mockup estático (FASE 3 do redesign visual, v3) — gera um PNG 1080x1920 usando exatamente as
técnicas gráficas disponíveis na implementação real (`app/ui/display.py` em Pygame/SDL, Raspberry
Pi 3): formas geométricas simples (retângulo, círculo, linha), texto com contorno via múltiplos
blits deslocados (mesma técnica de `_blit_outlined_text`), fontes cacheadas por tamanho. SEM alpha
composto, SEM glow, SEM motivo decorativo de roleta — a sofisticação vem só de proporção,
alinhamento, tipografia, contraste e composição (pedido explícito do cliente após a v2).

Reutiliza deliberadamente `app.ui.theme.Theme`/paleta (mesmo módulo que a tela real usa) em vez de
redefinir cores/escala à parte.

v3 -- ~80% da linguagem visual de uma referência "cassino premium" (proporção, densidade, cards
com acabamento, tipografia, hierarquia) + 100% da lógica funcional real do sistema (regras de
FRIO/QUENTE, histórico em raia única, estatísticas, limites, logo). Paleta permanece a paleta real
do sistema (near-black + grafite + azul/vermelho/verde + laranja discreto) -- a referência serve
para acabamento/composição, não para copiar a paleta dourada literalmente.

Uso:
    python tools/mockup_ui.py [--out CAMINHO.png]

Roda com `SDL_VIDEODRIVER=dummy` (sem precisar de tela real) — não depende de X11/KMSDRM, não abre
janela, não toca em nenhum estado do app real (não abre banco, não lê config.yaml)."""
from __future__ import annotations

import argparse
import math
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

_RED_NUMBERS = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}


def color_of(n: int) -> str:
    if n == 0:
        return "green"
    return "red" if n in _RED_NUMBERS else "black"


def blit_outlined(surface, font, text, center, fill, outline, outline_px=1):
    """Mesma técnica de `display.py::_blit_outlined_text`. Usada aqui só com contorno bem fino
    (1px) ou nenhum -- pedido explícito: "sem contorno preto pesado no numeral"."""
    if outline_px > 0:
        o = font.render(text, True, outline)
        for dx in range(-outline_px, outline_px + 1):
            for dy in range(-outline_px, outline_px + 1):
                if dx == 0 and dy == 0:
                    continue
                surface.blit(o, o.get_rect(center=(center[0] + dx, center[1] + dy)))
    fill_surf = font.render(text, True, fill)
    surface.blit(fill_surf, fill_surf.get_rect(center=center))


def draw_text(surface, font, text, pos, color, anchor="topleft"):
    s = font.render(text, True, color)
    r = s.get_rect(**{anchor: pos})
    surface.blit(s, r)
    return r


def draw_snowflake_icon(screen, center, size, color):
    cx, cy = center
    for angle in (0, 60, 120):
        rad = math.radians(angle)
        dx, dy = math.cos(rad) * size, math.sin(rad) * size
        pygame.draw.line(screen, color, (cx - dx, cy - dy), (cx + dx, cy + dy), 2)


def draw_flame_icon(screen, center, size, color):
    cx, cy = center
    pts = [
        (cx, cy - size), (cx + size * 0.55, cy - size * 0.1), (cx + size * 0.35, cy + size * 0.9),
        (cx, cy + size * 0.55), (cx - size * 0.35, cy + size * 0.9), (cx - size * 0.55, cy - size * 0.1),
    ]
    pygame.draw.polygon(screen, color, pts)


def draw_top_bar(screen, theme: Theme) -> int:
    """Faixa compacta: só APOSTA MÍN. / APOSTA MÁX. + indicador discreto SISTEMA OK no canto —
    nada de horário/data. Altura pequena de propósito (não desperdiça área vertical)."""
    top_h = theme.px(140)

    draw_text(screen, theme.font(20, True), "APOSTA MÍN.", (theme.px(32), theme.px(40)), TEXT_SECONDARY)
    draw_text(screen, theme.font(46, True), "R$ 5,00", (theme.px(32), theme.px(66)), TEXT_PRIMARY)

    draw_text(screen, theme.font(20, True), "APOSTA MÁX.",
              (theme.width - theme.px(32), theme.px(40)), TEXT_SECONDARY, anchor="topright")
    draw_text(screen, theme.font(46, True), "R$ 500,00",
              (theme.width - theme.px(32), theme.px(66)), TEXT_PRIMARY, anchor="topright")

    dot_c = (theme.width - theme.px(24), theme.px(18))
    pygame.draw.circle(screen, GREEN, dot_c, theme.px(5))
    draw_text(screen, theme.font(14, True), "SISTEMA OK",
              (dot_c[0] - theme.px(11), dot_c[1]), TEXT_MUTED, anchor="midright")

    pygame.draw.line(screen, PANEL_BORDER, (0, top_h), (theme.width, top_h), 1)
    return top_h


def draw_card_column(screen, theme: Theme, rect, title, subtitle, caption, accent, icon,
                      entries, unit, top_margin: int) -> pygame.Rect:
    """FRIO/QUENTE: cabeçalho + card compacto e bem preenchido (não uma lista solta perdida numa
    coluna gigantesca) -- linhas com ranking + número + contagem, separadas por hairline. O bloco
    inteiro (cabeçalho+card) é posicionado com a MESMA margem superior nas duas colunas, pra
    garantir simetria visual entre FRIO e QUENTE. Devolve o retângulo do card, para quem chamar
    (QUENTE) usar o espaço abaixo dele pra logo."""
    y = rect.top + top_margin

    title_font = theme.font(34, True)
    title_w = title_font.size(title)[0]
    icon_r = theme.px(12)
    icon_cx = rect.centerx - title_w // 2 - icon_r - theme.px(10)
    if icon == "snowflake":
        draw_snowflake_icon(screen, (icon_cx, y + icon_r), icon_r, accent)
    else:
        draw_flame_icon(screen, (icon_cx, y + icon_r * 1.3), icon_r, accent)
    draw_text(screen, title_font, title, (rect.centerx + icon_r, y), accent, anchor="midtop")
    y += theme.px(42)
    draw_text(screen, theme.font(20, True), subtitle, (rect.centerx, y), TEXT_PRIMARY, anchor="midtop")
    y += theme.px(28)
    draw_text(screen, theme.font(15, True), caption, (rect.centerx, y), TEXT_MUTED, anchor="midtop")
    y += theme.px(30)

    row_h = theme.px(140)
    card_w = int(rect.width * 0.90)
    card_h = row_h * len(entries) + theme.px(20)
    card = pygame.Rect(0, y, card_w, card_h)
    card.centerx = rect.centerx

    pygame.draw.rect(screen, PANEL_BG, card, border_radius=theme.px(12))
    pygame.draw.rect(screen, accent, (card.left, card.top, card.width, theme.px(3)),
                      border_top_left_radius=theme.px(12), border_top_right_radius=theme.px(12))
    pygame.draw.rect(screen, PANEL_BORDER, card, width=1, border_radius=theme.px(12))

    rank_font = theme.font(20, True)
    num_font = theme.font(52, True)
    count_font = theme.font(30, True)
    unit_font = theme.font(15, True)
    rank_r = theme.px(18)
    inset = theme.px(26)

    row_y = card.top + theme.px(10)
    for i, (num, count) in enumerate(entries):
        row = pygame.Rect(card.left, row_y, card.width, row_h)
        rank_cx = row.left + inset + rank_r
        pygame.draw.circle(screen, BG, (rank_cx, row.centery), rank_r)
        pygame.draw.circle(screen, accent, (rank_cx, row.centery), rank_r, width=1)
        draw_text(screen, rank_font, str(i + 1), (rank_cx, row.centery), accent, anchor="center")

        draw_text(screen, num_font, str(num), (rank_cx + rank_r + theme.px(18), row.centery),
                  TEXT_PRIMARY, anchor="midleft")

        val_x = row.right - inset
        count_r = draw_text(screen, count_font, str(count), (val_x, row.centery - theme.px(12)),
                             accent, anchor="topright")
        draw_text(screen, unit_font, unit, (val_x, count_r.bottom + theme.px(2)), TEXT_MUTED, anchor="topright")
        if i < len(entries) - 1:
            ly = row.bottom
            pygame.draw.line(screen, PANEL_BORDER, (card.left + theme.px(20), ly), (card.right - theme.px(20), ly), 1)
        row_y = row.bottom

    return card


def draw_center(screen, theme: Theme, rect, last_number: int, history: list[int]) -> None:
    y = rect.top + theme.px(28)
    draw_text(screen, theme.font(34, True), "ÚLTIMO RESULTADO", (rect.centerx, y), TEXT_PRIMARY, anchor="midtop")
    y += theme.px(50)

    last_color = color_of(last_number)
    circle_fill = {"red": RED, "black": GRAY_85, "green": GREEN}[last_color]
    # "Pequeno accent externo": um único anel fino mais claro, não uma pilha de camadas com alpha.
    ring_color = {"red": (255, 130, 122), "black": (110, 112, 118), "green": (110, 255, 170)}[last_color]

    radius = theme.px(160)
    cx, cy = rect.centerx, y + radius

    pygame.draw.circle(screen, circle_fill, (cx, cy), radius)
    pygame.draw.circle(screen, ring_color, (cx, cy), radius, width=theme.px(2))
    pygame.draw.circle(screen, ring_color, (cx, cy), radius + theme.px(9), width=1)

    # Numeral: sem contorno pesado -- só o preenchimento branco (contraste já garantido pelo
    # próprio círculo escuro/colorido atrás dele).
    num_font = theme.font(210, True)
    blit_outlined(screen, num_font, str(last_number), (cx, cy), fill=TEXT_PRIMARY, outline=(0, 0, 0), outline_px=0)

    tag_y = cy + radius + theme.px(38)
    tags = [("PRETO" if last_color == "black" else "VERMELHO" if last_color == "red" else "ZERO",
             TEXT_PRIMARY if last_color == "black" else RED if last_color == "red" else GREEN)]
    if last_number != 0:
        tags.append(("ÍMPAR" if last_number % 2 else "PAR", CYAN))
        tags.append(("MENOR" if last_number <= 18 else "MAIOR", ORANGE))

    pill_font = theme.font(18, True)
    pill_gap = theme.px(10)
    widths = [pill_font.size(label)[0] + theme.px(28) for label, _ in tags]
    px = rect.centerx - (sum(widths) + pill_gap * (len(tags) - 1)) // 2
    for (label, color), pw in zip(tags, widths):
        pill = pygame.Rect(px, tag_y, pw, theme.px(36))
        pygame.draw.rect(screen, PANEL_BG, pill, border_radius=theme.px(18))
        pygame.draw.rect(screen, color, pill, width=2, border_radius=theme.px(18))
        draw_text(screen, pill_font, label, pill.center, color, anchor="center")
        px += pw + pill_gap

    divider_y = tag_y + theme.px(36) + theme.px(26)
    pygame.draw.line(screen, PANEL_BORDER, (rect.left + theme.px(16), divider_y),
                      (rect.right - theme.px(16), divider_y), 1)

    draw_center_history(screen, theme, pygame.Rect(rect.left, divider_y + theme.px(18), rect.width,
                                                     rect.bottom - (divider_y + theme.px(18))), history)


def draw_center_history(screen, theme: Theme, rect, history: list[int]) -> None:
    """REGRA IMUTÁVEL preservada: três raias verticais -- preto esquerda, zero centro, vermelho
    direita -- mais recente no topo, descendo, uma linha por giro (nunca duas listas paralelas).
    Círculos ~25-30% maiores que a v1/v2 para melhor legibilidade à distância."""
    lane_x = {
        "black": rect.left + rect.width // 4,
        "green": rect.centerx,
        "red": rect.right - rect.width // 4,
    }
    lane_fill = {"black": TEXT_PRIMARY, "green": GREEN, "red": RED}
    lane_ring = {"black": GRAY_85, "green": (0, 90, 55), "red": (110, 30, 26)}

    row_h = theme.px(76)
    r = theme.px(28)
    pygame.draw.line(screen, PANEL_BORDER, (rect.centerx, rect.top), (rect.centerx, rect.bottom - theme.px(10)), 1)

    hist_font = theme.font(30, True)
    for i, n in enumerate(history):
        yy = rect.top + i * row_h + row_h // 2
        if yy > rect.bottom - theme.px(10):
            break
        c = color_of(n)
        x = lane_x[c]
        pygame.draw.circle(screen, lane_ring[c], (x, yy), r + theme.px(4), width=2)
        pygame.draw.circle(screen, PANEL_BG, (x, yy), r)
        blit_outlined(screen, hist_font, str(n), (x, yy), fill=lane_fill[c], outline=(0, 0, 0), outline_px=1)


def draw_logo(screen, theme: Theme, rect, image_path: str | None) -> None:
    """Logo integrada à coluna QUENTE, abaixo do card, centralizada, com respiro -- não um card
    enorme com o texto "LOGO DO CASSINO". Quando existe uma imagem real, ela é pré-escalada e
    centralizada preservando aspect ratio (mesma técnica de `_prepare_logo` em produção); sem
    imagem configurada, mostra um placeholder discreto (tracejado, texto pequeno) só para dev."""
    max_w = int(rect.width * 0.6)
    max_h = int(rect.height * 0.55)

    image = None
    if image_path and Path(image_path).exists():
        image = pygame.image.load(image_path).convert_alpha()

    if image is not None:
        ratio = min(max_w / image.get_width(), max_h / image.get_height())
        size = (max(1, int(image.get_width() * ratio)), max(1, int(image.get_height() * ratio)))
        scaled = pygame.transform.smoothscale(image, size)
        screen.blit(scaled, scaled.get_rect(center=rect.center))
        return

    box = pygame.Rect(0, 0, max_w, min(max_h, theme.px(90)))
    box.center = rect.center
    _dashed_rect(screen, box, PANEL_BORDER, dash=theme.px(8), gap=theme.px(6))
    draw_text(screen, theme.font(15, True), "LOGO", box.center, TEXT_MUTED, anchor="center")


def _dashed_rect(screen, rect: pygame.Rect, color, dash: int, gap: int) -> None:
    def dashed_line(p1, p2):
        length = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
        steps = max(1, int(length // (dash + gap)))
        for s in range(steps + 1):
            t0 = (s * (dash + gap)) / length
            t1 = min(1.0, t0 + dash / length)
            if t0 >= 1.0:
                break
            a = (p1[0] + (p2[0] - p1[0]) * t0, p1[1] + (p2[1] - p1[1]) * t0)
            b = (p1[0] + (p2[0] - p1[0]) * t1, p1[1] + (p2[1] - p1[1]) * t1)
            pygame.draw.line(screen, color, a, b, 1)

    corners = [rect.topleft, rect.topright, rect.bottomright, rect.bottomleft, rect.topleft]
    for a, b in zip(corners, corners[1:]):
        dashed_line(a, b)


def draw_bottom_bar(screen, theme: Theme, bottom_h: int) -> None:
    """7 categorias em cards com fundo grafite levemente diferente do fundo + linha de accent no
    topo -- mesmo vocabulário visual dos cards de FRIO/QUENTE (produto único, não 3 telas)."""
    top = theme.height - bottom_h
    pygame.draw.line(screen, PANEL_BORDER, (0, top), (theme.width, top), 1)

    cells = [
        ("ÍMPAR", "47%", CYAN), ("PAR", "51%", CYAN), ("VERMELHO", "49%", RED),
        ("ZERO", "3%", GREEN), ("PRETO", "48%", TEXT_PRIMARY),
        ("MENOR", "46%", ORANGE), ("MAIOR", "51%", ORANGE),
    ]
    cell_w = theme.width / len(cells)
    gutter = theme.px(6)
    value_font = theme.font(44, True)
    label_font = theme.font(20, True)

    for i, (label, value, accent) in enumerate(cells):
        outer = pygame.Rect(int(i * cell_w), top, int(cell_w) + 1, bottom_h)
        card = outer.inflate(-gutter * 2, -theme.px(20))
        pygame.draw.rect(screen, PANEL_BG, card, border_radius=theme.px(10))
        pygame.draw.rect(screen, accent, (card.left, card.top, card.width, theme.px(3)),
                          border_top_left_radius=theme.px(10), border_top_right_radius=theme.px(10))
        pygame.draw.rect(screen, PANEL_BORDER, card, width=1, border_radius=theme.px(10))
        draw_text(screen, value_font, value, (card.centerx, card.top + theme.px(48)), accent, anchor="center")
        draw_text(screen, label_font, label, (card.centerx, card.top + theme.px(88)), TEXT_SECONDARY, anchor="center")


def build_mockup(logo_path: str | None = None) -> pygame.Surface:
    pygame.init()
    theme = Theme(W, H)
    screen = pygame.Surface((theme.width, theme.height))
    screen.fill(BG)

    top_h = draw_top_bar(screen, theme)
    bottom_h = theme.px(250)
    columns_top = top_h
    columns_h = theme.height - top_h - bottom_h
    col_w = theme.width // 3

    frio_rect = pygame.Rect(0, columns_top, col_w, columns_h)
    center_rect = pygame.Rect(col_w, columns_top, col_w, columns_h)
    quente_rect = pygame.Rect(col_w * 2, columns_top, theme.width - col_w * 2, columns_h)

    pad_v = theme.px(16)
    pygame.draw.line(screen, PANEL_BORDER, (col_w, columns_top + pad_v), (col_w, columns_top + columns_h - pad_v), 1)
    pygame.draw.line(screen, PANEL_BORDER, (col_w * 2, columns_top + pad_v), (col_w * 2, columns_top + columns_h - pad_v), 1)

    # Cabeçalho+card de FRIO define a margem superior; QUENTE usa a MESMA margem, garantindo
    # simetria visual entre as duas colunas mesmo com dados de tamanhos diferentes.
    header_h = theme.px(42 + 28 + 30)
    row_h = theme.px(140)
    n_entries = 5
    card_h = row_h * n_entries + theme.px(20)
    block_h = header_h + card_h
    top_margin = max(theme.px(24), (columns_h - block_h) // 2)

    frio_entries = [(9, 41), (3, 38), (28, 36), (14, 33), (31, 30)]
    draw_card_column(screen, theme, frio_rect, "FRIO", "GIROS SEM SAIR", "últimos 300 giros",
                      CYAN, "snowflake", frio_entries, "GIROS", top_margin)

    quente_entries = [(7, 9), (23, 8), (0, 7), (16, 6), (34, 5)]
    quente_card = draw_card_column(screen, theme, quente_rect, "QUENTE", "OCORRÊNCIAS", "últimos 300 giros",
                                    RED, "flame", quente_entries, "VEZES", top_margin)

    logo_zone = pygame.Rect(quente_rect.left, quente_card.bottom, quente_rect.width,
                             quente_rect.bottom - quente_card.bottom)
    draw_logo(screen, theme, logo_zone, logo_path)

    # Exemplo do cliente (17, 32, 22, 0, 11, 5, 14) + continuação plausível só para o histórico
    # preencher visualmente o espaço disponível na coluna central, como aconteceria numa mesa em
    # operação real (não é um limite de exibição novo -- `_draw_center_history` já mostra "quantas
    # linhas couberem", isso é só o mockup usando uma amostra maior pra representar isso).
    history_sample = [17, 32, 22, 0, 11, 5, 14, 9, 26, 3, 31, 20, 15, 8, 27, 4, 19, 36]
    draw_center(screen, theme, center_rect, last_number=17, history=history_sample)

    draw_bottom_bar(screen, theme, bottom_h)
    return screen


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(Path(__file__).resolve().parent / "mockup_output.png"))
    parser.add_argument("--logo", default=None, help="Caminho opcional de uma logo real (PNG) para pré-visualizar.")
    args = parser.parse_args()

    surface = build_mockup(logo_path=args.logo)
    pygame.image.save(surface, args.out)
    print(f"mockup salvo em {args.out} ({surface.get_width()}x{surface.get_height()})")


if __name__ == "__main__":
    main()
