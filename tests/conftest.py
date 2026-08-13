"""Forces pygame onto the headless/dummy SDL video+audio drivers for the whole test session.

Without this, any test importing pygame (e.g. test_animation.py, which needs a real pygame.init()
for pygame.time.get_ticks()) would fail on a CI runner or dev machine with no display attached.
"""
import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
