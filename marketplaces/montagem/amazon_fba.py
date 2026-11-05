"""
marketplaces/montagem/amazon_fba.py — Pré-carrega na fila de montagem os envios FBA da Amazon
MZ App

Somente leitura na Amazon. Um envio entra na lista de "prontos para montar" quando:
  - o plano está ativo e o envio ainda não saiu (WORKING / READY_TO_SHIP);
  - a transportadora já está escolhida e tem coleta agendada (data e hora);
  - a NF-e de remessa está emitida (Invoices API: SUCCESS).
O que ainda falta (NF ou agendamento) aparece com o motivo, mas não é importado.
O estoquista (ou o líder) só atribui os produtos e começa: a fila nasce sem responsáveis.
"""

import time
from datetime import date, datetime, timedelta, timezone

from marketplaces.amazon import api

BRT = timezone(timedelta(hours=-3))
BASE = "/inbound/fba/2024-03-20"
NF = "/tax/invoices/2024-06-19/governmentInvoiceRequests"
CONTAS = ("amazon01", "amazon02")
JANELA_DIAS = 21              # só olha planos criados nos últimos N dias
PAGINAS_MAX = 12
# Depois da NF e do transporte confirmados a Amazon já marca SHIPPED, mesmo antes da coleta: o que vale é a data da coleta.
STATUS_ABERTOS = ("WORKING", "READY_TO_SHIP", "SHIPPED", "IN_TRANSIT")
# O plano inteiro também vira SHIPPED nesse momento (visto 05/11); só VOIDED/ERRORED ficam de fora.
PLANOS_ABERTOS = ("ACTIVE", "SHIPPED")
ANTECEDENCIA_MIN = 60         # "pronto até" = 1 h antes do início da janela de coleta
CACHE_SEG = 600
_cache = {"ts": 0.0, "dados": None}


def _get(conta, caminho, **params):
    """GET com espera quando a Amazon limita (429). Devolve (status, corpo)."""
    for tent in range(5):
        s, r = api.get(conta, caminho, **params)
        if s != 429:
            return s, r
        time.sleep(8 * (tent + 1))
    return s, r


def _paginas(conta, caminho, chave, max_paginas=PAGINAS_MAX, **params):
    itens, token = [], None
    for _ in range(max_paginas):
        kw = dict(params, pageSize=20)      # a Amazon aceita no máximo 20 por página em transportationOptions
        if token:
            kw["paginationToken"] = token
        s, r = _get(conta, caminho, **kw)
        if s != 200:
            raise api.ErroAmazon(f"{caminho}: HTTP {s}", s)
        itens += r.get(chave) or []
        token = (r.get("pagination") or {}).get("nextToken")
        if not token:
            break
    return itens


def _inicio_br(iso: str):
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(BRT)
    except ValueError:
        return None


