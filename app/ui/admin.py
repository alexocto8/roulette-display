"""PIN-gated admin overlay. Opened from the main display with CTRL+ALT+A.

A small explicit state machine (no generic form framework) — this screen has a fixed, known set
of fields and actions, so a state machine is simpler to read and verify than an abstraction that
would only ever be used once.
"""
from __future__ import annotations

import logging
import time

import pygame

from pathlib import Path

from app.config import Config, save_config
from app.reports import branding
from app.services.backup_service import BackupService
from app.services.export_service import ExportService
from app.services.retention_service import RetentionService
from app.services import power_service
from app.services.spin_service import SpinService
from app.ui.theme import BG, GOLD, PANEL_BG, PANEL_BORDER, RED, TEXT_MUTED, TEXT_PRIMARY, TEXT_SECONDARY, Theme
from app.version import __version__

logger = logging.getLogger("roulette.admin")

# NOTE: pygame's K_KP0..K_KP9 constants are *not* contiguous in ascending order (K_KP0 sorts
# after K_KP9), so a `K_KP0 <= key <= K_KP9` range check silently matches nothing. Use a dict.
_DIGIT_KEYS = {getattr(pygame, f"K_{i}"): str(i) for i in range(10)}
_DIGIT_KEYS.update({getattr(pygame, f"K_KP{i}"): str(i) for i in range(10)})

MAX_PIN_ATTEMPTS = 5
LOCKOUT_SECONDS = 30

# O menu é organizado em duas camadas: primeiro uma tela de categorias, depois os itens da
# categoria escolhida — 35 itens numa lista só (o tamanho original) tinha virado uma rolagem longa
# demais pra um overlay pequeno controlado só por teclado. As três categorias e o agrupamento
# seguem o pedido do usuário; a intenção de cada uma:
#   - PERSONALIZAÇÃO/IDENTIFICAÇÃO: como a mesa/venue se apresenta (nome, logo, código, moeda,
#     limites exibidos, quantidade de histórico) — tudo cosmético/identitário, nada operacional.
#   - FUNÇÕES ADMINISTRATIVAS: back-office — PIN, backups, exportação, auditoria, SMTP, rede,
#     licença, analytics, e os controles de energia (reiniciar/desligar o Pi), que também são
#     administração de infraestrutura, não do jogo em si.
#   - FUNÇÕES DO JOGO: ações que afetam a sessão/o placar ao vivo (ver total de giros, reiniciar
#     sessão, encerrar sessão).
CATEGORIES = [
    ("personalizacao", "Personalização / Identificação"),
    ("administrativas", "Funções administrativas"),
    ("jogo", "Funções do jogo"),
]

MENU_ITEMS_BY_CATEGORY: dict[str, list[tuple[str, str]]] = {
    "personalizacao": [
        ("casino_name", "Alterar nome do cassino (venue)"),
        ("roulette_name", "Alterar nome da mesa"),
        ("table_code", "Alterar código da mesa"),
        ("table_location", "Alterar local/setor"),
        ("device_name", "Alterar nome técnico do dispositivo"),
        ("table_id_info", "Ver ID permanente da mesa"),
        ("import_logo", "Importar logo (USB)"),
        ("remove_logo", "Remover logo"),
        ("history_size", "Alterar quantidade de histórico exibido"),
        ("statistics_window", "Alterar janela estatística"),
        ("currency", "Alterar moeda"),
        ("min_bet", "Alterar aposta mínima"),
        ("max_bet", "Alterar aposta máxima"),
        ("screen_rotation", "Girar tela (monitor paisagem montado em pé)"),
    ],
    "administrativas": [
        ("admin_pin", "Alterar PIN de administrador"),
        ("analytics_session", "Analytics (sessão atual)"),
        ("analytics_today", "Analytics (hoje)"),
        ("audit_log", "Ver auditoria (correções)"),
        ("audit_full", "Auditoria completa (filtros)"),
        ("audit_integrity", "Verificar integridade da auditoria"),
        ("export_csv", "Exportar resultados (CSV)"),
        ("backup", "Fazer backup do banco"),
        ("restore", "Importar backup"),
        ("run_retention_now", "Forçar limpeza de retenção agora"),
        ("smtp_host", "SMTP: servidor"),
        ("smtp_username", "SMTP: usuário"),
        ("smtp_password", "SMTP: senha"),
        ("email_to", "SMTP: destinatário"),
        ("smtp_test", "SMTP: testar envio"),
        ("network_status", "Ver status de rede"),
        ("license_info", "Informações da licença"),
        ("quit", "Reiniciar o painel (sair do programa)"),
        ("reboot", "Reiniciar Raspberry Pi"),
        ("shutdown", "Desligar Raspberry Pi"),
    ],
    "jogo": [
        ("total_spins", "Ver total de giros"),
        ("clear_session", "Reiniciar sessão atual (zera o painel)"),
        ("end_session", "Encerrar sessão (fecha e inicia nova)"),
    ],
}

# Linha extra da tela de categorias — não é uma categoria de verdade, fecha o painel direto
# (mesmo efeito do ESC nessa tela), pra não obrigar o operador a entrar numa categoria só pra sair.
_CLOSE_ENTRY = ("close", "Fechar administração")
_CATEGORY_ENTRIES = CATEGORIES + [_CLOSE_ENTRY]

