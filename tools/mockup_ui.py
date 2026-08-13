"""Mockup estático (FASE 3 do redesign visual, v6) — reconstrução de composição/densidade visando
diretamente a imagem premium de referência do cliente (não mais o mockup anterior). Gera um PNG
1080x1920 usando assets gráficos PRÉ-RENDERIZADOS (`assets/ui/*.png`, gerados uma única vez por
`tools/build_ui_assets.py` via Pillow) para reproduzir sombras suaves, gradientes, halo dourado
controlado e profundidade de fundo -- tudo calculado uma vez em build, nunca recalculado no loop
de render. Mesma distinção pedida pelo cliente: "efeito caro recalculado 60x/s" (proibido) vs.
"efeito sofisticado calculado uma vez e reutilizado por blit" (permitido).

Mudança de composição desta rodada (vs. v4/v5): FRIO/QUENTE viram painéis de altura completa
(mesma altura combinada de "último resultado + histórico", como a referência realmente faz — nas
versões anteriores o card terminava logo após o ranking, deixando fundo cru vazio abaixo; agora o
espaço abaixo do ranking continua DENTRO do card, com iluminação ambiente sutil e, em QUENTE, a
logo real do estabelecimento). Histórico com mais entradas visíveis, chips maiores. Círculo do
resultado com halo dourado visível. 100% da lógica funcional (três raias do histórico, dados de
FRIO/QUENTE independentes, 7 categorias de estatística) permanece intacta.

Antes de rodar, gere os assets (se ainda não existirem ou se `build_ui_assets.py` mudou):
    python tools/build_ui_assets.py
    python tools/mockup_ui.py [--out CAMINHO.png] [--logo CAMINHO.png]

Sem --logo, tenta usar a logo real já presente no projeto (`assets/logo.png`) antes de cair para
um placeholder neutro. Roda com `SDL_VIDEODRIVER=dummy` — não abre janela, não toca em banco/config
real."""
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
    CYAN, RED, GREEN, ORANGE, GOLD, TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED, PANEL_BORDER, Theme,
)

W, H = 1080, 1920
ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets" / "ui"
PROJECT_LOGO = Path(__file__).resolve().parent.parent / "assets" / "logo.png"
OFF_WHITE = (0xF2, 0xF0, 0xEC)

_RED_NUMBERS = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}


def color_of(n: int) -> str:
    if n == 0:
        return "green"
    return "red" if n in _RED_NUMBERS else "black"


# -- cache de assets: cada PNG carregado do disco uma única vez; cada escala pedida (tamanho final
# em pixels) cacheada uma única vez -- mesmo padrão já usado em produção pelo logo pré-escalado e
# pelas sombras cacheadas por tamanho em `display.py`.
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
    img = asset_scaled(name, (max(1, rect.width), max(1, rect.height)))
    screen.blit(img, rect.topleft)


def blit_card_bg(screen, rect: pygame.Rect, radius: int) -> None:
    grad = asset_scaled("card_gradient.png", (rect.width, rect.height))
    mask = pygame.Surface(rect.size, pygame.SRCALPHA)
    pygame.draw.rect(mask, (255, 255, 255, 255), mask.get_rect(), border_radius=radius)
    grad = grad.copy()
    grad.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
    screen.blit(grad, rect.topleft)


def draw_fading_title(screen, theme: Theme, text, y, rect: pygame.Rect, color=TEXT_SECONDARY) -> None:
    """Título com linhas de fade dos dois lados: "── ESTATÍSTICAS ──" -- pedido explícito, usando
    o mesmo asset de linha com gradiente (`separator_fade.png`) espelhado à esquerda."""
    font = theme.font(32, True)
    label = draw_text(screen, font, text, (rect.centerx, y), color, anchor="midtop")
    gap = theme.px(18)
    line_w = (rect.width - label.width) // 2 - gap * 2
    if line_w > theme.px(20):
        line_y = y + label.height // 2 - 1
        left_line = asset_scaled("separator_fade.png", (line_w, 2))
        screen.blit(left_line, (rect.left + theme.px(24), line_y))
        right_line = pygame.transform.flip(left_line, True, False)
        screen.blit(right_line, (rect.right - theme.px(24) - line_w, line_y))


