"""
marketplaces/amazon/calculos.py — Matemática do módulo Amazon (sem rede, sem disco)
MZCell Rentabilidade

Tudo aqui é determinístico e testável sozinho. O acesso à API fica em api.py.
"""

from datetime import date, timedelta


def agrupar_estoque_por_asin(linhas: list) -> list:
    """
    Consolida o inventário FBA (uma linha por seller SKU) em uma linha por ASIN.

    O mesmo ASIN pode ter vários SKUs FBA (ex.: KIT3TAP-FBA e KIT3-TAPMAG-SORTIDO em
    B0GXX6NG28). As vendas são apuradas por ASIN, então uma linha por SKU faria cada
    uma receber a venda cheia do ASIN e dobraria a meta de estoque.
    """
    grupos = {}
    for linha in linhas:
        g = grupos.get(linha["asin"])
        if g is None:
            grupos[linha["asin"]] = g = {**linha, "skus": []}
        else:
            g["estoque_valido"] += linha["estoque_valido"]
            g["disponivel"] += linha["disponivel"]
            g["em_transito"] += linha["em_transito"]
            if not g["titulo"]:
                g["titulo"] = linha["titulo"]
        if linha["sku"]:
            g["skus"].append(linha["sku"])

    for g in grupos.values():
        g["skus"] = sorted(set(g["skus"]))
        g["sku"] = " + ".join(g["skus"])
    return list(grupos.values())


def linha_de_estoque(resumo: dict) -> dict:
    """
    Converte um item de inventorySummaries da SP-API no formato do app.

    Estoque válido = disponível + em trânsito (working + shipped + receiving).
    Reservado e não vendável ficam de fora: não podem ser vendidos. Incluir o que já
    está a caminho evita reenviar o mesmo lote.
    """
    d = resumo.get("inventoryDetails") or {}
    disponivel = d.get("fulfillableQuantity") or 0
    em_transito = ((d.get("inboundWorkingQuantity") or 0)
                   + (d.get("inboundShippedQuantity") or 0)
                   + (d.get("inboundReceivingQuantity") or 0))
    return {
        "asin": resumo.get("asin", ""),
        "sku": resumo.get("sellerSku", ""),
        "titulo": resumo.get("productName", ""),
        "fnsku": resumo.get("fnSku", ""),
        "disponivel": disponivel,
        "em_transito": em_transito,
        "estoque_valido": disponivel + em_transito,
    }


def _data_do_bucket(bucket: dict):
    """Data inicial do bucket ('2026-09-14T00:00:00-07:00--2026-09-15T...'). None se ilegível."""
    try:
        return date.fromisoformat(str(bucket.get("interval", ""))[:10])
    except ValueError:
        return None


def velocidade(buckets: list, hoje: date, peso_7d: float = 0.40, peso_30d: float = 0.60) -> dict:
    """
    Velocidade de venda a partir dos buckets diários do orderMetrics.

    Os 7 dias são os que começam a menos de 7 dias de `hoje`, medidos pela data do
    bucket e não pela posição na lista: se a resposta omitir dias sem venda, "os
    últimos 7 itens" cobriria uma janela maior e inflaria a velocidade.
    Peso da Amazon (doc 04): 7d × 40% + 30d × 60%.
    """
    corte = hoje - timedelta(days=7)
    vendido_30d = vendido_7d = 0
    for b in buckets:
        und = b.get("unitCount") or 0
        vendido_30d += und
        d = _data_do_bucket(b)
        if d is not None and d >= corte:
            vendido_7d += und

    vel_7d, vel_30d = vendido_7d / 7, vendido_30d / 30
    return {
        "vendido_7d": vendido_7d,
        "vendido_30d": vendido_30d,
        "velocidade_7d": round(vel_7d, 3),
        "velocidade_30d": round(vel_30d, 3),
        "velocidade": round(vel_7d * peso_7d + vel_30d * peso_30d, 3),
    }
