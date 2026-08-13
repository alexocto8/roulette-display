# Painel de Roleta — Raspberry Pi 3

**Versão atual: 1.0.2** (ver [`CHANGELOG.md`](CHANGELOG.md)).

Registrador/placar eletrônico de roleta física de cassino. O operador digita o número sorteado
em um teclado numérico USB; o painel (fullscreen, em um monitor/TV próximo à mesa) atualiza
imediatamente número atual, histórico, estatísticas, números quentes/frios e identidade visual
do cassino. Funciona 100% offline, com persistência em SQLite e recuperação automática após
queda de energia ou travamento.

Este README é a referência técnica/arquitetural do projeto. Para uso do dia a dia e instalação,
veja os guias dedicados em [`docs/`](docs/):

- [`docs/FUNCIONALIDADES.md`](docs/FUNCIONALIDADES.md) ([PDF](docs/FUNCIONALIDADES.pdf)) — manual
  de uso: tela principal, atalhos de teclado, menu administrativo, licenciamento.
- [`docs/INSTALACAO_RASPBERRY_PI3.md`](docs/INSTALACAO_RASPBERRY_PI3.md)
  ([PDF](docs/INSTALACAO_RASPBERRY_PI3.pdf)) — passo a passo de instalação num Raspberry Pi 3
  físico, do cartão SD em branco ao equipamento em produção.

## 1. Arquitetura escolhida

**Python 3 + pygame (SDL2), sem X11/desktop, rodando direto via KMSDRM.**

Avaliei três alternativas antes de decidir:

| Opção | Veredito |
|---|---|
| Electron / navegador (Chromium kiosk) | Descartado. Um Pi 3 tem 1 GB de RAM e uma CPU ARM Cortex-A53 fraca; Chromium sozinho já consome 200-400 MB e boa parte da CPU só para existir. Incompatível com "extremamente leve" e "boot rápido". |
| PySide6/PyQt (Qt for Python) | Funciona, mas é pesado para o Pi 3 (biblioteca Qt inteira, compilação/instalação lenta em ARM, más vezes sem wheel pré-compilado) e normalmente ainda depende de X11 ou Wayland rodando por baixo — ou seja, mais uma camada (desktop/compositor) que o painel não precisa. |
| **pygame (SDL2) direto no framebuffer/DRM (`SDL_VIDEODRIVER=kmsdrm`)** | **Escolhido.** SDL2 desenha diretamente no `/dev/dri`, sem X11, sem window manager, sem navegador. Isso elimina *inteiramente* a camada de desktop que o requisito pede para nunca aparecer — não é "esconder o desktop", é não ter desktop nenhum rodando. Menor uso de RAM/CPU, boot mais rápido, systemd mais simples (não depende de `graphical.target`, X11 ou display manager). |

Consequência prática: o serviço systemd usa `WantedBy=multi-user.target` (modo texto), e o
Raspberry Pi OS recomendado é a versão **Lite** (sem desktop) — ela nem tenta subir X11, então não
há nada para o cliente ver além do nosso próprio splash e painel.

### Separação motor de resultados / interface

O código é dividido em camadas que não se conhecem por acidente:

- `app/models` e `app/statistics`: domínio puro (cor/par-ímpar/dúzia/coluna, frequência,
  quente/frio). Zero I/O, zero pygame, zero SQLite — só funções testáveis.
- `app/database`: acesso a dados (SQLite/WAL).
- `app/services`: regra de negócio (`SpinService`, `BackupService`, `ExportService`,
  `power_service`) — é o único ponto que a interface consulta.
- `app/ui`: pygame. Só desenha o que `SpinService.get_display_state()` devolve.

Essa separação é o que permite, no futuro, colocar 5/10/30 Raspberry Pi em mesas diferentes e
sincronizar tudo com um servidor central (API REST + Postgres) **sem reescrever o painel**: basta
trocar a implementação de `Database`/`SpinService` por uma que fale com o servidor, mantendo a
mesma interface. Veja a seção 9 (Roadmap).

### Layout retrato, identidade visual e transições

O layout principal é **retrato** ("tela em pé"), calcado literalmente no console físico de
referência do cliente (fotografado/filmado por eles e usado aqui como fonte visual): fundo
azul-marinho, **laranja** como cor de destaque só das badges "QUENTE", **ciano** para "FRIO" e os
limites de aposta, badges grandes em vez de listas de texto, e uma barra de porcentagem no rodapé
com uma célula colorida por categoria (ÍMPAR/PAR/MENOR/MAIOR em branco/cinza claro, VERMELHO/ZERO
na cor real, PRETO em âmbar — preto puro seria ilegível). Paisagem continua funcionando como
layout secundário para desenvolvimento numa janela normal.

**O número atual (no placar e na revelação) usa a cor real da roleta**: vermelho para números
vermelhos, verde para zero, e **preto de verdade** (`BLACK`, com contorno branco pra dar contraste
contra o fundo escuro) para números pretos — pedido explícito do cliente, que achava o branco usado
antes (recurso pra contornar a falta de contorno na época) confuso, já que "preto" não parecia
preto. Não é uma cor de acento genérica. Já **círculos/fundos** que representam a categoria "preto"
(o círculo da revelação em tela cheia) usam um **cinza 85%** (`GRAY_85`, `(38,38,38)`) em vez de
`BLACK` puro — preto puro sobre uma forma preenchida grande fica quase invisível contra o fundo
escuro do app sem nada que dê contraste, então essas formas usam esse cinza deliberadamente um
pouco mais claro (ainda lido como "preto/escuro", nunca confundido com as outras cores) — diferente
do número em si, que tem contorno claro garantindo o contraste mesmo sendo `BLACK` puro.

Os **badges trapezoidais FRIO/QUENTE** (`RouletteDisplay._draw_trapezoid`/`_draw_badge_column`)
têm o número maior (era 62% da altura do badge, agora 80%) e, como o "último resultado", em
**branco com contorno preto** em vez do texto escuro sólido de antes (`TEXT_ON_ACCENT`, removido) —
mais legível de longe contra o preenchimento vibrante (ciano/vermelho) do badge. Cada badge também
ganhou uma **linha fina no topo e na base**: dourada nos badges QUENTE (vermelhos), prateada nos
FRIO (ciano) — `GOLD`/`SILVER` em `app/ui/theme.py`, passadas como `accent_line` pro trapézio;
puramente decorativo, não muda nenhum dado exibido.

O elemento mais característico da referência é a **revelação em tela cheia**
(`RouletteDisplay._draw_full_reveal`): ao confirmar um giro, por 5 segundos (`_REVEAL_MS`) o painel
mostra, em tela cheia, fundo verde-gramado (`_FELT_GREEN` — deliberadamente diferente do verde vivo
usado pro indicador de zero/sistema-ok), um círculo grande (proporcional à tela, pra ler de longe)
na cor real do número (vermelho/preto/verde), sempre **centralizado horizontal e verticalmente**
(não muda de posição por cor), com o número em branco e contorno preto dentro dele, e a
classificação (VERMELHO/PRETO/ZERO + ÍMPAR/PAR + MENOR/MAIOR — zero só mostra ZERO, mesma
convenção de `app/models/roulette_data.py`) logo abaixo, com fonte proporcional ao tamanho do
círculo (não um tamanho fixo), pulsando suavemente (`_REVEAL_PULSE_HZ`) o tempo todo. Um beep
curto (duas notas, sintetizado em código — ver `app/ui/sound.py`, sem depender de nenhum arquivo
de áudio) toca quando a revelação aparece; falha de áudio (sem placa de som) nunca impede o resto
de funcionar. **Bloqueia o registro de um novo giro** (pedido explícito, revertendo uma versão
anterior "puramente visual" desta mesma funcionalidade): enquanto a revelação está em tela,
`ENTER` não confirma um número novo -- `RouletteDisplay._confirm_input` ignora a tentativa,
mantendo os dígitos já digitados intactos, então o operador só precisa apertar `ENTER` de novo
quando os 5s acabarem, sem perder o que tinha digitado. O undo (`DEL DEL`/`-` `ENTER`) **continua
funcionando normalmente** durante a revelação -- o bloqueio é só para lançar um resultado novo, não
para corrigir o anterior. Depois de voltar ao painel compacto, o número continua tendo o "pop" mais
sutil de sempre (`_NUMBER_POP_MS`) a cada troca.

