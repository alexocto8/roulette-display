# Assets do cassino

Coloque aqui as imagens de identidade visual. Todos os arquivos são opcionais — se um arquivo
não existir, a interface cai para um layout somente-texto (não trava, não mostra erro na tela).

| Arquivo           | Uso                                                        | Tamanho sugerido      |
|--------------------|--------------------------------------------------------------|------------------------|
| `logo.png`         | Logo na tela de boot **e** fixo no canto superior esquerdo do painel principal (sempre visível) | até ~800x600, fundo transparente (PNG com alpha) |
| `background.png`   | Fundo da tela de boot                                        | igual à resolução do monitor (ex. 1920x1080) |
| `splash.png`       | Alternativa a `background.png` na tela de boot               | igual à resolução do monitor |

Após trocar os arquivos, reinicie o serviço (`sudo systemctl restart roulette-display`) ou
reinicie o Raspberry Pi para ver a mudança.
