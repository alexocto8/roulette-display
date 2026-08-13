#!/usr/bin/env bash
# Backup manual do banco SQLite (fora do painel de administração). Pode ser chamado por cron.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB_PATH="${REPO_DIR}/data/roulette.db"
BACKUP_DIR="${REPO_DIR}/data/backups"
STAMP="$(date +%Y%m%d-%H%M%S)"
DEST="${BACKUP_DIR}/roulette-backup-${STAMP}.db"

mkdir -p "${BACKUP_DIR}"

if [[ ! -f "${DB_PATH}" ]]; then
    echo "Banco não encontrado em ${DB_PATH}" >&2
    exit 1
fi

if command -v sqlite3 >/dev/null 2>&1; then
    # ".backup" usa a API de backup do SQLite: seguro mesmo com o app rodando e escrevendo.
    sqlite3 "${DB_PATH}" ".backup '${DEST}'"
else
    cp "${DB_PATH}" "${DEST}"
fi

echo "Backup criado em: ${DEST}"

# Mantém só os 30 backups mais recentes (mesma política do painel administrativo) — sem isso, em
# 24/7 por meses, esse diretório cresce sem limite (cada backup é uma cópia inteira do banco).
RETENTION=30
ls -1t "${BACKUP_DIR}"/roulette-backup-*.db 2>/dev/null | tail -n +$((RETENTION + 1)) | while read -r old; do
    rm -f -- "${old}"
done
