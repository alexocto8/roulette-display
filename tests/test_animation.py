import pygame
import pytest

from app.ui.animation import Tween, ease_out_back, ease_out_cubic


@pytest.fixture(autouse=True, scope="module")
def _pygame_init():
    pygame.init()
    yield
    pygame.quit()


def test_ease_out_cubic_boundaries():
    assert ease_out_cubic(0.0) == 0.0
    assert ease_out_cubic(1.0) == 1.0
    assert 0.0 < ease_out_cubic(0.5) < 1.0


def test_ease_out_cubic_clamps_out_of_range_input():
    assert ease_out_cubic(-1.0) == 0.0
    assert ease_out_cubic(2.0) == 1.0


def test_ease_out_back_overshoots_past_one():
    values = [ease_out_back(t / 20) for t in range(21)]
    assert max(values) > 1.0
    assert values[0] == pytest.approx(0.0, abs=1e-6)
    assert values[-1] == pytest.approx(1.0, abs=1e-6)


def test_tween_value_at_start_and_end():
    now = pygame.time.get_ticks()
    tween = Tween(0, 100, 1000)
    assert tween.value(now) == pytest.approx(0.0, abs=0.5)
    assert tween.value(now + 1000) == pytest.approx(100.0, abs=0.5)


def test_tween_done():
    now = pygame.time.get_ticks()
    tween = Tween(0, 100, 500)
    assert not tween.done(now)
    assert not tween.done(now + 499)
    assert tween.done(now + 500)
    assert tween.done(now + 1000)


def test_tween_retarget_restarts_from_new_start():
    now = pygame.time.get_ticks()
    tween = Tween(0, 100, 500)
    tween.retarget(50, 200, 400)
    assert tween.value(now) == pytest.approx(50.0, abs=0.5)
    assert tween.value(now + 400) == pytest.approx(200.0, abs=0.5)
