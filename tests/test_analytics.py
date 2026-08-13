"""Item 54 da especificação: casos determinísticos de analytics, incluindo o zero tratado
corretamente em todas as categorias (o mesmo cuidado já coberto para app/statistics/engine.py na
rodada de hardening anterior, agora estendido às KPIs/streaks/esperado-vs-observado novas)."""
from __future__ import annotations

from app.analytics import kpis, periods
from app.analytics.analytics_service import AnalyticsService
from app.config import Config
from app.database.db import Database
from app.services.spin_service import SpinService
from app.statistics import engine as stats


def make_service(tmp_path) -> SpinService:
    config = Config(database_path=str(tmp_path / "roulette.db"))
    db = Database(tmp_path / "roulette.db")
    db.initialize()
    return SpinService(db, config)


# 17. contagem dos números correta -------------------------------------------------------------


def test_number_distribution_counts_are_correct(tmp_path):
    service = make_service(tmp_path)
    for n in (5, 5, 5, 12, 0):
        service.register_spin(n)
    dist = stats.expected_vs_observed([5, 5, 5, 12, 0])
    assert dist[5]["observed_count"] == 3
    assert dist[12]["observed_count"] == 1
    assert dist[0]["observed_count"] == 1
    assert dist[7]["observed_count"] == 0


# 18/19/20. red/black, even/odd, low/high corretos (zero excluído dos totais) -------------------


def test_color_even_odd_low_high_totals_exclude_zero_correctly():
    history = [1, 2, 0, 3, 4]  # 1=red/odd/low, 2=black/even/low, 0=green, 3=red/odd/low, 4=black/even/low
    color = stats.color_stats(history)
    parity = stats.parity_stats(history)
    range_ = stats.range_stats(history)
    assert color.total == 5  # zero conta pra total de cor (ele É uma cor: verde)
    assert color.counts["green"] == 1
    assert parity.total == 4  # zero NÃO conta pra par/ímpar
    assert range_.total == 4  # zero NÃO conta pra faixa


# 21. dozens correta ------------------------------------------------------------------------


def test_dozen_stats_correct_and_excludes_zero():
    history = [1, 13, 25, 0, 12, 24, 36]
    dozen = stats.dozen_stats(history)
    assert dozen.counts["1"] == 2  # 1, 12
    assert dozen.counts["2"] == 2  # 13, 24
    assert dozen.counts["3"] == 2  # 25, 36
    assert dozen.total == 6  # zero excluído


# 22. columns correta -------------------------------------------------------------------------


def test_column_stats_correct_and_excludes_zero():
    history = [1, 2, 3, 4, 0]
    column = stats.column_stats(history)
    assert column.counts["1"] == 2  # 1, 4
    assert column.counts["2"] == 1  # 2
    assert column.counts["3"] == 1  # 3
    assert column.total == 4


# 23. hot/cold correto ------------------------------------------------------------------------


def test_hot_cold_correct():
    history = [7, 7, 7, 3, 3, 12]
    hot = stats.hottest_numbers(history, top_n=2)
    assert hot[0] == (7, 3)
    assert hot[1] == (3, 2)
    cold = stats.coldest_numbers(history, top_n=1)
    # 12 foi o mais recente -> só 0 giros ausente, então NÃO vence: os 34 números nunca vistos
    # empatam no máximo de ausência (len(history)=6), desempate por número menor -> 0 primeiro.
    assert cold[0] == (0, 6)


# 24. spins_since_last_seen correto ------------------------------------------------------------


def test_spins_since_last_seen_correct():
    since = stats.spins_since_last_occurrence([5, 12, 5, 0])
    assert since[0] == 0  # zero foi o último
    assert since[5] == 1
    assert since[12] == 2


def test_largest_historical_gap_correct():
    # número 5 aparece nos índices 0, 3, 7 -> gaps de (3-0-1)=2 e (7-3-1)=3 -> maior = 3
    history = [5, 1, 1, 5, 1, 1, 1, 5]
    gaps = stats.largest_historical_gaps(history)
    assert gaps[5] == 3
    # 1 aparece nos índices 1,2,4,5,6 -> gaps: 0,1,0,0 -> maior = 1
    assert gaps[1] == 1


def test_largest_historical_gap_none_for_numbers_seen_fewer_than_twice():
    gaps = stats.largest_historical_gaps([5, 12])
    assert gaps[5] is None
    assert gaps[17] is None  # nunca visto


# 25. streaks corretas ------------------------------------------------------------------------


def test_streaks_correct_for_red_black():
    # 1=red,3=red,2=black,5=red,7=red,9=red,0=green(quebra)
    history = [1, 3, 2, 5, 7, 9, 0]
    streaks = stats.compute_streaks(history)
    assert streaks["red"]["largest"] == 3  # 5,7,9
    assert streaks["red"]["current"] == 0  # quebrado pelo zero no final
    assert streaks["black"]["largest"] == 1


def test_streaks_current_reflects_the_trailing_run():
    history = [2, 4, 6, 8]  # todos even, sequência em andamento
    streaks = stats.compute_streaks(history)
    assert streaks["even"]["current"] == 4
    assert streaks["even"]["largest"] == 4
    assert streaks["odd"]["current"] == 0


def test_zero_breaks_every_streak_category():
    history = [2, 2, 0]  # termina em zero -> nenhuma categoria pode ter sequência em andamento
    streaks = stats.compute_streaks(history)
    assert streaks["even"]["largest"] == 2  # antes do zero
    assert streaks["even"]["current"] == 0  # zero quebrou -> nada em andamento
    assert streaks["low"]["current"] == 0  # zero não é low nem high
    assert streaks["high"]["current"] == 0


# 26. spins/hour correto -----------------------------------------------------------------------


