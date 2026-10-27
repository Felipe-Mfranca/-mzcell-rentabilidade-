"""
main.py — Backend FastAPI
MZ App
"""

import json
import os
import asyncio
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile, File
import io
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import ml_api
import parser_rentabilidade as parser
import sync_service
import produtos_store
import auth_service
from refresh_ml_token import loop_refresh, loop_sync_diario

# Módulos novos (podem faltar em instalações antigas que só receberam parte dos arquivos pelo auto-update)
try:
    import shared_store
except ImportError:
    class _SemCompartilhado:
        """Sem shared_store.py: os comentários continuam só locais."""
        @staticmethod
        def enviar_async(*a, **k):
            return None

        @staticmethod
        def enviar_lote_async(*a, **k):
            return None

        @staticmethod
        def configurado():
            return False

        @staticmethod
        def sincronizar(*a, **k):
            return {"configurado": False}

        @staticmethod
        def agora_iso():
            from datetime import timezone
            return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

        @staticmethod
        def iniciar_sincronizacao(*a, **k):
            return None
    shared_store = _SemCompartilhado()


def _modulo(nome: str):
    """Importa um módulo opcional; sem ele, a rota responde 503 em vez de quebrar."""
    import importlib
    try:
        return importlib.import_module(nome)
    except ImportError:
        raise HTTPException(status_code=503, detail=f"recurso indisponível nesta instalação ({nome}); reinstale o app")


# ─── PATHS ───────────────────────────────────────────────────────────────────
BASE_DIR        = os.path.dirname(__file__)
CONFIG_PATH     = os.path.join(BASE_DIR, "config.json")
USUARIOS_PATH   = os.path.join(BASE_DIR, "usuarios.json")
PRODUTOS_PATH   = os.path.join(BASE_DIR, "produtos.json")
CONTAS_PATH     = os.path.join(BASE_DIR, "contas.json")
LOG_PATH        = os.path.join(BASE_DIR, "log_atividades.json")
ALERTAS_PATH    = os.path.join(BASE_DIR, "alertas.json")
DASHBOARD_PATH  = os.path.join(BASE_DIR, "dashboard.html")

# ─── APP ─────────────────────────────────────────────────────────────────────
app = FastAPI(title="MZ App API", version="1.0.0")
from fastapi.middleware.gzip import GZipMiddleware
app.add_middleware(GZipMiddleware, minimum_size=1000)   # HTML e JSON comprimidos: a tela carrega mais rápido
# Só origens locais: com "*", qualquer site aberto no navegador conseguiria ler/chamar
# o backend em 127.0.0.1 (inclusive /amazon/* e as rotas de custos).
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^http://(127\.0\.0\.1|localhost)(:\d+)?$",
    allow_methods=["*"],
    allow_headers=["*"],
)

# Módulos de marketplace (marketplaces/<nome>/router.py). Se o módulo não estiver
# presente — ex.: instalação que ainda não recebeu a pasta marketplaces/ — o resto
# do backend sobe normalmente.
try:
    from marketplaces.amazon.router import router as amazon_router
    app.include_router(amazon_router)
except ImportError as e:
    print(f"[startup] Módulo Amazon indisponível: {e}")

try:
    from marketplaces.shopee.router import router as shopee_router
    app.include_router(shopee_router)
except ImportError as e:
    print(f"[startup] Módulo Shopee indisponível: {e}")

try:
    from marketplaces.expedicao.router import router as expedicao_router
    app.include_router(expedicao_router)
except ImportError as e:
    print(f"[startup] Módulo Expedição indisponível: {e}")


_syncs_em_andamento: set = set()


async def _sync_background(conta: str):
    _syncs_em_andamento.add(conta)
    try:
        await sync_service.sincronizar_conta_func(conta)
    except Exception as e:
        log_atividade(f"Erro sync background {conta}: {e}")
    finally:
        _syncs_em_andamento.discard(conta)


@app.on_event("startup")
async def startup():
    asyncio.create_task(loop_refresh())
    asyncio.create_task(loop_sync_diario())
    # ROAS objetivo dos anúncios que ainda não têm o campo (segundo plano; não espera o ML)
    asyncio.create_task(sync_service.preencher_roas_faltando())
    # CMV da base central nos produtos e nas margens salvas: vale em cada máquina, sem depender de novo sync
    try:
        import cmv_base as _cmv
        _cmv.propagar_ml()
    except Exception as e:
        print(f"[startup] Aviso CMV: {e}")
    # Verifica e renova token imediatamente no startup
    try:
        from refresh_ml_token import verificar_e_renovar
        await verificar_e_renovar()
        print("[startup] Token verificado/renovado com sucesso")
    except Exception as e:
        print(f"[startup] Aviso token: {e}")
    # Banco compartilhado (Google Sheets): só liga se shared_config.json estiver preenchido
    try:
        if shared_store.configurado():
            auth_service.publicar_todos()      # semeia a planilha; troca de senha mais nova sempre prevalece
        if shared_store.iniciar_sincronizacao(_APLICADORES_COMPARTILHADOS):
            print("[startup] Sincronização com a planilha compartilhada ligada")
    except Exception as e:
        print(f"[startup] Aviso planilha compartilhada: {e}")


# ─── HELPERS ─────────────────────────────────────────────────────────────────

def load_json(path: str) -> any:
    if os.path.abspath(path) == os.path.abspath(PRODUTOS_PATH):
        return produtos_store.ler()
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(path: str, data: any):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def log_atividade(msg: str):
    logs = load_json(LOG_PATH) or []
    logs.append({"ts": datetime.now().isoformat(), "msg": msg})
    save_json(LOG_PATH, logs[-500:])


# ─── FRONTEND ────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    # sem cache: depois de uma atualização a tela não pode continuar com o JS antigo
    with open(DASHBOARD_PATH, "r", encoding="utf-8") as f:
        return HTMLResponse(f.read(), headers={"Cache-Control": "no-store"})


# ─── AUTH ─────────────────────────────────────────────────────────────────────

@app.post("/auth/login")
async def login(payload: dict):
    login_val = payload.get("login", "").strip().lower()
    senha     = payload.get("senha", "")
    u = auth_service.verificar_login(login_val, senha)
    if not u:
        # a senha pode ter sido trocada em outro computador: traz os usuários da planilha e tenta de novo
        try:
            shared_store.sincronizar({"usuarios": _aplicar_usuarios})
            u = auth_service.verificar_login(login_val, senha)
        except Exception:
            u = None
    if not u:
        raise HTTPException(status_code=401, detail="Login ou senha incorretos")
    log_atividade(f"Login: {u['nome']}")
    return {
        "success":         True,
        "login":           u["login"],
        "nome":            u["nome"],
        "role":            u["role"],
        "contas":          u.get("contas", []),
        "modulos":         u.get("modulos", ["todos"]),
        "primeiro_acesso": u.get("primeiro_acesso", False),
        "email":           u.get("email", ""),
    }

@app.post("/auth/enviar-codigo")
async def enviar_codigo(payload: dict):
    email = payload.get("email", "").strip()
    login_val = payload.get("login", "").strip()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Email inválido")
    usuarios = auth_service.load_usuarios()
    for u in usuarios:
        if u.get("email") == email and u["login"] != login_val:
            raise HTTPException(status_code=400, detail="Este email já está em uso")
    nome = next((u["nome"] for u in usuarios if u["login"] == login_val), login_val)
    auth_service.enviar_codigo(email, nome)
    return {"success": True, "msg": f"Código enviado para {email}"}

@app.post("/auth/verificar-codigo")
async def verificar_codigo_route(payload: dict):
    email  = payload.get("email", "").strip()
    codigo = payload.get("codigo", "").strip()
    if not auth_service.verificar_codigo(email, codigo):
        raise HTTPException(status_code=400, detail="Código inválido ou expirado")
    return {"success": True}

@app.post("/auth/atualizar-senha")
async def atualizar_senha(payload: dict):
    login_val  = payload.get("login", "").strip()
    email      = payload.get("email", "").strip()
    nova_senha = payload.get("nova_senha", "")
    if len(nova_senha) < 6:
        raise HTTPException(status_code=400, detail="Senha deve ter pelo menos 6 caracteres")
    auth_service.atualizar_email_senha(login_val, email, nova_senha)
    return {"success": True, "msg": "Acesso configurado com sucesso!"}

