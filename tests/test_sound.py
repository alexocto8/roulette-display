"""app/ui/sound.py: beep de alerta sintetizado em código (sem asset externo), tocado quando a
revelação em tela cheia aparece. Precisa nunca travar/levantar mesmo sem placa de som (o
conftest.py já força SDL_AUDIODRIVER=dummy pra todo o teste, o que exercita exatamente esse
caminho "sem áudio de verdade")."""
from __future__ import annotations

from unittest import mock

from app.ui import sound


def test_synthesize_beep_returns_nonempty_16bit_mono_samples():
    data = sound._synthesize_beep()
    assert len(data) > 0
    # 2 notas de 150ms a 22050Hz, 16 bits (2 bytes) por amostra -- tamanho aproximado esperado.
    expected_samples = int(sound._SAMPLE_RATE * 0.150) * 2
    assert abs(len(data) - expected_samples * 2) < 2000  # bytes = amostras * 2 (int16)


def test_ensure_ready_and_play_do_not_raise_under_dummy_audio_driver():
    sound._mixer_ready = False
    sound._beep_sound = None
    sound.ensure_ready()
    sound.play_reveal_beep()  # não deve levantar mesmo em driver dummy (sem som real)


def test_ensure_ready_degrades_gracefully_when_mixer_init_fails():
    sound._mixer_ready = False
    sound._beep_sound = None
    with mock.patch("pygame.mixer.init", side_effect=OSError("sem placa de som")):
        sound.ensure_ready()
    assert sound._mixer_ready is False
    sound.play_reveal_beep()  # continua um no-op seguro, nunca levanta

    # restaura pro resto da suíte (mixer_ready é global do módulo)
    sound._mixer_ready = False
    sound.ensure_ready()


def test_play_reveal_beep_survives_a_broken_sound_object():
    sound._mixer_ready = True
    broken = mock.Mock()
    broken.play.side_effect = RuntimeError("dispositivo sumiu")
    sound._beep_sound = broken
    sound.play_reveal_beep()  # não deve levantar

    sound._mixer_ready = False
    sound._beep_sound = None
    sound.ensure_ready()
