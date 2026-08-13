#!/usr/bin/env bash
# Atualiza o painel a partir do git remoto, reinstala dependências e reinicia o serviço.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_DIR}"

echo "==> Fazendo backup do banco antes de atualizar..."
"${REPO_DIR}/scripts/backup.sh" || echo "AVISO: backup falhou, continuando mesmo assim."

echo "==> Buscando atualizações..."
git pull --ff-only

echo "==> Atualizando dependências Python..."
"${REPO_DIR}/venv/bin/pip" install --upgrade -r "${REPO_DIR}/requirements.txt"

echo "==> Reiniciando serviço..."
systemctl restart roulette-display.service

echo "==> Atualização concluída."
