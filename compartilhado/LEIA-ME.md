# Banco compartilhado no Google Sheets (comentários, posições e marcações de clip)

Cada pessoa roda o app no próprio computador. Para que comentários, posição dos anúncios e marcações de clip
apareçam para todos, o app grava e lê uma planilha do Google. Isso é feito **automaticamente em segundo plano**;
o Claude não participa do funcionamento.

## 1. Criar tudo pelo script.google.com (uma vez, na conta da empresa)
1. Abra **script.google.com** (de preferência em janela anônima, com uma conta só) > **Novo projeto**.
2. Apague o código e cole o conteúdo de `compartilhado/Code.gs`. Salve.
3. Escolha a função **`configurar`** na lista do editor e clique em **Executar**. Autorize quando o Google pedir.
   Ela cria a planilha **MZ-app dados** na sua conta, gera o **TOKEN** e cria as abas.
4. Em **Registro de execução** aparecem o endereço da planilha e o **TOKEN**. Copie o TOKEN (para o passo 2 abaixo).
   Para ver de novo, rode a função **`mostrarDados`**.
5. **Implantar > Nova implantação** > tipo **App da Web**: executar como **Eu**, quem tem acesso **Qualquer pessoa**.
   Autorize e copie a **URL do app da Web** (termina em `/exec`).

A URL sozinha não dá acesso: sem o `TOKEN` o script recusa qualquer pedido.

(Alternativa manual: criar uma Planilha Google nativa, abrir Extensões > Apps Script, colar o código e definir as
propriedades `TOKEN` e, se o projeto for avulso, `PLANILHA_ID`. A planilha precisa ser nativa, não um `.xlsx`.)

## 2. Configurar em cada computador
1. Copie `shared_config.exemplo.json` para `shared_config.json` (na pasta do app) e preencha:
   ```json
   { "url": "<a URL que termina em /exec>", "token": "<o TOKEN do passo 1.4>", "intervalo_seg": 60 }
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
