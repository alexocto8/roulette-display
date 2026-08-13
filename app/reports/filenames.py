"""Central filename sanitization (item 42) — every report artifact's name goes through this, so
there is exactly one place that decides what characters are safe for a filename across every
filesystem this might end up on (including a USB stick formatted FAT32, which is pickier than
ext4/Windows about a few characters overlapping with `_UNSAFE` anyway)."""
from __future__ import annotations

import re

_UNSAFE = re.compile(r"[^A-Za-z0-9_-]+")


def sanitize_filename(text: str) -> str:
    cleaned = _UNSAFE.sub("_", (text or "").strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned or "arquivo"


def report_basename(session_row) -> str:
    """`Jackpot_Poker_Club_Mesa_01_2026-08-11_Sessao003` — venue + table + date + sequence, all
    sanitized. The three files of one report package (.pdf/.csv/.json) always share this exact
    basename, differing only by extension."""
    venue = sanitize_filename(session_row["venue_name"] or "Cassino")
    table = sanitize_filename(session_row["table_name"] or "Mesa")
    date_part = (session_row["started_at"] or "")[:10] or "0000-00-00"
    seq = session_row["session_code"].rsplit("-", 1)[-1] if session_row["session_code"] else "000"
    return f"{venue}_{table}_{date_part}_Sessao{seq}"
