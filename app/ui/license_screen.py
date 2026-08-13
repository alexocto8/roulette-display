""""SISTEMA NÃO ATIVADO" / "LICENÇA INVÁLIDA" — shown instead of the main panel when
`app.license.verify` doesn't come back OK. Deliberately minimal and generic: no stack traces, no
file paths, no public key material, nothing that helps someone reverse-engineer the verifier — the
technical reason lives in the log file only (the caller logs `LicenseResult.detail` before ever
showing this screen).

Blocks the process here, in its own small event loop, instead of letting `main.py` exit and rely
on `Restart=always` to loop back: a genuinely unlicensed device restarting every ~3s would likely
hit systemd's default start-rate limit and end up "failed" (no more restarts, blank screen) rather
than showing this message. Pressing ENTER re-checks the license file in place (e.g. after a
technician drops a `license.dat` onto the SD card via USB) without needing a service restart.
"""
from __future__ import annotations

import logging

import pygame

from app.config import Config
from app.license import hardware
from app.license.public_key import public_key_bytes
from app.license.verify import LicenseResult, LicenseStatus, verify_license
from app.ui.rotation import create_screen
from app.ui.theme import BG, RED, TEXT_MUTED, TEXT_PRIMARY, TEXT_SECONDARY, Theme

logger = logging.getLogger("roulette.license")

_RECHECK_KEYS = {pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_r}

_MISSING_TITLE = "SISTEMA NÃO ATIVADO"
_INVALID_TITLE = "LICENÇA INVÁLIDA"
_MISSING_LINE = "Entre em contato com o fornecedor para ativação."
_INVALID_LINE = "A licença deste equipamento não pôde ser validada."


def ensure_licensed(config: Config) -> bool:
    """Single entry point `main.py` calls before touching the database or the display: verifies
    the license and, if it's not OK, blocks on the gate screen. Returns True to proceed, False to
    exit the process (operator asked to quit from the gate screen)."""
    license_path = config.resolve(config.license_path)
    state_path = config.resolve(config.license_state_path)
    key_bytes = public_key_bytes()

    def _check() -> LicenseResult:
        return verify_license(license_path, key_bytes, state_path=state_path)

    result = _check()
    if result.ok:
        return True

    logger.error("Licença não OK no boot: status=%s detail=%s", result.status.value, result.detail)
    ok = _run_gate(config, _check, result)
    if ok:
        logger.info("Licença validada a partir da tela de ativação — prosseguindo para o painel.")
    else:
        logger.info("Encerrado a partir da tela de licença (sem licença válida).")
    return ok


def _run_gate(config: Config, check, result: LicenseResult) -> bool:
    """Blocks showing the gate screen (re-checking on ENTER) until the license becomes valid or
    the process is asked to quit."""
    screen, theme = create_screen(config, config.casino_name)

    device = hardware.device_id()
    clock = pygame.time.Clock()
    while True:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return False
            if event.type == pygame.KEYDOWN and event.key in _RECHECK_KEYS:
                result = check()
                if result.ok:
                    return True

        _draw(screen, theme, result, device)
        pygame.display.flip()
        clock.tick(10)  # nada anima aqui — só economiza CPU parado


def _draw(screen: pygame.Surface, theme: Theme, result: LicenseResult, device: str) -> None:
    screen.fill(BG)
    cx, cy = theme.width // 2, theme.height // 2

    title = _MISSING_TITLE if result.status is LicenseStatus.MISSING else _INVALID_TITLE
    title_font = theme.font(52, bold=True)
    title_surf = title_font.render(title, True, RED)
    screen.blit(title_surf, title_surf.get_rect(center=(cx, cy - theme.px(110))))

    id_label_font = theme.font(24, bold=True)
    id_label = id_label_font.render("DEVICE ID", True, TEXT_SECONDARY)
    screen.blit(id_label, id_label.get_rect(center=(cx, cy - theme.px(20))))

    id_font = theme.font(56, bold=True)
    id_surf = id_font.render(device, True, TEXT_PRIMARY)
    screen.blit(id_surf, id_surf.get_rect(center=(cx, cy + theme.px(38))))

    line = _MISSING_LINE if result.status is LicenseStatus.MISSING else _INVALID_LINE
    line_font = theme.font(26, bold=True)
    line_surf = line_font.render(line, True, TEXT_PRIMARY)
    screen.blit(line_surf, line_surf.get_rect(center=(cx, cy + theme.px(120))))

    hint_font = theme.font(18)
    hint = hint_font.render("ENTER verifica novamente após instalar a licença", True, TEXT_MUTED)
    screen.blit(hint, hint.get_rect(center=(cx, theme.height - theme.px(60))))
