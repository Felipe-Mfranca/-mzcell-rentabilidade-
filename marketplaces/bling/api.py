"""
marketplaces/bling/api.py — Acesso à API v3 do Bling (somente leitura por enquanto)
MZ App

Credenciais e tokens ficam em bling_config.json (no .gitignore).
Regra do projeto: dados do Bling NÃO entram no cálculo de margem/CMV.
"""

import base64
import json
import os
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta

import httpx

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_PATH = os.path.join(BASE_DIR, "bling_config.json")
API = "https://api.bling.com.br/Api/v3"
AUTHORIZE_URL = "https://www.bling.com.br/Api/v3/oauth/authorize"
TOKEN_URL = f"{API}/oauth/token"
REDIRECT_PORTA = 8765
REDIRECT_URI = f"http://127.0.0.1:{REDIRECT_PORTA}/bling/callback"
_TRAVA_RENOVACAO = defaultdict(threading.Lock)  # o refresh_token do Bling é de uso único: uma renovação por vez em cada conta
_ESPERA_APOS_FALHA = 300  # conta marcada para reautorizar: só tenta renovar de novo depois disso (ou se o refresh_token mudou)
_ESTADO = {}  # conta -> {"motivo", "refresh_falho", "quando", "ts"} quando o acesso morreu e precisa de reautorização


def estado_contas() -> dict:
    """Contas que precisam de reautorização (sem o token). Some sozinho quando uma chamada volta a funcionar."""
    return {c: {"motivo": v["motivo"], "quando": v["quando"]} for c, v in _ESTADO.items()}


def _marcar_morta(conta_id: str, motivo: str, refresh):
    _ESTADO[conta_id] = {"motivo": str(motivo)[:200], "refresh_falho": refresh, "ts": time.time(),
                         "quando": datetime.now().isoformat(timespec="seconds")}


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_config(config: dict):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def _basic(conta: dict) -> str:
    cred = f"{conta['client_id']}:{conta['client_secret']}".encode()
    return "Basic " + base64.b64encode(cred).decode()


def _salvar_tokens(conta_id: str, data: dict):
    config = load_config()
    c = config["contas"][conta_id]
    c["access_token"] = data["access_token"]
    c["refresh_token"] = data.get("refresh_token", c.get("refresh_token"))
    c["token_expires_at"] = (datetime.now() + timedelta(seconds=data.get("expires_in", 21600))).isoformat()
    save_config(config)


def trocar_code(conta_id: str, code: str) -> dict:
    """Troca o authorization code (válido por 1 minuto) pelos tokens."""
    c = load_config()["contas"][conta_id]
    r = httpx.post(TOKEN_URL, data={"grant_type": "authorization_code", "code": code},
                   headers={"Authorization": _basic(c), "Accept": "1.0", "enable-jwt": "1",
                            "Content-Type": "application/x-www-form-urlencoded"}, timeout=30)
    if r.status_code != 200:
        return {"success": False, "error": f"{r.status_code} {r.text[:300]}"}
    _salvar_tokens(conta_id, r.json())
    return {"success": True}


def refresh_token(conta_id: str, token_usado: str = None) -> dict:
    """Renova o token. Chamadas simultâneas esperam a primeira: se o token já mudou desde o que a chamada usou, não renova de novo."""
    with _TRAVA_RENOVACAO[conta_id]:
        c = load_config()["contas"][conta_id]
        if token_usado and c.get("access_token") != token_usado:
            return {"success": True}
        morta = _ESTADO.get(conta_id)
        if morta and morta["refresh_falho"] == c.get("refresh_token") and time.time() - morta["ts"] < _ESPERA_APOS_FALHA:
            return {"success": False, "error": "reautorização necessária: " + morta["motivo"]}
        return _renovar(conta_id, c)


def _renovar(conta_id: str, c: dict) -> dict:
    if not c.get("refresh_token") or str(c["refresh_token"]).startswith("COLE_AQUI"):
        _marcar_morta(conta_id, "sem refresh_token", None)
        return {"success": False, "error": "sem refresh_token — rode python -m marketplaces.bling.auth"}
    r = httpx.post(TOKEN_URL, data={"grant_type": "refresh_token", "refresh_token": c["refresh_token"]},
                   headers={"Authorization": _basic(c), "Accept": "1.0", "enable-jwt": "1",
                            "Content-Type": "application/x-www-form-urlencoded"}, timeout=30)
    if r.status_code != 200:
        if r.status_code in (400, 401):   # invalid_grant: o refresh_token morreu; só reautorizando
            _marcar_morta(conta_id, f"{r.status_code} {r.text[:120]}", c.get("refresh_token"))
        return {"success": False, "error": f"{r.status_code} {r.text[:300]}"}
    _salvar_tokens(conta_id, r.json())
    _ESTADO.pop(conta_id, None)
    return {"success": True}


def get(conta_id: str, path: str, **params):
    """GET com renovação automática do token (expirado ou 401) e espera em 429 (limite 3/s)."""
    c = load_config()["contas"][conta_id]
    exp = c.get("token_expires_at")
    if not c.get("access_token") or (exp and datetime.fromisoformat(exp) < datetime.now() + timedelta(minutes=5)):
        refresh_token(conta_id, c.get("access_token"))
    renovou, espera = False, 1
    for _ in range(8):
        tok = load_config()["contas"][conta_id]["access_token"]
        r = httpx.get(API + path, params=params, timeout=30,
                      headers={"Authorization": f"Bearer {tok}", "Accept": "application/json", "enable-jwt": "1"})
        if r.status_code == 401 and not renovou:
            renovou = True
            if refresh_token(conta_id, tok)["success"]:
                continue
        if r.status_code == 429:
            time.sleep(espera)
            espera = min(espera * 2, 30)
            continue
        break
    if r.status_code == 200:
        _ESTADO.pop(conta_id, None)   # voltou a funcionar (por exemplo, depois de reautorizar)
    time.sleep(0.35)  # mantém abaixo de 3 requisições por segundo
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, r.text[:300]
