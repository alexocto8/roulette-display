"""Builds and sends the session report e-mail (section 46). Pure `smtplib` (stdlib, no new
dependency) — this project already avoids heavy dependencies wherever a lighter option exists.
"""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from pathlib import Path

from app.config import Config
from app.delivery import smtp_credentials

logger = logging.getLogger("roulette.delivery")


class SmtpNotConfiguredError(RuntimeError):
    pass


def build_message(config: Config, session_row, report_paths: dict) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = f"[{session_row['venue_name'] or 'Roulette'}] {session_row['table_name']} - Relatorio {session_row['session_code']}"
    msg["From"] = config.email_from or config.smtp_username
    msg["To"] = config.email_to
    if config.email_cc:
        msg["Cc"] = config.email_cc

    started = (session_row["started_at"] or "")[:16].replace("T", " ")
    ended = (session_row["ended_at"] or "")[:16].replace("T", " ") if session_row["ended_at"] else "-"
    body = (
        "Relatorio automatico de sessao\n\n"
        f"Estabelecimento: {session_row['venue_name'] or '-'}\n"
        f"Mesa: {session_row['table_name']}\n"
        f"Codigo: {session_row['table_code'] or '-'}\n"
        f"Sessao: {session_row['session_code']}\n"
        f"Inicio: {started}\n"
        f"Encerramento: {ended}\n"
    )
    msg.set_content(body)

    for kind, path in report_paths.items():
        if kind not in ("pdf", "csv", "json"):
            continue
        path = Path(path)
        if not path.exists():
            continue
        data = path.read_bytes()
        maintype, subtype = {
            "pdf": ("application", "pdf"),
            "csv": ("text", "csv"),
            "json": ("application", "json"),
        }[kind]
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=path.name)

    return msg


def send_message(config: Config, msg: EmailMessage) -> None:
    if not config.smtp_host or not config.email_to:
        raise SmtpNotConfiguredError("SMTP não configurado (smtp_host/email_to ausentes)")

    password = smtp_credentials.get_smtp_password(config.resolve(config.smtp_credentials_path))
    security = (config.smtp_security or "NONE").upper()

    if security == "SSL_TLS":
        server = smtplib.SMTP_SSL(config.smtp_host, config.smtp_port, timeout=20)
    else:
        server = smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=20)
    try:
        if security == "STARTTLS":
            server.starttls()
        if config.smtp_username:
            server.login(config.smtp_username, password)
        server.send_message(msg)
    finally:
        server.quit()


def send_report_email(config: Config, session_row, report_paths: dict) -> None:
    msg = build_message(config, session_row, report_paths)
    send_message(config, msg)
