"""Mockup estático (FASE 3 do redesign visual, v5) — gera um PNG 1080x1920 usando assets gráficos
PRÉ-RENDERIZADOS (`assets/ui/*.png`, gerados uma única vez por `tools/build_ui_assets.py` via
Pillow) para reproduzir o acabamento "produto comercial" da referência premium: sombras suaves,
gradientes, glow controlado, ícones vetoriais com antialiasing real. Nada disso é recalculado em
runtime -- os PNGs são carregados uma vez, cacheados como `pygame.Surface`, e só `blit()`/
`smoothscale()` (cacheado por tamanho) acontecem depois disso. Mesma distinção que o cliente
pediu explicitamente: "não recalcular efeito caro continuamente" != "não usar efeito caro".

Antes de rodar este script, gere os assets (se ainda não existirem ou se `build_ui_assets.py`
mudou):
    python tools/build_ui_assets.py
    python tools/mockup_ui.py [--out CAMINHO.png] [--logo CAMINHO.png]

Roda com `SDL_VIDEODRIVER=dummy` — não abre janela, não toca em banco/config real. Mantém 100% da
lógica funcional/estrutural já aprovada na v4 (zonas por proporção, colunas 28/44/28, histórico em
três raias, FRIO/QUENTE simétricos) -- esta rodada é só acabamento/direção de arte."""
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
ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets" / "ui"

_RED_NUMBERS = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}


def color_of(n: int) -> str:
    if n == 0:
        return "green"
    return "red" if n in _RED_NUMBERS else "black"


# -- cache de assets: cada PNG é carregado do disco UMA vez; cada escala pedida (tamanho final em
# pixels) também é computada e cacheada uma vez -- exatamente o padrão já usado em produção pelo
# logo (`self.logo_surface`, pré-escalado no __init__) e pelas sombras (`_trapezoid_shadow_cache`
# em `display.py`). Nenhum `smoothscale`/`image.load` acontece mais de uma vez por combinação
# (asset, tamanho).
_raw_cache: dict[str, pygame.Surface] = {}
_scaled_cache: dict[tuple[str, int, int], pygame.Surface] = {}


def load_asset(name: str) -> pygame.Surface:
    surf = _raw_cache.get(name)
    if surf is None:
        surf = pygame.image.load(str(ASSETS_DIR / name)).convert_alpha()
        _raw_cache[name] = surf
    return surf


def asset_scaled(name: str, size: tuple[int, int]) -> pygame.Surface:
    key = (name, size[0], size[1])
    surf = _scaled_cache.get(key)
    if surf is None:
        surf = pygame.transform.smoothscale(load_asset(name), size)
        _scaled_cache[key] = surf
    return surf


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


def blit_hbar(screen, name, rect: pygame.Rect) -> None:
    """Barra horizontal com gradiente/fade pré-renderizado (accent de card, divisor, etc.),
    esticada pro tamanho pedido -- 1 `smoothscale` cacheado por tamanho, não um degradê recalculado
    pixel a pixel a cada chamada."""
    img = asset_scaled(name, (max(1, rect.width), rect.height))
    screen.blit(img, rect.topleft)


def blit_card_bg(screen, rect: pygame.Rect, radius: int) -> None:
    """Fundo grafite com leve gradiente vertical (em vez de um flat-fill único) -- o tile de 8px
    de largura já vem pré-renderizado (`card_gradient.png`); só esticamos pro tamanho do card."""
    grad = asset_scaled("card_gradient.png", (rect.width, rect.height))
    mask = pygame.Surface(rect.size, pygame.SRCALPHA)
    pygame.draw.rect(mask, (255, 255, 255, 255), mask.get_rect(), border_radius=radius)
    grad = grad.copy()
    grad.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
    screen.blit(grad, rect.topleft)


