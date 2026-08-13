"""Boot splash: logo + casino name + "Inicializando sistema..." while the DB/service warm up.

Shown for a minimum duration (so it doesn't just flash by on a fast boot) and gracefully falls
back to text-only if assets/splash.png or assets/logo.png are missing, instead of crashing —
a fresh install without custom branding yet must still boot cleanly.
"""
from __future__ import annotations

import pygame

from app.config import Config
from app.ui.assets import load_image
from app.ui.theme import BG, GOLD, TEXT_SECONDARY, Theme


def show_splash(screen: pygame.Surface, config: Config, theme: Theme, min_seconds: float = 1.5) -> None:
    assets_dir = config.resolve(config.assets_dir)
    background = load_image(assets_dir / "background.png") or load_image(assets_dir / "splash.png")
    logo = load_image(assets_dir / "logo.png")

    clock = pygame.time.Clock()
    elapsed = 0.0
    frame = 0

    while elapsed < min_seconds:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return

        screen.fill(BG)
        if background is not None:
            bg_scaled = pygame.transform.smoothscale(background, (theme.width, theme.height))
            screen.blit(bg_scaled, (0, 0))

        center_x = theme.width // 2
        center_y = theme.height // 2

        if logo is not None:
            max_logo_w = int(theme.width * 0.5)
            max_logo_h = int(theme.height * 0.35)
            logo_ratio = min(max_logo_w / logo.get_width(), max_logo_h / logo.get_height())
            logo_scaled = pygame.transform.smoothscale(
                logo, (max(1, int(logo.get_width() * logo_ratio)), max(1, int(logo.get_height() * logo_ratio)))
            )
            logo_rect = logo_scaled.get_rect(center=(center_x, center_y - theme.px(60)))
            screen.blit(logo_scaled, logo_rect)
            name_y = logo_rect.bottom + theme.px(30)
        else:
            name_y = center_y - theme.px(30)

        name_font = theme.font(48, bold=True)
        name_surface = name_font.render(config.casino_name, True, GOLD)
        screen.blit(name_surface, name_surface.get_rect(center=(center_x, name_y)))

        dots = "." * (1 + frame // 10 % 3)
        loading_font = theme.font(24)
        loading_surface = loading_font.render(f"Inicializando sistema{dots}", True, TEXT_SECONDARY)
        screen.blit(loading_surface, loading_surface.get_rect(center=(center_x, name_y + theme.px(60))))

        pygame.display.flip()
        dt = clock.tick(20) / 1000.0
        elapsed += dt
        frame += 1
