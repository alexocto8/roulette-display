"""Reboot/shutdown the host. Requires the exact passwordless sudo rules installed by
scripts/install.sh (see systemd/roulette-sudoers) — nothing broader than those two commands.
"""
from __future__ import annotations

import logging
import subprocess

logger = logging.getLogger("roulette.power")


def reboot() -> bool:
    try:
        subprocess.run(["sudo", "systemctl", "reboot"], check=True, timeout=10)
        return True
    except Exception:
        logger.exception("Failed to reboot")
        return False


def shutdown() -> bool:
    try:
        subprocess.run(["sudo", "systemctl", "poweroff"], check=True, timeout=10)
        return True
    except Exception:
        logger.exception("Failed to shutdown")
        return False
