# MZCell Rentabilidade — Instruções para Claude Code

## REGRAS OBRIGATÓRIAS — TODA SESSÃO

### Ao iniciar qualquer sessão
```
git pull origin main
git status
```
Verificar se há conflitos antes de qualquer alteração. Se houver conflito: **parar e avisar o usuário antes de continuar**.

### Ao finalizar qualquer alteração
```
git add [arquivos alterados]
git commit -m "tipo: descrição clara do que foi feito"
git push origin main
```
Nunca deixar alterações sem commit ao encerrar uma sessão.

### Proibido
- `git push --force` — nunca, em nenhuma circunstância
- Commitar qualquer arquivo da lista de sensíveis abaixo
- Fazer push sem antes verificar `git status`

### Padrão de mensagem de commit
| Prefixo | Quando usar |
|---|---|
| `feat:` | nova funcionalidade adicionada |
| `fix:` | correção de bug |
| `refactor:` | reorganização sem mudança de comportamento |
| `bump:` | atualização de versão |
| `docs:` | alteração em documentação |

---

## ARQUIVOS SENSÍVEIS — NUNCA COMMITAR

Já estão no `.gitignore`. Não usar `git add -f` neles.

| Arquivo | Motivo |
|---|---|
| `config.json` | Tokens OAuth do Mercado Livre (meli01/02/03) |
| `produtos.json` | Base de dados local com custos e histórico |
| `usuarios.json` | Usuários e senhas (mesmo que hasheadas) |
| `email_config.json` | Senha de app Gmail (SMTP) |
| `venv/` | Ambiente Python (pesado, regenerável) |
| `python-embed/` | Python embarcado para o instalador |
| `mzcell-electron/node_modules/` | Dependências Node (regeneráveis) |
| `mzcell-electron/dist/` | Instaladores gerados |

---

## ARQUITETURA DO PROJETO

### Visão geral
```
mzcell-rentabilidade/
├── main.py                  # Backend FastAPI — porta 8001
├── ml_api.py                # Integração API Mercado Livre
├── sync_service.py          # Sincronização em background
├── sync_produto.py          # Sync de produto individual
├── parser_rentabilidade.py  # Cálculo de margens e KPIs
├── refresh_ml_token.py      # Renovação automática de tokens OAuth
├── ranking_ml.py            # Ranking de produtos ML
├── auth_service.py          # Autenticação: login, email, recuperação de senha
├── dashboard.html           # Frontend SPA (HTML + CSS + JS inline)
├── version.txt              # Versão atual (ex: 1.1.2) — controla auto-update
├── config.json              # Credenciais ML e configurações (NÃO sobe)
├── produtos.json            # DB local de produtos e rentabilidade (NÃO sobe)
├── usuarios.json            # Usuários com senhas SHA-256 (NÃO sobe)
├── email_config.json        # Config SMTP Gmail (NÃO sobe)
└── mzcell-electron/
    ├── index.js             # Entry point Electron + auto-update
    ├── package.json         # Config build (versão deve espelhar version.txt)
    └── preload.js
```

### Stack
- **Backend:** Python 3 + FastAPI + uvicorn, porta `127.0.0.1:8001`
- **Frontend:** SPA em `dashboard.html` servido pelo FastAPI em `GET /`
- **Desktop:** Electron 35 empacota o backend + frontend como app Windows
- **Auto-update:** ao iniciar, Electron compara `version.txt` local com GitHub; se diferente, baixa os arquivos `.py` e `.html` atualizados antes de subir o servidor

### Contas Mercado Livre
| Conta | Descrição |
|---|---|
| `meli01` | Conta principal |
| `meli02` | Conta secundária |
| `meli03` | Conta terciária |

Cada conta tem `client_id`, `client_secret`, `access_token`, `refresh_token` e `seller_id` em `config.json`.

### Autenticação de usuários
Senhas armazenadas como SHA-256 em `usuarios.json`. Roles disponíveis:

| Role | Permissões |
|---|---|
| `master` | tudo |
| `adminplus` | ver, sync, custos, anotações, simulador |
| `viewer` | apenas visualização |

Fluxo de primeiro acesso: login → detecta `primeiro_acesso: true` → solicita email → envia código 6 dígitos via SMTP → confirma código → define nova senha.

---

## FLUXO DE PUBLICAÇÃO DE NOVA VERSÃO

1. Fazer as alterações nos arquivos `.py` / `.html`
2. Commitar e fazer push para o GitHub
3. Incrementar `version.txt` (ex: `1.1.2` → `1.1.3`) e fazer push
4. Atualizar `"version"` em `mzcell-electron/package.json` para o mesmo número
5. Gerar instalador:
   ```
   cd mzcell-electron
   npm run build
   ```