A faixa "GIROS DA SESSÃO" (contador + tira horizontal de chips) que ficava entre os limites de
aposta e as três colunas foi **eliminada** — mais espaço de tela pras três colunas, especialmente
pra coluna central. "ÚLTIMO RESULTADO" é a informação de maior destaque: uma sequência de ajustes
de tamanho pedidos em iterações sucessivas (+30%, +30% de novo, depois -30% em cima disso) deixou o
número em **+18,3%** do tamanho original (1.3×1.3×0.7=1.183), sempre com **contorno branco** (pedido
explícito), com o **dobro da grossura** do contorno das linhas de histórico logo abaixo
(`RouletteDisplay._draw_center_number`). A linha divisória entre o número e o histórico usa a
altura **realmente renderizada** do número (medida via `font.render(...).get_height()`), não o
tamanho de fonte nominal pedido ao `theme.font()` — os dois podem divergir bastante dependendo da
escala da tela, e usar o nominal deixava um vão bem maior que o pretendido entre o número e a
linha. Isso mantém a linha "colada" no número em qualquer resolução, sobrando o máximo de espaço
possível pro histórico logo abaixo — um histórico em **três raias** verticais (preto à esquerda,
vermelho à direita, zero centralizado) preenchido com os giros anteriores
(`RouletteDisplay._draw_center_history`), cuja fonte usa o tamanho *original* do número (antes dos
ajustes acima) como referência, também reduzida em -30% a pedido (era 70% do tamanho do número
grande, agora 49% — `_HISTORY_ROW_FONT_RATIO`) — o que também deixa mais linhas visíveis de uma vez.
Diferente das três colunas independentes de FRIO/QUENTE:
aqui cada **linha representa um giro** (mais recente sempre no topo, na mesma ordem de
`state.history`), e dentro da linha o número só aparece na raia da própria cor — as outras duas
ficam em branco naquela linha,
criando um "roadmap" de zigue-zague fácil de ler de relance, em vez
de três listas independentes por cor. Dúzias/colunas continuam calculadas por
`app/statistics/engine.py` (e testadas), só não ocupam uma faixa própria na tela.

