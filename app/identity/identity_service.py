"""Business-logic layer over Database's installation_identity methods: validates/normalizes field
values (max length, table_code charset) before they reach the database, and logs an
IDENTITY_CHANGED audit event per changed field — so app/ui/admin.py stays a thin UI layer instead
of duplicating validation or audit-logging logic.

`table_id` is not accepted as a field here at all (Database.update_identity already rejects it,
this is the second, redundant layer of the same guarantee — table_id is permanent by design).
"""
from __future__ import annotations

import re

from app.audit.audit_service import AuditService
from app.database.db import Database

MAX_LENGTHS = {
    "venue_name": 80,
    "table_name": 50,
    "table_code": 30,
    "table_location": 80,
    "device_name": 63,
    "venue_logo_path": 500,  # caminho de arquivo — bem mais folgado que os campos de texto livre
    "report_title": 80,
    "report_subtitle": 120,
    "venue_address": 120,
    "venue_phone": 30,
    "venue_email": 80,
    "venue_website": 80,
}

# "Permitir preferencialmente: A-Z a-z 0-9 _ -" — qualquer outro caractere é removido (não
# rejeitado com erro: normalizar é mais amigável para um operador digitando num numpad/teclado
# improvisado do que travar a edição por causa de um caractere incomum).
_TABLE_CODE_ALLOWED = re.compile(r"[^A-Za-z0-9_-]")


def normalize_table_code(raw: str) -> str:
    return _TABLE_CODE_ALLOWED.sub("", raw.strip())[: MAX_LENGTHS["table_code"]]


class IdentityService:
    def __init__(self, db: Database, audit: AuditService | None = None):
        self.db = db
        self.audit = audit or AuditService(db)

    def get(self):
        return self.db.get_identity()

    def update(self, actor_id: str | None = None, **fields):
        cleaned: dict[str, str] = {}
        for key, value in fields.items():
            if value is None:
                continue
            if key not in MAX_LENGTHS:
                raise ValueError(f"campo de identidade desconhecido: {key!r}")
            text = str(value).strip()
            if key == "table_code":
                text = normalize_table_code(text)
            cleaned[key] = text[: MAX_LENGTHS[key]]

        if not cleaned:
            return self.get()

        before = self.get()
        after = self.db.update_identity(**cleaned)
        for field in cleaned:
            if before[field] != after[field]:
                self.audit.log(
                    "IDENTITY_CHANGED", table_id=after["table_id"], actor_type="admin",
                    actor_id=actor_id, old_value=f"{field}={before[field]!r}",
                    new_value=f"{field}={after[field]!r}",
                )
        return after
