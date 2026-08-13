"""Named analysis periods (item 18 da especificação). Two kinds, handled differently by
AnalyticsService:

- Session-based (SESSAO_ATUAL, SESSAO_ANTERIOR): resolved against the `sessions` table, not a
  date range — a session's data is exactly its snapshot-scoped spins (see
  Database.get_report_spins_for_session).
- Date-based (everything else): resolved here into a (since, until) ISO-8601 range fed to
  Database.get_report_spins_in_range. `until=None` always means "no upper bound" (up to now).
"""
from __future__ import annotations

from datetime import datetime, timedelta

SESSAO_ATUAL = "SESSAO_ATUAL"
SESSAO_ANTERIOR = "SESSAO_ANTERIOR"
HOJE = "HOJE"
ONTEM = "ONTEM"
ULTIMOS_7_DIAS = "ULTIMOS_7_DIAS"
ULTIMOS_30_DIAS = "ULTIMOS_30_DIAS"
ULTIMOS_90_DIAS = "ULTIMOS_90_DIAS"
ANO = "ANO"
LIFETIME = "LIFETIME"
PERSONALIZADO = "PERSONALIZADO"

SESSION_BASED = frozenset({SESSAO_ATUAL, SESSAO_ANTERIOR})
DATE_BASED = frozenset({HOJE, ONTEM, ULTIMOS_7_DIAS, ULTIMOS_30_DIAS, ULTIMOS_90_DIAS, ANO, LIFETIME, PERSONALIZADO})

LABELS = {
    SESSAO_ATUAL: "Sessão atual",
    SESSAO_ANTERIOR: "Sessão anterior",
    HOJE: "Hoje",
    ONTEM: "Ontem",
    ULTIMOS_7_DIAS: "Últimos 7 dias",
    ULTIMOS_30_DIAS: "Últimos 30 dias",
    ULTIMOS_90_DIAS: "Últimos 90 dias",
    ANO: "Este ano",
    LIFETIME: "Todo o histórico",
    PERSONALIZADO: "Período personalizado",
}


def resolve_date_range(period: str, now: datetime | None = None) -> tuple[str | None, str | None]:
    """Returns (since, until) as ISO-8601 strings (or None) for a DATE_BASED period. Raises
    ValueError for a SESSION_BASED period (those are resolved by session id, not by date —
    calling this on one is a programming error, not a runtime edge case to handle gracefully)."""
    if period not in DATE_BASED:
        raise ValueError(f"{period!r} não é um período baseado em data")
    now = now or datetime.now()
    if period == LIFETIME:
        return None, None
    if period == HOJE:
        since = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return since.isoformat(), None
    if period == ONTEM:
        end_of_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start_of_yesterday = end_of_today - timedelta(days=1)
        return start_of_yesterday.isoformat(), end_of_today.isoformat()
    if period == ULTIMOS_7_DIAS:
        return (now - timedelta(days=7)).isoformat(), None
    if period == ULTIMOS_30_DIAS:
        return (now - timedelta(days=30)).isoformat(), None
    if period == ULTIMOS_90_DIAS:
        return (now - timedelta(days=90)).isoformat(), None
    if period == ANO:
        since = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        return since.isoformat(), None
    if period == PERSONALIZADO:
        raise ValueError("PERSONALIZADO exige since/until explícitos do chamador, não tem resolução automática")
    raise AssertionError(f"período não tratado: {period!r}")  # pragma: no cover — DATE_BASED já cobre todos acima
