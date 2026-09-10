"""Rotação de tela em software, opcional (`config.screen_rotation`) — para quando o monitor físico
é paisagem mas será montado de lado (retrato) e o driver de vídeo não faz a rotação sozinho.

Em produção real no Raspberry Pi (KMSDRM), a rotação já é feita pelo kernel via
`video=HDMI-A-1:1080x1920@60,rotate=90` no cmdline.txt (ver README, seção "Orientação retrato") —
`pygame.display.set_mode((0, 0), pygame.FULLSCREEN)` já enxerga a resolução rotacionada sozinho, e
`screen_rotation` deve ficar em 0 (padrão) nesse caso, sem precisar deste módulo fazer nada.

Este caminho existe para os casos em que a rotação por hardware/driver não está disponível — por
exemplo, o console de várias VMs (VMware/ESXi, algumas soluções de VNC) expõe uma tela virtual cujo
RandR não implementa rotação de verdade (`xrandr --rotate` falha com BadMatch nesses casos).

Ponto único usado pelas três telas que abrem sua própria janela pygame (painel principal, tela de
licença, tela de failsafe) — evita repetir a mesma sequência de `pygame.init()`/`set_mode()` três
vezes, e garante que a rotação (quando configurada) se aplica às três, não só ao painel principal.
"""
from __future__ import annotations

import time

import pygame

from app.config import Config
from app.ui.theme import Theme

# Quantas vezes tentar abrir o modo de vídeo antes de desistir, e quanto esperar entre
# tentativas -- o KMSDRM pode falhar de forma passageira logo após um restart do serviço
# (systemd mata o processo anterior mas o kernel/driver vc4 pode levar uma fração de segundo pra
# liberar o dispositivo DRM de verdade; tentar de novo imediatamente já resolve a maioria dos
# casos reais vistos em campo num Pi 3).
_VIDEO_INIT_ATTEMPTS = 5
_VIDEO_INIT_RETRY_S = 0.5

_VALID_ROTATIONS = (0, 90, 180, 270)

# Capturado uma vez, na importação do módulo — antes de qualquer chamada a `create_screen()` ter
# chance de substituir `pygame.display.flip`. Sem isso, uma segunda chamada (ex.: tela de licença
# seguida do painel principal, no mesmo processo) capturaria a versão já rotacionada como "original"
# e giraria o quadro duas vezes.
_ORIGINAL_FLIP = pygame.display.flip


def create_screen(config: Config, caption: str, dev_size: tuple[int, int] = (650, 1000)):
    """Substitui a sequência repetida de `pygame.init()` + `pygame.display.set_mode()` +
    `Theme(...)` nas três telas independentes do app. Devolve `(screen, theme)` — `screen` é a
    surface que o chamador deve desenhar; quando `screen_rotation` é 90/270 ela já vem com
    largura/altura trocadas em relação à tela física (é isso que faz `Theme.portrait` detectar
    retrato corretamente numa tela física paisagem), e `pygame.display.flip()` passa a girar o
    quadro final antes de mostrar — nenhuma outra mudança é necessária em quem já chama
    `pygame.display.flip()` (display.py, splash.py, license_screen.py, failsafe_screen.py)."""
    pygame.init()

    flags = pygame.FULLSCREEN if config.fullscreen else 0
    physical_size = (0, 0) if config.fullscreen else dev_size
    real_screen = _open_display(physical_size, flags)
    pygame.display.set_caption(caption)
    # Só depois que o modo de vídeo está de pé de verdade -- setar o mouse antes disso é o que
    # gerava o "video system not initialized" no Pi 3 quando a 1ª tentativa (com vsync) falhava.
    pygame.mouse.set_visible(not config.hide_cursor)

    rotation = config.screen_rotation if config.screen_rotation in _VALID_ROTATIONS else 0
    if rotation == 0:
        # Desfaz um hook instalado por uma tela anterior no mesmo processo (ex.: tela de licença
        # com rotação, seguida do painel principal sem rotação) — não deveria acontecer na prática
        # já que `screen_rotation` é um valor único por instalação, mas mantém o comportamento
        # correto mesmo assim.
        pygame.display.flip = _ORIGINAL_FLIP
        return real_screen, Theme(*real_screen.get_size())

    w, h = real_screen.get_size()
    logical_size = (h, w) if rotation in (90, 270) else (w, h)
    logical = pygame.Surface(logical_size)
    _install_rotated_flip(real_screen, logical, rotation)
    return logical, Theme(*logical.get_size())


def _open_display(physical_size: tuple[int, int], flags: int) -> pygame.Surface:
    """Abre o modo de vídeo com `vsync=1` (necessário no KMSDRM pra evitar "Could not queue
    pageflip: -22" em loop -- sem sincronizar com o vblank real, o loop principal pode pedir o
    próximo quadro antes do anterior terminar de verdade). Duas camadas de tolerância a falha,
    as duas encontradas em campo num Pi 3:

    1. Se o pedido com `vsync=1` falhar (nem todo driver/backend aceita -- ex.: SDL_VIDEODRIVER=
       dummy nos testes), tenta de novo sem vsync, mas SÓ depois de desligar e religar o módulo
       de vídeo -- só chamar `set_mode` de novo em cima da falha anterior deixava o vídeo "meio
       inicializado" e qualquer chamada seguinte (até `pygame.mouse.set_visible`) estourava
       "video system not initialized".
    2. Se AMBAS as tentativas falharem, espera um pouco e tenta a sequência inteira de novo --
       o KMSDRM pode falhar de forma passageira logo após um `systemctl restart` (o processo
       anterior às vezes não libera o dispositivo DRM instantaneamente)."""
    last_error: pygame.error | None = None
    for attempt in range(_VIDEO_INIT_ATTEMPTS):
        if attempt > 0:
            time.sleep(_VIDEO_INIT_RETRY_S)
            pygame.display.quit()
            pygame.display.init()
        try:
            return pygame.display.set_mode(physical_size, flags, vsync=1)
        except pygame.error as exc:
            last_error = exc
            pygame.display.quit()
            pygame.display.init()
            try:
                return pygame.display.set_mode(physical_size, flags)
            except pygame.error as exc2:
                last_error = exc2
    raise last_error


def _install_rotated_flip(real_screen: pygame.Surface, logical: pygame.Surface, rotation: int) -> None:
    """`pygame.transform.rotate` gira sentido anti-horário para ângulos positivos. 90 e 270 são
    oferecidas como as duas opções (mesmo espírito do parâmetro `rotate=` do kernel) porque qual
    delas bate com o sentido físico de montagem do monitor não dá pra adivinhar por software —
    só testando uma e, se a imagem ficar de cabeça pra baixo/de lado errado, trocando pela outra."""

    def rotated_flip() -> None:
        rotated = pygame.transform.rotate(logical, rotation)
        real_screen.blit(rotated, (0, 0))
        _ORIGINAL_FLIP()

    pygame.display.flip = rotated_flip
