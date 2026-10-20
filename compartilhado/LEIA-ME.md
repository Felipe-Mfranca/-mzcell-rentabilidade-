# Banco compartilhado no Google Sheets (comentários, posições e marcações de clip)

Cada pessoa roda o app no próprio computador. Para que comentários, posição dos anúncios e marcações de clip
apareçam para todos, o app grava e lê uma planilha do Google. Isso é feito **automaticamente em segundo plano**;
o Claude não participa do funcionamento.

## 1. Criar a planilha e o script (uma vez, na conta da empresa)
1. Em sheets.google.com, crie uma planilha em branco chamada **MZ-app dados**.
2. Menu **Extensões > Apps Script**. Apague o que estiver no editor e cole o conteúdo de `compartilhado/Code.gs`. Salve.
3. Defina o código de acesso (uma senha longa que só a equipe conhece):
   - Engrenagem **Configurações do projeto** > **Propriedades do script** > **Adicionar propriedade**.
   - Nome: `TOKEN`. Valor: invente uma sequência longa (mínimo 30 caracteres, letras e números).
4. **Implantar > Nova implantação** > tipo **App da Web**:
   - Executar como: **Eu**.
   - Quem tem acesso: **Qualquer pessoa**.
   - Autorize quando o Google pedir. Copie a **URL do app da Web** (termina em `/exec`).

A URL sozinha não dá acesso: sem o `TOKEN` o script recusa qualquer pedido.

## 2. Configurar em cada computador
1. Copie `shared_config.exemplo.json` para `shared_config.json` (na pasta do app) e preencha:
   ```json
   { "url": "<a URL que termina em /exec>", "token": "<o mesmo TOKEN do passo 1.3>", "intervalo_seg": 60 }
   ```
2. Teste a conexão: `.\venv\Scripts\python.exe -m shared_store --testar`
   (mostra apenas "conectado" ou o motivo da falha).

`shared_config.json` fica fora do GitHub. Não envie o TOKEN por e-mail aberto.

## 3. O que passa a ser compartilhado
- **Comentários** do modal (cada comentário novo, com o login de quem escreveu).
- **Posição do anúncio** (página, posição e palavra-chave).
- **Marcação de clip** do PIVO (tem clip, sem clip).

Abas criadas sozinhas na planilha: `comentarios`, `posicoes`, `clips`. Dá para ler e corrigir direto nelas.

## 4. Como funciona
- Ao salvar, o app grava no computador e envia à planilha. Sem internet, o envio fica numa fila e sobe depois.
- A cada 60 segundos o app busca o que mudou na planilha e atualiza os arquivos locais.
- Conflito: vale a gravação mais recente.
- Se trocar o TOKEN (por exemplo, computador perdido), altere a propriedade no Apps Script e o `shared_config.json` dos demais.