def _envio_do_plano(conta, plano_id, plano, agora):
    """Um dicionário por envio do plano, pronto ou com o motivo de ainda não estar."""
    saida = []
    for sh in plano.get("shipments") or []:
        sid = sh.get("shipmentId")
        base = {"conta": conta, "plano": plano_id, "envio": sid, "nome": plano.get("name") or "", "pronto": False, "motivo": ""}
        if sh.get("status") not in STATUS_ABERTOS:
            continue
        s, e = _get(conta, f"{BASE}/inboundPlans/{plano_id}/shipments/{sid}")
        if s != 200:
            saida.append(dict(base, motivo=f"não consegui ler o envio (HTTP {s})"))
            continue
        base["numero"] = e.get("shipmentConfirmationId") or sid
        base["destino"] = (e.get("destination") or {}).get("warehouseId")
        escolhida = e.get("selectedTransportationOptionId")
        if not escolhida:
            saida.append(dict(base, motivo="transporte ainda não confirmado"))
            continue
        kw = {"shipmentId": sid}
        if e.get("placementOptionId"):
            kw["placementOptionId"] = e["placementOptionId"]
        opcoes = _paginas(conta, f"{BASE}/inboundPlans/{plano_id}/transportationOptions", "transportationOptions", **kw)
        op = next((o for o in opcoes if o.get("transportationOptionId") == escolhida), None)
        ini = _inicio_br(((op or {}).get("carrierAppointment") or {}).get("startTime"))
        if not ini:
            saida.append(dict(base, motivo="sem coleta agendada"))
            continue
        if ini.date() < agora.date() - timedelta(days=1):
            continue                                   # coleta antiga: não é para montar agora
        base.update(coleta=ini.date().isoformat(), inicio_coleta=ini.strftime("%Y-%m-%dT%H:%M"),
                    prazo=(ini - timedelta(minutes=ANTECEDENCIA_MIN)).strftime("%Y-%m-%dT%H:%M"),
                    transportadora=((op or {}).get("carrier") or {}).get("name"))
        sn, nf = _get(conta, NF, marketplaceId=api.MARKETPLACE_BR, transactionType="INBOUND_SHIPMENT", shipmentId=sid,
                      invoiceType="REMITTANCE", inboundPlanId=plano_id)
        status_nf = "NAO_CRIADA" if sn == 404 else (nf.get("status") if sn == 200 else f"HTTP {sn}")
        base["nf"] = status_nf
        if status_nf != "SUCCESS":
            saida.append(dict(base, motivo="NF ainda não emitida" if status_nf in ("NAO_CRIADA", "PROCESSING") else f"NF com problema ({status_nf})"))
            continue
        itens = _paginas(conta, f"{BASE}/inboundPlans/{plano_id}/items", "items")
        base["produtos"] = [{"sku": i.get("msku"), "titulo": "", "unidades": int(i.get("quantity") or 0), "asin": i.get("asin")}
                            for i in itens if i.get("msku") and int(i.get("quantity") or 0) > 0]
        if not base["produtos"]:
            saida.append(dict(base, motivo="plano sem itens"))
            continue
        base["pronto"] = True
        saida.append(base)
    return saida


def buscar(contas=CONTAS, dias=JANELA_DIAS, agora=None) -> dict:
    """Lê a Amazon (lento: dezenas de chamadas). Devolve {envios, erros, gerado_em}."""
    agora = agora or datetime.now(BRT)
    corte = (agora - timedelta(days=dias)).astimezone(timezone.utc).isoformat()
    envios, erros = [], {}
    for conta in contas:
        try:
            # Sem "status" a Amazon só lista os ACTIVE: pede cada status, do mais novo ao mais antigo.
            planos = [p for st in PLANOS_ABERTOS
                      for p in _paginas(conta, f"{BASE}/inboundPlans", "inboundPlans", max_paginas=3,
                                        status=st, sortBy="CREATION_TIME", sortOrder="DESC")]
            for p in planos:
                if p.get("status") not in PLANOS_ABERTOS or (p.get("createdAt") or "") < corte[:19]:
                    continue
                s, plano = _get(conta, f"{BASE}/inboundPlans/{p['inboundPlanId']}")
                if s == 200:
                    envios += _envio_do_plano(conta, p["inboundPlanId"], plano, agora)
        except api.ErroAmazon as e:
            erros[conta] = api.mascarar(str(e))[:200]
        except Exception as e:
            erros[conta] = f"{type(e).__name__}: {api.mascarar(str(e))[:150]}"
    envios.sort(key=lambda x: (not x["pronto"], x.get("coleta") or "9999", x["conta"]))
    return {"envios": envios, "erros": erros, "gerado_em": agora.isoformat(timespec="seconds")}


def importar_prontos(dados: dict = None) -> list:
    """Coloca na fila (fim) os envios prontos que ainda não estão lá nem foram excluídos pelo líder. Devolve os números criados."""
    from marketplaces.montagem import armazenamento as arm
    dados = dados or disponiveis()
    ja = {e["chave"] for e in arm.listar()} | arm.dispensados()
    criados = []
    for x in dados["envios"]:
        if x.get("pronto") and f"amazon-{x['numero']}" not in ja:
            try:
                arm.criar(x["numero"], x["conta"], x["coleta"], x["prazo"], x["produtos"], "automático (Amazon)", "amazon")
                criados.append(x["numero"])
            except arm.ErroMontagem:
                pass
    return criados


def disponiveis(atualizar: bool = False, leitor=None) -> dict:
    """Resultado com cache de 10 min (a Amazon limita as consultas)."""
    agora = time.time()
    if not atualizar and _cache["dados"] is not None and agora - _cache["ts"] < CACHE_SEG:
        return _cache["dados"]
    dados = (leitor or buscar)()
    if not dados["erros"] or dados["envios"]:
        _cache.update(ts=time.time(), dados=dados)
    return dados
