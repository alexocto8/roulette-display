"""Teste de stress/soak (item 12 da auditoria): banco + lógica de estatística sob volume alto de
giros — deliberadamente SEM abrir uma janela pygame ou renderizar um único frame. Renderização e
persistência são preocupações de performance completamente diferentes (uma é FPS na GPU/CPU de um
Pi 3, a outra é IOPS/fsync no cartão SD); misturar as duas no mesmo teste seria artificial e não
mediria nenhuma das duas coisas direito.

Dois níveis:
- `test_soak_quick_smoke`: roda sempre (parte da suíte normal, ~1s) — pega regressões óbvias de
  performance/correção rapidamente.
- `test_soak_100k_spins`: o teste pedido explicitamente (100k giros), mas é PESADO de propósito
  (usa o mesmo `synchronous=FULL` com fsync por commit que roda em produção) — não faz sentido
  rodar em todo `pytest` de todo commit. Opt-in via `ROULETTE_RUN_SOAK=1 pytest tests/test_soak.py`.
"""
from __future__ import annotations

import gc
import os
import random
import resource
import time

import pytest

from app.config import Config
from app.database.db import Database
from app.services.backup_service import BackupService
from app.services.spin_service import SpinService


def _rss_mb() -> float:
    # ru_maxrss é KB no Linux — pico de memória residente do processo até agora.
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def _run_soak(tmp_path, n_spins: int, backup_every: int) -> dict:
    config = Config(
        database_path=str(tmp_path / "roulette.db"),
        backups_dir=str(tmp_path / "backups"),
        backup_retention_count=5,
    )
    db = Database(tmp_path / "roulette.db")
    db.initialize()
    service = SpinService(db, config)
    backup_service = BackupService(db, config)

    rng = random.Random(42)
    t0 = time.time()
    insert_total = 0.0
    session_boundaries = 0

    for i in range(n_spins):
        number = rng.randint(0, 36)  # inclui zero com a mesma probabilidade dos demais
        t_ins = time.time()
        service.register_spin(number)
        insert_total += time.time() - t_ins

        if i % 997 == 0:  # de vez em quando desfaz o último giro (padrão real de uso)
            service.undo_last()
            service.register_spin(number)  # repõe, pra manter o volume alvo de giros ativos
        if i % 9973 == 0 and i > 0:  # de vez em quando limpa e começa uma "nova sessão"
            service.clear_session()
            session_boundaries += 1
        if backup_every and i % backup_every == 0 and i > 0:
            backup_service.create_backup()

    elapsed_inserts = time.time() - t0

    t_stats = time.time()
    state = service.get_display_state()
    stats_elapsed = time.time() - t_stats

    db_size_mb = (tmp_path / "roulette.db").stat().st_size / (1024 * 1024)
    db.close()

    return {
        "n_spins": n_spins,
        "elapsed_inserts_s": elapsed_inserts,
        "avg_insert_ms": (insert_total / n_spins) * 1000,
        "stats_elapsed_s": stats_elapsed,
        "db_size_mb": db_size_mb,
        "session_boundaries": session_boundaries,
        "final_total_spins": state.total_spins,
    }


def test_soak_quick_smoke(tmp_path):
    """Roda sempre — 2.000 giros é suficiente para pegar uma regressão óbvia de performance sem
    tornar a suíte normal lenta."""
    result = _run_soak(tmp_path, n_spins=2000, backup_every=500)
    assert result["final_total_spins"] > 0
    # Limite generoso (não é benchmark de precisão, é um alarme de regressão): get_display_state
    # não deveria nunca chegar perto de 1s com uma janela de histórico dessa ordem de grandeza.
    assert result["stats_elapsed_s"] < 1.0


@pytest.mark.skipif(
    not os.environ.get("ROULETTE_RUN_SOAK"),
    reason="Soak pesado (100k giros, fsync real por commit) — rode com ROULETTE_RUN_SOAK=1 explicitamente.",
)
def test_soak_100k_spins(tmp_path, capsys):
    gc.collect()
    mem_before = _rss_mb()

    result = _run_soak(tmp_path, n_spins=100_000, backup_every=20_000)

    gc.collect()
    mem_after = _rss_mb()

    with capsys.disabled():
        print("\n--- Soak 100k giros ---")
        print(f"Tempo total de inserts: {result['elapsed_inserts_s']:.1f}s "
              f"({result['avg_insert_ms']:.3f}ms/giro em média)")
        print(f"get_display_state() no final: {result['stats_elapsed_s']*1000:.1f}ms")
        print(f"Tamanho do roulette.db: {result['db_size_mb']:.2f}MB")
        print(f"Sessões limpas durante o teste: {result['session_boundaries']}")
        print(f"RSS do processo: {mem_before:.1f}MB -> {mem_after:.1f}MB "
              f"(Δ{mem_after - mem_before:+.1f}MB)")
        print("Nota: este ambiente não é um Raspberry Pi 3 real — os tempos absolutos de fsync "
              "por commit (SD card) e a RAM disponível (1GB) são diferentes. O que este teste "
              "prova é ausência de crescimento patológico (memory leak, degradação de tempo de "
              "consulta) com o volume de dados, não o desempenho absoluto do hardware final.")

    # Nada de crash/exceção já é o resultado principal do teste. Correção básica além disso:
    assert result["final_total_spins"] > 0
    assert result["stats_elapsed_s"] < 2.0  # get_display_state ainda rápido com ~100k linhas no banco
    # Ausência de vazamento de memória grosseiro (limite generoso — isto é um alarme, não um
    # orçamento de memória preciso; RSS de processo Python tem ruído normal do alocador).
    assert (mem_after - mem_before) < 300
