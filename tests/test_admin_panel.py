"""Smoke tests do painel de administração — item 23 da auditoria (performance): confirma que o
overlay de tela cheia é alocado uma vez e reaproveitado entre frames, não recriado a cada
`render()`."""
from __future__ import annotations

import pygame

from app.config import Config
from app.database.db import Database
from app.services.backup_service import BackupService
from app.services.export_service import ExportService
from app.services.retention_service import RetentionService
from app.services.spin_service import SpinService
from app.ui.admin import AdminPanel
from app.ui.theme import Theme


def make_panel(tmp_path) -> AdminPanel:
    config = Config(database_path=str(tmp_path / "roulette.db"))
    db = Database(tmp_path / "roulette.db")
    db.initialize()
    service = SpinService(db, config)
    return AdminPanel(config, service, BackupService(db, config), ExportService(db, config),
                       RetentionService(db, config), config_path=str(tmp_path / "config.yaml"))


def test_license_info_text_starts_with_the_app_version(tmp_path):
    from app.version import __version__

    panel = make_panel(tmp_path)
    text = panel._license_info_text()
    assert text.startswith(f"Versão do sistema: {__version__}")


def test_overlay_surface_is_allocated_once_and_reused_across_frames(tmp_path):
    pygame.init()
    screen = pygame.display.set_mode((650, 1000))
    theme = Theme(650, 1000)
    panel = make_panel(tmp_path)

    panel.render(screen, theme, reveal=0.5)
    first_overlay = panel._overlay
    assert first_overlay is not None

    for reveal in (0.6, 0.8, 1.0):
        panel.render(screen, theme, reveal=reveal)
        assert panel._overlay is first_overlay  # mesmo objeto, não recriado a cada frame

    pygame.quit()


def test_full_menu_navigation_and_render_does_not_crash(tmp_path):
    """Abre com o PIN certo, entra em cada categoria e navega seus itens, renderizando cada tela —
    não valida pixels, só que nenhum estado do menu (categorias + itens) quebra o `render()`."""
    pygame.init()
    screen = pygame.display.set_mode((650, 1000))
    theme = Theme(650, 1000)
    panel = make_panel(tmp_path)
    panel.open()

    for ch in panel.config.admin_pin:
        digit_key = getattr(pygame, f"K_{ch}")
        panel.handle_key(pygame.event.Event(pygame.KEYDOWN, key=digit_key, mod=0, unicode=ch))
    panel.handle_key(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN, mod=0, unicode=""))
    assert panel.state == "category"

    from app.ui.admin import CATEGORIES, MENU_ITEMS_BY_CATEGORY

    for _ in range(len(CATEGORIES)):
        panel.render(screen, theme, reveal=1.0)
        panel.handle_key(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_DOWN, mod=0, unicode=""))

    for category_key, _label in CATEGORIES:
        panel.state = "category"
        panel.category_index = [key for key, _ in CATEGORIES].index(category_key)
        panel.handle_key(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN, mod=0, unicode=""))
        assert panel.state == "menu"
        assert panel.category == category_key

        items = MENU_ITEMS_BY_CATEGORY[category_key]
        for _ in range(len(items)):
            panel.render(screen, theme, reveal=1.0)
            panel.handle_key(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_DOWN, mod=0, unicode=""))

        # ESC na lista de itens volta pra tela de categorias, não fecha o painel.
        panel.handle_key(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE, mod=0, unicode=""))
        assert panel.state == "category"

    pygame.quit()


def test_close_entry_on_category_screen_closes_the_panel(tmp_path):
    pygame.init()
    screen = pygame.display.set_mode((650, 1000))
    theme = Theme(650, 1000)
    panel = make_panel(tmp_path)
    panel.open()

    for ch in panel.config.admin_pin:
        digit_key = getattr(pygame, f"K_{ch}")
        panel.handle_key(pygame.event.Event(pygame.KEYDOWN, key=digit_key, mod=0, unicode=ch))
    panel.handle_key(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN, mod=0, unicode=""))
    assert panel.state == "category"

    from app.ui.admin import _CATEGORY_ENTRIES

    panel.category_index = len(_CATEGORY_ENTRIES) - 1  # "Fechar administração"
    assert _CATEGORY_ENTRIES[panel.category_index][0] == "close"
    still_open = panel.handle_key(
        pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN, mod=0, unicode="")
    )
    assert still_open is False
    assert panel.state == "pin"

    pygame.quit()