def test_spins_per_hour_correct(tmp_path):
    from app.models.spin import Spin

    spins = [
        Spin(id=1, roulette_id=1, number=1, color="red", timestamp="2026-01-01T10:00:00", created_at="2026-01-01T10:00:00"),
        Spin(id=2, roulette_id=1, number=2, color="black", timestamp="2026-01-01T10:30:00", created_at="2026-01-01T10:30:00"),
        Spin(id=3, roulette_id=1, number=3, color="red", timestamp="2026-01-01T11:00:00", created_at="2026-01-01T11:00:00"),
    ]
    duration = kpis.session_duration_seconds(spins)
    assert duration == 3600.0  # 1h entre o primeiro e o último
    assert kpis.spins_per_hour(spins, duration_seconds=duration) == 3.0  # 3 giros numa hora


def test_spins_per_hour_zero_for_single_spin():
    from app.models.spin import Spin

    spins = [Spin(id=1, roulette_id=1, number=1, color="red", timestamp="2026-01-01T10:00:00", created_at="2026-01-01T10:00:00")]
    assert kpis.spins_per_hour(spins) == 0.0


def test_interval_stats_correct():
    from app.models.spin import Spin

    spins = [
        Spin(id=1, roulette_id=1, number=1, color="red", timestamp="2026-01-01T10:00:00", created_at="x"),
        Spin(id=2, roulette_id=1, number=2, color="black", timestamp="2026-01-01T10:00:30", created_at="x"),
        Spin(id=3, roulette_id=1, number=3, color="red", timestamp="2026-01-01T10:01:30", created_at="x"),
    ]
    result = kpis.interval_stats(spins)
    assert result["min_seconds"] == 30.0
    assert result["max_seconds"] == 60.0
    assert result["avg_seconds"] == 45.0


# 27. correction_rate correto -------------------------------------------------------------------


def test_correction_rate_correct(tmp_path):
    service = make_service(tmp_path)
    service.register_spin(1)
    service.register_spin(2)
    service.register_spin(3)
    service.register_spin(4)
    service.undo_last()  # 1 correção sobre 4 registrados = 25%

    analytics = AnalyticsService(service.db)
    session = service.session_service.ensure_open_session()
    snapshot = analytics.compute(1, periods.SESSAO_ATUAL, session_id=session["id"])

    assert snapshot.total_spins == 3  # o desfeito não conta mais como giro ativo
    assert snapshot.undo_count == 1
    assert snapshot.correction_rate == round(100 * 1 / 3, 2)


def test_correction_rate_zero_when_no_spins(tmp_path):
    service = make_service(tmp_path)
    analytics = AnalyticsService(service.db)
    session = service.session_service.ensure_open_session()
    snapshot = analytics.compute(1, periods.SESSAO_ATUAL, session_id=session["id"])
    assert snapshot.correction_rate == 0.0
    assert snapshot.total_spins == 0


# 28. resultado 0 tratado corretamente em TODAS as categorias (verificação agregada) -------------


def test_zero_is_never_counted_in_parity_range_dozen_column_or_streaks():
    history = [0, 0, 0]
    assert stats.parity_stats(history).total == 0
    assert stats.range_stats(history).total == 0
    assert stats.dozen_stats(history).total == 0
    assert stats.column_stats(history).total == 0
    streaks = stats.compute_streaks(history)
    for category in ("red", "black", "even", "odd", "low", "high", "dozen1", "dozen2", "dozen3"):
        assert streaks[category]["largest"] == 0
        assert streaks[category]["current"] == 0
    # mas zero CONTA como cor (verde) e como número (frequência/hot-cold/gaps)
    assert stats.color_stats(history).counts["green"] == 3
    assert stats.frequency(history)[0] == 3


# -- chi-quadrado: amostra insuficiente não gera veredito sensacionalista ------------------------


def test_chi_square_not_applicable_below_minimum_sample():
    result = stats.chi_square_verdict([1, 2, 3])
    assert result["applicable"] is False
    assert "amostra pequena" in result["reason"]


def test_chi_square_applicable_and_reasonable_for_uniform_large_sample():
    import itertools

    # amostra perfeitamente uniforme (5 de cada número, 0-36) -> chi-quadrado deve ficar baixo
    history = list(itertools.chain.from_iterable([n] * 5 for n in range(37)))
    result = stats.chi_square_verdict(history)
    assert result["applicable"] is True
    assert result["chi_square"] < 1.0  # distribuição perfeitamente uniforme -> chi-quadrado ~0
    assert "dentro da faixa" in result["verdict"]


def test_chi_square_never_declares_a_defective_wheel():
    """Trava textual explícita: seja qual for o veredito, nunca deve soar como acusação."""
    import itertools

    history = list(itertools.chain.from_iterable([n] * 5 for n in range(37)))
    result = stats.chi_square_verdict(history)
    assert "defeituos" not in result["verdict"].lower()
    assert "roda" not in result["verdict"].lower()


# -- períodos: resolução de datas ----------------------------------------------------------------


def test_lifetime_period_has_no_bounds():
    since, until = periods.resolve_date_range(periods.LIFETIME)
    assert since is None and until is None


def test_today_period_has_only_a_lower_bound():
    since, until = periods.resolve_date_range(periods.HOJE)
    assert since is not None
    assert until is None


def test_yesterday_period_has_both_bounds():
    since, until = periods.resolve_date_range(periods.ONTEM)
    assert since is not None and until is not None
    assert since < until


def test_session_based_period_raises_if_resolved_as_date_range():
    try:
        periods.resolve_date_range(periods.SESSAO_ATUAL)
        assert False, "deveria ter levantado ValueError"
    except ValueError:
        pass
