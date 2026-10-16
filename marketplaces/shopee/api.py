"""
marketplaces/shopee/api.py — Cliente da Shopee Open Platform API v2 (somente leitura)
MZCell Rentabilidade

Portado de shopee_poc (shopee-claude-code.zip): assinatura HMAC-SHA256, tokens por ambiente e loja,
renovação automática, janelas de 15 dias. Diferenças: httpx (já é dependência do app), leitor de .env
próprio e nenhuma função de escrita (estoque/preço ficam de fora).

Regras:
  - Partner key, access token, refresh token e code nunca saem do backend nem entram em mensagem de erro
    (`mascarar`).
  - Tokens ficam em .shopee_tokens.json (gitignored): {"SANDBOX": {"<shop_id>": {...}}, "PROD": {...}}.
  - O refresh token vale ~30 dias e é regravado a cada renovação.
"""

import hashlib
import hmac
import json
import os
import re
import threading
import time
from datetime import datetime, timedelta

import httpx

from marketplaces.shopee import lojas
from marketplaces.shopee.lojas import ErroShopee

TOKENS_PATH = os.path.join(lojas.BASE_DIR, ".shopee_tokens.json")
TIMEOUT = 30
JANELA_DIAS = 15            # get_order_list aceita no máximo 15 dias por consulta
VALIDADE_REFRESH_DIAS = 30
ALERTA_AMARELO_DIAS = 7
ALERTA_VERMELHO_DIAS = 3
_trava = threading.Lock()   # renovação de token: uma por vez (o refresh token muda a cada uso)

_PADRAO_SEGREDO = re.compile(r"(?i)\b(access_token|refresh_token|sign|code|partner_key)=([^&\s\"']+)")


def mascarar(texto, segredos=()) -> str:
    """Remove credenciais de um texto: parâmetros de URL conhecidos e valores secretos informados."""
    t = _PADRAO_SEGREDO.sub(lambda m: f"{m.group(1)}=***", str(texto))
    for s in segredos:
        if s and len(str(s)) >= 6:
            t = t.replace(str(s), "***")
    return t


# ─── assinatura e tokens ─────────────────────────────────────────────────────

def assinar(caminho: str, ts: int, access_token: str = "", shop_id="", env: dict = None) -> str:
    """HMAC-SHA256(partner_id + path + timestamp [+ access_token + shop_id]) com a partner key."""
    env = env if env is not None else lojas.ler_env()
    base = f"{lojas.cfg('PARTNER_ID', env)}{caminho}{ts}{access_token}{shop_id}"
    return hmac.new(lojas.cfg("PARTNER_KEY", env).encode(), base.encode(), hashlib.sha256).hexdigest()


