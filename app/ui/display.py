"""Main fullscreen display: the electronic roulette scoreboard itself.

Layout follows the client's approved visual design (mockup rounds v1-v17 of
`tools/mockup_ui.py`, iterated to final approval before any of this file was touched — see that
module's docstring history for the full design rationale): a full-height three-column structure —
FRIO (cold numbers) on the left, the current result centered, QUENTE (hot numbers, with the
casino logo underneath) on the right — with bet limits across the very top and seven individual
statistic cards (ÍMPAR/PAR/VERMELHO/ZERO/PRETO/MENOR/MAIOR) across the bottom. Colors are the
exact hex values the client specified. Cards/badges/the center circle use the pre-rendered PNGs in
`assets/ui/` (gold bezels, glows, chip badges — see `tools/build_ui_assets.py`), composited once
per distinct size and cached (`UiAssets`, `app/ui/assets.py`) rather than recomputed per frame.

A spin registration triggers a full-screen reveal animation (`_draw_reveal` and friends,
approved via `tools/mockup_reveal_animation.py`): a casino-logo splash, then the roulette wheel
(cropped from the client's reference art) spinning behind a partial dark gradient with the result
badge fading in, then the result held (pulsing, gold glow) before crossfading back to this normal
screen. See `_RevealPhase`/`_reveal_phase_at` below for the exact timeline.

Rendering stays cheap per frame (blits of pre-scaled/cached surfaces, text, a couple of short eased
tweens — no shaders, no video, no per-frame image rotation at full resolution — see
`_wheel_rotation_cache` for how the one genuinely expensive visual, the spinning wheel, is kept of
bounded cost) and the frame rate drops to `config.idle_fps` whenever nothing is actively
transitioning or being typed, which is what keeps CPU usage low on a Raspberry Pi 3 across the long
idle stretches between spins. See app/ui/animation.py for the tween/easing helpers used below.
"""
from __future__ import annotations

import logging
import math
import time

import pygame

