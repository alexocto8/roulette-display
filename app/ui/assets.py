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


class UiAssets:
    """Cache for the pre-rendered PNGs in `assets/ui/` (result badges, chips, accent bars,
    background vignette, roulette wheel cutout, glows) generated once at design time by
    `tools/build_ui_assets.py`. Same two-level cache used by the mockup tooling this shipped
    from: `_raw` loads each PNG off disk once; `scaled` caches each requested `(name, w, h)`
    output once -- every frame after that is just a `blit()`, never a fresh
    decode/scale/rotate. Optional by design (same spirit as `load_image` above): a name that
    doesn't exist on disk raises once at first use rather than silently no-oping, since these
    assets ship committed in the repo and a missing one means a broken install, not an
    unconfigured optional feature.
    """

    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self._raw: dict[str, pygame.Surface] = {}
        self._scaled: dict[tuple[str, int, int], pygame.Surface] = {}

    def raw(self, name: str) -> pygame.Surface:
        surf = self._raw.get(name)
        if surf is None:
            surf = pygame.image.load(str(self.base_dir / name)).convert_alpha()
            self._raw[name] = surf
        return surf

    def scaled(self, name: str, size: tuple[int, int]) -> pygame.Surface:
        key = (name, size[0], size[1])
        surf = self._scaled.get(key)
        if surf is None:
            surf = pygame.transform.smoothscale(self.raw(name), size)
            self._scaled[key] = surf
        return surf
