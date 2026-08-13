"""SQLite persistence layer.

Design choices driven by "must survive a power loss on a Raspberry Pi 3 running 24/7":

- WAL journal mode: readers never block the writer, and a WAL file left behind by a power cut is
  safely replayed/truncated by SQLite on the next open instead of corrupting the main db file.
- synchronous=FULL: every commit is fsync'd before we consider a spin "saved". Write volume here
  is one row roughly every 30-60 seconds (a human dealing a wheel), so the fsync cost is
  irrelevant to performance and buys real durability.
- Undo/correction is a soft delete (deleted=1), never a hard DELETE: the raw audit trail survives
  even after an operator correction, which matters for a casino floor system. The one deliberate
  exception is data retention (`purge_older_than`): spins older than `config.data_retention_days`
  are archived to CSV first, then hard-deleted, so a 24/7 table doesn't grow the database forever
  (see app/services/retention_service.py and README "Limitação conhecida: histórico muito longo").
- A single long-lived connection guarded by a threading.Lock. The UI is single-threaded today,
  but the lock costs nothing and saves a rewrite when a future REST API adds a second thread.
"""
from __future__ import annotations

import csv
import json
import logging
import re
import shutil
import socket
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path

from app.audit.integrity import GENESIS_HASH, compute_event_hash, verify_chain
from app.models.roulette_data import color_of, is_valid_number
from app.models.spin import Spin

logger = logging.getLogger("roulette.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS roulettes (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS spins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    roulette_id INTEGER NOT NULL,
    number INTEGER NOT NULL CHECK (number >= 0 AND number <= 36),
    color TEXT NOT NULL CHECK (color IN ('red', 'black', 'green')),
    timestamp TEXT NOT NULL,
    created_at TEXT NOT NULL,
    deleted INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (roulette_id) REFERENCES roulettes (id)
);

CREATE INDEX IF NOT EXISTS idx_spins_roulette_id ON spins (roulette_id, id);

CREATE TABLE IF NOT EXISTS spin_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    spin_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    reason TEXT,
    at TEXT NOT NULL,
    original_number INTEGER,
    operator TEXT
);

-- Identidade da instalação: linha única (id sempre 1), criada uma vez no primeiro boot.
-- table_id é permanente por design (nunca é reescrito por update_identity) — venue_name/
-- table_name/table_code/table_location/device_name podem ser editados livremente pelo operador
-- sem que isso afete o relacionamento histórico com sessões/relatórios já gerados (essas guardam
-- seu próprio snapshot, ver tabela `sessions`).
CREATE TABLE IF NOT EXISTS installation_identity (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    table_id TEXT NOT NULL,
    venue_name TEXT NOT NULL DEFAULT '',
    table_name TEXT NOT NULL DEFAULT 'Mesa 01',
    table_code TEXT NOT NULL DEFAULT '',
    table_location TEXT NOT NULL DEFAULT '',
    device_name TEXT NOT NULL DEFAULT '',
    venue_logo_path TEXT,
    report_title TEXT NOT NULL DEFAULT '',
    report_subtitle TEXT NOT NULL DEFAULT '',
    venue_address TEXT NOT NULL DEFAULT '',
    venue_phone TEXT NOT NULL DEFAULT '',
    venue_email TEXT NOT NULL DEFAULT '',
    venue_website TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Uma "sessão" é o período que um relatório cobre — deliberadamente distinto do gesto de teclado
-- "-97 ENTER" (que só reinicia o placar em tela, ver app/ui/display.py). Uma sessão só é fechada
-- por uma ação administrativa explícita ("Encerrar sessão", com PIN), que é o que dispara a
-- geração de relatório. Cada linha guarda um SNAPSHOT da identidade no momento em que a sessão
-- foi aberta — renomear a mesa depois não deve alterar relatórios já existentes.
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_code TEXT NOT NULL UNIQUE,
    roulette_id INTEGER NOT NULL,
    table_id TEXT NOT NULL,
    table_name TEXT NOT NULL,
    table_code TEXT NOT NULL,
    venue_name TEXT NOT NULL,
    table_location TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    FOREIGN KEY (roulette_id) REFERENCES roulettes (id)
);
CREATE INDEX IF NOT EXISTS idx_sessions_roulette_started ON sessions (roulette_id, started_at);

