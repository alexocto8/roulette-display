"""Mockup ANIMADO (não estático) da nova tela de revelação pós-giro -- ainda na fase
DESIGN -> MOCKUP -> APROVAÇÃO -> CÓDIGO, nenhuma linha de `app/`/`main.py` tocada.

Sequência pedida pelo cliente (rodada 2, substitui o timing "tudo simultâneo em 4s" da rodada
anterior):
  1. Logo: zoom/splash MUITO rápido (0.2s), segura 2s NA TELA SOZINHO (roleta/número/badges ainda
     não apareceram), some com fade de transparência em 0.3s (fase do logo = 2.5s).
  2. Só DEPOIS do logo sumir, roleta + número + badges entram juntos com um fade-in de 0.3s.
  3. No mesmo intervalo de 0.3s do fade-in, a roleta (que já vinha girando rápido, só que
     invisível) DESACELERA até parar -- termina parada exatamente quando tudo fica 100% visível.
  4. Número centralizado na tela (vertical E horizontal), badges empilhados embaixo dele,
     diâmetro >= 70% maior que o badge da rodada anterior (era 520px -> agora 900px, +73%).
  5. Depois de parado, o número fica na tela por mais 5s com um efeito de pulsar (leve variação
     de escala) + glow dourado na borda que respira junto.

Fundo: o MESMO da tela base (`background.png`) o tempo todo, sem trocar de cor/tema.

Gera uma sequência de PNGs + compila em MP4 via ffmpeg (só ferramenta de pré-visualização --
`ffmpeg` não é dependência do produto, só deste script de mockup) pra dar pra avaliar o TIMING de
verdade, não só um frame estático. Roda com SDL_VIDEODRIVER=dummy, não abre janela.

    python tools/mockup_reveal_animation.py [--out DIR] [--number 17] [--fps 30]
"""
from __future__ import annotations

import argparse
import math
import os
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pygame

from app.ui.theme import CYAN, RED, GREEN, ORANGE, Theme
from tools.mockup_ui import (
    ASSETS_DIR, PROJECT_LOGO, OFF_WHITE, color_of, load_asset, asset_scaled,
    blit_card_bg, draw_text,
)

W, H = 1080, 1920

# -- fase 1: logo sozinho ---------------------------------------------------------------------
LOGO_ZOOM_S = 0.2
LOGO_HOLD_S = 2.0  # era 1.0s -- pedido explícito
LOGO_FADE_S = 0.3
LOGO_END_S = LOGO_ZOOM_S + LOGO_HOLD_S + LOGO_FADE_S  # 2.5s

# -- fase 2: roleta/número/badges entram juntos, roleta desacelera até parar ------------------
REVEAL_FADE_S = 0.3
REVEAL_END_S = LOGO_END_S + REVEAL_FADE_S  # 2.8s -- tudo 100% visível, roda parada

# -- fase 3: número parado, pulsando, por mais 5s ----------------------------------------------
HOLD_S = 5.0
TOTAL_S = REVEAL_END_S + HOLD_S  # 7.8s

WHEEL_SPIN_DEG_S = 480.0  # rápido, ~1.3 voltas/segundo -- "igual a roleta do jogo"
PULSE_PERIOD_S = 1.2


