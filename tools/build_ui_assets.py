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
    """Floco de neve de 6 pontas com DOIS pares de galhos por braço (não só um V na ponta) e
    pontas arredondadas -- pedido explícito ("melhore o ícone"), mais próximo de um cristal de
    gelo de verdade do que a versão anterior (um X de 3 linhas com um único par de traços)."""
    img = _new((size, size))
    d = ImageDraw.Draw(img)
    cx, cy = img.size[0] / 2, img.size[1] / 2
    r = img.size[0] * 0.44
    w = max(2, int(img.size[0] * 0.05))
    w_branch = max(2, int(w * 0.78))
    cap = w / 2
    branch_cap = w_branch / 2

    def dot(pt, radius):
        d.ellipse([pt[0] - radius, pt[1] - radius, pt[0] + radius, pt[1] + radius], fill=(*color, 255))

    for angle_deg in range(0, 360, 60):
        rad = math.radians(angle_deg)
        ux, uy = math.cos(rad), math.sin(rad)
        tip = (cx + ux * r, cy + uy * r)
        d.line([(cx, cy), tip], fill=(*color, 255), width=w)
        dot(tip, cap)

        perp = math.radians(angle_deg + 90)
        pux, puy = math.cos(perp), math.sin(perp)
        for frac, branch_len in ((0.52, r * 0.26), (0.80, r * 0.20)):
            bx, by = cx + ux * r * frac, cy + uy * r * frac
            for sign in (-1, 1):
                bx2, by2 = bx + sign * pux * branch_len, by + sign * puy * branch_len
                d.line([(bx, by), (bx2, by2)], fill=(*color, 255), width=w_branch)
                dot((bx2, by2), branch_cap)

    dot((cx, cy), w * 0.85)
    return _down(img, (size, size))


def _flame_mask(W, H, pts) -> Image.Image:
    poly = [(x * W, y * H) for x, y in pts]
    mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(mask).polygon(poly, fill=255)
    return mask.filter(ImageFilter.GaussianBlur(radius=W * 0.006))


def make_flame_icon(dark, bright, core, size=64) -> Image.Image:
    """Silhueta de chama de duas camadas (corpo externo + núcleo mais claro), pontos suavizados
    pra ficar mais próxima do ícone da referência do que a versão anterior (poucos pontos retos)."""
    img = _new((size, size))
    W, H = img.size

    outer_pts = [
        (0.50, 0.00), (0.74, 0.22), (0.86, 0.48), (0.88, 0.70),
        (0.78, 0.90), (0.60, 1.00), (0.50, 0.96), (0.40, 1.00),
        (0.22, 0.90), (0.12, 0.70), (0.14, 0.46), (0.28, 0.24),
        (0.38, 0.34), (0.44, 0.16),
    ]
    grad = Image.new("RGBA", (W, H))
    for y in range(H):
        t = y / H
        color = _lerp(bright, dark, t)
        for x in range(W):
            grad.putpixel((x, y), (*color, 255))
    img.paste(grad, (0, 0), _flame_mask(W, H, outer_pts))

    inner_pts = [
        (0.50, 0.30), (0.64, 0.48), (0.68, 0.66), (0.58, 0.86),
        (0.50, 0.92), (0.42, 0.86), (0.34, 0.66), (0.38, 0.48),
    ]
    inner_grad = Image.new("RGBA", (W, H))
    for y in range(H):
        t = y / H
        color = _lerp((255, 235, 150), core, min(1.0, t * 1.3))
        for x in range(W):
            inner_grad.putpixel((x, y), (*color, 255))
    inner_mask = _flame_mask(W, H, inner_pts)
    inner_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    inner_layer.paste(inner_grad, (0, 0), inner_mask)
    img.alpha_composite(inner_layer)

    return _down(img, (size, size))


# -- círculo do último resultado / chips do histórico ----------------------------------------

