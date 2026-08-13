"""Mockup ANIMADO (não estático) da nova tela de revelação pós-giro -- ainda na fase
DESIGN -> MOCKUP -> APROVAÇÃO -> CÓDIGO, nenhuma linha de `app/`/`main.py` tocada.

Sequência pedida pelo cliente (4s no total):
  - Logo: zoom/splash de dentro pra fora, MUITO rápido (0.2s), segura 1s, some com fade de
    transparência em 0.3s (fase do logo = 1.5s).
  - Fundo: o MESMO da tela base (`background.png`), sem trocar de cor/tema durante ou depois.
  - Roleta: imagem extraída do print de referência do cliente (`assets/ui/roulette_wheel.png`),
    girando rápido em loop durante os 4s inteiros -- 60% dela fica cortada/oculta pra fora da
    borda esquerda da tela, diâmetro = 3/5 da altura da tela (as outras 2/5 -- 1/5 em cima, 1/5
    embaixo -- centralizam ela verticalmente).
  - Por cima da roleta: gradiente escuro de 70% -> 0%, cobrindo a metade esquerda da tela.
  - Por cima de tudo: o número sorteado no MESMO badge/aro dourado + pills de classificação já
    aprovados na tela principal.

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
    blit_card_bg, blit_outlined, draw_text,
)

W, H = 1080, 1920

TOTAL_S = 4.0
LOGO_ZOOM_S = 0.2
LOGO_HOLD_S = 1.0
LOGO_FADE_S = 0.3
LOGO_END_S = LOGO_ZOOM_S + LOGO_HOLD_S + LOGO_FADE_S  # 1.5s

WHEEL_SPIN_DEG_S = 480.0  # rápido, ~1.3 voltas/segundo -- "igual a roleta do jogo"


def ease_out_cubic(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def build_gradient_overlay(theme: Theme) -> pygame.Surface:
    """70% escuro -> 0% escuro, cobrindo a metade ESQUERDA da tela (onde a roleta gira) --
    calculado uma única vez, reutilizado em todo frame (mesma regra de sempre: nada caro
    recalculado por frame)."""
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


def draw_wheel(screen, wheel_base: pygame.Surface, center: tuple[float, float], t: float) -> None:
    angle = -(t * WHEEL_SPIN_DEG_S) % 360
    rotated = pygame.transform.rotozoom(wheel_base, angle, 1.0)
    rect = rotated.get_rect(center=(round(center[0]), round(center[1])))
    screen.blit(rotated, rect)


def draw_result_badge(screen, theme: Theme, number: int) -> None:
    """Mesmo badge/aro dourado + pills de classificação da tela principal (`draw_center`),
    centralizado na metade DIREITA da tela -- a zona que o degradê deixa 100% clara."""
    color = color_of(number)
    badge_asset = {"red": "result_badge_red.png", "black": "result_badge_black.png",
                   "green": "result_badge_green.png"}[color]

    diameter = theme.px(360)
    badge_size = int(diameter * 1.60)
    cx = round(theme.width * 0.5 + theme.width * 0.5 / 2)  # centro da metade direita da tela
    cy = theme.height // 2 - theme.px(40)
    badge = asset_scaled(badge_asset, (badge_size, badge_size))
    screen.blit(badge, (cx - badge_size // 2, cy - badge_size // 2))

    num_font = theme.font(int(diameter * 0.76), True)
    blit_outlined(screen, num_font, str(number), (cx, cy), fill=OFF_WHITE, outline=(0, 0, 0), outline_px=0)

    tag_y = cy + int(diameter * 0.60) + theme.px(34)
    tags = [("PRETO" if color == "black" else "VERMELHO" if color == "red" else "ZERO",
             OFF_WHITE if color == "black" else RED if color == "red" else GREEN)]
    if number != 0:
        tags.append(("ÍMPAR" if number % 2 else "PAR", CYAN))
        tags.append(("MENOR" if number <= 18 else "MAIOR", ORANGE))

    pill_font = theme.font(26, True)
    pill_gap = theme.px(10)
    pill_h = theme.px(42)
    widths = [pill_font.size(label)[0] + theme.px(28) for label, _ in tags]
    px = cx - (sum(widths) + pill_gap * (len(tags) - 1)) // 2
    for (label, tcolor), pw in zip(tags, widths):
        pill = pygame.Rect(px, tag_y, pw, pill_h)
        blit_card_bg(screen, pill, theme.px(18))
        pygame.draw.rect(screen, tcolor, pill, width=2, border_radius=theme.px(18))
        draw_text(screen, pill_font, label, pill.center, tcolor, anchor="center")
        px += pw + pill_gap


def draw_logo_splash(screen, theme: Theme, logo_raw: pygame.Surface, t: float) -> None:
    """Zoom/splash: cresce da posição atual (pequena, centralizada -- não há um logo persistente
    em outro lugar da cena base pra "crescer a partir dele") até o tamanho de destaque em 0.2s
    (ease-out, sensação de "pop" rápido), segura 1s, some com fade em 0.3s."""
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
    draw_wheel(screen, wheel_base, wheel_center, t)
    screen.blit(gradient, (0, 0))
    draw_result_badge(screen, theme, number)
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
