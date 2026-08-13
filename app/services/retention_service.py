"""Retenção de dados: giros com mais de `config.data_retention_days` são arquivados (CSV em
`exports_dir`) e então removidos de verdade da tabela `spins` — existe pra uma mesa 24/7 não
crescer o banco pra sempre (ver README, "Limitação conhecida: histórico muito longo"). O corte é
por `created_at` (quando a linha foi escrita), não `timestamp`, para não depender de o operador
poder editar o horário do giro. `spin_audit`/`audit_log` nunca são tocados aqui — são a trilha de
auditoria de verdade, bem menor, e `audit_log` tem cadeia de hash que quebraria se linhas fossem
removidas."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from app.config import Config
from app.database.db import Database

logger = logging.getLogger("roulette.retention")


class RetentionService:
    def __init__(self, db: Database, config: Config):
        self.db = db
        self.config = config

    def enforce_retention(self) -> int:
        """Arquiva e purga giros mais antigos que `data_retention_days`. Idempotente: se não há
        nada além do corte, não cria arquivo nem toca no banco -- seguro de chamar repetidamente
        (loop principal chama isso periodicamente, ver app/ui/display.py)."""
        cutoff = (datetime.now() - timedelta(days=max(1, self.config.data_retention_days))).isoformat()
        roulette_id = self.config.roulette_id

        count = self.db.count_spins_older_than(roulette_id, cutoff)
        if count == 0:
            return 0

        exports_dir = self.config.resolve(self.config.exports_dir)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = exports_dir / f"arquivo-retencao-{stamp}.csv"
        self.db.export_spins_older_than(roulette_id, cutoff, dest)

        purged = self.db.purge_spins_older_than(roulette_id, cutoff)
        logger.info(
            "Retenção de dados: %d giro(s) com mais de %d dias arquivados em %s e removidos do banco",
            purged, self.config.data_retention_days, dest,
        )
        return purged
