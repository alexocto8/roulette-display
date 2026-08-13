"""Tela genérica de falha — usada quando o app não consegue nem chegar ao painel principal (banco
não abre, erro fatal não tratado no loop gráfico). Sem isso, esses casos deixavam o processo
simplesmente morrer com `main.py` retornando um código de erro: a tela ficava preta/voltava pro
console por um instante entre cada tentativa do `Restart=always` — nenhuma informação pro operador,
e pareceria "quebrado" mesmo quando o systemd está reiniciando normalmente.

Mesmo padrão de app/ui/license_screen.py (janela pygame própria, sem depender de Database/AdminPanel
— o ponto é funcionar mesmo quando o banco é exatamente o que está falhando). Mostra a mensagem por
um período fixo e depois retorna, deixando o processo terminar e o `Restart=always` do systemd
tentar de novo — não tenta ele mesmo "consertar" nada nem vira um loop de retry paralelo.

Nunca mostra stack trace, caminho de arquivo ou detalhe técnico — isso vai só pro log (o chamador
loga antes de chamar esta função)."""
from __future__ import annotations

import pygame

from app.config import Config
from app.ui.rotation import create_screen
from app.ui.theme import BG, RED, TEXT_MUTED, TEXT_PRIMARY


def show_failsafe(config: Config, title: str, subtitle: str, hold_seconds: float = 6.0) -> None:
    screen, theme = create_screen(config, config.casino_name)

    clock = pygame.time.Clock()
    elapsed = 0.0
    while elapsed < hold_seconds:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit()
                return

        screen.fill(BG)
        cx, cy = theme.width // 2, theme.height // 2

        title_font = theme.font(46, bold=True)
        title_surf = title_font.render(title, True, RED)
        screen.blit(title_surf, title_surf.get_rect(center=(cx, cy - theme.px(40))))

        subtitle_font = theme.font(26, bold=True)
        subtitle_surf = subtitle_font.render(subtitle, True, TEXT_PRIMARY)
        screen.blit(subtitle_surf, subtitle_surf.get_rect(center=(cx, cy + theme.px(20))))

        hint_font = theme.font(18)
        hint = hint_font.render("O painel tentará reiniciar automaticamente.", True, TEXT_MUTED)
        screen.blit(hint, hint.get_rect(center=(cx, theme.height - theme.px(60))))

        pygame.display.flip()
        elapsed += clock.tick(10) / 1000.0

    pygame.quit()
