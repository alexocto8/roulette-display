"""Main fullscreen display: the electronic roulette scoreboard itself.

Layout follows the client's exact visual-contract reference (a precise annotated mockup, not
just a photo): a full-height three-column structure — FRIO (cold numbers, blue trapezoid badges)
on the left, the current result centered, QUENTE (hot numbers, orange trapezoid badges) with the
casino logo underneath on the right — with bet limits across the very top and a seven-cell
percentage bar (ÍMPAR/PAR/VERMELHO/ZERO/PRETO/MENOR/MAIOR, with hand-drawn card-suit icons — the
bundled font has no suit glyphs) across the very bottom. Colors are the exact hex values the
client specified. Flat colors, no gradients — this is meant to read as a piece of casino signage
hardware, not a web dashboard. Cards/badges do get a subtle drop shadow and result changes get a
brief soft glow (explicit client request for more depth/sophistication) — kept restrained: no
periodic idle animation, nothing that would pull attention away from the number that matters.

Rendering stays simple (flat rects/polygons, text, a couple of short eased tweens — no shaders, no
video) and the frame rate drops to `config.idle_fps` whenever nothing is actively transitioning or
being typed, which is what keeps CPU usage low on a Raspberry Pi 3 across the long idle stretches
between spins. See app/ui/animation.py for the tween/easing helpers used below.
"""
from __future__ import annotations

import logging
import math

import pygame

from app.config import Config
from app.database.db import Database
from app.models import roulette_data
from app.services.backup_service import BackupService
from app.services.export_service import ExportService
from app.services.spin_service import DisplayState, SpinService
from app.ui import sound
from app.ui.admin import AdminPanel
from app.ui.animation import Tween, ease_out_back, ease_out_cubic
from app.ui.assets import load_image
from app.ui.rotation import create_screen
from app.ui.splash import show_splash
from app.ui.theme import (
    BG,
    BLACK,
    COLOR_MAP,
    CYAN,
    GOLD,
    GREEN,
    NUMBER_COLOR_MAP,
    ORANGE,
    PANEL_BG,
    PANEL_BORDER,
    RED,
    SILVER,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    Theme,
)
from app.services import power_service, watchdog_service

logger = logging.getLogger("roulette.ui")

_DIGIT_KEYS = {getattr(pygame, f"K_{i}"): str(i) for i in range(10)}
_DIGIT_KEYS.update({getattr(pygame, f"K_KP{i}"): str(i) for i in range(10)})
_ENTER_KEYS = {pygame.K_RETURN, pygame.K_KP_ENTER}
_MINUS_KEYS = {pygame.K_MINUS, pygame.K_KP_MINUS}
_PLUS_KEYS = {pygame.K_PLUS, pygame.K_KP_PLUS, pygame.K_EQUALS}
_CANCEL_KEYS = {pygame.K_ESCAPE, pygame.K_PERIOD, pygame.K_KP_PERIOD}

# Teclas puramente modificadoras (sem significado próprio de "outra tecla foi digitada") — um
# numpad USB físico também tem uma tecla NumLock, e um toque acidental nela não deve abortar um
# comando "-" em andamento.
_MODIFIER_KEYS = {
    pygame.K_NUMLOCKCLEAR, pygame.K_CAPSLOCK, pygame.K_SCROLLLOCK,
    pygame.K_LSHIFT, pygame.K_RSHIFT, pygame.K_LCTRL, pygame.K_RCTRL,
    pygame.K_LALT, pygame.K_RALT, pygame.K_LGUI, pygame.K_RGUI, pygame.K_MODE,
}

# Sequência de teclas do console de referência do cliente: "-" "9" "7" ENTER. O gesto físico é
# preservado exatamente — o que muda é só a implementação por trás dele: aqui isso reinicia a
# SESSÃO exibida no painel (zera o placar), não apaga fisicamente nada do banco (soft delete, ver
# Database.clear_session). Para um equipamento de cassino, destruir evidência de forma irreversível
# por um gesto de 4 teclas seria um risco de auditoria desnecessário.
_CLEAR_ALL_CODE = "97"

_FLASH_MS = 2200
_REGISTERED_FLASH_MS = 650  # dentro da faixa pedida (500-800ms): rápido, não bloqueia o próximo giro
_NUMBER_POP_MS = 380  # "pop" sutil no número central ao trocar — no lugar, não tela cheia
_ADMIN_FADE_MS = 200
_BANNER_ANIM_MS = 200
_SPLASH_CROSSFADE_MS = 420

# Animação de revelação em tela cheia, disparada a cada giro registrado com sucesso. Pedido
# explícito: enquanto ela está em tela, o sistema NÃO deve permitir registrar um novo número —
# `_confirm_input` bloqueia o ENTER de confirmação nesses 5s (mantendo o que já foi digitado, o
# operador só precisa apertar ENTER de novo depois). Diferente do que essa constante sugeria antes
# (a revelação já existiu como "puramente visual, nunca bloqueia" numa iteração anterior do
# projeto) — undo (`DEL DEL`/`-` `ENTER`) continua funcionando normalmente durante a revelação, só
# o registro de um giro NOVO é que fica bloqueado.
_REVEAL_MS = 5000
_REVEAL_PULSE_HZ = 0.6  # ciclos/segundo -- "levemente aumentando e diminuindo"
# "Verde gramado" de mesa de cassino — deliberadamente diferente do GREEN vivo já usado pro
# indicador de zero/sistema-ok, que precisa continuar sendo um acento, não um fundo de tela cheia.
_FELT_GREEN = (13, 92, 63)

# Histórico em duas colunas (abaixo do último resultado): preto à esquerda, vermelho à direita,
# zero centralizado entre as duas — mesma convenção espacial usada na revelação em tela cheia.
_HISTORY_TITLE_SIZE = 38  # era 30 — "aumente a descrição do texto último número"
# 0.70 original ("30% menor que o último número"), depois -30% em cima disso a pedido (0.70*0.7=0.49).
_HISTORY_ROW_FONT_RATIO = 0.49

# Faixas fixas (topo: limites de aposta; rodapé: barra de estatística) — o resto da altura vai
# inteiro para as três colunas, que são a maior parte do contrato visual do cliente. A faixa
# "giros da sessão" (contador + tira de chips) foi eliminada — mais espaço pra coluna central
# ("ÚLTIMO RESULTADO" + histórico em três raias), que é a informação mais importante da tela.
_LIMITS_FRACTION = 0.10
_BOTTOM_BAR_PX = 270

# Sombra suave atrás dos badges/cartões e do número em destaque -- pedido explícito do cliente
# pra dar profundidade/sofisticação, aplicado em todas as telas com cartões (badges FRIO/QUENTE,
# cartões da barra de estatística, banner de aviso). Preto semi-transparente (não um preto opaco
# genérico): sobre o fundo já bem escuro do app, opaco ficaria quase invisível ou criaria um
# degradê visível demais -- translúcido lê como profundidade sutil em qualquer fundo por baixo.
_SHADOW_ALPHA = 90
_SHADOW_COLOR = (0, 0, 0, _SHADOW_ALPHA)

# Bem menor que WatchdogSec do systemd (30s) — manda pelo menos umas 3x dentro da janela, prática
# recomendada do próprio systemd pra não arriscar perder um pulso por uma variação pontual de FPS.
_WATCHDOG_HEARTBEAT_MS = 8000
# Checagem de saúde do banco (indicador visual, não o watchdog do systemd): intervalo folgado de
# propósito — só precisa flagar "o SQLite parou de responder", não é uma métrica de performance.
_HEALTH_CHECK_MS = 45000


