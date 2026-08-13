"""Application configuration: loaded from config.yaml, with safe defaults for every field.

Kept as a plain dataclass (not a singleton/global) so tests can build a Config in memory and the
future admin panel can mutate + persist it without touching module-level state.
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

DEFAULT_CONFIG_PATH = "config.yaml"

# Project root = parent of the app/ package this file lives in.
PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Config:
    casino_name: str = "CASSINO"
    roulette_name: str = "ROLETA 01"
    roulette_id: int = 1

    history_size: int = 15
    statistics_window: int = 300  # console de referência do cliente guarda os últimos 300 números
    hot_numbers_count: int = 3
    cold_numbers_count: int = 3

    # Exibidos como informação da mesa (não afetam registro/estatística de números). Texto livre
    # (não numérico) de propósito: formatação de moeda/limite varia por cassino e não vale a pena
    # validar/parsear aqui — é só o que aparece na tela.
    currency: str = "R$"
    min_bet: str = "5,00"
    max_bet: str = "5.000,00"

    fullscreen: bool = True
    target_fps: int = 30
    idle_fps: int = 8  # frame rate when nothing is animating, keeps CPU usage low on RPi3
    hide_cursor: bool = True
    # Rotação de tela em SOFTWARE (0/90/180/270) — só necessária quando o monitor físico é
    # paisagem, será montado de lado (retrato), e o driver de vídeo não faz a rotação sozinho (ex.:
    # console de VM sem suporte real a `xrandr --rotate`). No Raspberry Pi em produção (KMSDRM), a
    # rotação já é feita pelo kernel via `video=...,rotate=` no cmdline.txt — deixe 0 nesse caso
    # (ver README, seção "Orientação retrato", e app/ui/rotation.py).
    screen_rotation: int = 0

    admin_pin: str = "1234"
    undo_confirm_seconds: float = 4.0

    database_path: str = "data/roulette.db"
    logs_dir: str = "logs"
    assets_dir: str = "assets"
    backups_dir: str = "data/backups"
    exports_dir: str = "data/exports"
    backup_retention_count: int = 30  # backups mais antigos que isso são apagados a cada novo backup

    # Logo do cliente: arquivo final fica em `branding_dir/logo.png`; o operador copia o arquivo
    # de origem (USB/SCP) em `branding_dir/incoming/` antes de "Importar logo" no admin. Campo de
    # config (não uma constante fixa em app/ui/admin.py) principalmente para poder isolar em
    # testes com um `tmp_path` — mesmo raciocínio de `database_path`/`backups_dir` acima.
    branding_dir: str = "data/branding"
    reports_dir: str = "data/reports"
    # Chave Ed25519 PRÓPRIA pra assinar relatórios — gerada localmente no primeiro relatório,
    # nunca a mesma da licença (aquela prova autorização do fornecedor; esta prova que o
    # relatório saiu deste equipamento e não foi editado depois — ver app/reports/signing.py).
    report_signing_key_path: str = "data/report_signing_key.pem"

    # Campos de identificação de relatório com valor padrão vazio (usa o nome do cassino/mesa se
    # não configurado) — ver installation_identity.report_title/report_subtitle.
    report_generate_pdf: bool = True
    report_generate_csv: bool = True
    report_generate_json: bool = True
    report_include_analytics: bool = True
    report_include_audit_summary: bool = True

    # SMTP: só o que não é segredo. A senha fica fora daqui de propósito — ver
    # app/delivery/smtp_credentials.py (arquivo separado, permissão 600, nunca no config.yaml
    # que pode circular mais casualmente por backup/suporte).
    smtp_credentials_path: str = "data/smtp_credentials.yaml"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_security: str = "STARTTLS"  # NONE | STARTTLS | SSL_TLS
    email_from: str = ""
    email_to: str = ""
    email_cc: str = ""
    # NUNCA | AO_ENCERRAR_SESSAO | RESUMO_DIARIO | AMBOS
    email_auto_send: str = "NUNCA"

    print_on_session_end: bool = False
    printer_type: str = "NONE"  # NONE | ESCPOS_USB | ESCPOS_NETWORK
    printer_address: str = ""  # caminho USB (ex.: /dev/usb/lp0) ou host:porta na rede

    license_path: str = "data/license.dat"
    license_state_path: str = "data/.license_state"

    log_level: str = "INFO"
    log_max_bytes: int = 2_000_000
    log_backup_count: int = 5

    def resolve(self, relative_path: str) -> Path:
        """Resolve a config path relative to the project root (unless already absolute)."""
        p = Path(relative_path)
        return p if p.is_absolute() else PROJECT_ROOT / p

    def to_dict(self) -> dict:
        return asdict(self)


def _coerce(defaults: Config, raw: dict) -> Config:
    """Merge raw YAML dict onto the defaults, ignoring unknown keys and keeping types sane."""
    values = asdict(defaults)
    for key, value in (raw or {}).items():
        if key in values and value is not None:
            values[key] = value
    return Config(**values)


def load_config(path: str | os.PathLike = DEFAULT_CONFIG_PATH) -> Config:
    defaults = Config()
    resolved = defaults.resolve(str(path))
    if not resolved.exists():
        # First boot / fresh install: write the defaults out so the admin has something to edit.
        save_config(defaults, path)
        return defaults
    with open(resolved, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    config = _coerce(defaults, raw)
    _validate(config)
    return config


def save_config(config: Config, path: str | os.PathLike = DEFAULT_CONFIG_PATH) -> None:
    resolved = config.resolve(str(path))
    resolved.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = resolved.with_suffix(resolved.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(config.to_dict(), fh, allow_unicode=True, sort_keys=False)
    tmp_path.replace(resolved)  # atomic on POSIX, avoids a half-written config after power loss


def _validate(config: Config) -> None:
    if config.history_size < 1:
        config.history_size = 1
    if config.statistics_window < 1:
        config.statistics_window = 1
    if config.hot_numbers_count < 1:
        config.hot_numbers_count = 1
    if config.cold_numbers_count < 1:
        config.cold_numbers_count = 1
    if config.backup_retention_count < 1:
        config.backup_retention_count = 1
    if config.target_fps < 1:
        config.target_fps = 30
    if config.idle_fps < 1:
        config.idle_fps = 5
    if not config.admin_pin or not config.admin_pin.isdigit():
        config.admin_pin = "1234"
