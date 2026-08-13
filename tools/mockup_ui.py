"""Mockup estático (FASE 3 do redesign visual, v4) — gera um PNG 1080x1920 reproduzindo fielmente
a composição/densidade de uma referência visual "cassino premium" fornecida pelo cliente, mantendo
100% da lógica funcional real do sistema (app/ui/display.py). Só técnicas gráficas disponíveis em
Pygame/SDL no Raspberry Pi 3: retângulo, círculo, linha, texto com contorno via multi-blit, fontes
cacheadas por tamanho. Sem blur, sem shader, sem glow em camadas, sem filtro complexo.

Reutiliza `app.ui.theme.Theme`/paleta (mesmo módulo que a tela real usa) -- as cores sugeridas pelo
cliente nesta rodada (#070B10, #00A8FF, etc.) são conceitualmente quase idênticas à paleta já
existente em produção, então o mockup continua na paleta real em vez de introduzir uma segunda
paleta que divergiria da implementação final.

v4 -- reformulação total de composição pedida pelo cliente: FRIO/QUENTE viram UM card único que
ocupa a altura combinada de "último resultado + histórico" (mesma técnica visual da referência:
painel com borda visível preenchendo todo o espaço reservado, ranking compacto no topo, sem
"flutuar" no meio de espaço vazio). Proporções de zona seguem os percentuais pedidos: cabeçalho
~9%, último resultado ~22%, histórico ~42%, estatísticas ~16%.

Uso:
    python tools/mockup_ui.py [--out CAMINHO.png] [--logo CAMINHO.png]

Roda com `SDL_VIDEODRIVER=dummy` — não abre janela, não toca em banco/config real."""
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


def draw_header(screen, theme: Theme, header_h: int) -> None:
    """ZONA 1: dois cards compactos (APOSTA MÍN./MÁX.) + indicador discreto no canto. Sem
    relógio/data/copyright/OCTO/retenção/backup. Linha de accent fina abaixo de tudo.

    Faixa fininha reservada no topo só para o indicador -- evita a sobreposição entre o texto
    "SISTEMA OK" e o card APOSTA MÁX. quando os dois disputam o mesmo canto."""
    indicator_h = theme.px(26)
    pad = theme.px(16)
    card_top = indicator_h
    card_h = header_h - indicator_h - theme.px(14)
    label_font, value_font = theme.font(17, True), theme.font(36, True)

    def chip(x_ref, left: bool, label, value):
        w = max(value_font.size(value)[0], label_font.size(label)[0]) + pad * 2
        rect = (pygame.Rect(x_ref, card_top, w, card_h) if left
                else pygame.Rect(x_ref - w, card_top, w, card_h))
        pygame.draw.rect(screen, PANEL_BG, rect, border_radius=theme.px(8))
        pygame.draw.rect(screen, PANEL_BORDER, rect, width=1, border_radius=theme.px(8))
        draw_text(screen, label_font, label, (rect.centerx, rect.top + theme.px(7)), TEXT_SECONDARY, anchor="midtop")
        draw_text(screen, value_font, value, (rect.centerx, rect.top + theme.px(24)), ORANGE, anchor="midtop")

    chip(theme.px(20), True, "APOSTA MÍN.", "R$ 5,00")
    chip(theme.width - theme.px(20), False, "APOSTA MÁX.", "R$ 500,00")

    dot_c = (theme.width - theme.px(18), theme.px(13))
    pygame.draw.circle(screen, GREEN, dot_c, theme.px(5))
    draw_text(screen, theme.font(14, True), "SISTEMA OK",
              (dot_c[0] - theme.px(10), dot_c[1]), TEXT_MUTED, anchor="midright")

    pygame.draw.line(screen, (74, 62, 40), (0, header_h), (theme.width, header_h), 1)


