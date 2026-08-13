from app.statistics import engine as stats


def test_frequency_includes_zero_count_for_absent_numbers():
    counts = stats.frequency([1, 1, 2])
    assert counts[1] == 2
    assert counts[2] == 1
    assert counts[3] == 0
    assert len(counts) == 37


def test_color_stats_percentages():
    history = [1, 2, 0, 1]  # red, black, green, red
    result = stats.color_stats(history)
    assert result.total == 4
    assert result.counts["red"] == 2
    assert result.counts["black"] == 1
    assert result.counts["green"] == 1
    assert result.percentage("red") == 50.0


def test_color_stats_respects_window():
    history = [1, 1, 1, 2]  # only last 2 should count: [1, 2]
    result = stats.color_stats(history, window=2)
    assert result.total == 2
    assert result.counts["red"] == 1
    assert result.counts["black"] == 1


def test_parity_excludes_zero_from_total():
    history = [0, 1, 2]
    result = stats.parity_stats(history)
    assert result.total == 2
    assert result.counts["odd"] == 1
    assert result.counts["even"] == 1


def test_range_stats():
    history = [1, 18, 19, 36, 0]
    result = stats.range_stats(history)
    assert result.total == 4
    assert result.counts["low"] == 2
    assert result.counts["high"] == 2


def test_dozen_stats():
    history = [1, 13, 25, 0]
    result = stats.dozen_stats(history)
    assert result.counts["1"] == 1
    assert result.counts["2"] == 1
    assert result.counts["3"] == 1
    assert result.total == 3


def test_column_stats():
    history = [1, 2, 3, 4]
    result = stats.column_stats(history)
    assert result.counts["1"] == 2  # 1 and 4
    assert result.counts["2"] == 1
    assert result.counts["3"] == 1


def test_hottest_numbers_orders_by_count_then_number():
    history = [5, 5, 3, 3, 7]
    hottest = stats.hottest_numbers(history, top_n=3)
    assert hottest[0] == (3, 2)  # tie with 5, lower number wins
    assert hottest[1] == (5, 2)
    assert hottest[2] == (7, 1)


def test_hottest_numbers_excludes_absent_numbers():
    history = [1, 1]
    hottest = stats.hottest_numbers(history, top_n=37)
    assert all(count > 0 for _, count in hottest)
    assert len(hottest) == 1


def test_spins_since_last_occurrence_zero_for_most_recent():
    history = [1, 2, 3]
    since = stats.spins_since_last_occurrence(history)
    assert since[3] == 0
    assert since[2] == 1
    assert since[1] == 2


def test_spins_since_last_occurrence_full_length_for_never_seen():
    history = [1, 1, 1]
    since = stats.spins_since_last_occurrence(history)
    assert since[9] == 3


def test_coldest_numbers_orders_most_absent_first():
    history = [1, 2, 1]  # 1 last at idx2 (0 ago), 2 last at idx1 (1 ago), others never seen (3 ago)
    coldest = stats.coldest_numbers(history, top_n=37)
    # never-seen numbers (3 ago) should rank above 2 (1 ago) and 1 (0 ago)
    assert coldest[0][1] == 3
    assert coldest[-1] == (1, 0)
    assert coldest[-2] == (2, 1)


def test_hottest_and_coldest_on_empty_history():
    assert stats.hottest_numbers([], top_n=5) == []
    coldest = stats.coldest_numbers([], top_n=3)
    assert all(v == 0 for _, v in coldest)


# -- zero: categoria própria, nunca par/ímpar/vermelho/preto/faixa/dúzia/coluna -------------------
# Zero é a única categoria "própria" da roleta: não deve contar para NENHUMA das outras
# classificações binárias/ternárias. Cobertura explícita item por item (não só indireta via outros
# testes) porque é o tipo de regressão sutil e fácil de reintroduzir sem perceber.


def test_zero_is_not_red_nor_black():
    result = stats.color_stats([0])
    assert result.counts["red"] == 0
    assert result.counts["black"] == 0
    assert result.counts["green"] == 1
    assert result.total == 1  # zero SEGUE contando para o total de cor (ele é verde)


def test_zero_has_no_parity():
    result = stats.parity_stats([0])
    assert result.counts["odd"] == 0
    assert result.counts["even"] == 0
    assert result.total == 0  # excluído do total: zero não é par nem ímpar


def test_zero_has_no_range():
    result = stats.range_stats([0])
    assert result.counts["low"] == 0
    assert result.counts["high"] == 0
    assert result.total == 0


def test_zero_has_no_dozen():
    result = stats.dozen_stats([0])
    assert result.counts == {"1": 0, "2": 0, "3": 0}
    assert result.total == 0


def test_zero_has_no_column():
    result = stats.column_stats([0])
    assert result.counts == {"1": 0, "2": 0, "3": 0}
    assert result.total == 0


def test_zero_excluded_from_hottest_and_coldest_denominators_but_still_counted_as_a_number():
    # Zero PODE aparecer em quente/frio (ele é um número válido de roleta, 0-36) — só não tem
    # par/ímpar/faixa/dúzia/coluna. Isso é diferente das outras exclusões acima.
    hottest = stats.hottest_numbers([0, 0, 1], top_n=37)
    assert (0, 2) in hottest
    coldest = stats.coldest_numbers([1, 2, 0], top_n=37)
    assert dict(coldest)[0] == 0  # zero foi o último a sair -> 0 giros ausente


def test_roulette_data_zero_classification_helpers_directly():
    from app.models.roulette_data import color_of, column_of, dozen_of, parity_of, range_of

    assert color_of(0) == "green"
    assert parity_of(0) is None
    assert range_of(0) is None
    assert dozen_of(0) is None
    assert column_of(0) is None