def draw_header(screen, theme: Theme, header_h: int) -> None:
    indicator_h = theme.px(26)
    pad = theme.px(16)
    card_top = indicator_h
    card_h = header_h - indicator_h - theme.px(14)
    label_font, value_font = theme.font(17, True), theme.font(36, True)

    def chip(x_ref, left: bool, label, value):
        w = max(value_font.size(value)[0], label_font.size(label)[0]) + pad * 2
        rect = (pygame.Rect(x_ref, card_top, w, card_h) if left
                else pygame.Rect(x_ref - w, card_top, w, card_h))
        blit_card_bg(screen, rect, theme.px(8))
        pygame.draw.rect(screen, PANEL_BORDER, rect, width=1, border_radius=theme.px(8))
        draw_text(screen, label_font, label, (rect.centerx, rect.top + theme.px(7)), TEXT_SECONDARY, anchor="midtop")
        draw_text(screen, value_font, value, (rect.centerx, rect.top + theme.px(24)), ORANGE, anchor="midtop")

    chip(theme.px(20), True, "APOSTA MÍN.", "R$ 5,00")
    chip(theme.width - theme.px(20), False, "APOSTA MÁX.", "R$ 500,00")

    dot_c = (theme.width - theme.px(18), theme.px(13))
    pygame.draw.circle(screen, GREEN, dot_c, theme.px(5))
    draw_text(screen, theme.font(14, True), "SISTEMA OK",
              (dot_c[0] - theme.px(10), dot_c[1]), TEXT_MUTED, anchor="midright")

    blit_hbar(screen, "accent_gold.png", pygame.Rect(0, header_h - 2, theme.width, 3))