@app.post("/auth/recuperar-senha")
async def recuperar_senha(payload: dict):
    email    = payload.get("email", "").strip()
    usuarios = auth_service.load_usuarios()
    u = next((u for u in usuarios if u.get("email") == email), None)
    if not u:      # o email pode ter sido cadastrado em outro computador: traz os usuários da planilha
        try:
            shared_store.sincronizar({"usuarios": _aplicar_usuarios})
            usuarios = auth_service.load_usuarios()
            u = next((u for u in usuarios if u.get("email") == email), None)
        except Exception:
            u = None
    if not u:
        return {"success": True, "msg": "Se o email estiver cadastrado, você receberá um código"}
    auth_service.enviar_codigo(email, u["nome"])
    return {"success": True, "msg": "Código enviado!"}

@app.post("/auth/redefinir-senha")
async def redefinir_senha(payload: dict):
    email      = payload.get("email", "").strip()
    codigo     = payload.get("codigo", "").strip()
    nova_senha = payload.get("nova_senha", "")
    if not auth_service.verificar_codigo(email, codigo):
        raise HTTPException(status_code=400, detail="Código inválido ou expirado")
    if len(nova_senha) < 6:
        raise HTTPException(status_code=400, detail="Senha deve ter pelo menos 6 caracteres")
    usuarios = auth_service.load_usuarios()
    u = next((u for u in usuarios if u.get("email") == email), None)
    if not u:
        raise HTTPException(status_code=404, detail="Email não encontrado")
    auth_service.atualizar_email_senha(u["login"], email, nova_senha)
    return {"success": True}


@app.post("/usuario/senha")
async def alterar_senha_usuario(payload: dict):
    login_val     = payload.get("login", "").strip().lower()
    senha_atual   = payload.get("senha_atual", "")
    senha_nova    = payload.get("senha_nova", "")
    senha_confirm = payload.get("senha_nova_confirmacao", "")
    if not login_val:
        raise HTTPException(status_code=400, detail="Login não informado")
    if len(senha_nova) < 6:
        raise HTTPException(status_code=400, detail="Nova senha deve ter pelo menos 6 caracteres")
    if senha_nova != senha_confirm:
        raise HTTPException(status_code=400, detail="Confirmação de senha não confere")
    u = auth_service.verificar_login(login_val, senha_atual)
    if not u:
        raise HTTPException(status_code=401, detail="Senha atual incorreta")
    auth_service.atualizar_email_senha(login_val, u.get("email", ""), senha_nova)
    log_atividade(f"Senha alterada: {login_val}")
    return {"success": True, "msg": "Senha alterada com sucesso!"}


# ─── CONFIG / CONTAS ─────────────────────────────────────────────────────────

@app.get("/config/contas")
async def get_contas():
    config = load_json(CONFIG_PATH)
    contas_info = {}
    for key, dados in config.get("contas", {}).items():
        contas_info[key] = {
            "nome": dados.get("nome"),
            "seller_id": dados.get("seller_id"),
            "ativo": dados.get("ativo", True),
            "token_ok": bool(dados.get("access_token")),
            "token_expires_at": dados.get("token_expires_at", ""),
        }
    return contas_info


class ConfigContaPayload(BaseModel):
    conta: str
    client_id: str
    client_secret: str
    redirect_uri: Optional[str] = "https://seusite.com/oauth"

@app.post("/config/conta")
async def salvar_config_conta(payload: ConfigContaPayload):
    config = load_json(CONFIG_PATH)
    if payload.conta not in config["contas"]:
        raise HTTPException(status_code=404, detail="Conta não encontrada")
    config["contas"][payload.conta]["client_id"]     = payload.client_id
    config["contas"][payload.conta]["client_secret"] = payload.client_secret
    config["contas"][payload.conta]["redirect_uri"]  = payload.redirect_uri
    save_json(CONFIG_PATH, config)
    log_atividade(f"Config salva para {payload.conta}")
    return {"success": True}


# ─── OAUTH / TOKEN ───────────────────────────────────────────────────────────

@app.get("/auth/url/{conta}")
async def gerar_url_autorizacao(conta: str):
    config = load_json(CONFIG_PATH)
    if conta not in config["contas"]:
        raise HTTPException(status_code=404, detail="Conta não encontrada")
    url = ml_api.gerar_url_autorizacao(conta)
    return {"url": url}


class AuthCodePayload(BaseModel):
    conta: str
    code: str

@app.post("/auth/autorizar")
async def autorizar(payload: AuthCodePayload):
    resultado = await ml_api.autorizar_conta(payload.conta, payload.code)
    if not resultado["success"]:
        raise HTTPException(status_code=400, detail=resultado["error"])
    log_atividade(f"Token autorizado para {payload.conta} — seller_id: {resultado.get('seller_id')}")
    return resultado


@app.post("/auth/refresh/{conta}")
async def refresh(conta: str):
    resultado = await ml_api.refresh_token(conta)
    if not resultado["success"]:
        raise HTTPException(status_code=400, detail=resultado["error"])
    return resultado


# ─── PRODUTOS ────────────────────────────────────────────────────────────────

@app.get("/produtos/{conta}")
async def listar_produtos(conta: str):
    """Lista todos os produtos ativos da conta (via API ML)."""
    resultado = await ml_api.listar_produtos(conta)
    if not resultado["success"]:
        if resultado.get("code") == 401:
            raise HTTPException(status_code=401, detail="Token expirado — faça a autorização novamente")
        raise HTTPException(status_code=400, detail=resultado["error"])
    return resultado


@app.get("/produtos/{conta}/db")
async def produtos_db(conta: str):
    """Retorna os produtos do DB local com todos os dados de rentabilidade."""
    produtos = load_json(PRODUTOS_PATH)
    conta_produtos = {k: v for k, v in produtos.items() if v.get("conta") == conta}
    return {"conta": conta, "total": len(conta_produtos), "produtos": conta_produtos}


@app.get("/produtos/{conta}/{mlb}/custos-ml")
async def custos_ml(conta: str, mlb: str, preco: Optional[float] = None):
    """Custos reais do ML (comissão, frete, campanha, rebot, última venda) para o simulador."""
    res = await ml_api.custos_anuncio(conta, mlb, preco)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error", "erro"))
    # CMV de referência: a base de CMV pelo SKU (importada); o salvo no produto é só o que estava antes
    import cmv_base
    produto = load_json(PRODUTOS_PATH).get(mlb, {})
    res["cmv_referencia"] = cmv_base.cmv_de(produto.get("sku"))
    res["cmv_salvo"] = produto.get("cmv_unit")
    return res


@app.get("/produtos/{conta}/{mlb}/frete")
async def frete_ml(conta: str, mlb: str, preco: float):
    """Frete do vendedor para o anúncio a um preço simulado."""
    res = await ml_api.frete_para_preco(conta, mlb, preco)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error", "erro"))
    return res


class CustosPayload(BaseModel):
    mlb: str
    cmv_unit: float = 0.0
    frete_unit: float = 0.0
    imposto_pct: Optional[float] = None
    tem_st: Optional[bool] = None
    comissao_pct: Optional[float] = None
    roas_objetivo: float = 0.0
    rebot_unit: float = 0.0
    rebot_inicio: str = ""
    rebot_fim: str = ""
    simulador: Optional[dict] = None

# ─── Full: aviso de estoque zerado ou perto de romper (somente leitura) ─────────────
@app.get("/full/{conta}/alertas")
def full_alertas(conta: str, atualizar: bool = False):
    """Saldo do Full contra a média diária dos últimos 7 dias: zerado, crítico (até 5 dias) ou atenção (até 10 dias)."""
    full_alerta = _modulo("full_alerta")
    from reposicao_full import ML
    if conta not in ("meli01", "meli02", "meli03"):
        raise HTTPException(status_code=404, detail="conta inválida")
    try:
        return full_alerta.obter(conta, ML(conta), atualizar)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {str(e)[:150]}")


# ─── PIVO: análise dos anúncios do ML (somente leitura) ──────────────────────────
@app.post("/pivo/{conta}/analisar")
def pivo_analisar(conta: str):
    """Analisa em segundo plano todos os anúncios da conta (título, imagens, clip, descrição, promoção, ADS)."""
    pivo_ml = _modulo("pivo_ml")
    from reposicao_full import ML
    if conta not in ("meli01", "meli02", "meli03"):
        raise HTTPException(status_code=404, detail="conta inválida")
    return pivo_ml.iniciar(conta, ML(conta))


