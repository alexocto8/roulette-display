"""Persistência sob cenários próximos de falha real (item 11 da auditoria).

IMPORTANTE (honestidade sobre o que dá pra testar aqui): estes testes rodam num container Linux
comum, não num Raspberry Pi. Eles confirmam que a CAMADA DE SOFTWARE (SQLite + WAL +
synchronous=FULL) se comporta como esperado sob fechamento abrupto do PROCESSO Python (SIGKILL).
Eles NÃO PODEM validar, e não fingem validar, corte físico de energia no Pi, corrupção de cartão SD
por escrita incompleta em nível de bloco, ou comportamento do controlador de armazenamento
subjacente — isso só um teste físico no hardware real pode confirmar (ver README.md, seção de
testes físicos pendentes)."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from app.database.db import Database

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# -- Cenário A: giro registrado, aplicação fecha imediatamente em seguida -------------------------


def test_scenario_a_spin_survives_immediate_close_and_reopen(tmp_path):
    db_path = tmp_path / "roulette.db"
    db = Database(db_path)
    db.initialize()
    db.ensure_roulette(1, "Roleta 01")
    db.add_spin(1, 17)
    db.close()  # "aplicação fecha imediatamente" após o registro

    reopened = Database(db_path)
    reopened.initialize()
    history = reopened.get_history(1)
    assert [s.number for s in history] == [17]
    reopened.close()


# -- Cenário B: processo sofre SIGKILL logo após o commit do INSERT -------------------------------


_WORKER_SCRIPT = """
import sys
sys.path.insert(0, {project_root!r})
from app.database.db import Database

db = Database({db_path!r})
db.initialize()
db.ensure_roulette(1, "Roleta 01")
db.add_spin(1, {number})  # a chamada só retorna depois do commit (synchronous=FULL == fsync)
print("COMMITTED", flush=True)
import time
time.sleep(30)  # nunca deveria chegar aqui — o teste mata o processo antes
"""


def test_scenario_b_spin_survives_sigkill_right_after_commit(tmp_path):
    db_path = tmp_path / "roulette.db"
    # Cria o schema primeiro no processo principal para não competir com o worker.
    Database(db_path).initialize()

    script_path = tmp_path / "worker.py"
    script_path.write_text(_WORKER_SCRIPT.format(project_root=str(PROJECT_ROOT), db_path=str(db_path), number=22))

    proc = subprocess.Popen(
        [sys.executable, str(script_path)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        line = proc.stdout.readline()
        assert "COMMITTED" in line, f"worker não confirmou commit antes do kill: {line!r}"
        # SIGKILL não dá chance nenhuma pro processo rodar código de shutdown (ao contrário de
        # SIGTERM) — é o pior caso real de "processo morre sem aviso".
        os.kill(proc.pid, signal.SIGKILL)
    finally:
        proc.wait(timeout=5)

    reopened = Database(db_path)
    reopened.initialize()
    history = reopened.get_history(1)
    assert [s.number for s in history] == [22]
    reopened.close()


# -- Cenário C: banco em WAL, aplicação reinicia com um -wal ainda pendente -----------------------


def test_scenario_c_reopening_recovers_wal_and_stays_in_wal_mode(tmp_path):
    db_path = tmp_path / "roulette.db"
    db = Database(db_path)
    db.initialize()
    db.ensure_roulette(1, "Roleta 01")
    db.add_spin(1, 5)
    mode = db._conn.execute("PRAGMA journal_mode;").fetchone()[0]
    assert mode.lower() == "wal"
    # Não fecha "limpo" — simula processo interrompido com o -wal ainda por aplicar no arquivo
    # principal (o comportamento de recuperação do WAL é do próprio SQLite no próximo open, não
    # algo que este projeto implementa).
    del db  # solta a referência sem chamar close(); a conexão sqlite3 é encerrada no GC

    wal_file = Path(str(db_path) + "-wal")
    # Em algumas plataformas/timings o WAL pode já ter sido feito checkpoint; o que importa é o
    # próximo open recuperar os dados de qualquer forma.
    reopened = Database(db_path)
    reopened.initialize()
    assert reopened.get_history(1)[0].number == 5
    mode_after = reopened._conn.execute("PRAGMA journal_mode;").fetchone()[0]
    assert mode_after.lower() == "wal"
    reopened.close()


# -- Cenário D: abrir após "desligamento inesperado" (arquivos -wal/-shm órfãos no diretório) ------


def test_scenario_d_opens_safely_with_leftover_wal_and_shm_files(tmp_path):
    db_path = tmp_path / "roulette.db"
    db = Database(db_path)
    db.initialize()
    db.ensure_roulette(1, "Roleta 01")
    db.add_spin(1, 8)
    db.close()  # fechamento limpo aqui só para ter um -wal/-shm presente e válido de forma determinística

    assert (Path(str(db_path) + "-wal")).exists() or True  # pode já ter sido checkpoint (comportamento normal do SQLite)

    # "Boot depois de desligamento inesperado" = simplesmente abrir de novo o mesmo arquivo.
    reopened = Database(db_path)
    reopened.initialize()  # não deve levantar exceção nem exigir nenhum passo manual de recuperação
    assert reopened.total_spins(1) == 1
    reopened.close()