def _ler_tokens() -> dict:
    if not os.path.exists(TOKENS_PATH):
        return {}
    try:
        with open(TOKENS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except ValueError:
        raise ErroShopee(".shopee_tokens.json ilegível; refaça a autorização da loja", 500)


def _gravar_token(env_nome: str, shop_id: int, dados: dict):
    todos = _ler_tokens()
    agora = int(time.time())
    todos.setdefault(env_nome, {})[str(shop_id)] = {
        "access_token": dados["access_token"],
        "refresh_token": dados["refresh_token"],
        "expires_at": agora + int(dados.get("expire_in", 14400)) - 300,
        "saved_at": agora,
    }
    tmp = TOKENS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:   # grava inteiro e troca: nunca deixa o arquivo pela metade
        json.dump(todos, f, indent=2)
    os.replace(tmp, TOKENS_PATH)


def _token(env_nome: str, shop_id: int) -> dict:
    t = _ler_tokens().get(env_nome, {}).get(str(shop_id))
    if not t:
        raise ErroShopee(f"loja {shop_id} sem autorização; autorize a loja primeiro", 401)
    return t


def _post_publico(caminho: str, corpo: dict, env: dict) -> dict:
    ts = int(time.time())
    params = {"partner_id": int(lojas.cfg("PARTNER_ID", env)), "timestamp": ts,
              "sign": assinar(caminho, ts, env=env)}
    segredos = [lojas.cfg("PARTNER_KEY", env), corpo.get("code"), corpo.get("refresh_token")]
    try:
        r = httpx.post(lojas.cfg("BASE_URL", env).rstrip("/") + caminho, params=params, json=corpo, timeout=TIMEOUT)
        dados = r.json()
    except (httpx.HTTPError, ValueError) as e:
        raise ErroShopee(mascarar(f"falha ao falar com a Shopee: {e}", segredos))
    if dados.get("error"):
        raise ErroShopee(mascarar(f"{dados.get('error')}: {dados.get('message')}", segredos), 400)
    return dados


def url_autorizacao(env: dict = None) -> str:
    """Link para o dono da loja autorizar o app (a assinatura vai na URL, a partner key não)."""
    env = env if env is not None else lojas.ler_env()
    caminho, ts = "/api/v2/shop/auth_partner", int(time.time())
    redirect = env.get("SHOPEE_REDIRECT_URL", "")
    if not redirect:
        raise ErroShopee("SHOPEE_REDIRECT_URL não definida no .env", 500)
    return (f"{lojas.cfg('BASE_URL', env).rstrip('/')}{caminho}?partner_id={lojas.cfg('PARTNER_ID', env)}"
            f"&timestamp={ts}&sign={assinar(caminho, ts, env=env)}&redirect={redirect}")


def trocar_codigo(code: str, shop_id: int, env: dict = None) -> dict:
    """Troca o `code` do redirect por tokens da loja. Devolve só dados não secretos."""
    env = env if env is not None else lojas.ler_env()
    d = _post_publico("/api/v2/auth/token/get", {"code": code, "shop_id": int(shop_id),
                                                   "partner_id": int(lojas.cfg("PARTNER_ID", env))}, env)
    _gravar_token(lojas.ambiente(env), int(shop_id), d)
    return {"shop_id": int(shop_id), "expire_in": d.get("expire_in")}


def renovar(shop_id: int, env: dict = None) -> dict:
    env = env if env is not None else lojas.ler_env()
    nome = lojas.ambiente(env)
    with _trava:
        t = _token(nome, shop_id)
        d = _post_publico("/api/v2/auth/access_token/get",
                          {"refresh_token": t["refresh_token"], "shop_id": int(shop_id),
                           "partner_id": int(lojas.cfg("PARTNER_ID", env))}, env)
        _gravar_token(nome, shop_id, d)   # o refresh token muda a cada renovação
    return {"shop_id": int(shop_id), "expire_in": d.get("expire_in")}


# ─── chamadas ────────────────────────────────────────────────────────────────

def chamar(caminho: str, shop_id: int, params: dict = None, env: dict = None) -> dict:
    """GET assinado. Renova o token vencido ou recusado (1x) e recua no limite de chamadas (429)."""
    env = env if env is not None else lojas.ler_env()
    nome = lojas.ambiente(env)
    t = _token(nome, shop_id)
    if time.time() >= t["expires_at"]:
        renovar(shop_id, env)
        t = _token(nome, shop_id)
    renovado = False
    for espera in (0, 2, 5, 15):
        time.sleep(espera)
        ts = int(time.time())
        q = {"partner_id": int(lojas.cfg("PARTNER_ID", env)), "timestamp": ts, "access_token": t["access_token"],
             "shop_id": shop_id, "sign": assinar(caminho, ts, t["access_token"], shop_id, env)}
        q.update(params or {})
        segredos = [t["access_token"], t["refresh_token"], lojas.cfg("PARTNER_KEY", env)]
        try:
            r = httpx.get(lojas.cfg("BASE_URL", env).rstrip("/") + caminho, params=q, timeout=TIMEOUT)
            dados = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise ErroShopee(mascarar(f"falha ao falar com a Shopee: {e}", segredos))
        erro = str(dados.get("error") or "")
        if not erro:
            return dados
        if r.status_code == 429 or "rate" in erro.lower() or "too_many" in erro.lower():
            continue
        if not renovado and ("access_token" in erro.lower() or "token" in str(dados.get("message", "")).lower()):
            renovado = True
            renovar(shop_id, env)
            t = _token(nome, shop_id)
            continue
        raise ErroShopee(mascarar(f"{erro}: {dados.get('message')} (request_id={dados.get('request_id')})", segredos), 400)
    raise ErroShopee("a Shopee limitou as chamadas (rate limit); tente de novo em alguns minutos", 429)


def janelas(de: datetime, ate: datetime, dias: int = JANELA_DIAS):
    """Divide [de, ate] em pedaços de até `dias` dias; devolve pares de timestamps (segundos)."""
    atual = de
    while atual < ate:
        prox = min(atual + timedelta(days=dias), ate)
        yield int(atual.timestamp()), int(prox.timestamp())
        atual = prox


def info_loja(shop_id: int, env: dict = None) -> dict:
    """get_shop_info, só com campos não sensíveis."""
    r = chamar("/api/v2/shop/get_shop_info", shop_id, env=env)
    return {k: r.get(k) for k in ("shop_name", "region", "status", "expire_time", "is_cb", "is_sip") if k in r}


# ─── estado da autorização ───────────────────────────────────────────────────

def estado_autorizacao(shop_id: int, env: dict = None, agora: float = None) -> dict:
    """Situação da autorização sem expor tokens. O prazo é saved_at + 30 dias (refresh token)."""
    env = env if env is not None else lojas.ler_env()
    t = _ler_tokens().get(lojas.ambiente(env), {}).get(str(shop_id))
    if not t:
        return {"autorizada": False, "nivel": "vermelho", "dias_restantes": None, "expira_em": None,
                "mensagem": "Loja sem autorização. Autorize a loja."}
    expira = datetime.fromtimestamp(t["saved_at"]) + timedelta(days=VALIDADE_REFRESH_DIAS)
    dias = (expira - (datetime.fromtimestamp(agora) if agora else datetime.now())).total_seconds() / 86400
    if dias <= 0:
        nivel, msg = "vermelho", "Autorização vencida. Refaça a autorização da loja."
    elif dias <= ALERTA_VERMELHO_DIAS:
        nivel, msg = "vermelho", f"Autorização expira em {dias:.1f} dias. Refaça a autorização."
    elif dias <= ALERTA_AMARELO_DIAS:
        nivel, msg = "amarelo", f"Autorização expira em {dias:.0f} dias."
    else:
        nivel, msg = "ok", "Autorização em dia."
    return {"autorizada": dias > 0, "nivel": nivel, "dias_restantes": round(dias, 1),
            "expira_em": expira.isoformat(timespec="minutes"), "mensagem": msg}