@app.get("/rebot/{conta}")
async def rebot_verificar(conta: str):
    """Oportunidades de rebot de UMA conta, no mesmo formato do e-mail. Só leitura (nunca escreve no ML)."""
    if conta not in ("meli01", "meli02", "meli03"):
        raise HTTPException(status_code=404, detail="conta desconhecida")
    agenda = _modulo("rebot_agenda")
    rv = _modulo("rebot_visual")
    quando = datetime.now().strftime("%d/%m %H:%M")
    try:
        ops, baixa = await asyncio.to_thread(agenda.analisar_conta, conta, True)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"{conta}: {type(e).__name__}: {str(e)[:120]}")
    avisadas = set(agenda._json(agenda.ESTADO, {"avisadas": []}).get("avisadas", []))
    for o in ops:
        o["repetida"] = o["chave"] in avisadas
    return {"conta": conta, "quando": quando, "total": len(ops), "total_baixa": len(baixa),
            "texto": rv.texto(ops, {}, 0, quando, [conta], baixa), "html": rv.html(ops, {}, 0, quando, [conta], baixa)}


@app.get("/pivo/{conta}")
def pivo_resultado(conta: str):
    pivo_ml = _modulo("pivo_ml")
    r = pivo_ml.resultado(conta)
    for v in r["itens"].values():
        v.pop("_entrada", None)
    return r


@app.post("/pivo/{conta}/{mlb}/clip")
def pivo_clip(conta: str, mlb: str, payload: dict):
    """Marcação manual de clip: {"tem_clip": true|false|null}. A API do ML não expõe os clips."""
    pivo_ml = _modulo("pivo_ml")
    tem = payload.get("tem_clip")
    autor = str(payload.get("autor", ""))[:40]
    nome = str(payload.get("autor_nome", ""))[:60]
    novo = pivo_ml.marcar_clip(conta, mlb, tem, autor, nome)
    if not novo:   # anúncio sem análise local (vindo de outro computador): devolve o resultado já com o clip
        novo = (pivo_ml.resultado(conta).get("itens") or {}).get(mlb)
    if novo:
        novo.pop("_entrada", None)
    info = (_ler_json(pivo_ml.MANUAL_PATH).get(conta) or {}).get(mlb) or {}
    shared_store.enviar_async("clips", f"{conta}|{mlb}", {"clip": tem, "login": autor, "por": nome or autor, "em": info.get("em")}, autor, removido=tem is None)
    if novo:   # o resultado do PIVO recalculado também sobe, para os outros computadores verem o V atualizado
        pivo_ml.publicar(conta, {mlb: novo}, shared_store.agora_iso(), autor)
    return {"success": True, "item": novo}


# ─── Foto de capa dos anúncios (ML): busca os que faltam e guarda em fotos_cache.json ───
FOTOS_PATH = os.path.join(BASE_DIR, "fotos_cache.json")


@app.get("/fotos/{conta}")
def fotos_conta(conta: str):
    """{mlb: url} da imagem de capa. Só consulta o ML para anúncios ainda sem foto no cache."""
    if conta not in ("meli01", "meli02", "meli03"):
        raise HTTPException(status_code=404, detail="conta inválida")
    cache = _ler_json(FOTOS_PATH)
    # fotos mudam no ML: o cache de cada conta vale 7 dias; depois disso é refeito
    from datetime import datetime, timedelta
    datas = cache.setdefault("_datas", {})
    try:
        vencido = (datetime.now() - datetime.fromisoformat(datas.get(conta, ""))) > timedelta(days=7)
    except ValueError:
        vencido = True
    if vencido:
        cache[conta] = {}
        datas[conta] = datetime.now().isoformat(timespec="seconds")
    da_conta = cache.setdefault(conta, {})
    try:
        with open(os.path.join(BASE_DIR, "produtos.json"), encoding="utf-8") as f:
            todos = [m for m, p in json.load(f).items() if p.get("conta") == conta]
    except (OSError, ValueError):
        todos = []
    faltam = [m for m in todos if m not in da_conta]
    if faltam:
        try:
            from reposicao_full import ML
            ml = ML(conta)
            for i in range(0, len(faltam), 20):
                st, corpo = ml.get("/items", ids=",".join(faltam[i:i + 20]), attributes="id,thumbnail,secure_thumbnail,pictures")
                for e in (corpo or []) if st == 200 else []:
                    if e.get("code") == 200:
                        b = e["body"]
                        pics = b.get("pictures") or []
                        url = (pics[0].get("secure_url") if pics else None) or b.get("secure_thumbnail") or b.get("thumbnail")
                        if url:
                            da_conta[b["id"]] = url
            with open(FOTOS_PATH, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False)
        except Exception:
            pass          # sem foto por enquanto: a tela funciona sem elas
    return {"conta": conta, "fotos": {m: da_conta[m] for m in todos if m in da_conta}}


# ─── Base central de CMV (um arquivo para todas as plataformas) ──────────────────
@app.get("/impostos")
def impostos_tabela():
    """Alíquotas por conta e regime (Techseller, MIZI, SELLER INFO)."""
    return _modulo("impostos").carregar()


@app.post("/impostos/importar")
async def impostos_importar(file: UploadFile = File(...), autor: str = ""):
    """Importa a planilha de alíquotas (Conta, Regime, Alíquota, Data) e publica no banco compartilhado."""
    conteudo = await file.read()
    try:
        return _modulo("impostos").importar(conteudo, autor[:40])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/impostos/publicar")
def impostos_publicar():
    """Sobe a tabela de impostos atual para o banco compartilhado (semente para as outras máquinas)."""
    imp = _modulo("impostos")
    itens = imp.carregar().get("itens", {})
    imp.publicar(itens, "")
    return {"success": True, "linhas": len(itens)}


def _aplicar_impostos(registros: list):
    _modulo("impostos").aplicar_remoto(registros)


CMV_IMPORTAR_PERMITIDOS = {"zicri"}   # só o responsável importa e publica o CMV; liberar outros aqui


@app.post("/cmv/publicar")
def cmv_publicar(autor: str = ""):
    """Sobe toda a base de CMV local para o banco compartilhado (semente inicial para as outras máquinas)."""
    if autor.strip().lower() not in CMV_IMPORTAR_PERMITIDOS:
        raise HTTPException(status_code=403, detail="publicação de CMV liberada só para o responsável")
    cmv_base = _modulo("cmv_base")
    base = cmv_base.carregar()
    cmv_base.publicar(base.get("itens", {}), "")
    return {"success": True, "skus": len(base.get("itens", {}))}


def _aplicar_cmv(registros: list):
    _modulo("cmv_base").aplicar_remoto(registros)


@app.post("/cmv/importar")
async def cmv_importar(file: UploadFile = File(...), autor: str = Form("")):
    """Importa o arquivo de CMV do Grupo (abas com colunas SKU e CMV/Custo) para a base central cmv_base.json."""
    if autor.strip().lower() not in CMV_IMPORTAR_PERMITIDOS:
        raise HTTPException(status_code=403, detail="importação de CMV liberada só para o responsável")
    cmv_base = _modulo("cmv_base")
    conteudo = await file.read()
    try:
        return cmv_base.importar(conteudo, file.filename or "", autor[:40])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Erro ao ler o arquivo: {e}")


@app.get("/cmv/base")
async def cmv_resumo():
    cmv_base = _modulo("cmv_base")
    b = cmv_base.carregar()
    return {"skus": len(b["itens"]), "atualizado_em": b.get("_atualizado_em"), "arquivo": b.get("_arquivo")}


@app.post("/cmv/aplicar/{conta}")
def cmv_aplicar(conta: str, confirmar: bool = False, tem_st_padrao: Optional[bool] = None, imposto: bool = False):
    """Aplica a base de CMV aos produtos da conta (ml: produtos.json; amazon: amazon_custos.json).
    Simula por padrão; só grava com confirmar=true. Amazon: SKU sem imposto cadastrado precisa de tem_st_padrao."""
    cmv_base = _modulo("cmv_base")
    if conta.startswith("meli"):
        return cmv_base.aplicar_ml(conta, confirmar, imposto=imposto)
    if conta.startswith("amazon"):
        try:
            return cmv_base.aplicar_amazon(conta, tem_st_padrao, confirmar)
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))
    raise HTTPException(status_code=404, detail="conta desconhecida")


@app.get("/impostos/{conta}")
async def aliquotas_da_conta(conta: str):
    """Alíquotas COM ST e SEM ST da conta (config.json > impostos), em fração (0.1072 = 10,72%)."""
    com_st, sem_st = _modulo("impostos").por_conta(conta, parser.load_config().get("impostos", {}), (0.10, 0.08))
    return {"conta": conta, "com_st": com_st, "sem_st": sem_st}


