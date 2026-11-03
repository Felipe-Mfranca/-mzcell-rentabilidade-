# Radar ML — posição dos anúncios na busca do Mercado Livre

Um robô pesquisa cada (palavra-chave, anúncio) e guarda a página e a posição. O resultado vai para a planilha
compartilhada (a mesma dos comentários e tags) e **todas as máquinas leem de lá**. No modal do anúncio aparece só a
página e a posição do termo principal; o detalhe (histórico, gráficos, todos os termos) fica na aba **Ranking**.

## Quem roda o quê
- **Máquina do Eduardo (robô):** única que abre o navegador e pesquisa. 3 vezes ao dia (8h30, 13h30, 19h30) e sempre que alguém clica em "Atualizar posições agora".
- **Demais máquinas:** só leem e editam a lista de palavras-chave. Nada a instalar.

## Instalar na máquina do robô (uma vez)
1. Chrome instalado e o app já funcionando com a planilha compartilhada (`shared_config.json` preenchido).
2. `.\venv\Scripts\python.exe -m pip install -r requirements-radar.txt`
3. Copie `radar_config.exemplo.json` para `radar_config.json` e troque `"robo_ativo"` para `true`.
4. Abra o navegador do robô uma vez para entrar na **conta neutra de pesquisa** (nunca numa conta de vendedor: logado como
   vendedor o ML personaliza a busca e os anúncios sobem):
   `.\venv\Scripts\python.exe -c "from marketplaces.radar import robo; n=robo.NavegadorPlaywright(robo.config()); n._page.goto('https://www.mercadolivre.com.br/'); input('Entre na conta neutra e aperte Enter... '); n.fechar()"`
5. Se o nome da conta neutra não for `SM ONLINE`, ajuste `"viewer"` em `radar_config.json` (vazio = não confere).
6. Reinicie o app. No log deve aparecer `Robô do Radar ML ligado`.

## Planilha compartilhada
O `compartilhado/Code.gs` ganhou 3 coleções (`radar_watch`, `radar`, `radar_status`). **Cole o código novo no Apps Script
e publique uma nova versão da implantação**; sem isso a planilha recusa as coleções ("colecao invalida") e cada máquina
vê só o que ela mesma gravou.

## Bloqueios
Captcha, pedido de login ou conta errada interrompem a execução e aparecem em vermelho na aba Ranking. O que já foi
verificado fica salvo; nada é gravado como "não encontrado" por causa do bloqueio. Resolva no navegador do robô e clique em Atualizar.

## Limites
- A leitura automática da busca pública pode conflitar com os termos do Mercado Livre; o volume é mantido baixo (pausa de 3–8 s entre buscas, até 75 min por execução).
- Anúncios de catálogo oscilam: o `wid` só é o seu MLB quando você está ganhando o buy-box.
- Os trechos que dependem do HTML do ML (seletor `li.ui-search-layout__item`, parâmetros `wid`/`position`/`type` dos links, texto de captcha e do cabeçalho) estão em `robo.py` e `parse.py`; se o ML mudar o layout, são os pontos a ajustar.