def draw_side_card(screen, theme: Theme, col_rect: pygame.Rect, top_y: int, title, subtitle, accent, icon,
                    entries, unit, show_logo: bool = False, logo_path: str | None = None,
                    logo_zone_h: int = 0) -> pygame.Rect:
    """FRIO/QUENTE: card compacto e bem preenchido, do TAMANHO DO SEU CONTEÚDO (ranking + logo,
    quando houver) -- não esticado para preencher artificialmente a coluna inteira (a referência
    do cliente também deixa espaço livre abaixo do card, isso é intencional, não um defeito).
    FRIO e QUENTE começam exatamente na mesma altura (`top_y`) e usam a mesma tipografia/row_h --
    é isso que garante "componentes irmãos", não terem a mesma altura total de card (QUENTE fica
    mais alto por causa da logo, exatamente como na referência)."""
    row_h = theme.px(128)
    header_h = theme.px(20 + 36 + 28 + 10)
    card_h = header_h + row_h * len(entries) + theme.px(18) + (logo_zone_h if show_logo else 0)

    side_pad = theme.px(10)
    rect = col_rect.inflate(-side_pad * 2, 0)
    rect.top = top_y
    rect.height = card_h

    pygame.draw.rect(screen, PANEL_BG, rect, border_radius=theme.px(14))
    pygame.draw.rect(screen, accent, (rect.left, rect.top, rect.width, theme.px(3)),
                      border_top_left_radius=theme.px(14), border_top_right_radius=theme.px(14))
    pygame.draw.rect(screen, PANEL_BORDER, rect, width=1, border_radius=theme.px(14))

    y = rect.top + theme.px(20)
    title_font = theme.font(30, True)
    title_w = title_font.size(title)[0]
    icon_r = theme.px(11)
    icon_cx = rect.centerx - title_w // 2 - icon_r - theme.px(9)
    if icon == "snowflake":
        draw_snowflake_icon(screen, (icon_cx, y + icon_r), icon_r, accent)
    else:
        draw_flame_icon(screen, (icon_cx, y + icon_r * 1.3), icon_r, accent)
    draw_text(screen, title_font, title, (rect.centerx + icon_r, y), accent, anchor="midtop")
    y += theme.px(36)
    draw_text(screen, theme.font(16, True), subtitle, (rect.centerx, y), TEXT_SECONDARY, anchor="midtop")
    y += theme.px(28)
    pygame.draw.line(screen, PANEL_BORDER, (rect.left + theme.px(18), y), (rect.right - theme.px(18), y), 1)
    y += theme.px(10)

    rank_font = theme.font(20, True)
    num_font = theme.font(56, True)
    count_font = theme.font(32, True)
    unit_font = theme.font(14, True)
    rank_r = theme.px(18)
    inset = theme.px(24)

    for i, (num, count) in enumerate(entries):
        row = pygame.Rect(rect.left, y, rect.width, row_h)
        rank_cx = row.left + inset + rank_r
        pygame.draw.circle(screen, BG, (rank_cx, row.centery), rank_r)
        pygame.draw.circle(screen, accent, (rank_cx, row.centery), rank_r, width=1)
        draw_text(screen, rank_font, str(i + 1), (rank_cx, row.centery), accent, anchor="center")

        draw_text(screen, num_font, str(num), (rank_cx + rank_r + theme.px(16), row.centery),
                  TEXT_PRIMARY, anchor="midleft")

        val_x = row.right - inset
        count_r = draw_text(screen, count_font, str(count), (val_x, row.centery - theme.px(11)),
                             accent, anchor="topright")
        draw_text(screen, unit_font, unit, (val_x, count_r.bottom + theme.px(1)), TEXT_MUTED, anchor="topright")

        if i < len(entries) - 1:
            ly = row.bottom
            pygame.draw.line(screen, PANEL_BORDER, (rect.left + theme.px(18), ly), (rect.right - theme.px(18), ly), 1)
        y = row.bottom

    if show_logo:
        logo_zone = pygame.Rect(rect.left + theme.px(14), y + theme.px(8),
                                 rect.width - theme.px(28), rect.bottom - (y + theme.px(8)) - theme.px(14))
        draw_logo(screen, theme, logo_zone, logo_path)

    return rect


def draw_logo(screen, theme: Theme, rect, image_path: str | None) -> None:
    """Pré-escala/centraliza preservando aspect ratio quando existe uma logo real (mesma técnica
    de `_prepare_logo` em produção); sem imagem configurada, placeholder neutro sem texto grande."""
    max_w = int(rect.width * 0.82)
    max_h = int(rect.height * 0.86)

    image = None
    if image_path and Path(image_path).exists():
        image = pygame.image.load(image_path).convert_alpha()

    if image is not None:
        ratio = min(max_w / image.get_width(), max_h / image.get_height())
        size = (max(1, int(image.get_width() * ratio)), max(1, int(image.get_height() * ratio)))
        scaled = pygame.transform.smoothscale(image, size)
        screen.blit(scaled, scaled.get_rect(center=rect.center))
        return

    box = pygame.Rect(0, 0, min(max_w, theme.px(150)), min(max_h, theme.px(80)))
    box.center = rect.center
    pygame.draw.rect(screen, (26, 30, 36), box, border_radius=theme.px(8))
    pygame.draw.rect(screen, PANEL_BORDER, box, width=1, border_radius=theme.px(8))