@app.post("/produtos/custos")
async def salvar_custos(payload: CustosPayload):
    """Salva/atualiza custos de um produto no DB local."""
    produtos = load_json(PRODUTOS_PATH)
    if payload.mlb not in produtos:
        raise HTTPException(status_code=404, detail="Produto não encontrado no DB")
    p = produtos[payload.mlb]
    if payload.cmv_unit      != 0.0: p["cmv_unit"]      = payload.cmv_unit
    if payload.frete_unit    != 0.0: p["frete_unit"]    = payload.frete_unit
    if payload.roas_objetivo != 0.0: p["roas_objetivo"] = payload.roas_objetivo
    if payload.rebot_unit    != 0.0: p["rebot_unit"]    = payload.rebot_unit
    if payload.rebot_inicio  != "":  p["rebot_inicio"]  = payload.rebot_inicio
    if payload.rebot_fim     != "":  p["rebot_fim"]     = payload.rebot_fim
    if payload.imposto_pct   is not None: p["imposto_pct"]  = payload.imposto_pct
    if payload.tem_st        is not None: p["tem_st"]       = payload.tem_st
    if payload.comissao_pct  is not None: p["comissao_pct"] = payload.comissao_pct
    if payload.simulador     is not None: p["simulador"]    = payload.simulador
    produtos_store.mesclar({payload.mlb: p})
    log_atividade(f"Custos atualizados: {payload.mlb}")
    return {"success": True}


@app.post("/produtos/{conta}/preencher-skus")
async def preencher_skus(conta: str):
    """Preenche o SKU dos anúncios da conta que estão sem SKU no produtos.json (não sobrescreve os que já têm)."""
    produtos = load_json(PRODUTOS_PATH)
    vazios = [m for m, p in produtos.items() if p.get("conta") == conta and not p.get("sku")]
    if not vazios:
        return {"success": True, "conta": conta, "sem_sku_antes": 0, "preenchidos": 0, "ainda_sem_sku": []}
    achados = await ml_api.skus_anuncios(conta, vazios)
    n = 0
    for m, sku in achados.items():
        if sku and m in produtos and not produtos[m].get("sku"):
            produtos[m]["sku"] = sku
            n += 1
    produtos_store.mesclar({m: produtos[m] for m, sku in achados.items() if sku and m in produtos})
    log_atividade(f"SKUs preenchidos ({conta}): {n}")
    return {"success": True, "conta": conta, "sem_sku_antes": len(vazios), "preenchidos": n,
            "ainda_sem_sku": [m for m in vazios if not achados.get(m)]}


@app.post("/produtos/{mlb}/arquivar")
async def arquivar_produto(mlb: str, payload: dict):
    produtos = load_json(PRODUTOS_PATH)
    if mlb not in produtos:
        raise HTTPException(status_code=404, detail="Produto não encontrado")
    produtos[mlb]["arquivado"] = payload.get("arquivado", True)
    produtos_store.mesclar({mlb: produtos[mlb]})
    log_atividade(f"Produto {'arquivado' if payload.get('arquivado') else 'desarquivado'}: {mlb}")
    return {"success": True, "arquivado": produtos[mlb]["arquivado"]}


# ─── SYNC — Puxa dados da API ML e atualiza DB ───────────────────────────────

@app.get("/modelos/{conta}")
async def baixar_modelo_custos(conta: str):
    """Gera e retorna o modelo .xlsx de importação de custos pré-preenchido com os produtos da conta."""
    import openpyxl
    from openpyxl.styles import PatternFill, Font, Alignment

    cfg      = parser.load_config()
    aliq_com_st, aliq_sem_st = _modulo("impostos").por_conta(conta, cfg.get("impostos", {}), (0.10, 0.08))

    produtos_db    = parser.load_produtos()
    produtos_conta = sorted(
        [(mlb, p) for mlb, p in produtos_db.items() if p.get("conta") == conta],
        key=lambda x: x[1].get("titulo", ""),
    )

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Importação"

    fill_amarelo = PatternFill("solid", fgColor="FFF176")
    fill_header  = PatternFill("solid", fgColor="1A1A24")
    fill_dica    = PatternFill("solid", fgColor="EEEEEE")

    COLS    = ["A","B","C","D","E","F","G","H","I","J"]
    HEADERS = ["ID do anúncio","SKU","Título","Preço de Custo",
               "Frete","Comissão","Imposto","Rebot","Rebot Início","Rebot Fim"]
    WIDTHS  = [22, 15, 46, 16, 12, 12, 12, 12, 14, 14]

    # Linha 1 — título
    ws.merge_cells("A1:J1")
    ws["A1"].value     = f"Modelo de Importação — MZ Rentabilidade · {conta}"
    ws["A1"].font      = Font(bold=True, size=13)
    ws["A1"].alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 30

    # Linha 2 — alíquotas editáveis (B2 = COM ST, D2 = SEM ST)
    ws["A2"].value     = "Alíquota COM ST:"
    ws["A2"].font      = Font(size=10)
    ws["A2"].alignment = Alignment(vertical="center")

    ws["B2"].value         = round(aliq_com_st * 100, 4)
    ws["B2"].fill          = fill_amarelo
    ws["B2"].font          = Font(bold=True, size=10)
    ws["B2"].number_format = "0.00"
    ws["B2"].alignment     = Alignment(horizontal="center", vertical="center")

    ws["C2"].value     = "|   Alíquota SEM ST:"
    ws["C2"].font      = Font(size=10)
    ws["C2"].alignment = Alignment(vertical="center")

    ws["D2"].value         = round(aliq_sem_st * 100, 4)
    ws["D2"].fill          = fill_amarelo
    ws["D2"].font          = Font(bold=True, size=10)
    ws["D2"].number_format = "0.00"
    ws["D2"].alignment     = Alignment(horizontal="center", vertical="center")

    try:
        from openpyxl.workbook.defined_name import DefinedName
        wb.defined_names["aliquota_com_st"] = DefinedName("aliquota_com_st", attr_text="Importação!$B$2")
        wb.defined_names["aliquota_sem_st"] = DefinedName("aliquota_sem_st", attr_text="Importação!$D$2")
    except Exception:
        pass

    ws.row_dimensions[2].height = 22

    # Linha 3 — dica (antes do cabeçalho para não ser processada como dado)
    ws.merge_cells("A3:J3")
    ws["A3"].value     = "  Imposto: COM ST ou SEM ST  ·  Comissão em % (ex: 11 para 11%)  ·  Datas: DD/MM/AAAA  ·  Deixe em branco campos sem valor"
    ws["A3"].fill      = fill_dica
    ws["A3"].font      = Font(italic=True, size=9, color="9E9E9E")
    ws["A3"].alignment = Alignment(vertical="center")
    ws.row_dimensions[3].height = 16

    # Linha 4 — cabeçalhos
    for col, header in zip(COLS, HEADERS):
        c            = ws[f"{col}4"]
        c.value      = header
        c.fill       = fill_header
        c.font       = Font(bold=True, color="FFFFFF", size=10)
        c.alignment  = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[4].height = 22

    # Linhas 5+ — produtos (sem linha de dica entre cabeçalho e dados)
    def _num(val):
        return val if val is not None else ""

    for row_num, (mlb, p) in enumerate(produtos_conta, start=5):
        comissao_val = round((p.get("comissao_pct") or 0.11) * 100, 2)
        tem_st_val   = "COM ST" if p.get("tem_st", True) else "SEM ST"
        ws.cell(row=row_num, column=1).value  = mlb
        ws.cell(row=row_num, column=2).value  = p.get("sku", "")
        ws.cell(row=row_num, column=3).value  = p.get("titulo", "")
        ws.cell(row=row_num, column=4).value  = _num(p.get("cmv_unit"))
        ws.cell(row=row_num, column=5).value  = _num(p.get("frete_unit"))
        ws.cell(row=row_num, column=6).value  = comissao_val
        ws.cell(row=row_num, column=7).value  = tem_st_val
        ws.cell(row=row_num, column=8).value  = _num(p.get("rebot_unit"))
        ws.cell(row=row_num, column=9).value  = p.get("rebot_inicio") or ""
        ws.cell(row=row_num, column=10).value = p.get("rebot_fim") or ""

    for col_letter, width in zip(COLS, WIDTHS):
        ws.column_dimensions[col_letter].width = width
    ws.freeze_panes = "A5"

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=modelo_custos_{conta}.xlsx"},
    )


