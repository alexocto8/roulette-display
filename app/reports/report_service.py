"""Orchestrates the whole "close a session -> produce a report package" flow: PDF/CSV/JSON files
on disk, a SHA-256 digest, and an Ed25519 signature over that digest (see signing.py). Called
from the admin panel's "Encerrar sessão" flow (never from the keyboard/render path) — heavy work
(PDF layout, image decode) happens here, well outside anything that could delay a spin.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from app.analytics.analytics_service import AnalyticsService
from app.analytics import periods
from app.audit.audit_service import AuditService
from app.config import Config
from app.database.db import Database
from app.reports import filenames, json_export, pdf_report, session_csv, signing

logger = logging.getLogger("roulette.reports")


def _audit_summary(db: Database, session_id: int) -> dict:
    integrity_ok, broken_event_id = db.verify_audit_integrity()
    undo_count = db.count_audit_events(session_id=session_id, event_type="SPIN_UNDONE")
    clear_count = db.count_audit_events(session_id=session_id, event_type="SESSION_CLEARED")
    admin_events = db.get_audit_events(session_id=session_id, limit=1000)
    return {
        "integrity": "VALID" if integrity_ok else "BROKEN",
        "integrity_broken_event_id": broken_event_id,
        "correction_count": undo_count,
        "session_clear_count": clear_count,
        "total_events": len(admin_events),
    }


def generate_report(
    db: Database, config: Config, session_row, *, roulette_id: int,
) -> dict:
    """Returns a dict of the paths written (only the keys for formats actually enabled in
    config) plus the computed sha256 hex digest. Any single artifact failing to generate (e.g. a
    corrupt logo slipping past validation, though branding.is_valid_stored_logo should have
    already caught that) must not take down the others — see the try/except around the PDF step."""
    identity_row = db.get_identity()
    audit = AuditService(db)
    analytics = AnalyticsService(db)

    # Usa `spins` que já buscamos aqui, em vez de chamar `analytics.compute()` (que buscaria de
    # novo do banco) — mesmo conjunto de dados, uma consulta a menos.
    spins = db.get_report_spins_for_session(session_row["id"])
    snapshot = analytics.build_snapshot_from_spins(
        periods.SESSAO_ATUAL, spins, session_id=session_row["id"], hot_n=5, cold_n=5,
    )

    integrity_ok, _ = db.verify_audit_integrity()
    audit_summary = _audit_summary(db, session_row["id"]) if config.report_include_audit_summary else None
    analytics_dict = _snapshot_to_dict(snapshot) if config.report_include_analytics else None

    session_dict = json_export.build_session_dict(session_row, spins, analytics=analytics_dict, audit_summary=audit_summary)
    canonical = json_export.canonical_bytes(session_dict)
    sha256_hex = hashlib.sha256(canonical).hexdigest()

    key_path = config.resolve(config.report_signing_key_path)
    signature_hex = signing.sign_bytes(key_path, canonical).hex()

    basename = filenames.report_basename(session_row)
    started = session_row["started_at"] or ""
    year, month = (started[:4] or "0000"), (started[5:7] or "00")
    report_dir = config.resolve(config.reports_dir) / year / month / session_row["session_code"]
    report_dir.mkdir(parents=True, exist_ok=True)

    written = {"directory": report_dir, "sha256": sha256_hex}

    if config.report_generate_json:
        json_path = report_dir / f"{basename}.json"
        json_export.write_session_json(session_dict, json_path)
        written["json"] = json_path

    if config.report_generate_csv:
        csv_path = report_dir / f"{basename}.csv"
        session_csv.write_session_csv(session_row, spins, csv_path)
        written["csv"] = csv_path

    if config.report_generate_pdf:
        try:
            pdf_path = report_dir / f"{basename}.pdf"
            pdf_report.generate_pdf_report(identity_row, session_row, spins, snapshot, integrity_ok, pdf_path)
            written["pdf"] = pdf_path
        except Exception:
            logger.exception("Falha ao gerar PDF do relatório — CSV/JSON continuam disponíveis")

    (report_dir / "report.sha256").write_text(sha256_hex + "\n", encoding="utf-8")
    (report_dir / "report.sig").write_text(signature_hex + "\n", encoding="utf-8")
    written["sha256_path"] = report_dir / "report.sha256"
    written["sig_path"] = report_dir / "report.sig"

    audit.log(
        "REPORT_GENERATED", session_id=session_row["id"], table_id=session_row["table_id"],
        actor_type="admin", new_value=sha256_hex[:16],
        metadata={"formats": [k for k in ("pdf", "csv", "json") if k in written]},
    )
    return written


def _snapshot_to_dict(snapshot) -> dict:
    return {
        "period": snapshot.period,
        "total_spins": snapshot.total_spins,
        "session_duration_seconds": snapshot.session_duration_seconds,
        "spins_per_hour": snapshot.spins_per_hour,
        "interval_avg_seconds": snapshot.interval_avg_seconds,
        "interval_min_seconds": snapshot.interval_min_seconds,
        "interval_max_seconds": snapshot.interval_max_seconds,
        "correction_count": snapshot.correction_count,
        "undo_count": snapshot.undo_count,
        "correction_rate": snapshot.correction_rate,
        "color": dict(snapshot.color.counts, total=snapshot.color.total),
        "parity": dict(snapshot.parity.counts, total=snapshot.parity.total),
        "range": dict(snapshot.range_.counts, total=snapshot.range_.total),
        "dozen": dict(snapshot.dozen.counts, total=snapshot.dozen.total),
        "column": dict(snapshot.column.counts, total=snapshot.column.total),
        "hot": snapshot.hot,
        "cold": snapshot.cold,
        "streaks": snapshot.streaks,
        "chi_square": snapshot.chi_square,
    }
