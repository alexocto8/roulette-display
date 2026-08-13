# Manual de funcionalidades — Painel de Roleta v1.0.2

Guia prático de uso do painel eletrônico de roleta: o que aparece na tela, o que cada tecla faz e
o que cada função do menu administrativo faz. Para instalação em um Raspberry Pi 3 do zero, veja
[`INSTALACAO_RASPBERRY_PI3.md`](INSTALACAO_RASPBERRY_PI3.md). Para detalhes técnicos/arquitetura,
veja o [`README.md`](../README.md) na raiz do projeto.

## 1. A tela principal

A tela é dividida em cinco áreas, de cima para baixo:

1. **Limites de aposta** — "APOSTA MIN." / "APOSTA MAX.", configurados pelo administrador.
2. **Três colunas**:
   - **FRIO** (esquerda, azul): os números que estão há mais giros sem sair.
   - **ÚLTIMO RESULTADO** (centro): o número mais recente em destaque, com contorno branco, e
     logo abaixo um histórico dos giros anteriores — um número por linha, sempre na "raia" da sua
     própria cor (preto à esquerda, vermelho à direita, zero no meio), mais recente no topo.
   - **QUENTE** (direita, vermelho): os números que mais saíram na janela estatística
     configurada, com a logo do cassino/casa embaixo.
3. **Barra de estatística** (rodapé): sete cartões com percentuais — ÍMPAR, PAR, VERMELHO, ZERO,
   PRETO, MENOR (1-18), MAIOR (19-36).
4. **Indicador "● SISTEMA OK"** discreto no canto: fica verde só quando o banco de dados está
   respondendo normalmente. Se ficar vermelho/laranja por muito tempo, é sinal de problema —
   contate o suporte técnico.
5. **Animação de revelação**: toda vez que um resultado é confirmado, a tela inteira mostra por 5
   segundos um círculo grande na cor do número (verde para zero, vermelho/preto para os demais),
   com a classificação completa (VERMELHO/PRETO/ZERO, ÍMPAR/PAR, MENOR/MAIOR) e um beep curto.
   **Enquanto ela está na tela, o sistema não registra um número novo**: pode digitar à vontade,
   mas o `ENTER` só confirma depois que os 5s acabarem — os dígitos digitados não se perdem,
   basta apertar `ENTER` de novo. Desfazer o último resultado (`DEL DEL` ou `-` `ENTER`) continua
   funcionando normalmente durante a revelação.

As estatísticas são **descritivas do histórico** — a roleta não tem memória, "frio"/"quente" não
preveem o próximo resultado.

## 2. Registrando resultados (uso do dia a dia)

| Tecla | O que faz |
|---|---|
| `0`-`9` (numérico ou linha superior) | Digita o número do resultado (até 2 dígitos) |
| `ENTER` | Confirma o número digitado (aceita só 0 a 36) |
| `BACKSPACE` | Apaga o último dígito digitado, antes de confirmar |
| `ESC` ou `.` (ponto) | Cancela a digitação atual, ou um comando `-`/`-97` em andamento |
| `+` | Marca "novo giro" na tela (aviso piscando, só visual — some sozinho) |

**Fluxo normal**: operador vê a bolinha cair no número X → digita `X` → `ENTER` → tela confirma
("REGISTRADO • X") e a animação de revelação aparece por 5s. **O próximo número só é registrado
depois que a animação terminar** — pode digitar antes, mas o `ENTER` de confirmação só tem efeito
quando os 5s acabarem.

### Corrigindo um erro de digitação

| Tecla | O que faz |
|---|---|
| `DEL`, `DEL` (duas vezes, em poucos segundos) | Desfaz o último resultado confirmado |
| `CTRL`+`BACKSPACE` | Desfaz o último resultado imediatamente, sem pedir confirmação |
| `-` `ENTER` | Mesma coisa — desfaz o último resultado, sem confirmação |

