from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Spin:
    """A single recorded roulette result. Immutable — undo creates a soft-delete, not a mutation."""

    id: int
    roulette_id: int
    number: int
    color: str
    timestamp: str  # ISO-8601, when the ball actually landed (operator-confirmed instant)
    created_at: str  # ISO-8601, when the row was written to the DB
    deleted: bool = False
