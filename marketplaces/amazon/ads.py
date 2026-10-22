"""
marketplaces/amazon/ads.py — Investimento em Ads por ASIN (Amazon, somente leitura)
MZCell Rentabilidade

Usa o Data Kiosk (analytics_economics_2024_03_15), que não exige a Advertising API. Entrega só o
gasto de Sponsored Products por ASIN (adTypeName SponsoredProductFee). Não traz impressões, cliques
nem venda atribuída ao anúncio: por isso ACOS e ROAS ficam indisponíveis e só TACOS e investimento são
calculados. Consulta assíncrona: cria, espera DONE, baixa o documento (JSON lines, pode vir gzip).
"""

import gzip
import json
import time
import urllib.request

from marketplaces.amazon import api

MARKETPLACE_BR = api.MARKETPLACE_BR
TENTATIVAS = 120          # 3 s entre tentativas: até 6 minutos
TIPO_SPONSORED_PRODUCTS = "SponsoredProductFee"


def _query(de: str, ate: str) -> str:
    return """query {
  analytics_economics_2024_03_15 {
    economics(
      startDate: "%s", endDate: "%s",
      marketplaceIds: ["%s"],
      aggregateBy: {date: RANGE, productId: CHILD_ASIN}
    ) {
      startDate endDate childAsin
      ads { adTypeName charge { totalAmount { amount currencyCode } } }
    }
  }
}""" % (de, ate, MARKETPLACE_BR)


def ads_por_asin(conta: str, de: str, ate: str) -> dict:
    """{ASIN: investimento em R$ no período}. Erro da Amazon vira ErroAmazon; nunca devolve parcial."""
    st, b = api.post(conta, "/dataKiosk/2023-11-15/queries", {"query": _query(de, ate)})
    if st not in (200, 201, 202):
        raise api.falha(st, b)
    qid = b["queryId"]
    status, s = None, {}
    for _ in range(TENTATIVAS):
        st, s = api.get(conta, f"/dataKiosk/2023-11-15/queries/{qid}")
        if st != 200:
            raise api.falha(st, s)
        status = s.get("processingStatus")
        if status == "DONE":
            break
        if status in ("FATAL", "CANCELLED"):
            raise api.ErroAmazon(f"consulta de Ads falhou na Amazon ({status})")
        time.sleep(3)
    if status != "DONE":
        raise api.ErroAmazon("a Amazon demorou para gerar o relatório de Ads; tente de novo em instantes")
    st, d = api.get(conta, f"/dataKiosk/2023-11-15/documents/{s['dataDocumentId']}")
    if st != 200:
        raise api.falha(st, d)
    bruto = urllib.request.urlopen(d["documentUrl"], timeout=60).read()
    if bruto[:2] == b"\x1f\x8b":
        bruto = gzip.decompress(bruto)
    por_asin: dict = {}
    for linha in bruto.decode("utf-8").splitlines():
        if not linha.strip():
            continue
        r = json.loads(linha)
        total = 0.0
        for a in r.get("ads") or []:
            if a.get("adTypeName") == TIPO_SPONSORED_PRODUCTS:
                total += float(((a.get("charge") or {}).get("totalAmount") or {}).get("amount") or 0)
        asin = r.get("childAsin")
        if asin:
            por_asin[asin] = round(por_asin.get(asin, 0.0) + total, 2)
    return por_asin
