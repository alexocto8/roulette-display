#!/usr/bin/env bash
# Infraestrutura para o teste de execução prolongada (24h/48h/72h) pedido na auditoria — feito
# para rodar no Raspberry Pi 3 FÍSICO, ao lado do serviço systemd real, não em CI.
#
# O que este script NÃO faz: não roda ele mesmo por 24-72h. Ele tira UMA amostra e sai — quem
# decide a duração é o agendamento externo (cron/systemd timer, ver abaixo). Isso é deliberado:
# um script bash de longa duração seria ele mesmo mais um processo pra monitorar quanto a
# vazamentos/travamentos, o oposto do que se quer de uma ferramenta de observação.
#
# Uso recomendado (crontab do usuário que roda o painel, ou root):
#   * * * * * /caminho/roulette-display/scripts/long_run_monitor.sh >> /caminho/roulette-display/logs/long-run-monitor.log 2>&1
#
# Cada linha do CSV de saída (logs/long-run-monitor.csv) é uma amostra com:
#   timestamp, rss_kb, cpu_pct, temp_c, total_spins, db_size_kb, systemd_active,
#   systemd_restarts, errors_last_window, wal_size_kb
#
# Ao final do teste (24h/48h/72h), inspecione o CSV: RSS e temp_c devem oscilar dentro de uma
# faixa estável (sem tendência de crescimento monotônico == vazamento), systemd_restarts deve
# ficar em 0 num equipamento saudável (>0 é normal só se o watchdog realmente pegou uma trava
# real, não um bug do monitor).
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="${REPO_DIR}/logs"
CSV_PATH="${LOG_DIR}/long-run-monitor.csv"
DB_PATH="${REPO_DIR}/data/roulette.db"
SERVICE_NAME="roulette-display"

mkdir -p "${LOG_DIR}"

if [[ ! -f "${CSV_PATH}" ]]; then
    echo "timestamp,rss_kb,cpu_pct,temp_c,total_spins,db_size_kb,systemd_active,systemd_restarts,errors_last_window,wal_size_kb" > "${CSV_PATH}"
fi

TS="$(date -Is)"

PID="$(systemctl show -p MainPID --value "${SERVICE_NAME}" 2>/dev/null || echo 0)"
RSS_KB=0
CPU_PCT="0.0"
if [[ -n "${PID}" && "${PID}" != "0" && -d "/proc/${PID}" ]]; then
    RSS_KB="$(awk '/VmRSS/{print $2}' "/proc/${PID}/status" 2>/dev/null || echo 0)"
    # %CPU acumulado desde o início do processo (média histórica, não instantânea) — suficiente
    # pra detectar uma trava (CPU indo a 100% e ficando) sem precisar de duas amostras.
    CPU_PCT="$(ps -o %cpu= -p "${PID}" 2>/dev/null | tr -d ' ' || echo 0.0)"
fi

TEMP_C="NA"
if command -v vcgencmd >/dev/null 2>&1; then
    TEMP_RAW="$(vcgencmd measure_temp 2>/dev/null || true)"  # formato: temp=42.8'C
    TEMP_C="$(echo "${TEMP_RAW}" | grep -oE '[0-9]+\.[0-9]+' || echo NA)"
fi

TOTAL_SPINS=0
DB_SIZE_KB=0
WAL_SIZE_KB=0
if [[ -f "${DB_PATH}" ]] && command -v sqlite3 >/dev/null 2>&1; then
    TOTAL_SPINS="$(sqlite3 "${DB_PATH}" "SELECT COUNT(*) FROM spins WHERE deleted = 0;" 2>/dev/null || echo 0)"
    DB_SIZE_KB="$(du -k "${DB_PATH}" 2>/dev/null | cut -f1 || echo 0)"
    [[ -f "${DB_PATH}-wal" ]] && WAL_SIZE_KB="$(du -k "${DB_PATH}-wal" 2>/dev/null | cut -f1 || echo 0)"
fi

SYSTEMD_ACTIVE="$(systemctl is-active "${SERVICE_NAME}" 2>/dev/null || echo unknown)"
SYSTEMD_RESTARTS="$(systemctl show -p NRestarts --value "${SERVICE_NAME}" 2>/dev/null || echo NA)"

# Aproximação simples e suficiente pra uma amostra de monitoramento (não uma auditoria de log
# completa): ERROR/CRITICAL nas últimas ~500 linhas do log da aplicação. Como o CSV já registra
# um timestamp por amostra, picos aparecem claramente numa análise posterior mesmo sem uma janela
# de tempo exata aqui.
ERRORS_LAST_WINDOW=0
if [[ -f "${LOG_DIR}/app.log" ]]; then
    ERRORS_LAST_WINDOW="$(tail -n 500 "${LOG_DIR}/app.log" 2>/dev/null | grep -cE "ERROR|CRITICAL" || echo 0)"
fi

echo "${TS},${RSS_KB},${CPU_PCT},${TEMP_C},${TOTAL_SPINS},${DB_SIZE_KB},${SYSTEMD_ACTIVE},${SYSTEMD_RESTARTS},${ERRORS_LAST_WINDOW},${WAL_SIZE_KB}" >> "${CSV_PATH}"