def ease_out_cubic(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def build_gradient_overlay(theme: Theme) -> pygame.Surface:
    """70% escuro -> 0% escuro, cobrindo a metade ESQUERDA da tela (onde a roleta gira) --
    calculado uma única vez, reutilizado em todo frame."""
    half_w = theme.width // 2
    grad = pygame.Surface((half_w, theme.height), pygame.SRCALPHA)
    max_alpha = int(255 * 0.70)
    for x in range(half_w):
        a = int(max_alpha * (1 - x / half_w))
        pygame.draw.line(grad, (0, 0, 0, a), (x, 0), (x, theme.height))
    return grad


def build_wheel_base(theme: Theme) -> tuple[pygame.Surface, tuple[float, float]]:
    diameter = round(theme.height * 0.6)  # 3/5 da altura
    wheel = pygame.transform.smoothscale(load_asset("roulette_wheel.png"), (diameter, diameter))
    center_y = theme.height / 2  # 1/5 de folga em cima e embaixo, centralizada
    center_x = -0.10 * diameter  # 60% do diâmetro oculto pra fora da borda esquerda
    return wheel, (center_x, center_y)


def wheel_angle(t: float) -> float:
    """Continua girando (rápido) o tempo todo, mesmo enquanto invisível durante a fase do logo --
    quando a fase 2 começa, desacelera de velocidade total até ZERO ao longo de `REVEAL_FADE_S`
    (desaceleração linear na VELOCIDADE, o que dá uma curva suave na posição), terminando parada
    exatamente no ângulo em que ficou -- nunca mais se move depois disso."""
    if t <= LOGO_END_S:
        return -(t * WHEEL_SPIN_DEG_S) % 360
    base = LOGO_END_S * WHEEL_SPIN_DEG_S
    if t >= REVEAL_END_S:
        u = 1.0
    else:
        u = (t - LOGO_END_S) / REVEAL_FADE_S
    # velocidade linear de WHEEL_SPIN_DEG_S -> 0 ao longo de `u`; posição = integral da velocidade
    extra = WHEEL_SPIN_DEG_S * REVEAL_FADE_S * (u - u * u / 2)
    return -(base + extra) % 360


def scene_alpha(t: float) -> int:
    """Roleta + gradiente + badge/número/pills só existem DEPOIS que o logo suma -- fade-in único
    de `REVEAL_FADE_S`, tudo junto (renderizado numa camada à parte e com alpha aplicado nela,
    não em cada elemento separadamente -- ver `render_frame`)."""
    if t <= LOGO_END_S:
        return 0
    if t >= REVEAL_END_S:
        return 255
    return int(255 * (t - LOGO_END_S) / REVEAL_FADE_S)


def pulse_state(t: float) -> tuple[float, float]:
    """Só pulsa depois que tudo termina de entrar E a roleta já está parada (`REVEAL_END_S`).
    `scale` é a leve variação de tamanho do badge/número (~3.5%); `glow_t` (0..1) modula a
    intensidade do glow dourado extra na borda, na mesma fase -- os dois "respiram" juntos."""
    if t < REVEAL_END_S:
        return 1.0, 0.0
    phase = 2 * math.pi * (t - REVEAL_END_S) / PULSE_PERIOD_S
    scale = 1.0 + 0.035 * math.sin(phase)
    glow_t = (math.sin(phase) + 1) / 2
    return scale, glow_t


def draw_wheel(screen, wheel_base: pygame.Surface, center: tuple[float, float], t: float) -> None:
    rotated = pygame.transform.rotozoom(wheel_base, wheel_angle(t), 1.0)
    rect = rotated.get_rect(center=(round(center[0]), round(center[1])))
    screen.blit(rotated, rect)


def draw_number_dropshadow(screen, font, text, center, fill, shadow, offset) -> None:
    """Numeral com uma cópia escura duplicada, deslocada pra baixo-direita, por BAIXO da cópia
    branca -- efeito "extrudado/adesivo" do print de referência do cliente."""
    shadow_surf = font.render(text, True, shadow)
    screen.blit(shadow_surf, shadow_surf.get_rect(center=(center[0] + offset, center[1] + offset)))
    fill_surf = font.render(text, True, fill)
    screen.blit(fill_surf, fill_surf.get_rect(center=center))


def draw_result_badge(screen, theme: Theme, number: int, pulse_scale: float, glow_t: float) -> None:
    """Número centralizado na tela (vertical E horizontal -- pedido explícito, saiu do
    deslocamento pra direita da rodada anterior), diâmetro-base 70%+ maior que a rodada anterior
    (520px -> 900px), badges empilhados embaixo dele. Durante o "hold" final, `pulse_scale`
    (leve respiração de tamanho) e `glow_t` (halo dourado extra, mais/menos intenso) animam o
    conjunto -- fora dessa fase os dois vêm neutros (1.0 / 0.0) e não mudam nada visualmente."""
    color = color_of(number)
    badge_asset = {"red": "reveal_badge_red.png", "black": "reveal_badge_black.png",
                   "green": "reveal_badge_green.png"}[color]

    base_diameter = theme.px(900)  # era 520 -- +73%, acima do mínimo de +70% pedido
    diameter = round(base_diameter * pulse_scale)
    badge_size = int(diameter * 1.80)  # já inclui a folga do glow largo
    cx, cy = theme.width // 2, theme.height // 2  # centralizado vertical E horizontal

    glow_size = int(base_diameter * 2.6)
    glow = asset_scaled("reveal_glow_blue.png", (glow_size, glow_size))
    screen.blit(glow, (cx - glow_size // 2, cy - glow_size // 2))

    if glow_t > 0:
        pulse_glow_size = int(diameter * 1.55)
        pulse_glow = asset_scaled("pulse_glow_gold.png", (pulse_glow_size, pulse_glow_size)).copy()
        pulse_glow.set_alpha(int(70 + 160 * glow_t))
        screen.blit(pulse_glow, (cx - pulse_glow_size // 2, cy - pulse_glow_size // 2))

    badge = asset_scaled(badge_asset, (badge_size, badge_size))
    screen.blit(badge, (cx - badge_size // 2, cy - badge_size // 2))

    num_font = theme.font(int(diameter * 1.30), True)
    shadow_tone = {"red": (60, 4, 2), "black": (5, 5, 6), "green": (2, 45, 22)}[color]
    draw_number_dropshadow(screen, num_font, str(number), (cx, cy), fill=OFF_WHITE,
                            shadow=shadow_tone, offset=theme.px(10))

    tags = [("PRETO" if color == "black" else "VERMELHO" if color == "red" else "ZERO",
             OFF_WHITE if color == "black" else RED if color == "red" else GREEN)]
    if number != 0:
        tags.append(("ÍMPAR" if number % 2 else "PAR", CYAN))
        tags.append(("MENOR" if number <= 18 else "MAIOR", ORANGE))

    # posição dos badges calculada a partir do diâmetro BASE (não do pulsado) -- só o
    # círculo/número respiram, os badges ficam parados (evita jitter de posição a cada frame).
    pill_font = theme.font(30, True)
    pill_w = theme.px(340)
    pill_h = theme.px(72)
    pill_gap = theme.px(16)
    tag_y = cy + int(base_diameter * 0.5) + theme.px(56)
    for label, tcolor in tags:
        pill = pygame.Rect(cx - pill_w // 2, tag_y, pill_w, pill_h)
        blit_card_bg(screen, pill, theme.px(20))
        pygame.draw.rect(screen, tcolor, pill, width=2, border_radius=theme.px(20))
        draw_text(screen, pill_font, label, pill.center, tcolor, anchor="center")
        tag_y += pill_h + pill_gap


def draw_logo_splash(screen, theme: Theme, logo_raw: pygame.Surface, t: float) -> None:
    """Zoom/splash: cresce da posição atual (pequena, centralizada -- não há um logo persistente
    em outro lugar da cena base pra "crescer a partir dele") até o tamanho de destaque em 0.2s
    (ease-out, sensação de "pop" rápido), segura 2s sozinho na tela, some com fade em 0.3s."""
    if t >= LOGO_END_S:
        return

    target_w = theme.width * 0.62
    ratio = target_w / logo_raw.get_width()
    target_h = logo_raw.get_height() * ratio

    if t < LOGO_ZOOM_S:
        scale = 0.12 + 0.88 * ease_out_cubic(t / LOGO_ZOOM_S)
        alpha = 255
    elif t < LOGO_ZOOM_S + LOGO_HOLD_S:
        scale = 1.0
        alpha = 255
    else:
        scale = 1.0
        fade_t = (t - LOGO_ZOOM_S - LOGO_HOLD_S) / LOGO_FADE_S
        alpha = int(255 * (1 - min(1.0, fade_t)))

    w, h = max(1, int(target_w * scale)), max(1, int(target_h * scale))
    scaled = pygame.transform.smoothscale(logo_raw, (w, h))
    scaled.set_alpha(alpha)
    rect = scaled.get_rect(center=(theme.width // 2, theme.height // 2))
    screen.blit(scaled, rect)


def render_frame(theme, bg, wheel_base, wheel_center, gradient, logo_raw, number, t) -> pygame.Surface:
    screen = pygame.Surface((theme.width, theme.height))
    screen.blit(bg, (0, 0))  # fundo do layout base -- igual durante e depois da cena, sem trocar

    a = scene_alpha(t)
    if a > 0:
        pulse_scale, glow_t = pulse_state(t)
        reveal_layer = pygame.Surface((theme.width, theme.height), pygame.SRCALPHA)
        draw_wheel(reveal_layer, wheel_base, wheel_center, t)
        reveal_layer.blit(gradient, (0, 0))
        draw_result_badge(reveal_layer, theme, number, pulse_scale, glow_t)
        reveal_layer.set_alpha(a)
        screen.blit(reveal_layer, (0, 0))

    draw_logo_splash(screen, theme, logo_raw, t)
    return screen


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(Path(__file__).resolve().parent / "reveal_preview"))
    parser.add_argument("--number", type=int, default=17)
    parser.add_argument("--fps", type=int, default=30)
    args = parser.parse_args()

    if not (ASSETS_DIR / "roulette_wheel.png").exists():
        print(f"assets ausentes em {ASSETS_DIR} -- gere/rode antes o extrator da roleta.")
        sys.exit(1)

    pygame.init()
    pygame.display.set_mode((1, 1))
    theme = Theme(W, H)

    bg = asset_scaled("background.png", (theme.width, theme.height))
    wheel_base, wheel_center = build_wheel_base(theme)
    gradient = build_gradient_overlay(theme)
    logo_raw = pygame.image.load(str(PROJECT_LOGO)).convert_alpha()

    out_dir = Path(args.out)
    frames_dir = out_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    n_frames = round(TOTAL_S * args.fps)
    for i in range(n_frames):
        t = i / args.fps
        frame = render_frame(theme, bg, wheel_base, wheel_center, gradient, logo_raw, args.number, t)
        pygame.image.save(frame, str(frames_dir / f"frame_{i:04d}.png"))

    mp4_path = out_dir / "reveal_preview.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-framerate", str(args.fps), "-i", str(frames_dir / "frame_%04d.png"),
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(mp4_path)],
        check=True, capture_output=True,
    )
    print(f"{n_frames} frames em {frames_dir}")
    print(f"vídeo: {mp4_path}")


if __name__ == "__main__":
    main()
