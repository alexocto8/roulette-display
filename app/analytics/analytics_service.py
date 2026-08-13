"""Analytics orchestrator: wires together app/statistics/engine.py (pure number-frequency math),
app/analytics/kpis.py (pure timing math over Spin objects) and Database's report-aware spin
queries into one snapshot per period. Read-only, on-demand (admin panel / report generation) —
never called from the render loop or the keyboard event path.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.analytics import kpis, periods
from app.database.db import Database
from app.models.spin import Spin
from app.statistics import engine as stats


@dataclass
class AnalyticsSnapshot:
    period: str
    period_label: str
    total_spins: int

    session_duration_seconds: float
    spins_per_hour: float
    interval_avg_seconds: float
    interval_min_seconds: float
    interval_max_seconds: float

    correction_count: int
    undo_count: int
    correction_rate: float

    color: stats.BucketStats
    parity: stats.BucketStats
    range_: stats.BucketStats
    dozen: stats.BucketStats
    column: stats.BucketStats

    number_distribution: dict[int, dict] = field(default_factory=dict)  # expected_vs_observed()
    hot: list[tuple[int, int]] = field(default_factory=list)
    cold: list[tuple[int, int]] = field(default_factory=list)
    largest_historical_gaps: dict[int, int | None] = field(default_factory=dict)
    streaks: dict[str, dict[str, int]] = field(default_factory=dict)
    chi_square: dict = field(default_factory=dict)


class AnalyticsService:
    def __init__(self, db: Database):
        self.db = db

    def compute(
        self, roulette_id: int, period: str, *, session_id: int | None = None,
        since: str | None = None, until: str | None = None, hot_n: int = 5, cold_n: int = 5,
    ) -> AnalyticsSnapshot:
        """`session_id` is required for SESSAO_ATUAL/SESSAO_ANTERIOR; `since`/`until` are used
        (and required) only for PERSONALIZADO. Every other DATE_BASED period resolves its own
        range automatically."""
        if period in periods.SESSION_BASED:
            if session_id is None:
                raise ValueError(f"period={period!r} exige session_id")
            spins = self.db.get_report_spins_for_session(session_id)
        elif period == periods.PERSONALIZADO:
            if since is None and until is None:
                raise ValueError("PERSONALIZADO exige since e/ou until")
            spins = self.db.get_report_spins_in_range(roulette_id, since=since, until=until)
        else:
            resolved_since, resolved_until = periods.resolve_date_range(period)
            spins = self.db.get_report_spins_in_range(roulette_id, since=resolved_since, until=resolved_until)

        return self.build_snapshot_from_spins(period, spins, session_id=session_id, hot_n=hot_n, cold_n=cold_n)

    def build_snapshot_from_spins(
        self, period: str, spins: list[Spin], *, session_id: int | None, hot_n: int, cold_n: int,
    ) -> AnalyticsSnapshot:
        numbers = [s.number for s in spins]

        duration = kpis.session_duration_seconds(spins)
        interval = kpis.interval_stats(spins)

        if session_id is not None:
            undo_count = self.db.count_audit_events(session_id=session_id, event_type="SPIN_UNDONE")
        else:
            undo_count = 0  # correção é sempre um evento de sessão — período por data não soma entre sessões

        return AnalyticsSnapshot(
            period=period,
            period_label=periods.LABELS.get(period, period),
            total_spins=len(spins),
            session_duration_seconds=duration,
            spins_per_hour=kpis.spins_per_hour(spins, duration_seconds=duration),
            interval_avg_seconds=interval["avg_seconds"],
            interval_min_seconds=interval["min_seconds"],
            interval_max_seconds=interval["max_seconds"],
            correction_count=undo_count,
            undo_count=undo_count,
            correction_rate=round(100.0 * undo_count / len(spins), 2) if spins else 0.0,
            color=stats.color_stats(numbers),
            parity=stats.parity_stats(numbers),
            range_=stats.range_stats(numbers),
            dozen=stats.dozen_stats(numbers),
            column=stats.column_stats(numbers),
            number_distribution=stats.expected_vs_observed(numbers),
            hot=stats.hottest_numbers(numbers, top_n=hot_n),
            cold=stats.coldest_numbers(numbers, top_n=cold_n),
            largest_historical_gaps=stats.largest_historical_gaps(numbers),
            streaks=stats.compute_streaks(numbers),
            chi_square=stats.chi_square_verdict(numbers),
        )

    def compute_previous_session(self, roulette_id: int, hot_n: int = 5, cold_n: int = 5) -> AnalyticsSnapshot | None:
        """SESSAO_ANTERIOR needs to find the previous session's id first (the two most recent
        rows in `sessions` for this roulette — the newest is current/open, the one before it is
        "previous"). Returns None if there isn't one yet (e.g. right after a fresh install)."""
        recent = self.db.list_sessions(roulette_id, limit=2)
        if len(recent) < 2:
            return None
        previous = recent[1]
        return self.compute(roulette_id, periods.SESSAO_ANTERIOR, session_id=previous["id"], hot_n=hot_n, cold_n=cold_n)