def draw_side_card(screen, theme: Theme, col_rect: pygame.Rect, top_y: int, title, subtitle,
                    accent_asset: str, icon_asset: str, entries, unit, rank_ring_asset: str,
                    show_logo: bool = False, logo_path: str | None = None, logo_zone_h: int = 0) -> pygame.Rect:
    row_h = theme.px(128)
    header_h = theme.px(20 + 36 + 28 + 10)
    card_h = header_h + row_h * len(entries) + theme.px(18) + (logo_zone_h if show_logo else 0)

    side_pad = theme.px(10)
    rect = col_rect.inflate(-side_pad * 2, 0)
    rect.top = top_y
    rect.height = card_h
    radius = theme.px(14)

    blit_card_bg(screen, rect, radius)
    blit_hbar(screen, accent_asset, pygame.Rect(rect.left, rect.top, rect.width, theme.px(4)))
    pygame.draw.rect(screen, PANEL_BORDER, rect, width=1, border_radius=radius)

    accent = CYAN if "cold" in accent_asset else RED

    y = rect.top + theme.px(22)
    icon_size = theme.px(30)
    icon_img = asset_scaled(icon_asset, (icon_size, icon_size))
    title_font = theme.font(30, True)
    title_w = title_font.size(title)[0]
    icon_x = rect.centerx - title_w // 2 - icon_size - theme.px(8)
    screen.blit(icon_img, (icon_x, y - theme.px(2)))
    draw_text(screen, title_font, title, (rect.centerx + theme.px(8), y), accent, anchor="midtop")
    y += theme.px(36)
    draw_text(screen, theme.font(16, True), subtitle, (rect.centerx, y), TEXT_SECONDARY, anchor="midtop")
    y += theme.px(26)
    blit_hbar(screen, "separator_fade.png", pygame.Rect(rect.left + theme.px(16), y, rect.width - theme.px(32), 2))
    y += theme.px(12)

    rank_font = theme.font(19, True)
    num_font = theme.font(56, True)
    count_font = theme.font(32, True)
    unit_font = theme.font(14, True)
    rank_d = theme.px(38)
    inset = theme.px(24)

    for i, (num, count) in enumerate(entries):
        row = pygame.Rect(rect.left, y, rect.width, row_h)
        rank_cx = row.left + inset + rank_d // 2
        ring_img = asset_scaled(rank_ring_asset, (rank_d, rank_d))
        screen.blit(ring_img, (rank_cx - rank_d // 2, row.centery - rank_d // 2))
        draw_text(screen, rank_font, str(i + 1), (rank_cx, row.centery), accent, anchor="center")

        draw_text(screen, num_font, str(num), (rank_cx + rank_d // 2 + theme.px(16), row.centery),
                  TEXT_PRIMARY, anchor="midleft")

        val_x = row.right - inset
        count_r = draw_text(screen, count_font, str(count), (val_x, row.centery - theme.px(11)),
                             accent, anchor="topright")
        draw_text(screen, unit_font, unit, (val_x, count_r.bottom + theme.px(1)), TEXT_MUTED, anchor="topright")

        if i < len(entries) - 1:
            blit_hbar(screen, "separator_fade.png",
                      pygame.Rect(rect.left + theme.px(16), row.bottom, rect.width - theme.px(32), 2))
        y = row.bottom

    if show_logo:
        logo_zone = pygame.Rect(rect.left + theme.px(14), y + theme.px(8),
                                 rect.width - theme.px(28), rect.bottom - (y + theme.px(8)) - theme.px(14))
        draw_logo(screen, theme, logo_zone, logo_path)

    return rect


def draw_logo(screen, theme: Theme, rect, image_path: str | None) -> None:
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
    y = last_rect.top + theme.px(14)
    draw_text(screen, theme.font(32, True), "ÚLTIMO RESULTADO", (last_rect.centerx, y), TEXT_PRIMARY, anchor="midtop")
    y += theme.px(48)

    last_color = color_of(last_number)
    badge_asset = {"red": "result_badge_red.png", "black": "result_badge_black.png",
                   "green": "result_badge_green.png"}[last_color]

    diameter = int(last_rect.width * 0.66)
    badge_size = int(diameter * 1.32)  # o PNG já inclui a margem da sombra/anel de accent
    badge = asset_scaled(badge_asset, (badge_size, badge_size))
    cx, cy = last_rect.centerx, y + diameter // 2
    screen.blit(badge, (cx - badge_size // 2, cy - badge_size // 2))

    num_font = theme.font(int(diameter * 0.62), True)
    blit_outlined(screen, num_font, str(last_number), (cx, cy), fill=TEXT_PRIMARY, outline=(0, 0, 0), outline_px=0)

    tag_y = cy + diameter // 2 + theme.px(30)
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

    draw_center_history(screen, theme, hist_rect, history)


def draw_center_history(screen, theme: Theme, rect: pygame.Rect, history: list[int]) -> None:
    """REGRA FUNCIONAL IMUTÁVEL: três raias -- preto esquerda, zero centro, vermelho direita --
    mais recente no topo, uma linha por giro, nunca duas listas paralelas. Cada resultado usa o
    chip pré-renderizado (glow+anel+gradiente) em vez de um círculo flat."""
    y = rect.top
    draw_text(screen, theme.font(22, True), "HISTÓRICO", (rect.centerx, y), TEXT_SECONDARY, anchor="midtop")
    y += theme.px(40)

    lane_x = {
        "black": rect.left + rect.width // 4,
        "green": rect.centerx,
        "red": rect.right - rect.width // 4,
    }
    lane_fill = {"black": TEXT_PRIMARY, "green": GREEN, "red": RED}
    chip_asset = {"black": "history_chip_black.png", "green": "history_chip_green.png", "red": "history_chip_red.png"}

    lanes_top = y
    lanes_bottom = rect.bottom - theme.px(6)
    d = theme.px(64)
    row_h = int(d * 1.18)
    n_rows = max(len(history), (lanes_bottom - lanes_top) // row_h)

    # A raia vertical é uma linha fininha reta (um gradiente horizontal não faz sentido numa linha
    # vertical) -- desenhada direto, é uma primitiva de 1px, custo desprezível mesmo em runtime.
    for x in lane_x.values():
        pygame.draw.line(screen, (24, 28, 34), (x, lanes_top), (x, lanes_top + n_rows * row_h), 1)

    hist_font = theme.font(int(d * 0.42), True)
    chip_size = int(d * 1.3)
    for i in range(n_rows):
        yy = lanes_top + i * row_h + row_h // 2
        active_lane = color_of(history[i]) if i < len(history) else None
        for lane, x in lane_x.items():
            if lane == active_lane:
                chip = asset_scaled(chip_asset[lane], (chip_size, chip_size))
                screen.blit(chip, (x - chip_size // 2, yy - chip_size // 2))
                blit_outlined(screen, hist_font, str(history[i]), (x, yy), fill=lane_fill[lane],
                              outline=(0, 0, 0), outline_px=1)
            else:
                pygame.draw.circle(screen, (50, 54, 60), (x, yy), theme.px(3))


def draw_stats(screen, theme: Theme, rect: pygame.Rect) -> None:
    draw_text(screen, theme.font(22, True), "ESTATÍSTICAS", (rect.centerx, rect.top), TEXT_SECONDARY, anchor="midtop")
    cards_top = rect.top + theme.px(38)
    blit_hbar(screen, "separator_fade.png",
              pygame.Rect(theme.px(24), cards_top - theme.px(10), rect.width - theme.px(48), 2))

    cells = [
        ("ÍMPAR", "47%", CYAN, "accent_cold.png"), ("PAR", "51%", CYAN, "accent_cold.png"),
        ("VERMELHO", "49%", RED, "accent_hot.png"), ("ZERO", "3%", GREEN, "accent_green.png"),
        ("PRETO", "48%", TEXT_PRIMARY, "accent_white.png"),
        ("MENOR", "46%", ORANGE, "accent_orange.png"), ("MAIOR", "51%", ORANGE, "accent_orange.png"),
    ]
    cell_w = theme.width / len(cells)
    gutter = theme.px(6)
    card_h = rect.bottom - cards_top
    value_font = theme.font(36, True)
    label_font = theme.font(17, True)

    for i, (label, value, accent, accent_asset) in enumerate(cells):
        outer = pygame.Rect(int(i * cell_w), cards_top, int(cell_w) + 1, card_h)
        card = outer.inflate(-gutter * 2, 0)
        radius = theme.px(8)
        blit_card_bg(screen, card, radius)
        blit_hbar(screen, accent_asset, pygame.Rect(card.left, card.top, card.width, theme.px(4)))
        pygame.draw.rect(screen, PANEL_BORDER, card, width=1, border_radius=radius)
        draw_text(screen, value_font, value, (card.centerx, card.top + theme.px(16)), accent, anchor="midtop")
        draw_text(screen, label_font, label, (card.centerx, card.top + theme.px(58)), TEXT_SECONDARY, anchor="midtop")


def build_mockup(logo_path: str | None = None) -> pygame.Surface:
    pygame.init()
    pygame.display.set_mode((1, 1))  # necessário para `.convert_alpha()` funcionar sob SDL dummy
    theme = Theme(W, H)
    screen = pygame.Surface((theme.width, theme.height))
    screen.fill(BG)

    header_h = theme.px(round(H * 0.09))
    last_h = theme.px(round(H * 0.22))
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

    frio_entries = [(9, 41), (3, 38), (28, 36), (14, 33), (31, 30)]
    draw_side_card(screen, theme, frio_col, body_top, "FRIO", "GIROS SEM SAIR", "accent_cold.png",
                    "cold_icon.png", frio_entries, "GIROS", "rank_ring_cold.png", show_logo=False)

    quente_entries = [(7, 9), (23, 8), (0, 7), (16, 6), (34, 5)]
    draw_side_card(screen, theme, quente_col, body_top, "QUENTE", "OCORRÊNCIAS", "accent_hot.png",
                    "hot_icon.png", quente_entries, "VEZES", "rank_ring_hot.png",
                    show_logo=True, logo_path=logo_path, logo_zone_h=theme.px(320))

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

    if not ASSETS_DIR.exists():
        print(f"assets ausentes em {ASSETS_DIR} -- rode `python tools/build_ui_assets.py` primeiro.")
        sys.exit(1)

    surface = build_mockup(logo_path=args.logo)
    pygame.image.save(surface, args.out)
    print(f"mockup salvo em {args.out} ({surface.get_width()}x{surface.get_height()})")


if __name__ == "__main__":
    main()
