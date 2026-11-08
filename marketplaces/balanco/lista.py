"""
marketplaces/balanco/lista.py — Lista de contagem do balanço (somente leitura)
MZ App

Uma linha por SKU físico (kits e clones são composições: só os componentes entram). Soma os três Blings e separa em
recorrentes e sazonais pela categoria do produto no Bling.
"""

import unicodedata

from marketplaces.balanco import catalogo
from marketplaces.bling import estoque

# Trechos (sem acento, minúsculos) de categorias sazonais. A categoria "Artigos Sazonais" e as filhas dela também contam.
# Lista inicial: o usuário confirma e ajusta.
SAZONAIS = ["sazonal", "natal", "pisca", "guirlanda", "decoracao", "fantasia", "mascara", "bal", "bandeirinha", "halloween", "escolar",
            "junina", "enfeites para arvore"]   # 08/11: laços de Natal e roupas de festa junina estavam como recorrentes


def _norm(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", t or "") if unicodedata.category(c) != "Mn").lower()


def classe(categoria: str) -> str:
    n = _norm(categoria)
    return "sazonal" if any(t in n for t in SAZONAIS) else "recorrente"


def montar() -> dict:
    cat = catalogo.ler()
    blings = cat.get("blings") or {}
    saldos = {}
    for b in estoque.BLINGS:
        try:
            saldos[b] = estoque.saldos(b)["saldos"]
        except Exception:
            saldos[b] = {}
    skus = {}
    for b, d in blings.items():
        for k, v in d.get("skus", {}).items():
            x = skus.setdefault(k, {"nome": v.get("nome", ""), "categoria": ""})
            x["categoria"] = x["categoria"] or v.get("categoria", "")
            x["nome"] = x["nome"] or v.get("nome", "")
    compoe = {}
    for b, d in blings.items():
        for kit, c in d.get("kits", {}).items():
            for comp, qtd in c.get("comp", []):
                compoe.setdefault(comp, {})[kit] = qtd
    linhas = []
    for k, x in skus.items():
        por = {b: saldos[b].get(k) for b in estoque.BLINGS}
        total = sum(v or 0 for v in por.values())
        linhas.append({"sku": k, "nome": x["nome"], "categoria": x["categoria"], "classe": classe(x["categoria"]),
                       "bling": por, "total": total, "compoe": sorted(compoe.get(k, {}))[:6], "n_kits": len(compoe.get(k, {}))})
    linhas.sort(key=lambda l: (l["categoria"], l["sku"]))
    return {"gerado_em": cat.get("gerado_em"), "erros": cat.get("erros", {}), "linhas": linhas}
