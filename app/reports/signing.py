"""Report integrity signing: a separate, locally-generated Ed25519 keypair — deliberately NOT
the same key used by app/license/ + license-generator/.

Why separate: the licensing key proves "the vendor authorized this specific hardware to run the
software" — its private half must never exist on any Raspberry Pi (see license-generator/README.md).
Report signing proves something else entirely: "this PDF/JSON came from this table's installation
and was not edited afterward." That has to be verifiable completely offline, on the same device,
with no vendor round-trip — which means the private half of THIS key must live on the Pi. Reusing
the licensing key for this would force exactly the key-on-the-Pi exposure the licensing
architecture was built to avoid. Two keys, two different security properties, no shortcuts.

Generated once, on first use, and persisted at `config.report_signing_key_path` (0600, gitignored
by convention same as license.dat/private keys — this file lives under data/, already excluded).
There is no external authority for this key — it is not a chain of trust to a third party, only a
tamper-evidence mechanism: given the report files and this device's public key (exportable via the
admin panel, mirroring the license Device ID display), anyone can confirm a report file is exactly
what this table generated.
"""
from __future__ import annotations

import logging
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
    load_pem_private_key,
)

logger = logging.getLogger("roulette.reports")


def get_or_create_signing_key(key_path: str | Path) -> Ed25519PrivateKey:
    path = Path(key_path)
    if path.exists():
        return load_pem_private_key(path.read_bytes(), password=None)

    logger.info("Gerando nova chave de assinatura de relatórios em %s (primeira vez)", path)
    private_key = Ed25519PrivateKey.generate()
    path.parent.mkdir(parents=True, exist_ok=True)
    pem = private_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(pem)
    tmp.chmod(0o600)
    tmp.replace(path)
    return private_key


def public_key_hex(key_path: str | Path) -> str:
    private_key = get_or_create_signing_key(key_path)
    public_bytes = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return public_bytes.hex()


def sign_bytes(key_path: str | Path, data: bytes) -> bytes:
    private_key = get_or_create_signing_key(key_path)
    return private_key.sign(data)


def verify_signature(public_key_hex_str: str, data: bytes, signature: bytes) -> bool:
    try:
        public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex_str))
        public_key.verify(signature, data)
        return True
    except Exception:
        return False
