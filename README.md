# MZCell Rentabilidade — App Desktop

## Arquitetura
Electron 35 + FastAPI (Python) + HTML/CSS/JS puro
Mesma estrutura do FMF Monitor de Catálogos.

## Estrutura de Arquivos
```
mzcell-rentabilidade/
├── main.py                      # Backend FastAPI (porta 8001)
├── ml_api.py                    # Integração API Mercado Livre
├── parser_rentabilidade.py      # Cálculos de margem, TACOS, ROAS
├── refresh_ml_token.py          # Renovação automática de tokens
├── config.json                  # Tokens, contas, impostos
├── contas.json                  # Dados das contas
├── produtos.json                # DB local dos produtos com métricas
├── usuarios.json                # Usuários do sistema
├── historico_rentabilidade.json # Histórico de syncs
├── alertas.json                 # Alertas gerados
├── log_atividades.json          # Log de ações
├── dashboard.html               # Frontend
└── mzcell-electron/
    ├── index.js                 # Electron main process
    ├── preload.js               # Context bridge
    └── package.json             # Config + build

## Como rodar em desenvolvimento
# Terminal 1 — Backend
cd mzcell-rentabilidade
venv\Scripts\python.exe -m uvicorn main:app --port 8001

# Terminal 2 — Electron
cd mzcell-electron
npm start

## Como configurar as contas ML

### 1. Cada CEO cria um App no ML
- Acesse: https://developers.mercadolivre.com.br
- Logue com a conta Meli01 (ou Meli03)
- Crie um novo app e anote Client ID e Client Secret
- Redirect URI: https://seusite.com/oauth

### 2. Configure no Painel
- Abra o app → aba Configurações
- Preencha Client ID e Client Secret de cada conta
- Clique em "Salvar e Autorizar"
- Abra a URL gerada em aba anônima logado na conta correta
- Cole o código TG-... no campo abaixo
- Clique em "Trocar por Token"

### 3. Sincronize os dados
- Clique em "↻ Sincronizar API ML" na aba de cada conta

## Build — Gerar instalador .exe
cd mzcell-electron
npm run build
# Arquivo gerado em: dist/MZCell Rentabilidade Setup 1.0.0.exe

## Permissão de pasta (após instalar)
icacls "C:\Program Files\mzcell-rentabilidade\resources" /grant *S-1-1-0:F /T

## Endpoints da API
GET  /                              → Dashboard HTML
POST /auth/login                    → Login
GET  /config/contas                 → Status das contas
POST /config/conta                  → Salvar Client ID/Secret
GET  /auth/url/{conta}              → Gera URL de autorização ML
POST /auth/autorizar                → Troca código TG por token
POST /auth/refresh/{conta}          → Renova token
GET  /produtos/{conta}              → Lista produtos ativos (API ML)
GET  /produtos/{conta}/db           → Produtos no DB local
POST /produtos/custos               → Salva CMV, frete, imposto
POST /sync/{conta}                  → Sincroniza conta com API ML
GET  /rentabilidade/{conta}         → Rentabilidade por período
GET  /log                           → Log de atividades
GET  /alertas                       → Alertas

## Tecnologias
- Backend: Python 3.12 + FastAPI + Uvicorn + httpx
- Frontend: HTML/CSS/JS puro
- Desktop: Electron 35
- Build: electron-builder + NSIS

## Próximos Passos
1. ✅ Estrutura completa do projeto
2. ✅ Backend FastAPI com todos os endpoints
3. ✅ Integração API ML (tokens, produtos, vendas, ADS)
4. ✅ Parser de rentabilidade (margem, TACOS, ROAS, ACOS)
5. ✅ Frontend dashboard com login, KPIs, tabela, modal
6. ✅ Electron app desktop
7. ⏳ Criar venv com dependências (pip install)
8. ⏳ Configurar tokens das contas Meli01 e Meli03
9. ⏳ Gerar instalador .exe
10. ⏳ Testar sincronização com API ML real
