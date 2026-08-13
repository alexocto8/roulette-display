"""Read-only network status (section 48) — deliberately NOT a Wi-Fi manager. Listing/selecting/
connecting to SSIDs needs NetworkManager (`nmcli`) with elevated privileges and, more importantly,
real Wi-Fi hardware to test against meaningfully; neither exists in this development environment,
and building a full picker UI in a tiny keyboard-only pygame overlay is a lot of surface for a
feature most fixed-installation casino tables won't need (Ethernet is the realistic default for a
kiosk appliance). This module only ever reads status — it can never be the reason the display
stops working, which is the one hard requirement section 48 actually cares about.

Every check degrades to "indisponível"/"desconhecido" instead of raising if the underlying tool
(`nmcli`, `ip`) isn't present — exactly the case in this sandbox, and a real possibility on a
minimal Raspberry Pi OS Lite image too.
"""
from __future__ import annotations

import logging
import shutil
import subprocess

logger = logging.getLogger("roulette.delivery")

_TIMEOUT = 3


def _run(cmd: list[str]) -> str | None:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=_TIMEOUT)
        return result.stdout.strip() if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def get_network_status() -> dict:
    status = {
        "ethernet": "desconhecido",
        "ip": "-",
        "wifi_ssid": "-",
        "wifi_signal": "-",
        "internet": "desconhecido",
    }

    if shutil.which("nmcli"):
        devices = _run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device"])
        if devices:
            for line in devices.splitlines():
                parts = line.split(":")
                if len(parts) != 3:
                    continue
                _device, dtype, state = parts
                if dtype == "ethernet":
                    status["ethernet"] = "Conectado" if state == "connected" else "Desconectado"

        wifi = _run(["nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL", "device", "wifi"])
        if wifi:
            for line in wifi.splitlines():
                parts = line.split(":")
                if len(parts) == 3 and parts[0] == "yes":
                    status["wifi_ssid"] = parts[1]
                    status["wifi_signal"] = f"{parts[2]}%"
                    break

        ip_out = _run(["nmcli", "-t", "-f", "IP4.ADDRESS", "device", "show"])
        if ip_out:
            first = ip_out.splitlines()[0].split(":")[-1].split("/")[0]
            status["ip"] = first or "-"
    elif shutil.which("ip"):
        addr = _run(["ip", "-4", "-o", "addr", "show", "scope", "global"])
        if addr:
            status["ip"] = addr.split()[3].split("/")[0] if len(addr.split()) > 3 else "-"
            status["ethernet"] = "Conectado"

    status["internet"] = "Disponível" if _has_internet() else "Indisponível"
    return status


def _has_internet() -> bool:
    # Só um teste de conectividade TCP simples (sem depender de DNS de um domínio específico do
    # fornecedor) — 1.1.1.1:53 é um resolvedor público conhecido, não um serviço deste projeto.
    import socket

    try:
        with socket.create_connection(("1.1.1.1", 53), timeout=_TIMEOUT):
            return True
    except OSError:
        return False