### Reiniciando a sessão da mesa

`-` `9` `7` `ENTER` → a tela pede confirmação (`ENTER` de novo confirma, `ESC` cancela) → zera o
placar visível na tela (contador de giros, histórico, estatísticas voltam a zero).

**Importante**: isso **não apaga dado nenhum**. Os giros continuam salvos no banco para auditoria
e exportação — "reiniciar sessão" só limpa o que aparece na tela, é o equivalente a começar uma
nova temporada/turno na mesma mesa.

## 3. Administração (`CTRL`+`ALT`+`A`)

Pede o PIN de administrador (padrão `1234` — **troque antes de usar em produção**, veja a seção
5). O menu é organizado em três categorias:

### Personalização / Identificação

Como a mesa se apresenta, sem afetar o jogo em andamento:

- Nome do cassino/venue, nome da mesa, código da mesa, local/setor, nome técnico do dispositivo.
- Ver o ID permanente da mesa (não muda mesmo trocando nome/código).
- Importar/remover logo (copie o arquivo para a pasta `data/branding/incoming/` via USB/SCP antes
  de importar).
- Quantidade de histórico exibido, janela estatística (quantos giros entram na conta de
  frio/quente), moeda e limites de aposta exibidos.
- Girar tela (`0`→`90`→`180`→`270` a cada `ENTER`) — só necessário em cenários específicos onde o
  monitor não gira sozinho (veja o guia de instalação); tem efeito só depois de reiniciar o painel.

### Funções administrativas

Back-office e infraestrutura:

- Trocar PIN de administrador.
- **Analytics** (sessão atual / hoje): indicadores estatísticos mais detalhados que a barra
  principal.
- **Auditoria**: ver correções feitas (desfazer/reiniciar sessão), auditoria completa com
  filtros, e verificação de integridade do log de auditoria (detecta se algum registro foi
  adulterado).
- **Exportar resultados (CSV)**, **fazer backup do banco**, **importar backup**.
- Configuração de e-mail (SMTP: servidor, usuário, senha, destinatário, teste de envio) — para
  envio automático de relatórios.
- Ver status de rede.
- **Informações da licença**: versão do sistema instalada, ID único do equipamento (Device ID),
  status da licença (ativa/expirada/inválida), cliente e validade.
- Reiniciar o painel (sai e o systemd sobe de novo), reiniciar o Raspberry Pi, desligar o
  Raspberry Pi.

### Funções do jogo

Ações que afetam a sessão ao vivo:

- Ver total de giros registrados.
- Reiniciar sessão atual (mesmo efeito do atalho `-97 ENTER`, mas pelo menu).
- Encerrar sessão (fecha a sessão atual e inicia uma nova — usado para trocar de turno/dia).

`ESC` na lista de itens volta para a tela de categorias; `ESC` na tela de categorias fecha a
administração.

## 4. Licenciamento

O painel só funciona com uma licença válida para **aquele equipamento específico** (o Device ID é
derivado do hardware, não pode ser copiado para outro Raspberry Pi). Sem licença válida, a tela
mostra "SISTEMA NÃO ATIVADO" (ou "LICENÇA INVÁLIDA"/"LICENÇA EXPIRADA", conforme o caso) em vez do
painel — nada de dado é perdido, é só um bloqueio de acesso até a licença ser corrigida. O Device
ID necessário para gerar a licença aparece nessa mesma tela e em `Funções administrativas >
Informações da licença`.

## 5. Antes de colocar em produção

- **Troque o PIN de administrador padrão** (`Funções administrativas > Trocar PIN`, ou o campo
  `admin_pin` em `config.yaml`).
- Configure nome do cassino/mesa, moeda e limites de aposta reais.
- Importe a logo do cliente, se houver.
- Confirme que a licença está ativa (`Informações da licença`).
- Faça um backup manual de teste (`Fazer backup do banco`) para confirmar que está funcionando.
