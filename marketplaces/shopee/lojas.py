"""
marketplaces/shopee/lojas.py — Configuração da Shopee: ambiente, credenciais do app e lojas
MZCell Rentabilidade

Credenciais do app ficam em .env (raiz do projeto, gitignored) e só o backend as lê.
As lojas (nome -> shop_id) ficam em shopee_lojas.json (gitignored):
    {"techseller": {"nome": "Techseller", "shop_id": 123}, "mzcell": {...}, "mizi": {...}}
Sem esse arquivo, vale a loja única de SHOPEE_<ENV>_SHOP_ID (chamada "padrao").

Nada aqui devolve valor de credencial: só nomes de chave e se existem.
"""

import json
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENV_PATH = os.path.join(BASE_DIR, ".env")
LOJAS_PATH = os.path.join(BASE_DIR, "shopee_lojas.json")


class ErroShopee(Exception):
    def __init__(self, msg: str, status: int = 502):
        super().__init__(msg)
        self.status = status


def ler_env(caminho: str = None) -> dict:
    """Lê o .env (CHAVE=valor, # comentários). Variáveis do processo têm prioridade. Não imprime nada."""
    dados = {}
    caminho = caminho or ENV_PATH
    if os.path.exists(caminho):
        with open(caminho, encoding="utf-8-sig") as f:
            for linha in f:
                linha = linha.strip()
                if not linha or linha.startswith("#") or "=" not in linha:
                    continue
                k, v = linha.split("=", 1)
                dados[k.strip()] = v.strip().strip('"').strip("'")
    for k in list(dados):
        if k in os.environ:
            dados[k] = os.environ[k]
    for k, v in os.environ.items():
        if k.startswith("SHOPEE_"):
            dados[k] = v
    return dados


def ambiente(env: dict = None) -> str:
    """'SANDBOX' (padrão) ou 'PROD'. Só vai para produção se o .env disser SHOPEE_ENV=prod."""
    valor = (env if env is not None else ler_env()).get("SHOPEE_ENV", "sandbox").strip().upper()
    if valor not in ("SANDBOX", "PROD"):
        raise ErroShopee(f"SHOPEE_ENV inválido: use sandbox ou prod", 500)
    return valor


def cfg(chave: str, env: dict = None) -> str:
    env = env if env is not None else ler_env()
    nome = f"SHOPEE_{ambiente(env)}_{chave}"
    valor = env.get(nome)
    if not valor:
        raise ErroShopee(f"{nome} não definida no .env", 500)
    return valor


def configurado(env: dict = None) -> bool:
    """True se há URL base, partner_id e partner_key do ambiente ativo (sem expor os valores)."""
    env = env if env is not None else ler_env()
    try:
        return all(cfg(c, env) for c in ("BASE_URL", "PARTNER_ID", "PARTNER_KEY"))
    except ErroShopee:
        return False


def lojas(env: dict = None, caminho: str = None) -> dict:
    """{id_da_loja: {"nome":..., "shop_id": int}}."""
    caminho = caminho or LOJAS_PATH
    if os.path.exists(caminho):
        try:
            with open(caminho, encoding="utf-8") as f:
                bruto = json.load(f)
        except ValueError as e:
            raise ErroShopee(f"shopee_lojas.json inválido: {e}", 500)
        saida = {}
        for k, v in bruto.items():
            try:
                saida[k] = {"nome": v.get("nome", k), "shop_id": int(v["shop_id"])}
            except (KeyError, TypeError, ValueError, AttributeError):
                raise ErroShopee(f"shopee_lojas.json: loja '{k}' precisa de shop_id numérico", 500)
        return saida
    env = env if env is not None else ler_env()
    sid = env.get(f"SHOPEE_{ambiente(env)}_SHOP_ID")
    if sid and sid.isdigit():
        return {"padrao": {"nome": "Loja padrão", "shop_id": int(sid)}}
    return {}


def shop_id_da(loja: str, env: dict = None, caminho: str = None) -> int:
    todas = lojas(env, caminho)
    if loja not in todas:
        raise ErroShopee(f"loja '{loja}' não cadastrada (conhecidas: {', '.join(todas) or 'nenhuma'})", 404)
    return todas[loja]["shop_id"]
