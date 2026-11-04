"""
marketplaces/expedicao/amazon_dba.py — Pedidos Amazon DBA (Delivery by Amazon / Easy Ship) para o monitor (somente leitura)
MZ App

DBA = pedidos MFN (enviados pelo vendedor). O campo EasyShipShipmentStatus mostra em que ponto está cada pedido:
  PendingSchedule                      etiqueta ainda NÃO gerada               -> checkout pendente
  PendingPickUp / PendingDropOff       etiqueta gerada, aguardando coleta/entrega no ponto -> checkout feito
  PickedUp / DroppedOff / AtOriginFC … já com a transportadora / no ponto de coleta -> checkout feito e no ponto
  LabelCanceled                        etiqueta cancelada (volta a pendente)

Regras da operação (definidas em 03/11/2026, conferir no Seller Central — mudam com o tempo):
  - etiqueta impressa até 11:00 e pacote no ponto de coleta até 14:00;
  - pedido comprado antes das 11:00 (horário de Brasília) tem o corte de hoje; depois das 11:00, o de amanhã.
Devolve linhas no mesmo formato de ml.linha para entrar na conta do painel do dia. Nada é escrito na Amazon.
"""

import time
from datetime import datetime, timedelta, timezone

from marketplaces.amazon import api

BRT = timezone(timedelta(hours=-3))
CONTAS = ("amazon01", "amazon02")
CORTE_ETIQUETA = (11, 0)
CORTE_PONTO = (14, 0)
DIAS_BUSCA = 5
CACHE_SEG = 60
NO_PONTO = {"PickedUp", "DroppedOff", "AtOriginFC", "AtDestinationFC", "OutForDelivery", "Delivered", "Rejected"}
PENDENTES = {None, "", "PendingSchedule", "LabelCanceled"}
SITUACAO = {"PendingSchedule": "Etiqueta não gerada", "PendingPickUp": "Etiqueta gerada, aguardando coleta",
            "PendingDropOff": "Etiqueta gerada, levar ao ponto", "PickedUp": "Coletado", "DroppedOff": "No ponto de coleta",
            "AtOriginFC": "No centro de origem", "AtDestinationFC": "No centro de destino", "OutForDelivery": "Saiu para entrega",
            "Delivered": "Entregue", "LabelCanceled": "Etiqueta cancelada", "Rejected": "Rejeitado"}
_cache = {}


def _dt(iso):
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(BRT)
    except Exception:
        return None


def corte_do_pedido(compra: datetime):
    """(meta_etiqueta, prazo_ponto) em BRT: comprado antes das 11:00 vale o dia; depois, o dia seguinte."""
    dia = compra.date()
    if (compra.hour, compra.minute) >= CORTE_ETIQUETA:
        dia += timedelta(days=1)
    mk = lambda hm: datetime(dia.year, dia.month, dia.day, hm[0], hm[1], tzinfo=BRT)  # noqa: E731
    return mk(CORTE_ETIQUETA), mk(CORTE_PONTO)


def linha(conta: str, o: dict):
    """Linha do painel para um pedido MFN; None se não for DBA aberto/ativo."""
    compra = _dt(o.get("PurchaseDate"))
    if not compra or o.get("FulfillmentChannel") != "MFN" or o.get("OrderStatus") in ("Canceled", "Pending", "Unfulfillable"):
        return None
    es = o.get("EasyShipShipmentStatus")
    if es is None and "Easy" not in str(o.get("ShipServiceLevel", "")) and "EZ" not in str(o.get("ShipServiceLevel", "")):
        return None   # MFN que não é DBA/Easy Ship: não entra no monitor
    meta, ponto = corte_do_pedido(compra)
    feito = es not in PENDENTES
    un = (o.get("NumberOfItemsUnshipped") or 0) + (o.get("NumberOfItemsShipped") or 0)
    return {"conta": conta, "canal": "DBA", "logistica": "easy_ship", "pedido": o.get("AmazonOrderId"), "pacote": None,
            "envio": o.get("AmazonOrderId"), "unidades": un or 1, "status": o.get("OrderStatus"), "substatus": es,
            "situacao": SITUACAO.get(es, es or "Etiqueta não gerada"), "liberado": True,
            "prazo_ml": ponto.isoformat(), "meta": meta.isoformat(), "itens": [],
            "checkout_auto": feito, "no_ponto": es in NO_PONTO}


def _conta(conta: str) -> list:
    desde = (datetime.now(timezone.utc) - timedelta(days=DIAS_BUSCA)).strftime("%Y-%m-%dT%H:%M:%SZ")
    params = {"MarketplaceIds": api.MARKETPLACE_BR, "FulfillmentChannels": "MFN", "MaxResultsPerPage": "100",
              "OrderStatuses": "Unshipped,PartiallyShipped,Shipped", "CreatedAfter": desde}
    linhas = []
    for _ in range(6):
        status, corpo = api.get(conta, "/orders/v0/orders", **params)
        if status != 200:
            raise api.falha(status, corpo)
        payload = corpo.get("payload") or {}
        linhas += [x for x in (linha(conta, o) for o in payload.get("Orders") or []) if x]
        prox = payload.get("NextToken")
        if not prox:
            break
        params = {"MarketplaceIds": api.MARKETPLACE_BR, "NextToken": prox}
        time.sleep(2)
    return linhas


def linhas(contas=CONTAS) -> tuple:
    """(linhas, erros por conta). Cache de 60 s: a cota de pedidos da Amazon é baixa."""
    saida, erros = [], {}
    for c in contas:
        agora = time.time()
        if c in _cache and agora - _cache[c][0] < CACHE_SEG:
            saida += _cache[c][1]
            continue
        try:
            if not api.autenticado(c):
                raise RuntimeError("sem credenciais completas")
            ls = _conta(c)
            _cache[c] = (agora, ls)
            saida += ls
        except Exception as e:
            erros[c] = f"Amazon: {type(e).__name__}: {api.mascarar(str(e))[:100]}"
    return saida, erros