**Sombras suaves e brilho no número recém-trocado**: pedido explícito do cliente pra dar mais
profundidade/sofisticação à interface ("as melhorias visuais devem ser aplicadas em todas as
telas, para tornar a imersão mais real"). Os badges trapezoidais FRIO/QUENTE, os sete cartões da
barra de estatística e o banner de avisos (`REGISTRADO`, `NOVO RESULTADO`, etc.) ganharam uma
sombra preta semi-transparente levemente deslocada (`_trapezoid_shadow_surface`/
`_rect_shadow_surface`, cacheadas por tamanho pra não alocar uma `Surface` nova a cada frame no
Pi 3). O "ÚLTIMO RESULTADO" ganhou um brilho suave atrás do número, sincronizado com o "pop" que já
existia: aparece e desvanece junto com ele (`_NUMBER_POP_MS`), reforçando visualmente que o
resultado acabou de mudar sem virar uma animação nova pra acompanhar. Deliberadamente **não**
foram adicionados reflexos/brilhos periódicos ou qualquer animação que rode enquanto a tela está
parada: numa sinalização de cassino olhada de longe por horas, um efeito assim tende a puxar o
olho do operador pra longe do número que realmente importa — o objetivo é elegância discreta, não
chamar atenção pra si mesmo.

As transições (`app/ui/animation.py`) usam curvas de easing simples (`ease_out_cubic`,
`ease_out_back`) em vez de qualquer motor de animação — cobrem a revelação do número, o histórico
deslizando/desvanecendo ao entrar ou sair da lista, e o overlay de administração abrindo/fechando
com fade. Nada disso roda por pixel: são poucos blits/renderizações de texto durante uma janela
curta (~200-600ms, ~1,5s só na revelação), e o resto do tempo (a grande maioria, entre giros) a
tela fica parada a `idle_fps`, sem pesar no Pi 3.

## 2. Fluxo funcional

1. Boot do Pi → sem mensagens de kernel/desktop (configurado pelo `install.sh`) → serviço
   systemd `roulette-display` sobe → splash com logo/nome do cassino (mínimo ~1.5s) →
   tela principal.
2. Operador digita `2` `3` no teclado numérico → tela mostra `NOVO RESULTADO: 23`.
3. Operador aperta `ENTER` → resultado é validado (0-36), gravado no SQLite (commit + fsync
   imediato), cor calculada, histórico/estatísticas recalculados, tela atualizada com animação
   simples (~0.9s) no número atual.
4. Erro de digitação: `BACKSPACE` apaga o último dígito antes do `ENTER`; `ESC` cancela tudo.
5. Bolinha errada já confirmada: `DEL` `DEL` (dentro de alguns segundos) ou `CTRL+BACKSPACE`
   remove o último resultado (soft delete — some da tela e das estatísticas, mas o registro bruto
   fica preservado em auditoria, nunca é apagado fisicamente).
6. `CTRL+ALT+A` abre a administração, que pede PIN.

## 3. Estrutura do projeto

```
roulette-display/
├── main.py                    # entrypoint
├── config.yaml                # configuração (casino, mesa, tamanhos, PIN, etc.)
├── requirements.txt
├── pytest.ini
├── app/
│   ├── config.py               # dataclass Config + load/save
│   ├── logging_setup.py        # logging rotativo + hook de exceção global
│   ├── models/
│   │   ├── roulette_data.py    # cor/paridade/dúzia/coluna (domínio puro)
│   │   └── spin.py             # dataclass Spin
│   ├── statistics/
│   │   └── engine.py           # funções puras de estatística (testadas em tests/)
│   ├── database/
│   │   └── db.py                # SQLite, WAL, soft-delete, export, backup, audit trail
│   ├── license/
│   │   ├── hardware.py          # Device ID (serial do SoC -> fingerprint)
│   │   ├── verify.py            # verificação Ed25519 offline do license.dat
│   │   └── public_key.py        # chave pública embutida (não secreta)
│   ├── services/
│   │   ├── spin_service.py      # regra de negócio central (usado pela UI)
│   │   ├── backup_service.py    # com retenção automática
│   │   ├── export_service.py
│   │   ├── power_service.py     # reboot/shutdown
│   │   └── watchdog_service.py  # heartbeat sd_notify (systemd WatchdogSec)
│   └── ui/
│       ├── theme.py             # cores + escala responsiva + fontes
│       ├── splash.py            # tela de boot
│       ├── license_screen.py    # "SISTEMA NÃO ATIVADO" / "LICENÇA INVÁLIDA"
│       ├── failsafe_screen.py   # "SISTEMA TEMPORARIAMENTE INDISPONÍVEL" / "ERRO DO SISTEMA"
│       ├── display.py           # loop principal, teclado, renderização
│       └── admin.py             # painel administrativo (PIN)
├── assets/                     # logo.png / background.png / splash.png (ver assets/README.md)
├── data/                       # roulette.db, backups/, exports/, license.dat (gitignored)
├── logs/                       # app.log rotativo (gitignored)
├── scripts/
│   ├── install.sh              # instalação completa num Pi OS novo
│   ├── backup.sh                # backup manual do SQLite, com retenção (pode rodar via cron)
│   ├── update.sh                # git pull + reinstala deps + reinicia serviço
│   └── long_run_monitor.sh      # amostra RSS/CPU/temp/giros p/ teste de 24-72h (cron, ver seção 14)
├── systemd/
│   ├── roulette-display.service # Type=notify + WatchdogSec
│   └── roulette-sudoers          # regra de sudo pro hardening opcional (rodar sem root)
└── tests/                      # pytest — estatística, domínio, config, banco, licenciamento

../license-generator/           # FORA deste diretório — nunca instalado no Pi. Ver seção 9.
```

## 4. Banco de dados (SQLite)

Tabelas: `roulettes` (mesas), `spins` (giros, com `deleted` para soft-delete), `spin_audit`
(trilha de quem/quando desfez um resultado).

Decisões para sobreviver a queda de energia:

- `PRAGMA journal_mode=WAL`: um corte de energia no meio de uma escrita deixa o arquivo `.db`
  principal intacto; o WAL é reaplicado/truncado automaticamente na próxima abertura.
- `PRAGMA synchronous=FULL`: cada `commit` só retorna depois do fsync no disco. O volume de
  escrita é baixíssimo (1 giro a cada ~30-60s), então o custo de performance é irrelevante — a
  durabilidade vale mais.
- **Undo é soft delete, nunca `DELETE`**: corrigir uma bolinha errada marca a linha como
  `deleted=1` e registra em `spin_audit`; o dado bruto nunca é destruído, só some da tela.
- Ao iniciar, o histórico completo é recarregado do banco — nada crítico fica só em memória.

## 5. Atalhos de teclado

| Tecla | Ação |
|---|---|
| `0`-`9` (numérico ou linha superior) | Digita o número do resultado (até 2 dígitos) |
| `ENTER` | Confirma o número digitado (valida 0-36) |
| `BACKSPACE` | Apaga o último dígito digitado (antes de confirmar) |
| `ESC` ou `.` (ponto) | Cancela a digitação atual / cancela um comando `-` ou `-97` em andamento |
| `DEL`, `DEL` (duas vezes, dentro de alguns segundos) | Desfaz o último resultado confirmado |
| `CTRL`+`BACKSPACE` | Desfaz o último resultado imediatamente (combo já é "intencional") |
| `-` `ENTER` | Desfaz o último resultado imediatamente (sem dupla confirmação) |
| `-` `9` `7` `ENTER` | Reinicia a sessão da mesa atual — zera o placar em tela (pede uma segunda confirmação); os giros continuam salvos no banco (soft-delete) para auditoria/exportação, nunca são apagados de verdade |
| `+` | Marca visualmente "novo giro" (aviso piscando; puramente cosmético, some sozinho ao confirmar o próximo número ou com `ESC`/`.`) |
| `CTRL`+`ALT`+`A` | Abre a administração (pede PIN) |

Os três atalhos com `-` seguem exatamente o protocolo do console de referência do cliente
("Controle Roleta"): `-` sozinho + `ENTER` corrige o último número, e `-97` + `ENTER` reinicia a
sessão exibida na mesa. A única diferença deliberada em relação ao aparelho original é que `-97`
pede uma segunda tecla (`ENTER` de novo ou `ESC` para cancelar) antes de executar — o aparelho de
referência executa na hora, sem confirmação nenhuma, e como isso zera o placar da mesa inteira sem
exigir PIN, preferi manter essa trava mínima por padrão. Se quiser fidelidade total ao aparelho
(zero confirmação), é uma mudança pequena e posso fazer.

Importante (auditoria de hardening): "reiniciar sessão" nunca significou `DELETE FROM spins`. É
sempre soft-delete (`deleted=1` na tabela `spins`) — os giros continuam fisicamente no banco,
disponíveis para auditoria (`Ver auditoria (correções)` no painel admin) e presentes em qualquer
backup. O único texto que mudou nesta revisão foi o que aparece na tela para o operador ("apagar
memória" → "reiniciar sessão"), porque a redação antiga sugeria destruição permanente que nunca
existiu de fato — ver seção 14.

## 6. Configuração (`config.yaml`)

Todos os campos têm um default seguro; o arquivo é criado automaticamente na primeira execução
se não existir. Pode ser editado à mão ou pelo painel administrativo (que reescreve o YAML de
forma atômica — grava em `.tmp` e faz `rename`, então uma queda de energia no meio da escrita não
corrompe a configuração).

Principais campos: `casino_name`, `roulette_name`, `roulette_id`, `history_size`,
`statistics_window`, `hot_numbers_count`, `cold_numbers_count`, `currency`, `min_bet`, `max_bet`,
`fullscreen`, `admin_pin`, `undo_confirm_seconds`, `backup_retention_count`,
`data_retention_days` (padrão 30 — ver seção 8, "Limitação conhecida: histórico muito longo"),
`license_path`, `license_state_path`, caminhos de banco/logs/assets/backups/exports.

`currency`/`min_bet`/`max_bet` são só texto exibido em uma faixa fina abaixo do cabeçalho (ex.:
"MÍNIMA: R$ 5,00 MÁXIMA: R$ 5.000,00") — não são validados como número nem usados em cálculo
nenhum, propositalmente: formatação de moeda/limite varia por cassino, e o painel não faz
apostas, só mostra a informação. Editáveis pelo admin (`Alterar moeda`/`Alterar aposta
mínima`/`Alterar aposta máxima`), correspondendo a "/" e F7/F8 do console de referência do
cliente — mantive atrás do PIN do admin em vez de teclas diretas sem confirmação, para ficar
consistente com o resto do painel (nenhuma configuração muda sem PIN).

**Troque `admin_pin` do padrão (`1234`) antes de colocar o equipamento em produção.**

## 7. Estatísticas

`app/statistics/engine.py` é puro e testado (`tests/test_statistics.py`): frequência por número,
cor, par/ímpar, baixo/alto, 1ª/2ª/3ª dúzia, 1ª/2ª/3ª coluna, números mais/menos frequentes na
janela configurada (`statistics_window`), e giros desde a última ocorrência de cada número
(frios). **São estatísticas descritivas do histórico — a roleta não tem memória, e nada aqui
prevê o próximo resultado.**

## 8. Instalação num Raspberry Pi 3

Recomendado: **Raspberry Pi OS Lite (64-bit)**, sem desktop — o painel não precisa e assim o
sistema nem tenta subir X11.

```bash
git clone <url-do-repo> roulette-display
cd roulette-display
sudo ./scripts/install.sh
sudo reboot
```

O `install.sh`:

1. Instala dependências de sistema (Python, SDL2, ferramentas de build).
2. Cria um virtualenv em `venv/` e instala `requirements.txt`.
3. Cria `data/`, `logs/`, `data/backups/`, `data/exports/`.
4. Instala e habilita o serviço systemd (`Restart=always`, `RestartSec=3`).
5. Desabilita o login automático na tty1 (`getty@tty1`) para não disputar a tela com o painel.
6. Ajusta `cmdline.txt`/`config.txt` para boot silencioso (ver seção 9).

Depois de reiniciar, o equipamento sobe direto no painel — sem terminal, sem desktop, sem prompt.

### Boot splash customizado (sem mostrar Linux)

O `install.sh` já faz isso, mas para entender ou ajustar manualmente:

- **`/boot/firmware/config.txt`** (ou `/boot/config.txt` em versões mais antigas): adicionar
  `disable_splash=1` — troca o "arco-íris" de boot por uma tela preta em vez de removê-lo.
- **`/boot/firmware/cmdline.txt`**: adicionar `quiet loglevel=3 logo.nologo
  vt.global_cursor_default=0 consoleblank=0` na mesma linha (cmdline.txt é uma linha só).
  Isso silencia as mensagens de kernel e o cursor piscando no console.
- Desabilitar o `getty` da tty1 (`sudo systemctl disable getty@tty1.service`) evita que um prompt
  de login apareça por trás/antes do nosso serviço.
- Nossa própria tela de splash (`app/ui/splash.py`, com logo/nome do cassino) assume a partir daí,
  assim que o serviço `roulette-display` inicia.
- Opcional/avançado: para um splash *desde o firmware* (antes até do kernel terminar de subir),
  é possível instalar e configurar o **Plymouth** com um tema customizado usando `logo.png`. Isso
  é mais trabalhoso e specific da versão do Raspberry Pi OS, por isso não é feito pelo
  `install.sh` — a combinação `disable_splash=1` + boot silencioso + nosso splash em pygame já
  cobre o requisito de "não mostrar terminal/desktop" com muito menos complexidade e risco de
  quebrar o boot.

### Orientação retrato ("tela em pé")

O layout principal do painel é **retrato** (mais alto que largo) — é o layout que imita o console
de referência do cliente e o que deve ser usado numa TV montada em pé ao lado da mesa. A interface
detecta a orientação sozinha (`Theme.portrait = altura > largura`) e escolhe o layout certo — não é
preciso configurar nada no `config.yaml` para isso, só a **resolução que o Raspberry realmente
emite** precisa estar em retrato.

Como não há X11 em produção (rodamos direto em KMSDRM), não existe `xrandr --rotate` — a rotação
tem que ser feita na saída de vídeo do próprio firmware/kernel, antes do pygame sequer abrir a
tela:

- **`/boot/firmware/cmdline.txt`** (mesma linha do boot silencioso da seção anterior), adicione um
  parâmetro `video=` girando a saída HDMI. Para uma TV Full HD física (1920x1080) montada de lado,
  girando para ficar em pé:
  ```
  video=HDMI-A-1:1080x1920@60,rotate=90
  ```
  Troque `HDMI-A-1` pela porta usada (`HDMI-A-2` na segunda saída de um Pi 4/CM4; no Pi 3 normalmente
  só existe `HDMI-A-1`) e `rotate=90`/`rotate=270` pelo sentido do giro físico da TV.
- Depois de reiniciar, `pygame.display.set_mode((0, 0), pygame.FULLSCREEN)` (o que o app já faz)
  detecta a resolução atual do KMS — já rotacionada — automaticamente. Nenhuma mudança de código é
  necessária.
- Se a TV/monitor aceitar **entrada nativa em retrato** (alguns monitores de sinalização digital
  giram o próprio painel via OSD sem precisar que a placa de vídeo gire nada), o `video=` acima
  pode não ser necessário — teste sem ele primeiro.
- Não fiz o `install.sh` aplicar esse parâmetro automaticamente: o nome da porta HDMI e o sentido
  do giro dependem do hardware exato (modelo do Pi, TV, como ela foi fisicamente montada), e um
  parâmetro `video=` errado pode deixar a saída de vídeo em branco no boot — prefiro que isso seja
  um passo manual e testado, em vez de um valor adivinhado sendo aplicado automaticamente num
  campo em produção.

O layout paisagem (mais largo que alto) continua funcionando — é o que aparece automaticamente ao
rodar `python3 main.py` numa janela normal (`fullscreen: false`) durante o desenvolvimento, ou se
o equipamento acabar instalado numa TV horizontal comum.

#### Rotação em software (`screen_rotation`) — quando o driver de vídeo não gira sozinho

O caminho acima (`video=...,rotate=`) depende do kernel/firmware girar a saída antes do pygame
abrir a tela — funciona no Raspberry Pi real (KMSDRM), mas alguns ambientes não suportam nenhuma
forma de rotação por hardware/driver: o console de várias VMs (VMware/ESXi, algumas soluções de
VNC) expõe uma tela virtual cujo RandR não implementa rotação de verdade (`xrandr --rotate` falha
com `BadMatch`/`RRSetScreenSize` nesses casos) — situação comum ao testar o app numa VM com monitor
paisagem que será montada em pé.

Para esses casos, `config.screen_rotation` (`0`/`90`/`180`/`270`, padrão `0`) gira o quadro em
software, em `app/ui/rotation.py`: o app desenha normalmente numa superfície "lógica" já com as
dimensões trocadas (retrato), e `pygame.display.flip()` gira esse quadro pra caber na tela física
(paisagem) antes de mostrar — nenhuma outra parte do código precisa saber disso. Editável também
pelo admin (`Personalização/Identificação > Girar tela`, alterna 0→90→180→270 a cada `ENTER`) —
como a tela só é aberta uma vez no início do processo, a mudança só tem efeito depois de reiniciar
o painel. Qual das duas opções (`90` ou `270`) fica correta depende do sentido físico de montagem
do monitor — não dá pra adivinhar por software, só testando uma e trocando pela outra se a imagem
sair de cabeça pra baixo ou de lado errado.

### Hardening opcional (rodar sem root)

Por padrão o serviço roda como `root` porque o acesso direto a `/dev/dri` (KMSDRM) e
`/dev/input/*` (teclado) fica mais simples e robusto assim — é a escolha certa para um
equipamento dedicado de uso único. Se quiser reduzir privilégios:

```bash
sudo useradd -r -G video,input,render,tty roulette
sudo chown -R roulette:roulette /caminho/para/roulette-display
```

E edite `systemd/roulette-display.service` trocando `User=root` por `User=roulette`, instalando a
regra de sudo já pronta em `systemd/roulette-sudoers` (restrita exatamente a `systemctl
reboot`/`systemctl poweroff`, nada mais):

```bash
sudo install -m 0440 systemd/roulette-sudoers /etc/sudoers.d/roulette-power
sudo visudo -c
```

(sem isso, os botões "Reiniciar"/"Desligar" do painel administrativo não vão funcionar com um
usuário não-root).

**Atenção**: esse caminho não foi validado em hardware real neste projeto (sem acesso a um
Raspberry Pi físico durante o desenvolvimento) — teste o acesso a `/dev/dri`/`/dev/input/*` com o
usuário `roulette` antes de assumir como padrão de produção. `User=root` continua sendo o padrão
do `.service` justamente por isso.

### Solução de problemas

- **Tela preta, painel não aparece**: verifique `journalctl -u roulette-display -f`. Se o SDL não
  conseguir abrir KMSDRM (placa/driver não suportado), troque
  `Environment=SDL_VIDEODRIVER=kmsdrm` por `Environment=SDL_VIDEODRIVER=fbcon` no arquivo de
  serviço e rode `sudo systemctl daemon-reload && sudo systemctl restart roulette-display`.
- **Teclado numérico não responde**: confirme que o usuário do serviço tem acesso a
  `/dev/input/event*` (grupo `input`) — com `User=root` isso nunca é um problema.
- **Serviço reinicia em loop**: veja `logs/app.log` (ou `journalctl`) para o traceback; o
  `Restart=always` + `RestartSec=3` mantém o equipamento operacional mesmo assim, mas o log tem a
  causa raiz.
- **Serviço nunca "termina de iniciar" / reinicia repetidamente logo após o boot**: o `.service`
  usa `Type=notify` — o systemd espera o app avisar (`READY=1`) que o painel já está desenhado na
  tela. Se isso não chegar dentro de `TimeoutStartSec` (padrão do systemd, não definido por nós),
  o systemd mata e tenta de novo. Verifique `journalctl -u roulette-display` por
  `sd_notify failed`; se aparecer, o mais provável é `$NOTIFY_SOCKET` não estar chegando ao
  processo (raro, mas pode acontecer atrás de outro supervisor/contêiner) — nesse caso, troque
  `Type=notify` por `Type=simple` e remova `WatchdogSec` no `.service` (perde a recuperação
  automática de travamento, mas mantém tudo o resto). Este mecanismo não foi validado num
  Raspberry Pi físico durante o desenvolvimento — teste com `systemctl daemon-reload && systemctl
  restart roulette-display && systemctl status roulette-display` antes de confiar nele em produção.

### Limitação conhecida: histórico muito longo

`SpinService.get_display_state()` recarrega a tabela `spins` inteira do banco a cada giro (é o
jeito mais simples de garantir que "frio" — giros desde a última ocorrência — fique sempre
correto, e é praticamente instantâneo em um Pi 3 até algumas centenas de milhares de linhas). Para
uma mesa em operação 24/7 por muitos meses sem nunca reiniciar a sessão, essa lista cresce sem
limite e o recálculo a cada giro eventualmente fica perceptível — o teste de soak desta revisão
(seção 14) mediu ~14ms com 100 mil giros ativos, então essa margem é bem confortável na prática.
Se isso virar um problema real em escala, o próximo passo natural é o `SpinService` calcular
"frio" a partir de um índice `last_seen` por número mantido incrementalmente em vez de escanear o
histórico completo — não implementado agora para não adicionar complexidade sem necessidade
comprovada.

**Mitigação em produção — retenção automática de dados** (`app/services/retention_service.py`):
giros com `created_at` mais antigo que `config.data_retention_days` (padrão **30 dias**) são
arquivados em CSV (`exports_dir/arquivo-retencao-AAAAMMDD-HHMMSS.csv`, incluindo os já
soft-deletados, com uma coluna `status` marcando cada um) e **removidos de verdade** da tabela
`spins` (`Database.purge_spins_older_than` — a única exceção deliberada ao princípio de nunca
fazer hard delete que rege o resto do banco, ver comentário no topo de `app/database/db.py`).
`spin_audit`/`audit_log` nunca são tocados por essa política — a trilha de auditoria (bem menor, e
com cadeia de hash no caso de `audit_log`) continua íntegra mesmo depois que o giro em si já foi
purgado. Roda sozinha a cada `_RETENTION_CHECK_S` (6h) dentro do loop principal — checagem
por relógio de parede (`time.time()`), não `pygame.time.get_ticks()`, que é medido em
milissegundos e passaria a exigir cuidado extra depois de ~24 dias de uptime contínuo (exatamente
o cenário que essa política existe para cobrir). `enforce_retention()` é idempotente (não faz
nada se não há giro além do corte), então essa checagem periódica é barata mesmo rodando sozinha
por meses. Também disponível como ação manual no admin ("Forçar limpeza de retenção agora"), para
confirmar que a política está ativa sem esperar o ciclo automático.

## 9. Licenciamento

O painel é um produto comercial vinculado ao equipamento — copiar o cartão SD (`dd`) para outro
Raspberry Pi **não** produz uma segunda instalação funcional. `app/license/` faz essa verificação
uma vez, no boot, antes de abrir o banco ou a tela principal.

### Como funciona

1. **Device ID** (`app/license/hardware.py`): derivado do serial do SoC, lido de
   `/proc/cpuinfo` — gravado em memória OTP na fábrica, não fica em nenhum arquivo do cartão, então
   um clone bit-a-bit do SD não o copia. Formatado como `RLT-XXXX-XXXX` (prefixo de um hash
   SHA-256, o suficiente pra ser lido/digitado por uma pessoa sem gerar colisão relevante na escala
   real deste produto — o que garante a segurança de verdade é a assinatura Ed25519, não o
   tamanho do ID).
2. **Licença assinada** (`app/license/verify.py`): `license.dat` é um JSON com os dados da licença
   (cliente, cassino, mesa, validade, Device ID) + uma assinatura Ed25519. A chave pública (não
   secreta) está embutida em `app/license/public_key.py`; a chave privada nunca existe neste
   repositório nem no Raspberry Pi — só na ferramenta separada `license-generator/` (ver
   `license-generator/README.md`).
3. **Verificação no boot** (`main.py` → `app/ui/license_screen.py`): se a licença faltar, estiver
   corrompida, tiver assinatura inválida, for de outro equipamento ou estiver expirada, o painel
   mostra "SISTEMA NÃO ATIVADO"/"LICENÇA INVÁLIDA" com o Device ID — nunca detalhes internos,
   caminhos ou chaves. `ENTER` reverifica sem precisar reiniciar o serviço (útil depois de copiar
   um `license.dat` novo via USB).

### Ativação (offline, sem internet)

```
Pi mostra "DEVICE ID: RLT-8F2A-91C7"
  → cliente/técnico te envia esse código
  → você roda license-generator/generate_license.py (só na sua máquina, tem a chave privada)
  → transfere o license.dat gerado pro Pi (USB) em data/license.dat (config.yaml: license_path)
  → ENTER na tela de ativação (ou reinicia o serviço)
```

### Manutenção legítima

- **Atualizar o software** (`git pull`/`update.sh`): nunca invalida a licença — ela está vinculada
  ao Device ID (hardware), não a um hash do código.
- **Trocar o cartão SD** no mesmo Raspberry: o Device ID não muda (vem do SoC, não do cartão) —
  reinstale o `license.dat` no cartão novo e pronto.
- **Trocar o Raspberry** (o equipamento morreu): o Device ID muda de propósito — é preciso gerar e
  instalar uma licença nova pro novo equipamento. Isso é o comportamento correto, não um bug.

### Limitações conhecidas (honestas, não escondidas)

- Fora de um Raspberry Pi real (dev/CI/este ambiente), `/proc/cpuinfo` não tem o serial do SoC —
  o código cai para `/etc/machine-id` com um aviso no log; esse fallback **não** está ancorado a
  hardware e não deve ser tratado como equivalente em produção.
- Licenças temporárias (`--expires`) dependem do relógio do sistema. `verify.py` guarda o último
  horário já observado (`license_state_path`) e usa o mais recente entre ele e o relógio atual —
  isso impede um simples "atrasar o relógio" trivial, mas nenhum esquema puramente offline resiste
  a alguém disposto a também apagar/editar esse arquivo de estado. Não há solução perfeita pra
  isso sem um servidor de licenças online (fora de escopo desta versão).
- Não há revogação remota nem canal de telemetria — 100% offline por design. Um servidor central
  (`licensing.<domínio>`) é um passo futuro natural que não exige mudar esse mecanismo local, só
  complementar (ver Roadmap).

## 10. Roadmap (arquitetura já preparada, não implementado nesta versão)

A separação `services` / `database` / `ui` existe justamente para isto:

- Painel web de administração e dashboard.
- API REST (a mesma `SpinService` viraria o backend de uma API sem mudar a UI local).
- Sincronização com servidor central (Postgres), múltiplas mesas.
- Atualização remota, controle de usuários.
- Integração automática com sensor/câmera da roleta (troca o registro manual por um "producer"
  que chama `SpinService.register_spin()` — o resto do sistema não muda).

## 11. Rodando localmente (desenvolvimento, sem Raspberry Pi)

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
# em config.yaml, deixe fullscreen: false para testar em uma janela normal
python3 main.py
```

## 12. Testes

```bash
pip install pytest
pytest
```

Cobrem: classificação de números (cor/par-ímpar/dúzia/coluna, com testes dedicados ao zero como
categoria própria em todas as estatísticas), motor de estatísticas (frequência, quente/frio, giros
desde a última ocorrência), configuração (load/save/validação), banco de dados (undo soft-delete,
limite de histórico, export CSV, backup/restore, modo WAL, audit trail com timestamp original
preservado, migração de schema pré-existente), retenção automática de backup, licenciamento
(fingerprint determinístico, todos os status de `verify_license` — válida, ausente, corrompida,
vazia, assinatura inválida/forjada/chave pública errada, equipamento errado, expirada, tentativa
de retroceder o relógio — e os três cenários nomeados de clonagem de SD), painel administrativo
(navegação completa do menu, overlay reaproveitado entre frames), o protocolo completo do numpad
(0-36 válidos, 37/99 rejeitados, ENTER vazio/duplicado/tecla mantida não duplica giro, dois
lançamentos deliberados do mesmo número continuam permitidos, BACKSPACE, `-`, `-97 ENTER` com
confirmação em dois passos e soft-delete confirmado, `+`, NumLock não aborta um comando em
andamento, eventos de foco perdido/reganho ignorados com segurança), persistência sob falha real
(processo morto com `SIGKILL` de verdade logo após o commit, recuperação de WAL, reabertura com
arquivos `-wal`/`-shm` órfãos), stress/soak de banco (2.000 giros sempre; 100.000 giros opt-in via
`ROULETTE_RUN_SOAK=1`), tela de failsafe, logging (rotação limitada, PIN nunca em texto puro), e
as curvas de easing/tween das transições. Renderização em si (pygame) não tem teste automatizado de
pixel — mas boa parte da lógica de teclado/estado que antes só era coberta "rodando e vendo" agora
tem teste de verdade (`tests/test_input_protocol.py`), e a verificação visual continua sendo feita
gravando o app rodando de verdade sob Xvfb, que é como o bug do texto invisível no QUENTE/FRIO e o
`NameError` na tela de licença acabaram aparecendo originalmente.

## 13. Revisão crítica (bugs encontrados e corrigidos antes da entrega)

- **Undo acidental**: a primeira versão permitia apagar o último resultado com uma única tecla.
  Corrigido para exigir confirmação (`DEL` `DEL` com janela de tempo configurável, ou
  `CTRL+BACKSPACE` como combo explícito).
- **Corrupção após queda de energia**: sem WAL + `synchronous=FULL`, uma escrita interrompida no
  meio poderia corromper o `.db`. Corrigido configurando os dois PRAGMAs na abertura da conexão.
- **`config.yaml` corrompido por escrita parcial**: `save_config` agora escreve em um arquivo
  `.tmp` e usa `rename` atômico, em vez de sobrescrever o arquivo original diretamente.
- **Uso de CPU constante**: a primeira versão rodava a 30 FPS o tempo todo. Corrigido com FPS
  dinâmico (`idle_fps` quando não há nada acontecendo, `target_fps` só durante ~0.9s de animação
  ou enquanto o operador está digitando/no menu admin).
- **Conflito de VT com o login (`getty`) na tty1**: podia fazer o KMSDRM falhar em obter acesso
  exclusivo ao display. Corrigido desabilitando `getty@tty1` no `install.sh`.
- **PIN de administração fraco por padrão + sem proteção contra tentativa repetida**: adicionado
  bloqueio temporário (30s) após 5 tentativas erradas de PIN.
- **Teclado numérico USB nem sempre envia os mesmos keycodes**: alguns emulam a linha de números
  (`K_0`-`K_9`), outros o bloco numérico (`K_KP0`-`K_KP9`) dependendo do driver/NumLock. Ambos os
  conjuntos são tratados de forma idêntica.
- **Bug real encontrado nesta revisão**: o painel administrativo checava as teclas do bloco
  numérico com `K_KP0 <= key <= K_KP9`. O pygame/SDL não numera essas constantes em ordem
  crescente (`K_KP0` vale *mais* que `K_KP9`), então essa comparação nunca era verdadeira e o PIN
  não podia ser digitado pelo teclado numérico — só pela linha de números. Corrigido trocando por
  um dicionário de mapeamento (a mesma técnica já usada na tela principal), com teste manual
  cobrindo especificamente as teclas `K_KP*`.
- **"Importar backup" deixava o app num estado quebrado**: restaurar um backup exige fechar a
  conexão SQLite viva antes de sobrescrever o arquivo — mas isso valia tanto para sucesso quanto
  para falha da restauração, e o app continuava rodando com a conexão fechada até o operador
  tentar registrar o próximo giro (aí sim quebrava, com um crash + restart via systemd). Corrigido
  fazendo essa ação sempre forçar o reinício do processo logo em seguida (mesmo mecanismo do
  "reiniciar painel"), em vez de deixar o app tentando continuar com uma conexão morta.
- **Cache de fontes com colisão de chave**: `Theme.font()` cacheava por
  `tamanho_em_px * (2 if negrito else 1)`, o que permite duas fontes diferentes (ex.: negrito 20px
  e normal 40px) colidirem na mesma chave e uma "roubar" o cache da outra. Não chegava a acontecer
  com os tamanhos usados hoje na UI, mas era uma armadilha para qualquer tela nova. Corrigido
  usando `(tamanho, negrito)` como chave.
- **`Restart=always` do systemd vs. "Sair do programa" do admin**: os dois requisitos, lidos ao
  pé da letra, se contradizem — se o serviço sempre reinicia sozinho, um "sair" nunca fica
  parado. Resolvido tratando essa opção do admin como "reiniciar o painel" (o processo encerra
  limpo e o systemd sobe uma instância nova em ~3s) — útil para aplicar configurações que exigem
  reinicializar o pygame, sem precisar de acesso SSH. Para desligar de fato, use "Desligar
  Raspberry Pi" (que derruba o sistema operacional inteiro, e com ele o serviço).
- **Dependência desnecessária**: nenhuma biblioteca de GUI pesada (Qt, Electron/Chromium) — só
  `pygame-ce` e `PyYAML`.
- **Números pretos invisíveis nos painéis QUENTE/FRIO**: esses painéis desenhavam o número numa
  cor igual à da roleta (vermelho/preto/verde) direto sobre o fundo escuro, sem nenhum chip atrás
  para dar contraste — diferente do histórico, que desenha um círculo preenchido. Como o preto da
  roleta é quase idêntico ao fundo do painel, números pretos praticamente somem. Só apareceu numa
  gravação de vídeo real do app rodando (os testes automatizados não cobrem renderização); a
  suíte não pegaria isso sozinha. Corrigido com `LABEL_COLOR_MAP` (mesmo mapa, mas com preto
  trocado por um cinza claro), usado só onde o texto não tem chip de fundo.
- **"Fechar administração" pelo menu não fechava o overlay**: selecionar essa opção com ENTER só
  resetava o estado interno do painel administrativo para a tela de PIN — o overlay escuro
  continuava na tela por cima, e o operador ficava preso ali (só `ESC` no topo do menu fechava de
  verdade). Corrigido para tratar a seleção do menu do mesmo jeito que o `ESC`.
- **`NameError` na tela de licença ao reverificar com ENTER**: `_run_gate` recebia a função de
  checagem como parâmetro `check`, mas o corpo do loop chamava `_check()` (nome de uma versão
  anterior da função, de antes de virar parâmetro) — funcionava na primeira verificação (feita
  antes do loop) e só quebrava ao apertar ENTER pra reverificar depois de instalar uma licença,
  com o processo inteiro caindo num traceback. Só apareceu rodando o fluxo completo de verdade
  (gerar uma licença real com `license-generator/`, instalar, apertar ENTER na tela) — exatamente
  o tipo de bug que revisão de código sozinha não pega, porque o texto do código "parece" certo.
- **Watchdog do systemd não testado em hardware real**: `Type=notify` + `WatchdogSec` dependem do
  systemd de verdade (não há como simular isso neste ambiente de desenvolvimento). O protocolo
  `sd_notify` em si foi validado com um socket Unix real fazendo o papel do systemd (ver
  `app/services/watchdog_service.py`), mas o comportamento fim-a-fim (start job, timeout,
  restart por watchdog) precisa ser testado num Raspberry Pi real antes de confiar em produção —
  documentado explicitamente na seção de solução de problemas, não escondido.

## 14. Hardening desta revisão (release candidate)

Rodada de correções sobre o sistema já aprovado — sem trocar arquitetura, protocolo do numpad ou
identidade visual. Resumo do que mudou e por quê (detalhe completo de cada item no histórico de
commits desta revisão):

- **FRIO/QUENTE com legenda inequívoca**: os subtítulos agora dizem exatamente o que o número
  representa — "GIROS SEM SAIR" (FRIO) e "OCORRÊNCIAS" (QUENTE, com uma segunda linha discreta
  mostrando o tamanho real da janela estatística em uso). O algoritmo não mudou, só o rótulo.
- **Histórico recente mais legível à distância**: chips maiores (64px → 78px), fonte maior
  (24px → 30px), e um anel cinza-claro ao redor dos chips pretos (o preto da roleta é quase
  idêntico ao fundo do painel — sem o anel, um resultado preto no histórico praticamente some
  visto de longe). Reduzido de 10 para 8 chips exibidos, para não competir visualmente com o
  número central, que continua sendo a informação principal da tela.
- **Feedback "REGISTRADO" mais direto**: texto mudou para `REGISTRADO • N`, fonte bem maior que os
  demais avisos (44px vs. 28px), duração ajustada para 650ms (dentro da faixa pedida de
  500-800ms). Continua sendo só um desenho temporário — não é modal, não bloqueia entrada do
  próximo giro, não trava o loop.
- **Ícones da barra inferior**: os naipes de baralho (♦♣♥♠) usados em ÍMPAR/PAR/VERMELHO/PRETO
  eram puramente decorativos — não havia nenhuma regra do sistema associada a eles (confirmado
  lendo o código antes de mexer). Substituídos por um ponto neutro na cor da categoria, no mesmo
  espírito do que ZERO/MENOR/MAIOR já faziam (texto + cor carregam o significado). Um equipamento
  dedicado só a roleta não deveria remeter a jogo de cartas.
- **"-97 ENTER" (limpar sessão): a semântica sempre foi soft-delete, mas o TEXTO na tela dizia
  "apagar toda a tela e memória"**, o que sugeria destruição permanente para o operador — mesmo
  com os dados preservados no banco para auditoria. Corrigido para "REINICIAR SESSÃO" em todo
  lugar (banner principal, painel admin, mensagens de confirmação), sem alterar em nada o
  comportamento por trás: os giros continuam com soft-delete (`deleted=1`), nunca `DELETE FROM
  spins`. Esse foi o ponto mais sensível desta revisão — o comportamento já era correto, só a
  comunicação com o operador estava enganosa.
- **Indicador "SISTEMA OK" agora reflete saúde real, não só "processo ligado"**: passou a
  depender de duas condições — a última escrita no banco teve sucesso E uma checagem periódica
  leve (`SELECT 1`, a cada 45s, fora do loop de renderização) confirma que o SQLite ainda
  responde. Cobre o caso de o banco ficar inacessível numa sessão sem nenhum giro novo por muito
  tempo, sem adicionar nenhuma consulta ao banco por frame.
- **Watchdog do systemd auditado (não só lido)**: confirmado por grep que não existe nenhuma
  `threading.Thread` no projeto além do lock do banco — o heartbeat só é enviado de dentro do
  próprio loop síncrono principal, então uma trava real em qualquer ponto do loop
  (`_handle_events`, `_render`, etc.) automaticamente para de mandar o pulso, e o `WatchdogSec` do
  systemd detecta isso corretamente. Não existe (nem existiu) uma thread de heartbeat "viva por
  conta própria" que pudesse mascarar uma trava do loop gráfico principal.
- **Tela de falha genérica (`app/ui/failsafe_screen.py`)**: antes, se o banco não abrisse no boot
  ou o loop gráfico morresse com uma exceção não tratada, o processo simplesmente encerrava —
  tela preta/console por um instante entre cada tentativa do `Restart=always`, sem nenhuma
  informação para o operador. Agora mostra "SISTEMA TEMPORARIAMENTE INDISPONÍVEL" (falha de
  banco) ou "ERRO DO SISTEMA — CONTATE O SUPORTE" (erro fatal do loop) por alguns segundos antes
  de sair — nunca stack trace, caminho de arquivo ou detalhe técnico na tela (isso vai só para o
  log). O comportamento de reinício continua sendo 100% do systemd; esta tela não implementa
  nenhum retry próprio.
- **Performance: Surface de tela cheia do overlay administrativo deixou de ser recriada a cada
  frame**. `AdminPanel.render()` alocava um novo `pygame.Surface` do tamanho da tela inteira
  (~8MB numa TV 1080x1920) a cada chamada enquanto o painel estava aberto ou em transição. Como a
  resolução não muda em runtime, o Surface agora é criado uma vez e só tem o `fill()` (alpha)
  atualizado por frame. Demais alocações de Surface por frame identificadas (banners de aviso,
  texto renderizado pelo pygame) foram avaliadas e mantidas como estão — são pequenas, só
  acontecem enquanto um aviso está mesmo visível, e o throttle de FPS já existente (`idle_fps`
  quando nada está animando) é a mitigação certa para o caso geral; cachear haveria de adicionar
  complexidade de invalidação sem ganho real medido.
- **Auditoria de licenciamento re-executada com testes nomeados pelos três cenários pedidos**:
  `tests/test_license.py` agora tem `test_clone_scenario_original_sd_on_original_pi_works`,
  `test_clone_scenario_cloned_sd_back_on_the_same_original_pi_still_works` (troca de cartão SD no
  mesmo Pi = manutenção legítima, continua funcionando) e
  `test_clone_scenario_cloned_sd_on_a_different_pi_is_blocked` (o cenário antipirataria: mesmo
  arquivo `license.dat`, Device ID diferente = bloqueado). Também ampliada a matriz de
  fail-closed: arquivo vazio, JSON sem os campos esperados, e chave pública errada embarcada no
  app — todos rejeitam corretamente, nenhum "passa por acidente".
- **Busca por chave privada vazada**: varredura completa no repositório (working tree + todo o
  histórico de commits) por qualquer material `BEGIN PRIVATE KEY`/`.pem`. A única chave privada
  existente no ambiente de desenvolvimento vive em `../license-generator/keys/private_key.pem`
  (fora de `roulette-display/`, a árvore que é distribuída para o Raspberry), nunca foi commitada
  (confirmado com `git status --short --untracked-files=all` e uma busca em todo o histórico) e
  está protegida por `.gitignore`. Nenhum achado CRÍTICO.
- **Novos testes de persistência sob falha real**: `tests/test_persistence_failure.py` inclui um
  cenário que de fato mata um processo Python com `SIGKILL` logo após o commit de um `INSERT` (não
  simulado — um `subprocess` real é morto de verdade) e confirma que o giro sobrevive na reabertura
  do banco, além de cenários de reabertura em modo WAL e com arquivos `-wal`/`-shm` órfãos.
  Documentado com a mesma honestidade da revisão anterior: isso valida a camada de software
  (SQLite + WAL + `synchronous=FULL`), não corte de energia físico real ou corrupção de cartão SD
  em nível de bloco — só um teste no Raspberry Pi físico valida isso.
- **Teste de stress/soak**: `tests/test_soak.py` roda 100.000 giros (incluindo zero, undo, novas
  sessões, backups) só na camada de banco/lógica, sem abrir uma janela pygame — separado de
  propósito do teste de renderização, para não misturar duas preocupações de performance
  diferentes (IOPS de banco vs. FPS de tela). Neste ambiente: ~26s de inserts (0,25ms/giro em
  média), banco final de ~12,7MB, `get_display_state()` continua em ~14ms mesmo com ~100 mil
  linhas, sem crescimento de memória fora do normal. É opt-in
  (`ROULETTE_RUN_SOAK=1 pytest tests/test_soak.py`) porque é pesado de propósito (usa o mesmo
  `synchronous=FULL` com fsync por commit que roda em produção); uma versão rápida (2.000 giros)
  roda sempre, como alarme de regressão.
- **Infraestrutura para teste de 24h/48h/72h** (`scripts/long_run_monitor.sh`): script que tira
  UMA amostra por chamada (RSS, %CPU, temperatura via `vcgencmd` quando disponível, total de
  giros, tamanho do banco/WAL, estado e contagem de reinícios do serviço systemd, erros recentes
  no log) e grava num CSV — pensado para ser chamado a cada minuto via `cron` durante um teste
  prolongado no Raspberry Pi físico. Este ambiente de desenvolvimento não é um Pi 3 real, então o
  script foi testado apenas quanto a não quebrar e degradar graciosamente (sem `vcgencmd`, sem o
  serviço systemd real) — a execução de 24-72h em si é uma pendência física, não algo que este
  ambiente pudesse produzir.

### Pendências que só um Raspberry Pi 3 físico pode validar

Sem fingir cobertura que não existe: os itens abaixo foram auditados por leitura de código e, onde
possível, testados neste ambiente (Xvfb + `SDL_VIDEODRIVER=dummy`/`x11`), mas o veredito final
depende de hardware real.

- Boot silencioso ponta-a-ponta (sem terminal/desktop visível) — `install.sh` configura
  `cmdline.txt`/`config.txt`/`getty` e o serviço usa `SDL_VIDEODRIVER=kmsdrm`, mas isso só se
  confirma vendo a TV real ligar.
- Numpad USB físico — o protocolo de teclas foi testado exaustivamente via eventos pygame
  sintéticos (`tests/test_input_protocol.py`), não via hardware USB real.
- Clonagem de cartão SD entre dois Raspberry Pi de verdade (o teste automatizado simula a
  diferença de Device ID via monkeypatch, não dois Pi físicos).
- Corte físico de energia (não SIGKILL de processo, que já foi testado).
- Comportamento fim-a-fim do watchdog systemd (`Type=notify`/`WatchdogSec`) sob uma trava real.
- Teste de 24h/48h/72h com `scripts/long_run_monitor.sh` — infraestrutura pronta, execução
  pendente.
- Desempenho/fluidez real via saída HDMI 1080x1920 num Pi 3 (a verificação aqui usou Xvfb, que não
  reproduz o desempenho de GPU/driver real do Pi).

## 15. Identificação, Auditoria, Analytics e Relatórios

Evolução do sistema em quatro blocos aditivos (não reescreve nada da arquitetura/protocolo já
aprovados — ver seção 14). Continua 100% offline: nada aqui depende de internet pra o painel
funcionar, e nenhum módulo destes pode atrasar o registro de um giro (ver o princípio de
arquitetura no início desta seção do código-fonte, `app/services/spin_service.py`).

**Identidade da instalação** (`app/identity/`) — `venue_name`, `table_name`, `table_code`,
`table_location`, `device_name` editáveis pelo admin; `table_id` (UUID v4) é permanente por
design — a própria API do banco recusa alterá-lo. Uma **sessão** (`sessions`, distinta do gesto
"-97 ENTER") é o período que um relatório cobre; cada uma guarda um snapshot congelado da
identidade no momento em que abriu, então renomear a mesa nunca altera relatórios já emitidos.

**Auditoria** (`app/audit/`) — `audit_log` é um catálogo geral de eventos (sistema, sessão, giro,
identidade, admin, licença, relatório), encadeado por hash SHA-256
(`verify_audit_integrity()` aponta o primeiro evento inconsistente). Campos que parecem sensíveis
(PIN, senha, token) são automaticamente trocados por `[REDACTED]` antes de gravar. Tela "Auditoria
completa" no admin com filtro por tipo de evento e paginação.

**Analytics** (`app/analytics/`) — leitura sobre dados já persistidos (nunca calculado no evento
de teclado): KPIs operacionais, distribuição por número/cor/paridade/faixa/dúzia/coluna (zero
sempre como categoria própria), hot/cold, streaks, comparação esperado (1/37) vs. observado, e um
teste qui-quadrado deliberadamente conservador (nunca declara "roda defeituosa", só sinaliza
desvio com amostra ≥185 giros). Períodos: sessão atual/anterior, hoje, ontem, 7/30/90 dias, ano,
lifetime, personalizado.

**Relatórios** (`app/reports/`) — ao "Encerrar sessão" (PIN-gated, distinto de "-97 ENTER"), gera
automaticamente PDF (via `fpdf2` — puro Python, leve pro Pi 3, sem Cairo/Pango), CSV e JSON em
`data/reports/AAAA/MM/session_code/`. `session.json` é a fonte canônica; PDF e CSV são sempre
derivados dela. Cada pacote tem `report.sha256` + `report.sig`, assinados com uma chave Ed25519
**própria** (`app/reports/signing.py`, gerada localmente no primeiro relatório) — deliberadamente
**nunca** a mesma chave da licença, porque reaproveitá-la exigiria colocar a chave privada de
licenciamento no próprio Pi, exatamente o que aquela arquitetura existe pra evitar. Falha ao gerar
o relatório nunca desfaz o encerramento da sessão nem trava o painel.

**Entrega e impressão** (`app/delivery/`) — fila persistente (`report_delivery`) para envio de
e-mail: "Encerrar sessão" só enfileira (um INSERT rápido); quem envia de verdade é
`scripts/send_pending_reports.py`, um processo **separado**, chamado periodicamente por cron —
nunca uma thread dentro do app pygame. Retentativas automáticas (até 5 tentativas por relatório).
A senha SMTP fica fora do `config.yaml`, em `data/smtp_credentials.yaml` (permissão 600) — isolamento
por permissão de arquivo, não criptografia (o mesmo modelo de ameaça já aceito para o PIN admin;
criptografar com uma chave que mora no mesmo disco não seria proteção real contra quem já tem
acesso físico ao cartão SD). Recibo de impressão térmica (`printer_service.build_receipt_text`) é
puro texto, testável sem hardware; o envio de verdade usa `python-escpos`, importado só se
impressão estiver configurada (não é dependência obrigatória do projeto). Status de rede é
**somente leitura** — não existe um gerenciador de Wi-Fi completo (selecionar/conectar SSID);
dado que scripts/nmcli e uma UI de seleção de rede num overlay pygame controlado só por teclado
são complexidade real sem hardware aqui pra validar, e a maioria das mesas fixas num cassino já
tem Ethernet, essa parte ficou deliberadamente fora do escopo desta rodada.

Para ativar o envio automático de relatórios por e-mail, adicione ao crontab do usuário que roda
o serviço:

```
*/5 * * * * /caminho/roulette-display/venv/bin/python3 /caminho/roulette-display/scripts/send_pending_reports.py
```

### Organização do menu admin em categorias

Com a soma dos itens de identidade/auditoria/analytics/relatórios/entrega descritos acima, o menu
do painel administrativo (`CTRL+ALT+A`) tinha crescido para 35 itens numa lista única. Ele agora é
navegado em duas camadas: uma tela de categorias primeiro, depois os itens da categoria escolhida.
Três categorias, cada uma reunindo funções de natureza parecida:

- **Personalização / Identificação** — como a mesa/venue se apresenta: nome, logo, código, local,
  moeda, limites exibidos, quantidade de histórico e janela estatística. Nada operacional.
- **Funções administrativas** — back-office: PIN, backups, exportação, auditoria (log + filtro +
  verificação de integridade), analytics, configuração de SMTP, status de rede, licença, e os
  controles de energia do Raspberry Pi (reiniciar/desligar) — também administração de
  infraestrutura, não do jogo em si.
- **Funções do jogo** — ações que afetam a sessão/o placar ao vivo: ver total de giros, reiniciar
  sessão (`-97` administrativo), encerrar sessão.

`ESC` na lista de itens volta pra tela de categorias; `ESC` na tela de categorias fecha a
administração (mesmo efeito de selecionar "Fechar administração", a última linha dessa tela).

### Pendências físicas específicas desta seção

- Envio real via um servidor SMTP de verdade (aqui só foi testado com `smtplib.SMTP` mockado —
  a lógica de fila/retry/status está coberta, a integração com um provedor real não).
- Impressora térmica ESC/POS real (USB ou rede) — `python-escpos` nunca foi exercitado contra
  hardware.
- Wi-Fi real via `nmcli`/NetworkManager — o status de rede só foi testado com o comando mockado
  neste ambiente, que não tem `nmcli` nem interface Wi-Fi.