def make_result_badge(diameter, fill, ring_tone, accent, shadow_alpha=110, gold_halo=False,
                       double_ring=False) -> Image.Image:
    """Composição completa: sombra suave -> halo dourado (opcional) -> bisel dourado grosso ->
    preenchimento com leve gradiente radial (cor CHEIA/saturada, sem diluir em cinza) -> highlight
    superior discreto. Usada tanto pro círculo grande do último resultado (`gold_halo=True,
    double_ring=True`) quanto pros chips do histórico (`gold_halo=True`, sem o segundo anel/brilho,
    que é sutil demais pra fazer sentido num círculo pequeno)."""
    pad = int(diameter * (0.34 if double_ring else 0.30 if gold_halo else 0.16))
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

    # 1.5 halo dourado suave (só no círculo principal) -- um anel espesso desenhado e depois
    # borrado bastante, pra criar o "glow" quente ao redor do círculo que a referência usa. Ainda
    # é só um blur no BUILD, nunca em runtime.
    if gold_halo:
        # Posicionado JUNTO DA BORDA EXTERNA do bisel (não mais perto do preenchimento) e com
        # blur bem menor -- um halo desfocado demais "engolia" o vão entre o preenchimento e o
        # bisel e escondia o anel fino duplo (bug encontrado nesta rodada: o glow tinha blur
        # ~23px finais, maior que o próprio vão de ~9px, vazando pra dentro dele).
        halo = Image.new("RGBA", img.size, (0, 0, 0, 0))
        halo_r = r + SS * 38
        ImageDraw.Draw(halo).ellipse([cx - halo_r, cy - halo_r, cx + halo_r, cy + halo_r],
                                      outline=(*accent, 190), width=int(SS * 14))
        halo = halo.filter(ImageFilter.GaussianBlur(radius=diameter * 0.035 * SS))
        img.alpha_composite(halo)

    # 2. bezel dourado GROSSO (não uma linha fina) -- anel metálico com gradiente radial (mais
    # escuro na borda externa, mais claro perto do círculo) + um arco de brilho na metade
    # superior simulando luz vinda de cima, igual a um bisel de moeda/medalha física. Deixa um
    # vão (sem pintar nada) entre o preenchimento (raio `r`) e o bisel -- é esse vão escuro que
    # cria a separação visual "círculo preto real" -> "anel dourado", em vez de um anel colado.
    # Vão 1: espaço em branco (transparente) logo depois do preenchimento -- é o que separa
    # visualmente "círculo preto real" do anel dourado, em vez dos dois ficarem colados.
    ring1_r = r + SS * 9
    ring1_w = SS * 2
    # Vão 2: outro espaço em branco antes do bisel grosso começar -- sem isso, a borda mais clara
    # do próprio gradiente do bisel (que já começa dourado bem claro) fica colada no anel fino e
    # os dois se misturam visualmente num anel só.
    bezel_inner = ring1_r + ring1_w + SS * 10
    if gold_halo:
        bezel_outer = bezel_inner + SS * 15
        band = Image.new("RGBA", img.size, (0, 0, 0, 0))
        bd = ImageDraw.Draw(band)
        gold_dark = _lerp(accent, (40, 30, 10), 0.45)
        gold_light = _lerp(accent, (255, 245, 210), 0.55)
        steps = 26
        for i in range(steps, -1, -1):
            t = i / steps  # 1 na borda externa -> 0 na borda interna
            rr = bezel_inner + (bezel_outer - bezel_inner) * t
            color = _lerp(gold_light, gold_dark, t)
            bd.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=(*color, 255))
        # `ellipse(fill=...)` sempre preenche o disco INTEIRO até aquele raio -- sem isso, o menor
        # círculo do loop (raio `bezel_inner`) deixava um disco dourado sólido colado no
        # preenchimento em vez de um anel oco, escondendo o vão/anel duplo (bug real encontrado
        # nesta rodada). `fill=(0,0,0,0)` sobrescreve os pixels direto (sem blending), então isso
        # apaga de verdade o miolo em vez de só desenhar transparência por cima.
        bd.ellipse([cx - bezel_inner, cy - bezel_inner, cx + bezel_inner, cy + bezel_inner], fill=(0, 0, 0, 0))
        # brilho superior (luz vinda de cima) -- arco largo e claro, só no terço de cima do bisel
        bezel_mid = (bezel_outer + bezel_inner) / 2
        bw = bezel_outer - bezel_inner
        sheen = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(sheen).arc([cx - bezel_mid, cy - bezel_mid, cx + bezel_mid, cy + bezel_mid],
                                   200, 340, fill=(255, 250, 225, 235), width=int(bw * 0.85))
        sheen = sheen.filter(ImageFilter.GaussianBlur(radius=bw * 0.18))
        band.alpha_composite(sheen)
        # perde a suavidade da borda externa/interna do bisel só um pouquinho, pra não ficar um
        # disco "cortado a laser" -- blur bem pequeno, é só antialiasing extra.
        band = band.filter(ImageFilter.GaussianBlur(radius=SS * 0.6))
        img.alpha_composite(band)

        # 2.5 segundo anel, fino e brilhante, concêntrico, DENTRO do vão -- só no círculo
        # principal (chips pequenos não têm espaço/necessidade pra um anel duplo). Um pequeno
        # brilho pontual ("glint") simula reflexo de luz numa superfície polida.
        if double_ring:
            ImageDraw.Draw(img).ellipse(
                [cx - ring1_r, cy - ring1_r, cx + ring1_r, cy + ring1_r],
                outline=(255, 235, 190, 235), width=ring1_w)

            glint = Image.new("RGBA", img.size, (0, 0, 0, 0))
            glint_r = bezel_mid
            glint_angle = math.radians(215)
            gx = cx + math.cos(glint_angle) * glint_r
            gy = cy + math.sin(glint_angle) * glint_r
            gs = bw * 0.9
            ImageDraw.Draw(glint).ellipse([gx - gs, gy - gs, gx + gs, gy + gs], fill=(255, 255, 255, 235))
            glint = glint.filter(ImageFilter.GaussianBlur(radius=gs * 0.35))
            img.alpha_composite(glint)
    else:
        ring_r = r + SS * 5
        ImageDraw.Draw(img).ellipse([cx - ring_r, cy - ring_r, cx + ring_r, cy + ring_r],
                                     outline=(*accent, 130), width=max(1, SS))
        # anel escuro fino -- só usado nessa variante simples (sem bisel dourado)
        ImageDraw.Draw(img).ellipse([cx - r, cy - r, cx + r, cy + r], outline=(*ring_tone, 255), width=SS * 2)

    # 4. preenchimento com gradiente radial (centro mais claro, borda mais escura) -- cor CHEIA,
    # saturada (a referência usa preto/vermelho/verde de verdade, não uma versão diluída/cinza);
    # o contraste contra o fundo escuro do app vem do bisel dourado, não mais de clarear o
    # preenchimento como a v1-v7 faziam pro "preto".
    # Sombreamento bem mais discreto que a v8 -- o centro estava clareando demais e o preto lia
    # como cinza em vez de preto de verdade (pedido explícito do cliente pra corrigir).
    center_tone = _lerp(fill, (255, 255, 255), 0.06)
    edge_tone = _lerp(fill, (0, 0, 0), 0.22)
    fill_img = Image.new("RGBA", img.size, (0, 0, 0, 0))
    fd = ImageDraw.Draw(fill_img)
    steps = 28
    for i in range(steps, 0, -1):
        t = i / steps
        rr = r * t
        color = _lerp(edge_tone, center_tone, 1 - t)
        fd.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=(*color, 255))
    img.alpha_composite(fill_img)

    # 5. highlight discreto (arco claro perto do topo, bem sutil -- "vidro", não "neon")
    hl = Image.new("RGBA", img.size, (0, 0, 0, 0))
    hd = ImageDraw.Draw(hl)
    hl_w, hl_h = r * 1.15, r * 0.55
    hd.ellipse([cx - hl_w / 2, cy - r * 0.78, cx + hl_w / 2, cy - r * 0.78 + hl_h],
               fill=(255, 255, 255, 13))
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


