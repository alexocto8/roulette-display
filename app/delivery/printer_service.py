"""Thermal receipt (section 49): a compact plain-text summary for an 80mm/58mm ESC/POS printer.
`build_receipt_text()` is pure and fully testable without any printer hardware — it's the part
that actually matters for correctness (the numbers on the slip). `print_receipt()` is a thin,
best-effort integration point: `python-escpos` is imported lazily, only when printing is actually
attempted, so installations that never use a printer (the common case) never pay for the extra
dependency (which pulls in pyusb and, on some systems, needs libusb — not something to force on
every install). Never called automatically unless `config.print_on_session_end` is explicitly on.
"""
from __future__ import annotations

import logging

from app.config import Config

logger = logging.getLogger("roulette.delivery")

_LINE_WIDTH = 32  # típico pra uma impressora térmica de 58mm; 80mm cabe ~42-48


def _dotted_line(label: str, value: str, width: int = _LINE_WIDTH) -> str:
    dots = max(1, width - len(label) - len(value))
    return f"{label} {'.' * dots} {value}"


def build_receipt_text(identity_row, session_row, snapshot) -> str:
    lines = [
        (identity_row["venue_name"] or "CASSINO").upper(),
        session_row["table_name"].upper(),
        session_row["table_code"] or "",
        (session_row["started_at"] or "")[:10],
        f"{(session_row['started_at'] or '')[11:16]} - {(session_row['ended_at'] or '')[11:16]}",
        "",
        _dotted_line("GIROS", str(snapshot.total_spins)),
        _dotted_line("GIROS/H", f"{snapshot.spins_per_hour:.1f}"),
        _dotted_line("VERMELHO", f"{snapshot.color.percentage('red')}%"),
        _dotted_line("PRETO", f"{snapshot.color.percentage('black')}%"),
        _dotted_line("ZERO", f"{snapshot.color.percentage('green')}%"),
        "",
        "HOT",
    ]
    for number, count in snapshot.hot[:3]:
        lines.append(_dotted_line(f"{number:>2}", str(count)))
    lines.append("")
    lines.append("COLD")
    for number, count in snapshot.cold[:3]:
        lines.append(_dotted_line(f"{number:>2}", str(count)))
    lines += [
        "",
        _dotted_line("CORRECOES", str(snapshot.undo_count)),
        "",
        "SESSION",
        session_row["session_code"],
    ]
    return "\n".join(lines)


def print_receipt(config: Config, text: str) -> bool:
    """Best-effort: returns False (and logs) instead of raising on any failure — a printer being
    off/disconnected must never be treated as a fatal error anywhere in the app. Returns False
    immediately, with no import attempt at all, if printing isn't configured."""
    if config.printer_type == "NONE" or not config.printer_address:
        return False
    try:
        from escpos.printer import Network, Usb  # type: ignore[import-not-found]
    except ImportError:
        logger.warning("python-escpos não instalado — impressão desabilitada (pip install python-escpos)")
        return False

    try:
        if config.printer_type == "ESCPOS_NETWORK":
            host, _, port = config.printer_address.partition(":")
            printer = Network(host, int(port) if port else 9100)
        else:
            printer = Usb(0x0000, 0x0000, profile="default")  # placeholder — VID/PID reais dependem do modelo
        printer.text(text + "\n\n\n")
        printer.cut()
        return True
    except Exception:
        logger.warning("Falha ao imprimir recibo", exc_info=True)
        return False
