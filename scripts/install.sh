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

# config.yaml é versionado (serve de template inicial), mas cada mesa reescreve o próprio
# arquivo em campo -- pelo menu admin (girar tela, nome do cassino, PIN etc.) ou por
# app/config.py:save_config(). Sem isso, qualquer atualização futura do repositório que
# toque config.yaml quebra "git pull" com "Your local changes... would be overwritten by
# merge" em toda mesa já instalada (bug real visto em campo). skip-worktree diz pro git
# ignorar esse arquivo específico em pulls/checkouts dali em diante -- as mudanças locais
# do operador nunca mais são tocadas, e o repositório segue livre pra evoluir o template
# pra instalações novas.
echo "==> Protegendo config.yaml de sobrescritas por 'git pull' (git update-index --skip-worktree)..."
git -C "${REPO_DIR}" update-index --skip-worktree config.yaml || true

echo "==> Instalando serviço systemd..."
sed "s#__INSTALL_DIR__#${REPO_DIR}#g" "${REPO_DIR}/systemd/roulette-display.service" \
    > /etc/systemd/system/roulette-display.service

systemctl daemon-reload
systemctl enable roulette-display.service

echo "==> Desabilitando login (getty) na tty1 para evitar conflito com o painel..."
systemctl disable getty@tty1.service 2>/dev/null || true

# Se a imagem instalada foi a "com Desktop" (em vez da "Lite" recomendada), o gerenciador
# gráfico (lightdm/gdm3/sddm) sobe sozinho no boot e disputa o /dev/dri (KMSDRM) com o painel --
# sintoma real visto em campo: "Could not queue pageflip: -22" em loop e a tela do painel nunca
# aparece (às vezes mostra a área de trabalho no lugar). Força o boot pra `multi-user.target`
# (texto, sem desktop) incondicionalmente -- inofensivo mesmo numa instalação já Lite, onde não
# existe gerenciador gráfico nenhum pra desabilitar.
echo "==> Desabilitando ambiente gráfico (desktop) para não disputar a tela com o painel..."
systemctl set-default multi-user.target
for dm in lightdm gdm3 gdm sddm; do
    systemctl disable "${dm}.service" 2>/dev/null || true
    systemctl stop "${dm}.service" 2>/dev/null || true
done

echo "==> Configurando boot silencioso (sem mensagens de kernel/desktop)..."
BOOT_DIR="/boot/firmware"
[[ -d "${BOOT_DIR}" ]] || BOOT_DIR="/boot"
CMDLINE_FILE="${BOOT_DIR}/cmdline.txt"
CONFIG_FILE="${BOOT_DIR}/config.txt"

if [[ -f "${CMDLINE_FILE}" ]]; then
    cp "${CMDLINE_FILE}" "${CMDLINE_FILE}.bak.$(date +%Y%m%d%H%M%S)"
    LINE="$(cat "${CMDLINE_FILE}")"
    # "splash" (presente por padrão na imagem oficial) é o que liga a animação de boot do
    # Plymouth (a logo/tela de carregamento do Raspberry Pi OS) -- remove como palavra inteira
    # (não como substring) pra não mexer em outro parâmetro que por acaso contenha essas letras.
    LINE="$(echo " ${LINE} " | sed -E 's/[[:space:]]splash[[:space:]]/ /g')"
    for arg in quiet loglevel=3 logo.nologo vt.global_cursor_default=0 consoleblank=0 plymouth.enable=0; do
        if [[ "${LINE}" != *"${arg}"* ]]; then
            LINE="${LINE} ${arg}"
        fi
    done
    echo "${LINE}" | sed -E 's/[[:space:]]+/ /g; s/^ *//; s/ *$//' > "${CMDLINE_FILE}"
    echo "    cmdline.txt atualizado (boot silencioso, sem logo do Plymouth; backup salvo ao lado)."
else
    echo "    AVISO: ${CMDLINE_FILE} não encontrado, pulei essa etapa (ajuste manualmente)."
fi

if [[ -f "${CONFIG_FILE}" ]]; then
    if ! grep -q "^disable_splash=1" "${CONFIG_FILE}"; then
        echo "disable_splash=1" >> "${CONFIG_FILE}"
    fi
    # Driver de vídeo mais estável no Pi 3 (o KMS completo, vc4-kms-v3d, tem histórico de
    # "Could not queue pageflip: -22" nesse hardware com apps que redesenham continuamente) +
    # mais um buffer de tela pra folga (default de fábrica é 2). Só troca se a linha já existir
    # com outro valor -- não adiciona `dtoverlay=vc4-kms-v3d` do zero se o config.txt não tiver
    # nenhuma linha `dtoverlay=vc4-*`, pra não mexer numa placa/imagem que já veio configurada
    # diferente de propósito.
    if grep -q "^dtoverlay=vc4-kms-v3d" "${CONFIG_FILE}"; then
        sed -i 's/^dtoverlay=vc4-kms-v3d/dtoverlay=vc4-fkms-v3d/' "${CONFIG_FILE}"
    fi
    if grep -q "^max_framebuffers=" "${CONFIG_FILE}"; then
        sed -i 's/^max_framebuffers=.*/max_framebuffers=3/' "${CONFIG_FILE}"
    elif grep -q "^dtoverlay=vc4-fkms-v3d" "${CONFIG_FILE}"; then
        sed -i '/^dtoverlay=vc4-fkms-v3d/a max_framebuffers=3' "${CONFIG_FILE}"
    fi
    echo "    config.txt atualizado (disable_splash=1, vc4-fkms-v3d, max_framebuffers=3)."
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