def draw_header(screen, theme: Theme, header_h: int) -> None:
    """APOSTA MÍN. no canto esquerdo, APOSTA MÁX. no canto direito, sem borda (só o fundo com
    gradiente sutil do card) e fonte ainda maior -- pedido explícito. "SISTEMA OK" saiu do topo
    (competia visualmente com os dois cards) e foi pro rodapé do próprio cabeçalho, discreto,
    colado na linha dourada."""
    card_top = theme.px(8)
    card_w, card_h = theme.px(268), theme.px(158)  # itens de aposta ainda maiores -- pedido explícito
    label_font, value_font = theme.font(34, True), theme.font(66, True)

    left_rect = pygame.Rect(theme.px(20), card_top, card_w, card_h)
    right_rect = pygame.Rect(theme.width - theme.px(20) - card_w, card_top, card_w, card_h)
    for rect, (label, value) in ((left_rect, ("APOSTA MÍN.", "R$ 5,00")),
                                  (right_rect, ("APOSTA MÁX.", "R$ 500,00"))):
        blit_card_bg(screen, rect, theme.px(10))
        draw_text(screen, label_font, label, (rect.centerx, rect.top + theme.px(18)), TEXT_SECONDARY, anchor="midtop")
        draw_text(screen, value_font, value, (rect.centerx, rect.top + theme.px(58)), ORANGE, anchor="midtop")

    dot_c = (theme.width - theme.px(22), header_h - theme.px(16))
    pygame.draw.circle(screen, GREEN, dot_c, theme.px(6))
    draw_text(screen, theme.font(17, True), "SISTEMA OK",
              (dot_c[0] - theme.px(12), dot_c[1]), TEXT_MUTED, anchor="midright")

    blit_hbar(screen, "accent_gold_glow.png", pygame.Rect(0, header_h - theme.px(10), theme.width, theme.px(20)))
    blit_hbar(screen, "accent_gold.png", pygame.Rect(0, header_h - 2, theme.width, 3))


