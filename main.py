#!/usr/bin/env python3
"""Entrypoint for the roulette electronic scoreboard.

Run directly (`python3 main.py`) for local development, or via the systemd unit in
systemd/roulette-display.service for production (Restart=always keeps it running 24/7).
"""
from __future__ import annotations

import sys

from app.config import load_config
from app.database.db import Database
from app.logging_setup import setup_logging
from app.version import __version__


def main() -> int:
    config = load_config()
    logger = setup_logging(config)
    logger.info(
        "Starting roulette display v%s - casino=%r table=%r",
        __version__, config.casino_name, config.roulette_name,
    )

    # Portão de licença: roda ANTES de abrir o banco ou o painel. Bloqueia (não sai do processo)
    # mostrando "SISTEMA NÃO ATIVADO"/"LICENÇA INVÁLIDA" até a licença ficar válida ou o processo
    # ser encerrado — ver app/ui/license_screen.py sobre por que não é um simples "return 1" aqui.
    from app.ui.license_screen import ensure_licensed

    if not ensure_licensed(config):
        return 0

    db = Database(config.resolve(config.database_path))
    try:
        db.initialize()
    except Exception:
        logger.exception("Failed to initialize database at %s", config.database_path)
        # Nunca deixa o processo simplesmente morrer pra uma tela preta/console: mostra uma
        # mensagem genérica (sem detalhe técnico — isso já foi pro log acima) por alguns segundos
        # antes de sair, pro operador ver que o equipamento sabe que algo está errado, não que
        # travou. O Restart=always do systemd tenta de novo em seguida.
        from app.ui.failsafe_screen import show_failsafe

        show_failsafe(config, "SISTEMA TEMPORARIAMENTE INDISPONÍVEL", "Tente novamente em instantes.")
        return 1

    try:
        # Imported lazily so a missing/broken pygame install fails with a clear log line instead
        # of aborting before logging is configured.
        from app.ui.display import RouletteDisplay

        display = RouletteDisplay(config, db)
        display.run()
    except Exception:
        logger.exception("Fatal error in display loop")
        from app.ui.failsafe_screen import show_failsafe

        show_failsafe(config, "ERRO DO SISTEMA", "Contate o suporte técnico.")
        return 1

    logger.info("Roulette display exited cleanly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
