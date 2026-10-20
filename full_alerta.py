"""
full_alerta.py — Aviso de produto sem estoque ou perto de romper no Full (Mercado Livre), somente leitura
MZCell Rentabilidade

Regra: média diária de vendas dos últimos 7 dias fechados, somando todos os anúncios que dividem o mesmo
inventory_id, contra o saldo do Full (disponível + em transferência, que já é apto e volta a ficar disponível).
  saldo 0                         -> "zerado"  (só se o anúncio está ativo ou vendeu nos últimos 30 dias)
  cobertura de até 5 dias         -> "critico"
  cobertura de 6 a 10 dias        -> "atencao" (saldo não cobre mais de 10 dias)
Anúncio sem Full (sem inventory_id) não entra. As vendas vêm do produtos.json (última sincronização).
"""

import json
import os
import time
from datetime import date, datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PRODUTOS_PATH = os.path.join(BASE_DIR, "produtos.json")
CACHE_PATH = os.path.join(BASE_DIR, "full_alertas_cache.json")
DIAS_MEDIA = 7
LIMITE_DIAS = 10
LIMITE_CRITICO = 5
VALIDADE_MIN = 30


def classificar(saldo: float, media_7d: float, ativo: bool = True, vendeu_30d: bool = False):
    """(nivel, dias_de_cobertura) com nivel None, 'atencao', 'critico' ou 'zerado'."""
    if saldo <= 0:
        return ("zerado", 0.0) if (ativo or vendeu_30d or media_7d > 0) else (None, None)
    if media_7d <= 0 or not ativo:      # anúncio pausado não está vendendo: sem aviso de ruptura
        return None, None
    dias = round(saldo / media_7d, 1)
    if dias <= LIMITE_CRITICO:
        return "critico", dias
    if dias <= LIMITE_DIAS:
        return "atencao", dias
    return None, dias


def media_diaria(dias: dict, ate: date, n: int = DIAS_MEDIA) -> float:
    """Unidades por dia nos n dias fechados terminando em `ate` (inclusive)."""
    total = 0
    for i in range(n):
        d = (ate - timedelta(days=i)).isoformat()
        total += (dias.get(d) or {}).get("unidades", 0) or 0
    return total / n


def _inventarios(item: dict) -> list:
    ids = []
    if item.get("inventory_id"):
        ids.append(item["inventory_id"])
    for v in item.get("variations") or []:
        if v.get("inventory_id") and v["inventory_id"] not in ids:
            ids.append(v["inventory_id"])
    return ids


def calcular(conta: str, ml, produtos: dict = None, ate: date = None) -> dict:
    """`ml` é o cliente do ML (reposicao_full.ML). Devolve {mlb: {...}} só com o que tem Full."""
    from reposicao_full import estoque
    if produtos is None:
        with open(PRODUTOS_PATH, encoding="utf-8") as f:
            produtos = json.load(f)
    ate = ate or (datetime.now().date() - timedelta(days=1))
    meus = {m: p for m, p in produtos.items() if p.get("conta") == conta and not p.get("arquivado")}
    itens = {}
    ids = list(meus)
    for i in range(0, len(ids), 20):
        st, b = ml.get("/items", ids=",".join(ids[i:i + 20]), attributes="id,status,inventory_id,variations,shipping")
        for e in b or []:
            if e.get("code") == 200:
                itens[e["body"]["id"]] = e["body"]
    por_inv, status = {}, {}
    for mlb, it in itens.items():
        if (it.get("shipping") or {}).get("logistic_type") != "fulfillment":
            continue
        for inv in _inventarios(it):
            por_inv.setdefault(inv, []).append(mlb)
        status[mlb] = it.get("status")
    saidas = {}
    for inv, mlbs in por_inv.items():
        e = estoque(ml, inv)
        if "erro" in e:
            continue
        saldo = (e.get("disponivel") or 0) + (e.get("transferencia") or 0)
        media = sum(media_diaria(meus[m].get("dias", {}), ate) for m in mlbs)
        m30 = sum(media_diaria(meus[m].get("dias", {}), ate, 30) for m in mlbs)
        for m in mlbs:
            ativo_grupo = any(status.get(x) == "active" for x in mlbs)     # estoque compartilhado: basta um anúncio ativo
            nivel, dias = classificar(saldo, media, ativo_grupo, m30 > 0)
            saidas[m] = {"inventory_id": inv, "saldo": saldo, "media_7d": round(media, 2), "dias_cobertura": dias,
                         "nivel": nivel, "anuncios_no_inventario": len(mlbs)}
    return saidas


def _ler() -> dict:
    try:
        with open(CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def obter(conta: str, ml, forcar: bool = False) -> dict:
    cache = _ler()
    c = cache.get(conta)
    if c and not forcar:
        idade = (datetime.now() - datetime.fromisoformat(c["gerado_em"])).total_seconds() / 60
        if idade < VALIDADE_MIN:
            return c
    itens = calcular(conta, ml)
    res = {"gerado_em": datetime.now().isoformat(timespec="seconds"), "conta": conta, "itens": itens,
           "resumo": {n: sum(1 for v in itens.values() if v["nivel"] == n) for n in ("zerado", "critico", "atencao")}}
    cache[conta] = res
    tmp = CACHE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)
    os.replace(tmp, CACHE_PATH)
    return res
