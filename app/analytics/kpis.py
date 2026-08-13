"""Pure KPI functions over a chronological list of `Spin` objects (needs timestamps, unlike
app/statistics/engine.py which only needs the bare numbers). Same "descriptive, not predictive"
rule applies — these are operational metrics (how fast is the table running, how often are
corrections needed), not gambling odds.
"""
from __future__ import annotations

from datetime import datetime

from app.models.spin import Spin


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def session_duration_seconds(spins: list[Spin]) -> float:
    """From the first to the last spin's timestamp. 0 for an empty or single-spin period (there's
    no meaningful duration with fewer than two points)."""
    if len(spins) < 2:
        return 0.0
    return (_parse(spins[-1].timestamp) - _parse(spins[0].timestamp)).total_seconds()


def spins_per_hour(spins: list[Spin], duration_seconds: float | None = None) -> float:
    if not spins:
        return 0.0
    duration = duration_seconds if duration_seconds is not None else session_duration_seconds(spins)
    hours = duration / 3600.0
    if hours <= 0:
        return 0.0
    return round(len(spins) / hours, 1)


def spin_intervals_seconds(spins: list[Spin]) -> list[float]:
    times = [_parse(s.timestamp) for s in spins]
    return [(times[i] - times[i - 1]).total_seconds() for i in range(1, len(times))]


def interval_stats(spins: list[Spin]) -> dict:
    intervals = spin_intervals_seconds(spins)
    if not intervals:
        return {"avg_seconds": 0.0, "min_seconds": 0.0, "max_seconds": 0.0}
    return {
        "avg_seconds": round(sum(intervals) / len(intervals), 1),
        "min_seconds": round(min(intervals), 1),
        "max_seconds": round(max(intervals), 1),
    }