def draw_side_panel(screen, theme: Theme, panel_rect: pygame.Rect,
                     title, subtitle, accent_asset: str, glow_asset: str, icon_asset: str,
                     entries, unit, accent_color,
                     show_logo: bool = False, logo_path: str | None = None) -> None:
    """FRIO/QUENTE: painel de ALTURA COMPLETA (`panel_rect` já vem com a altura combinada de
    último resultado + histórico) -- correção principal desta rodada. O ranking ocupa o topo; o
    espaço abaixo continua dentro do MESMO card, com iluminação ambiente sutil (e, em QUENTE, a
    logo real do estabelecimento) em vez de terminar o card cedo e deixar fundo cru vazio."""
    radius = theme.px(16)
    blit_card_bg(screen, panel_rect, radius)

    # iluminação ambiente extremamente discreta na metade inferior do painel -- pré-renderizada,
    # um único blit (alpha normal, não aditivo -- ADD deixa "queimado"/com anéis visíveis), nunca
    # recalculada.
    glow_size = int(panel_rect.width * 1.1)
    glow = asset_scaled(glow_asset, (glow_size, glow_size))
    glow_cy = panel_rect.top + int(panel_rect.height * 0.74)
    screen.blit(glow, (panel_rect.centerx - glow_size // 2, glow_cy - glow_size // 2))

    glow_bar_asset = accent_asset.replace(".png", "_glow.png")
    blit_hbar(screen, glow_bar_asset, pygame.Rect(panel_rect.left, panel_rect.top - theme.px(8),
                                                   panel_rect.width, theme.px(16)))
    blit_hbar(screen, accent_asset, pygame.Rect(panel_rect.left, panel_rect.top, panel_rect.width, theme.px(4)))
    pygame.draw.rect(screen, PANEL_BORDER, panel_rect, width=1, border_radius=radius)

    y = panel_rect.top + theme.px(24)
    title_font = theme.font(44, True)
    subtitle_font = theme.font(24, True)
    title_r = draw_text(screen, title_font, title, (panel_rect.centerx, y), accent_color, anchor="midtop")

    # Ícone centralizado verticalmente com o TÍTULO (não esticado até o subtítulo -- o subtítulo
    # agora é bem mais largo, "MENOS/MAIS RECORRENTES", e um ícone descendo até a linha dele
    # colidia visualmente com o texto). Ícone + título formam o par visual centralizado.
    icon_size = theme.px(40)
    icon_img = asset_scaled(icon_asset, (icon_size, icon_size))
    icon_x = panel_rect.centerx - title_r.width // 2 - icon_size - theme.px(10)
    screen.blit(icon_img, (icon_x, title_r.centery - icon_size // 2))

    y = title_r.bottom + theme.px(6)
    subtitle_r = draw_text(screen, subtitle_font, subtitle, (panel_rect.centerx, y), TEXT_SECONDARY, anchor="midtop")

    y = subtitle_r.bottom + theme.px(16)
    blit_hbar(screen, "separator_fade.png",
              pygame.Rect(panel_rect.left + theme.px(16), y, panel_rect.width - theme.px(32), 2))
    y += theme.px(16)

    row_h = theme.px(148)
    rank_font = theme.font(31, True)
    num_font = theme.font(40, True)
    count_font = theme.font(47, True)
    unit_font = theme.font(21, True)
    badge_d = theme.px(78)
    inset = theme.px(24)
    number_chip_asset = {"black": "number_chip_black.png", "red": "number_chip_red.png", "green": "number_chip_green.png"}

    for i, (num, count) in enumerate(entries):
        row = pygame.Rect(panel_rect.left, y, panel_rect.width, row_h)

        # Posição do ranking (1º, 2º, 3º...) fica como TEXTO simples à esquerda -- não mais
        # dentro de um círculo (pedido explícito: só o NÚMERO da roleta em si é que deve estar
        # dentro de um círculo, na cor real daquele número).
        rank_r = draw_text(screen, rank_font, f"{i + 1}º", (row.left + inset, row.centery),
                            accent_color, anchor="midleft")

        badge_cx = rank_r.right + theme.px(16) + badge_d // 2
        badge = asset_scaled(number_chip_asset[color_of(num)], (badge_d, badge_d))
        screen.blit(badge, (badge_cx - badge_d // 2, row.centery - badge_d // 2))
        blit_outlined(screen, num_font, str(num), (badge_cx, row.centery), fill=OFF_WHITE,
                      outline=(0, 0, 0), outline_px=0)

        val_x = row.right - inset
        count_r = draw_text(screen, count_font, str(count), (val_x, row.centery - theme.px(13)),
                             accent_color, anchor="topright")
        draw_text(screen, unit_font, unit, (val_x, count_r.bottom + theme.px(2)), TEXT_MUTED, anchor="topright")

        if i < len(entries) - 1:
            blit_hbar(screen, "separator_fade.png",
                      pygame.Rect(panel_rect.left + theme.px(16), row.bottom, panel_rect.width - theme.px(32), 2))
        y = row.bottom

    if show_logo:
        logo_zone = pygame.Rect(panel_rect.left + theme.px(8), y + theme.px(16),
                                 panel_rect.width - theme.px(16), panel_rect.bottom - (y + theme.px(16)) - theme.px(16))
        draw_logo(screen, theme, logo_zone, logo_path)


def draw_logo(screen, theme: Theme, rect, image_path: str | None) -> None:
    """Logo real do estabelecimento, se disponível (`--logo`, ou `assets/logo.png` do próprio
    projeto por padrão) -- ocupa a maior parte da largura interna do painel, proporção preservada,
    pré-escalada uma vez. Sem imagem, um placeholder neutro discreto (sem caixa "LOGO...")."""
    max_w = int(rect.width * 0.98)
    max_h = int(rect.height * 0.98)

    image = None
    if image_path and Path(image_path).exists():
        image = pygame.image.load(image_path).convert_alpha()
    elif PROJECT_LOGO.exists():
        image = pygame.image.load(str(PROJECT_LOGO)).convert_alpha()

    if image is not None:
        ratio = min(max_w / image.get_width(), max_h / image.get_height())
        size = (max(1, int(image.get_width() * ratio)), max(1, int(image.get_height() * ratio)))
        scaled = pygame.transform.smoothscale(image, size)
        screen.blit(scaled, scaled.get_rect(center=rect.center))
        return

    box = pygame.Rect(0, 0, min(max_w, theme.px(150)), min(max_h, theme.px(70)))
    box.center = rect.center
    pygame.draw.rect(screen, (26, 30, 36), box, border_radius=theme.px(8))
    pygame.draw.rect(screen, PANEL_BORDER, box, width=1, border_radius=theme.px(8))


def draw_center(screen, theme: Theme, content_rect: pygame.Rect,
                 last_number: int, history: list[int]) -> None:
    """Recebe a coluna central INTEIRA (não dois retângulos pré-divididos por uma proporção fixa)
    -- o histórico começa logo depois de onde as classificações realmente terminam, não numa
    altura fixa calculada sem olhar pro conteúdo real acima (bug da v6 inicial: um círculo maior
    empurrava as pills pra baixo do início "fixo" do histórico, causando sobreposição)."""
    y = content_rect.top + theme.px(10)
    title_r = draw_text(screen, theme.font(48, True), "ÚLTIMO RESULTADO", (content_rect.centerx, y),
                         OFF_WHITE, anchor="midtop")

    last_color = color_of(last_number)
    badge_asset = {"red": "result_badge_red.png", "black": "result_badge_black.png",
                   "green": "result_badge_green.png"}[last_color]

    diameter = min(int(content_rect.width * 0.86), int(theme.px(380)))
    badge_size = int(diameter * 1.60)  # o PNG já inclui a margem do halo dourado
    cx = content_rect.centerx
    # Mesma folga dos dois lados do aro: a borda externa visível do aro (~0.60*diameter do
    # centro, igual à conta já usada abaixo pras pills) fica a `theme.px(34)` do título em cima,
    # exatamente a mesma folga de `theme.px(34)` que already separa o aro das pills embaixo --
    # pedido explícito pra igualar o espaçamento acima e abaixo do círculo.
    cy = title_r.bottom + theme.px(34) + int(diameter * 0.60)
    badge = asset_scaled(badge_asset, (badge_size, badge_size))
    screen.blit(badge, (cx - badge_size // 2, cy - badge_size // 2))

    num_font = theme.font(int(diameter * 0.76), True)
    blit_outlined(screen, num_font, str(last_number), (cx, cy), fill=OFF_WHITE, outline=(0, 0, 0), outline_px=0)

    # O aro dourado se estende bem além do círculo preenchido (halo/bisel do asset) -- medir a
    # folga a partir de `diameter//2` (a borda do preenchimento) subestimava o quanto o aro
    # realmente ocupa, deixando as classificações quase coladas nele. `0.60*diameter` aproxima
    # melhor a borda externa visível do aro.
    tag_y = cy + int(diameter * 0.60) + theme.px(34)
    tags = [("PRETO" if last_color == "black" else "VERMELHO" if last_color == "red" else "ZERO",
             OFF_WHITE if last_color == "black" else RED if last_color == "red" else GREEN)]
    if last_number != 0:
        tags.append(("ÍMPAR" if last_number % 2 else "PAR", CYAN))
        tags.append(("MENOR" if last_number <= 18 else "MAIOR", ORANGE))

    pill_font = theme.font(26, True)
    pill_gap = theme.px(10)
    pill_h = theme.px(42)
    widths = [pill_font.size(label)[0] + theme.px(28) for label, _ in tags]
    px = content_rect.centerx - (sum(widths) + pill_gap * (len(tags) - 1)) // 2
    for (label, color), pw in zip(tags, widths):
        pill = pygame.Rect(px, tag_y, pw, pill_h)
        blit_card_bg(screen, pill, theme.px(18))
        pygame.draw.rect(screen, color, pill, width=2, border_radius=theme.px(18))
        draw_text(screen, pill_font, label, pill.center, color, anchor="center")
        px += pw + pill_gap

    hist_top = tag_y + pill_h + theme.px(18)
    hist_rect = pygame.Rect(content_rect.left, hist_top, content_rect.width, content_rect.bottom - hist_top)
    draw_center_history(screen, theme, hist_rect, history)


NODE_GOLD = (214, 178, 104)
CONNECTOR_GOLD = (196, 160, 92)


def draw_center_history(screen, theme: Theme, rect: pygame.Rect, history: list[int]) -> None:
    """REGRA FUNCIONAL IMUTÁVEL: três raias -- preto esquerda, zero centro, vermelho direita --
    mais recente no topo, uma linha por giro, nunca duas listas paralelas (continuam
    INDEPENDENTES, nunca interligadas numa linha central -- só o ACABAMENTO de cada raia mudou
    pra dourado). Cada raia é uma linha dourada fina com fade nas duas pontas (reaproveita
    `accent_gold.png`, que já esmaece nas bordas, rotacionado 90°), com um nó dourado em cada
    posição -- ligado à sua bolinha por um traço horizontal curto, igual à referência premium
    pedida. Chips com o bisel dourado/cores cheias da v8; numerais SEMPRE off-white, independente
    da cor do chip."""
    y = rect.top
    draw_text(screen, theme.font(32, True), "HISTÓRICO", (rect.centerx, y), TEXT_SECONDARY, anchor="midtop")
    y += theme.px(40)

    lane_x = {
        "black": rect.left + rect.width // 4,
        "green": rect.centerx,
        "red": rect.right - rect.width // 4,
    }
    chip_asset = {"black": "history_chip_black.png", "green": "history_chip_green.png", "red": "history_chip_red.png"}

    lanes_top = y
    lanes_bottom = rect.bottom  # sem selo "MAIS RECENTE" embaixo, a raia usa o espaço inteiro
    d = theme.px(76)  # +15% pedido explícito (era 66)
    row_h = int(d * 1.28)
    # Igual à regra real ("quantas linhas couberem"): nunca força mais linhas do que cabem no
    # espaço disponível.
    n_rows = max(1, (lanes_bottom - lanes_top) // row_h)
    history = history[:n_rows]

    spine_h = n_rows * row_h
    fade_src = asset_scaled("accent_gold.png", (spine_h, theme.px(3)))
    fade_img = pygame.transform.rotate(fade_src, 90)
    for x in lane_x.values():
        screen.blit(fade_img, (x - fade_img.get_width() // 2, lanes_top))

    hist_font = theme.font(int(d * 0.54), True)
    chip_size = int(d * 1.30)
    node_r = theme.px(4)
    connector_w = theme.px(2)
    for i in range(n_rows):
        yy = lanes_top + i * row_h + row_h // 2
        n = history[i] if i < len(history) else None
        active_lane = color_of(n) if n is not None else None
        for lane, x in lane_x.items():
            is_active = lane == active_lane
            # Conector horizontal curto ligando o nó da raia à bolinha -- pra posições ativas,
            # mais largo que o chip pra "espiar" nas laterais dele (o chip cobre o meio); pra
            # posições vazias, só um traço pequeno junto ao nó.
            tick_half = (chip_size // 2 + theme.px(9)) if is_active else theme.px(10)
            pygame.draw.line(screen, CONNECTOR_GOLD, (x - tick_half, yy), (x + tick_half, yy), connector_w)
            pygame.draw.circle(screen, NODE_GOLD, (x, yy), node_r)
            if is_active:
                chip = asset_scaled(chip_asset[lane], (chip_size, chip_size))
                screen.blit(chip, (x - chip_size // 2, yy - chip_size // 2))
                blit_outlined(screen, hist_font, str(n), (x, yy), fill=OFF_WHITE, outline=(0, 0, 0), outline_px=1)


def draw_stats(screen, theme: Theme, rect: pygame.Rect) -> None:
    """Pedido explícito do cliente, com print de referência anexado: volta a 7 cards INDIVIDUAIS
    (substitui o reagrupamento em pares da rodada anterior) -- cada card com borda na cor da
    própria categoria, a contagem bruta "141 / 300" abaixo do rótulo, e uma barra de progresso
    preenchida na mesma cor. Título ganha o sufixo "ÚLTIMOS 300 GIROS" ladeado por linhas
    douradas com um ponto em cada ponta. Mesmas 7 categorias/percentuais de sempre -- a contagem
    "X / 300" é só pct% de uma amostra fixa de 300 giros (consistente com o percentual já
    mostrado), nenhum dado novo. Os separadores "·" são desenhados como círculos, não o glifo
    Unicode -- a fonte padrão embutida do pygame não garante esse glifo."""
    title_font = theme.font(30, True)
    subtitle_font = theme.font(24, True)
    title_s = title_font.render("ESTATÍSTICAS", True, TEXT_PRIMARY)
    subtitle_s = subtitle_font.render("ÚLTIMOS 300 GIROS", True, TEXT_SECONDARY)
    mid_gap = theme.px(16)
    dot_r = theme.px(3)
    total_w = title_s.get_width() + mid_gap * 2 + dot_r * 2 + subtitle_s.get_width()
    max_h = max(title_s.get_height(), subtitle_s.get_height())
    y = rect.top
    x = rect.centerx - total_w // 2
    screen.blit(title_s, (x, y + (max_h - title_s.get_height()) // 2))
    x += title_s.get_width() + mid_gap
    pygame.draw.circle(screen, GOLD, (x + dot_r, y + max_h // 2), dot_r)
    x += dot_r * 2 + mid_gap
    screen.blit(subtitle_s, (x, y + (max_h - subtitle_s.get_height()) // 2))

    line_y = y + max_h // 2
    edge_gap = theme.px(22)
    line_w = (rect.width - total_w) // 2 - edge_gap - theme.px(24)
    if line_w > theme.px(20):
        gold_line = asset_scaled("accent_gold.png", (line_w, 3))
        left_x = rect.centerx - total_w // 2 - edge_gap
        right_x = rect.centerx + total_w // 2 + edge_gap
        screen.blit(gold_line, (left_x - line_w, line_y - 1))
        screen.blit(pygame.transform.flip(gold_line, True, False), (right_x, line_y - 1))
        pygame.draw.circle(screen, GOLD, (left_x - theme.px(8), line_y), dot_r)
        pygame.draw.circle(screen, GOLD, (right_x + theme.px(8), line_y), dot_r)

    cards_top = y + max_h + theme.px(22)
    gutter = theme.px(7)
    radius = theme.px(12)
    unit_w = theme.width / 7
    card_h = theme.px(150)

    SAMPLE_N = 300
    cells = [
        ("ÍMPAR", 47, CYAN, "accent_cold.png"), ("PAR", 51, CYAN, "accent_cold.png"),
        ("VERMELHO", 49, RED, "accent_hot.png"), ("ZERO", 3, GREEN, "accent_green.png"),
        ("PRETO", 48, OFF_WHITE, "accent_white.png"),
        ("MENOR", 46, ORANGE, "accent_orange.png"), ("MAIOR", 51, ORANGE, "accent_orange.png"),
    ]
    value_font = theme.font(38, True)
    label_font = theme.font(17, True)
    frac_font = theme.font(19, True)

    for i, (label, pct, accent, accent_asset) in enumerate(cells):
        outer = pygame.Rect(round(i * unit_w), cards_top, round(unit_w) + 1, card_h)
        card = outer.inflate(-gutter * 2, 0)
        blit_card_bg(screen, card, radius)
        pygame.draw.rect(screen, accent, card, width=2, border_radius=radius)

        draw_text(screen, value_font, f"{pct}%", (card.centerx, card.top + theme.px(14)), accent, anchor="midtop")
        draw_text(screen, label_font, label, (card.centerx, card.top + theme.px(58)), TEXT_SECONDARY, anchor="midtop")
        count = round(pct / 100 * SAMPLE_N)
        draw_text(screen, frac_font, f"{count} / {SAMPLE_N}", (card.centerx, card.top + theme.px(82)),
                  TEXT_PRIMARY, anchor="midtop")

        bar_h = theme.px(6)
        bar_rect = pygame.Rect(card.left + theme.px(14), card.top + theme.px(118), card.width - theme.px(28), bar_h)
        pygame.draw.rect(screen, (46, 50, 56), bar_rect, border_radius=bar_h // 2)
        fill_w = max(bar_h, int(bar_rect.width * min(1.0, pct / 100)))
        pygame.draw.rect(screen, accent, pygame.Rect(bar_rect.left, bar_rect.top, fill_w, bar_h),
                          border_radius=bar_h // 2)


def build_mockup(logo_path: str | None = None) -> pygame.Surface:
    pygame.init()
    pygame.display.set_mode((1, 1))  # necessário para `.convert_alpha()` funcionar sob SDL dummy
    theme = Theme(W, H)
    screen = pygame.Surface((theme.width, theme.height))
    bg = asset_scaled("background.png", (theme.width, theme.height))
    screen.blit(bg, (0, 0))

    header_h = theme.px(round(H * 0.105))
    stats_h = theme.px(round(H * 0.155))
    gap = theme.px(12)

    draw_header(screen, theme, header_h)

    body_top = header_h + gap
    stats_top = theme.height - stats_h
    body_bottom = stats_top - gap

    col_frio_w = round(theme.width * 0.28)
    col_quente_w = round(theme.width * 0.28)
    col_center_w = theme.width - col_frio_w - col_quente_w

    side_pad = theme.px(10)
    frio_panel = pygame.Rect(0, body_top, col_frio_w, body_bottom - body_top).inflate(-side_pad * 2, 0)
    quente_panel = pygame.Rect(col_frio_w + col_center_w, body_top, col_quente_w, body_bottom - body_top).inflate(-side_pad * 2, 0)
    center_col = pygame.Rect(col_frio_w, body_top, col_center_w, body_bottom - body_top)

    frio_entries = [(9, 41), (3, 38), (28, 36), (14, 33), (31, 30)]
    draw_side_panel(screen, theme, frio_panel, "FRIO", "MENOS RECORRENTES", "accent_cold.png",
                     "ambient_glow_blue.png", "cold_icon.png", frio_entries, "GIROS",
                     CYAN, show_logo=False)

    quente_entries = [(7, 9), (23, 8), (0, 7), (16, 6), (34, 5)]
    draw_side_panel(screen, theme, quente_panel, "QUENTE", "MAIS RECORRENTES", "accent_hot.png",
                     "ambient_glow_red.png", "hot_icon.png", quente_entries, "VEZES",
                     RED, show_logo=True, logo_path=logo_path)

    center_content = pygame.Rect(center_col.left + theme.px(8), center_col.top,
                                  center_col.width - theme.px(16), center_col.height)
    # 17, 32, 22, 0, 11, 5, 14 (exemplo do cliente) + continuação plausível, pra ocupar as ~10-14
    # posições que a área central comporta, como uma mesa em operação real teria.
    history_sample = [17, 32, 22, 0, 11, 5, 14, 9, 26, 3, 31, 20, 15, 8, 27, 4, 19, 36]
    draw_center(screen, theme, center_content, last_number=17, history=history_sample)

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