6. Publicar release no GitHub via API (criar tag, upload `.exe` + `latest.yml` + `.blockmap`)

Na próxima abertura do app instalado, o auto-update detecta a nova versão e baixa os arquivos atualizados automaticamente.

---

## REPOSITÓRIO

- **URL:** https://github.com/Felipe-Mfranca/-mzcell-rentabilidade-
- **Branch principal:** `main`
- **Raw base URL:** `https://raw.githubusercontent.com/Felipe-Mfranca/-mzcell-rentabilidade-/main`
- **Visibilidade:** público (necessário para o auto-update funcionar sem autenticação)

---

## COMO INICIAR O SERVIDOR (DESENVOLVIMENTO)

```powershell
cd E:\mzcell-rentabilidade
.\venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8001
```

Acessar em: http://127.0.0.1:8001

## COMO INICIAR O APP ELECTRON (DESENVOLVIMENTO)

```powershell
cd E:\mzcell-rentabilidade\mzcell-electron
npx electron .
```

---

## AVISOS IMPORTANTES

- **package.json nunca editar com `ConvertTo-Json` do PowerShell 5.1** — corrompe o JSON com BOM e espaços extras. Sempre usar regex direto na string ou reescrever o arquivo com `[System.IO.File]::WriteAllText(..., $utf8NoBom)`.
- **version.txt nunca salvar com `Set-Content -Encoding UTF8`** — adiciona BOM, o que quebra a comparação de versão no Electron. Usar `[System.IO.File]::WriteAllText` com `UTF8Encoding($false)`.
- **Porta 8001 em uso:** se o servidor não iniciar, verificar com `netstat -ano | findstr :8001` e encerrar o processo antes.
- **SSL no httpx/requests:** o ambiente usa `verify=False` nas chamadas à API ML por questões de certificado local.

---

## HISTÓRICO DE DECISÕES TÉCNICAS

### Sessão 29/05/2026 — Auditoria de receita + melhorias de autenticação

#### Correção pack_splitted (v1.1.3)
- **Problema:** sistema incluía pedidos `pack_splitted` no GMV bruto, inflando receita
- **Causa:** ML cancela order original e recria novo ao dividir pack — são cancelamentos técnicos, não reais
- **Solução:** `parser_rentabilidade.py` — excluir orders com `cancel_detail.code == "pack_splitted"` antes de acumular receita
- **Validação:** 2 MLBs × 4 cenários vs Seller Center — impacto −R$2.216,25 no período 28/04→27/05
- **Decisão fuso:** manter `-04:00` — trocar para `-03:00` piora C3 sem benefício nos demais; resíduo de ~R$66–138 por período é aceitável (~0,09%)

#### Melhorias de autenticação (v1.1.5)
- Eye icon mostrar/ocultar senha no login e em configurações
- Login case-insensitive — `.lower()` em `auth_service.py:verificar_login()`
- Novo endpoint `POST /usuario/senha` — alteração de senha pelo próprio usuário
- Removida função morta `verificar_usuario()` em `main.py` (comparava senha sem hash)

#### Varredura de arquivos não atualizáveis
- `auth_service.py` não estava em `ARQUIVOS_ONLINE` no `index.js` — corrigido em v1.1.9
- `index.js` fica dentro de `app.asar` — **nunca é atualizado via push no GitHub**
- Consequência: qualquer lógica nova no `index.js` só chega ao usuário via novo instalador

#### Popup de changelog (pendente — v1.2.0)
- Implementado mas não funcional — popup dispara antes do login, usuário não vê
- Abordagem via `flag_atualizacao.txt` não funciona pois `index.js` está no `app.asar`
- Abordagem via comparação `version.txt` vs `version_anterior.txt` implementada mas popup ainda não aparece
- **Decisão:** mover para v1.2.0 junto com redesign e renomeação

#### Redesign do dashboard (pendente — v1.2.0)
- Protótipo aprovado: fundo branco, cinza grafite na topbar, verde escuro nos destaques, escala de cinza nos elementos estruturais
- Paleta semântica: verde `#166534` positivo, âmbar `#854d0e` atenção, vermelho `#991b1b` alerta
- Aguarda aprovação da diretoria antes de implementar

#### Decisões arquiteturais
- Alíquota de imposto meli02: **não corrigir no parser** — virá via importar custos (produtos.json sobrescreve o default)
- Controle de acesso por role nos endpoints de importação: `tem_permissao()` existe mas não está conectada — implementar na v1.1.x
- SHA-256 sem salt nas senhas: risco baixo no momento — mover para v1.2.0