@app.post("/produtos/importar-custos/{conta}")
async def importar_custos_xlsx(conta: str, file: UploadFile = File(...)):
    """
    Lê o modelo xlsx MZCell e atualiza custos no produtos.json.
    Colunas: ID do anúncio | SKU | Título | Preço de Custo | Frete | Comissão | Imposto | Rebot | Rebot Início | Rebot Fim
    B2 = alíquota COM ST (%) · D2 = alíquota SEM ST (%) — sobrepõem config.json quando preenchidos.
    """
    import openpyxl
    from datetime import datetime

    content = await file.read()
    try:
        wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Erro ao ler xlsx: {e}")

    # Busca aba pelo nome
    sheet_name = None
    for name in wb.sheetnames:
        if "relat" in name.lower() or "custo" in name.lower() or "importa" in name.lower():
            sheet_name = name
            break
    if not sheet_name:
        sheet_name = wb.sheetnames[0]
    ws = wb[sheet_name]

    # Lê alíquotas de B2/D2 se o modelo MZCell foi usado (sobrepõem config.json)
    def _parse_aliq(val):
        try:
            v = float(val)
            return v / 100 if v > 1 else v
        except Exception:
            return None

    aliq_com_st_override = _parse_aliq(ws["B2"].value)
    aliq_sem_st_override = _parse_aliq(ws["D2"].value)

    # Detecta linha de cabeçalho procurando "ID do anúncio" ou "SKU"
    # Cada linha é varrida de forma independente; só é aceita se contiver mlb OU sku,
    # evitando que linhas de título com "custo" sejam confundidas com cabeçalho.
    header_row = None
    col_map = {}
    for row in ws.iter_rows(min_row=1, max_row=10):
        row_map = {}
        for cell in row:
            val = str(cell.value or "").lower().strip()
            if "id do" in val or ("anúncio" in val and "id" in val):
                row_map["mlb"] = cell.column
            elif val in ["frete", "frete (r$)"]:
                row_map["frete"] = cell.column
            elif "custo" in val or "cmv" in val:
                row_map["cmv"] = cell.column
            elif "sku" in val:
                row_map["sku"] = cell.column
            elif "imposto" in val:
                row_map["imposto"] = cell.column
            elif "comiss" in val:
                row_map["comissao"] = cell.column
            elif val == "rebot\n(r$/un)" or (val.startswith("rebot") and "início" not in val and "fim" not in val and "inicio" not in val):
                row_map["rebot"] = cell.column
            elif "início" in val or "inicio" in val:
                row_map["rebot_inicio"] = cell.column
            elif "fim" in val and "rebot" in row_map:
                row_map["rebot_fim"] = cell.column
        if "mlb" in row_map or "sku" in row_map:
            col_map = row_map
            header_row = cell.row
            break

    if not header_row:
        raise HTTPException(status_code=400, detail="Cabeçalho não encontrado. Verifique o formato do arquivo.")

    # Carrega produtos.json e monta índices
    produtos_db = parser.load_produtos()
    mudados = {}
    mlb_index = {mlb: mlb for mlb in produtos_db if produtos_db[mlb].get("conta") == conta}
    sku_index  = {}
    for mlb, p in produtos_db.items():
        if p.get("conta") == conta and p.get("sku"):
            sku_index[str(p["sku"]).strip().upper()] = mlb

    def get_val(row, campo):
        if campo in col_map:
            return row[col_map[campo] - 1]
        return None

    def fmt_data(d):
        if not d:
            return ""
        d = str(d).strip()
        for fmt in ["%d/%m/%Y", "%Y-%m-%d", "%m/%d/%Y"]:
            try:
                return datetime.strptime(d, fmt).strftime("%Y-%m-%d")
            except Exception:
                pass
        return d

    # Alíquotas: planilha (B2/D2) tem prioridade sobre a tabela de impostos
    cfg      = parser.load_config()
    tab_com, tab_sem = _modulo("impostos").por_conta(conta, cfg.get("impostos", {}), (0.10, 0.08))
    aliq_com_st = aliq_com_st_override if aliq_com_st_override is not None else tab_com
    aliq_sem_st = aliq_sem_st_override if aliq_sem_st_override is not None else tab_sem

    atualizados, nao_encontrados = [], []

    for row_idx, row in enumerate(ws.iter_rows(min_row=header_row + 1, values_only=True), start=header_row + 1):
        if not any(v is not None for v in row):
            continue

        mlb_val  = str(get_val(row, "mlb") or "").strip()
        sku_val  = str(get_val(row, "sku")  or "").strip().upper()
        linha_id = mlb_val or sku_val or f"linha {row_idx}"

        # Validar campos obrigatórios (apenas colunas presentes na planilha)
        campos_faltando = []
        if not (mlb_val or sku_val):
            campos_faltando.append("ID do anúncio / SKU")
        for campo, nome in [("cmv", "Preço de Custo"), ("frete", "Frete"),
                             ("comissao", "Comissão"), ("imposto", "Imposto")]:
            if campo in col_map:
                v = get_val(row, campo)
                if v is None or str(v).strip() == "":
                    campos_faltando.append(nome)
        if campos_faltando:
            nao_encontrados.append({"id": linha_id,
                                    "motivo": f"campo(s) obrigatório(s) não preenchido(s): {', '.join(campos_faltando)}"})
            continue

        mlb = mlb_index.get(mlb_val) or mlb_index.get(f"MLB{mlb_val}") or sku_index.get(sku_val)
        if not mlb:
            nao_encontrados.append({"id": mlb_val or sku_val, "motivo": "não encontrado nos produtos cadastrados"})
            continue

        frete    = float(get_val(row, "frete")    or 0)
        cmv      = float(get_val(row, "cmv")      or 0)
        comissao = float(get_val(row, "comissao")  or 0.11)
        if comissao > 1:
            comissao = comissao / 100

        imposto_str = str(get_val(row, "imposto") or "").strip().upper()
        tem_st  = imposto_str == "COM ST"
        imposto = aliq_com_st if tem_st else aliq_sem_st

        rebot     = float(get_val(row, "rebot")        or 0)
        rb_inicio = fmt_data(get_val(row, "rebot_inicio"))
        rb_fim    = fmt_data(get_val(row, "rebot_fim"))

        produtos_db[mlb].update({
            "cmv_unit":     round(cmv, 4),
            "frete_unit":   round(frete, 4),
            "comissao_pct": round(comissao, 4),
            "imposto_pct":  round(imposto, 6),
            "tem_st":       tem_st,
            "rebot_unit":   round(rebot, 4),
            "rebot_inicio": rb_inicio,
            "rebot_fim":    rb_fim,
        })
        atualizados.append(mlb_val or sku_val)
        mudados[mlb] = produtos_db[mlb]

    produtos_store.mesclar(mudados)
    log_atividade(f"Importação custos {conta}: {len(atualizados)} produtos atualizados")

    return {
        "success":           True,
        "conta":             conta,
        "atualizados":       len(atualizados),
        "itens_atualizados": atualizados,
        "nao_encontrados":   nao_encontrados,
    }


@app.post("/sync/{conta}")
async def sincronizar_conta(conta: str, dias: int = 30, date_from: str = None, date_to: str = None):
    """Sincroniza todos os produtos da conta com a API do ML."""
    resultado = await sync_service.sincronizar_conta_func(
        conta, dias=dias, date_from=date_from, date_to=date_to
    )
    if not resultado["success"]:
        raise HTTPException(status_code=400, detail=resultado.get("error", "Erro no sync"))
    return resultado


@app.post("/sync/{conta}/{mlb}")
async def sincronizar_produto(conta: str, mlb: str, date_from: str = None, date_to: str = None):
    """Sincroniza um único produto da conta com a API do ML."""
    import sync_produto
    resultado = await sync_produto.sincronizar_e_retornar(conta, mlb, date_from=date_from, date_to=date_to)
    if not resultado["success"]:
        raise HTTPException(status_code=400, detail=resultado.get("error", "Erro no sync"))
    log_atividade(f"Sync produto: {mlb} ({conta}) {date_from} → {date_to}")
    return resultado