def draw_center(screen, theme: Theme, last_rect: pygame.Rect, hist_rect: pygame.Rect,
                 last_number: int, history: list[int]) -> None:
    # -- ÚLTIMO RESULTADO (ZONA 2, elemento de maior impacto) --
    y = last_rect.top + theme.px(14)
    draw_text(screen, theme.font(32, True), "ÚLTIMO RESULTADO", (last_rect.centerx, y), TEXT_PRIMARY, anchor="midtop")
    y += theme.px(48)

    last_color = color_of(last_number)
    circle_fill = {"red": RED, "black": GRAY_85, "green": GREEN}[last_color]
    border_tone = {"red": (255, 120, 112), "black": (100, 102, 108), "green": (90, 235, 160)}[last_color]
    gold_accent = (196, 160, 92)  # accent dourado MUITO sutil (um único anel fino), não a cor do círculo

    radius = int(last_rect.width * 0.325)
    cx, cy = last_rect.centerx, y + radius

    pygame.draw.circle(screen, circle_fill, (cx, cy), radius)
    pygame.draw.circle(screen, border_tone, (cx, cy), radius, width=theme.px(2))
    pygame.draw.circle(screen, gold_accent, (cx, cy), radius + theme.px(7), width=1)

    num_font = theme.font(int(radius * 1.3), True)
    blit_outlined(screen, num_font, str(last_number), (cx, cy), fill=TEXT_PRIMARY, outline=(0, 0, 0), outline_px=0)

    tag_y = cy + radius + theme.px(30)
    tags = [("PRETO" if last_color == "black" else "VERMELHO" if last_color == "red" else "ZERO",
             TEXT_PRIMARY if last_color == "black" else RED if last_color == "red" else GREEN)]
    if last_number != 0:
        tags.append(("ÍMPAR" if last_number % 2 else "PAR", CYAN))
        tags.append(("MENOR" if last_number <= 18 else "MAIOR", ORANGE))

    pill_font = theme.font(17, True)
    pill_gap = theme.px(8)
    widths = [pill_font.size(label)[0] + theme.px(24) for label, _ in tags]
    px = last_rect.centerx - (sum(widths) + pill_gap * (len(tags) - 1)) // 2
    for (label, color), pw in zip(tags, widths):
        pill = pygame.Rect(px, tag_y, pw, theme.px(32))
        pygame.draw.rect(screen, PANEL_BG, pill, border_radius=theme.px(16))
        pygame.draw.rect(screen, color, pill, width=2, border_radius=theme.px(16))
        draw_text(screen, pill_font, label, pill.center, color, anchor="center")
        px += pw + pill_gap

    # -- HISTÓRICO (ZONA 3, região central abaixo do último resultado) --
    draw_center_history(screen, theme, hist_rect, history)


