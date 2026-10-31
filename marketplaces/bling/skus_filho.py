"""
marketplaces/bling/skus_filho.py — SKU da variação (filho) dos anúncios do ML (somente leitura)
MZ App

Alguns anúncios têm no campo SKU o código do produto pai do Bling (formato V, sem saldo) e o código da
variação no atributo SELLER_SKU (ex.: REL-REDONDO / REL-REDONDO-PRETO). O saldo está no filho.
"""

import json
import os
import threading
import time

import reposicao_full

CACHE_SEG = 3600
_cache = {}
_trava = threading.Lock()
RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _anuncios(conta: str) -> list:
    with open(os.path.join(RAIZ, "produtos.json"), encoding="utf-8") as f:
        produtos = json.load(f)
    return [m for m, p in produtos.items() if p.get("conta") == conta]


def _calcular(conta: str) -> dict:
    ml = reposicao_full.ML(conta)
    ids, filhos = _anuncios(conta), {}
    for i in range(0, len(ids), 20):
        st, lote = ml.get("/items", ids=",".join(ids[i:i + 20]), attributes="id,seller_custom_field,attributes")
        for e in lote or []:
            if e.get("code") != 200:
                continue
            b = e["body"]
            attr = next((str(a["value_name"]).strip() for a in b.get("attributes") or []
                         if a.get("id") == "SELLER_SKU" and a.get("value_name")), "")
            campo = (b.get("seller_custom_field") or "").strip()
            if attr and campo and attr.upper() != campo.upper():
                filhos[b["id"]] = attr
    return filhos


def por_conta(conta: str, forcar: bool = False) -> dict:
    """{MLB: SKU da variação} só dos anúncios em que o atributo SELLER_SKU difere do campo SKU."""
    with _trava:
        c = _cache.get(conta)
        if c and not forcar and time.time() - c[0] < CACHE_SEG:
            return c[1]
    try:
        f = _calcular(conta)
    except Exception:
        return c[1] if c else {}
    with _trava:
        _cache[conta] = (time.time(), f)
    return f
