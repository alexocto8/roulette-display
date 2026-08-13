"""Thin wrapper over Database's audit_log methods (app/database/db.py) — thin, but not a pure
pass-through: it's where the "never log secrets" rule (PIN, SMTP password, SMTP/Wi-Fi credentials,
tokens, the Ed25519 private key) is enforced centrally as a safety net, independent of every
individual call site remembering to redact by hand.
"""
from __future__ import annotations

from app.database.db import Database

# Substrings (case-insensitive) that, if found in a field's *name* passed via `metadata`, or in
# the `reason` text, mark the corresponding value as sensitive — the value itself is replaced,
# never partially logged. This is deliberately conservative (a few false positives redacting a
# harmless field is an acceptable cost; a real secret slipping through is not).
_SENSITIVE_NAME_HINTS = (
    "pin", "password", "senha", "secret", "token", "private_key", "chave_privada", "credential",
)
_REDACTED = "[REDACTED]"


def _looks_sensitive(field_name: str) -> bool:
    lowered = field_name.lower()
    return any(hint in lowered for hint in _SENSITIVE_NAME_HINTS)


class AuditService:
    def __init__(self, db: Database):
        self.db = db

    def log(
        self, event_type: str, *, session_id: int | None = None, spin_id: int | None = None,
        table_id: str | None = None, actor_type: str = "system", actor_id: str | None = None,
        source: str | None = None, old_value: str | None = None, new_value: str | None = None,
        reason: str | None = None, metadata: dict | None = None, sensitive_field: str | None = None,
    ):
        """`sensitive_field` — quando o evento é sobre um campo sensível (ex.: "smtp_password"),
        o chamador passa o NOME do campo aqui; `old_value`/`new_value` são automaticamente
        trocados por `[REDACTED]` antes de gravar, não importa o que o chamador tenha passado.
        Também aplica a mesma checagem a qualquer chave de `metadata` cujo nome pareça sensível."""
        if sensitive_field is not None or (old_value is not None and _looks_sensitive(str(new_value or ""))):
            old_value = _REDACTED if old_value is not None else None
            new_value = _REDACTED if new_value is not None else None
        if metadata:
            metadata = {
                k: (_REDACTED if _looks_sensitive(k) else v) for k, v in metadata.items()
            }
        return self.db.append_audit_event(
            event_type, session_id=session_id, spin_id=spin_id, table_id=table_id,
            actor_type=actor_type, actor_id=actor_id, source=source, old_value=old_value,
            new_value=new_value, reason=reason, metadata=metadata,
        )

    def get_events(self, **filters):
        return self.db.get_audit_events(**filters)

    def verify_integrity(self) -> tuple[bool, str | None]:
        return self.db.verify_audit_integrity()
