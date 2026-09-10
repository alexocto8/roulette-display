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
6. **Desabilita o ambiente gráfico** (`lightdm`/`gdm3`/`sddm`, força boot em `multi-user.target`)
   — mesmo que a imagem gravada tenha sido a "com Desktop" em vez da Lite recomendada. Sem isso,
   o gerenciador gráfico sobe sozinho e disputa o `/dev/dri` (KMSDRM) com o painel — sintoma real
   visto em campo: tela preta ou área de trabalho normal no lugar do painel, e
   `journalctl -u roulette-display` cheio de `Could not queue pageflip: -22`.
7. Ajusta o driver de vídeo do Pi 3 (`vc4-fkms-v3d` + `max_framebuffers=3` em `config.txt`) —
   mais estável nesse hardware que o KMS completo pra um app que redesenha a tela continuamente.
8. Ajusta a configuração de boot para não mostrar mensagens de kernel/Linux na tela.

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
da mesa (física, virada 90° no suporte — não é sobre um monitor que já é retrato de fábrica).

**Método recomendado — rotação por software** (validado em campo num Pi 3, é o que o
`config.yaml` já traz pronto):

1. Vire a TV fisicamente pra posição vertical.
2. Edite `config.yaml` (na raiz do projeto) e ajuste a linha `screen_rotation`:
   ```yaml
   screen_rotation: 90
   ```
3. Reinicie o serviço: `sudo systemctl restart roulette-display`.
4. Se o conteúdo aparecer de cabeça para baixo ou de lado errado, troque `90` por `270` no mesmo
   arquivo e reinicie de novo — qual dos dois bate certo depende só do sentido físico da
   montagem, não dá pra adivinhar de antemão.

Também editável em `Personalização/Identificação > Girar tela` no menu admin
(`CTRL+ALT+A`), sem precisar de SSH.

> **Alternativa por hardware/firmware** (`video=HDMI-A-1:1080x1920@60,rotate=90` em
> `/boot/firmware/cmdline.txt`): existe e é suportada pelo KMSDRM em teoria, mas **não foi o
> caminho usado/validado nas instalações reais até agora** — prefira a rotação por software acima,
> que já foi testada de ponta a ponta num Pi 3 físico.

## 7. Conferências finais antes de liberar o equipamento

- [ ] Painel sobe sozinho depois de `sudo reboot`, sem terminal/desktop visível
      (`systemctl get-default` responde `multi-user.target`).
- [ ] Teclado numérico responde (digite um número de teste e confirme com `ENTER`).
- [ ] Licença ativa (`CTRL+ALT+A` → PIN → Funções administrativas → Informações da licença).
- [ ] PIN de administrador trocado do padrão (`1234`).
- [ ] Nome do cassino/mesa, moeda e limites de aposta configurados.
- [ ] Logo do cliente importada, se houver.
- [ ] Orientação da tela correta (retrato, lado certo).
- [ ] Backup manual de teste funciona (`Funções administrativas > Fazer backup do banco`).

## 8. Solução de problemas

- **Tela preta, painel não aparece, ou aparece a área de trabalho normal do sistema em vez do
  painel**: quase sempre é o ambiente gráfico (`lightdm`/`gdm3`) disputando a tela com o app —
  confirme com `systemctl get-default`. Se responder `graphical.target` (em vez de
  `multi-user.target`), o `install.sh` não rodou completo ou a imagem foi regravada depois.
  Corrija com:
  ```bash
  sudo systemctl set-default multi-user.target
  sudo systemctl disable lightdm && sudo systemctl stop lightdm
  sudo reboot
  ```
  Se `journalctl -u roulette-display -f` mostrar `Could not queue pageflip: -22` em loop mesmo
  sem desktop nenhum rodando, é um problema de driver de vídeo do Pi 3 com KMS — o `install.sh`
  já ajusta isso automaticamente (`vc4-fkms-v3d` + `max_framebuffers=3` em `config.txt`); confirme
  que essas linhas estão presentes e, se não, adicione manualmente e reinicie.

  Não use `SDL_VIDEODRIVER=fbcon` — não existe mais no SDL2 (era do SDL 1.2) e faz o app cair
  ainda mais cedo, com "video system not initialized". O driver certo pra KMSDRM é `kmsdrm`
  mesmo (padrão do `roulette-display.service`); o app já tenta abrir a tela com `vsync=1` e tem
  retry automático pra falhas passageiras do driver logo após um restart — não precisa mexer
  nisso manualmente.
- **`git pull` recusa com "Your local changes to the following files would be overwritten by
  merge: config.yaml"**: normal em mesas já em uso — `config.yaml` é reescrito pelo próprio
  painel (menu admin: girar tela, nome do cassino, PIN etc.), então diverge do repositório.
  Instalações feitas com a versão atual do `install.sh` já protegem esse arquivo
  automaticamente (`git update-index --skip-worktree config.yaml`); numa mesa mais antiga,
  rode uma vez, via SSH/console:
  ```bash
  cd ~/roulette-display
  git update-index --skip-worktree config.yaml
  git pull --ff-only
  ```
  Isso preserva as configurações locais e resolve o problema definitivamente — `git pull` nunca
  mais tenta tocar em `config.yaml` nessa mesa, mesmo que o template do repositório mude.
- **Teclado numérico não responde**: confirme que o usuário do serviço tem acesso a
  `/dev/input/event*` (grupo `input`) — com o serviço rodando como `root` (padrão) isso nunca é
  problema.
- **Serviço reinicia em loop**: veja `logs/app.log` ou `journalctl -u roulette-display -b -l` (o
  `-l` evita cortar as linhas do traceback Python) para o motivo — o `Restart=always` mantém o
  equipamento operacional mesmo assim, mas o log tem a causa raiz.
- **Serviço demora/reinicia repetidamente logo após o boot**: veja `journalctl -u roulette-display`
  por `sd_notify failed`. Se aparecer, troque `Type=notify` por `Type=simple` e remova
  `WatchdogSec` em `systemd/roulette-display.service` (perde a recuperação automática de
  travamento, mas resolve o problema de inicialização).

Mais detalhes técnicos (hardening opcional para rodar sem root, arquitetura, limitações
conhecidas) estão no [`README.md`](../README.md), seção "8. Instalação num Raspberry Pi 3".

## 9. Suporte técnico

- **Telefone**: (15) 3190-4141 — atendimento das 08h às 18h, segunda a sexta.
- **Site**: www.octo.net.br

*OCTO Tecnologia — gestão de TI para empresas que não podem parar.*
