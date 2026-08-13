"""Pure hash-chain logic for the audit log — no SQLite/DB knowledge here, just canonical hashing
over event fields. Kept separate from app/database/db.py (which does the actual INSERT while
holding the lock) so the exact same canonicalization is trivially unit-testable and reusable by
any future offline verification tool that only has a copy of the .db file, not a live connection.

Deliberately NOT a blockchain: no proof-of-work, no distributed consensus, no peers, no mining.
It is a local, single-writer, tamper-evident chain — each event's hash commits to the previous
event's hash plus its own content, so editing or deleting any past row breaks every hash after it.
That is the entire guarantee: local database file integrity, verifiable offline, with a clear
answer to "was anything in this table edited after the fact?" — not a claim of distributed trust.
"""
from __future__ import annotations

import hashlib
import json

GENESIS_HASH = "0" * 64  # previous_hash for the very first audit event ever written

# Order doesn't matter for the hash itself (the payload dict is sorted before serializing), but
# keeping an explicit tuple here documents exactly what is — and, just as importantly, is NOT —
# covered by the chain (e.g. the internal `id` autoincrement is deliberately excluded: it's a
# storage detail, not part of the event's meaning).
_CHAINED_FIELDS = (
    "event_id", "created_at", "event_type", "session_id", "spin_id", "table_id",
    "actor_type", "actor_id", "source", "old_value", "new_value", "reason", "metadata_json",
)


def _canonical_bytes(previous_hash: str, event: dict) -> bytes:
    payload = {"previous_hash": previous_hash, **{k: event.get(k) for k in _CHAINED_FIELDS}}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def compute_event_hash(previous_hash: str, event: dict) -> str:
    """`event` needs at least the fields in `_CHAINED_FIELDS` (missing ones are treated as
    None/null, same as SQLite would store them)."""
    return hashlib.sha256(_canonical_bytes(previous_hash, event)).hexdigest()


def verify_chain(events: list[dict]) -> tuple[bool, str | None]:
    """`events` in insertion order (oldest first), each a dict with the chained fields plus
    `previous_hash`/`event_hash`/`event_id`. Returns (True, None) if the whole chain recomputes
    correctly, or (False, event_id) naming the first event whose hash doesn't match — either its
    own content was altered, a previous event was altered, or an event was deleted/reordered."""
    expected_previous = GENESIS_HASH
    for event in events:
        recomputed = compute_event_hash(expected_previous, event)
        if event.get("previous_hash") != expected_previous or event.get("event_hash") != recomputed:
            return False, event.get("event_id")
        expected_previous = event["event_hash"]
    return True, None
