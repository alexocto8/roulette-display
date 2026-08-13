"""Mockup ANIMADO (não estático) da nova tela de revelação pós-giro -- ainda na fase
DESIGN -> MOCKUP -> APROVAÇÃO -> CÓDIGO, nenhuma linha de `app/`/`main.py` tocada.

Sequência pedida pelo cliente (rodada 3):
  0. Transição: fade in de 0.3s entrando nessa animação a partir da TELA PRINCIPAL de verdade
     (crossfade com `mockup_ui.build_mockup()`, não um fade pro preto), e fade out de 0.3s
     saindo de volta pra ela no final.
  1. Logo: mesma sequência de zoom/segura/some da rodada anterior, só que com o TEMPO TOTAL
     DOBRADO (0.2/2.0/0.3s -> 0.4/4.0/0.6s = 5.0s de fase do logo).
  2. Só depois do logo sumir, roleta + número + badges entram juntos com fade-in de 0.3s.
  3. A roleta pode continuar girando à vontade depois disso -- ela só desacelera e para nos
     ÚLTIMOS 2 segundos da animação inteira (não mais sincronizada com o fade-in do número).
  4. Círculo/aro do número IGUAIS ao "ÚLTIMO RESULTADO" do painel principal (mesmo asset
     `result_badge_*.png`, bisel grosso + halo duplo) -- volta a manter o padrão visual da tela
     principal (rodada anterior tinha trocado por um anel fino + número transbordando; o cliente
     pediu de volta o padrão oficial). Número redimensionado pra caber DENTRO do círculo, sem
     exceder (igual ao painel principal), mas o diâmetro do círculo em si continua >= 70% maior
     que o badge "antigo" (520px -> 900px).
  5. Número exibido por 8s (era 5s), pulsando + glow dourado na borda o tempo todo dessa janela.
  6. Degradê de 70%->0% escuro, esquerda->centro da tela: sem mudança (já era assim).

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
    blit_card_bg, blit_outlined, draw_text, build_mockup,
)

W, H = 1080, 1920

# -- fase 0: crossfade de entrada/saída com a tela principal ----------------------------------
GLOBAL_FADE_S = 0.3

# -- fase 1: logo sozinho (tempo total DOBRADO -- pedido explícito) ---------------------------
LOGO_ZOOM_S = 0.4
LOGO_HOLD_S = 4.0
LOGO_FADE_S = 0.6
LOGO_END_S = LOGO_ZOOM_S + LOGO_HOLD_S + LOGO_FADE_S  # 5.0s

# -- fase 2: roleta/número/badges entram juntos ------------------------------------------------
REVEAL_FADE_S = 0.3
REVEAL_END_S = LOGO_END_S + REVEAL_FADE_S  # 5.3s -- tudo 100% visível

# -- fase 3: número exibido, pulsando, por 8s ---------------------------------------------------
NUMBER_DISPLAY_S = 8.0
TOTAL_S = REVEAL_END_S + NUMBER_DISPLAY_S  # 13.3s

# -- roleta: gira livre, só desacelera/para nos ÚLTIMOS 2s da animação inteira -----------------
WHEEL_SPIN_DEG_S = 480.0  # rápido, ~1.3 voltas/segundo -- "igual a roleta do jogo"
WHEEL_DECEL_S = 2.0
WHEEL_DECEL_START_S = TOTAL_S - WHEEL_DECEL_S  # 11.3s

PULSE_PERIOD_S = 1.2


def ease_out_cubic(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def build_gradient_overlay(theme: Theme) -> pygame.Surface:
    """70% escuro -> 0% escuro, esquerda -> CENTRO da tela (metade esquerda) -- sem mudança
    pedida nesta rodada, só reconfirmado. Calculado uma única vez, reutilizado em todo frame."""
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
    """Gira à velocidade total o tempo todo (mesmo enquanto ainda invisível, antes do fade-in) --
    só desacelera de velocidade total até ZERO ao longo de `WHEEL_DECEL_S`, nos últimos 2s da
    animação INTEIRA (não mais atrelada ao fade-in do número) -- termina parada exatamente no
    fim, nunca mais se move depois."""
    if t <= WHEEL_DECEL_START_S:
        return -(t * WHEEL_SPIN_DEG_S) % 360
    u = min(1.0, (t - WHEEL_DECEL_START_S) / WHEEL_DECEL_S)
    base = WHEEL_DECEL_START_S * WHEEL_SPIN_DEG_S
    # velocidade linear de WHEEL_SPIN_DEG_S -> 0 ao longo de `u`; posição = integral da velocidade
    extra = WHEEL_SPIN_DEG_S * WHEEL_DECEL_S * (u - u * u / 2)
    return -(base + extra) % 360


def scene_alpha(t: float) -> int:
    """Roleta + gradiente + badge/número/pills só existem DEPOIS que o logo suma -- fade-in único
    de `REVEAL_FADE_S`, tudo junto (renderizado numa camada à parte e com alpha aplicado nela)."""
    if t <= LOGO_END_S:
        return 0
    if t >= REVEAL_END_S:
        return 255
    return int(255 * (t - LOGO_END_S) / REVEAL_FADE_S)


def pulse_state(t: float) -> tuple[float, float]:
    """Pulsa durante TODA a janela de exibição do número (`REVEAL_END_S` até o fim), não mais
    atrelado à roleta ter parado (agora ela só para bem depois, nos últimos 2s). `scale` é a leve
    variação de tamanho do badge/número (~3.5%); `glow_t` (0..1) modula a intensidade do glow
    dourado extra na borda, respirando junto."""
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


def draw_result_badge(screen, theme: Theme, number: int, pulse_scale: float, glow_t: float) -> None:
    """Círculo/aro IGUAIS ao "ÚLTIMO RESULTADO" do painel principal (`result_badge_*.png`, bisel
    grosso + halo duplo) -- pedido explícito de voltar a manter o padrão visual, em vez do anel
    fino + número transbordando da rodada anterior. Número dimensionado pra caber DENTRO do
    círculo (mesma proporção `diameter * 0.76` do painel principal), sem exceder. Diâmetro-base
    do círculo continua 900px (>= 70% maior que os 520px da versão "antiga"). Centralizado na
    tela (vertical e horizontal), badges empilhados embaixo usando a MESMA fórmula de espaçamento
    do painel principal (`diameter * 0.60 + 34px`)."""
    color = color_of(number)
    badge_asset = {"red": "result_badge_red.png", "black": "result_badge_black.png",
                   "green": "result_badge_green.png"}[color]

    base_diameter = theme.px(900)  # era 520 -- +73%, acima do mínimo de +70% pedido
    diameter = round(base_diameter * pulse_scale)
    badge_size = int(diameter * 1.60)  # mesma proporção do painel principal (inclui halo)
    cx, cy = theme.width // 2, theme.height // 2  # centralizado vertical E horizontal

    glow_size = int(base_diameter * 2.2)
    glow = asset_scaled("reveal_glow_blue.png", (glow_size, glow_size))
    screen.blit(glow, (cx - glow_size // 2, cy - glow_size // 2))

    if glow_t > 0:
        pulse_glow_size = int(diameter * 1.30)
        pulse_glow = asset_scaled("pulse_glow_gold.png", (pulse_glow_size, pulse_glow_size)).copy()
        pulse_glow.set_alpha(int(70 + 160 * glow_t))
        screen.blit(pulse_glow, (cx - pulse_glow_size // 2, cy - pulse_glow_size // 2))

    badge = asset_scaled(badge_asset, (badge_size, badge_size))
    screen.blit(badge, (cx - badge_size // 2, cy - badge_size // 2))

    num_font = theme.font(int(diameter * 0.76), True)  # cabe dentro do círculo, igual ao painel principal
    blit_outlined(screen, num_font, str(number), (cx, cy), fill=OFF_WHITE, outline=(0, 0, 0), outline_px=0)

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
    tag_y = cy + int(base_diameter * 0.60) + theme.px(34)  # mesma fórmula do painel principal
    for label, tcolor in tags:
        pill = pygame.Rect(cx - pill_w // 2, tag_y, pill_w, pill_h)
        blit_card_bg(screen, pill, theme.px(20))
        pygame.draw.rect(screen, tcolor, pill, width=2, border_radius=theme.px(20))
        draw_text(screen, pill_font, label, pill.center, tcolor, anchor="center")
        tag_y += pill_h + pill_gap


def draw_logo_splash(screen, theme: Theme, logo_raw: pygame.Surface, t: float) -> None:
    """Zoom/splash: cresce da posição atual (pequena, centralizada) até o tamanho de destaque em
    `LOGO_ZOOM_S` (ease-out), segura `LOGO_HOLD_S` sozinho na tela, some com fade em
    `LOGO_FADE_S` -- os três tempos dobrados nesta rodada em relação à anterior."""
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


def render_reveal_content(theme, bg, wheel_base, wheel_center, gradient, logo_raw, number, t) -> pygame.Surface:
    """A animação de revelação em si (sem o crossfade de entrada/saída), opaca -- o crossfade com
    a tela principal é aplicado por cima disso em `render_frame`."""
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


def render_frame(theme, main_screen, bg, wheel_base, wheel_center, gradient, logo_raw, number, t) -> pygame.Surface:
    """Crossfade de 0.3s ENTRANDO a partir da tela principal de verdade (não um fade pro preto) e
    de 0.3s SAINDO de volta pra ela no final -- pedido explícito ("transitar entre essa animação
    e a tela principal")."""
    content = render_reveal_content(theme, bg, wheel_base, wheel_center, gradient, logo_raw, number, t)

    if t < GLOBAL_FADE_S:
        blend = t / GLOBAL_FADE_S
    elif t > TOTAL_S - GLOBAL_FADE_S:
        blend = (TOTAL_S - t) / GLOBAL_FADE_S
    else:
        blend = 1.0
    blend = max(0.0, min(1.0, blend))

    screen = pygame.Surface((theme.width, theme.height))
    screen.blit(main_screen, (0, 0))
    content.set_alpha(round(255 * blend))
    screen.blit(content, (0, 0))
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

    main_screen = build_mockup()  # tela principal DE VERDADE, pro crossfade de entrada/saída
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
        frame = render_frame(theme, main_screen, bg, wheel_base, wheel_center, gradient, logo_raw, args.number, t)
        pygame.image.save(frame, str(frames_dir / f"frame_{i:04d}.png"))

    mp4_path = out_dir / "reveal_preview.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-framerate", str(args.fps), "-i", str(frames_dir / "frame_%04d.png"),
         "-c:v", "libx264", "-profile:v", "baseline", "-level", "3.0",
         "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(mp4_path)],
        check=True, capture_output=True,
    )
    print(f"{n_frames} frames em {frames_dir}")
    print(f"vídeo: {mp4_path}")


if __name__ == "__main__":
    main()
