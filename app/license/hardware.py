"""Device fingerprint: a deterministic identifier tied to the physical Raspberry Pi, not to
anything stored on the SD card.

Why the SoC serial and not, say, the filesystem UUID or a random ID saved to a file: the whole
point of this fingerprint is that `dd`-cloning the SD card to a different physical Pi must produce
a *different* Device ID. Anything read from a file on the card is copied bit-for-bit by that clone
— it would prove nothing. The CPU serial lives in one-time-programmable memory burned into the SoC
at the factory (readable, not writable, not stored anywhere on the card), so it's the one value
that's actually anchored to *this* piece of silicon.

Two other identifiers were considered and rejected as the primary anchor:
- MAC address: trivially changed in software (`ip link set ... address ...`), no better than a
  config value for this purpose.
- Filesystem/root UUID: this *is* copied by a clone, so using it alone would be actively wrong.

On a real Raspberry Pi, `/proc/cpuinfo` always has a `Serial` line for the SoC. This module falls
back to `/etc/machine-id` (present on any systemd Linux system) when that line is missing —
strictly so this code runs on dev machines/CI/this sandbox during development. That fallback is
NOT hardware-anchored (machine-id lives on disk) and must never be treated as equivalent security
in production; `get_hardware_id()` reports which source it used so the caller can log it.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("roulette.license")

_CPUINFO_PATH = Path("/proc/cpuinfo")
_MACHINE_ID_PATH = Path("/etc/machine-id")


@dataclass(frozen=True)
class HardwareId:
    raw: str  # the raw identifier string used as hash input
    source: str  # "cpuinfo-serial" (real Pi) or "machine-id" (fallback, dev-only)

    @property
    def is_hardware_anchored(self) -> bool:
        return self.source == "cpuinfo-serial"


def _read_cpuinfo_serial() -> str | None:
    if not _CPUINFO_PATH.exists():
        return None
    try:
        for line in _CPUINFO_PATH.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.lower().startswith("serial"):
                _, _, value = line.partition(":")
                value = value.strip()
                # Placas sem serial de verdade reportam só zeros — não é um identificador real.
                if value and value.strip("0") != "":
                    return value
    except OSError:
        logger.warning("Could not read %s", _CPUINFO_PATH, exc_info=True)
    return None


def _read_machine_id() -> str | None:
    try:
        value = _MACHINE_ID_PATH.read_text(encoding="utf-8").strip()
        return value or None
    except OSError:
        return None


def get_hardware_id() -> HardwareId:
    serial = _read_cpuinfo_serial()
    if serial is not None:
        return HardwareId(raw=serial, source="cpuinfo-serial")

    logger.warning(
        "Serial do SoC não encontrado em %s — usando %s como identificador. "
        "Isso é esperado fora de um Raspberry Pi real (dev/CI); em produção, um Device ID baseado "
        "nisso NÃO está ancorado ao hardware.",
        _CPUINFO_PATH, _MACHINE_ID_PATH,
    )
    machine_id = _read_machine_id()
    if machine_id is not None:
        return HardwareId(raw=machine_id, source="machine-id")

    # Nenhuma fonte disponível — situação anômala (nem Pi, nem systemd). Ainda assim retorna algo
    # determinístico ao invés de lançar, pra não derrubar o boot por causa disso; o fingerprint
    # resultante é essencialmente uma string fixa (raw="unknown"), então qualquer licença exigirá
    # ativação/atenção manual, o que é o comportamento seguro por padrão nesse caso.
    logger.error("Nenhuma fonte de identificador de hardware disponível — usando fallback fixo.")
    return HardwareId(raw="unknown", source="none")


def fingerprint(hw: HardwareId | None = None) -> str:
    """Full SHA-256 hex digest — o valor real comparado na validação da licença. Nunca truncado
    (evitar qualquer risco de colisão na checagem de segurança em si)."""
    hw = hw or get_hardware_id()
    return hashlib.sha256(hw.raw.encode("utf-8")).hexdigest()


def device_id(hw: HardwareId | None = None) -> str:
    """Formato amigável pra tela/comunicação com o cliente (`RLT-XXXX-XXXX`) — um prefixo de 32
    bits do mesmo fingerprint completo. É este valor curto (não o `fingerprint()` de 64 caracteres)
    que `app.license.verify` de fato compara para decidir se a licença é válida para este
    equipamento: precisa ser a mesma string que o cliente lê na tela e relata para a emissão da
    licença, então o valor mostrado e o valor verificado têm que ser um só. Ver a análise de risco
    de colisão completa em app/license/verify.py — o que realmente impede forjar uma licença é a
    assinatura Ed25519, não o tamanho deste identificador."""
    digest = fingerprint(hw)
    return f"RLT-{digest[:4].upper()}-{digest[4:8].upper()}"
