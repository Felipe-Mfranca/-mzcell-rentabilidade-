"""
marketplaces/amazon/promocoes.py — Descontos de promoção por ASIN no período (Amazon, somente leitura)
MZ App

Lê o relatório de pedidos (GET_FLAT_FILE_ALL_ORDERS_DATA_BY_ORDER_DATE_GENERAL) e soma:
  - desconto de item (item-promotion-discount), por ASIN e por promotion-id;
  - desconto de frete (ship-promotion-discount), por ASIN;
  - ASINs que venderam com mais de um preço unitário no período (preço que mudou ou promoção aplicada).

O relatório de cupons (GET_COUPON_PERFORMANCE_REPORT) e o de promoções (GET_PROMOTION_PERFORMANCE_REPORT)
devolvem FATAL quando não há dados no período. Por isso o módulo só usa o relatório de pedidos.
Nomes de colunas vêm do relatório em inglês; a tela mostra em português.
"""

from collections import Counter, defaultdict

from marketplaces.amazon import api, relatorios

TIPO_PEDIDOS = "GET_FLAT_FILE_ALL_ORDERS_DATA_BY_ORDER_DATE_GENERAL"


def _num(v) -> float:
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return 0.0


def promocoes_do_periodo(conta: str, de: str, ate: str) -> dict:
    """de/ate em AAAA-MM-DD (ate exclusivo, como nos relatórios da Amazon)."""
    rid = relatorios.criar(conta, TIPO_PEDIDOS, f"{de}T00:00:00Z", f"{ate}T00:00:00Z")
    doc = relatorios.aguardar(conta, rid, intervalo=5, tentativas=120)
    linhas = relatorios.ler_tsv(relatorios.baixar_texto(conta, doc))

    desc_item = defaultdict(float)          # (asin, promotion-id) -> R$
    desc_frete = defaultdict(float)         # asin -> R$
    precos = defaultdict(Counter)           # asin -> {preço unitário: unidades}
    pedidos_item = 0
    pedidos_frete = 0
    for l in linhas:
        asin = l.get("asin") or ""
        qtd = _num(l.get("quantity-purchased") or l.get("quantity")) or 1
        preco_un = round(_num(l.get("item-price")) / qtd, 2)
        if preco_un:
            precos[asin][preco_un] += int(qtd)
        pi = _num(l.get("item-promotion-discount"))
        if pi:
            pedidos_item += 1
            desc_item[(asin, l.get("promotion-ids") or "")] += pi
        pf = _num(l.get("ship-promotion-discount"))
        if pf:
            pedidos_frete += 1
            desc_frete[asin] += pf

    multi = {a: dict(c) for a, c in precos.items() if len(c) > 1}
    return {
        "conta": conta,
        "periodo": {"de": de, "ate": ate},
        "linhas": len(linhas),
        "resumo": {
            "desconto_item": round(sum(desc_item.values()), 2),
            "desconto_frete": round(sum(desc_frete.values()), 2),
            "pedidos_com_desconto_item": pedidos_item,
            "pedidos_com_desconto_frete": pedidos_frete,
            "asins_com_mais_de_um_preco": len(multi),
        },
        "por_promocao": [{"asin": a, "promotion_id": p, "desconto": round(v, 2)}
                         for (a, p), v in sorted(desc_item.items(), key=lambda x: -x[1])],
        "frete_por_asin": [{"asin": a, "desconto": round(v, 2)}
                           for a, v in sorted(desc_frete.items(), key=lambda x: -x[1])],
        "precos_multiplos": {a: {str(p): n for p, n in sorted(c.items())} for a, c in multi.items()},
        "relatorios_sem_dados": ["cupons (GET_COUPON_PERFORMANCE_REPORT)", "promoções (GET_PROMOTION_PERFORMANCE_REPORT)"],
    }
