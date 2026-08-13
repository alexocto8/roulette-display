"""Business logic tying the database, the statistics engine and config together.

This is the seam the UI talks to. It is also the seam a future REST API / sync-to-central-server
job would sit behind — the display and any future frontend both just ask this service for a
`DisplayState` and never touch sqlite or the stats engine directly.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.audit.audit_service import AuditService
from app.config import Config
from app.database.db import Database
from app.identity.identity_service import IdentityService
from app.models.spin import Spin
from app.services.session_service import SessionService
from app.statistics import engine as stats


@dataclass
class DisplayState:
    last_spin: Spin | None
    history: list[Spin]  # most recent first, length capped at config.history_size
    total_spins: int
    color: stats.BucketStats
    parity: stats.BucketStats
    range_: stats.BucketStats
    dozen: stats.BucketStats
    column: stats.BucketStats
    hot: list[tuple[int, int]]
    cold: list[tuple[int, int]]


class SpinService:
    def __init__(self, db: Database, config: Config):
        self.db = db
        self.config = config
        self.db.ensure_roulette(config.roulette_id, config.roulette_name)
        self.audit = AuditService(db)
        self.identity = IdentityService(db, audit=self.audit)
        self._sync_identity_and_config()
        self.session_service = SessionService(db, config.roulette_id, audit=self.audit)
        # Resume um shift já aberto (ex.: systemd reiniciou o processo) em vez de fragmentar o
        # relatório — ver o docstring de SessionService. Também é o "SYSTEM_STARTED" da trilha de
        # auditoria: cobre tanto o boot real quanto um restart do systemd, que é exatamente a
        # distinção que os eventos de auditoria dessa família precisam registrar.
        session = self.session_service.ensure_open_session(actor_type="system")
        self.audit.log("SYSTEM_STARTED", session_id=session["id"], table_id=session["table_id"], actor_type="system")

    def _sync_identity_and_config(self) -> None:
        """`installation_identity` (SQLite) and `config.yaml`'s `casino_name`/`roulette_name` now
        overlap conceptually — rather than duplicate the "nome da mesa" concept as two
        independently-editable values, `installation_identity` becomes authoritative going
        forward. Two things happen here, both one-directional and cheap (a couple of indexed
        reads/writes at boot, not per-frame):

        1. One-time seed (only right after `_bootstrap_identity` created the row, detected via
           `updated_at == created_at`): copies whatever was already in config.yaml into the new
           identity row, so upgrading an existing installation that already customized
           `roulette_name`/`casino_name` doesn't regress to a generic default.
        2. Every boot after that: `self.config.casino_name`/`roulette_name` (the in-memory values
           every existing screen already reads) are refreshed FROM identity, so editing identity
           via the new admin screen is immediately reflected everywhere the old fields are used,
           without having to touch display.py/splash.py's existing `self.config.casino_name`
           references.
        """
        identity = self.identity.get()
        if identity["updated_at"] == identity["created_at"]:
            seed = {}
            if self.config.casino_name and self.config.casino_name != "CASSINO":
                seed["venue_name"] = self.config.casino_name
            if self.config.roulette_name and self.config.roulette_name != "ROLETA 01":
                seed["table_name"] = self.config.roulette_name
            if seed:
                identity = self.db.update_identity(**seed)
        if identity["venue_name"]:
            self.config.casino_name = identity["venue_name"]
        if identity["table_name"]:
            self.config.roulette_name = identity["table_name"]

    def register_spin(self, number: int) -> Spin:
        session = self.session_service.ensure_open_session()
        spin = self.db.add_spin(self.config.roulette_id, number, session_id=session["id"])
        self.audit.log(
            "SPIN_CREATED", session_id=session["id"], spin_id=spin.id, table_id=session["table_id"],
            actor_type="keypad", source="KEYPAD", new_value=str(number),
        )
        return spin

    def undo_last(self, operator: str = "teclado") -> Spin | None:
        removed = self.db.undo_last(self.config.roulette_id, operator=operator)
        if removed is not None:
            session = self.session_service.ensure_open_session()
            self.audit.log(
                "SPIN_UNDONE", session_id=session["id"], spin_id=removed.id,
                table_id=session["table_id"], actor_type=("admin" if operator == "admin" else "keypad"),
                actor_id=operator, source=operator, old_value=str(removed.number),
            )
        return removed

    def clear_session(self, operator: str = "teclado") -> int:
        """Zera o placar em tela (soft-delete dos giros ativos) — o gesto "-97 ENTER" / "Reiniciar
        sessão atual" do admin. NÃO fecha a sessão formal de relatório (ver `end_session` para
        isso); os novos giros continuam na mesma sessão."""
        session = self.session_service.ensure_open_session()
        n = self.db.clear_session(self.config.roulette_id, operator=operator)
        self.audit.log(
            "SESSION_CLEARED", session_id=session["id"], table_id=session["table_id"],
            actor_type=("admin" if operator == "admin" else "keypad"), actor_id=operator,
            new_value=str(n),
        )
        return n

    def end_session(self, operator: str = "admin") -> dict:
        """Encerra a sessão formal atual (dispara geração de relatório, fora do escopo desta
        classe — ver app/reports/) e zera o placar em tela junto, para o operador começar o
        próximo turno com a tela limpa. Sempre PIN-gated no admin — nunca acionado pelo teclado
        puro, diferente de `clear_session`."""
        n = self.db.clear_session(self.config.roulette_id, operator=operator)
        result = self.session_service.close_current_session(actor_type="admin", actor_id=operator)
        result["cleared_spins"] = n
        return result

    def get_audit_log(self, limit: int = 20):
        return self.db.get_audit_log(self.config.roulette_id, limit=limit)

    def get_last_spin(self) -> Spin | None:
        return self.db.get_last_spin(self.config.roulette_id)

    def get_display_state(self) -> DisplayState:
        # Full non-deleted history is needed for accurate "spins since last occurrence" (cold
        # numbers) and total count; it is bounded by real-world spin volume (a few thousand rows
        # even after months of 24/7 use), so loading it in full is cheap on an RPi3.
        full_history = self.db.get_history(self.config.roulette_id)
        numbers = [s.number for s in full_history]
        window = self.config.statistics_window

        recent = full_history[-self.config.history_size:]
        recent_display = list(reversed(recent))  # newest first, for the on-screen history strip

        return DisplayState(
            last_spin=full_history[-1] if full_history else None,
            history=recent_display,
            total_spins=len(full_history),
            color=stats.color_stats(numbers, window),
            parity=stats.parity_stats(numbers, window),
            range_=stats.range_stats(numbers, window),
            dozen=stats.dozen_stats(numbers, window),
            column=stats.column_stats(numbers, window),
            hot=stats.hottest_numbers(numbers, window, self.config.hot_numbers_count),
            cold=stats.coldest_numbers(numbers, self.config.cold_numbers_count),
        )
