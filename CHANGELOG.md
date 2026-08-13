# Changelog

## 1.0.1 — 2026-08-13

Ajustes visuais/UX pedidos após os primeiros testes em servidor real:

- Rotação de tela em software (`config.screen_rotation`) para ambientes onde o driver de vídeo
  não gira sozinho (ex.: consoles de VM), além da rotação por firmware já suportada no Raspberry
  Pi real.
- Animação de revelação em tela cheia ao registrar um giro: fundo verde-gramado, círculo colorido
  pulsando suavemente, classificação completa (cor/paridade/faixa), beep curto — nunca bloqueia o
  teclado.
- Histórico central redesenhado em "raias" por cor (zigue-zague: cada linha é um giro, mais
  recente no topo, número só na raia da própria cor).
- Removida a faixa "GIROS DA SESSÃO"; "ÚLTIMO RESULTADO" ganhou destaque maior (+18,3%) com
  contorno branco; histórico ganhou mais linhas visíveis.
- Número da categoria "preto" volta a ser preenchido em preto de verdade (com borda branca) no
  placar principal; círculos/fundos que representam "preto" usam um cinza 85% dedicado, em vez de
  preto puro (quase invisível contra o fundo escuro do app).
- Badges FRIO/QUENTE: números maiores, brancos com borda preta; linha dourada no topo/base dos
  badges QUENTE, prateada nos FRIO.
- Sombra suave atrás dos badges, dos cartões da barra de estatística e do banner de avisos; brilho
  suave e breve no número que acabou de ser registrado — mais profundidade visual sem animações
  contínuas que distraiam o operador.
- Versão do sistema agora visível em `Funções administrativas > Informações da licença` e no log
  de inicialização.

## 1.0.0

Primeira versão completa: placar principal (três colunas FRIO/ÚLTIMO RESULTADO/QUENTE, limites de
aposta, barra de estatística), motor de estatísticas puro e testado, banco SQLite com auditoria e
soft-delete, licenciamento assinado por device ID (Ed25519), painel administrativo com PIN
(identificação, backups, exportação, auditoria, analytics, SMTP, rede, energia), relatórios
PDF/CSV/JSON assinados, splash de boot customizado, instalação automatizada para Raspberry Pi 3
via `scripts/install.sh`.