from app.config import Config
from app.database.db import Database
from app.models import roulette_data
from app.services.backup_service import BackupService
from app.services.export_service import ExportService
from app.services.retention_service import RetentionService
from app.services.spin_service import DisplayState, SpinService
from app.ui import sound
from app.ui.admin import AdminPanel
from app.ui.animation import Tween, ease_out_back, ease_out_cubic
from app.ui.assets import UiAssets, load_image
from app.ui.rotation import create_screen
from app.ui.splash import show_splash
from app.ui.theme import (
    BG,
    BLACK,
    CYAN,
    GOLD,
    GREEN,
    NUMBER_COLOR_MAP,
    OFF_WHITE,
    ORANGE,
    PANEL_BG,
    PANEL_BORDER,
    RED,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
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
_NUMBER_POP_MS = 380  # "pop" sutil no número central ao trocar
_ADMIN_FADE_MS = 200
_BANNER_ANIM_MS = 200
_SPLASH_CROSSFADE_MS = 420

# -- animação de revelação em tela cheia (disparada a cada giro registrado) -------------------
#
# Timeline aprovada pelo cliente via `tools/mockup_reveal_animation.py` (vídeo revisado quadro a
# quadro antes de qualquer código aqui), fases sequenciais em milissegundos a partir do registro
# do giro:
#   0                    -> _REVEAL_LOGO_ZOOM_MS       logo cresce (zoom/splash) sozinho na tela
#   ...                  -> _REVEAL_LOGO_END_MS         logo segura, depois some com fade
#   _REVEAL_LOGO_END_MS  -> _REVEAL_CONTENT_START_MS    roleta+número+badges entram em fade-in
#                                                        (a roleta já vinha girando, invisível)
#   _REVEAL_CONTENT_START_MS -> _REVEAL_MS              número exibido, pulsando com glow dourado;
#                                                        a roleta gira livre e só desacelera/para
#                                                        nos últimos `_REVEAL_WHEEL_DECEL_MS`
# Mais um crossfade de `_REVEAL_GLOBAL_FADE_MS` entrando e saindo dessa cena a partir da tela
# normal (não um fade pro preto). Pedido explícito: enquanto a revelação está em tela, o sistema
# NÃO deve permitir registrar um número novo -- `_confirm_input` bloqueia o ENTER de confirmação
# durante toda essa janela (mantendo o que já foi digitado, o operador só precisa apertar ENTER de
# novo depois). Undo (`DEL DEL`/`-` `ENTER`) continua funcionando normalmente durante a revelação,
# só o registro de um giro NOVO é que fica bloqueado.
_REVEAL_GLOBAL_FADE_MS = 300
_REVEAL_LOGO_ZOOM_MS = 400
_REVEAL_LOGO_HOLD_MS = 10_000
_REVEAL_LOGO_FADE_MS = 600
_REVEAL_LOGO_END_MS = _REVEAL_LOGO_ZOOM_MS + _REVEAL_LOGO_HOLD_MS + _REVEAL_LOGO_FADE_MS  # 11 000
_REVEAL_CONTENT_FADE_MS = 300
_REVEAL_CONTENT_START_MS = _REVEAL_LOGO_END_MS + _REVEAL_CONTENT_FADE_MS  # 11 300
_REVEAL_NUMBER_DISPLAY_MS = 8000
_REVEAL_MS = _REVEAL_CONTENT_START_MS + _REVEAL_NUMBER_DISPLAY_MS  # 19 300 -- duração total
_REVEAL_WHEEL_DECEL_MS = 2000  # a roleta só desacelera/para nos últimos 2s da animação inteira
_REVEAL_WHEEL_DECEL_START_MS = _REVEAL_MS - _REVEAL_WHEEL_DECEL_MS
_REVEAL_WHEEL_SPIN_DEG_S = 480.0  # rápido, ~1.3 voltas/segundo -- "igual a roleta do jogo"
_REVEAL_PULSE_PERIOD_MS = 1200

# Rotacionar uma imagem circular grande (a roleta ocupa 60% da altura da tela) em tempo real, TODO
# frame, por ~14s de animação seria caro demais pra um Pi 3 -- em vez disso, um número pequeno de
# ângulos é pré-rotacionado UMA VEZ (numa resolução pequena, não na resolução final de tela) e
# cacheado em memória; a cada frame só se escolhe o ângulo mais próximo e faz UM `smoothscale` até
# o tamanho final (bem mais barato que rotacionar na resolução final). 36 passos = 10° de
# resolução -- imperceptível numa roleta girando rápido, e mesmo desacelerando só fica levemente
# "granulado" no último instante antes de parar. 500px de origem mantém o cache inteiro (36
# frames) em ~35MB, bem dentro do orçamento de memória do Pi 3.
_REVEAL_WHEEL_ROTATION_STEPS = 36
_REVEAL_WHEEL_SOURCE_PX = 500

# Histórico central: três raias verticais (preto/zero/vermelho) com nós/conectores dourados --
# tons diferentes do GOLD "cheio" usado em bordas/linhas de accent (mais claro/mais escuro,
# aprovados no mockup como o par nó+conector que dá o acabamento "trilho premium").
_NODE_GOLD = (214, 178, 104)
_CONNECTOR_GOLD = (196, 160, 92)

# Sombra suave atrás dos badges/cartões — pedido explícito do cliente pra dar profundidade/
# sofisticação. Preto semi-transparente (não um preto opaco genérico): sobre o fundo já bem escuro
# do app, opaco ficaria quase invisível ou criaria um degradê visível demais -- translúcido lê como
# profundidade sutil em qualquer fundo por baixo.
_SHADOW_ALPHA = 90
_SHADOW_COLOR = (0, 0, 0, _SHADOW_ALPHA)

# Bem menor que WatchdogSec do systemd (30s) — manda pelo menos umas 3x dentro da janela, prática
# recomendada do próprio systemd pra não arriscar perder um pulso por uma variação pontual de FPS.
_WATCHDOG_HEARTBEAT_MS = 8000
# Checagem de saúde do banco (indicador visual, não o watchdog do systemd): intervalo folgado de
# propósito — só precisa flagar "o SQLite parou de responder", não é uma métrica de performance.
_HEALTH_CHECK_MS = 45000
# Retenção de dados (30 dias por padrão, ver config.data_retention_days): checagem em SEGUNDOS de
# relógio de verdade (`time.time()`), não `pygame.time.get_ticks()` -- os ticks do pygame são
# inteiros de milissegundos que passam a exigir cuidado extra depois de ~24 dias de uptime contínuo
# (exatamente o cenário de uma mesa 24/7), e essa checagem não precisa de precisão de frame de
# jeito nenhum. `enforce_retention()` é idempotente (não faz nada se não há giro além do corte),
# então checar a cada poucas horas é seguro e barato.
_RETENTION_CHECK_S = 6 * 3600


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


def _draw_text(surface: pygame.Surface, font: pygame.font.Font, text: str,
                pos: tuple[int, int], color, anchor: str = "topleft") -> pygame.Rect:
    """Texto simples (sem contorno) posicionado por âncora (`midtop`, `topright`, `center`...),
    devolvendo o Rect renderizado -- usado pelo cabeçalho/painéis/estatísticas pra encadear
    posições (ex.: "o próximo elemento começa onde este terminou") sem recalcular tamanhos."""
    surf = font.render(text, True, color)
    rect = surf.get_rect(**{anchor: pos})
    surface.blit(surf, rect)
    return rect


class RouletteDisplay:
    def __init__(self, config: Config, db: Database):
        self.config = config
        self.db = db
        self.service = SpinService(db, config)
        self.backup_service = BackupService(db, config)
        self.export_service = ExportService(db, config)
        self.retention_service = RetentionService(db, config)

        # Janela padrão de desenvolvimento também em retrato — é o layout que realmente importa.
        # `create_screen` também aplica `config.screen_rotation`, se configurado (ver
        # app/ui/rotation.py) — nenhuma outra mudança é necessária aqui, `pygame.display.flip()`
        # já sai rotacionado quando for o caso.
        self.screen, self.theme = create_screen(config, f"{config.casino_name} - {config.roulette_name}")
        self.clock = pygame.time.Clock()

        # Assets pré-renderizados (bordas/bisel dourado, chips, glows, roleta recortada do print
        # de referência do cliente) -- gerados uma única vez por `tools/build_ui_assets.py`,
        # cacheados aqui por (nome, tamanho): cada composição cara acontece só na primeira vez que
        # aquele tamanho é pedido, todo frame depois é só um `blit()` (ver `app/ui/assets.py`).
        self.ui_assets = UiAssets(config.resolve(config.assets_dir) / "ui")

        # Logo real do estabelecimento: carregada em resolução original uma única vez; a versão
        # ESCALADA é recalculada só quando o tamanho-alvo pedido muda (ver `_scaled_logo`) --
        # normalmente uma única vez também, já que a área que ela precisa preencher (rodapé do
        # painel QUENTE) não muda depois que a tela está de pé.
        self._logo_raw = load_image(config.resolve(config.assets_dir) / "logo.png")
        self._logo_scaled_cache: dict[tuple[int, int], pygame.Surface] = {}

        # Sintetiza o beep uma única vez aqui, não a cada revelação — falha (sem placa de som) só
        # desativa o som, nunca trava o boot (ver app/ui/sound.py).
        sound.ensure_ready()

        self.admin = AdminPanel(config, self.service, self.backup_service, self.export_service,
                                 self.retention_service)
        self.admin_open = False
        self.admin_fade = Tween(0.0, 0.0, 1)  # 0 = fechado, 1 = totalmente aberto

        # Revelação em tela cheia pós-giro — timer de renderização (ver `_reveal_active` e as
        # constantes `_REVEAL_*` acima para as fases). `_confirm_input` bloqueia o registro de um
        # giro novo enquanto ela está ativa (pedido explícito) — digitar continua funcionando, só
        # o ENTER de confirmação fica sem efeito até a animação acabar.
        self.reveal_number: int | None = None
        self.reveal_color: str | None = None
        self.reveal_started_at = 0
        # Snapshot da tela normal capturado no INSTANTE em que a revelação começa -- usado como
        # pano de fundo do crossfade de entrada (`_REVEAL_GLOBAL_FADE_MS`). O crossfade de saída
        # não precisa de snapshot: a essa altura o giro já foi registrado e `_render_main_screen`
        # desenha o estado atual (que já é o que deve aparecer por baixo do fade-out).
        self._reveal_entry_backdrop: pygame.Surface | None = None

        # Roleta do print de referência do cliente, rotacionada em ângulos discretos (não a cada
        # frame) -- ver `_wheel_rotation_frame` para o motivo (custo de rotacionar uma imagem
        # circular grande em tempo real, todo frame, por ~14s de animação, seria pesado demais
        # pra um Pi 3; um `smoothscale` por frame a partir de um cache pequeno de ângulos
        # pré-rotacionados é a mesma técnica "computa uma vez, reusa" do resto do projeto,
        # aplicada aqui de um jeito que cabe no orçamento de memória do Pi 3).
        self._wheel_rotation_cache: list[pygame.Surface] | None = None
        # Degradê escuro (70%->0%, esquerda->centro da tela) por cima da roleta -- construído uma
        # única vez (tamanho fixo pra um dado tamanho de tela) e reusado em todo frame da revelação.
        self._reveal_gradient_cache: pygame.Surface | None = None

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

        # Sombras suaves atrás de cartões/banners -- pedido explícito do cliente pra dar
        # profundidade/sofisticação à interface. Cacheadas por tamanho (não mudam frame a frame,
        # só quando a janela é redimensionada/rotacionada), pra não alocar uma Surface nova a cada
        # frame no Pi 3.
        self._rect_shadow_cache: dict[tuple[int, int, int], pygame.Surface] = {}
        self._glow_cache: dict[int, pygame.Surface] = {}

        # Fundo em degradê (mesmo asset `card_gradient.png`) recortado numa máscara arredondada --
        # usado por praticamente todo cartão/badge/painel/pill da tela nova. Cacheado pelo
        # resultado FINAL (já com a máscara aplicada) por (largura, altura, raio): como o layout é
        # fixo pra um dado tamanho de tela, o mesmo (w, h, raio) se repete todo frame -- computado
        # uma vez, reusado o resto da execução.
        self._card_mask_cache: dict[tuple[int, int, int], pygame.Surface] = {}
        self._card_bg_cache: dict[tuple[int, int, int], pygame.Surface] = {}

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

    def _scaled_logo(self, max_size: tuple[int, int]) -> pygame.Surface | None:
        """Logo real do estabelecimento, redimensionada pra caber em `max_size` preservando a
        proporção -- cacheada por tamanho-alvo (o painel QUENTE só pede um tamanho por execução,
        já que a área disponível não muda depois que a tela está de pé)."""
        if self._logo_raw is None:
            return None
        key = max_size
        cached = self._logo_scaled_cache.get(key)
        if cached is not None:
            return cached
        ratio = min(max_size[0] / self._logo_raw.get_width(), max_size[1] / self._logo_raw.get_height())
        size = (max(1, int(self._logo_raw.get_width() * ratio)), max(1, int(self._logo_raw.get_height() * ratio)))
        scaled = pygame.transform.smoothscale(self._logo_raw, size)
        self._logo_scaled_cache[key] = scaled
        return scaled

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
        self._last_retention_check = time.time()
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
                wall_now = time.time()
                if _should_heartbeat(wall_now, self._last_retention_check, _RETENTION_CHECK_S):
                    self._enforce_retention()
                    self._last_retention_check = wall_now

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
            # está na animação de revelação (~19s, ver as constantes `_REVEAL_*`). O buffer
            # digitado fica intacto -- o operador só precisa apertar ENTER de novo quando a
            # revelação acabar, sem perder o que já tinha digitado. A própria tela cheia da
            # revelação já deixa claro pro operador por que o ENTER não teve efeito, então não
            # duplicamos isso com um banner (que nem apareceria: a revelação substitui o frame
            # inteiro, ver `_render`).
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
        # Revelação em tela cheia (ver `_reveal_active`/as constantes `_REVEAL_*`). Só chegamos
        # aqui se ela NÃO estava ativa (bloqueada logo no topo de `_confirm_input`) -- então isto
        # sempre inicia um ciclo novo, nunca sobrescreve uma revelação em andamento. O snapshot da
        # tela normal (já com este giro refletido, por causa do `_refresh_state()` acima) vira o
        # pano de fundo do crossfade de entrada da animação.
        self.reveal_number = spin.number
        self.reveal_color = spin.color
        self.reveal_started_at = pygame.time.get_ticks()
        self._reveal_entry_backdrop = self._capture_main_screen()
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

    def _enforce_retention(self) -> None:
        """Chamada periodicamente (`_RETENTION_CHECK_S`), nunca por frame. Uma falha aqui (disco
        cheio na hora de exportar o arquivo, por exemplo) não pode derrubar o painel -- só fica no
        log, e a tentativa seguinte (algumas horas depois) tenta de novo."""
        try:
            self.retention_service.enforce_retention()
        except Exception:
            logger.exception("Retenção de dados falhou")

    # -- rendering ---------------------------------------------------------------

    def _layout_bands(self) -> tuple[int, int, int]:
        """Faixas fixas (cabeçalho/rodapé) que o layout aprovado usa em toda tela normal --
        centralizado aqui porque tanto `_render_main_screen` quanto o banner (`_draw_banner`,
        que precisa saber onde o rodapé de estatísticas começa pra se ancorar acima dele)
        precisam do mesmo cálculo."""
        theme = self.theme
        header_h = theme.px(round(theme.height * 0.105))
        stats_h = theme.px(round(theme.height * 0.155))
        gap = theme.px(12)
        return header_h, stats_h, gap

    def _render(self) -> None:
        screen = self.screen
        theme = self.theme
        now = pygame.time.get_ticks()

        if self._reveal_active(now):
            self._draw_reveal(now - self.reveal_started_at)
            if self.admin_open or self.admin_fade.value() > 0.001:
                self.admin.render(screen, theme, reveal=self.admin_fade.value())
            pygame.display.flip()
            return

        self._render_main_screen(screen)

        if self.admin_open or self.admin_fade.value() > 0.001:
            self.admin.render(screen, theme, reveal=self.admin_fade.value())

        pygame.display.flip()

    def _render_main_screen(self, surface: pygame.Surface) -> None:
        """A tela normal (cabeçalho, colunas, estatísticas, banners) desenhada em `surface` --
        extraído de `_render` pra também servir de pano de fundo do crossfade de entrada/saída da
        revelação (ver `_draw_reveal`/`_capture_main_screen`), sem duplicar a lógica de layout."""
        theme = self.theme
        surface.fill(BG)
        header_h, stats_h, gap = self._layout_bands()
        body_top = header_h + gap
        stats_top = theme.height - stats_h
        body_bottom = stats_top - gap

        self._draw_header(surface, pygame.Rect(0, 0, theme.width, header_h))
        self._draw_body(surface, pygame.Rect(0, body_top, theme.width, body_bottom - body_top))
        self._draw_stats(surface, pygame.Rect(0, stats_top, theme.width, stats_h))
        self._draw_overlays(surface)

    def _capture_main_screen(self) -> pygame.Surface:
        surface = pygame.Surface(self.screen.get_size())
        self._render_main_screen(surface)
        return surface

    # -- helpers de desenho reusados por várias seções (cabeçalho, painéis, revelação) --------

    def _card_mask(self, w: int, h: int, radius: int) -> pygame.Surface:
        key = (w, h, radius)
        mask = self._card_mask_cache.get(key)
        if mask is None:
            mask = pygame.Surface((w, h), pygame.SRCALPHA)
            pygame.draw.rect(mask, (255, 255, 255, 255), mask.get_rect(), border_radius=radius)
            self._card_mask_cache[key] = mask
        return mask

    def _blit_card_bg(self, surface: pygame.Surface, rect: pygame.Rect, radius: int) -> None:
        """Fundo em degradê sutil (mesmo asset em todo cartão/badge/painel/pill da tela),
        recortado numa máscara arredondada -- cacheado pelo resultado final por (largura, altura,
        raio), computado uma única vez por tamanho e reusado todo frame depois disso."""
        key = (rect.width, rect.height, radius)
        bg = self._card_bg_cache.get(key)
        if bg is None:
            bg = self.ui_assets.scaled("card_gradient.png", (rect.width, rect.height)).copy()
            bg.blit(self._card_mask(rect.width, rect.height, radius), (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
            self._card_bg_cache[key] = bg
        surface.blit(bg, rect.topleft)

    def _blit_hbar(self, surface: pygame.Surface, name: str, rect: pygame.Rect) -> None:
        img = self.ui_assets.scaled(name, (max(1, rect.width), max(1, rect.height)))
        surface.blit(img, rect.topleft)

    def _rect_shadow_surface(self, w: int, h: int, radius: int) -> pygame.Surface:
        """Sombra de um cartão retangular (cantos arredondados), cacheada por tamanho (ver
        comentário no `__init__` sobre não alocar Surface nova a cada frame no Pi 3) -- usada no
        banner de avisos."""
        key = (w, h, radius)
        surf = self._rect_shadow_cache.get(key)
        if surf is None:
            surf = pygame.Surface((w, h), pygame.SRCALPHA)
            pygame.draw.rect(surf, _SHADOW_COLOR, surf.get_rect(), border_radius=radius)
            self._rect_shadow_cache[key] = surf
        return surf

    def _glow_surface(self, radius: int) -> pygame.Surface:
        """Círculo branco cheio, cacheado por raio -- a opacidade real é ajustada por frame via
        `set_alpha()` em `_draw_center` (dá o brilho suave que aparece e desvanece junto com o
        "pop" do número, sem alocar Surface nova a cada frame)."""
        surf = self._glow_cache.get(radius)
        if surf is None:
            surf = pygame.Surface((radius * 2, radius * 2), pygame.SRCALPHA)
            pygame.draw.circle(surf, (*TEXT_PRIMARY, 255), (radius, radius), radius)
            self._glow_cache[radius] = surf
        return surf

    @staticmethod
    def _reveal_tags(number: int, color_name: str) -> list[tuple[str, tuple[int, int, int]]]:
        """Classificação (cor + ímpar/par + faixa) usada tanto pelas pills abaixo do círculo
        central quanto pela revelação em tela cheia — zero só mostra "ZERO" (paridade/faixa não
        se aplicam a zero, mesma convenção já usada em app/models/roulette_data.py)."""
        color_tag = {"red": ("VERMELHO", RED), "black": ("PRETO", OFF_WHITE), "green": ("ZERO", GREEN)}
        tags = [color_tag[color_name]]
        if number != 0:
            parity = roulette_data.parity_of(number)
            tags.append(("ÍMPAR", CYAN) if parity == "odd" else ("PAR", CYAN))
            range_ = roulette_data.range_of(number)
            tags.append(("MENOR", ORANGE) if range_ == "low" else ("MAIOR", ORANGE))
        return tags

    # -- cabeçalho: limites de aposta + indicador de sistema -------------------------------

    def _draw_header(self, surface: pygame.Surface, rect: pygame.Rect) -> None:
        """APOSTA MÍN. no canto esquerdo, APOSTA MÁX. no canto direito, cada uma em um cartão sem
        borda; "SISTEMA OK" discreto no canto inferior direito, colado na linha dourada que
        fecha o cabeçalho -- layout aprovado em `tools/mockup_ui.py`."""
        theme = self.theme
        header_h = rect.height
        card_top = theme.px(8)
        card_w, card_h = theme.px(268), theme.px(158)
        label_font, value_font = theme.font(34, bold=True), theme.font(66, bold=True)

        left_rect = pygame.Rect(theme.px(20), card_top, card_w, card_h)
        right_rect = pygame.Rect(theme.width - theme.px(20) - card_w, card_top, card_w, card_h)
        for r, (label, value) in (
            (left_rect, ("APOSTA MÍN.", f"{self.config.currency} {self.config.min_bet}")),
            (right_rect, ("APOSTA MÁX.", f"{self.config.currency} {self.config.max_bet}")),
        ):
            self._blit_card_bg(surface, r, theme.px(10))
            _draw_text(surface, label_font, label, (r.centerx, r.top + theme.px(18)), TEXT_SECONDARY, anchor="midtop")
            _draw_text(surface, value_font, value, (r.centerx, r.top + theme.px(58)), ORANGE, anchor="midtop")

        # "● SISTEMA OK": verde só quando as duas coisas forem verdade -- a última escrita no
        # banco teve sucesso E a checagem periódica de saúde do SQLite (`self.system_ok`)
        # também passou. Não é telemetria, só um sinal local rápido de "o painel está realmente
        # gravando" sem precisar abrir o admin ou ler log.
        dot_c = (theme.width - theme.px(22), header_h - theme.px(16))
        dot_color = GREEN if self.system_ok else RED
        pygame.draw.circle(surface, dot_color, dot_c, theme.px(6))
        label_text = "SISTEMA OK" if self.system_ok else "SISTEMA COM FALHA"
        _draw_text(surface, theme.font(17, bold=True), label_text,
                   (dot_c[0] - theme.px(12), dot_c[1]), TEXT_MUTED, anchor="midright")

        self._blit_hbar(surface, "accent_gold_glow.png", pygame.Rect(0, header_h - theme.px(10), theme.width, theme.px(20)))
        self._blit_hbar(surface, "accent_gold.png", pygame.Rect(0, header_h - 2, theme.width, 3))

    # -- corpo: FRIO (esquerda) / último resultado (centro) / QUENTE (direita) ---------------

    def _draw_body(self, surface: pygame.Surface, rect: pygame.Rect) -> None:
        theme = self.theme
        col_frio_w = round(theme.width * 0.28)
        col_quente_w = round(theme.width * 0.28)
        col_center_w = theme.width - col_frio_w - col_quente_w
        side_pad = theme.px(10)

        frio_panel = pygame.Rect(0, rect.top, col_frio_w, rect.height).inflate(-side_pad * 2, 0)
        quente_panel = pygame.Rect(col_frio_w + col_center_w, rect.top, col_quente_w, rect.height).inflate(-side_pad * 2, 0)
        center_col = pygame.Rect(col_frio_w, rect.top, col_center_w, rect.height)

        self._draw_side_panel(surface, frio_panel, "FRIO", "MENOS RECORRENTES", "accent_cold.png",
                               "ambient_glow_blue.png", "cold_icon.png", self.state.cold,
                               self.config.cold_numbers_count, "GIROS", CYAN, show_logo=False)
        self._draw_side_panel(surface, quente_panel, "QUENTE", "MAIS RECORRENTES", "accent_hot.png",
                               "ambient_glow_red.png", "hot_icon.png", self.state.hot,
                               self.config.hot_numbers_count, "VEZES", RED, show_logo=True)

        center_content = pygame.Rect(center_col.left + theme.px(8), center_col.top,
                                      center_col.width - theme.px(16), center_col.height)
        self._draw_center(surface, center_content)

    def _draw_side_panel(self, surface: pygame.Surface, panel_rect: pygame.Rect, title: str, subtitle: str,
                          accent_asset: str, glow_asset: str, icon_asset: str,
                          entries: list[tuple[int, int]], slot_count: int, unit: str, accent_color,
                          show_logo: bool = False) -> None:
        """Painel FRIO/QUENTE de ALTURA COMPLETA: título + ícone + subtítulo, depois até
        `slot_count` linhas ranqueadas ("1º", "2º"...) com o número real dentro de um chip na cor
        real da roleta (não a cor de acento da coluna) e a contagem à direita. `entries` pode ter
        menos itens que `slot_count` (sessão ainda no início) -- linhas sem entrada ficam em
        branco, sem quebrar o espaçamento das linhas seguintes."""
        theme = self.theme
        radius = theme.px(16)
        self._blit_card_bg(surface, panel_rect, radius)

        # Iluminação ambiente discreta na metade inferior do painel -- alpha baixo (ver
        # `make_ambient_glow` em tools/build_ui_assets.py), um único blit, nunca recalculada.
        glow_size = int(panel_rect.width * 1.1)
        glow = self.ui_assets.scaled(glow_asset, (glow_size, glow_size))
        glow_cy = panel_rect.top + int(panel_rect.height * 0.74)
        surface.blit(glow, (panel_rect.centerx - glow_size // 2, glow_cy - glow_size // 2))

        glow_bar_asset = accent_asset.replace(".png", "_glow.png")
        self._blit_hbar(surface, glow_bar_asset, pygame.Rect(panel_rect.left, panel_rect.top - theme.px(8),
                                                               panel_rect.width, theme.px(16)))
        self._blit_hbar(surface, accent_asset, pygame.Rect(panel_rect.left, panel_rect.top, panel_rect.width, theme.px(4)))
        pygame.draw.rect(surface, PANEL_BORDER, panel_rect, width=1, border_radius=radius)

        y = panel_rect.top + theme.px(24)
        title_font = theme.font(44, bold=True)
        subtitle_font = theme.font(24, bold=True)
        title_r = _draw_text(surface, title_font, title, (panel_rect.centerx, y), accent_color, anchor="midtop")

        # Ícone centralizado verticalmente com o TÍTULO (não esticado até o subtítulo).
        icon_size = theme.px(40)
        icon_img = self.ui_assets.scaled(icon_asset, (icon_size, icon_size))
        icon_x = panel_rect.centerx - title_r.width // 2 - icon_size - theme.px(10)
        surface.blit(icon_img, (icon_x, title_r.centery - icon_size // 2))

        y = title_r.bottom + theme.px(6)
        subtitle_r = _draw_text(surface, subtitle_font, subtitle, (panel_rect.centerx, y), TEXT_SECONDARY, anchor="midtop")

        y = subtitle_r.bottom + theme.px(16)
        self._blit_hbar(surface, "separator_fade.png",
                         pygame.Rect(panel_rect.left + theme.px(16), y, panel_rect.width - theme.px(32), 2))
        y += theme.px(16)

        row_h = theme.px(148)
        rank_font = theme.font(31, bold=True)
        num_font = theme.font(40, bold=True)
        count_font = theme.font(47, bold=True)
        unit_font = theme.font(21, bold=True)
        badge_d = theme.px(78)
        inset = theme.px(24)
        number_chip_asset = {"black": "number_chip_black.png", "red": "number_chip_red.png", "green": "number_chip_green.png"}

        rows = max(1, slot_count)
        for i in range(rows):
            row = pygame.Rect(panel_rect.left, y, panel_rect.width, row_h)

            if i < len(entries):
                num, count = entries[i]
                # Posição do ranking como texto simples ("1º", "2º"...) -- só o NÚMERO da roleta
                # em si fica dentro de um círculo, na cor real daquele número.
                rank_r = _draw_text(surface, rank_font, f"{i + 1}º", (row.left + inset, row.centery),
                                     accent_color, anchor="midleft")

                badge_cx = rank_r.right + theme.px(16) + badge_d // 2
                badge = self.ui_assets.scaled(number_chip_asset[roulette_data.color_of(num)], (badge_d, badge_d))
                surface.blit(badge, (badge_cx - badge_d // 2, row.centery - badge_d // 2))
                _blit_outlined_text(surface, num_font, str(num), (badge_cx, row.centery),
                                     fill=OFF_WHITE, outline=BLACK, outline_px=0)

                val_x = row.right - inset
                count_r = _draw_text(surface, count_font, str(count), (val_x, row.centery - theme.px(13)),
                                      accent_color, anchor="topright")
                _draw_text(surface, unit_font, unit, (val_x, count_r.bottom + theme.px(2)), TEXT_MUTED, anchor="topright")

            if i < rows - 1:
                self._blit_hbar(surface, "separator_fade.png",
                                 pygame.Rect(panel_rect.left + theme.px(16), row.bottom, panel_rect.width - theme.px(32), 2))
            y = row.bottom

        if show_logo:
            # Ancorada perto do rodapé (não centralizada no vão vazio) -- a logo fica "grudada"
            # na base do painel, preenchendo o espaço que sobra abaixo da última linha.
            margin_bottom = theme.px(28)
            zone_w = panel_rect.width - theme.px(16)
            zone_h = max(1, panel_rect.bottom - margin_bottom - y)
            logo = self._scaled_logo((zone_w, zone_h))
            if logo is not None:
                logo_cy = max(y + logo.get_height() // 2, panel_rect.bottom - margin_bottom - logo.get_height() // 2)
                surface.blit(logo, logo.get_rect(center=(panel_rect.centerx, logo_cy)))

    # -- último resultado (círculo central) + histórico -------------------------------------

    def _draw_center(self, surface: pygame.Surface, rect: pygame.Rect) -> None:
        theme = self.theme
        last = self.state.last_spin
        now = pygame.time.get_ticks()

        y = rect.top + theme.px(10)
        title_font = theme.font(48, bold=True)
        title_r = _draw_text(surface, title_font, "ÚLTIMO RESULTADO", (rect.centerx, y), OFF_WHITE, anchor="midtop")

        if self.awaiting_spin:
            blink_on = (now // 500) % 2 == 0
            if blink_on:
                # Sem "●" Unicode: a fonte padrão do pygame não garante esse glifo -- desenhado
                # como um círculo vetorial ao lado do texto.
                badge_font = theme.font(22, bold=True)
                badge_surf = badge_font.render("AGUARDANDO NOVO GIRO", True, ORANGE)
                badge_y = title_r.bottom + theme.px(10)
                dot_r = theme.px(5)
                text_rect = badge_surf.get_rect(midtop=(rect.centerx + dot_r + theme.px(4), badge_y))
                pygame.draw.circle(surface, ORANGE, (text_rect.left - dot_r - theme.px(4), text_rect.centery), dot_r)
                surface.blit(badge_surf, text_rect)

        if last is None:
            hint_font = theme.font(30, bold=True)
            hint = hint_font.render("AGUARDANDO", True, TEXT_MUTED)
            hint2 = hint_font.render("PRIMEIRO GIRO", True, TEXT_MUTED)
            hint_y = title_r.bottom + theme.px(80)
            surface.blit(hint, hint.get_rect(midtop=(rect.centerx, hint_y)))
            surface.blit(hint2, hint2.get_rect(midtop=(rect.centerx, hint_y + hint.get_height() + theme.px(4))))
            history_rect = pygame.Rect(rect.left, hint_y + hint.get_height() * 2 + theme.px(40),
                                        rect.width, rect.bottom - (hint_y + hint.get_height() * 2 + theme.px(40)))
            self._draw_center_history(surface, history_rect)
            return

        last_color = last.color
        badge_asset = {"red": "result_badge_red.png", "black": "result_badge_black.png",
                       "green": "result_badge_green.png"}[last_color]

        # "Pop" sutil ao trocar de resultado (ease-out com leve overshoot) -- só o TAMANHO
        # VISUAL do aro/número escala; a posição (cy/tag_y, calculada a partir do diâmetro BASE)
        # fica parada, senão o histórico abaixo pularia de lugar a cada giro.
        elapsed = now - self.number_anim_start
        t = min(1.0, elapsed / _NUMBER_POP_MS) if elapsed >= 0 else 1.0
        pop = ease_out_back(t) if elapsed < _NUMBER_POP_MS else 1.0
        pop_scale = 0.88 + 0.12 * pop

        base_diameter = min(int(rect.width * 0.86), int(theme.px(380)))
        badge_diameter = round(base_diameter * pop_scale)
        badge_size = int(badge_diameter * 1.60)  # o PNG já inclui a margem do halo dourado
        cx = rect.centerx
        cy = title_r.bottom + theme.px(34) + int(base_diameter * 0.60)

        if elapsed < _NUMBER_POP_MS:
            glow_alpha = int(130 * (1.0 - t))
            if glow_alpha > 0:
                glow_r = int(base_diameter * 0.6)
                glow = self._glow_surface(glow_r)
                glow.set_alpha(glow_alpha)
                surface.blit(glow, (cx - glow_r, cy - glow_r))

        badge = self.ui_assets.scaled(badge_asset, (badge_size, badge_size))
        surface.blit(badge, (cx - badge_size // 2, cy - badge_size // 2))

        num_font = theme.font(int(badge_diameter * 0.76), bold=True)
        _blit_outlined_text(surface, num_font, str(last.number), (cx, cy), fill=OFF_WHITE, outline=BLACK, outline_px=0)

        # O aro dourado se estende bem além do círculo preenchido (halo/bisel do asset) -- a
        # folga é medida a partir de ~0.60*diâmetro (a borda externa visível do aro), não da
        # metade do diâmetro do preenchimento.
        tag_y = cy + int(base_diameter * 0.60) + theme.px(34)
        tags = self._reveal_tags(last.number, last_color)
        pill_font = theme.font(26, bold=True)
        pill_gap = theme.px(10)
        pill_h = theme.px(42)
        widths = [pill_font.size(label)[0] + theme.px(28) for label, _ in tags]
        px_x = cx - (sum(widths) + pill_gap * (len(tags) - 1)) // 2
        for (label, color), pw in zip(tags, widths):
            pill = pygame.Rect(px_x, tag_y, pw, pill_h)
            self._blit_card_bg(surface, pill, theme.px(18))
            pygame.draw.rect(surface, color, pill, width=2, border_radius=theme.px(18))
            _draw_text(surface, pill_font, label, pill.center, color, anchor="center")
            px_x += pw + pill_gap

        hist_top = tag_y + pill_h + theme.px(18)
        history_rect = pygame.Rect(rect.left, hist_top, rect.width, rect.bottom - hist_top)
        self._draw_center_history(surface, history_rect)

    def _draw_center_history(self, surface: pygame.Surface, rect: pygame.Rect) -> None:
        """REGRA FUNCIONAL IMUTÁVEL: três raias -- preto esquerda, zero centro, vermelho direita
        -- mais recente no topo, uma linha por giro, nunca duas listas paralelas, nunca
        interligadas numa linha central. Cada raia é uma linha dourada fina com fade nas duas
        pontas, um nó dourado em cada posição, ligado à sua bolinha (ou ao marcador vazio) por um
        conector horizontal curto. Numerais SEMPRE off-white, independente da cor do chip.
        Limitado a quantas linhas couberem na altura disponível -- nunca uma lista infinita."""
        theme = self.theme
        if rect.height <= 0:
            return
        history = self.state.history  # mais recente primeiro

        y = rect.top
        _draw_text(surface, theme.font(32, bold=True), "HISTÓRICO", (rect.centerx, y), TEXT_SECONDARY, anchor="midtop")
        y += theme.px(40)

        lane_x = {
            "black": rect.left + rect.width // 4,
            "green": rect.centerx,
            "red": rect.right - rect.width // 4,
        }
        chip_asset = {"black": "history_chip_black.png", "green": "history_chip_green.png", "red": "history_chip_red.png"}

        lanes_top = y
        lanes_bottom = rect.bottom
        d = theme.px(76)
        row_h = int(d * 1.28)
        n_rows = max(1, (lanes_bottom - lanes_top) // row_h)
        visible = history[:n_rows]

        spine_h = n_rows * row_h
        if spine_h > 0:
            fade_src = self.ui_assets.scaled("accent_gold.png", (spine_h, theme.px(3)))
            fade_img = pygame.transform.rotate(fade_src, 90)
            for x in lane_x.values():
                surface.blit(fade_img, (x - fade_img.get_width() // 2, lanes_top))

        hist_font = theme.font(int(d * 0.54), bold=True)
        chip_size = int(d * 1.30)
        node_r = theme.px(4)
        connector_w = theme.px(2)
        for i in range(n_rows):
            yy = lanes_top + i * row_h + row_h // 2
            spin = visible[i] if i < len(visible) else None
            active_lane = spin.color if spin is not None else None
            for lane, x in lane_x.items():
                is_active = lane == active_lane
                tick_half = (chip_size // 2 + theme.px(9)) if is_active else theme.px(10)
                pygame.draw.line(surface, _CONNECTOR_GOLD, (x - tick_half, yy), (x + tick_half, yy), connector_w)
                pygame.draw.circle(surface, _NODE_GOLD, (x, yy), node_r)
                if is_active:
                    chip = self.ui_assets.scaled(chip_asset[lane], (chip_size, chip_size))
                    surface.blit(chip, (x - chip_size // 2, yy - chip_size // 2))
                    _blit_outlined_text(surface, hist_font, str(spin.number), (x, yy),
                                        fill=OFF_WHITE, outline=BLACK, outline_px=1)

    # -- barra de estatísticas (rodapé) ------------------------------------------------------

    def _draw_stats(self, surface: pygame.Surface, rect: pygame.Rect) -> None:
        """Sete cartões INDIVIDUAIS (ÍMPAR/PAR/VERMELHO/ZERO/PRETO/MENOR/MAIOR), cada um com
        borda na cor da própria categoria, percentual, contagem bruta real ("141 / 300") e uma
        barra de progresso -- layout aprovado em `tools/mockup_ui.py`. PRETO usa off-white (sem
        "preto" na paleta, igual ao número central); MENOR/MAIOR usam laranja como categoria
        própria (não ligada a QUENTE, que é vermelho). Contagens/percentuais vêm direto das
        `BucketStats` já calculadas pelo serviço -- nenhum dado novo, só uma segunda forma de
        expressar o mesmo número que o percentual já mostra."""
        theme = self.theme
        s = self.state
        window = min(s.total_spins, self.config.statistics_window)

        title_font = theme.font(30, bold=True)
        subtitle_font = theme.font(24, bold=True)
        title_s = title_font.render("ESTATÍSTICAS", True, TEXT_PRIMARY)
        subtitle_s = subtitle_font.render(f"ÚLTIMOS {window} GIROS", True, TEXT_SECONDARY)
        mid_gap = theme.px(16)
        dot_r = theme.px(3)
        total_w = title_s.get_width() + mid_gap * 2 + dot_r * 2 + subtitle_s.get_width()
        max_h = max(title_s.get_height(), subtitle_s.get_height())
        y = rect.top
        x = rect.centerx - total_w // 2
        surface.blit(title_s, (x, y + (max_h - title_s.get_height()) // 2))
        x += title_s.get_width() + mid_gap
        pygame.draw.circle(surface, GOLD, (x + dot_r, y + max_h // 2), dot_r)
        x += dot_r * 2 + mid_gap
        surface.blit(subtitle_s, (x, y + (max_h - subtitle_s.get_height()) // 2))

        line_y = y + max_h // 2
        edge_gap = theme.px(22)
        line_w = (rect.width - total_w) // 2 - edge_gap - theme.px(24)
        if line_w > theme.px(20):
            gold_line = self.ui_assets.scaled("accent_gold.png", (line_w, 3))
            left_x = rect.centerx - total_w // 2 - edge_gap
            right_x = rect.centerx + total_w // 2 + edge_gap
            surface.blit(gold_line, (left_x - line_w, line_y - 1))
            surface.blit(pygame.transform.flip(gold_line, True, False), (right_x, line_y - 1))
            pygame.draw.circle(surface, GOLD, (left_x - theme.px(8), line_y), dot_r)
            pygame.draw.circle(surface, GOLD, (right_x + theme.px(8), line_y), dot_r)

        cards_top = y + max_h + theme.px(22)
        gutter = theme.px(7)
        radius = theme.px(12)
        unit_w = rect.width / 7
        card_h = max(1, min(theme.px(150), rect.bottom - cards_top))

        cells = [
            ("ÍMPAR", s.parity.percentage("odd"), CYAN, s.parity.counts.get("odd", 0), s.parity.total),
            ("PAR", s.parity.percentage("even"), CYAN, s.parity.counts.get("even", 0), s.parity.total),
            ("VERMELHO", s.color.percentage("red"), RED, s.color.counts.get("red", 0), s.color.total),
            ("ZERO", s.color.percentage("green"), GREEN, s.color.counts.get("green", 0), s.color.total),
            ("PRETO", s.color.percentage("black"), OFF_WHITE, s.color.counts.get("black", 0), s.color.total),
            ("MENOR", s.range_.percentage("low"), ORANGE, s.range_.counts.get("low", 0), s.range_.total),
            ("MAIOR", s.range_.percentage("high"), ORANGE, s.range_.counts.get("high", 0), s.range_.total),
        ]
        value_font = theme.font(38, bold=True)
        label_font = theme.font(17, bold=True)
        frac_font = theme.font(19, bold=True)

        for i, (label, pct, accent, count, total) in enumerate(cells):
            outer = pygame.Rect(round(i * unit_w), cards_top, round(unit_w) + 1, card_h)
            card = outer.inflate(-gutter * 2, 0)
            self._blit_card_bg(surface, card, radius)
            pygame.draw.rect(surface, accent, card, width=2, border_radius=radius)

            _draw_text(surface, value_font, f"{pct}%", (card.centerx, card.top + theme.px(14)), accent, anchor="midtop")
            _draw_text(surface, label_font, label, (card.centerx, card.top + theme.px(58)), TEXT_SECONDARY, anchor="midtop")
            _draw_text(surface, frac_font, f"{count} / {total}", (card.centerx, card.top + theme.px(82)),
                       TEXT_PRIMARY, anchor="midtop")

            bar_h = theme.px(6)
            bar_top = min(card.top + theme.px(118), card.bottom - bar_h - theme.px(10))
            bar_rect = pygame.Rect(card.left + theme.px(14), bar_top, card.width - theme.px(28), bar_h)
            pygame.draw.rect(surface, (46, 50, 56), bar_rect, border_radius=bar_h // 2)
            fill_w = max(bar_h, int(bar_rect.width * min(1.0, pct / 100)))
            pygame.draw.rect(surface, accent, pygame.Rect(bar_rect.left, bar_rect.top, fill_w, bar_h),
                              border_radius=bar_h // 2)

    # -- revelação em tela cheia (pós-giro) ---------------------------------------------------
    #
    # Ver as constantes `_REVEAL_*` no topo do módulo para a timeline completa (aprovada via
    # `tools/mockup_reveal_animation.py`, revisada quadro a quadro antes de qualquer código
    # aqui): logo sozinho -> roleta/número/badges entram em fade-in (roleta desacelera só nos
    # últimos segundos) -> número exibido, pulsando -- com um crossfade real (não pro preto)
    # entrando e saindo dessa cena a partir da tela normal.

    def _draw_reveal(self, elapsed: int) -> None:
        screen = self.screen

        if elapsed < _REVEAL_GLOBAL_FADE_MS:
            blend = elapsed / _REVEAL_GLOBAL_FADE_MS
        elif elapsed > _REVEAL_MS - _REVEAL_GLOBAL_FADE_MS:
            blend = (_REVEAL_MS - elapsed) / _REVEAL_GLOBAL_FADE_MS
        else:
            blend = 1.0
        blend = max(0.0, min(1.0, blend))

        if blend >= 0.999:
            self._draw_reveal_content(screen, elapsed)
            return

        # Crossfade: o pano de fundo é a tela normal de verdade -- o snapshot capturado no
        # instante em que a revelação começou (entrada) ou o estado atual, renderizado ao vivo
        # (saída) -- nunca um fade genérico pro preto.
        if elapsed < _REVEAL_GLOBAL_FADE_MS and self._reveal_entry_backdrop is not None:
            screen.blit(self._reveal_entry_backdrop, (0, 0))
        else:
            self._render_main_screen(screen)

        content = pygame.Surface(screen.get_size())
        self._draw_reveal_content(content, elapsed)
        content.set_alpha(round(255 * blend))
        screen.blit(content, (0, 0))

    def _draw_reveal_content(self, surface: pygame.Surface, elapsed: int) -> None:
        theme = self.theme
        surface.fill(BG)  # mesmo fundo da tela normal, sem trocar de cor/tema -- pedido explícito
        alpha = self._reveal_scene_alpha(elapsed)
        if alpha > 0:
            pulse_scale, glow_t = self._reveal_pulse_state(elapsed)
            layer = pygame.Surface((theme.width, theme.height), pygame.SRCALPHA)
            self._draw_reveal_wheel(layer, elapsed)
            layer.blit(self._reveal_gradient(), (0, 0))
            self._draw_reveal_badge(layer, pulse_scale, glow_t)
            layer.set_alpha(alpha)
            surface.blit(layer, (0, 0))
        self._draw_reveal_logo(surface, elapsed)

    def _reveal_scene_alpha(self, elapsed: int) -> int:
        """Roleta + degradê + badge/número/pills só existem DEPOIS que o logo suma -- fade-in
        único de `_REVEAL_CONTENT_FADE_MS`, tudo junto (uma única camada com um alpha só, não
        cada elemento fadeando separado)."""
        if elapsed <= _REVEAL_LOGO_END_MS:
            return 0
        if elapsed >= _REVEAL_CONTENT_START_MS:
            return 255
        return int(255 * (elapsed - _REVEAL_LOGO_END_MS) / _REVEAL_CONTENT_FADE_MS)

    def _reveal_pulse_state(self, elapsed: int) -> tuple[float, float]:
        """Só pulsa durante a janela de exibição do número (depois que tudo termina de entrar).
        `scale` é a leve variação de tamanho do badge/número (~3.5%); `glow_t` (0..1) modula a
        intensidade do glow dourado extra na borda, respirando junto."""
        if elapsed < _REVEAL_CONTENT_START_MS:
            return 1.0, 0.0
        phase = 2 * math.pi * (elapsed - _REVEAL_CONTENT_START_MS) / _REVEAL_PULSE_PERIOD_MS
        scale = 1.0 + 0.035 * math.sin(phase)
        glow_t = (math.sin(phase) + 1) / 2
        return scale, glow_t

    def _wheel_rotation_frames(self) -> list[pygame.Surface]:
        frames = self._wheel_rotation_cache
        if frames is None:
            raw = self.ui_assets.raw("roulette_wheel.png")
            base = pygame.transform.smoothscale(raw, (_REVEAL_WHEEL_SOURCE_PX, _REVEAL_WHEEL_SOURCE_PX))
            step_deg = 360 / _REVEAL_WHEEL_ROTATION_STEPS
            frames = [pygame.transform.rotozoom(base, i * step_deg, 1.0) for i in range(_REVEAL_WHEEL_ROTATION_STEPS)]
            self._wheel_rotation_cache = frames
        return frames

    def _reveal_wheel_angle(self, elapsed: int) -> float:
        """Gira à velocidade total o tempo todo -- só desacelera de velocidade total até ZERO ao
        longo de `_REVEAL_WHEEL_DECEL_MS`, nos últimos segundos da animação inteira (não mais
        atrelada ao fade-in do número). Desaceleração linear na VELOCIDADE -> posição é a
        integral dessa velocidade, dando uma curva suave até parar exatamente no fim."""
        t = elapsed / 1000.0
        decel_start_s = _REVEAL_WHEEL_DECEL_START_MS / 1000.0
        decel_s = _REVEAL_WHEEL_DECEL_MS / 1000.0
        if t <= decel_start_s:
            return -(t * _REVEAL_WHEEL_SPIN_DEG_S) % 360
        u = min(1.0, (t - decel_start_s) / decel_s)
        base = decel_start_s * _REVEAL_WHEEL_SPIN_DEG_S
        extra = _REVEAL_WHEEL_SPIN_DEG_S * decel_s * (u - u * u / 2)
        return -(base + extra) % 360

    def _draw_reveal_wheel(self, surface: pygame.Surface, elapsed: int) -> None:
        """Roleta extraída do print de referência do cliente: 3/5 da altura da tela, 60% cortada
        pra fora da borda esquerda -- só a fatia direita (40%) fica visível. Um único
        `smoothscale` por frame a partir do ângulo pré-rotacionado mais próximo (ver
        `_wheel_rotation_frames`), nunca uma rotação em tempo real na resolução final."""
        theme = self.theme
        diameter = round(theme.height * 0.6)
        frames = self._wheel_rotation_frames()
        step_deg = 360 / _REVEAL_WHEEL_ROTATION_STEPS
        idx = round(self._reveal_wheel_angle(elapsed) / step_deg) % _REVEAL_WHEEL_ROTATION_STEPS
        frame = pygame.transform.smoothscale(frames[idx], (diameter, diameter))
        cy = theme.height / 2
        cx = -0.10 * diameter
        surface.blit(frame, frame.get_rect(center=(round(cx), round(cy))))

    def _reveal_gradient(self) -> pygame.Surface:
        """70% escuro -> 0% escuro, esquerda -> centro da tela (metade esquerda, onde a roleta
        gira) -- construído uma única vez por tamanho de tela, reusado em todo frame."""
        if self._reveal_gradient_cache is not None:
            return self._reveal_gradient_cache
        theme = self.theme
        half_w = theme.width // 2
        grad = pygame.Surface((half_w, theme.height), pygame.SRCALPHA)
        max_alpha = int(255 * 0.70)
        for x in range(half_w):
            a = int(max_alpha * (1 - x / half_w))
            pygame.draw.line(grad, (0, 0, 0, a), (x, 0), (x, theme.height))
        self._reveal_gradient_cache = grad
        return grad

    def _draw_reveal_badge(self, surface: pygame.Surface, pulse_scale: float, glow_t: float) -> None:
        """Círculo/aro IGUAIS ao "ÚLTIMO RESULTADO" da tela principal (mesmo asset
        `result_badge_*.png`) -- mantém o padrão visual entre as duas telas. Número dimensionado
        pra caber DENTRO do círculo (mesma proporção `diâmetro * 0.76` do painel principal), sem
        exceder. Centralizado na tela (vertical e horizontal), badges empilhados embaixo. Durante
        a janela de exibição, `pulse_scale`/`glow_t` fazem o conjunto respirar (fora dela vêm
        neutros e não mudam nada)."""
        theme = self.theme
        number = self.reveal_number
        color = self.reveal_color

        badge_asset = {"red": "result_badge_red.png", "black": "result_badge_black.png",
                       "green": "result_badge_green.png"}[color]

        base_diameter = theme.px(900)  # era 520 no badge antigo -- +73%, acima do mínimo de +70% pedido
        diameter = round(base_diameter * pulse_scale)
        badge_size = int(diameter * 1.60)
        cx, cy = theme.width // 2, theme.height // 2

        glow_size = int(base_diameter * 2.2)
        glow = self.ui_assets.scaled("reveal_glow_blue.png", (glow_size, glow_size))
        surface.blit(glow, (cx - glow_size // 2, cy - glow_size // 2))

        if glow_t > 0:
            pulse_glow_size = int(diameter * 1.30)
            pulse_glow = self.ui_assets.scaled("pulse_glow_gold.png", (pulse_glow_size, pulse_glow_size))
            pulse_glow.set_alpha(int(70 + 160 * glow_t))
            surface.blit(pulse_glow, (cx - pulse_glow_size // 2, cy - pulse_glow_size // 2))

        badge = self.ui_assets.scaled(badge_asset, (badge_size, badge_size))
        surface.blit(badge, (cx - badge_size // 2, cy - badge_size // 2))

        num_font = theme.font(int(diameter * 0.76), bold=True)
        _blit_outlined_text(surface, num_font, str(number), (cx, cy), fill=OFF_WHITE, outline=BLACK, outline_px=0)

        tags = self._reveal_tags(number, color)
        pill_font = theme.font(30, bold=True)
        pill_w = theme.px(340)
        pill_h = theme.px(72)
        pill_gap = theme.px(16)
        tag_y = cy + int(base_diameter * 0.60) + theme.px(34)
        for label, tcolor in tags:
            pill = pygame.Rect(cx - pill_w // 2, tag_y, pill_w, pill_h)
            self._blit_card_bg(surface, pill, theme.px(20))
            pygame.draw.rect(surface, tcolor, pill, width=2, border_radius=theme.px(20))
            _draw_text(surface, pill_font, label, pill.center, tcolor, anchor="center")
            tag_y += pill_h + pill_gap

    def _draw_reveal_logo(self, surface: pygame.Surface, elapsed: int) -> None:
        """Zoom/splash: cresce pequeno e centralizado até o tamanho de destaque (ease-out,
        sensação de "pop" rápido), segura sozinho na tela, some com fade -- ver as constantes
        `_REVEAL_LOGO_*`. A versão em escala TOTAL é cacheada (`_scaled_logo`) e só recebe
        `set_alpha()` durante o "segura"/fade (nenhum recálculo por frame nessa janela, que é a
        maior parte da fase do logo); só o "zoom" em si (bem curto) escala a cada frame."""
        if elapsed >= _REVEAL_LOGO_END_MS or self._logo_raw is None:
            return
        theme = self.theme
        target_w = int(theme.width * 0.62)
        ratio = target_w / self._logo_raw.get_width()
        target_h = int(self._logo_raw.get_height() * ratio)
        full = self._scaled_logo((target_w, target_h))
        if full is None:
            return

        if elapsed < _REVEAL_LOGO_ZOOM_MS:
            scale = 0.12 + 0.88 * ease_out_cubic(elapsed / _REVEAL_LOGO_ZOOM_MS)
            w, h = max(1, int(full.get_width() * scale)), max(1, int(full.get_height() * scale))
            frame = pygame.transform.smoothscale(full, (w, h))
            alpha = 255
        else:
            frame = full
            if elapsed < _REVEAL_LOGO_ZOOM_MS + _REVEAL_LOGO_HOLD_MS:
                alpha = 255
            else:
                fade_t = (elapsed - _REVEAL_LOGO_ZOOM_MS - _REVEAL_LOGO_HOLD_MS) / _REVEAL_LOGO_FADE_MS
                alpha = int(255 * (1 - min(1.0, fade_t)))

        frame.set_alpha(alpha)
        rect = frame.get_rect(center=(theme.width // 2, theme.height // 2))
        surface.blit(frame, rect)

    # -- avisos/banners -------------------------------------------------------

    def _draw_overlays(self, surface: pygame.Surface) -> None:
        now = pygame.time.get_ticks()

        if self.clear_all_pending:
            secs_left = max(0, (self.clear_all_deadline - now) // 1000 + 1)
            # "REINICIAR SESSÃO", não "apagar memória": -97 zera o que aparece no painel, mas os
            # giros continuam salvos (soft delete) para auditoria/exportação — ver
            # _execute_clear_all / Database.clear_session. O texto na tela precisa dizer isso com
            # a mesma precisão que o código já garante, senão o operador é levado a acreditar que
            # a ação é mais destrutiva do que realmente é.
            key = self._draw_banner(
                surface,
                f"REINICIAR SESSÃO? O painel zera (histórico fica arquivado). "
                f"ENTER confirma • ESC cancela ({secs_left}s)", RED,
                key="clear_all",
            )
        elif self.minus_buffer is not None:
            key = self._draw_banner(surface, f"COMANDO: -{self.minus_buffer}_  (ENTER confirma • ESC cancela)",
                                     ORANGE, key="minus")
        elif self.input_buffer:
            key = self._draw_banner(surface, f"NOVO RESULTADO: {self.input_buffer}", ORANGE,
                                     key=f"input:{self.input_buffer}")
        elif self.pending_undo:
            secs_left = max(0, (self.pending_undo_deadline - now) // 1000 + 1)
            key = self._draw_banner(
                surface, f"Remover último resultado ({self.pending_undo_number})? DEL novamente ({secs_left}s)", RED,
                key="pending_undo",
            )
        elif now < self.flash_until and self.flash_text:
            key = self._draw_banner(
                surface, self.flash_text, self.flash_color, key=f"flash:{self.flash_text}:{self.flash_until}",
                font_size=self.flash_font_size,
            )
        else:
            key = None

        if key != self._banner_key:
            self._banner_key = key
            if key is not None:
                self.banner_reveal = Tween(0.0, 1.0, _BANNER_ANIM_MS, ease_out_cubic)

    def _draw_banner(self, surface: pygame.Surface, text: str, color, key: str, font_size: int = 28) -> str:
        theme = self.theme
        reveal = self.banner_reveal.value() if key == self._banner_key else 1.0
        reveal = max(0.0, min(1.0, reveal))

        font = theme.font(font_size, bold=True)
        surf = font.render(text, True, TEXT_PRIMARY)
        pad_x, pad_y = theme.px(22), theme.px(11)
        box = pygame.Rect(0, 0, surf.get_width() + pad_x * 2, surf.get_height() + pad_y * 2)
        _, stats_h, _ = self._layout_bands()
        base_bottom = theme.height - stats_h - theme.px(14)
        box.midbottom = (theme.width // 2, base_bottom + int((1 - reveal) * theme.px(14)))

        shadow_offset = theme.px(5)
        shadow = self._rect_shadow_surface(box.width, box.height, theme.px(8))
        shadow.set_alpha(int(255 * reveal))
        surface.blit(shadow, (box.left + shadow_offset, box.top + shadow_offset))

        panel = pygame.Surface(box.size, pygame.SRCALPHA)
        pygame.draw.rect(panel, (*PANEL_BG, 255), panel.get_rect(), border_radius=theme.px(8))
        pygame.draw.rect(panel, (*color, 255), panel.get_rect(), width=2, border_radius=theme.px(8))
        panel.blit(surf, surf.get_rect(center=(box.width // 2, box.height // 2)))
        panel.set_alpha(int(255 * reveal))
        surface.blit(panel, box.topleft)
        return key
