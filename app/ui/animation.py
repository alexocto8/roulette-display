"""Easing + tween helpers for UI transitions.

Deliberately tiny — no animation-graph/timeline library, just a couple of pure easing functions
and a small value tween used by the display for its handful of transitions (number reveal,
history list reflow, overlay fades). Evaluating these every frame is a few float multiplications,
not per-pixel work, so it stays cheap on a Raspberry Pi 3 — the cost that actually matters (extra
surface blits/text renders) only happens while something is actively animating, which the display
already limits to short, bounded windows.
"""
from __future__ import annotations

import pygame


def ease_out_cubic(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return 1 - (1 - t) ** 3


def ease_out_back(t: float, overshoot: float = 1.7) -> float:
    """Overshoots slightly past 1.0 before settling — a small "pop" that reads as polished."""
    t = min(1.0, max(0.0, t)) - 1
    return 1 + (overshoot + 1) * (t ** 3) + overshoot * (t ** 2)


class Tween:
    """A scalar animated from `start` to `end` over `duration_ms`, using `easing`.

    Time is read from pygame.time.get_ticks(), so the transition's wall-clock duration doesn't
    depend on the current frame rate (which the display varies deliberately between idle_fps and
    target_fps).
    """

    __slots__ = ("start", "end", "duration_ms", "easing", "started_at")

    def __init__(self, start: float, end: float, duration_ms: int, easing=ease_out_cubic):
        self.start = start
        self.end = end
        self.duration_ms = max(1, duration_ms)
        self.easing = easing
        self.started_at = pygame.time.get_ticks()

    def retarget(self, start: float, end: float, duration_ms: int | None = None) -> None:
        self.start = start
        self.end = end
        if duration_ms is not None:
            self.duration_ms = max(1, duration_ms)
        self.started_at = pygame.time.get_ticks()

    def progress(self, now: int | None = None) -> float:
        now = pygame.time.get_ticks() if now is None else now
        return (now - self.started_at) / self.duration_ms

    def value(self, now: int | None = None) -> float:
        t = self.easing(self.progress(now))
        return self.start + (self.end - self.start) * t

    def done(self, now: int | None = None) -> bool:
        return self.progress(now) >= 1.0
