"""Offline Ed25519 license verification.

License file (`license.dat`) is a JSON envelope: `{"payload": {...}, "signature": "<base64>"}`.
`payload` is signed by the license-generator's private key (never present here, never in this
repo) over its canonical form (`json.dumps(payload, sort_keys=True, separators=(",", ":"))`,
UTF-8). This module only ever holds the *public* key — safe to embed in source, nothing secret
about it — and only ever reads/verifies, never signs.

Payload fields: license_id, device_id (the `RLT-XXXX-XXXX` form from hardware.py — deliberately
the *short* form, not the full 64-char fingerprint: this is the string a customer actually reads
off the "not activated" screen and relays to get a license issued, so it has to be the same string
verified here. It's a 32-bit slice of the SHA-256 digest — not a security weakness in practice at
this product's realistic scale (the birthday bound for a 50% collision chance across the *whole*
fleet ever sold is on the order of tens of thousands of units), and the Ed25519 signature is what
actually prevents forgery either way; the short form only trades a theoretical, astronomically
unlikely device-ID collision for something a human can read and type), customer, casino, table,
issued_at, expires_at (ISO date, or null for a perpetual license), features (list[str]),
schema_version.

Deliberately separate from any release/code-integrity signing (see the audit note in the repo
history on this point): this module answers "is this hardware authorized to run", not "has this
software been tampered with" — a `git pull` updating the app must never invalidate a license,
because the license is bound to `device_id`, not to a hash of the code.
"""
from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app.license.hardware import device_id as compute_device_id

logger = logging.getLogger("roulette.license")

SCHEMA_VERSION = 1


class LicenseStatus(Enum):
    VALID = "valid"
    MISSING = "missing"
    CORRUPT = "corrupt"
    INVALID_SIGNATURE = "invalid_signature"
    DEVICE_MISMATCH = "device_mismatch"
    EXPIRED = "expired"


@dataclass(frozen=True)
class LicenseResult:
    status: LicenseStatus
    payload: dict | None = None
    detail: str = ""  # technical detail for the log file — never shown to the operator as-is

    @property
    def ok(self) -> bool:
        return self.status is LicenseStatus.VALID


def _canonical_bytes(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _load_envelope(license_path: Path) -> tuple[dict, bytes] | None:
    try:
        raw = license_path.read_text(encoding="utf-8")
        envelope = json.loads(raw)
        payload = envelope["payload"]
        signature = base64.b64decode(envelope["signature"], validate=True)
        if not isinstance(payload, dict):
            raise ValueError("payload is not an object")
        return payload, signature
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def _parse_expiry(payload: dict) -> datetime | None:
    raw = payload.get("expires_at")
    if not raw:
        return None
    return datetime.fromisoformat(raw).replace(tzinfo=timezone.utc)


def _effective_now(state_path: Path | None) -> datetime:
    """The latest timestamp this device has ever legitimately observed, monotonic across boots —
    protects a TEMPORARY license's expiry check against a naive "set the clock back" bypass. A
    perpetual license never calls this (nothing to bypass). Not unbreakable: an attacker who also
    deletes/edits the state file defeats it — see the module docstring and the audit answer on
    offline clock tampering for why no purely offline scheme can do better than this."""
    now = datetime.now(timezone.utc)
    if state_path is None:
        return now
    last_seen = now
    try:
        recorded = datetime.fromisoformat(state_path.read_text(encoding="utf-8").strip())
        if recorded.tzinfo is None:
            recorded = recorded.replace(tzinfo=timezone.utc)
        if recorded > now:
            logger.warning(
                "Relógio do sistema (%s) está ATRÁS do último horário observado (%s) — possível "
                "adulteração de relógio. Usando o horário mais recente já visto para checar validade.",
                now.isoformat(), recorded.isoformat(),
            )
            last_seen = recorded
        else:
            last_seen = now
    except (OSError, ValueError):
        pass
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(last_seen.isoformat(), encoding="utf-8")
    except OSError:
        logger.warning("Não foi possível persistir o estado de relógio da licença", exc_info=True)
    return last_seen


def verify_license(
    license_path: str | Path,
    public_key_bytes: bytes,
    state_path: str | Path | None = None,
) -> LicenseResult:
    license_path = Path(license_path)
    if not license_path.exists():
        return LicenseResult(LicenseStatus.MISSING, detail=f"{license_path} não existe")

    loaded = _load_envelope(license_path)
    if loaded is None:
        return LicenseResult(LicenseStatus.CORRUPT, detail="JSON/base64 malformado ou campos ausentes")
    payload, signature = loaded

    try:
        public_key = Ed25519PublicKey.from_public_bytes(public_key_bytes)
        public_key.verify(signature, _canonical_bytes(payload))
    except (InvalidSignature, ValueError):
        return LicenseResult(LicenseStatus.INVALID_SIGNATURE, detail="assinatura não confere")

    licensed_device = payload.get("device_id", "")
    this_device = compute_device_id()
    if licensed_device != this_device:
        return LicenseResult(
            LicenseStatus.DEVICE_MISMATCH, payload=payload,
            detail=f"licença emitida para {licensed_device}, este equipamento é {this_device}",
        )

    try:
        expires_at = _parse_expiry(payload)
    except ValueError:
        return LicenseResult(LicenseStatus.CORRUPT, detail="expires_at malformado")

    if expires_at is not None:
        now = _effective_now(Path(state_path) if state_path else None)
        if now > expires_at:
            return LicenseResult(
                LicenseStatus.EXPIRED, payload=payload,
                detail=f"expirou em {expires_at.isoformat()}, agora é {now.isoformat()}",
            )

    return LicenseResult(LicenseStatus.VALID, payload=payload, detail="ok")
