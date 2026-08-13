"""Builds the canonical JSON representation of a closed session (section 41 of the spec) — this
is what report.sha256/report.sig actually sign, and what the PDF/CSV are generated FROM (single
source of truth for one report, so the three files can never disagree with each other).
"""
from __future__ import annotations

import json

from app.models.roulette_data import color_of, column_of, dozen_of, parity_of, range_of
from app.models.spin import Spin

SCHEMA_VERSION = 1


def _spin_status(spin: Spin) -> str:
    return "ATIVO" if not spin.deleted else "ARQUIVADO"


def build_session_dict(
    session_row, spins: list[Spin], analytics: dict | None = None, audit_summary: dict | None = None,
) -> dict:
    spin_records = []
    for i, spin in enumerate(spins, start=1):
        spin_records.append({
            "sequence": i,
            "number": spin.number,
            "color": spin.color,
            "parity": parity_of(spin.number),
            "range": range_of(spin.number),
            "dozen": dozen_of(spin.number),
            "column": column_of(spin.number),
            "created_at": spin.created_at,
            "input_source": "KEYPAD",
            "status": _spin_status(spin),
        })

    payload = {
        "schema_version": SCHEMA_VERSION,
        "venue": {"name": session_row["venue_name"]},
        "table": {
            "id": session_row["table_id"],
            "code": session_row["table_code"],
            "name": session_row["table_name"],
            "location": session_row["table_location"],
        },
        "session": {
            "id": session_row["session_code"],
            "started_at": session_row["started_at"],
            "ended_at": session_row["ended_at"],
            "spin_count": len(spins),
        },
        "spins": spin_records,
    }
    if analytics is not None:
        payload["analytics"] = analytics
    if audit_summary is not None:
        payload["audit_summary"] = audit_summary
    return payload


def canonical_bytes(session_dict: dict) -> bytes:
    """Deterministic serialization — same input always produces the same bytes, which is what
    gets hashed/signed. Pretty-printed (not canonical-minified) on disk for readability, but the
    hash is always computed over THIS canonical form regardless of how the file on disk is
    formatted, so re-serializing to pretty JSON never invalidates the signature."""
    return json.dumps(session_dict, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def write_session_json(session_dict: dict, dest_path) -> None:
    from pathlib import Path

    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(session_dict, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8")