@app.post("/sync/{conta}/{mlb}/comparar")
async def sincronizar_produto_comparar(conta: str, mlb: str, date_from: str = None, date_to: str = None):
    """Sincroniza período atual e anterior em paralelo, retorna ambos para badges de variação."""
    import sync_produto
    from datetime import datetime, timedelta

    if not date_to:
        date_to = datetime.now().strftime("%Y-%m-%d")
    if not date_from:
        date_from = (datetime.now() - timedelta(days=6)).strftime("%Y-%m-%d")

    dt_from = datetime.fromisoformat(date_from)
    dt_to   = datetime.fromisoformat(date_to)
    dias    = (dt_to - dt_from).days + 1
    prev_to   = (dt_from - timedelta(days=1)).strftime("%Y-%m-%d")
    prev_from = (dt_from - timedelta(days=dias)).strftime("%Y-%m-%d")

    atual, anterior = await asyncio.gather(
        sync_produto.sincronizar_e_retornar(conta, mlb, date_from=date_from, date_to=date_to),
        sync_produto.sincronizar_e_retornar(conta, mlb, date_from=prev_from, date_to=prev_to),
    )

    if not atual["success"]:
        raise HTTPException(status_code=400, detail=atual.get("error", "Erro no sync"))

    # grava no produtos.json o que o sync trouxe (dias do período, ADS, ROAS objetivo): sem isso a tela
    # continuava mostrando os valores salvos antes do sync
    import sync_service
    try:
        gravado = await sync_service.sincronizar_produto_func(conta, mlb, date_from, date_to)
    except Exception as e:
        gravado = {"success": False, "error": str(e)}

    log_atividade(f"Sync comparar: {mlb} ({conta}) {date_from}→{date_to} vs {prev_from}→{prev_to}")
    return {**atual, "anterior": anterior, "gravado": bool(gravado.get("success"))}


@app.post("/sistema/abrir-link")
async def abrir_link(payload: dict):
    """Abre um anúncio do Mercado Livre no navegador padrão do Windows (fora da janela do app)."""
    import webbrowser
    url = str(payload.get("url", ""))
    # só páginas de anúncio: MLB no Mercado Livre e ASIN (10 caracteres) na Amazon
    asin = url[len("https://www.amazon.com.br/dp/"):] if url.startswith("https://www.amazon.com.br/dp/") else ""
    permitido = url.startswith("https://produto.mercadolivre.com.br/MLB-") or (len(asin) == 10 and asin.isalnum())
    if not permitido:
        raise HTTPException(status_code=400, detail="Endereço não permitido")
    webbrowser.open(url, new=2)
    return {"success": True}


@app.get("/ads/{conta}/{mlb}/dias")
async def ads_dias(conta: str, mlb: str, datas: str):
    """ADS e vendas dia a dia (AAAA-MM-DD separados por vírgula, até 7 dias)."""
    import sync_produto
    from datetime import datetime
    lista = [d.strip() for d in datas.split(",") if d.strip()][:7]
    try:
        for d in lista:
            datetime.fromisoformat(d)
    except ValueError:
        raise HTTPException(status_code=400, detail="data inválida")
    return {"success": True, "dias": await sync_produto.ads_por_dia(conta, mlb, lista)}


@app.get("/sync/status")
async def get_sync_status():
    """Retorna status do último sync por conta e quais estão em andamento."""
    return {
        "em_andamento": list(_syncs_em_andamento),
        "contas": sync_service.get_sync_status(),
    }


# ─── ANOTAÇÕES ────────────────────────────────────────────────────────────────

def _anotacoes_path():
    return os.path.join(BASE_DIR, "anotacoes.json")

def _load_anotacoes():
    p = _anotacoes_path()
    if not os.path.exists(p):
        return {}
    with open(p, encoding="utf-8") as f:
        return json.load(f)

def _save_anotacoes(db: dict):
    with open(_anotacoes_path(), "w", encoding="utf-8") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)


# ─── Banco compartilhado: aplica no disco local o que veio da planilha ────────────────
def _aplicar_comentarios(registros: list):
    db = _load_anotacoes()
    for r in registros:
        try:
            conta, mlb, cid = r["chave"].split("|", 2)
            d = json.loads(r["dados"]) if r.get("dados") else {}
        except (ValueError, KeyError):
            continue
        lista = db.setdefault(conta, {}).setdefault(mlb, [])
        existente = next((a for a in lista if a.get("id") == cid), None)
        if r.get("removido"):
            if existente:
                lista.remove(existente)
        elif existente is None and d.get("texto"):
            lista.append({"id": cid, "texto": d["texto"], "criado_em": d.get("criado_em", ""),
                          "criado_ts": d.get("criado_ts", r.get("atualizado_em", "")), "autor": d.get("autor", r.get("autor", "")),
                          "autor_nome": d.get("autor_nome", ""), "dados": d.get("dados"),
                          "favoritos": d.get("favoritos", [])})
        elif existente is not None and "favoritos" in d:   # favoritar em outro computador: a lista vem da planilha
            existente["favoritos"] = d["favoritos"]
    _save_anotacoes(db)


def _aplicar_posicoes(registros: list):
    db = _load_posicoes()
    for r in registros:
        try:
            conta, mlb = r["chave"].split("|", 1)
            novo = json.loads(r["dados"])
        except (ValueError, KeyError):
            continue
        if r.get("removido"):
            db.get(conta, {}).pop(mlb, None)
            continue
        atual = db.setdefault(conta, {}).get(mlb)
        if atual is None or (atual.get("atualizado_em") or "") <= (novo.get("atualizado_em") or r.get("atualizado_em", "")):
            db[conta][mlb] = novo
    with open(POSICOES_PATH, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)


def _aplicar_clips(registros: list):
    pivo_ml = _modulo("pivo_ml")
    for r in registros:
        try:
            conta, mlb = r["chave"].split("|", 1)
            d = json.loads(r["dados"])
            clip = d.get("clip")
        except (ValueError, KeyError, AttributeError):
            continue
        pivo_ml.marcar_clip(conta, mlb, None if r.get("removido") else clip, d.get("login", ""), d.get("por", ""),
                            d.get("em") or r.get("atualizado_em"))


def _aplicar_pivo(registros: list):
    _modulo("pivo_ml").aplicar_remoto(registros)


# ─── Tags de análise dos anúncios (Eduardo/Michel): visíveis para todos, exigem comentário do mesmo dia ───
TAGS_PATH = os.path.join(BASE_DIR, "tags_compartilhadas.json")
TAGS_PERMISSAO = {"ranqueamento": {"eduardo", "michel", "zicri"}, "sazonalidade": {"eduardo", "michel", "zicri"},
                  "revisado": {"eduardo", "zicri"}, "atencao": {"eduardo", "zicri"}}  # zicri: liberado para teste
TAGS_COM_COMENTARIO = {"revisado", "atencao"}   # só estas exigem comentário escrito no mesmo dia


def _ler_tags() -> dict:
    return _ler_json(TAGS_PATH)


def _gravar_tags(db: dict):
    with open(TAGS_PATH, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False)


def _aplicar_tags(registros: list):
    """Aplica tags vindas de outros computadores. Registro removido ou inativo apaga a tag."""
    db = _ler_tags()
    for r in registros:
        try:
            conta, mlb, tipo = r["chave"].split("|", 2)
            d = json.loads(r["dados"]) if r.get("dados") else {}
        except (ValueError, KeyError):
            continue
        if r.get("removido") or not d.get("ativa"):
            db.get(conta, {}).get(mlb, {}).pop(tipo, None)
        else:
            db.setdefault(conta, {}).setdefault(mlb, {})[tipo] = d
    _gravar_tags(db)


CONTESTACOES_PATH = os.path.join(BASE_DIR, "contestacoes_compartilhadas.json")


def _aplicar_contestacoes(registros: list):
    db = _ler_json(CONTESTACOES_PATH)
    for r in registros:
        try:
            conta, cid = r["chave"].split("|", 1)
            d = json.loads(r["dados"])
        except (ValueError, KeyError):
            continue
        atual = db.setdefault(conta, {}).get(cid)
        if r.get("removido"):
            db[conta].pop(cid, None)
        elif not atual or (atual.get("_atualizado_em") or "") <= (r.get("atualizado_em") or ""):
            d["_atualizado_em"] = r.get("atualizado_em", "")
            db[conta][cid] = d
    with open(CONTESTACOES_PATH, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False)