def make_accent_glow(width, height, color) -> Image.Image:
    """Versão borrada/maior da barra de accent, pra blitar POR BAIXO da barra nítida -- dá o
    "glow sutil" que a referência tem no topo dos painéis FRIO/QUENTE, sem custo em runtime (só
    mais um blit, a mesma técnica de sombra/halo já usada em todo o resto dos assets)."""
    bar = make_accent_bar(width, height, dark=(0, 0, 0), vivid=color)
    canvas = Image.new("RGBA", (width, height * 6), (0, 0, 0, 0))
    canvas.alpha_composite(bar, (0, height * 3 - height // 2))
    canvas = canvas.filter(ImageFilter.GaussianBlur(radius=height * 1.4))
    return canvas


def make_card_gradient_tile(width, height, top, bottom) -> Image.Image:
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    for y in range(height):
        t = y / max(1, height - 1)
        color = _lerp(top, bottom, t)
        for x in range(width):
            img.putpixel((x, y), (*color, 255))
    return img


def make_simple_chip(diameter, fill, gold, shadow_alpha=90) -> Image.Image:
    """Bolinha simples: sombra sutil -> preenchimento com a cor real (mesmo sombreamento discreto
    do círculo principal) -> UMA borda fina dourada logo depois do limite da cor (sem bisel
    grosso, sem halo/glow, sem anel duplo) -- pedido explícito: "não precisam ter a borda dourada
    grossa, apenas borda fina dourada logo após o limite da cor". Usada tanto pros chips do
    histórico quanto pro selo colorido de cada número em FRIO/QUENTE (mesmo asset, tamanhos
    diferentes)."""
    pad = int(diameter * 0.14)
    canvas = diameter + pad * 2
    img = Image.new("RGBA", (canvas * SS, canvas * SS), (0, 0, 0, 0))
    cx = cy = canvas * SS / 2
    r = diameter * SS / 2

    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).ellipse([cx - r, cy - r + SS * 2, cx + r, cy + r + SS * 2],
                                    fill=(0, 0, 0, shadow_alpha))
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=diameter * 0.035 * SS))
    img.alpha_composite(shadow)

    center_tone = _lerp(fill, (255, 255, 255), 0.06)
    edge_tone = _lerp(fill, (0, 0, 0), 0.22)
    fill_img = Image.new("RGBA", img.size, (0, 0, 0, 0))
    fd = ImageDraw.Draw(fill_img)
    steps = 22
    for i in range(steps, 0, -1):
        t = i / steps
        rr = r * t
        color = _lerp(edge_tone, center_tone, 1 - t)
        fd.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=(*color, 255))
    img.alpha_composite(fill_img)

    ring_w = max(1, int(SS * 2.2))
    ImageDraw.Draw(img).ellipse([cx - r, cy - r, cx + r, cy + r], outline=(*gold, 255), width=ring_w)

    hl = Image.new("RGBA", img.size, (0, 0, 0, 0))
    hl_w, hl_h = r * 1.0, r * 0.42
    ImageDraw.Draw(hl).ellipse([cx - hl_w / 2, cy - r * 0.72, cx + hl_w / 2, cy - r * 0.72 + hl_h],
                                fill=(255, 255, 255, 12))
    hl = hl.filter(ImageFilter.GaussianBlur(radius=diameter * 0.03 * SS))
    img.alpha_composite(hl)

    return _down(img, (canvas, canvas))


