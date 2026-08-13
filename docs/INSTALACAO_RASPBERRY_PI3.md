# Guia de instalação — Raspberry Pi 3

Passo a passo para colocar o painel de roleta rodando em um Raspberry Pi 3 físico, do cartão SD
em branco até o equipamento subindo sozinho no boot, sem terminal nem desktop visível. Para o que
o painel faz e como usá-lo no dia a dia, veja
[`FUNCIONALIDADES.md`](FUNCIONALIDADES.md).

## 1. O que você precisa

- Raspberry Pi 3 (B ou B+) com fonte de alimentação adequada (5V/2.5A mínimo).
- Cartão microSD (8GB+, classe 10 recomendado).
- Um computador para gravar o cartão SD (Raspberry Pi Imager: <https://www.raspberrypi.com/software/>).
- Monitor/TV com entrada HDMI e cabo HDMI.
- Teclado numérico USB (ou teclado completo) — é a única entrada usada pelo operador.
- Acesso à internet no Pi durante a instalação (rede cabeada ou Wi-Fi), só para baixar
  dependências e clonar o repositório; não é necessária depois de instalado.

## 2. Gravar o sistema operacional

Recomendado: **Raspberry Pi OS Lite (64-bit)**, sem desktop — o painel não precisa de ambiente
gráfico (X11/Wayland) e assim o sistema nem tenta subir um.

1. Abra o Raspberry Pi Imager, escolha "Raspberry Pi OS Lite (64-bit)".
2. Nas opções avançadas (ícone de engrenagem), configure:
   - Hostname (ex.: `roleta-mesa01`).
   - Habilitar SSH (com senha, ou sua chave pública).
   - Usuário e senha.
   - Wi-Fi, se for usar (senão, cabo de rede).
3. Grave no cartão SD, insira no Pi e ligue.

## 3. Conectar e clonar o projeto

Pelo SSH (ou direto no console do Pi):

```bash
ssh usuario@<ip-do-pi>
git clone <url-do-repositorio> roulette-display
cd roulette-display
```

## 4. Instalar

```bash
sudo ./scripts/install.sh
sudo reboot
```

O script `install.sh`:

1. Instala dependências de sistema (Python 3, SDL2, ferramentas de build) via `apt-get`.
2. Cria um ambiente virtual Python em `venv/` e instala `requirements.txt`.
3. Cria as pastas de dados (`data/`, `logs/`, `data/backups/`, `data/exports/`).
4. Instala e habilita o serviço systemd `roulette-display` (`Restart=always`, reinicia sozinho se
   cair).
5. Desabilita o login automático do console (`getty@tty1`), para não disputar a tela com o painel.
6. Ajusta a configuração de boot para não mostrar mensagens de kernel/Linux na tela.

Depois do `reboot`, o equipamento deve subir **direto no painel** — sem terminal, sem prompt de
login, sem desktop.

> `install.sh` usa `apt-get` e é feito especificamente para Raspberry Pi OS/Debian. Não funciona
> em outras distribuições (ex.: para testar em uma VM CentOS, é preciso instalar as dependências
> manualmente e rodar `python3 main.py` direto, sem o serviço systemd).

## 5. Gerar e instalar a licença

O painel não funciona sem uma licença válida para aquele equipamento específico.

1. Com o painel já rodando (mesmo sem licença — ele mostra a tela "SISTEMA NÃO ATIVADO"), anote o
   **Device ID** que aparece nessa tela.
2. Na ferramenta `license-generator` (roda em outra máquina, não precisa estar no Pi), gere o
   arquivo de licença para esse Device ID — veja o `README.md` dentro de `license-generator/` para
   os comandos exatos.
3. Copie o arquivo gerado (`license.dat`) para o Pi, no caminho configurado em `license_path` do
   `config.yaml` (padrão: dentro da pasta do projeto).
4. Reinicie o painel (`sudo systemctl restart roulette-display`) — a tela de bloqueio some e o
   painel principal aparece.

## 6. Orientação da tela (retrato)

O layout principal é **retrato** (mais alto que largo), pensado para uma TV montada em pé ao lado
da mesa. A interface detecta a orientação sozinha a partir da resolução que o Raspberry realmente
emite — não precisa configurar nada no app para isso, só garantir que a **saída de vídeo** já
esteja em retrato antes do painel abrir a tela.

Como o Pi roda sem X11 em produção (direto via KMSDRM), a rotação é feita na saída de vídeo do
firmware/kernel, não em software:

1. Edite `/boot/firmware/cmdline.txt` (é uma linha só — não quebre em várias linhas) e adicione um
   parâmetro `video=`. Exemplo para uma TV Full HD (1920x1080) montada de lado:
   ```
   video=HDMI-A-1:1080x1920@60,rotate=90
   ```
   Troque `rotate=90` por `rotate=270` se a imagem ficar de cabeça para baixo depois de testar.
2. Reinicie. O painel detecta a resolução já rotacionada automaticamente — nenhuma configuração
   adicional é necessária.
3. Se o monitor/TV aceitar girar a **entrada** sozinho (comum em monitores de sinalização
   digital), teste sem o parâmetro `video=` primeiro — pode não ser necessário.

**Se o monitor não suportar nenhuma rotação por hardware/firmware** (situação rara em produção,
mais comum ao testar em uma VM antes do hardware físico chegar): existe uma rotação por
**software** de reserva, `screen_rotation` em `config.yaml` (também editável em
`Personalização/Identificação > Girar tela` no menu admin). Deixe em `0` no Raspberry Pi real —
use isso só se o passo acima não funcionar.

## 7. Conferências finais antes de liberar o equipamento

- [ ] Painel sobe sozinho depois de `sudo reboot`, sem terminal/desktop visível.
- [ ] Teclado numérico responde (digite um número de teste e confirme com `ENTER`).
- [ ] Licença ativa (`CTRL+ALT+A` → PIN → Funções administrativas → Informações da licença).
- [ ] PIN de administrador trocado do padrão (`1234`).
- [ ] Nome do cassino/mesa, moeda e limites de aposta configurados.
- [ ] Logo do cliente importada, se houver.
- [ ] Orientação da tela correta (retrato, lado certo).
- [ ] Backup manual de teste funciona (`Funções administrativas > Fazer backup do banco`).

## 8. Solução de problemas

- **Tela preta, painel não aparece**: `journalctl -u roulette-display -f`. Se o SDL não conseguir
  abrir KMSDRM (placa/driver não suportado), edite `systemd/roulette-display.service` trocando
  `Environment=SDL_VIDEODRIVER=kmsdrm` por `Environment=SDL_VIDEODRIVER=fbcon`, depois:
  ```bash
  sudo systemctl daemon-reload && sudo systemctl restart roulette-display
  ```
- **Teclado numérico não responde**: confirme que o usuário do serviço tem acesso a
  `/dev/input/event*` (grupo `input`) — com o serviço rodando como `root` (padrão) isso nunca é
  problema.
- **Serviço reinicia em loop**: veja `logs/app.log` ou `journalctl -u roulette-display` para o
  motivo — o `Restart=always` mantém o equipamento operacional mesmo assim, mas o log tem a causa
  raiz.
- **Serviço demora/reinicia repetidamente logo após o boot**: veja `journalctl -u roulette-display`
  por `sd_notify failed`. Se aparecer, troque `Type=notify` por `Type=simple` e remova
  `WatchdogSec` em `systemd/roulette-display.service` (perde a recuperação automática de
  travamento, mas resolve o problema de inicialização).

Mais detalhes técnicos (hardening opcional para rodar sem root, arquitetura, limitações
conhecidas) estão no [`README.md`](../README.md), seção "8. Instalação num Raspberry Pi 3".