def _ler_json(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def _aplicar_usuarios(registros: list):
    auth_service.aplicar_usuarios(registros)


_APLICADORES_COMPARTILHADOS = {"comentarios": _aplicar_comentarios, "posicoes": _aplicar_posicoes, "clips": _aplicar_clips,
                               "pivo": _aplicar_pivo, "contestacoes": _aplicar_contestacoes, "usuarios": _aplicar_usuarios,
                               "tags": _aplicar_tags, "cmv": _aplicar_cmv, "impostos": _aplicar_impostos}


@app.post("/compartilhado/sincronizar")
def compartilhado_sincronizar():
    """Sincronização sob demanda com a planilha da equipe (sobe a fila e traz o que mudou)."""
    return shared_store.sincronizar(_APLICADORES_COMPARTILHADOS)


POSICOES_PATH = os.path.join(BASE_DIR, "posicoes.json")

def _load_posicoes() -> dict:
    try:
        with open(POSICOES_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

@app.get("/posicoes/{conta}")
async def listar_posicoes(conta: str):
    """Posição de cada anúncio na busca do ML (digitada pelo analista), compartilhada entre usuários."""
    return {"success": True, "posicoes": _load_posicoes().get(conta, {})}

@app.post("/posicoes/{conta}/{mlb}")
async def salvar_posicao_anuncio(conta: str, mlb: str, payload: dict):
    from datetime import datetime as _dt
    try:
        pagina, posicao = int(payload.get("pagina") or 1), int(payload.get("posicao") or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="pagina e posicao devem ser números")
    if not (1 <= pagina <= 99 and 1 <= posicao <= 99):
        raise HTTPException(status_code=400, detail="pagina e posicao devem estar entre 1 e 99")
    db = _load_posicoes()
    atual = db.setdefault(conta, {}).get(mlb, {})
    hist = atual.get("historico", [])
    hist.append({"data": _dt.now().strftime("%d/%m/%Y"), "pagina": pagina, "posicao": posicao,
                 "keyword": str(payload.get("keyword", "")).strip()[:80]})
    novo = {"pagina": pagina, "posicao": posicao, "keyword": str(payload.get("keyword", "")).strip()[:80],
            "historico": hist[-20:]}
    novo["atualizado_em"] = shared_store.agora_iso()
    db[conta][mlb] = novo
    with open(POSICOES_PATH, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)
    shared_store.enviar_async("posicoes", f"{conta}|{mlb}", novo, str(payload.get("autor", ""))[:40])
    return {"success": True, "posicao": novo}


@app.get("/tags/{conta}")
def get_tags(conta: str):
    """Tags ativas de cada anúncio da conta: {mlb: {tipo: {nome, comentario, comentario_em, em}}}."""
    return {"success": True, "tags": _ler_tags().get(conta, {})}


@app.post("/tags/{conta}/{mlb}")
def marcar_tag(conta: str, mlb: str, payload: dict):
    """Marca ou remove uma tag. Marcar exige anotação feita hoje no anúncio: a tag copia esse comentário e a data."""
    tipo = str(payload.get("tipo", ""))
    ativa = bool(payload.get("ativa", True))
    login = str(payload.get("login", ""))[:40].lower()
    nome = str(payload.get("nome", ""))[:60]
    if tipo not in TAGS_PERMISSAO:
        raise HTTPException(status_code=400, detail="tipo de tag inválido")
    if login not in TAGS_PERMISSAO[tipo]:
        raise HTTPException(status_code=403, detail="sem permissão para esta tag")
    agora = datetime.now()
    if ativa and tipo not in TAGS_COM_COMENTARIO:   # ranqueamento e sazonalidade: só marcação
        rec = {"ativa": True, "login": login, "nome": nome or login, "em": agora.isoformat(timespec="seconds"),
               "comentario": "", "comentario_em": agora.strftime("%d/%m/%Y %H:%M"), "anotacao_id": ""}
    elif ativa:
        hoje = agora.strftime("%d/%m/%Y")
        do_dia = [a for a in _load_anotacoes().get(conta, {}).get(mlb, []) if (a.get("criado_em") or "")[:10] == hoje]
        if not do_dia:
            raise HTTPException(status_code=400, detail="escreva um comentário neste anúncio hoje antes de marcar a tag")
        c = max(do_dia, key=lambda a: a.get("id", ""))
        rec = {"ativa": True, "login": login, "nome": nome or login, "em": agora.isoformat(timespec="seconds"),
               "comentario": c.get("texto", ""), "comentario_em": c.get("criado_em", ""), "anotacao_id": c.get("id", "")}
    else:
        rec = {"ativa": False, "login": login, "nome": nome or login, "em": agora.isoformat(timespec="seconds")}
    db = _ler_tags()
    if ativa:
        db.setdefault(conta, {}).setdefault(mlb, {})[tipo] = rec
    else:
        db.get(conta, {}).get(mlb, {}).pop(tipo, None)
    _gravar_tags(db)
    shared_store.enviar_async("tags", f"{conta}|{mlb}|{tipo}", rec, login, removido=not ativa)
    log_atividade(f"Tag {tipo} {'marcada' if ativa else 'removida'}: {mlb} ({conta}) por {login}")
    return {"success": True, "tag": rec if ativa else None}


@app.get("/anotacoes/{conta}/{mlb}")
async def get_anotacoes(conta: str, mlb: str):
    """Retorna TODAS as anotações do produto, ordenadas da mais recente."""
    db = _load_anotacoes()
    anotacoes = db.get(conta, {}).get(mlb, [])
    return {"success": True, "anotacoes": sorted(anotacoes, key=lambda x: x.get("id", ""), reverse=True)}


@app.post("/anotacoes/{conta}/{mlb}")
async def salvar_anotacao(conta: str, mlb: str, payload: dict):
    """Salva anotação no banco dedicado anotacoes.json."""
    from datetime import datetime as _dt
    texto = payload.get("texto", "").strip()
    if not texto:
        raise HTTPException(status_code=400, detail="Texto não pode ser vazio")

    db = _load_anotacoes()
    if conta not in db:
        db[conta] = {}
    if mlb not in db[conta]:
        db[conta][mlb] = []

    anotacao = {
        "id":        _dt.now().strftime("%Y%m%d%H%M%S%f"),
        "texto":     texto,
        "criado_em": _dt.now().strftime("%d/%m/%Y %H:%M"),
        "criado_ts": _dt.now().isoformat(),
        "autor":     str(payload.get("autor", ""))[:40],
        "autor_nome": str(payload.get("autor_nome", ""))[:60],
    }
    if isinstance(payload.get("dados"), dict):
        anotacao["dados"] = payload["dados"]
    db[conta][mlb].append(anotacao)
    _save_anotacoes(db)
    shared_store.enviar_async("comentarios", f"{conta}|{mlb}|{anotacao['id']}", anotacao, anotacao["autor"])
    log_atividade(f"Anotação: {mlb} ({conta}) {anotacao['criado_em']}")
    return {"success": True, "anotacao": anotacao}


@app.post("/anotacoes/{conta}/{mlb}/{anotacao_id}/favorito")
async def favoritar_anotacao(conta: str, mlb: str, anotacao_id: str, payload: dict):
    """Marca ou desmarca o comentário como favorito de um usuário (lista de logins em `favoritos`)."""
    login = str(payload.get("login", "")).strip()[:40]
    if not login:
        raise HTTPException(status_code=400, detail="Usuário não informado")
    db = _load_anotacoes()
    anotacao = next((a for a in db.get(conta, {}).get(mlb, []) if a.get("id") == anotacao_id), None)
    if anotacao is None:
        raise HTTPException(status_code=404, detail="Anotação não encontrada")
    favoritos = [x for x in anotacao.get("favoritos", []) if x != login]
    if payload.get("favorito", True):
        favoritos.append(login)
    anotacao["favoritos"] = favoritos
    _save_anotacoes(db)
    shared_store.enviar_async("comentarios", f"{conta}|{mlb}|{anotacao_id}", anotacao, login)
    return {"success": True, "favoritos": favoritos}


@app.delete("/anotacoes/{conta}/{mlb}/{anotacao_id}")
async def deletar_anotacao(conta: str, mlb: str, anotacao_id: str):
    """Remove uma anotação pelo id."""
    db = _load_anotacoes()
    lista = db.get(conta, {}).get(mlb, [])
    nova = [a for a in lista if a.get("id") != anotacao_id]
    if len(nova) == len(lista):
        raise HTTPException(status_code=404, detail="Anotação não encontrada")
    db[conta][mlb] = nova
    _save_anotacoes(db)
    shared_store.enviar_async("comentarios", f"{conta}|{mlb}|{anotacao_id}", {"id": anotacao_id}, removido=True)
    return {"success": True}


# ─── RENTABILIDADE — Agrega por período ──────────────────────────────────────

VALIDADE_SYNC_HORAS = 24   # dados de frete, comissão, rebot e vendas valem por 24h; depois a tela pede novo sync


def _validade_sync(produto: dict) -> dict:
    """Quando o anúncio foi sincronizado pela última vez e se os dados já venceram."""
    from datetime import datetime
    marca = produto.get("atualizado_em") or ""
    try:
        horas = (datetime.now() - datetime.fromisoformat(marca)).total_seconds() / 3600
    except ValueError:
        return {"sincronizado_em": marca or None, "horas": None, "vencido": True}
    fora = bool(produto.get("inativo_no_ml"))   # não está mais ativo no ML: não há o que sincronizar
    return {"sincronizado_em": marca, "horas": round(horas, 1), "vencido": horas > VALIDADE_SYNC_HORAS and not fora,
            "inativo_no_ml": fora}


@app.get("/rentabilidade/{conta}")
async def rentabilidade(conta: str, date_from: str, date_to: str):
    """
    Retorna rentabilidade agregada de todos os produtos da conta
    no período selecionado.
    """
    produtos = load_json(PRODUTOS_PATH)
    conta_produtos = {k: v for k, v in produtos.items() if v.get("conta") == conta}

    resultado = []
    for mlb, produto in conta_produtos.items():
        agregado = parser.agregar_periodo(produto, date_from, date_to)
        resultado.append({
            "mlb":       mlb,
            "titulo":    produto.get("titulo"),
            "sku":       produto.get("sku"),
            "variacao":  produto.get("variacao"),
            "arquivado": produto.get("arquivado", False),
            "tem_st":    produto.get("tem_st", True),
            "validade":  _validade_sync(produto),
            "catalogo":  produto.get("catalogo"),
            "roas_objetivo_ml": produto.get("roas_objetivo_ml"),
            "custos": {
                "cmv_unit":    produto.get("cmv_unit"),
                "frete_unit":  produto.get("frete_unit"),
                "imposto_pct": produto.get("imposto_pct"),
                "comissao_pct": produto.get("comissao_pct"),
                "roas_objetivo": produto.get("roas_objetivo"),
                "rebot_unit":  produto.get("rebot_unit"),
                "simulador":   produto.get("simulador"),
            },
            "periodo": agregado,
        })

    # Ordena por receita desc
    resultado.sort(key=lambda x: x["periodo"].get("receita", 0), reverse=True)

    # KPIs globais
    kpis = {
        "receita_total":   round(sum(r["periodo"]["receita"] for r in resultado), 2),
        "margem_total":    round(sum(r["periodo"]["margem_total"] for r in resultado), 2),
        "ads_total":       round(sum(r["periodo"]["ads_total"] for r in resultado), 2),
        "unidades_total":  sum(r["periodo"]["unidades"] for r in resultado),
        "produtos_ativos": len(resultado),
        "desatualizados":  sum(1 for r in resultado if r["validade"]["vencido"]),
    }
    kpis["margem_pct_global"] = round(kpis["margem_total"] / kpis["receita_total"], 6) if kpis["receita_total"] > 0 else 0
    kpis["tacos_global"]      = round(kpis["ads_total"] / kpis["receita_total"], 6) if kpis["receita_total"] > 0 else 0

    return {
        "conta": conta,
        "periodo": {"de": date_from, "ate": date_to},
        "kpis": kpis,
        "produtos": resultado,
    }


# ─── LOG / ALERTAS ───────────────────────────────────────────────────────────

@app.get("/log")
async def get_log(limit: int = 100):
    logs = load_json(LOG_PATH) or []
    return {"logs": logs[-limit:]}

@app.get("/alertas")
async def get_alertas():
    return {"alertas": load_json(ALERTAS_PATH) or []}


@app.get("/sistema/versao")
async def get_versao():
    try:
        v = open(os.path.join(BASE_DIR, "version.txt"), encoding="utf-8").read().strip()
    except FileNotFoundError:
        v = "?"
    return {"versao": v}


@app.get("/sistema/houve-atualizacao")
async def houve_atualizacao_endpoint():
    version_path = os.path.join(BASE_DIR, "version.txt")
    anterior_path = os.path.join(BASE_DIR, "version_anterior.txt")
    try:
        versao_atual = open(version_path, encoding="utf-8").read().strip()
    except FileNotFoundError:
        return {"houve": False}
    versao_anterior = None
    if os.path.exists(anterior_path):
        versao_anterior = open(anterior_path, encoding="utf-8").read().strip()
    if versao_anterior != versao_atual:
        open(anterior_path, "w", encoding="utf-8").write(versao_atual)
        return {"houve": True, "versao": versao_atual}
    return {"houve": False}


@app.post("/sistema/sync-catalog/{conta}")
async def sync_catalog_listing(conta: str):
    """SSE — preenche catalog_listing dos produtos da conta que ainda não têm o campo."""
    produtos = load_json(PRODUTOS_PATH)
    pendentes = [
        (mlb, v) for mlb, v in produtos.items()
        if v.get("conta") == conta and v.get("catalog_listing") is None
    ]
    total = len(pendentes)

    async def generator():
        atualizados = 0
        erros = 0
        db = load_json(PRODUTOS_PATH)
        for i, (mlb, v) in enumerate(pendentes, 1):
            yield f"data: {json.dumps({'tipo':'progresso','atual':i,'total':total,'mlb':mlb,'titulo':v.get('titulo','')})}\n\n"
            try:
                res = await ml_api.get_catalog_listing(conta, mlb)
                if res["success"]:
                    db[mlb]["catalog_listing"] = res["catalog_listing"]
                    db[mlb]["variacoes_count"]  = res["variacoes_count"]
                    atualizados += 1
                else:
                    erros += 1
            except Exception:
                erros += 1
            if i % 10 == 0:
                produtos_store.mesclar({m: db[m] for m, _ in pendentes[:i]})
                await asyncio.sleep(2)
        produtos_store.mesclar({m: db[m] for m, _ in pendentes})
        yield f"data: {json.dumps({'tipo':'concluido','total':total,'atualizados':atualizados,'erros':erros})}\n\n"

    return StreamingResponse(generator(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


if __name__ == "__main__":
    import subprocess, time, uvicorn
    try:
        saida = subprocess.check_output("netstat -ano | findstr :8001", shell=True).decode()
        for linha in saida.strip().split('\n'):
            col = linha.strip().split()
            pid = col[-1] if col else ""
            if pid.isdigit() and pid != '0':
                subprocess.run(f"taskkill /PID {pid} /F", shell=True, capture_output=True)
        time.sleep(0.5)
    except Exception:
        pass
    config = load_json(CONFIG_PATH)
    host = config.get("servidor", {}).get("host", "127.0.0.1")
    port = config.get("servidor", {}).get("port", 8001)
    uvicorn.run("main:app", host=host, port=port, reload=False)


def _validar_datas(de: Optional[str], ate: Optional[str]):
    for v in (de, ate):
        if v:
            try:
                datetime.strptime(v, "%Y-%m-%d")
            except ValueError:
                raise HTTPException(status_code=400, detail="datas devem estar no formato AAAA-MM-DD")


@app.get("/sac/resumo")
async def sac_resumo(de: Optional[str] = None, ate: Optional[str] = None):
    """Dashboard geral do SAC: perguntas, mensagens e reclamações pendentes por conta. Somente leitura."""
    _validar_datas(de, ate)
    return await ml_api.sac_resumo(de, ate)


@app.get("/sac/{conta}/pendencias")
async def sac_pendencias(conta: str, de: Optional[str] = None, ate: Optional[str] = None):
    """Pendências do SAC de uma conta, com as listas. Somente leitura (nada é marcado como lido)."""
    _validar_datas(de, ate)
    if conta not in ("meli01", "meli02", "meli03"):
        raise HTTPException(status_code=404, detail="conta inválida")
    return await ml_api.sac_pendencias(conta, de, ate)


@app.get("/sac/{conta}/contestacoes")
async def sac_contestacoes(conta: str, limite: int = 30, abertas: bool = True,
                           de: Optional[str] = None, ate: Optional[str] = None):
    """Contestações de devolução (etapa recontact) com o desfecho de cada uma. Somente leitura."""
    if not 1 <= limite <= 50:
        raise HTTPException(status_code=400, detail="limite deve estar entre 1 e 50")
    _validar_datas(de, ate)
    res = await ml_api.sac_contestacoes(conta, limite, abertas, de, ate)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error", "erro"))
    shared_store.enviar_lote_async("contestacoes", [(f"{conta}|{i['claim_id']}", i) for i in res.get("itens", []) if i.get("claim_id")])
    return res


@app.get("/sac/{conta}/contestacoes/compartilhadas")
def sac_contestacoes_compartilhadas(conta: str):
    """Contestações consultadas por qualquer computador da equipe (banco compartilhado), mais recentes primeiro."""
    itens = list((_ler_json(CONTESTACOES_PATH).get(conta) or {}).values())
    itens.sort(key=lambda i: i.get("atualizada_em") or "", reverse=True)
    return {"success": True, "conta": conta, "itens": itens}