def make_background(width, height, base, center_tint) -> Image.Image:
    """Fundo com profundidade sutil: leve iluminação central + vignette discreto nos cantos.
    Pré-renderizado 1x -- em runtime custa um único `blit()` no lugar do `screen.fill()` flat que
    a v5 usava."""
    small_w, small_h = max(1, width // 4), max(1, height // 4)
    img = Image.new("RGB", (small_w, small_h), base)
    cx, cy = small_w / 2, small_h * 0.42  # centro óptico um pouco acima do meio geométrico
    max_d = math.hypot(cx, cy)
    px = img.load()
    for y in range(small_h):
        for x in range(small_w):
            d = math.hypot(x - cx, y - cy) / max_d
            t = min(1.0, d) ** 1.6
            px[x, y] = _lerp(center_tint, base, t)
    return img.resize((width, height), Image.LANCZOS)


def make_ambient_glow(size, color, alpha=26) -> Image.Image:
    """Mancha de luz ambiente extremamente sutil (gradiente radial por círculos concêntricos com
    alfa decrescente -- mesma técnica do preenchimento do `result_badge`, evita o "anel visível"
    que um único blur insuficiente deixaria), usada atrás da logo e na metade inferior "quieta" dos
    cards FRIO/QUENTE. Sempre pré-renderizada, um blit só em runtime."""
    img = Image.new("RGBA", (size * SS, size * SS), (0, 0, 0, 0))
    cx = cy = size * SS / 2
    max_r = size * SS * 0.5
    steps = 48
    d = ImageDraw.Draw(img)
    # Desenha do círculo MAIOR (quase transparente) pro MENOR (mais opaco) -- cada círculo
    # subsequente, menor, é desenhado por cima e cobre o centro do anterior, construindo uma
    # queda suave sem depender de blur (evita o "anel visível" de um blur insuficiente).
    for i in range(steps, 0, -1):
        t = i / steps
        r = max_r * t
        a = int(alpha * (1 - t) ** 2.2)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(*color, a))
    return _down(img, (size, size))


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
    RED = (0xE8, 0x1B, 0x1B)
    GREEN = (0x0C, 0x8A, 0x40)
    TRUE_BLACK = (14, 14, 16)  # preto de verdade -- o bisel dourado (não mais o tom do
    # preenchimento) é quem garante contraste contra o fundo escuro do app agora.
    GOLD = (196, 160, 92)
    GOLD_DARK = (90, 74, 44)
    RED_DARK = (90, 20, 18)
    RED_BRIGHT = (255, 90, 82)
    BLUE_DARK = (10, 40, 70)
    BLUE_BRIGHT = (60, 170, 255)
    WHITE = (240, 240, 240)
    GRAY_LINE = (120, 126, 132)

    make_snowflake_icon(BLUE, size=64).save(OUT_DIR / "cold_icon.png")
    make_flame_icon((160, 25, 10), (255, 140, 30), core=(255, 110, 20), size=64).save(OUT_DIR / "hot_icon.png")

    make_result_badge(380, fill=RED, ring_tone=RED, accent=GOLD, gold_halo=True, double_ring=True).save(OUT_DIR / "result_badge_red.png")
    make_result_badge(380, fill=TRUE_BLACK, ring_tone=TRUE_BLACK, accent=GOLD, gold_halo=True, double_ring=True).save(OUT_DIR / "result_badge_black.png")
    make_result_badge(380, fill=GREEN, ring_tone=GREEN, accent=GOLD, gold_halo=True, double_ring=True).save(OUT_DIR / "result_badge_green.png")

    # Bolinhas simples (sem bisel grosso -- só uma borda fina dourada logo depois do limite da
    # cor). Mesmo asset reaproveitado pros chips do histórico E pro selo colorido do número em
    # cada linha de FRIO/QUENTE (mesmo estilo visual, tamanhos diferentes).
    make_simple_chip(96, fill=RED, gold=GOLD).save(OUT_DIR / "history_chip_red.png")
    make_simple_chip(96, fill=TRUE_BLACK, gold=GOLD).save(OUT_DIR / "history_chip_black.png")
    make_simple_chip(96, fill=GREEN, gold=GOLD).save(OUT_DIR / "history_chip_green.png")

    make_simple_chip(72, fill=RED, gold=GOLD).save(OUT_DIR / "number_chip_red.png")
    make_simple_chip(72, fill=TRUE_BLACK, gold=GOLD).save(OUT_DIR / "number_chip_black.png")
    make_simple_chip(72, fill=GREEN, gold=GOLD).save(OUT_DIR / "number_chip_green.png")

    make_accent_bar(420, 6, BLUE_DARK, BLUE_BRIGHT).save(OUT_DIR / "accent_cold.png")
    make_accent_bar(420, 6, RED_DARK, RED_BRIGHT).save(OUT_DIR / "accent_hot.png")
    make_accent_bar(420, 4, GOLD_DARK, GOLD).save(OUT_DIR / "accent_gold.png")
    make_accent_bar(420, 4, (10, 60, 40), GREEN).save(OUT_DIR / "accent_green.png")
    make_accent_bar(420, 4, (70, 70, 70), WHITE).save(OUT_DIR / "accent_white.png")
    make_accent_bar(420, 4, (110, 70, 10), (0xF5, 0xA0, 0x00)).save(OUT_DIR / "accent_orange.png")
    make_accent_bar(420, 2, (30, 34, 40), GRAY_LINE).save(OUT_DIR / "separator_fade.png")

    make_accent_glow(420, 6, BLUE_BRIGHT).save(OUT_DIR / "accent_cold_glow.png")
    make_accent_glow(420, 6, RED_BRIGHT).save(OUT_DIR / "accent_hot_glow.png")
    make_accent_glow(1080, 3, GOLD).save(OUT_DIR / "accent_gold_glow.png")

    make_card_gradient_tile(8, 320, (16, 20, 26), (10, 13, 18)).save(OUT_DIR / "card_gradient.png")

    make_background(1080, 1920, base=(7, 10, 14), center_tint=(16, 20, 27)).save(OUT_DIR / "background.png")
    make_ambient_glow(420, BLUE_BRIGHT, alpha=40).save(OUT_DIR / "ambient_glow_blue.png")
    make_ambient_glow(420, RED_BRIGHT, alpha=40).save(OUT_DIR / "ambient_glow_red.png")

    print(f"assets gerados em {OUT_DIR}")


if __name__ == "__main__":
    main()