def _should_heartbeat(now: int, last: int, interval_ms: int) -> bool:
    """Pure gate extracted out of `run()`'s loop so the interval math is unit-testable without a
    real pygame/display session. `now`/`last` are `pygame.time.get_ticks()` values (ms)."""
    return now - last >= interval_ms


def _blit_outlined_text(surface: pygame.Surface, font: pygame.font.Font, text: str,
                         center: tuple[int, int], fill, outline, outline_px: int = 2) -> None:
    """Desenha `text` com um contorno sólido — 8 cópias do texto na cor do contorno, deslocadas 1px
    de cada vez até `outline_px`, depois a cópia final na cor de preenchimento por cima. Técnica
    padrão pra "stroke" de texto sem depender de shader/efeito do font renderer (que o pygame não
    tem) — barato o bastante pra usar tanto na revelação em tela cheia (um texto grande, 5s) quanto
    no histórico em duas colunas (poucas linhas, sempre limitadas ao que cabe na coluna)."""
    outline_surf = font.render(text, True, outline)
    for dx in range(-outline_px, outline_px + 1):
        for dy in range(-outline_px, outline_px + 1):
            if dx == 0 and dy == 0:
                continue
            surface.blit(outline_surf, outline_surf.get_rect(center=(center[0] + dx, center[1] + dy)))
    fill_surf = font.render(text, True, fill)
    surface.blit(fill_surf, fill_surf.get_rect(center=center))