# Lista achatada de todos os itens (todas as categorias, na ordem, + "Fechar administração") —
# mantida só para lookups por rótulo (log de auditoria, telas de edição/confirmação) que não
# precisam saber de categoria. Continua se chamando MENU_ITEMS porque testes existentes já
# importam esse nome.
MENU_ITEMS: list[tuple[str, str]] = [
    item for _, category_items in MENU_ITEMS_BY_CATEGORY.items() for item in category_items
] + [_CLOSE_ENTRY]

# Diretório onde o operador copia (USB/SCP) o arquivo de logo antes de "Importar logo" — sem um
# seletor de arquivos gráfico no pygame, a lista de arquivos encontrados aqui reaproveita
# exatamente o mesmo padrão de UI já usado por "Importar backup" (lista + ENTER escolhe).
_LOGO_EXTENSIONS = (".png", ".jpg", ".jpeg")

# Filtro de tipo de evento na tela "Auditoria completa" — None = todos.
_AUDIT_EVENT_TYPE_FILTERS = [
    None, "SPIN_CREATED", "SPIN_UNDONE", "SESSION_CLEARED", "SESSION_STARTED", "SESSION_CLOSED",
    "IDENTITY_CHANGED", "CONFIG_CHANGED", "ADMIN_LOGIN_SUCCESS", "ADMIN_LOGIN_FAILED",
    "LICENSE_VALIDATED", "LICENSE_FAILED", "REPORT_GENERATED", "EMAIL_SENT", "EMAIL_FAILED",
    "BACKUP_CREATED",
]
_AUDIT_PAGE_SIZE = 10


