"""session.csv — part of the closed-session report package (reports/YYYY/MM/session_code/).

Deliberately a separate module from app/services/export_service.py, which stays exactly as it
was: an on-demand "export whatever is currently on the board" CSV for the admin's existing
"Exportar resultados (CSV)" action, unrelated to a specific closed session or its report
package. Merging the two would conflate "quick ad-hoc export of live data" with "the permanent
record of a closed session" — different purposes, different lifecycles, worth keeping apart.
"""
from __future__ import annotations

import csv
from pathlib import Path

from app.models.roulette_data import color_of, column_of, dozen_of, parity_of, range_of
from app.models.spin import Spin

FIELDNAMES = [
    "venue_name", "table_id", "table_name", "table_code", "session_id", "spin_sequence",
    "number", "color", "parity", "range", "dozen", "column", "created_at", "input_source", "status",
]


def write_session_csv(session_row, spins: list[Spin], dest_path: str | Path) -> Path:
    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()
        for i, spin in enumerate(spins, start=1):
            writer.writerow({
                "venue_name": session_row["venue_name"],
                "table_id": session_row["table_id"],
                "table_name": session_row["table_name"],
                "table_code": session_row["table_code"],
                "session_id": session_row["session_code"],
                "spin_sequence": i,
                "number": spin.number,
                "color": color_of(spin.number),
                "parity": parity_of(spin.number) or "",
                "range": range_of(spin.number) or "",
                "dozen": dozen_of(spin.number) or "",
                "column": column_of(spin.number) or "",
                "created_at": spin.created_at,
                "input_source": "KEYPAD",
                "status": "ATIVO" if not spin.deleted else "ARQUIVADO",
            })
    return dest
