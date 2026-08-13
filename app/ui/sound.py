"""Som de alerta tocado quando a animação de revelação em tela cheia aparece — sintetizado em
código, sem depender de nenhum arquivo de áudio externo (mesmo espírito do resto do app: ícones de
naipe são desenhados, gráficos do relatório usam as primitivas do fpdf2 em vez de puxar uma lib de
imagem). Duas notas curtas em sequência, no estilo "beep de fila de atendimento" — discreto mas
feito pra chamar atenção.

Falha ao inicializar/tocar áudio (sem placa de som, sem driver, ambiente headless — comum em
Raspberry Pi sem áudio configurado, ou neste próprio ambiente de desenvolvimento) nunca pode
travar nem aparecer pro operador: só vai pro log, e a revelação continua funcionando inteiramente
sem som, no mesmo padrão defensivo já usado por app/delivery/printer_service.py."""
from __future__ import annotations

import array
import logging
import math

import pygame

logger = logging.getLogger("roulette.ui")

_SAMPLE_RATE = 22050
_mixer_ready = False
_beep_sound: pygame.mixer.Sound | None = None


def _synthesize_beep() -> bytes:
    """Duas notas curtas em subida (A5 -> D6, ~150ms cada) com fade in/out nas bordas de cada
    nota — sem o fade, uma descontinuidade abrupta de amplitude vira um "click" audível."""
    notes_hz = (880.0, 1175.0)
    note_ms = 150
    fade_fraction = 0.1
    samples = array.array("h")
    for hz in notes_hz:
        n = int(_SAMPLE_RATE * note_ms / 1000)
        fade_n = max(1, int(n * fade_fraction))
        for i in range(n):
            t = i / _SAMPLE_RATE
            fade = min(i / fade_n, (n - i) / fade_n, 1.0)
            value = math.sin(2 * math.pi * hz * t) * fade * 0.5
            samples.append(int(value * 32767))
    return samples.tobytes()


def ensure_ready() -> None:
    """Chamado uma vez, na inicialização do painel — inicializa o mixer e sintetiza o beep uma
    única vez (não a cada revelação, que rodaria a cada giro registrado). Nunca levanta."""
    global _mixer_ready, _beep_sound
    if _mixer_ready:
        return
    try:
        pygame.mixer.quit()  # descarta o mixer default que pygame.init() já pode ter aberto
        pygame.mixer.init(frequency=_SAMPLE_RATE, size=-16, channels=1)
        _beep_sound = pygame.mixer.Sound(buffer=_synthesize_beep())
        _mixer_ready = True
    except Exception:
        logger.warning("Áudio indisponível — a revelação em tela cheia vai funcionar sem som.")
        _mixer_ready = False


def play_reveal_beep() -> None:
    if not _mixer_ready or _beep_sound is None:
        return
    try:
        _beep_sound.play()
    except Exception:
        logger.warning("Falha ao tocar o beep de revelação")
