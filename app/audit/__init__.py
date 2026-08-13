"""Audit trail: a general, hash-chained event log (`audit_log`) complementing the older,
correction-only `spin_audit` table in app/database/db.py (kept as-is, still used by the quick
"Ver auditoria (correções)" admin screen — not rewritten, to avoid touching a small, already
tested, working path). `audit_log` is the newer, broader catalog: system/session/admin/license/
report events, with old/new values and a tamper-evident hash chain (see integrity.py).
"""
