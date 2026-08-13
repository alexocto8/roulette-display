"""Session lifecycle — deliberately a different concept from `SpinService.clear_session()` /
the "-97 ENTER" keyboard shortcut (see app/ui/display.py), which only resets the on-screen board
and never touches the `sessions` table. A "session" here is the period a professional report
covers: it starts on first boot (or resumes an already-open one after a restart, so systemd
bouncing the process never fragments a shift's report) and only ends when an administrator
explicitly closes it (PIN-gated "Encerrar sessão" — see app/services/spin_service.py's
`end_session`), which is what triggers report generation.

Every session row is a frozen snapshot of the installation identity at the moment it opened —
renaming the table later must never change how a past session's report describes itself.
"""
from __future__ import annotations

from app.audit.audit_service import AuditService
from app.database.db import Database


class SessionService:
    def __init__(self, db: Database, roulette_id: int, audit: AuditService | None = None):
        self.db = db
        self.roulette_id = roulette_id
        self.audit = audit or AuditService(db)

    def ensure_open_session(self, actor_type: str = "system", actor_id: str | None = None):
        """Returns the currently open session, opening a new one (with a fresh identity
        snapshot) only if none exists yet. Safe to call as often as needed — cheap read in the
        common case (one indexed SELECT)."""
        existing = self.db.get_open_session(self.roulette_id)
        if existing is not None:
            return existing
        identity = self.db.get_identity()
        session = self.db.open_session(self.roulette_id, identity)
        self.audit.log(
            "SESSION_STARTED", session_id=session["id"], table_id=identity["table_id"],
            actor_type=actor_type, actor_id=actor_id, new_value=session["session_code"],
        )
        return session

    def close_current_session(self, actor_type: str = "admin", actor_id: str | None = None) -> dict:
        """Closes the open session and immediately opens the next one (the table is never left
        without an open session). Returns {"closed": row, "opened": row} — the caller (typically
        `SpinService.end_session`) is responsible for anything session-boundary related that also
        needs to happen, like resetting the visible board and triggering report generation."""
        current = self.ensure_open_session(actor_type=actor_type, actor_id=actor_id)
        closed = self.db.close_session(current["id"])
        self.audit.log(
            "SESSION_CLOSED", session_id=closed["id"], table_id=closed["table_id"],
            actor_type=actor_type, actor_id=actor_id,
        )
        identity = self.db.get_identity()
        opened = self.db.open_session(self.roulette_id, identity)
        self.audit.log(
            "SESSION_STARTED", session_id=opened["id"], table_id=identity["table_id"],
            actor_type=actor_type, actor_id=actor_id, new_value=opened["session_code"],
        )
        return {"closed": closed, "opened": opened}
