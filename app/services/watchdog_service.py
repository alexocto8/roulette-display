"""Minimal sd_notify client — no new dependency, just the documented protocol (systemd.exec(5),
"Notify" section): write ASCII key=value lines to the AF_UNIX datagram socket named by the
$NOTIFY_SOCKET environment variable that systemd sets on the service's process.

Used for two things, both opt-in via `systemd/roulette-display.service` (`Type=notify` +
`WatchdogSec=`):

- `ready()`: tells systemd the app finished starting (splash done, main loop about to run) — a
  `Type=notify` unit blocks its start job until this arrives, so calling it too early (before the
  app can actually respond to input) would make systemd think startup is done when it isn't.
- `heartbeat()`: proves the process is not just alive but *responsive* — `Restart=always` alone
  only restarts on process exit, never on a hang. Call this periodically (well inside
  `WatchdogSec`); if it stops arriving, systemd kills and restarts the service.

Outside of systemd (local dev, `python3 main.py` directly, or a systemd unit without
`Type=notify`), `$NOTIFY_SOCKET` is unset and every call here is a silent no-op — never raises,
never required for the app to run.
"""
from __future__ import annotations

import logging
import os
import socket

logger = logging.getLogger("roulette.watchdog")


def _notify(message: str) -> None:
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    if addr.startswith("@"):
        addr = "\0" + addr[1:]  # abstract namespace socket, per the sd_notify(3) convention
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect(addr)
            sock.sendall(message.encode("ascii"))
    except OSError:
        # Best-effort signaling only — a failure here must never take down the display loop.
        logger.warning("sd_notify failed for %r", message, exc_info=True)


def ready() -> None:
    _notify("READY=1")


def heartbeat() -> None:
    _notify("WATCHDOG=1")


def status(text: str) -> None:
    """Free-text status systemd shows in `systemctl status` — purely informational."""
    _notify(f"STATUS={text}")