def draw_center_history(screen, theme: Theme, rect: pygame.Rect, history: list[int]) -> None:
    """REGRA FUNCIONAL IMUTÁVEL: três raias -- preto esquerda, zero centro, vermelho direita --
    mais recente no topo, uma linha por giro, nunca duas listas paralelas. Círculos grandes,
    legíveis a distância; abaixo dos giros reais, as raias continuam como guias verticais
    pontilhadas discretas (slots futuros) até o fim da zona -- mesma ideia da referência, sem
    inventar giros que não aconteceram."""
    y = rect.top
    draw_text(screen, theme.font(22, True), "HISTÓRICO", (rect.centerx, y), TEXT_SECONDARY, anchor="midtop")
    y += theme.px(40)

    lane_x = {
        "black": rect.left + rect.width // 4,
        "green": rect.centerx,
        "red": rect.right - rect.width // 4,
    }
    lane_fill = {"black": TEXT_PRIMARY, "green": GREEN, "red": RED}
    lane_ring = {"black": (110, 112, 116), "green": (20, 130, 80), "red": (170, 50, 46)}

    lanes_top = y
    lanes_bottom = rect.bottom - theme.px(6)
    r = theme.px(34)
    row_h = int(r * 2.35)
    n_rows = max(len(history), (lanes_bottom - lanes_top) // row_h)

    for x in lane_x.values():
        pygame.draw.line(screen, (26, 30, 36), (x, lanes_top), (x, lanes_top + n_rows * row_h), 1)

    hist_font = theme.font(int(r * 1.15), True)
    for i in range(n_rows):
        yy = lanes_top + i * row_h + row_h // 2
        if i < len(history):
            n = history[i]
            active_lane = color_of(n)
        else:
            n = None
            active_lane = None
        for lane, x in lane_x.items():
            if lane == active_lane:
                pygame.draw.circle(screen, lane_ring[lane], (x, yy), r + theme.px(4), width=2)
                pygame.draw.circle(screen, PANEL_BG, (x, yy), r)
                blit_outlined(screen, hist_font, str(n), (x, yy), fill=lane_fill[lane],
                              outline=(0, 0, 0), outline_px=1)
            else:
                pygame.draw.circle(screen, (52, 56, 62), (x, yy), theme.px(3))


def draw_stats(screen, theme: Theme, rect: pygame.Rect) -> None:
    """ZONA 4: título + 7 cards compactos ocupando toda a largura -- percentual protagonista,
    label secundária, sem gráfico/número absoluto que não exista na implementação real."""
    draw_text(screen, theme.font(22, True), "ESTATÍSTICAS", (rect.centerx, rect.top), TEXT_SECONDARY, anchor="midtop")
    cards_top = rect.top + theme.px(38)
    pygame.draw.line(screen, PANEL_BORDER, (theme.px(24), cards_top - theme.px(10)),
                      (rect.width - theme.px(24), cards_top - theme.px(10)), 1)

    cells = [
        ("ÍMPAR", "47%", CYAN), ("PAR", "51%", CYAN), ("VERMELHO", "49%", RED),
        ("ZERO", "3%", GREEN), ("PRETO", "48%", TEXT_PRIMARY),
        ("MENOR", "46%", ORANGE), ("MAIOR", "51%", ORANGE),
    ]
    cell_w = theme.width / len(cells)
    gutter = theme.px(6)
    card_h = rect.bottom - cards_top
    value_font = theme.font(36, True)
    label_font = theme.font(17, True)

    for i, (label, value, accent) in enumerate(cells):
        outer = pygame.Rect(int(i * cell_w), cards_top, int(cell_w) + 1, card_h)
        card = outer.inflate(-gutter * 2, 0)
        pygame.draw.rect(screen, PANEL_BG, card, border_radius=theme.px(8))
        pygame.draw.rect(screen, accent, (card.left, card.top, card.width, theme.px(3)),
                          border_top_left_radius=theme.px(8), border_top_right_radius=theme.px(8))
        pygame.draw.rect(screen, PANEL_BORDER, card, width=1, border_radius=theme.px(8))
        draw_text(screen, value_font, value, (card.centerx, card.top + theme.px(16)), accent, anchor="midtop")
        draw_text(screen, label_font, label, (card.centerx, card.top + theme.px(58)), TEXT_SECONDARY, anchor="midtop")


def build_mockup(logo_path: str | None = None) -> pygame.Surface:
    pygame.init()
    theme = Theme(W, H)
    screen = pygame.Surface((theme.width, theme.height))
    screen.fill(BG)

    # -- proporções de zona pedidas: cabeçalho ~9%, último resultado ~22%, histórico ~42%,
    # estatísticas ~16%, o resto vira margens/gaps entre zonas.
    header_h = theme.px(round(H * 0.09))
    last_h = theme.px(round(H * 0.22))
    hist_h = theme.px(round(H * 0.42))
    stats_h = theme.px(round(H * 0.16))
    gap = theme.px(14)

    draw_header(screen, theme, header_h)

    body_top = header_h + gap
    stats_top = theme.height - stats_h
    body_bottom = stats_top - gap

    last_rect_full = pygame.Rect(0, body_top, theme.width, last_h)
    hist_rect_full = pygame.Rect(0, body_top + last_h + gap, theme.width, body_bottom - (body_top + last_h + gap))

    col_frio_w = round(theme.width * 0.28)
    col_quente_w = round(theme.width * 0.28)
    col_center_w = theme.width - col_frio_w - col_quente_w

    frio_col = pygame.Rect(0, body_top, col_frio_w, body_bottom - body_top)
    center_col = pygame.Rect(col_frio_w, body_top, col_center_w, body_bottom - body_top)
    quente_col = pygame.Rect(col_frio_w + col_center_w, body_top, col_quente_w, body_bottom - body_top)

    # FRIO e QUENTE começam na MESMA altura (`body_top`) -- garante que as três colunas comecem
    # "praticamente na mesma altura", como pedido. QUENTE fica mais alto que FRIO por causa da
    # logo -- mesmo padrão da referência (o card não é esticado artificialmente pra compensar).
    frio_entries = [(9, 41), (3, 38), (28, 36), (14, 33), (31, 30)]
    draw_side_card(screen, theme, frio_col, body_top, "FRIO", "GIROS SEM SAIR", CYAN, "snowflake",
                    frio_entries, "GIROS", logo_path=None)

    quente_entries = [(7, 9), (23, 8), (0, 7), (16, 6), (34, 5)]
    draw_side_card(screen, theme, quente_col, body_top, "QUENTE", "OCORRÊNCIAS", RED, "flame",
                    quente_entries, "VEZES", show_logo=True, logo_path=logo_path, logo_zone_h=theme.px(320))

    last_rect = pygame.Rect(center_col.left + theme.px(8), last_rect_full.top,
                             center_col.width - theme.px(16), last_h)
    hist_rect = pygame.Rect(center_col.left + theme.px(8), hist_rect_full.top,
                             center_col.width - theme.px(16), hist_rect_full.height)
    draw_center(screen, theme, last_rect, hist_rect, last_number=17, history=[17, 32, 22, 0, 11, 5, 14])

    draw_stats(screen, theme, pygame.Rect(0, stats_top, theme.width, stats_h))

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
