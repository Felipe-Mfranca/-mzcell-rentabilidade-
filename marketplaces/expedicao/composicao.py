"""
marketplaces/expedicao/composicao.py — O que cada kit leva, para a lista de montagem
MZ App

Só lê o catálogo já guardado (balanco_catalogo.json); não chama o Bling. Sem catálogo, a lista segue sem a composição.
"""

from marketplaces.balanco import catalogo
from marketplaces.expedicao.bling_checkout import PARES

try:
    from cmv_composicao import DIRETO_NO_PRODUTO
except ImportError:
    DIRETO_NO_PRODUTO = set()


def _chave(sku) -> str:
    return str(sku or "").strip().upper()


def _kits(blings: dict) -> dict:
    """{bling: {KIT: [(componente, qtd), ...]}}. Clone (1× outro SKU) e produto unitário ficam de fora: são uma peça só."""
    saida = {}
    for b, dados in blings.items():
        saida[b] = {}
        for kit, x in (dados.get("kits") or {}).items():
            comp = [(str(c).strip(), float(q or 1)) for c, q in x.get("comp") or []]
            if not comp or _chave(kit) in DIRETO_NO_PRODUTO or (len(comp) == 1 and comp[0][1] == 1):
                continue
            saida[b][_chave(kit)] = comp
    return saida


def anexar(linhas: list) -> list:
    """Devolve as linhas com `comp` nos itens que são kit, sem alterar as originais (ficam no cache do mapa)."""
    try:
        kits = _kits(catalogo.ler().get("blings") or {})
    except Exception:
        return linhas
    saida = []
    for l in linhas:
        par = PARES.get(l.get("conta"))
        itens = []
        for i in l.get("itens") or []:
            s = _chave(i.get("sku"))
            comp = (kits.get(par) or {}).get(s) or next((k[s] for k in kits.values() if s in k), None)
            itens.append(dict(i, comp=[{"sku": c, "qtd": q} for c, q in comp]) if comp else i)
        saida.append(dict(l, itens=itens))
    return saida