class AdminPanel:
    def __init__(self, config: Config, service: SpinService, backup_service: BackupService,
                 export_service: ExportService, retention_service: RetentionService,
                 config_path: str = "config.yaml"):
        self.config = config
        self.service = service
        self.backup_service = backup_service
        self.export_service = export_service
        self.retention_service = retention_service
        self.config_path = config_path
        # Cria uma vez, reaproveitada em todo `render()` (só o alpha muda por frame, via
        # `fill()`) — a resolução da tela não muda em runtime, então não há razão pra alocar um
        # Surface do tamanho da tela inteira (~8MB numa TV 1080x1920) a cada frame enquanto o
        # painel de administração estiver aberto. Item de performance identificado na auditoria.
        self._overlay: pygame.Surface | None = None
        self.reset()

    def reset(self) -> None:
        self.state = "pin"
        self.pin_buffer = ""
        self.attempts = 0
        self.locked_until = 0.0
        self.category: str | None = None
        self.category_index = 0
        self.menu_index = 0
        self.edit_buffer = ""
        self.message = ""
        self.pending_action: str | None = None
        self.backups: list = []
        self.backup_index = 0
        self.logo_files: list = []
        self.logo_index = 0
        self.audit_events: list = []
        self.audit_type_index = 0
        self.audit_page = 0
        self.quit_requested = False
        self.reboot_requested = False
        self.shutdown_requested = False

    def open(self) -> None:
        self.reset()

    def close(self) -> None:
        self.reset()

    # -- input -----------------------------------------------------------------

    def handle_key(self, event: pygame.event.Event) -> bool:
        """Returns True if the admin panel should stay open, False if it just closed."""
        key = event.key

        if self.state == "pin":
            if key == pygame.K_ESCAPE:
                # Sem isso o operador fica preso na tela de PIN (ex.: CTRL+ALT+A acionado sem
                # querer) até acertar a senha ou esgotar as tentativas — precisa dar pra cancelar.
                self.close()
                return False
            self._handle_pin(key)
            return True

        if key == pygame.K_ESCAPE:
            if self.state == "category":
                self.close()
                return False
            if self.state == "menu":
                # ESC na lista de itens volta pra tela de categorias, não fecha o painel — só a
                # tela de categorias (o topo da hierarquia) fecha.
                self.state = "category"
                self.edit_buffer = ""
                self.message = ""
                return True
            self.state = "menu"
            self.edit_buffer = ""
            self.message = ""
            return True

        if self.state == "category":
            self._handle_category(key)
            if self.state == "pin":
                # "Fechar administração" foi selecionado (via ENTER) — self.close() já resetou o
                # estado interno; sinaliza pro chamador que o overlay deve mesmo sumir, igual ao
                # ESC no topo (senão fica preso numa tela de PIN escondida atrás do overlay
                # escuro, sem nunca voltar pro placar).
                return False
        elif self.state == "menu":
            self._handle_menu(key)
        elif self.state in ("edit_text", "edit_number"):
            self._handle_edit(event)
        elif self.state == "confirm":
            self._handle_confirm(key)
        elif self.state == "restore_pick":
            self._handle_restore_pick(key)
        elif self.state == "logo_pick":
            self._handle_logo_pick(key)
        elif self.state == "audit_full":
            self._handle_audit_full(key)
        elif self.state == "message":
            if key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_ESCAPE):
                self.state = "menu"
                self.message = ""
        return True

    def _handle_pin(self, key: int) -> None:
        now = time.monotonic()
        if now < self.locked_until:
            return
        digit = _DIGIT_KEYS.get(key)

        if digit is not None and len(self.pin_buffer) < 8:
            self.pin_buffer += digit
            return
        if key == pygame.K_BACKSPACE:
            self.pin_buffer = self.pin_buffer[:-1]
            return
        if key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            if self.pin_buffer == self.config.admin_pin:
                self.state = "category"
                self.category_index = 0
            else:
                self.attempts += 1
                self.pin_buffer = ""
                if self.attempts >= MAX_PIN_ATTEMPTS:
                    self.locked_until = now + LOCKOUT_SECONDS
                    self.attempts = 0

    def _current_items(self) -> list[tuple[str, str]]:
        return MENU_ITEMS_BY_CATEGORY[self.category]

    def _handle_category(self, key: int) -> None:
        if key in (pygame.K_UP, pygame.K_w):
            self.category_index = (self.category_index - 1) % len(_CATEGORY_ENTRIES)
        elif key in (pygame.K_DOWN, pygame.K_s):
            self.category_index = (self.category_index + 1) % len(_CATEGORY_ENTRIES)
        elif key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            entry_key, _ = _CATEGORY_ENTRIES[self.category_index]
            if entry_key == "close":
                self._activate("close")
            else:
                self.category = entry_key
                self.menu_index = 0
                self.state = "menu"

    def _handle_menu(self, key: int) -> None:
        items = self._current_items()
        if key in (pygame.K_UP, pygame.K_w):
            self.menu_index = (self.menu_index - 1) % len(items)
        elif key in (pygame.K_DOWN, pygame.K_s):
            self.menu_index = (self.menu_index + 1) % len(items)
        elif key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            self._activate(items[self.menu_index][0])

    def _activate(self, action: str) -> None:
        # Log de auditoria das ações administrativas — não existia nenhum registro além do log de
        # exceção genérico; toda ação que o operador escolhe no menu fica no log rotativo agora.
        logger.info("ADMIN AÇÃO: %s", dict(MENU_ITEMS).get(action, action))

        if action == "casino_name":
            self.state, self.edit_buffer, self.pending_action = "edit_text", self.config.casino_name, action
        elif action == "roulette_name":
            self.state, self.edit_buffer, self.pending_action = "edit_text", self.config.roulette_name, action
        elif action in ("table_code", "table_location", "device_name"):
            identity = self.service.identity.get()
            self.state = "edit_text"
            self.edit_buffer = identity[action] or ""
            self.pending_action = action
        elif action == "table_id_info":
            identity = self.service.identity.get()
            self.message = (
                f"ID permanente da mesa:\n{identity['table_id']}\n\n"
                "Este identificador nunca muda, mesmo renomeando a mesa — é o que liga o "
                "histórico de sessões/relatórios entre si."
            )
            self.state = "message"
        elif action == "import_logo":
            self._open_logo_picker()
        elif action == "remove_logo":
            branding.remove(self.config.resolve(self.config.branding_dir))
            self.service.identity.update(venue_logo_path="")
            self.message = "Logo removido."
            self.state = "message"
        elif action == "screen_rotation":
            # Alterna entre as 4 posições a cada ENTER, igual "Remover logo" acima -- não é um
            # campo de texto/número, é um valor pequeno e fechado (ver app/ui/rotation.py). Só tem
            # efeito na PRÓXIMA vez que a tela pygame for aberta (pygame.display.set_mode() só
            # roda uma vez, no início) -- por isso a mensagem pede pra reiniciar o painel.
            order = (0, 90, 180, 270)
            current = self.config.screen_rotation if self.config.screen_rotation in order else 0
            self.config.screen_rotation = order[(order.index(current) + 1) % len(order)]
            save_config(self.config, self.config_path)
            self.message = (
                f"Rotação da tela: {self.config.screen_rotation}°\n\n"
                "Reinicie o painel (\"Reiniciar o painel\" no menu) para aplicar."
            )
            self.state = "message"
        elif action == "audit_full":
            self.audit_type_index = 0
            self.audit_page = 0
            self._reload_audit_full()
            self.state = "audit_full"
        elif action == "audit_integrity":
            ok, broken = self.service.audit.verify_integrity()
            self.message = "Integridade da auditoria: VALID" if ok else (
                f"Integridade da auditoria: BROKEN\nPrimeiro evento inconsistente:\n{broken}"
            )
            self.state = "message"
        elif action == "end_session":
            self.pending_action = action
            self.state = "confirm"
        elif action == "history_size":
            self.state, self.edit_buffer, self.pending_action = "edit_number", str(self.config.history_size), action
        elif action == "statistics_window":
            self.state = "edit_number"
            self.edit_buffer = str(self.config.statistics_window)
            self.pending_action = action
        elif action == "currency":
            self.state, self.edit_buffer, self.pending_action = "edit_text", self.config.currency, action
        elif action == "min_bet":
            self.state, self.edit_buffer, self.pending_action = "edit_text", self.config.min_bet, action
        elif action == "max_bet":
            self.state, self.edit_buffer, self.pending_action = "edit_text", self.config.max_bet, action
        elif action == "admin_pin":
            self.state, self.edit_buffer, self.pending_action = "edit_number", "", action
        elif action == "total_spins":
            total = self.service.db.total_spins(self.config.roulette_id)
            self.message = f"Total de giros registrados: {total}"
            self.state = "message"
        elif action == "analytics_session":
            self.message = self._analytics_text("SESSAO_ATUAL")
            self.state = "message"
        elif action == "analytics_today":
            self.message = self._analytics_text("HOJE")
            self.state = "message"
        elif action == "audit_log":
            entries = self.service.get_audit_log(limit=10)
            if not entries:
                self.message = "Nenhuma correção registrada ainda."
            else:
                lines = [
                    f"{e['at']}  #{e['original_number']:>2}  {e['reason']}  ({e['operator']})"
                    for e in entries
                ]
                self.message = "Últimas correções:\n" + "\n".join(lines)
            self.state = "message"
        elif action == "export_csv":
            try:
                path = self.export_service.export_csv()
                self.message = f"Exportado com sucesso:\n{path}"
            except Exception as exc:
                logger.exception("CSV export failed")
                self.message = f"Falha ao exportar: {exc}"
            self.state = "message"
        elif action == "backup":
            try:
                path = self.backup_service.create_backup()
                self.message = f"Backup criado:\n{path}"
            except Exception as exc:
                logger.exception("Backup failed")
                self.message = f"Falha no backup: {exc}"
            self.state = "message"
        elif action == "restore":
            self.backups = self.backup_service.list_backups()
            if not self.backups:
                self.message = "Nenhum backup encontrado."
                self.state = "message"
            else:
                self.backup_index = 0
                self.state = "restore_pick"
        elif action in ("smtp_host", "smtp_username", "email_to"):
            self.state, self.edit_buffer, self.pending_action = "edit_text", getattr(self.config, action), action
        elif action == "smtp_password":
            self.state, self.edit_buffer, self.pending_action = "edit_text", "", action
        elif action == "smtp_test":
            self.message = self._smtp_test_text()
            self.state = "message"
        elif action == "run_retention_now":
            self.message = self._run_retention_text()
            self.state = "message"
        elif action == "network_status":
            self.message = self._network_status_text()
            self.state = "message"
        elif action == "license_info":
            self.message = self._license_info_text()
            self.state = "message"
        elif action in ("clear_session", "quit", "reboot", "shutdown"):
            self.pending_action = action
            self.state = "confirm"
        elif action == "close":
            self.close()

    def _print_session_receipt(self, session_row) -> None:
        """Best-effort — uma impressora desligada/desconectada nunca pode interromper o fluxo de
        encerrar sessão, então qualquer falha aqui só é logada, nunca propagada."""
        try:
            from app.analytics.analytics_service import AnalyticsService
            from app.analytics import periods as period_names
            from app.delivery import printer_service

            spins = self.service.db.get_report_spins_for_session(session_row["id"])
            snapshot = AnalyticsService(self.service.db).build_snapshot_from_spins(
                period_names.SESSAO_ATUAL, spins, session_id=session_row["id"], hot_n=3, cold_n=3,
            )
            text = printer_service.build_receipt_text(self.service.identity.get(), session_row, snapshot)
            printer_service.print_receipt(self.config, text)
        except Exception:
            logger.exception("Falha ao preparar recibo de impressão")

    def _smtp_test_text(self) -> str:
        from app.delivery import email_service

        try:
            msg = email_service.build_message(
                self.config,
                {
                    "venue_name": self.config.casino_name, "table_name": self.config.roulette_name,
                    "table_code": "", "session_code": "TESTE", "started_at": "", "ended_at": "",
                },
                {},
            )
            msg.set_content("Este é um e-mail de teste do painel de roleta. Se você recebeu isto, o SMTP está configurado corretamente.")
            email_service.send_message(self.config, msg)
            return "E-mail de teste enviado com sucesso."
        except email_service.SmtpNotConfiguredError as exc:
            return f"SMTP não configurado: {exc}"
        except Exception as exc:
            logger.exception("Teste de envio SMTP falhou")
            return f"Falha no envio de teste: {exc}"

    def _run_retention_text(self) -> str:
        """Dispara a retenção na hora (o painel também faz isso sozinho a cada algumas horas, ver
        `RouletteDisplay._enforce_retention`) -- útil pra confirmar que a política está ativa sem
        esperar o ciclo automático."""
        try:
            purged = self.retention_service.enforce_retention()
        except Exception as exc:
            logger.exception("Limpeza de retenção manual falhou")
            return f"Falha na limpeza de retenção: {exc}"
        if purged == 0:
            return f"Nenhum giro com mais de {self.config.data_retention_days} dias — nada para arquivar."
        return (
            f"{purged} giro(s) com mais de {self.config.data_retention_days} dias arquivados "
            f"em '{self.config.exports_dir}' e removidos do banco."
        )

    def _network_status_text(self) -> str:
        from app.delivery.network_status import get_network_status

        status = get_network_status()
        return (
            f"Ethernet: {status['ethernet']}\n"
            f"IP: {status['ip']}\n"
            f"Wi-Fi: {status['wifi_ssid']}\n"
            f"Sinal: {status['wifi_signal']}\n"
            f"Internet: {status['internet']}"
        )

    def _analytics_text(self, period: str) -> str:
        """Resumo textual — não uma tela cheia de gráficos. O painel admin é um overlay pequeno,
        controlado só por teclado; um dashboard visual de verdade é o relatório PDF (ver
        app/reports/), que tem o espaço e as ferramentas de layout certos para isso."""
        from app.analytics import periods as period_names
        from app.analytics.analytics_service import AnalyticsService

        analytics = AnalyticsService(self.service.db)
        if period == "SESSAO_ATUAL":
            session = self.service.session_service.ensure_open_session()
            snapshot = analytics.compute(self.config.roulette_id, period_names.SESSAO_ATUAL, session_id=session["id"])
        else:
            snapshot = analytics.compute(self.config.roulette_id, period)

        if snapshot.total_spins == 0:
            return f"{snapshot.period_label}: nenhum giro registrado ainda."

        lines = [
            snapshot.period_label,
            f"Giros: {snapshot.total_spins}",
            f"Giros/hora: {snapshot.spins_per_hour}",
            f"Correções: {snapshot.undo_count} ({snapshot.correction_rate}%)",
            "",
            f"Vermelho {snapshot.color.percentage('red')}%  "
            f"Preto {snapshot.color.percentage('black')}%  "
            f"Zero {snapshot.color.percentage('green')}%",
            "",
            "QUENTE: " + ", ".join(f"{n} ({c}x)" for n, c in snapshot.hot[:3]),
            "FRIO: " + ", ".join(f"{n} ({c} giros)" for n, c in snapshot.cold[:3]),
        ]
        if snapshot.chi_square.get("applicable"):
            lines += ["", snapshot.chi_square["verdict"]]
        return "\n".join(lines)

    def _license_info_text(self) -> str:
        from app.license import hardware
        from app.license.public_key import public_key_bytes
        from app.license.verify import LicenseStatus, verify_license

        device = hardware.device_id()
        result = verify_license(
            self.config.resolve(self.config.license_path),
            public_key_bytes(),
            state_path=self.config.resolve(self.config.license_state_path),
        )
        lines = [f"Versão do sistema: {__version__}", f"Device ID: {device}"]
        status_labels = {
            LicenseStatus.VALID: "Ativa",
            LicenseStatus.MISSING: "Não instalada",
            LicenseStatus.CORRUPT: "Arquivo corrompido",
            LicenseStatus.INVALID_SIGNATURE: "Assinatura inválida",
            LicenseStatus.DEVICE_MISMATCH: "Emitida para outro equipamento",
            LicenseStatus.EXPIRED: "Expirada",
        }
        lines.append(f"Status: {status_labels.get(result.status, result.status.value)}")
        if result.payload:
            p = result.payload
            lines.append(f"Cliente: {p.get('customer', '—')}")
            lines.append(f"Validade: {p.get('expires_at') or 'PERPÉTUA'}")
        return "\n".join(lines)

    def _handle_edit(self, event: pygame.event.Event) -> None:
        key = event.key
        if key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            self._save_edit()
        elif key == pygame.K_BACKSPACE:
            self.edit_buffer = self.edit_buffer[:-1]
        elif self.state == "edit_number":
            digit = _DIGIT_KEYS.get(key)
            # 8 dígitos cobre history_size/statistics_window (poucos dígitos) e admin_pin (até 8,
            # mesmo teto aceito na tela de PIN) sem precisar de um terceiro estado só pra isso.
            if digit is not None and len(self.edit_buffer) < 8:
                self.edit_buffer += digit
        elif self.state == "edit_text" and event.unicode and event.unicode.isprintable():
            if len(self.edit_buffer) < 40:
                self.edit_buffer += event.unicode

    def _save_edit(self) -> None:
        action = self.pending_action
        if action == "casino_name":
            value = self.edit_buffer.strip() or self.config.casino_name
            self.config.casino_name = value
            # installation_identity é a fonte da verdade para "nome do cassino/mesa" desde a
            # auditoria de identidade — ver SpinService._sync_identity_and_config. Escrever aqui
            # também mantém o admin PIN-gated como o único caminho de edição, igual antes.
            self.service.identity.update(venue_name=value, actor_id="admin")
        elif action == "roulette_name":
            value = self.edit_buffer.strip() or self.config.roulette_name
            self.config.roulette_name = value
            self.service.db.ensure_roulette(self.config.roulette_id, value)
            self.service.identity.update(table_name=value, actor_id="admin")
        elif action in ("table_code", "table_location", "device_name"):
            self.service.identity.update(**{action: self.edit_buffer.strip()}, actor_id="admin")
        elif action in ("smtp_host", "smtp_username", "email_to"):
            setattr(self.config, action, self.edit_buffer.strip())
        elif action == "smtp_password":
            from app.delivery import smtp_credentials

            smtp_credentials.set_smtp_password(
                self.config.resolve(self.config.smtp_credentials_path), self.edit_buffer
            )
            self.service.audit.log(
                "CONFIG_CHANGED", actor_type="admin", sensitive_field="smtp_password",
                old_value="senha anterior", new_value="senha nova", reason="smtp_password alterado",
            )
        elif action == "history_size":
            try:
                self.config.history_size = max(1, min(50, int(self.edit_buffer)))
            except ValueError:
                pass
        elif action == "statistics_window":
            try:
                self.config.statistics_window = max(1, min(10000, int(self.edit_buffer)))
            except ValueError:
                pass
        elif action == "currency":
            self.config.currency = self.edit_buffer.strip() or self.config.currency
        elif action == "min_bet":
            self.config.min_bet = self.edit_buffer.strip() or self.config.min_bet
        elif action == "max_bet":
            self.config.max_bet = self.edit_buffer.strip() or self.config.max_bet
        elif action == "admin_pin":
            if self.edit_buffer.isdigit() and 4 <= len(self.edit_buffer) <= 8:
                self.config.admin_pin = self.edit_buffer
            else:
                self.message = "PIN inválido — precisa ter de 4 a 8 dígitos. Mantido o anterior."
                self.state = "message"
                self.edit_buffer = ""
                return
        save_config(self.config, self.config_path)
        self.state = "menu"
        self.edit_buffer = ""

    def _handle_confirm(self, key: int) -> None:
        if key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            action = self.pending_action
            if action == "clear_session":
                n = self.service.clear_session(operator="admin")
                self.message = f"Sessão reiniciada — painel zerado ({n} giros arquivados)."
                self.state = "message"
            elif action == "end_session":
                result = self.service.end_session(operator="admin")
                closed = result["closed"]
                opened = result["opened"]
                self.message = (
                    f"Sessão encerrada: {closed['session_code']}\n"
                    f"({result['cleared_spins']} giros zerados do painel)\n\n"
                    f"Nova sessão iniciada: {opened['session_code']}"
                )
                # Geração do relatório é deliberadamente DEPOIS de já ter fechado a sessão e
                # zerado o painel acima — se o PDF/CSV/JSON falhar por qualquer motivo (logo
                # corrompido, disco cheio), a sessão já está encerrada e o operador já pode
                # seguir pro próximo turno; o relatório pode ser regerado depois via os arquivos
                # já persistidos (session.json é a fonte canônica). Nunca trava aqui.
                try:
                    from app.reports import report_service

                    written = report_service.generate_report(
                        self.service.db, self.config, closed, roulette_id=self.config.roulette_id,
                    )
                    formats = ", ".join(k.upper() for k in ("pdf", "csv", "json") if k in written)
                    self.message += f"\n\nRelatório gerado ({formats}):\n{written['directory']}"

                    if self.config.email_auto_send in ("AO_ENCERRAR_SESSAO", "AMBOS"):
                        from app.delivery import delivery_queue

                        delivery_queue.enqueue_report(self.service.db, written["directory"], session_id=closed["id"])
                        self.message += "\n\nE-mail enfileirado para envio."

                    if self.config.print_on_session_end:
                        self._print_session_receipt(closed)
                except Exception:
                    logger.exception("Falha ao gerar relatório da sessão encerrada")
                    self.message += "\n\nAVISO: falha ao gerar o relatório — veja o log técnico."
                self.state = "message"
            elif action == "quit":
                self.quit_requested = True
            elif action == "reboot":
                self.reboot_requested = True
            elif action == "shutdown":
                self.shutdown_requested = True
        elif key == pygame.K_ESCAPE:
            self.state = "menu"

    def _handle_restore_pick(self, key: int) -> None:
        if key in (pygame.K_UP, pygame.K_w):
            self.backup_index = (self.backup_index - 1) % len(self.backups)
        elif key in (pygame.K_DOWN, pygame.K_s):
            self.backup_index = (self.backup_index + 1) % len(self.backups)
        elif key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            chosen = self.backups[self.backup_index]
            # db.close() below is unconditional (needed before overwriting the live file), so the
            # running process can no longer use its connection either way. Force a restart via
            # systemd (Restart=always) instead of leaving the app running with a closed
            # connection, which would otherwise crash on the next spin registration.
            try:
                self.service.db.close()
                self.backup_service.restore_backup(chosen)
                self.message = f"Backup restaurado de:\n{chosen}\nReiniciando o painel..."
            except Exception as exc:
                logger.exception("Restore failed")
                self.message = f"Falha ao restaurar: {exc}\nReiniciando o painel..."
            self.state = "message"
            self.quit_requested = True

    # -- logo -------------------------------------------------------------------

    def _open_logo_picker(self) -> None:
        staging_dir = self.config.resolve(self.config.branding_dir) / "incoming"
        self.logo_files = sorted(
            p for p in staging_dir.glob("*") if p.suffix.lower() in _LOGO_EXTENSIONS
        ) if staging_dir.exists() else []
        if not self.logo_files:
            self.message = (
                f"Nenhuma imagem encontrada em:\n{staging_dir}\n\n"
                "Copie o arquivo (PNG/JPG) para essa pasta via USB e tente de novo."
            )
            self.state = "message"
        else:
            self.logo_index = 0
            self.state = "logo_pick"

    def _handle_logo_pick(self, key: int) -> None:
        if key in (pygame.K_UP, pygame.K_w):
            self.logo_index = (self.logo_index - 1) % len(self.logo_files)
        elif key in (pygame.K_DOWN, pygame.K_s):
            self.logo_index = (self.logo_index + 1) % len(self.logo_files)
        elif key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            chosen = self.logo_files[self.logo_index]
            try:
                dest = branding.validate_and_store(chosen, self.config.resolve(self.config.branding_dir))
                self.service.identity.update(venue_logo_path=str(dest), actor_id="admin")
                self.message = f"Logo importado com sucesso:\n{dest.name}"
            except branding.InvalidLogoError as exc:
                self.message = f"Não foi possível importar o logo:\n{exc}"
            self.state = "message"
        elif key == pygame.K_ESCAPE:
            self.state = "menu"

    # -- auditoria completa -------------------------------------------------------

    def _reload_audit_full(self) -> None:
        event_type = _AUDIT_EVENT_TYPE_FILTERS[self.audit_type_index]
        self.audit_events = self.service.audit.get_events(
            event_type=event_type, limit=_AUDIT_PAGE_SIZE, offset=self.audit_page * _AUDIT_PAGE_SIZE,
        )

    def _handle_audit_full(self, key: int) -> None:
        if key in (pygame.K_LEFT, pygame.K_a):
            self.audit_type_index = (self.audit_type_index - 1) % len(_AUDIT_EVENT_TYPE_FILTERS)
            self.audit_page = 0
            self._reload_audit_full()
        elif key in (pygame.K_RIGHT, pygame.K_d):
            self.audit_type_index = (self.audit_type_index + 1) % len(_AUDIT_EVENT_TYPE_FILTERS)
            self.audit_page = 0
            self._reload_audit_full()
        elif key in (pygame.K_DOWN, pygame.K_s, pygame.K_PAGEDOWN):
            if len(self.audit_events) == _AUDIT_PAGE_SIZE:  # só avança se a página atual está cheia
                self.audit_page += 1
                self._reload_audit_full()
        elif key in (pygame.K_UP, pygame.K_w, pygame.K_PAGEUP):
            if self.audit_page > 0:
                self.audit_page -= 1
                self._reload_audit_full()
        elif key == pygame.K_ESCAPE:
            self.state = "menu"

    # -- rendering ---------------------------------------------------------------

    def render(self, screen: pygame.Surface, theme: Theme, reveal: float = 1.0) -> None:
        """`reveal` goes 0 -> 1 while the panel is opening (or 1 -> 0 while closing) — drives a
        quick fade + slight scale-in so the overlay doesn't just snap on/off screen."""
        reveal = max(0.0, min(1.0, reveal))

        if self._overlay is None or self._overlay.get_size() != (theme.width, theme.height):
            self._overlay = pygame.Surface((theme.width, theme.height), pygame.SRCALPHA)
        self._overlay.fill((0, 0, 0, int(210 * reveal)))
        screen.blit(self._overlay, (0, 0))

        if reveal <= 0.01:
            return

        panel_w = int(theme.width * 0.7)
        panel_h = int(theme.height * 0.75)
        scale = 0.94 + 0.06 * reveal
        panel = pygame.Rect(0, 0, int(panel_w * scale), int(panel_h * scale))
        panel.center = (theme.width // 2, theme.height // 2)
        panel_alpha = int(255 * reveal)

        panel_surf = pygame.Surface(panel.size, pygame.SRCALPHA)
        pygame.draw.rect(panel_surf, PANEL_BG, panel_surf.get_rect(), border_radius=theme.px(10))
        pygame.draw.rect(panel_surf, GOLD, panel_surf.get_rect(), width=2, border_radius=theme.px(10))
        panel_surf.set_alpha(panel_alpha)
        screen.blit(panel_surf, panel.topleft)

        title_font = theme.font(30, bold=True)
        title = title_font.render("ADMINISTRAÇÃO", True, GOLD)
        screen.blit(title, title.get_rect(midtop=(panel.centerx, panel.top + theme.px(16))))

        content_top = panel.top + theme.px(70)

        if self.state == "pin":
            self._render_pin(screen, theme, panel, content_top)
        elif self.state == "category":
            self._render_category(screen, theme, panel, content_top)
        elif self.state == "menu":
            self._render_menu(screen, theme, panel, content_top)
        elif self.state in ("edit_text", "edit_number"):
            self._render_edit(screen, theme, panel, content_top)
        elif self.state == "confirm":
            self._render_confirm(screen, theme, panel, content_top)
        elif self.state == "restore_pick":
            self._render_restore_pick(screen, theme, panel, content_top)
        elif self.state == "logo_pick":
            self._render_logo_pick(screen, theme, panel, content_top)
        elif self.state == "audit_full":
            self._render_audit_full(screen, theme, panel, content_top)
        elif self.state == "message":
            self._render_message(screen, theme, panel, content_top)

    def _render_pin(self, screen, theme, panel, top) -> None:
        font = theme.font(24)
        now = time.monotonic()
        if now < self.locked_until:
            remaining = int(self.locked_until - now) + 1
            text = font.render(f"Bloqueado. Tente novamente em {remaining}s.", True, RED)
        else:
            masked = "*" * len(self.pin_buffer)
            text = font.render(f"Digite o PIN: {masked}", True, TEXT_PRIMARY)
        screen.blit(text, text.get_rect(center=(panel.centerx, top + theme.px(40))))

    def _render_category(self, screen, theme, panel, top) -> None:
        font = theme.font(22)
        row_h = theme.px(38)
        for i, (key, label) in enumerate(_CATEGORY_ENTRIES):
            y = top + i * row_h
            color = GOLD if i == self.category_index else TEXT_SECONDARY
            prefix = "> " if i == self.category_index else "  "
            text = font.render(prefix + label, True, color)
            screen.blit(text, (panel.left + theme.px(30), y))
        hint = theme.font(16).render("ENTER entra  •  ESC fecha", True, TEXT_MUTED)
        screen.blit(hint, (panel.left + theme.px(30), panel.bottom - theme.px(40)))

    def _render_menu(self, screen, theme, panel, top) -> None:
        category_label = dict(CATEGORIES).get(self.category, "")
        header_font = theme.font(18, bold=True)
        header = header_font.render(category_label, True, GOLD)
        screen.blit(header, (panel.left + theme.px(30), top))

        font = theme.font(20)
        row_h = theme.px(30)
        list_top = top + theme.px(32)
        for i, (key, label) in enumerate(self._current_items()):
            y = list_top + i * row_h
            color = GOLD if i == self.menu_index else TEXT_SECONDARY
            prefix = "> " if i == self.menu_index else "  "
            text = font.render(prefix + label, True, color)
            screen.blit(text, (panel.left + theme.px(30), y))
        hint = theme.font(16).render("ENTER escolhe  •  ESC volta às categorias", True, TEXT_MUTED)
        screen.blit(hint, (panel.left + theme.px(30), panel.bottom - theme.px(40)))

    def _render_edit(self, screen, theme, panel, top) -> None:
        label = dict(MENU_ITEMS).get(self.pending_action, "")
        font = theme.font(22)
        label_surf = font.render(label, True, TEXT_SECONDARY)
        screen.blit(label_surf, label_surf.get_rect(midtop=(panel.centerx, top)))
        value_font = theme.font(32, bold=True)
        value_surf = value_font.render(self.edit_buffer + "_", True, TEXT_PRIMARY)
        screen.blit(value_surf, value_surf.get_rect(midtop=(panel.centerx, top + theme.px(50))))
        hint = theme.font(16).render("ENTER confirma  •  ESC cancela", True, TEXT_MUTED)
        screen.blit(hint, hint.get_rect(midtop=(panel.centerx, top + theme.px(110))))

    def _render_confirm(self, screen, theme, panel, top) -> None:
        labels = {
            "clear_session": "Reiniciar a sessão? O painel zera, mas o histórico fica arquivado (auditoria/exportação).",
            "end_session": "Encerrar a sessão atual e iniciar uma nova? Isso zera o painel e fecha o período do relatório.",
            "quit": "Reiniciar o painel? O serviço volta a subir sozinho em poucos segundos.",
            "reboot": "Reiniciar o Raspberry Pi?",
            "shutdown": "Desligar o Raspberry Pi?",
        }
        font = theme.font(22)
        text = font.render(labels.get(self.pending_action, ""), True, TEXT_PRIMARY)
        screen.blit(text, text.get_rect(center=(panel.centerx, top + theme.px(40))))
        hint = theme.font(16).render("ENTER confirma  •  ESC cancela", True, TEXT_MUTED)
        screen.blit(hint, hint.get_rect(midtop=(panel.centerx, top + theme.px(90))))

    def _render_restore_pick(self, screen, theme, panel, top) -> None:
        font = theme.font(18)
        for i, path in enumerate(self.backups):
            y = top + i * theme.px(28)
            color = GOLD if i == self.backup_index else TEXT_SECONDARY
            prefix = "> " if i == self.backup_index else "  "
            text = font.render(prefix + path.name, True, color)
            screen.blit(text, (panel.left + theme.px(30), y))
        hint = theme.font(16).render("ENTER restaura  •  ESC cancela", True, TEXT_MUTED)
        screen.blit(hint, (panel.left + theme.px(30), panel.bottom - theme.px(40)))

    def _render_logo_pick(self, screen, theme, panel, top) -> None:
        font = theme.font(18)
        for i, path in enumerate(self.logo_files):
            y = top + i * theme.px(28)
            color = GOLD if i == self.logo_index else TEXT_SECONDARY
            prefix = "> " if i == self.logo_index else "  "
            text = font.render(prefix + path.name, True, color)
            screen.blit(text, (panel.left + theme.px(30), y))
        hint = theme.font(16).render("ENTER importa  •  ESC cancela", True, TEXT_MUTED)
        screen.blit(hint, (panel.left + theme.px(30), panel.bottom - theme.px(40)))

    def _render_audit_full(self, screen, theme, panel, top) -> None:
        event_type = _AUDIT_EVENT_TYPE_FILTERS[self.audit_type_index]
        filter_font = theme.font(18, bold=True)
        # Sem setas Unicode (←/→/↑/↓): a fonte padrão do pygame não tem esses glifos (mesmo
        # problema já documentado para os ícones de naipe e o "●" de status — ver display.py).
        filter_text = filter_font.render(
            f"Filtro: {event_type or 'TODOS'}   (A/D muda, página {self.audit_page + 1})",
            True, GOLD,
        )
        screen.blit(filter_text, (panel.left + theme.px(30), top))

        font = theme.font(15)
        row_h = theme.px(24)
        list_top = top + theme.px(36)
        if not self.audit_events:
            empty = font.render("Nenhum evento nesta página/filtro.", True, TEXT_MUTED)
            screen.blit(empty, (panel.left + theme.px(30), list_top))
        for i, e in enumerate(self.audit_events):
            y = list_top + i * row_h
            summary = f"{e['created_at']}  {e['event_type']}"
            detail_bits = []
            if e["old_value"] or e["new_value"]:
                detail_bits.append(f"{e['old_value'] or '—'} -> {e['new_value'] or '—'}")
            if e["actor_id"]:
                detail_bits.append(f"por {e['actor_id']}")
            if detail_bits:
                summary += "  (" + ", ".join(detail_bits) + ")"
            text = font.render(summary, True, TEXT_SECONDARY)
            screen.blit(text, (panel.left + theme.px(30), y))
        hint = theme.font(16).render(
            "A/D filtro  •  W/S página  •  ESC volta", True, TEXT_MUTED
        )
        screen.blit(hint, (panel.left + theme.px(30), panel.bottom - theme.px(40)))

    def _render_message(self, screen, theme, panel, top) -> None:
        font = theme.font(20)
        for i, line in enumerate(self.message.split("\n")):
            text = font.render(line, True, TEXT_PRIMARY)
            screen.blit(text, text.get_rect(center=(panel.centerx, top + theme.px(30) + i * theme.px(30))))
        hint = theme.font(16).render("ENTER para voltar", True, TEXT_MUTED)
        screen.blit(hint, hint.get_rect(midtop=(panel.centerx, panel.bottom - theme.px(40))))
