"""Pure statistics functions over a chronological list of spin numbers (ints, oldest first).

Deliberately decoupled from the database and UI: every function here takes plain data in and
returns plain data out, so it is trivially unit-testable and reusable by a future web dashboard
or REST API without dragging pygame/sqlite along.

IMPORTANT: everything here is *descriptive* of past results. Nothing in this module should ever
be read as a prediction of future spins (roulette outcomes are independent trials).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from app.models.roulette_data import (
    COLOR_BLACK,
    COLOR_GREEN,
    COLOR_RED,
    PARITY_EVEN,
    PARITY_ODD,
    RANGE_HIGH,
    RANGE_LOW,
    VALID_NUMBERS,
    color_of,
    column_of,
    dozen_of,
    parity_of,
    range_of,
)


def _window(history: list[int], window: int | None) -> list[int]:
    """Last `window` spins (most recent), or the full history if window is None/<=0."""
    if not window or window <= 0 or window >= len(history):
        return history
    return history[-window:]


def frequency(history: list[int]) -> dict[int, int]:
    """Count of occurrences per number (0-36), including numbers that never appeared (count 0)."""
    counts = {n: 0 for n in VALID_NUMBERS}
    for n in history:
        counts[n] += 1
    return counts


@dataclass
class BucketStats:
    """Generic two/three-way bucket breakdown, e.g. red/black/green or low/high."""

    counts: dict[str, int] = field(default_factory=dict)
    total: int = 0

    def percentage(self, key: str) -> float:
        if self.total == 0:
            return 0.0
        return round(100.0 * self.counts.get(key, 0) / self.total, 1)


def color_stats(history: list[int], window: int | None = None) -> BucketStats:
    sample = _window(history, window)
    counts = {COLOR_RED: 0, COLOR_BLACK: 0, COLOR_GREEN: 0}
    for n in sample:
        counts[color_of(n)] += 1
    return BucketStats(counts=counts, total=len(sample))


def parity_stats(history: list[int], window: int | None = None) -> BucketStats:
    """Even/odd. Zero is excluded from the total (it has no parity)."""
    sample = _window(history, window)
    counts = {PARITY_EVEN: 0, PARITY_ODD: 0}
    total = 0
    for n in sample:
        p = parity_of(n)
        if p is not None:
            counts[p] += 1
            total += 1
    return BucketStats(counts=counts, total=total)


def range_stats(history: list[int], window: int | None = None) -> BucketStats:
    """Low (1-18) / High (19-36). Zero is excluded from the total."""
    sample = _window(history, window)
    counts = {RANGE_LOW: 0, RANGE_HIGH: 0}
    total = 0
    for n in sample:
        r = range_of(n)
        if r is not None:
            counts[r] += 1
            total += 1
    return BucketStats(counts=counts, total=total)


def dozen_stats(history: list[int], window: int | None = None) -> BucketStats:
    sample = _window(history, window)
    counts = {"1": 0, "2": 0, "3": 0}
    total = 0
    for n in sample:
        d = dozen_of(n)
        if d is not None:
            counts[str(d)] += 1
            total += 1
    return BucketStats(counts=counts, total=total)


def column_stats(history: list[int], window: int | None = None) -> BucketStats:
    sample = _window(history, window)
    counts = {"1": 0, "2": 0, "3": 0}
    total = 0
    for n in sample:
        c = column_of(n)
        if c is not None:
            counts[str(c)] += 1
            total += 1
    return BucketStats(counts=counts, total=total)


def hottest_numbers(history: list[int], window: int | None = None, top_n: int = 5) -> list[tuple[int, int]]:
    """Most frequent numbers within the window, as (number, count), ties broken by number asc.

    Numbers with zero occurrences in the window are excluded.
    """
    sample = _window(history, window)
    counts = frequency(sample)
    ranked = sorted(
        ((n, c) for n, c in counts.items() if c > 0),
        key=lambda item: (-item[1], item[0]),
    )
    return ranked[:top_n]


def spins_since_last_occurrence(history: list[int]) -> dict[int, int]:
    """For each number 0-36, how many spins have happened since it last appeared.

    A number that appeared as the very last spin has a value of 0. A number that has never
    appeared gets the full length of the history (i.e. it has been "absent" the whole time).
    """
    total = len(history)
    last_seen_index: dict[int, int] = {}
    for idx, n in enumerate(history):
        last_seen_index[n] = idx
    result: dict[int, int] = {}
    for n in VALID_NUMBERS:
        if n in last_seen_index:
            result[n] = (total - 1) - last_seen_index[n]
        else:
            result[n] = total
    return result


def coldest_numbers(history: list[int], top_n: int = 5) -> list[tuple[int, int]]:
    """Numbers absent the longest, as (number, spins_since_last_occurrence), most absent first."""
    since = spins_since_last_occurrence(history)
    ranked = sorted(since.items(), key=lambda item: (-item[1], item[0]))
    return ranked[:top_n]


def largest_historical_gaps(history: list[int]) -> dict[int, int | None]:
    """For each number, the largest gap (in spins) ever recorded between two consecutive
    appearances of it — different from `spins_since_last_occurrence`, which is only the gap
    *currently in progress*. Numbers with fewer than 2 appearances have no defined gap (None)."""
    positions: dict[int, list[int]] = {n: [] for n in VALID_NUMBERS}
    for idx, n in enumerate(history):
        positions[n].append(idx)
    result: dict[int, int | None] = {}
    for n in VALID_NUMBERS:
        idxs = positions[n]
        if len(idxs) < 2:
            result[n] = None
            continue
        result[n] = max(idxs[i] - idxs[i - 1] - 1 for i in range(1, len(idxs)))
    return result


# -- esperado vs. observado (roleta europeia: 1/37 por número) ------------------------------------
# Puramente descritivo: um desvio, por si só, não prova roda defeituosa nem prevê o próximo giro.

THEORETICAL_FREQUENCY_PCT = round(100.0 / 37, 4)


def expected_vs_observed(history: list[int]) -> dict[int, dict]:
    """Para cada número: contagem observada, percentual observado, percentual teórico (1/37) e o
    desvio entre os dois (em pontos percentuais). `history` pode ser uma janela qualquer (sessão,
    últimos N giros, lifetime) — a função não sabe nem precisa saber a origem dos dados."""
    counts = frequency(history)
    total = len(history)
    result = {}
    for n in VALID_NUMBERS:
        observed_pct = round(100.0 * counts[n] / total, 2) if total else 0.0
        result[n] = {
            "observed_count": counts[n],
            "observed_pct": observed_pct,
            "expected_pct": THEORETICAL_FREQUENCY_PCT,
            "deviation_pct": round(observed_pct - THEORETICAL_FREQUENCY_PCT, 2),
        }
    return result


# Valores tabelados da distribuição qui-quadrado para 36 graus de liberdade (37 números - 1) —
# padrão de tabelas estatísticas, evita puxar scipy só por dois números fixos.
_CHI_SQUARE_CRITICAL_DF36_P05 = 50.998
_CHI_SQUARE_CRITICAL_DF36_P01 = 58.619
_CHI_SQUARE_MIN_SAMPLE = 185  # regra prática: >=5 ocorrências esperadas por número (5 * 37)


def chi_square_verdict(history: list[int]) -> dict:
    """Teste de aderência qui-quadrado simples: a distribuição observada está dentro do que se
    espera de uma roleta honesta (H0: todos os números igualmente prováveis)? Deliberadamente
    conservador na linguagem do veredito — um desvio estatístico é um convite a acompanhar, nunca
    uma acusação de "roda defeituosa" a partir de uma amostra isolada."""
    n = len(history)
    if n < _CHI_SQUARE_MIN_SAMPLE:
        return {
            "applicable": False,
            "sample_size": n,
            "reason": f"amostra pequena ({n} giros; mínimo recomendado {_CHI_SQUARE_MIN_SAMPLE})",
        }
    counts = frequency(history)
    expected = n / 37
    chi2 = sum((counts[k] - expected) ** 2 / expected for k in VALID_NUMBERS)
    if chi2 > _CHI_SQUARE_CRITICAL_DF36_P01:
        verdict = "Distribuição apresenta desvio estatístico que merece acompanhamento (p<0,01)"
    elif chi2 > _CHI_SQUARE_CRITICAL_DF36_P05:
        verdict = "Distribuição apresenta desvio estatístico que merece acompanhamento (p<0,05)"
    else:
        verdict = "Distribuição dentro da faixa histórica esperada"
    return {"applicable": True, "sample_size": n, "chi_square": round(chi2, 2), "verdict": verdict}


# -- sequências (streaks) --------------------------------------------------------------------

STREAK_CATEGORIES: dict[str, Callable[[int], bool]] = {
    "red": lambda n: color_of(n) == COLOR_RED,
    "black": lambda n: color_of(n) == COLOR_BLACK,
    "even": lambda n: parity_of(n) == PARITY_EVEN,
    "odd": lambda n: parity_of(n) == PARITY_ODD,
    "low": lambda n: range_of(n) == RANGE_LOW,
    "high": lambda n: range_of(n) == RANGE_HIGH,
    "dozen1": lambda n: dozen_of(n) == 1,
    "dozen2": lambda n: dozen_of(n) == 2,
    "dozen3": lambda n: dozen_of(n) == 3,
}


def _streak_for_predicate(history: list[int], predicate) -> dict[str, int]:
    largest = current = 0
    for n in history:
        if predicate(n):
            current += 1
            largest = max(largest, current)
        else:
            current = 0
    return {"current": current, "largest": largest}


def compute_streaks(history: list[int]) -> dict[str, dict[str, int]]:
    """Maior sequência consecutiva (e a sequência em andamento no momento) para cada categoria
    binária de aposta — vermelho/preto, par/ímpar, menor/maior, cada uma das três dúzias. Zero
    (ou qualquer giro fora da categoria) interrompe a sequência, como esperado numa mesa real."""
    return {name: _streak_for_predicate(history, predicate) for name, predicate in STREAK_CATEGORIES.items()}
