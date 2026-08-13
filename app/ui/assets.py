"""Shared image loading for the UI layer.

Every asset (logo, background, splash) is optional: a fresh install without custom branding yet
must still boot and run cleanly, so a missing/broken file logs a warning and yields None instead
of crashing.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pygame

logger = logging.getLogger("roulette.ui")


def load_image(path: Path) -> pygame.Surface | None:
    if not path.exists():
        return None
    try:
        return pygame.image.load(str(path)).convert_alpha()
    except pygame.error as exc:
        logger.warning("Could not load image %s: %s", path, exc)
        return None