-- Trilha de auditoria geral (superset do spin_audit acima, que continua existindo e sendo usado
-- pela tela rápida "Ver auditoria (correções)" já existente — não removido, para não reescrever um
-- caminho já testado). audit_log cobre o catálogo completo de eventos (sistema, sessão, admin,
-- licença, relatório, etc.), com uma cadeia de hash simples para detectar edição posterior.
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    event_type TEXT NOT NULL,
    session_id INTEGER,
    spin_id INTEGER,
    table_id TEXT,
    actor_type TEXT NOT NULL,
    actor_id TEXT,
    source TEXT,
    old_value TEXT,
    new_value TEXT,
    reason TEXT,
    metadata_json TEXT,
    previous_hash TEXT NOT NULL,
    event_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_log_session ON audit_log (session_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_created ON audit_log (created_at);
CREATE INDEX IF NOT EXISTS idx_audit_log_event_type ON audit_log (event_type);

CREATE INDEX IF NOT EXISTS idx_spins_number ON spins (roulette_id, number);
CREATE INDEX IF NOT EXISTS idx_spins_created_at ON spins (created_at);

-- Fila persistente de envio de relatório por e-mail (item 47). Sobrevive a reinícios do
-- processo e a falta de internet — "ENFILEIRAR" é sempre síncrono e rápido (um INSERT), o envio
-- de verdade acontece depois, fora do processo principal (ver scripts/send_pending_reports.py).
CREATE TABLE IF NOT EXISTS report_delivery (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_dir TEXT NOT NULL,
    session_id INTEGER,
    status TEXT NOT NULL DEFAULT 'PENDING',
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    last_attempt_at TEXT,
    sent_at TEXT,
    last_error TEXT
);
CREATE INDEX IF NOT EXISTS idx_report_delivery_status ON report_delivery (status);
"""

# Colunas adicionadas depois da versão inicial do schema — `CREATE TABLE IF NOT EXISTS` não altera
# uma tabela já existente, então um `spin_audit` criado antes dessas colunas existirem precisa de
# uma migração explícita e idempotente (senão bancos já em produção nunca ganham as colunas novas).
_MIGRATIONS: list[tuple[str, str]] = [
    ("spin_audit", "ALTER TABLE spin_audit ADD COLUMN original_number INTEGER;"),
    ("spin_audit", "ALTER TABLE spin_audit ADD COLUMN operator TEXT;"),
    # Nullable de propósito: giros gravados antes desta versão não têm uma sessão formal — o
    # backfill (_backfill_legacy_session, chamado depois das migrações de coluna) cria uma sessão
    # "legada" e associa esses giros a ela, em vez de deixá-los soltos ou inventar um limite de
    # sessão que não existiu de verdade.
    ("spins", "ALTER TABLE spins ADD COLUMN session_id INTEGER;"),
]


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Database:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._configure_pragmas()

    def _configure_pragmas(self) -> None:
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=FULL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")

    def initialize(self) -> None:
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._run_migrations()
            self._bootstrap_identity()
            self._backfill_legacy_sessions()
            self._conn.commit()

    def _bootstrap_identity(self) -> None:
        """Cria a linha única de `installation_identity` na primeira vez que o app abre este
        banco. Idempotente: se a linha já existe (id=1), não faz nada — em particular, nunca
        gera um `table_id` novo para uma instalação que já tinha um."""
        row = self._conn.execute("SELECT id FROM installation_identity WHERE id = 1;").fetchone()
        if row is not None:
            return
        try:
            hostname = socket.gethostname()
        except OSError:
            hostname = "roulette-display"
        now = _now_iso()
        self._conn.execute(
            "INSERT INTO installation_identity "
            "(id, table_id, venue_name, table_name, table_code, table_location, device_name, "
            " report_title, report_subtitle, venue_address, venue_phone, venue_email, "
            " venue_website, created_at, updated_at) "
            "VALUES (1, ?, '', 'Mesa 01', '', '', ?, '', '', '', '', '', '', ?, ?)",
            (str(uuid.uuid4()), hostname, now, now),
        )

    def _backfill_legacy_sessions(self) -> None:
        """Giros gravados antes desta versão não têm `session_id`. Não dá pra reconstruir os
        limites de sessão reais de dados antigos (isso exigiria adivinhar), então a migração cria
        UMA sessão "legada" por roleta com giros órfãos, usando o snapshot de identidade atual, e
        associa todos esses giros a ela — preserva os dados para analytics/relatórios em vez de
        deixá-los invisíveis, sendo honesto (no nome da sessão) sobre a natureza aproximada desse
        agrupamento retroativo."""
        orphan_roulettes = [
            row["roulette_id"] for row in self._conn.execute(
                "SELECT DISTINCT roulette_id FROM spins WHERE session_id IS NULL;"
            )
        ]
        if not orphan_roulettes:
            return
        identity = self._conn.execute("SELECT * FROM installation_identity WHERE id = 1;").fetchone()
        for roulette_id in orphan_roulettes:
            first = self._conn.execute(
                "SELECT MIN(created_at) AS first_at FROM spins WHERE roulette_id = ? AND session_id IS NULL;",
                (roulette_id,),
            ).fetchone()
            started_at = first["first_at"] or _now_iso()
            code = f"LEGACY-{roulette_id}-{uuid.uuid4().hex[:8]}"
            cur = self._conn.execute(
                "INSERT INTO sessions (session_code, roulette_id, table_id, table_name, table_code, "
                " venue_name, table_location, started_at, ended_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    code, roulette_id, identity["table_id"],
                    f"{identity['table_name']} (dados anteriores à auditoria)",
                    identity["table_code"], identity["venue_name"], identity["table_location"],
                    started_at, _now_iso(),
                ),
            )
            session_id = cur.lastrowid
            self._conn.execute(
                "UPDATE spins SET session_id = ? WHERE roulette_id = ? AND session_id IS NULL;",
                (session_id, roulette_id),
            )

    def _run_migrations(self) -> None:
        for table, statement in _MIGRATIONS:
            column = statement.split("ADD COLUMN")[1].strip().split(" ")[0]
            existing = {row["name"] for row in self._conn.execute(f"PRAGMA table_info({table});")}
            if column not in existing:
                self._conn.execute(statement)

    def ensure_roulette(self, roulette_id: int, name: str) -> None:
        with self._lock:
            row = self._conn.execute("SELECT id, name FROM roulettes WHERE id = ?", (roulette_id,)).fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO roulettes (id, name, created_at) VALUES (?, ?, ?)",
                    (roulette_id, name, _now_iso()),
                )
                self._conn.commit()
            elif row["name"] != name:
                self._conn.execute("UPDATE roulettes SET name = ? WHERE id = ?", (name, roulette_id))
                self._conn.commit()

    # -- Spins ----------------------------------------------------------------

    def add_spin(self, roulette_id: int, number: int, session_id: int | None = None) -> Spin:
        if not is_valid_number(number):
            raise ValueError(f"invalid roulette number: {number!r}")
        color = color_of(number)
        now = _now_iso()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO spins (roulette_id, number, color, timestamp, created_at, deleted, session_id) "
                "VALUES (?, ?, ?, ?, ?, 0, ?)",
                (roulette_id, number, color, now, now, session_id),
            )
            self._conn.commit()
            spin_id = cur.lastrowid
        return Spin(id=spin_id, roulette_id=roulette_id, number=number, color=color,
                     timestamp=now, created_at=now, deleted=False)

    def get_history(self, roulette_id: int, limit: int | None = None) -> list[Spin]:
        """Non-deleted spins, oldest first. Pass `limit` to get only the most recent N."""
        with self._lock:
            if limit:
                rows = self._conn.execute(
                    "SELECT * FROM spins WHERE roulette_id = ? AND deleted = 0 "
                    "ORDER BY id DESC LIMIT ?",
                    (roulette_id, limit),
                ).fetchall()
                rows = list(reversed(rows))
            else:
                rows = self._conn.execute(
                    "SELECT * FROM spins WHERE roulette_id = ? AND deleted = 0 ORDER BY id ASC",
                    (roulette_id,),
                ).fetchall()
        return [self._row_to_spin(r) for r in rows]

    def get_last_spin(self, roulette_id: int) -> Spin | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM spins WHERE roulette_id = ? AND deleted = 0 ORDER BY id DESC LIMIT 1",
                (roulette_id,),
            ).fetchone()
        return self._row_to_spin(row) if row else None

    def total_spins(self, roulette_id: int) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS c FROM spins WHERE roulette_id = ? AND deleted = 0",
                (roulette_id,),
            ).fetchone()
        return row["c"]

    def get_audit_log(self, roulette_id: int, limit: int = 20) -> list[sqlite3.Row]:
        """Most recent correction/clear events for this roulette, newest first — the actual
        evidence behind "undo/clear removed this from the board", for the admin auditoria screen.

        `original_timestamp` comes from the `spins` row itself (joined by `spin_id`): since undo
        is a soft delete, that row — and its original timestamp — is still there, untouched, even
        after the correction. Nothing about the original registration is lost."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT a.spin_id, a.action, a.reason, a.at, a.original_number, a.operator, "
                "s.timestamp AS original_timestamp "
                "FROM spin_audit a JOIN spins s ON s.id = a.spin_id "
                "WHERE s.roulette_id = ? ORDER BY a.id DESC LIMIT ?",
                (roulette_id, limit),
            ).fetchall()
        return rows

    def undo_last(self, roulette_id: int, operator: str = "teclado") -> Spin | None:
        """Soft-delete the most recent non-deleted spin. Returns it (already marked deleted), or
        None if there was nothing to undo. `operator` records how the action was triggered
        ("teclado" = direct shortcut on the main screen, no PIN; "admin" = PIN-gated menu) — there
        is no per-person operator identity today, so this is the most honest thing we can log."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM spins WHERE roulette_id = ? AND deleted = 0 ORDER BY id DESC LIMIT 1",
                (roulette_id,),
            ).fetchone()
            if row is None:
                return None
            self._conn.execute("UPDATE spins SET deleted = 1 WHERE id = ?", (row["id"],))
            self._conn.execute(
                "INSERT INTO spin_audit (spin_id, action, reason, at, original_number, operator) "
                "VALUES (?, 'undo', 'operator_correction', ?, ?, ?)",
                (row["id"], _now_iso(), row["number"], operator),
            )
            self._conn.commit()
        spin = self._row_to_spin(row)
        return Spin(**{**spin.__dict__, "deleted": True})

    def clear_session(self, roulette_id: int, operator: str = "teclado") -> int:
        """Soft-delete every current spin for this roulette (keeps the raw audit trail).
        Returns the number of rows affected."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT id, number FROM spins WHERE roulette_id = ? AND deleted = 0", (roulette_id,)
            )
            rows = cur.fetchall()
            if not rows:
                return 0
            ids = [r["id"] for r in rows]
            self._conn.executemany(
                "UPDATE spins SET deleted = 1 WHERE id = ?", [(i,) for i in ids]
            )
            self._conn.executemany(
                "INSERT INTO spin_audit (spin_id, action, reason, at, original_number, operator) "
                "VALUES (?, 'undo', 'session_clear', ?, ?, ?)",
                [(r["id"], _now_iso(), r["number"], operator) for r in rows],
            )
            self._conn.commit()
        return len(ids)

    # -- Export / backup --------------------------------------------------------

    def export_csv(self, roulette_id: int, dest_path: str | Path) -> Path:
        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        spins = self.get_history(roulette_id)
        roulette_name = self.get_roulette_name(roulette_id)
        with open(dest, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["data", "hora", "numero", "cor", "mesa"])
            for s in spins:
                dt = datetime.fromisoformat(s.timestamp)
                writer.writerow([dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M:%S"), s.number, s.color, roulette_name])
        return dest

    def get_roulette_name(self, roulette_id: int) -> str:
        with self._lock:
            row = self._conn.execute("SELECT name FROM roulettes WHERE id = ?", (roulette_id,)).fetchone()
        return row["name"] if row else ""

    # -- Retenção de dados (30 dias por padrão) ----------------------------------

    def count_spins_older_than(self, roulette_id: int, before_iso: str) -> int:
        """Quantos giros (ativos OU já soft-deletados) têm `created_at` antes de `before_iso` —
        usado pelo RetentionService pra decidir se vale a pena arquivar/purgar (não roda o
        arquivamento à toa quando não há nada além do corte)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS c FROM spins WHERE roulette_id = ? AND created_at < ?",
                (roulette_id, before_iso),
            ).fetchone()
        return row["c"]

    def export_spins_older_than(self, roulette_id: int, before_iso: str, dest_path: str | Path) -> Path:
        """Arquiva (exporta) os giros mais antigos que `before_iso` antes de serem purgados de
        verdade — inclui os já soft-deletados (`status`) porque essa é a última chance de guardar
        esse dado em algum lugar depois que `purge_spins_older_than` remover a linha do banco."""
        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        roulette_name = self.get_roulette_name(roulette_id)
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM spins WHERE roulette_id = ? AND created_at < ? ORDER BY id ASC",
                (roulette_id, before_iso),
            ).fetchall()
        with open(dest, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["data", "hora", "numero", "cor", "mesa", "status"])
            for row in rows:
                dt = datetime.fromisoformat(row["timestamp"])
                status = "removido_pelo_operador" if row["deleted"] else "ativo"
                writer.writerow([dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M:%S"), row["number"], row["color"],
                                  roulette_name, status])
        return dest

    def purge_spins_older_than(self, roulette_id: int, before_iso: str) -> int:
        """Remove de verdade (hard delete) giros com `created_at` antes de `before_iso` -- só deve
        ser chamado depois de `export_spins_older_than` ter arquivado essas mesmas linhas em
        algum lugar (ver RetentionService.enforce_retention). Não mexe em `spin_audit`/`audit_log`:
        a trilha de auditoria (muito menor, e com cadeia de hash no caso de `audit_log`) continua
        íntegra mesmo depois que o giro em si já foi purgado."""
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM spins WHERE roulette_id = ? AND created_at < ?", (roulette_id, before_iso)
            )
            self._conn.commit()
        return cursor.rowcount

    def backup(self, dest_path: str | Path) -> Path:
        """Online-safe backup using SQLite's own backup API (does not require pausing writes)."""
        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            dest_conn = sqlite3.connect(str(dest))
            try:
                self._conn.backup(dest_conn)
            finally:
                dest_conn.close()
        return dest

    @staticmethod
    def restore(backup_path: str | Path, target_path: str | Path) -> None:
        """Restore a backup file over the live database. Caller must ensure the app is not
        holding an open Database instance on target_path when calling this."""
        backup_path = Path(backup_path)
        target_path = Path(target_path)
        if not backup_path.exists():
            raise FileNotFoundError(backup_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(backup_path, target_path)
        for suffix in ("-wal", "-shm"):
            stale = Path(str(target_path) + suffix)
            if stale.exists():
                stale.unlink()

    def is_healthy(self) -> bool:
        """Cheap liveness check (`SELECT 1`) — for the UI's periodic health indicator, not called
        every frame. Returns False instead of raising on any sqlite3 error (connection closed,
        disk I/O error, corruption detected by SQLite itself, etc.) so callers never need a
        try/except around this specifically."""
        try:
            with self._lock:
                self._conn.execute("SELECT 1;").fetchone()
            return True
        except sqlite3.Error:
            logger.warning("Health check falhou", exc_info=True)
            return False

    # -- Identidade da instalação ------------------------------------------------

    _IDENTITY_EDITABLE_FIELDS = frozenset({
        "venue_name", "table_name", "table_code", "table_location", "device_name",
        "venue_logo_path", "report_title", "report_subtitle", "venue_address",
        "venue_phone", "venue_email", "venue_website",
    })

    def get_identity(self) -> sqlite3.Row:
        with self._lock:
            row = self._conn.execute("SELECT * FROM installation_identity WHERE id = 1;").fetchone()
        if row is None:
            raise RuntimeError("installation_identity ausente — Database.initialize() não foi chamado?")
        return row

    def update_identity(self, **fields) -> sqlite3.Row:
        """`table_id` deliberadamente não está em `_IDENTITY_EDITABLE_FIELDS` — não é só uma
        convenção de UI, a própria API do banco recusa alterá-lo, então nenhum chamador (presente
        ou futuro) consegue mudar o identificador permanente da mesa por acidente."""
        unknown = set(fields) - self._IDENTITY_EDITABLE_FIELDS
        if unknown:
            raise ValueError(f"campos não editáveis (ou inexistentes) em installation_identity: {sorted(unknown)}")
        if not fields:
            return self.get_identity()
        assignments = ", ".join(f"{k} = ?" for k in fields)
        with self._lock:
            self._conn.execute(
                f"UPDATE installation_identity SET {assignments}, updated_at = ? WHERE id = 1;",
                [*fields.values(), _now_iso()],
            )
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM installation_identity WHERE id = 1;").fetchone()
        return row

    # -- Sessões --------------------------------------------------------------

    @staticmethod
    def _session_code_prefix(identity: sqlite3.Row) -> str:
        code = re.sub(r"[^A-Za-z0-9_-]", "", (identity["table_code"] or "").strip())
        if code:
            return code.upper()
        # Sem prefixo "MESA" extra aqui: o nome da mesa já vira o prefixo (ex.: table_name="Mesa
        # 01" -> "MESA01", igual ao exemplo do contrato). Prependendo "MESA" de novo resultaria
        # em "MESAMESA01" sempre que o nome já começasse com "Mesa".
        name = re.sub(r"[^A-Za-z0-9]", "", (identity["table_name"] or "").strip())
        if name:
            return name.upper()[:20]
        return "SESSION"

    def get_open_session(self, roulette_id: int) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM sessions WHERE roulette_id = ? AND ended_at IS NULL ORDER BY id DESC LIMIT 1;",
                (roulette_id,),
            ).fetchone()

    def open_session(self, roulette_id: int, identity: sqlite3.Row) -> sqlite3.Row:
        """Abre uma sessão nova com um snapshot congelado da identidade atual — ver o comentário
        na definição da tabela `sessions` sobre por que isso nunca é atualizado retroativamente.
        `session_code` segue `{TABLE_CODE}-{YYYYMMDD}-{SEQ}` (ou um prefixo derivado do nome da
        mesa, ou "SESSION", quando não há código configurado)."""
        prefix = self._session_code_prefix(identity)
        date_part = datetime.now().strftime("%Y%m%d")
        now = _now_iso()
        with self._lock:
            like_pattern = f"{prefix}-{date_part}-%"
            existing = self._conn.execute(
                "SELECT COUNT(*) AS c FROM sessions WHERE session_code LIKE ?;", (like_pattern,)
            ).fetchone()["c"]
            session_code = f"{prefix}-{date_part}-{existing + 1:03d}"
            cur = self._conn.execute(
                "INSERT INTO sessions (session_code, roulette_id, table_id, table_name, table_code, "
                " venue_name, table_location, started_at, ended_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                (session_code, roulette_id, identity["table_id"], identity["table_name"],
                 identity["table_code"], identity["venue_name"], identity["table_location"], now),
            )
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM sessions WHERE id = ?;", (cur.lastrowid,)).fetchone()
        return row

    def close_session(self, session_id: int) -> sqlite3.Row | None:
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET ended_at = ? WHERE id = ? AND ended_at IS NULL;",
                (_now_iso(), session_id),
            )
            self._conn.commit()
            return self._conn.execute("SELECT * FROM sessions WHERE id = ?;", (session_id,)).fetchone()

    def get_session(self, session_id: int) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute("SELECT * FROM sessions WHERE id = ?;", (session_id,)).fetchone()

    def get_session_by_code(self, session_code: str) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM sessions WHERE session_code = ?;", (session_code,)
            ).fetchone()

    def list_sessions(self, roulette_id: int, limit: int = 50) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM sessions WHERE roulette_id = ? ORDER BY id DESC LIMIT ?;",
                (roulette_id, limit),
            ).fetchall()

    def get_spins_for_session(self, session_id: int, include_deleted: bool = False) -> list[Spin]:
        query = "SELECT * FROM spins WHERE session_id = ?"
        if not include_deleted:
            query += " AND deleted = 0"
        query += " ORDER BY id ASC;"
        with self._lock:
            rows = self._conn.execute(query, (session_id,)).fetchall()
        return [self._row_to_spin(r) for r in rows]

    def get_report_spins_for_session(self, session_id: int) -> list[Spin]:
        """What a report/analytics for this session should count as "real" spins: everything
        still active, PLUS anything soft-deleted by a "-97 ENTER" session clear (the operator
        resetting the on-screen board mid-shift — the number genuinely happened), MINUS anything
        soft-deleted by an operator correction ("-" / DEL DEL undo — a mistaken digit that should
        never have counted). A spin only ever gets one `spin_audit` row (undo/clear only ever
        touches `deleted = 0` rows), so this distinction is unambiguous."""
        query = (
            "SELECT s.* FROM spins s WHERE s.session_id = ? AND ("
            "  s.deleted = 0"
            "  OR NOT EXISTS ("
            "    SELECT 1 FROM spin_audit a WHERE a.spin_id = s.id AND a.reason = 'operator_correction'"
            "  )"
            ") ORDER BY s.id ASC;"
        )
        with self._lock:
            rows = self._conn.execute(query, (session_id,)).fetchall()
        return [self._row_to_spin(r) for r in rows]

    def get_report_spins_in_range(
        self, roulette_id: int, since: str | None = None, until: str | None = None,
    ) -> list[Spin]:
        """Mesma regra do `get_report_spins_for_session` (soft-delete por "-97" conta, soft-
        delete por correção não conta), mas por janela de tempo (`created_at`) em vez de sessão
        — usado pelos períodos de análise baseados em data (HOJE, 7 DIAS, etc., ver
        app/analytics/periods.py). `since`/`until` são strings ISO-8601 (ou None = sem limite
        naquele lado)."""
        clauses = ["s.roulette_id = ?"]
        params: list = [roulette_id]
        if since is not None:
            clauses.append("s.created_at >= ?")
            params.append(since)
        if until is not None:
            clauses.append("s.created_at <= ?")
            params.append(until)
        query = (
            "SELECT s.* FROM spins s WHERE " + " AND ".join(clauses) + " AND ("
            "  s.deleted = 0"
            "  OR NOT EXISTS ("
            "    SELECT 1 FROM spin_audit a WHERE a.spin_id = s.id AND a.reason = 'operator_correction'"
            "  )"
            ") ORDER BY s.id ASC;"
        )
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_spin(r) for r in rows]

    # -- Auditoria (audit_log, com cadeia de hash) -----------------------------

    def append_audit_event(
        self, event_type: str, *, session_id: int | None = None, spin_id: int | None = None,
        table_id: str | None = None, actor_type: str = "system", actor_id: str | None = None,
        source: str | None = None, old_value: str | None = None, new_value: str | None = None,
        reason: str | None = None, metadata: dict | None = None,
    ) -> sqlite3.Row:
        """Grava um evento na trilha de auditoria geral, encadeado por hash (ver app/audit/
        integrity.py). Fica no mesmo commit/lock que o resto do banco — é uma escrita só (um
        INSERT indexado, microssegundos), então mantê-la síncrona aqui é o mesmo raciocínio já
        aplicado ao `spin_audit` existente: perder um evento de auditoria por causa de uma fila
        assíncrona quebrada seria pior para confiabilidade do que o custo desprezível de escrever
        direto. O que de fato precisa ficar fora do caminho crítico é geração de PDF/CSV/JSON,
        envio de e-mail e impressão — não uma única linha de log."""
        event_id = str(uuid.uuid4())
        created_at = _now_iso()
        metadata_json = json.dumps(metadata, sort_keys=True, ensure_ascii=False) if metadata else None
        event = {
            "event_id": event_id, "created_at": created_at, "event_type": event_type,
            "session_id": session_id, "spin_id": spin_id, "table_id": table_id,
            "actor_type": actor_type, "actor_id": actor_id, "source": source,
            "old_value": old_value, "new_value": new_value, "reason": reason,
            "metadata_json": metadata_json,
        }
        with self._lock:
            prev = self._conn.execute("SELECT event_hash FROM audit_log ORDER BY id DESC LIMIT 1;").fetchone()
            previous_hash = prev["event_hash"] if prev else GENESIS_HASH
            event_hash = compute_event_hash(previous_hash, event)
            cur = self._conn.execute(
                "INSERT INTO audit_log (event_id, created_at, event_type, session_id, spin_id, "
                " table_id, actor_type, actor_id, source, old_value, new_value, reason, "
                " metadata_json, previous_hash, event_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (event_id, created_at, event_type, session_id, spin_id, table_id, actor_type,
                 actor_id, source, old_value, new_value, reason, metadata_json, previous_hash, event_hash),
            )
            self._conn.commit()
            return self._conn.execute("SELECT * FROM audit_log WHERE id = ?;", (cur.lastrowid,)).fetchone()

    def get_audit_events(
        self, *, session_id: int | None = None, spin_id: int | None = None,
        event_type: str | None = None, table_id: str | None = None, actor_id: str | None = None,
        since: str | None = None, until: str | None = None, limit: int = 50, offset: int = 0,
    ) -> list[sqlite3.Row]:
        """Filtros combináveis (todos opcionais, aplicados com AND) + paginação — a tela de
        auditoria não pode carregar a tabela inteira de uma vez em instalações de longa duração."""
        clauses: list[str] = []
        params: list = []
        for column, value in (
            ("session_id", session_id), ("spin_id", spin_id), ("event_type", event_type),
            ("table_id", table_id), ("actor_id", actor_id),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        if since is not None:
            clauses.append("created_at >= ?")
            params.append(since)
        if until is not None:
            clauses.append("created_at <= ?")
            params.append(until)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params += [limit, offset]
        with self._lock:
            return self._conn.execute(
                f"SELECT * FROM audit_log {where} ORDER BY id DESC LIMIT ? OFFSET ?;", params
            ).fetchall()

    def count_audit_events(self, *, session_id: int | None = None, event_type: str | None = None) -> int:
        """Contagem direta (sem trazer as linhas) — usado pelas KPIs de correção do analytics
        (correction_count/undo_count), que só precisam do total, não do conteúdo dos eventos."""
        clauses: list[str] = []
        params: list = []
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)
        if event_type is not None:
            clauses.append("event_type = ?")
            params.append(event_type)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            return self._conn.execute(f"SELECT COUNT(*) AS c FROM audit_log {where};", params).fetchone()["c"]

    def verify_audit_integrity(self) -> tuple[bool, str | None]:
        """Recalcula a cadeia inteira de hash e compara. Retorna (True, None) se íntegra, ou
        (False, event_id) apontando o primeiro evento inconsistente. O(n) no número de eventos —
        aceitável para rodar sob demanda (tela de auditoria/relatório), não a cada frame."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT event_id, created_at, event_type, session_id, spin_id, table_id, "
                "actor_type, actor_id, source, old_value, new_value, reason, metadata_json, "
                "previous_hash, event_hash FROM audit_log ORDER BY id ASC;"
            ).fetchall()
        return verify_chain([dict(r) for r in rows])

    # -- Fila de entrega de relatório (e-mail) ---------------------------------

    def enqueue_delivery(self, report_dir: str, session_id: int | None = None) -> sqlite3.Row:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO report_delivery (report_dir, session_id, status, attempts, created_at) "
                "VALUES (?, ?, 'PENDING', 0, ?)",
                (report_dir, session_id, _now_iso()),
            )
            self._conn.commit()
            return self._conn.execute(
                "SELECT * FROM report_delivery WHERE id = ?;", (cur.lastrowid,)
            ).fetchone()

    def get_pending_deliveries(self, limit: int = 20) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM report_delivery WHERE status IN ('PENDING', 'FAILED') "
                "ORDER BY id ASC LIMIT ?;", (limit,),
            ).fetchall()

    def mark_delivery_sending(self, delivery_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE report_delivery SET status = 'SENDING', attempts = attempts + 1, "
                "last_attempt_at = ? WHERE id = ?;", (_now_iso(), delivery_id),
            )
            self._conn.commit()

    def mark_delivery_sent(self, delivery_id: int) -> None:
        with self._lock:
            now = _now_iso()
            self._conn.execute(
                "UPDATE report_delivery SET status = 'SENT', sent_at = ?, last_error = NULL "
                "WHERE id = ?;", (now, delivery_id),
            )
            self._conn.commit()

    def mark_delivery_failed(self, delivery_id: int, error: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE report_delivery SET status = 'FAILED', last_error = ? WHERE id = ?;",
                (error, delivery_id),
            )
            self._conn.commit()

    def list_deliveries(self, limit: int = 50) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM report_delivery ORDER BY id DESC LIMIT ?;", (limit,)
            ).fetchall()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @staticmethod
    def _row_to_spin(row: sqlite3.Row) -> Spin:
        return Spin(
            id=row["id"],
            roulette_id=row["roulette_id"],
            number=row["number"],
            color=row["color"],
            timestamp=row["timestamp"],
            created_at=row["created_at"],
            deleted=bool(row["deleted"]),
        )
