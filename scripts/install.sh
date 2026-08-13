#!/usr/bin/env bash
# Prepara um Raspberry Pi OS (recomendado: Raspberry Pi OS Lite, sem desktop) recém-instalado
# para rodar o painel de roleta como um serviço systemd que sobe direto em fullscreen no boot.
#
# Uso:
#   git clone <repo> roulette-display
#   cd roulette-display
#   sudo ./scripts/install.sh
#   sudo reboot
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Este script precisa ser executado como root (use: sudo ./scripts/install.sh)" >&2
    exit 1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
echo "==> Instalando em: ${REPO_DIR}"

echo "==> Atualizando índice de pacotes..."
apt-get update -y

echo "==> Instalando dependências de sistema..."
apt-get install -y \
    python3 \
    python3-venv \
    python3-pip \
    python3-dev \
    build-essential \
    git \
    sqlite3 \
    libsdl2-2.0-0 \
    libsdl2-dev \
    libsdl2-image-2.0-0 \
    libsdl2-ttf-2.0-0 \
    libsdl2-mixer-2.0-0 \
    libjpeg-dev \
    zlib1g-dev \
    libfreetype6-dev \
    fonts-dejavu-core

echo "==> Criando ambiente virtual Python..."
if [[ ! -d "${REPO_DIR}/venv" ]]; then
    python3 -m venv "${REPO_DIR}/venv"
fi

echo "==> Instalando dependências Python (isso pode demorar alguns minutos em um Pi 3)..."
"${REPO_DIR}/venv/bin/pip" install --upgrade pip
"${REPO_DIR}/venv/bin/pip" install -r "${REPO_DIR}/requirements.txt"

echo "==> Criando diretórios de dados..."
mkdir -p "${REPO_DIR}/data/backups" "${REPO_DIR}/data/exports" "${REPO_DIR}/logs"
chmod -R u+rwX "${REPO_DIR}/data" "${REPO_DIR}/logs"

if [[ ! -f "${REPO_DIR}/config.yaml" ]]; then
    echo "==> config.yaml não encontrado, será criado com valores padrão na primeira execução."
fi

echo "==> Instalando serviço systemd..."
sed "s#__INSTALL_DIR__#${REPO_DIR}#g" "${REPO_DIR}/systemd/roulette-display.service" \
    > /etc/systemd/system/roulette-display.service

systemctl daemon-reload
systemctl enable roulette-display.service

echo "==> Desabilitando login (getty) na tty1 para evitar conflito com o painel..."
systemctl disable getty@tty1.service 2>/dev/null || true

echo "==> Configurando boot silencioso (sem mensagens de kernel/desktop)..."
BOOT_DIR="/boot/firmware"
[[ -d "${BOOT_DIR}" ]] || BOOT_DIR="/boot"
CMDLINE_FILE="${BOOT_DIR}/cmdline.txt"
CONFIG_FILE="${BOOT_DIR}/config.txt"

if [[ -f "${CMDLINE_FILE}" ]]; then
    cp "${CMDLINE_FILE}" "${CMDLINE_FILE}.bak.$(date +%Y%m%d%H%M%S)"
    LINE="$(cat "${CMDLINE_FILE}")"
    for arg in quiet loglevel=3 logo.nologo vt.global_cursor_default=0 consoleblank=0; do
        if [[ "${LINE}" != *"${arg}"* ]]; then
            LINE="${LINE} ${arg}"
        fi
    done
    echo "${LINE}" | sed 's/^ *//' > "${CMDLINE_FILE}"
    echo "    cmdline.txt atualizado (backup salvo ao lado)."
else
    echo "    AVISO: ${CMDLINE_FILE} não encontrado, pulei essa etapa (ajuste manualmente)."
fi

if [[ -f "${CONFIG_FILE}" ]]; then
    if ! grep -q "^disable_splash=1" "${CONFIG_FILE}"; then
        echo "disable_splash=1" >> "${CONFIG_FILE}"
    fi
    echo "    config.txt atualizado (disable_splash=1)."
else
    echo "    AVISO: ${CONFIG_FILE} não encontrado, pulei essa etapa (ajuste manualmente)."
fi

echo ""
echo "==> Instalação concluída."
echo "    Substitua a marca d'água em ${REPO_DIR}/assets/ (logo.png, background.png, splash.png)"
echo "    e ajuste ${REPO_DIR}/config.yaml conforme o cassino."
echo "    Reinicie para o painel subir automaticamente: sudo reboot"
echo ""
echo "    Ver logs em tempo real:  journalctl -u roulette-display -f"
echo "    Ver status do serviço:   systemctl status roulette-display"
