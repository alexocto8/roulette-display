"""Gera os assets gráficos pré-renderizados usados pelo mockup/redesign (`assets/ui/*.png`).

Roda UMA VEZ (build/instalação), nunca durante o loop de render -- é exatamente a distinção que o
cliente pediu: "não recalcular efeito caro continuamente" vs. "calcular uma vez e reutilizar por
blit". Usa Pillow, que já é dependência de produção do projeto (`requirements.txt`, usada hoje para
validar/redimensionar a logo do cliente e gerar PDFs de relatório) -- com wheel pré-compilada para
armv7/aarch64, ou seja, roda no Raspberry Pi 3 sem compilar nada, exatamente como já acontece hoje.

Técnica usada para anti-aliasing de alta qualidade sem depender de um parser SVG (cairosvg/svglib
não são dependências do projeto e adicioná-las só para 6 ícones simples seria desproporcional):
supersampling -- cada asset é desenhado numa resolução bem maior que o tamanho final e reduzido
uma vez com `Image.LANCZOS`, técnica explicitamente autorizada pelo cliente ("renderizar em
resolução 2x e reduzir uma única vez durante build é aceitável").

Sombra/glow usam `ImageFilter.GaussianBlur` -- também só no build, nunca em runtime.

Uso:
    python tools/build_ui_assets.py
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

OUT_DIR = Path(__file__).resolve().parent.parent / "assets" / "ui"
SS = 4  # fator de supersampling (desenha em 4x, reduz 1x no final)


def _new(size: tuple[int, int]) -> Image.Image:
    return Image.new("RGBA", (size[0] * SS, size[1] * SS), (0, 0, 0, 0))


def _down(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    return img.resize(size, Image.LANCZOS)


def _lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(len(a)))


# -- ícones ---------------------------------------------------------------------------------

def make_snowflake_icon(color, size=64) -> Image.Image:
    img = _new((size, size))
    d = ImageDraw.Draw(img)
    cx, cy = img.size[0] / 2, img.size[1] / 2
    r = img.size[0] * 0.42
    w = max(2, int(img.size[0] * 0.045))
    for angle_deg in (0, 60, 120):
        rad = math.radians(angle_deg)
        dx, dy = math.cos(rad) * r, math.sin(rad) * r
        d.line([(cx - dx, cy - dy), (cx + dx, cy + dy)], fill=(*color, 255), width=w)
        # pequenos traços nas pontas, como cristais de gelo
        for sign in (-1, 1):
            tip = (cx + sign * dx, cy + sign * dy)
            perp = math.radians(angle_deg + 90)
            tl = r * 0.22
            p1 = (tip[0] + math.cos(perp) * tl, tip[1] + math.sin(perp) * tl)
            p2 = (tip[0] - math.cos(perp) * tl, tip[1] - math.sin(perp) * tl)
            d.line([p1, tip], fill=(*color, 255), width=w)
            d.line([p2, tip], fill=(*color, 255), width=w)
    return _down(img, (size, size))


def make_flame_icon(dark, bright, size=64) -> Image.Image:
    img = _new((size, size))
    W, H = img.size
    pts = [
        (0.5, 0.02), (0.82, 0.42), (0.68, 0.40), (0.90, 1.0),
        (0.5, 0.80), (0.10, 1.0), (0.32, 0.40), (0.18, 0.42),
    ]
    poly = [(x * W, y * H) for x, y in pts]
    mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(mask).polygon(poly, fill=255)
    grad = Image.new("RGBA", (W, H))
    for y in range(H):
        t = y / H  # topo (ponta) claro -> base escuro
        color = _lerp(bright, dark, t)
        for x in range(W):
            grad.putpixel((x, y), (*color, 255))
    img.paste(grad, (0, 0), mask)
    return _down(img, (size, size))


# -- círculo do último resultado / chips do histórico ----------------------------------------

def make_result_badge(diameter, fill, ring_tone, accent, shadow_alpha=110) -> Image.Image:
    """Composição completa: sombra suave -> anel de accent sutil -> anel escuro -> preenchimento
    com leve gradiente radial -> highlight superior discreto. Usada tanto pro círculo grande do
    último resultado quanto (num diâmetro menor) pros chips do histórico -- mesma função, só o
    tamanho muda, pra manter consistência visual entre os dois."""
    pad = int(diameter * 0.16)
    canvas = diameter + pad * 2
    img = Image.new("RGBA", (canvas * SS, canvas * SS), (0, 0, 0, 0))
    cx = cy = canvas * SS / 2
    r = diameter * SS / 2

    # 1. sombra externa suave (blur, não recalculada em runtime)
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).ellipse([cx - r, cy - r + SS * 3, cx + r, cy + r + SS * 3],
                                    fill=(0, 0, 0, shadow_alpha))
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=diameter * 0.05 * SS))
    img.alpha_composite(shadow)

    # 2. anel de accent sutil (levemente maior que o círculo principal)
    ring_r = r + SS * 5
    ImageDraw.Draw(img).ellipse([cx - ring_r, cy - ring_r, cx + ring_r, cy + ring_r],
                                 outline=(*accent, 130), width=max(1, SS))

    # 3. anel escuro fino
    ImageDraw.Draw(img).ellipse([cx - r, cy - r, cx + r, cy + r], outline=(*ring_tone, 255), width=SS * 2)

    # 4. preenchimento com gradiente radial sutil (centro pouco mais claro que a borda)
    center_tone = _lerp(fill, (255, 255, 255), 0.10)
    fill_img = Image.new("RGBA", img.size, (0, 0, 0, 0))
    fd = ImageDraw.Draw(fill_img)
    steps = 24
    for i in range(steps, 0, -1):
        t = i / steps
        rr = r * t
        color = _lerp(fill, center_tone, 1 - t)
        fd.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=(*color, 255))
    img.alpha_composite(fill_img)

    # 5. highlight discreto (arco claro perto do topo, bem sutil -- "vidro", não "neon")
    hl = Image.new("RGBA", img.size, (0, 0, 0, 0))
    hd = ImageDraw.Draw(hl)
    hl_w, hl_h = r * 1.15, r * 0.55
    hd.ellipse([cx - hl_w / 2, cy - r * 0.78, cx + hl_w / 2, cy - r * 0.78 + hl_h],
               fill=(255, 255, 255, 26))
    hl = hl.filter(ImageFilter.GaussianBlur(radius=diameter * 0.03 * SS))
    img.alpha_composite(hl)

    return _down(img, (canvas, canvas))


# -- barras/linhas de accent com gradiente (transparente -> cor -> vivo -> cor -> transparente) --

def make_accent_bar(width, height, dark, vivid) -> Image.Image:
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    cx = width / 2
    for x in range(width):
        d = abs(x - cx) / cx  # 0 no centro, 1 nas pontas
        # alfa: sobe rápido saindo da ponta, plateau no meio
        alpha = int(255 * max(0.0, 1 - d ** 1.6))
        color = _lerp(vivid, dark, min(1.0, d * 1.3))
        for y in range(height):
            img.putpixel((x, y), (*color, alpha))
    return img


def make_card_gradient_tile(width, height, top, bottom) -> Image.Image:
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    for y in range(height):
        t = y / max(1, height - 1)
        color = _lerp(top, bottom, t)
        for x in range(width):
            img.putpixel((x, y), (*color, 255))
    return img


def make_rank_ring(diameter, accent) -> Image.Image:
    """Anel fino do selo de ranking (①②③...) com um leve highlight -- pequeno demais pra
    precisar de sombra própria, mas ainda com antialiasing de verdade via supersampling."""
    img = Image.new("RGBA", (diameter * SS, diameter * SS), (0, 0, 0, 0))
    cx = cy = diameter * SS / 2
    r = diameter * SS / 2 - SS
    d = ImageDraw.Draw(img)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(*accent, 220), width=SS)
    hl_r = r * 0.55
    d.ellipse([cx - hl_r, cy - r * 0.55, cx + hl_r, cy - r * 0.15], fill=(255, 255, 255, 14))
    return _down(img, (diameter, diameter))


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    BLUE = (0x00, 0xA8, 0xFF)
    RED = (0xFF, 0x39, 0x39)
    GREEN = (0x00, 0xD8, 0x75)
    GRAY_85 = (38, 38, 38)
    GOLD = (196, 160, 92)
    GOLD_DARK = (90, 74, 44)
    RED_DARK = (90, 20, 18)
    RED_BRIGHT = (255, 90, 82)
    BLUE_DARK = (10, 40, 70)
    BLUE_BRIGHT = (60, 170, 255)
    WHITE = (240, 240, 240)
    GRAY_LINE = (120, 126, 132)

    make_snowflake_icon(BLUE, size=64).save(OUT_DIR / "cold_icon.png")
    make_flame_icon((150, 20, 10), (255, 150, 40), size=64).save(OUT_DIR / "hot_icon.png")

    make_result_badge(320, fill=RED, ring_tone=(255, 130, 122), accent=GOLD).save(OUT_DIR / "result_badge_red.png")
    make_result_badge(320, fill=GRAY_85, ring_tone=(110, 112, 118), accent=GOLD).save(OUT_DIR / "result_badge_black.png")
    make_result_badge(320, fill=GREEN, ring_tone=(90, 235, 160), accent=GOLD).save(OUT_DIR / "result_badge_green.png")

    make_result_badge(72, fill=RED, ring_tone=(170, 50, 46), accent=(170, 50, 46), shadow_alpha=70).save(OUT_DIR / "history_chip_red.png")
    make_result_badge(72, fill=GRAY_85, ring_tone=(110, 112, 116), accent=(110, 112, 116), shadow_alpha=70).save(OUT_DIR / "history_chip_black.png")
    make_result_badge(72, fill=GREEN, ring_tone=(20, 130, 80), accent=(20, 130, 80), shadow_alpha=70).save(OUT_DIR / "history_chip_green.png")

    make_accent_bar(420, 6, BLUE_DARK, BLUE_BRIGHT).save(OUT_DIR / "accent_cold.png")
    make_accent_bar(420, 6, RED_DARK, RED_BRIGHT).save(OUT_DIR / "accent_hot.png")
    make_accent_bar(420, 4, GOLD_DARK, GOLD).save(OUT_DIR / "accent_gold.png")
    make_accent_bar(420, 4, (10, 60, 40), GREEN).save(OUT_DIR / "accent_green.png")
    make_accent_bar(420, 4, (70, 70, 70), WHITE).save(OUT_DIR / "accent_white.png")
    make_accent_bar(420, 4, (110, 70, 10), (0xF5, 0xA0, 0x00)).save(OUT_DIR / "accent_orange.png")
    make_accent_bar(420, 2, (30, 34, 40), GRAY_LINE).save(OUT_DIR / "separator_fade.png")

    make_card_gradient_tile(8, 320, (16, 20, 26), (10, 13, 18)).save(OUT_DIR / "card_gradient.png")

    make_rank_ring(38, BLUE).save(OUT_DIR / "rank_ring_cold.png")
    make_rank_ring(38, RED).save(OUT_DIR / "rank_ring_hot.png")

    print(f"assets gerados em {OUT_DIR}")


if __name__ == "__main__":
    main()
