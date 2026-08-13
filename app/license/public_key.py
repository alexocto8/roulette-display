"""The Ed25519 PUBLIC key used to verify license.dat — safe to ship, nothing secret about it (only
the private key, held exclusively by license-generator/ outside this repo, can *sign* a license).

Regenerated only if the keypair in license-generator/ is ever rotated (see that tool's README for
why that's a rare, deliberate, all-hardware-affecting event) — copy the new
`license-generator/keys/public_key.hex` value here when that happens.
"""
from __future__ import annotations

PUBLIC_KEY_HEX = "95c7f0d9ebe298e43474b6fa00a62925d2fb68fd0ff5b5958a7b76d3375221d0"


def public_key_bytes() -> bytes:
    return bytes.fromhex(PUBLIC_KEY_HEX)