class RouletteDisplay:
    def __init__(self, config: Config, db: Database):
        self.config = config
        self.db = db
        self.service = SpinService(db, config)
        self.backup_service = BackupService(db, config)
        self.export_service = ExportService(db, config)

        # Janela padrão de desenvolvimento também em retrato — é o layout que realmente importa.
        # `create_screen` também aplica `config.screen_rotation`, se configurado (ver
        # app/ui/rotation.py) — nenhuma outra mudança é necessária aqui, `pygame.display.flip()`
        # já sai rotacionado quando for o caso.
        self.screen, self.theme = create_screen(config, f"{config.casino_name} - {config.roulette_name}")
        self.clock = pygame.time.Clock()

        # Carregada uma única vez (não por frame) — pré-escalada para a coluna QUENTE, onde a
        # referência do cliente posiciona a logo (abaixo dos números quentes).
        self.logo_surface = self._prepare_logo(config, self.theme)

        # Sintetiza o beep uma única vez aqui, não a cada revelação — falha (sem placa de som) só
        # desativa o som, nunca trava o boot (ver app/ui/sound.py).
        sound.ensure_ready()

        self.admin = AdminPanel(config, self.service, self.backup_service, self.export_service)
        self.admin_open = False
        self.admin_fade = Tween(0.0, 0.0, 1)  # 0 = fechado, 1 = totalmente aberto

        # Revelação em tela cheia pós-giro — timer de renderização (ver `_reveal_active`).
        # `_confirm_input` bloqueia o registro de um giro novo enquanto ela está ativa (pedido
        # explícito) — digitar continua funcionando, só o ENTER de confirmação fica sem efeito
        # até os 5s acabarem.
        self.reveal_number: int | None = None
        self.reveal_color: str | None = None
        self.reveal_started_at = 0

        self.input_buffer = ""
        self.pending_undo = False
        self.pending_undo_number: int | None = None
        self.pending_undo_deadline = 0

        # Estado do comando "-" (correção/limpeza), no padrão do console de referência do cliente.
        self.minus_buffer: str | None = None
        self.clear_all_pending = False
        self.clear_all_deadline = 0

        # "+" marca "novo giro" (puramente visual — não altera dado nenhum, some sozinho quando o
        # próximo número é confirmado).
        self.awaiting_spin = False

        self.flash_text = ""
        self.flash_color = TEXT_PRIMARY
        self.flash_until = 0
        self.flash_font_size = 28
        self._banner_key: str | None = None
        self.banner_reveal = Tween(1.0, 1.0, 1)

        # "Pop" sutil do número central ao trocar de resultado (não é mais uma tela cheia — só um
        # instante de destaque no próprio número, no lugar onde ele já mora).
        self.number_anim_start = 0

        # Sombras suaves atrás dos badges/cartões -- pedido explícito do cliente pra dar
        # profundidade/sofisticação à interface. Cacheadas por tamanho (não mudam frame a frame,
        # só quando a janela é redimensionada/rotacionada), pra não alocar uma Surface nova a cada
        # frame no Pi 3.
        self._trapezoid_shadow_cache: dict[tuple[int, int, float], pygame.Surface] = {}
        self._rect_shadow_cache: dict[tuple[int, int, int], pygame.Surface] = {}
        self._glow_cache: dict[int, pygame.Surface] = {}

        # Indicador discreto "● SISTEMA OK": só fica verde quando as duas coisas forem verdade —
        # a última escrita no banco teve sucesso E uma checagem periódica leve confirma que o
        # SQLite ainda responde. Não é "ligou = verde": no boot já foi feita uma leitura real do
        # banco (get_display_state logo abaixo), então mesmo o primeiro frame reflete um banco
        # que respondeu de verdade, não um estado assumido.
        self._last_write_ok = True
        self._db_health_ok = True
        self._last_health_check = 0

        self.state: DisplayState = self.service.get_display_state()
        self.running = True

    @staticmethod
    def _prepare_logo(config: Config, theme: Theme) -> pygame.Surface | None:
        image = load_image(config.resolve(config.assets_dir) / "logo.png")
        if image is None:
            return None
        col_w = theme.width / 3
        max_w = col_w * 0.8
        max_h = theme.height * 0.16
        ratio = min(max_w / image.get_width(), max_h / image.get_height())
        size = (max(1, int(image.get_width() * ratio)), max(1, int(image.get_height() * ratio)))
        return pygame.transform.smoothscale(image, size)

    # -- lifecycle ---------------------------------------------------------------

    def run(self) -> None:
        show_splash(self.screen, self.config, self.theme)
        self._render()
        self._crossfade_from_black()
        # Sinaliza pro systemd (Type=notify) que o boot terminou de verdade — só agora o painel
        # está desenhado e pronto pra receber giro. No-op fora do systemd (dev local).
        watchdog_service.ready()
        last_heartbeat = pygame.time.get_ticks()
        self._last_health_check = pygame.time.get_ticks()
        try:
            while self.running:
                fps = self._current_fps()
                self.clock.tick(fps)
                self._handle_events()
                self._check_timeouts()
                self._render()

                # Deliberadamente sequencial, dentro da mesma iteração do loop: não existe uma
                # thread separada de watchdog. Se qualquer chamada acima (_handle_events,
                # _render, etc.) travar de verdade, o código abaixo nunca executa e o pulso para
                # de ser enviado — é exatamente esse silêncio que o WatchdogSec do systemd detecta
                # e usa pra matar/reiniciar o processo. Um heartbeat "vivo" independente do loop
                # principal (ex.: numa thread à parte) daria falso positivo de saúde justamente no
                # caso que mais importa (loop gráfico travado), então não existe um aqui.
                now = pygame.time.get_ticks()
                if _should_heartbeat(now, last_heartbeat, _WATCHDOG_HEARTBEAT_MS):
                    watchdog_service.heartbeat()
                    last_heartbeat = now
                if _should_heartbeat(now, self._last_health_check, _HEALTH_CHECK_MS):
                    self._check_db_health()
                    self._last_health_check = now

                if self.admin.quit_requested:
                    self.running = False
                if self.admin.reboot_requested:
                    self.running = False
                    power_service.reboot()
                if self.admin.shutdown_requested:
                    self.running = False
                    power_service.shutdown()
        finally:
            self.db.close()
            pygame.quit()

    def _crossfade_from_black(self) -> None:
        """Suaviza o corte entre a splash e a tela principal: a primeira tela já foi desenhada,
        só sobrepõe um véu preto que desvanece por cima dela."""
        clock = pygame.time.Clock()
        start = pygame.time.get_ticks()
        veil = pygame.Surface(self.screen.get_size())
        veil.fill((0, 0, 0))
        while True:
            t = (pygame.time.get_ticks() - start) / _SPLASH_CROSSFADE_MS
            if t >= 1.0:
                break
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.running = False
                    return
            veil.set_alpha(int(255 * (1 - ease_out_cubic(t))))
            self.screen.blit(veil, (0, 0))
            pygame.display.flip()
            clock.tick(self.config.target_fps)

    def _current_fps(self) -> int:
        now = pygame.time.get_ticks()
        typing = (
            bool(self.input_buffer)
            or self.pending_undo
            or self.minus_buffer is not None
            or self.clear_all_pending
            or self.awaiting_spin
        )
        if self.admin_open or typing or self._is_animating(now):
            return self.config.target_fps
        return self.config.idle_fps

    def _is_animating(self, now: int) -> bool:
        if now - self.number_anim_start < _NUMBER_POP_MS:
            return True
        if not self.admin_fade.done(now):
            return True
        if not self.banner_reveal.done(now):
            return True
        if self._reveal_active(now):
            return True
        return False

    def _reveal_active(self, now: int) -> bool:
        return self.reveal_number is not None and (now - self.reveal_started_at) < _REVEAL_MS

    # -- events ---------------------------------------------------------------

    def _handle_events(self) -> None:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
            elif event.type == pygame.KEYDOWN:
                self._handle_keydown(event)

    def _handle_keydown(self, event: pygame.event.Event) -> None:
        if self.admin_open:
            still_open = self.admin.handle_key(event)
            if not still_open:
                self.admin_open = False
                self.admin_fade.retarget(self.admin_fade.value(), 0.0, _ADMIN_FADE_MS)
            return

        mods = event.mod
        key = event.key

        if key in _MODIFIER_KEYS:
            return

        if key == pygame.K_a and (mods & pygame.KMOD_CTRL) and (mods & pygame.KMOD_ALT):
            self.admin.open()
            self.admin_open = True
            self.admin_fade.retarget(self.admin_fade.value(), 1.0, _ADMIN_FADE_MS)
            return

        # Diálogo de confirmação do "apagar tudo" tem prioridade: qualquer tecla que não seja
        # ENTER/cancelar apenas descarta o diálogo (não deixa a tecla "vazar" para outro comando).
        if self.clear_all_pending:
            if key in _ENTER_KEYS:
                self._execute_clear_all()
            elif key in _CANCEL_KEYS:
                self.clear_all_pending = False
                self._flash("Cancelado", TEXT_MUTED, duration_ms=1000)
            else:
                self.clear_all_pending = False
            return

        if key == pygame.K_BACKSPACE and (mods & pygame.KMOD_CTRL):
            self._attempt_undo(force=True)
            return

        if key in _PLUS_KEYS:
            self.awaiting_spin = True
            return

        if key in _MINUS_KEYS:
            self.input_buffer = ""
            self.pending_undo = False
            self.minus_buffer = ""
            return

        if self.minus_buffer is not None:
            if key in _DIGIT_KEYS:
                if len(self.minus_buffer) < len(_CLEAR_ALL_CODE):
                    self.minus_buffer += _DIGIT_KEYS[key]
                return
            if key in _CANCEL_KEYS:
                self.minus_buffer = None
                return
            if key in _ENTER_KEYS:
                self._confirm_minus_command()
                return
            # qualquer outra tecla aborta o comando "-" em andamento
            self.minus_buffer = None

        if key in _DIGIT_KEYS:
            if len(self.input_buffer) < 2:
                self.input_buffer += _DIGIT_KEYS[key]
            self.pending_undo = False
            return

        if key == pygame.K_BACKSPACE:
            self.input_buffer = self.input_buffer[:-1]
            return

        if key in _CANCEL_KEYS:
            self.input_buffer = ""
            self.pending_undo = False
            self.awaiting_spin = False
            return

        if key in _ENTER_KEYS:
            self._confirm_input()
            return

        if key == pygame.K_DELETE:
            self._attempt_undo(force=False)
            return

    def _confirm_input(self) -> None:
        if not self.input_buffer:
            return
        if self._reveal_active(pygame.time.get_ticks()):
            # Pedido explícito: não permitir registrar um número novo enquanto o anterior ainda
            # está na tela de revelação (5s). O buffer digitado fica intacto -- o operador só
            # precisa apertar ENTER de novo quando a revelação acabar, sem perder o que já tinha
            # digitado. A própria tela cheia da revelação já deixa claro pro operador por que o
            # ENTER não teve efeito, então não duplicamos isso com um banner (que nem apareceria:
            # a revelação substitui o frame inteiro, ver `_render`).
            return
        number = int(self.input_buffer)
        self.input_buffer = ""
        if not (0 <= number <= 36):
            self._flash(f"Número inválido: {number} (use 0 a 36)", RED)
            return
        try:
            spin = self.service.register_spin(number)
        except Exception:
            self._mark_write_failed("registrar giro")
            return
        self._mark_write_ok()
        self.awaiting_spin = False
        self._refresh_state()
        # Feedback curto (não a mesma coisa que o "pop" do número: aquele é permanente até o
        # próximo giro, isso aqui é só uma confirmação de que o ENTER "pegou"). Fonte maior que os
        # outros avisos (banner genérico usa 28px) — precisa ser percebido instantaneamente, sem
        # travar a leitura do próximo giro nem bloquear entrada (só desenha, não pausa o loop).
        self._flash(
            f"REGISTRADO • {spin.number}", NUMBER_COLOR_MAP[spin.color],
            duration_ms=_REGISTERED_FLASH_MS, font_size=44,
        )
        # Revelação em tela cheia (5s), ver `_reveal_active`. Só chegamos aqui se ela NÃO estava
        # ativa (bloqueada logo no topo de `_confirm_input`) -- então isto sempre inicia um ciclo
        # novo, nunca sobrescreve uma revelação em andamento.
        self.reveal_number = spin.number
        self.reveal_color = spin.color
        self.reveal_started_at = pygame.time.get_ticks()
        sound.play_reveal_beep()

    def _confirm_minus_command(self) -> None:
        """Handles "-" ENTER (corrige o último número) e "-97" ENTER (apaga tudo), no mesmo
        padrão do console de referência do cliente."""
        code = self.minus_buffer or ""
        self.minus_buffer = None
        if code == "":
            self._attempt_undo(force=True)
        elif code == _CLEAR_ALL_CODE:
            self.clear_all_pending = True
            self.clear_all_deadline = pygame.time.get_ticks() + int(self.config.undo_confirm_seconds * 1000)
        else:
            self._flash(f"Comando inválido: -{code}", RED)

    def _execute_clear_all(self) -> None:
        self.clear_all_pending = False
        try:
            n = self.service.clear_session()
        except Exception:
            self._mark_write_failed("reiniciar sessão")
            return
        self._mark_write_ok()
        self._refresh_state()
        self._flash(f"Sessão reiniciada — painel zerado ({n} giros arquivados)", RED)

    def _attempt_undo(self, force: bool) -> None:
        last = self.state.last_spin
        if last is None:
            self._flash("Nada para desfazer", TEXT_MUTED)
            return
        now = pygame.time.get_ticks()
        if force or (self.pending_undo and now < self.pending_undo_deadline):
            try:
                removed = self.service.undo_last()
            except Exception:
                self._mark_write_failed("desfazer giro")
                return
            self._mark_write_ok()
            self.pending_undo = False
            self._refresh_state()
            if removed is not None:
                self._flash(f"Resultado removido: {removed.number}", RED)
        else:
            self.pending_undo = True
            self.pending_undo_number = last.number
            self.pending_undo_deadline = now + int(self.config.undo_confirm_seconds * 1000)

    def _refresh_state(self) -> None:
        """Recarrega o estado do serviço e dispara o "pop" do número quando o resultado atual
        muda — chamado depois de toda ação que altera spins (registrar/desfazer/limpar)."""
        old_last_id = self.state.last_spin.id if self.state.last_spin else None
        self.state = self.service.get_display_state()
        new_last_id = self.state.last_spin.id if self.state.last_spin else None
        if new_last_id != old_last_id:
            self.number_anim_start = pygame.time.get_ticks()

    def _check_timeouts(self) -> None:
        now = pygame.time.get_ticks()
        if self.pending_undo and now >= self.pending_undo_deadline:
            self.pending_undo = False
        if self.clear_all_pending and now >= self.clear_all_deadline:
            self.clear_all_pending = False

    def _flash(self, text: str, color, duration_ms: int = _FLASH_MS, font_size: int = 28) -> None:
        self.flash_text = text
        self.flash_color = color
        self.flash_until = pygame.time.get_ticks() + duration_ms
        self.flash_font_size = font_size

    @property
    def system_ok(self) -> bool:
        """Verde só quando a última escrita teve sucesso E a checagem periódica de saúde do
        banco (não por frame — ver `_check_db_health`) também passou. Nunca "verde porque o
        processo iniciou"."""
        return self._last_write_ok and self._db_health_ok

    def _mark_write_ok(self) -> None:
        self._last_write_ok = True

    def _mark_write_failed(self, action: str) -> None:
        """Uma escrita no SQLite falhou (cartão cheio, corrompido, etc.) — o operador precisa
        saber na hora, não só depois lendo log. O indicador "SISTEMA OK" vira vermelho até a
        próxima escrita bem-sucedida."""
        logger.exception("Falha ao %s", action)
        self._last_write_ok = False
        self._flash(f"Falha ao {action} — veja o indicador de sistema", RED, duration_ms=4000)

    def _check_db_health(self) -> None:
        """Checagem leve e periódica (não a cada frame — chamada só a cada `_HEALTH_CHECK_MS`,
        no mesmo espírito do heartbeat do watchdog) para cobrir o caso de o banco ficar
        inacessível numa sessão sem nenhum giro novo por um bom tempo (ex.: intervalo entre
        mesas). Um `SELECT 1` é essencialmente grátis para o SQLite, então isso não pesa mesmo
        chamado a cada alguns segundos — só não faz sentido fazer isso a cada frame (~30x/s)."""
        try:
            self._db_health_ok = self.db.is_healthy()
        except Exception:
            logger.exception("Checagem de saúde do banco falhou")
            self._db_health_ok = False

    # -- rendering ---------------------------------------------------------------

    def _render(self) -> None:
        screen = self.screen
        theme = self.theme
        now = pygame.time.get_ticks()

        if self._reveal_active(now):
            # Substitui o frame inteiro (não desenha por cima) — mais barato e evita qualquer
            # vazamento visual do placar por baixo/nas bordas do círculo.
            self._draw_full_reveal(now)
            if self.admin_open or self.admin_fade.value() > 0.001:
                self.admin.render(screen, theme, reveal=self.admin_fade.value())
            pygame.display.flip()
            return

        screen.fill(BG)

        limits_h = int(theme.height * _LIMITS_FRACTION)
        bottom_h = theme.px(_BOTTOM_BAR_PX)
        # "Giros da sessão" + tira de chips foi eliminada — o espaço que ela ocupava agora é da
        # coluna central (mais altura pra "ÚLTIMO RESULTADO" e pro histórico em três raias).
        columns_h = theme.height - limits_h - bottom_h

        self._draw_limits(pygame.Rect(0, 0, theme.width, limits_h))
        self._draw_columns(pygame.Rect(0, limits_h, theme.width, columns_h))
        self._draw_bottom_bar(pygame.Rect(0, theme.height - bottom_h, theme.width, bottom_h))

        self._draw_overlays()
        self._draw_system_indicator()

        if self.admin_open or self.admin_fade.value() > 0.001:
            self.admin.render(screen, theme, reveal=self.admin_fade.value())

        pygame.display.flip()

    # -- revelação em tela cheia (pós-giro) ------------------------------------------

    def _draw_full_reveal(self, now: int) -> None:
        """Fundo verde-gramado, número em branco com borda preta dentro de um círculo grande na
        cor do número, classificação embaixo — número e círculo sempre centralizados na tela
        (horizontal e vertical, não importa a cor), tudo dimensionado pra ser lido de longe.
        Pulsa suavemente (`_REVEAL_PULSE_HZ`) enquanto fica em tela."""
        theme = self.theme
        screen = self.screen
        number = self.reveal_number
        color_name = self.reveal_color
        screen.fill(_FELT_GREEN)

        elapsed_s = (now - self.reveal_started_at) / 1000.0
        pulse = 1.0 + 0.05 * math.sin(2 * math.pi * _REVEAL_PULSE_HZ * elapsed_s)

        cx, cy = theme.width // 2, theme.height // 2

        # Era 0.22 -- "bem maiores" pedido explicitamente, pra ler de longe (parede de cassino).
        base_r = int(min(theme.width, theme.height) * 0.34)
        r = max(1, int(base_r * pulse))
        pygame.draw.circle(screen, COLOR_MAP[color_name], (cx, cy), r)

        number_font = theme.font(max(20, int(r * 1.05)), bold=True)
        outline_px = max(2, theme.px(4))
        _blit_outlined_text(screen, number_font, str(number), (cx, cy),
                             fill=TEXT_PRIMARY, outline=BLACK, outline_px=outline_px)

        # Classificação proporcional ao tamanho do círculo (não mais um tamanho fixo) -- "dados
        # abaixo dos números devem ser grandes também, proporcionais ao tamanho do círculo".
        tag_font = theme.font(max(20, int(r * 0.20)), bold=True)
        tag_gap = int(r * 0.16)
        tag_y = cy + r + tag_gap
        for tag_text, tag_color in self._reveal_tags(number, color_name):
            tag_surf = tag_font.render(tag_text, True, tag_color)
            screen.blit(tag_surf, tag_surf.get_rect(midtop=(cx, tag_y)))
            tag_y += tag_surf.get_height() + theme.px(10)

    @staticmethod
    def _reveal_tags(number: int, color_name: str) -> list[tuple[str, tuple[int, int, int]]]:
        """Mesmos termos já usados na barra de estatística (ÍMPAR/PAR/VERMELHO/ZERO/PRETO/
        MENOR/MAIOR) — zero só mostra "ZERO" (paridade/faixa não se aplicam a zero, mesma
        convenção já usada em app/models/roulette_data.py e no módulo de analytics)."""
        color_tag = {"red": ("VERMELHO", RED), "black": ("PRETO", TEXT_PRIMARY), "green": ("ZERO", GREEN)}
        tags = [color_tag[color_name]]
        if number != 0:
            parity = roulette_data.parity_of(number)
            tags.append(("ÍMPAR", CYAN) if parity == "odd" else ("PAR", CYAN))
            range_ = roulette_data.range_of(number)
            tags.append(("MENOR", ORANGE) if range_ == "low" else ("MAIOR", ORANGE))
        return tags

    def _draw_limits(self, rect: pygame.Rect) -> None:
        """Zona 1: APOSTA MIN. à esquerda, APOSTA MAX. à direita, valores em azul/ciano."""
        theme = self.theme
        margin = theme.px(24)
        label_font = theme.font(40, bold=True)
        value_font = theme.font(82, bold=True)

        min_label = label_font.render("APOSTA MIN.", True, TEXT_PRIMARY)
        min_value = value_font.render(f"{self.config.currency} {self.config.min_bet}", True, CYAN)
        max_label = label_font.render("APOSTA MAX.", True, TEXT_PRIMARY)
        max_value = value_font.render(f"{self.config.currency} {self.config.max_bet}", True, CYAN)

        block_h = min_label.get_height() + min_value.get_height() + theme.px(4)
        top = rect.centery - block_h // 2

        self.screen.blit(min_label, (margin, top))
        self.screen.blit(min_value, (margin, top + min_label.get_height() + theme.px(4)))
        self.screen.blit(max_label, max_label.get_rect(topright=(rect.width - margin, top)))
        self.screen.blit(
            max_value, max_value.get_rect(topright=(rect.width - margin, top + max_label.get_height() + theme.px(4)))
        )
        pygame.draw.line(self.screen, PANEL_BORDER, (0, rect.bottom), (rect.width, rect.bottom), 1)

    # -- as três colunas (frio / resultado atual / quente) -------------------------

    def _draw_columns(self, rect: pygame.Rect) -> None:
        theme = self.theme
        col_w = rect.width // 3
        frio_rect = pygame.Rect(rect.left, rect.top, col_w, rect.height)
        center_rect = pygame.Rect(rect.left + col_w, rect.top, col_w, rect.height)
        quente_rect = pygame.Rect(rect.left + col_w * 2, rect.top, rect.width - col_w * 2, rect.height)

        pad_v = theme.px(16)
        for x in (frio_rect.right, quente_rect.left):
            pygame.draw.line(self.screen, PANEL_BORDER, (x, rect.top + pad_v), (x, rect.bottom - pad_v), 1)

        # "300 GIROS" é o tamanho MÁXIMO da janela de análise (config), não quantos giros já
        # aconteceram de fato — mostrar o valor fixo do config aqui enganaria o operador logo no
        # início da sessão (ex.: "300 GIROS" com só 4 giros registrados). O rótulo mostra quantos
        # giros estão realmente entrando na conta agora, que nunca passa do tamanho da janela.
        # Rótulos explícitos sobre o que cada coluna realmente mede — nem FRIO nem QUENTE são
        # previsão (roleta não tem memória): FRIO é há quantos giros aquele número não sai,
        # QUENTE é quantas vezes ele saiu dentro da janela estatística configurada. O algoritmo
        # não muda aqui, só a legenda que explica o número mostrado embaixo de cada badge.
        window = min(self.state.total_spins, self.config.statistics_window)
        # SILVER (FRIO) / GOLD (QUENTE): linha de destaque no topo e na base de cada badge —
        # pedido explícito do cliente, puramente decorativo (não muda o dado exibido).
        self._draw_badge_column(frio_rect, "FRIO", "GIROS SEM SAIR", CYAN, self.state.cold,
                                 self.config.cold_numbers_count, unit="GIROS", accent_line=SILVER)
        self._draw_center_number(center_rect)
        self._draw_badge_column(quente_rect, "QUENTE", "OCORRÊNCIAS", RED, self.state.hot,
                                 self.config.hot_numbers_count, unit="VEZES", show_logo=True,
                                 caption=f"últimos {window} giros", accent_line=GOLD)

    def _draw_badge_column(self, rect: pygame.Rect, title: str, subtitle: str, color,
                            entries: list[tuple[int, int]], slot_count: int, unit: str,
                            show_logo: bool = False, caption: str | None = None,
                            accent_line=None) -> None:
        """Coluna FRIO/QUENTE: título + subtítulo, depois `slot_count` badges trapezoidais
        (número dentro, cor real do card) com a contagem e a unidade escritas abaixo de cada
        badge — exatamente a estrutura da referência do cliente. `caption` é uma segunda linha
        opcional, menor e discreta (ex.: tamanho da janela estatística) — não substitui o
        subtítulo principal, só adiciona contexto sem virar um segundo título."""
        theme = self.theme

        title_font = theme.font(38, bold=True)
        title_surf = title_font.render(title, True, color)
        self.screen.blit(title_surf, title_surf.get_rect(midtop=(rect.centerx, rect.top + theme.px(8))))

        subtitle_font = theme.font(21, bold=True)
        subtitle_surf = subtitle_font.render(subtitle, True, TEXT_PRIMARY)
        sub_y = rect.top + theme.px(8) + title_surf.get_height() + theme.px(2)
        self.screen.blit(subtitle_surf, subtitle_surf.get_rect(midtop=(rect.centerx, sub_y)))

        content_top = sub_y + subtitle_surf.get_height() + theme.px(2)
        if caption:
            caption_font = theme.font(15, bold=False)
            caption_surf = caption_font.render(caption, True, TEXT_SECONDARY)
            content_top += caption_surf.get_height()
            self.screen.blit(caption_surf, caption_surf.get_rect(midtop=(rect.centerx, content_top - caption_surf.get_height())))

        # Badges compactos e agrupados logo abaixo do subtítulo — igual à referência do cliente,
        # que NÃO espalha os 3 badges pela altura inteira da coluna. Dimensão do trapézio segue a
        # largura da coluna (não a altura disponível), então o tamanho não varia com slot_count.
        # Levemente maiores que antes (0.62->0.70 da largura da coluna) para reduzir a área morta
        # que sobrava entre os badges e o rodapé da coluna, sem adicionar nenhum elemento novo.
        content_top += theme.px(20)
        badge_w = int(rect.width * 0.70)
        badge_h = int(badge_w / 1.7)
        badge_gap = theme.px(30)

        # Era 0.62 -- "os numeros das sessoes quente/frio devem ser maiores".
        number_font = theme.font(int(badge_h * 0.80), bold=True)
        number_outline_px = max(1, theme.px(2))
        count_font = theme.font(36, bold=True)
        unit_font = theme.font(26, bold=True)

        y = content_top
        for i in range(max(1, slot_count)):
            badge_rect = pygame.Rect(0, 0, badge_w, badge_h)
            badge_rect.midtop = (rect.centerx, int(y))

            if i < len(entries):
                number, value = entries[i]
                self._draw_trapezoid(badge_rect, color, accent_line=accent_line)
                # Branco com borda preta -- pedido explícito, no lugar do texto escuro sobre o
                # preenchimento vibrante do badge (menos contraste/legibilidade à distância).
                _blit_outlined_text(self.screen, number_font, str(number), badge_rect.center,
                                     fill=TEXT_PRIMARY, outline=BLACK, outline_px=number_outline_px)

                count_surf = count_font.render(str(value), True, TEXT_PRIMARY)
                count_y = badge_rect.bottom + theme.px(6)
                self.screen.blit(count_surf, count_surf.get_rect(midtop=(rect.centerx, count_y)))
                unit_surf = unit_font.render(unit, True, TEXT_SECONDARY)
                unit_y = count_y + count_surf.get_height() + theme.px(2)
                self.screen.blit(unit_surf, unit_surf.get_rect(midtop=(rect.centerx, unit_y)))
                y = unit_y + unit_surf.get_height() + badge_gap
            else:
                y = badge_rect.bottom + badge_gap

        if show_logo and self.logo_surface is not None:
            # Ancorada perto do rodapé (não centralizada no vão vazio) — mantém a logo "grudada"
            # na base da coluna como na referência, em vez de flutuar solta no meio do espaço
            # vazio quando a tela real de TV (bem mais alta que o mockup) sobra mais altura.
            margin_bottom = theme.px(28)
            min_cy = y + self.logo_surface.get_height() // 2
            logo_cy = max(min_cy, rect.bottom - margin_bottom - self.logo_surface.get_height() // 2)
            self.screen.blit(self.logo_surface, self.logo_surface.get_rect(center=(rect.centerx, logo_cy)))

    def _draw_trapezoid(self, rect: pygame.Rect, color, taper: float = 0.16, accent_line=None) -> None:
        """Card trapezoidal (mais largo em cima, mais estreito embaixo), igual aos badges da
        referência do cliente — não um retângulo arredondado genérico.

        Desenha em `self.screen`, não em `pygame.display.get_surface()`: com `screen_rotation`
        configurado (ver app/ui/rotation.py) os dois deixam de ser a mesma superfície — `self.screen`
        é a superfície "lógica" que o resto do app desenha, e é ela que precisa receber o trapézio
        pra ele aparecer rotacionado corretamente junto com o resto do frame.

        `accent_line`: cor opcional de uma linha fina no topo e na base do trapézio (dourada nos
        badges QUENTE, prateada nos FRIO) — puramente decorativo, pedido explícito do cliente."""
        theme = self.theme
        shadow_offset = theme.px(5)
        shadow = self._trapezoid_shadow_surface(rect.width, rect.height, taper)
        self.screen.blit(shadow, (rect.left + shadow_offset, rect.top + shadow_offset))

        top_left = (rect.left, rect.top)
        top_right = (rect.right, rect.top)
        inset = int(rect.width * taper)
        bottom_right = (rect.right - inset, rect.bottom)
        bottom_left = (rect.left + inset, rect.bottom)
        pygame.draw.polygon(self.screen, color, [top_left, top_right, bottom_right, bottom_left])
        if accent_line is not None:
            line_px = max(2, theme.px(3))
            pygame.draw.line(self.screen, accent_line, top_left, top_right, line_px)
            pygame.draw.line(self.screen, accent_line, bottom_left, bottom_right, line_px)

    def _trapezoid_shadow_surface(self, w: int, h: int, taper: float) -> pygame.Surface:
        """Sombra do trapézio, cacheada por tamanho (ver comentário no `__init__` sobre não alocar
        Surface nova a cada frame no Pi 3)."""
        key = (w, h, taper)
        surf = self._trapezoid_shadow_cache.get(key)
        if surf is None:
            surf = pygame.Surface((w, h), pygame.SRCALPHA)
            inset = int(w * taper)
            pygame.draw.polygon(surf, _SHADOW_COLOR, [(0, 0), (w, 0), (w - inset, h), (inset, h)])
            self._trapezoid_shadow_cache[key] = surf
        return surf

    def _rect_shadow_surface(self, w: int, h: int, radius: int) -> pygame.Surface:
        """Sombra de um cartão retangular (cantos arredondados), mesma lógica de cache acima --
        usada na barra de estatística e no banner de avisos."""
        key = (w, h, radius)
        surf = self._rect_shadow_cache.get(key)
        if surf is None:
            surf = pygame.Surface((w, h), pygame.SRCALPHA)
            pygame.draw.rect(surf, _SHADOW_COLOR, surf.get_rect(), border_radius=radius)
            self._rect_shadow_cache[key] = surf
        return surf

    def _glow_surface(self, radius: int) -> pygame.Surface:
        """Círculo branco cheio, cacheado por raio -- a opacidade real é ajustada por frame via
        `set_alpha()` em `_draw_center_number` (ver comentário lá: dá o brilho suave que aparece e
        desvanece junto com o "pop" do número, sem alocar Surface nova a cada frame)."""
        surf = self._glow_cache.get(radius)
        if surf is None:
            surf = pygame.Surface((radius * 2, radius * 2), pygame.SRCALPHA)
            pygame.draw.circle(surf, (*TEXT_PRIMARY, 255), (radius, radius), radius)
            self._glow_cache[radius] = surf
        return surf

    # -- número atual (coluna central) ---------------------------------------------

    def _draw_center_number(self, rect: pygame.Rect) -> None:
        theme = self.theme
        last = self.state.last_spin
        now = pygame.time.get_ticks()

        # Era 30px — "aumente a descrição do texto último número".
        title_font = theme.font(_HISTORY_TITLE_SIZE, bold=True)
        title_surf = title_font.render("ÚLTIMO RESULTADO", True, TEXT_PRIMARY)
        self.screen.blit(title_surf, title_surf.get_rect(midtop=(rect.centerx, rect.top + theme.px(8))))

        if self.awaiting_spin:
            blink_on = (now // 500) % 2 == 0
            if blink_on:
                # Sem "●" Unicode: a fonte padrão do pygame não tem esse glifo (mesmo problema dos
                # naipes) — o ponto é desenhado como um círculo vetorial ao lado do texto.
                badge_font = theme.font(20, bold=True)
                badge_surf = badge_font.render("AGUARDANDO", True, ORANGE)
                badge_y = rect.top + theme.px(8) + title_surf.get_height() + theme.px(4)
                dot_r = theme.px(4)
                text_rect = badge_surf.get_rect(
                    midtop=(rect.centerx + dot_r + theme.px(3), badge_y)
                )
                pygame.draw.circle(
                    self.screen, ORANGE, (text_rect.left - dot_r - theme.px(3), text_rect.centery), dot_r
                )
                self.screen.blit(badge_surf, text_rect)

        # Alinhado com o topo dos badges de FRIO/QUENTE (não centralizado na coluna) — na
        # referência do cliente o número grande fica agrupado no alto, junto com as outras colunas,
        # deixando a metade inferior livre (mesmo espaço vazio que sobra ao lado dos badges).
        content_top = rect.top + theme.px(8) + title_surf.get_height() + theme.px(60)

        # 0.34->0.40 da altura da coluna: número central maior preenche melhor o espaço
        # disponível (reduz a sensação de "área morta" logo abaixo dele) sem descer o bloco
        # de posição — continua alinhado ao topo, junto com os badges de FRIO/QUENTE, como no
        # contrato visual do cliente. Calculado independente de `last`/animação (tamanho "de
        # repouso") para a linha divisória abaixo não pular de posição a cada giro.
        reference_size = min(int(rect.height * 0.40), int(rect.width * 1.0))
        # Histórico de pedidos: +30%, +30% de novo (1.3*1.3=1.69), depois -30% em cima disso
        # (1.69*0.7=1.183) — "ÚLTIMO RESULTADO" continua sendo a informação de maior destaque, só
        # um pouco menor que a versão anterior. Usado só pro número grande: o histórico abaixo usa
        # `reference_size` (o tamanho "original") pra calcular a fonte das linhas.
        base_size = int(reference_size * 1.183)

        history_outline_px = max(1, theme.px(2))  # mesmo valor usado em _draw_center_history

        # Altura REAL renderizada do número, não `base_size` (o "tamanho de fonte" pedido ao
        # theme.font(), que passa de novo por `theme.px()` internamente — em telas com escala
        # diferente de 1080 de referência, o pixel real renderizado pode ficar bem menor que
        # `base_size` sugere). Medida uma vez, num tamanho "de repouso" (sem a escala da animação
        # de pop), pra linha divisória não pular de posição a cada giro nem abrir um vão enorme.
        steady_font = theme.font(max(20, base_size), bold=True)
        number_pixel_height = steady_font.render("8", True, TEXT_PRIMARY).get_height()

        if last is not None:
            # "Preto" fica preto de verdade aqui (não branco, como em NUMBER_COLOR_MAP — usado só
            # neste destaque, não em outros lugares que consomem NUMBER_COLOR_MAP) — o contorno
            # branco acima já garante contraste contra o fundo escuro, então não precisa mais do
            # branco como preenchimento de substituição.
            color = BLACK if last.color == "black" else NUMBER_COLOR_MAP[last.color]
            elapsed = now - self.number_anim_start
            t = min(1.0, elapsed / _NUMBER_POP_MS) if elapsed >= 0 else 1.0
            pop = ease_out_back(t) if elapsed < _NUMBER_POP_MS else 1.0
            scale = 0.72 + 0.28 * pop

            number_font = theme.font(max(20, int(base_size * scale)), bold=True)
            # Contorno sempre BRANCO (pedido explícito — era preto, igual à revelação em tela
            # cheia) com o dobro da grossura do contorno usado no histórico abaixo.
            center = (rect.centerx, content_top + number_pixel_height // 2)

            # Brilho suave atrás do número, só durante a janela do "pop" (mesmo elapsed/t de cima)
            # -- desvanece junto com ele, reforçando visualmente "acabou de mudar" sem virar uma
            # animação nova e separada pra acompanhar.
            if elapsed < _NUMBER_POP_MS:
                glow_alpha = int(130 * (1.0 - t))
                if glow_alpha > 0:
                    glow_r = int(number_pixel_height * 0.85)
                    glow = self._glow_surface(glow_r)
                    glow.set_alpha(glow_alpha)
                    self.screen.blit(glow, (center[0] - glow_r, center[1] - glow_r))

            _blit_outlined_text(self.screen, number_font, str(last.number), center,
                                 fill=color, outline=TEXT_PRIMARY, outline_px=history_outline_px * 2)
        else:
            hint_font = theme.font(26, bold=True)
            hint = hint_font.render("AGUARDANDO", True, TEXT_MUTED)
            hint2 = hint_font.render("PRIMEIRO GIRO", True, TEXT_MUTED)
            self.screen.blit(hint, hint.get_rect(midtop=(rect.centerx, content_top)))
            self.screen.blit(hint2, hint2.get_rect(midtop=(rect.centerx, content_top + hint.get_height())))

        # Linha divisória + histórico em três raias (preto/vermelho, zero centralizado) no espaço
        # que sobrava vazio abaixo do número — ver `_draw_center_history`. Gap BEM reduzido (era
        # px(24), depois px(10)) pra colar a linha no número e sobrar o máximo de altura possível
        # pro histórico — "preenchido com os números que foram lançados antes". A margem de
        # `history_outline_px * 2` garante espaço pro contorno (agora mais grosso) do número não
        # ficar cortado pela própria linha.
        divider_y = content_top + number_pixel_height + history_outline_px * 2 + theme.px(3)
        pygame.draw.line(
            self.screen, PANEL_BORDER, (rect.left + theme.px(16), divider_y), (rect.right - theme.px(16), divider_y), 1,
        )
        history_rect = pygame.Rect(rect.left, divider_y + theme.px(6), rect.width, rect.bottom - (divider_y + theme.px(6)))
        self._draw_center_history(history_rect, reference_size)

    def _draw_center_history(self, rect: pygame.Rect, last_number_base_size: int) -> None:
        """Histórico recente em três "raias" verticais dentro do que sobra da coluna central:
        preto à esquerda, vermelho à direita, zero centralizado — mesma convenção espacial da
        revelação em tela cheia. Cada LINHA representa um giro (mais recente no topo, sempre —
        `state.history` já vem nessa ordem); dentro da linha, o número aparece só na raia da sua
        cor, e as outras duas ficam em branco — um "roadmap" de zigue-zague, não três listas
        independentes por cor, pra ficar fácil de ler de relance qual cor saiu quando (pedido
        explícito: "o do meio ou a lateral na posição paralela devem ficar em branco"). Limitado a
        quantas linhas couberem na altura disponível — mesmo espírito "sempre limitado, nunca uma
        lista infinita" já usado pelos badges de FRIO/QUENTE."""
        theme = self.theme
        history = self.state.history  # mais recente primeiro
        if not history or rect.height <= 0:
            return

        row_font_size = max(12, int(last_number_base_size * _HISTORY_ROW_FONT_RATIO))
        row_font = theme.font(row_font_size, bold=True)
        row_h = row_font.get_linesize() + theme.px(4)
        max_rows = max(0, rect.height // row_h)
        if max_rows == 0:
            return

        lane = {
            "black": (rect.left + rect.width // 4, BLACK),
            "red": (rect.right - rect.width // 4, RED),
            "green": (rect.centerx, GREEN),
        }
        outline_px = max(1, theme.px(2))

        for i, spin in enumerate(history[:max_rows]):
            y = rect.top + i * row_h + row_h // 2
            cx, fill = lane[spin.color]
            _blit_outlined_text(self.screen, row_font, str(spin.number), (cx, y),
                                 fill=fill, outline=TEXT_PRIMARY, outline_px=outline_px)

    # -- barra de estatística (rodapé) ----------------------------------------------

    def _draw_bottom_bar(self, rect: pygame.Rect) -> None:
        """Zona 5: sete cartões arredondados (ÍMPAR/PAR/VERMELHO/ZERO/PRETO/MENOR/MAIOR), cada um
        com borda e fundo sutilmente tingido na cor da própria categoria — não mais só
        VERMELHO/ZERO preenchidos e o resto neutro. PRETO usa branco (sem "preto" na paleta,
        igual ao número central) e MENOR/MAIOR usam laranja como cor de categoria própria (não
        ligada ao "quente", que agora é vermelho).

        O "ícone" de cada cartão é só texto/forma neutra (nunca naipe de baralho ♦♣♥♠): eram
        puramente decorativos, sem nenhuma regra do sistema ligada a eles, e um equipamento só de
        roleta não deveria remeter a jogo de cartas. ZERO/MENOR/MAIOR já usavam número/faixa como
        "ícone" — ÍMPAR/PAR/VERMELHO/PRETO agora usam um ponto simples na cor da categoria, no
        mesmo espírito (cor + texto carregam o significado, a forma é só um apoio visual neutro)."""
        theme = self.theme
        s = self.state
        pygame.draw.line(self.screen, PANEL_BORDER, (0, rect.top), (rect.width, rect.top), 1)

        cells = [
            ("ÍMPAR", f"{s.parity.percentage('odd')}%", "dot", CYAN),
            ("PAR", f"{s.parity.percentage('even')}%", "dot", CYAN),
            ("VERMELHO", f"{s.color.percentage('red')}%", "dot", RED),
            ("ZERO", f"{s.color.percentage('green')}%", "zero", GREEN),
            ("PRETO", f"{s.color.percentage('black')}%", "dot", TEXT_PRIMARY),
            ("MENOR", f"{s.range_.percentage('low')}%", "1–18", ORANGE),
            ("MAIOR", f"{s.range_.percentage('high')}%", "19–36", ORANGE),
        ]
        value_font = theme.font(46, bold=True)
        label_font = theme.font(28, bold=True)
        cell_w = rect.width / len(cells)
        gutter = theme.px(6)
        radius = theme.px(16)

        shadow_offset = theme.px(5)
        for i, (label, value, icon, accent) in enumerate(cells):
            outer = pygame.Rect(int(i * cell_w), rect.top, int(cell_w) + 1, rect.height)
            card = outer.inflate(-gutter * 2, -theme.px(16))

            shadow = self._rect_shadow_surface(card.width, card.height, radius)
            self.screen.blit(shadow, (card.left + shadow_offset, card.top + shadow_offset))

            pygame.draw.rect(self.screen, self._tint(accent, 0.16), card, border_radius=radius)
            pygame.draw.rect(self.screen, accent, card, width=2, border_radius=radius)

            value_surf = value_font.render(value, True, accent)
            self.screen.blit(value_surf, value_surf.get_rect(center=(card.centerx, card.top + theme.px(46))))
            label_surf = label_font.render(label, True, accent)
            self.screen.blit(label_surf, label_surf.get_rect(center=(card.centerx, card.top + theme.px(86))))

            icon_center = (card.centerx, card.bottom - theme.px(50))
            if icon in ("zero", "1–18", "19–36"):
                # Mesmo tamanho do valor (primeira linha do cartão) — ZERO/MENOR/MAIOR não têm
                # ícone gráfico, então o texto precisa carregar o mesmo peso visual.
                icon_font = theme.font(46, bold=True)
                text = "0" if icon == "zero" else icon
                icon_surf = icon_font.render(text, True, accent)
                self.screen.blit(icon_surf, icon_surf.get_rect(center=icon_center))
            else:
                # Ponto neutro (não naipe de baralho) — só reforça a cor da categoria.
                pygame.draw.circle(self.screen, accent, icon_center, theme.px(16))
                pygame.draw.circle(self.screen, BG, icon_center, theme.px(7))

    @staticmethod
    def _tint(color, t: float):
        """Mistura `color` com o fundo escuro do painel — usado pro preenchimento sutil dos
        cartões da barra de estatística (borda e texto ficam na cor cheia, só o fundo é diluído)."""
        return tuple(int(BG[i] + (color[i] - BG[i]) * t) for i in range(3))

    def _draw_system_indicator(self) -> None:
        """"● SISTEMA OK" discreto no canto — verde só quando a última escrita no banco teve
        sucesso E a checagem periódica de saúde do SQLite (`self.system_ok`, ver a property)
        também passou. Não é telemetria, só um sinal local rápido de "o painel está realmente
        gravando" sem precisar abrir o admin ou ler log."""
        theme = self.theme
        color = GREEN if self.system_ok else RED
        r = theme.px(6)
        cx = theme.px(18)
        # Logo acima da barra de estatística, no vão vazio da coluna FRIO — o rodapé em si já é
        # ocupado pelos cartões coloridos até bem perto da borda.
        cy = theme.height - theme.px(_BOTTOM_BAR_PX) - theme.px(24)
        pygame.draw.circle(self.screen, color, (cx, cy), r)
        if not self.system_ok:
            font = theme.font(14, bold=True)
            label = font.render("SISTEMA COM FALHA", True, RED)
            self.screen.blit(label, (cx + r + theme.px(8), cy - label.get_height() // 2))

    # -- avisos/banners -------------------------------------------------------

    def _draw_overlays(self) -> None:
        theme = self.theme
        now = pygame.time.get_ticks()

        if self.clear_all_pending:
            secs_left = max(0, (self.clear_all_deadline - now) // 1000 + 1)
            # "REINICIAR SESSÃO", não "apagar memória": -97 zera o que aparece no painel, mas os
            # giros continuam salvos (soft delete) para auditoria/exportação — ver
            # _execute_clear_all / Database.clear_session. O texto na tela precisa dizer isso com
            # a mesma precisão que o código já garante, senão o operador é levado a acreditar que
            # a ação é mais destrutiva do que realmente é.
            key = self._draw_banner(
                f"REINICIAR SESSÃO? O painel zera (histórico fica arquivado). "
                f"ENTER confirma • ESC cancela ({secs_left}s)", RED,
                key="clear_all",
            )
        elif self.minus_buffer is not None:
            key = self._draw_banner(f"COMANDO: -{self.minus_buffer}_  (ENTER confirma • ESC cancela)", ORANGE, key="minus")
        elif self.input_buffer:
            key = self._draw_banner(f"NOVO RESULTADO: {self.input_buffer}", ORANGE, key=f"input:{self.input_buffer}")
        elif self.pending_undo:
            secs_left = max(0, (self.pending_undo_deadline - now) // 1000 + 1)
            key = self._draw_banner(
                f"Remover último resultado ({self.pending_undo_number})? DEL novamente ({secs_left}s)", RED,
                key="pending_undo",
            )
        elif now < self.flash_until and self.flash_text:
            key = self._draw_banner(
                self.flash_text, self.flash_color, key=f"flash:{self.flash_text}:{self.flash_until}",
                font_size=self.flash_font_size,
            )
        else:
            key = None

        if key != self._banner_key:
            self._banner_key = key
            if key is not None:
                self.banner_reveal = Tween(0.0, 1.0, _BANNER_ANIM_MS, ease_out_cubic)

    def _draw_banner(self, text: str, color, key: str, font_size: int = 28) -> str:
        theme = self.theme
        reveal = self.banner_reveal.value() if key == self._banner_key else 1.0
        reveal = max(0.0, min(1.0, reveal))

        font = theme.font(font_size, bold=True)
        surf = font.render(text, True, TEXT_PRIMARY)
        pad_x, pad_y = theme.px(22), theme.px(11)
        box = pygame.Rect(0, 0, surf.get_width() + pad_x * 2, surf.get_height() + pad_y * 2)
        base_bottom = theme.height - theme.px(_BOTTOM_BAR_PX) - theme.px(14)
        box.midbottom = (theme.width // 2, base_bottom + int((1 - reveal) * theme.px(14)))

        shadow_offset = theme.px(5)
        shadow = self._rect_shadow_surface(box.width, box.height, theme.px(8))
        shadow.set_alpha(int(255 * reveal))
        self.screen.blit(shadow, (box.left + shadow_offset, box.top + shadow_offset))

        panel = pygame.Surface(box.size, pygame.SRCALPHA)
        pygame.draw.rect(panel, (*PANEL_BG, 255), panel.get_rect(), border_radius=theme.px(8))
        pygame.draw.rect(panel, (*color, 255), panel.get_rect(), width=2, border_radius=theme.px(8))
        panel.blit(surf, surf.get_rect(center=(box.width // 2, box.height // 2)))
        panel.set_alpha(int(255 * reveal))
        self.screen.blit(panel, box.topleft)
        return key
