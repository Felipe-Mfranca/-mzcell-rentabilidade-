"""
marketplaces/amazon/envio_fba.py — Criação de envio FBA (Fulfillment Inbound v2024-03-20)
MZ App

ESCREVE na Amazon. Tudo simula por padrão: só cria, agenda ou cancela com --confirmar.
Fluxo validado em 31/10/2026 na amazon01 (FBA19VTQKGBW): plano → embalagem → destino → transporte.

  simular   valida SKU, quantidades e origem; mostra o que seria enviado. Não escreve nada.
  criar     cria o plano, define as caixas, confirma o destino e lista as datas de coleta. NÃO agenda.
  agendar   escolhe a coleta (--data AAAA-MM-DD ou --mais-cedo) e confirma o transporte.
  cancelar  cancela um plano que ainda não foi enviado.
  status    mostra plano, envio e coleta.

Uso (venv):
  python -m marketplaces.amazon.envio_fba simular amazon01 --sku KIT2MIC-FBA --caixas 3 --por-caixa 30 --peso 19 --dim 53x33x33
  python -m marketplaces.amazon.envio_fba criar   amazon01 --sku KIT2MIC-FBA --caixas 3 --por-caixa 30 --peso 19 --dim 53x33x33 --confirmar
  python -m marketplaces.amazon.envio_fba agendar amazon01 --plano wf... --mais-cedo --confirmar
  python -m marketplaces.amazon.envio_fba cancelar amazon01 --plano wf... --confirmar

Regras: um SKU por execução; endereço de origem e contato em amazon_fba_origem.json (gitignored, modelo
amazon_fba_origem.exemplo.json); a coleta só cai em dia útil (sem fim de semana nem feriado nacional).
"""

import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timedelta

from marketplaces.amazon import api

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ORIGEM_PATH = os.path.join(RAIZ, "amazon_fba_origem.json")
BASE = "/inbound/fba/2024-03-20"
FERIADOS = {  # nacionais; conferir no começo de cada ano
    date(2026, 1, 1), date(2026, 4, 3), date(2026, 4, 21), date(2026, 5, 1), date(2026, 6, 4), date(2026, 9, 7),
    date(2026, 10, 12), date(2026, 11, 2), date(2026, 11, 15), date(2026, 11, 20), date(2026, 12, 25),
}


class ErroEnvio(Exception):
    pass


# ─── Regras sem rede (testáveis) ─────────────────────────────────────────────

def dia_util(d: date) -> bool:
    return d.weekday() < 5 and d not in FERIADOS


def proximo_dia_util(a_partir_de: date) -> date:
    d = a_partir_de + timedelta(days=1)
    while not dia_util(d):
        d += timedelta(days=1)
    return d


def parse_dim(texto: str) -> tuple:
    """'53x33x33' → (comprimento, largura, altura) em cm."""
    try:
        c, l, a = (float(p.replace(",", ".")) for p in texto.lower().replace("×", "x").split("x"))
    except ValueError:
        raise ErroEnvio(f"Dimensões inválidas: '{texto}'. Use CxLxA em cm, como 53x33x33.")
    if min(c, l, a) <= 0:
        raise ErroEnvio("As dimensões precisam ser maiores que zero.")
    return c, l, a


def validar(qtd, caixas, por_caixa, peso) -> int:
    """Total de unidades. Se --qtd vier, precisa bater com caixas x por-caixa."""
    if caixas < 1 or por_caixa < 1 or peso <= 0:
        raise ErroEnvio("Caixas, unidades por caixa e peso precisam ser maiores que zero.")
    total = caixas * por_caixa
    if qtd is not None and qtd != total:
        raise ErroEnvio(f"Quantidade {qtd} não bate com {caixas} caixas x {por_caixa} un = {total}.")
    return total


def corpo_plano(sku, total, origem, marketplace, nome) -> dict:
    """createInboundPlan. SKU sem preparo exige prepOwner NONE (a Amazon recusa SELLER nesses casos).
    `sku`/`total` podem ser listas de mesmo tamanho (caixa mista: vários SKUs no mesmo plano)."""
    skus, totais = ([sku], [total]) if isinstance(sku, str) else (list(sku), list(total))
    return {"destinationMarketplaces": [marketplace], "name": nome, "sourceAddress": origem["endereco"],
            "items": [{"msku": k, "quantity": t, "labelOwner": "SELLER", "prepOwner": "NONE"} for k, t in zip(skus, totais)]}


def corpo_embalagem(grupo, sku, caixas, por_caixa, peso, dim) -> dict:
    """`sku`/`por_caixa` podem ser listas de mesmo tamanho: cada caixa leva todos os SKUs (caixa mista)."""
    c, l, a = dim
    skus, qtds = ([sku], [por_caixa]) if isinstance(sku, str) else (list(sku), list(por_caixa))
    return {"packageGroupings": [{"packingGroupId": grupo, "boxes": [{
        "contentInformationSource": "BOX_CONTENT_PROVIDED", "quantity": caixas,
        "dimensions": {"length": c, "width": l, "height": a, "unitOfMeasurement": "CM"},
        "weight": {"unit": "KG", "value": peso},
        "items": [{"labelOwner": "SELLER", "msku": k, "quantity": q, "prepOwner": "NONE"} for k, q in zip(skus, qtds)]}]}]}


def nome_padrao(skus, totais) -> str:
    """Nome do plano; a Amazon aceita no máximo 40 caracteres."""
    nome = " + ".join(f"{k} {t}un" for k, t in zip(skus, totais)) + " - MZ"
    if len(nome) > 40:
        nome = f"MZ {len(skus)} SKUs {sum(totais)}un {date.today():%d/%m}"
    return nome[:40]


def parse_itens(texto: str) -> list:
    """'SKU1=15,SKU2=10' → [('SKU1', 15), ('SKU2', 10)]: unidades de cada SKU por caixa (caixa mista)."""
    itens = []
    for parte in texto.split(","):
        sku, _, q = parte.strip().rpartition("=")
        if not sku or not q.strip().isdigit() or int(q) < 1:
            raise ErroEnvio(f"Item inválido: '{parte}'. Use SKU=unidades_por_caixa, separados por vírgula.")
        itens.append((sku.strip(), int(q)))
    if len({k for k, _ in itens}) != len(itens):
        raise ErroEnvio("SKU repetido em --itens.")
    return itens


def inicio_coleta(opcao) -> str:
    return ((opcao.get("carrierAppointment") or {}).get("startTime")) or ""


def escolher_opcao(opcoes: list, data: date = None, mais_cedo: bool = False):
    """Só transportadora parceira da Amazon com agenda, em dia útil. Data exata ou a mais cedo."""
    cand = []
    for o in opcoes:
        ini = inicio_coleta(o)
        if o.get("shippingSolution") != "AMAZON_PARTNERED_CARRIER" or not ini:
            continue
        d = date.fromisoformat(ini[:10])
        if dia_util(d):
            cand.append((ini, d, o))
    cand.sort(key=lambda t: t[0])
    if not cand:
        return None
    if data:
        return next((o for _, d, o in cand if d == data), None)
    return cand[0][2] if mais_cedo else None


# ─── Origem ──────────────────────────────────────────────────────────────────

def ler_origem(conta: str) -> dict:
    try:
        with open(ORIGEM_PATH, encoding="utf-8") as f:
            o = json.load(f).get(conta)
    except (OSError, ValueError):
        o = None
    if not o or not o.get("endereco") or not o.get("contato"):
        raise ErroEnvio(f"Sem origem para {conta}. Crie amazon_fba_origem.json (modelo: amazon_fba_origem.exemplo.json).")
    return o


# ─── Chamadas à Amazon ───────────────────────────────────────────────────────

def _ok(status, corpo, onde):
    if status not in (200, 202):
        raise ErroEnvio(f"{onde}: Amazon respondeu {status}: {api.mascarar(json.dumps(corpo, ensure_ascii=False))[:400]}")
    return corpo


def _op(conta, resposta, onde, espera=4, tentativas=30):
    """Espera a operação assíncrona terminar. Avisos (WARNING) são devolvidos; erro aborta."""
    op = resposta["operationId"]
    for _ in range(tentativas):
        s, r = api.get(conta, f"{BASE}/operations/{op}")
        _ok(s, r, onde)
        if r.get("operationStatus") != "IN_PROGRESS":
            if r.get("operationStatus") != "SUCCESS":
                raise ErroEnvio(f"{onde} falhou: {json.dumps(r.get('operationProblems'), ensure_ascii=False)[:400]}")
            return r.get("operationProblems") or []
        time.sleep(espera)
    raise ErroEnvio(f"{onde}: a Amazon não terminou a tempo (operação {op}).")


def _sku_do_inventario(conta, sku):
    for i in api.estoque_fba(conta):
        if sku in (i.get("skus") or [i.get("sku")]):
            return i
    raise ErroEnvio(f"SKU {sku} não está no inventário FBA de {conta}.")


def _opcoes_transporte(conta, plano, placement, envio):
    todas, token = [], None
    while True:
        kw = {"placementOptionId": placement, "shipmentId": envio}
        if token:
            kw["paginationToken"] = token
        s, r = api.get(conta, f"{BASE}/inboundPlans/{plano}/transportationOptions", **kw)
        _ok(s, r, "transportationOptions")
        todas += r.get("transportationOptions", [])
        token = (r.get("pagination") or {}).get("nextToken")
        if not token:
            return todas


# ─── Comandos ────────────────────────────────────────────────────────────────

def _itens(a) -> list:
    """[(sku, unidades_por_caixa)]: um SKU (--sku/--por-caixa) ou caixa mista (--itens)."""
    if getattr(a, "itens", None):
        if a.sku or a.qtd:
            raise ErroEnvio("Use --itens sozinho (sem --sku/--qtd).")
        return parse_itens(a.itens)
    if not a.sku or not a.por_caixa:
        raise ErroEnvio("Informe --sku e --por-caixa, ou --itens SKU=un,SKU2=un.")
    return [(a.sku, a.por_caixa)]


def _parametros(a):
    itens = _itens(a)
    totais = [validar(a.qtd if len(itens) == 1 else None, a.caixas, q, a.peso) for _, q in itens]
    return itens, totais, parse_dim(a.dim)


def cmd_simular(a):
    itens, totais, dim = _parametros(a)
    origem = ler_origem(a.conta)
    print(f"SIMULAÇÃO (nada foi enviado) — {a.conta}")
    for (sku, q), total in zip(itens, totais):
        inv = _sku_do_inventario(a.conta, sku)
        print(f"  Produto: {sku} · ASIN {inv.get('asin')} · FNSKU {inv.get('fnsku')} · {total} un ({q} por caixa)")
        print(f"    Etiqueta do produto: sua (FNSKU {inv.get('fnsku')}) · preparo: nenhum")
    print(f"  {a.caixas} caixa(s){' MISTAS' if len(itens) > 1 else ''} · {a.peso} kg · {dim[0]:g}x{dim[1]:g}x{dim[2]:g} cm cada")
    e = origem["endereco"]
    print(f"  Origem: {e.get('addressLine1')} {e.get('addressLine2', '')}, {e.get('city')} ({e.get('postalCode')})")
    print(f"  Primeiro dia útil possível para coleta: {proximo_dia_util(date.today()):%d/%m/%Y}")
    print("  Para criar o plano: repita o comando trocando 'simular' por 'criar' e adicionando --confirmar.")


def cmd_criar(a):
    itens, totais, dim = _parametros(a)
    skus = [k for k, _ in itens]
    if not a.confirmar:
        print("Faltou --confirmar. Mostrando só a simulação:\n")
        return cmd_simular(a)
    origem = ler_origem(a.conta)
    cfg = api.conta_cfg(a.conta)
    mkt = cfg.get("marketplace_id") or "A2Q3Y263D00KWC"
    for k in skus:
        _sku_do_inventario(a.conta, k)
    nome = a.nome or nome_padrao(skus, totais)
    s, r = api.post(a.conta, f"{BASE}/inboundPlans", corpo_plano(skus, totais, origem, mkt, nome))
    _ok(s, r, "createInboundPlan"); plano = r["inboundPlanId"]
    _op(a.conta, r, "createInboundPlan")
    print(f"Plano criado: {plano}")
    P = f"{BASE}/inboundPlans/{plano}"
    s, r = api.post(a.conta, P + "/packingOptions", {}); _ok(s, r, "generatePackingOptions")
    _op(a.conta, r, "generatePackingOptions")
    s, r = api.get(a.conta, P + "/packingOptions"); _ok(s, r, "packingOptions")
    po = r["packingOptions"][0]
    if len(po["packingGroups"]) != 1:
        raise ErroEnvio(f"A Amazon dividiu o plano {plano} em {len(po['packingGroups'])} grupos de embalagem (caixa mista não suportada "
                        f"assim). Nada foi confirmado; cancele com: cancelar {a.conta} --plano {plano} --confirmar")
    s, r = api.post(a.conta, f"{P}/packingOptions/{po['packingOptionId']}/confirmation", {}); _ok(s, r, "confirmPackingOption")
    _op(a.conta, r, "confirmPackingOption")
    s, r = api.post(a.conta, P + "/packingInformation", corpo_embalagem(po["packingGroups"][0], skus, a.caixas, [q for _, q in itens], a.peso, dim))
    _ok(s, r, "setPackingInformation")
    for p in _op(a.conta, r, "setPackingInformation"):
        print(f"  aviso da Amazon: {p.get('message')}")
    s, r = api.post(a.conta, P + "/placementOptions", {}); _ok(s, r, "generatePlacementOptions")
    _op(a.conta, r, "generatePlacementOptions")
    s, r = api.get(a.conta, P + "/placementOptions"); _ok(s, r, "placementOptions")
    pl = r["placementOptions"][0]
    if len(pl["shipmentIds"]) != 1:
        raise ErroEnvio(f"A Amazon dividiu o plano {plano} em {len(pl['shipmentIds'])} envios (destinos). Nada foi confirmado; "
                        f"cancele com: cancelar {a.conta} --plano {plano} --confirmar")
    taxa = sum((f.get("value") or {}).get("amount", 0) for f in pl.get("fees", []))
    s, r = api.post(a.conta, f"{P}/placementOptions/{pl['placementOptionId']}/confirmation", {}); _ok(s, r, "confirmPlacementOption")
    _op(a.conta, r, "confirmPlacementOption")
    envio = pl["shipmentIds"][0]
    s, r = api.get(a.conta, f"{P}/shipments/{envio}"); _ok(s, r, "shipment")
    destino = (r.get("destination") or {}).get("warehouseId")
    pronto = proximo_dia_util(date.today()) if not a.pronto_em else date.fromisoformat(a.pronto_em)
    c = origem["contato"]
    s, r = api.post(a.conta, P + "/transportationOptions", {"placementOptionId": pl["placementOptionId"], "shipmentTransportationConfigurations": [
        {"shipmentId": envio, "readyToShipWindow": {"start": f"{pronto}T11:00:00Z"}, "contactInformation": c}]})
    _ok(s, r, "generateTransportationOptions"); _op(a.conta, r, "generateTransportationOptions")
    opcoes = _opcoes_transporte(a.conta, plano, pl["placementOptionId"], envio)
    estado = {"conta": a.conta, "plano": plano, "placement": pl["placementOptionId"], "envio": envio,
              "confirmacao": r_get(a.conta, P, envio), "sku": " + ".join(skus), "total": sum(totais), "destino": destino}
    _salvar_estado(estado)
    print(f"Destino: {destino} · taxa de alocação R$ {taxa:.2f} · envio {estado['confirmacao']}")
    print("Coletas disponíveis (transportadora parceira, dias úteis):")
    for o in sorted((o for o in opcoes if inicio_coleta(o)), key=inicio_coleta):
        d = date.fromisoformat(inicio_coleta(o)[:10])
        if o.get("shippingSolution") == "AMAZON_PARTNERED_CARRIER" and dia_util(d):
            custo = ((o.get("quote") or {}).get("cost") or {}).get("amount")
            print(f"  {d:%d/%m/%Y} ({['seg','ter','qua','qui','sex','sáb','dom'][d.weekday()]}) · {o['carrier'].get('name')} · R$ {custo}")
    print(f"\nNADA FOI AGENDADO. Para agendar: agendar {a.conta} --plano {plano} --mais-cedo --confirmar  (ou --data AAAA-MM-DD)")


def r_get(conta, P, envio):
    s, r = api.get(conta, f"{P}/shipments/{envio}")
    return (r or {}).get("shipmentConfirmationId") if s == 200 else None


def _salvar_estado(est):
    caminho = os.path.join(RAIZ, "amazon_fba_planos.json")
    try:
        with open(caminho, encoding="utf-8") as f:
            dados = json.load(f)
    except (OSError, ValueError):
        dados = {}
    dados[est["plano"]] = dict(est, criado_em=datetime.now().isoformat(timespec="seconds"))
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=1)


def _estado(plano):
    try:
        with open(os.path.join(RAIZ, "amazon_fba_planos.json"), encoding="utf-8") as f:
            return json.load(f).get(plano)
    except (OSError, ValueError):
        return None


def cmd_agendar(a):
    est = _estado(a.plano)
    if not est:
        raise ErroEnvio(f"Plano {a.plano} não foi criado por este comando (rode 'criar' antes).")
    if not a.data and not a.mais_cedo:
        raise ErroEnvio("Diga a coleta: --mais-cedo ou --data AAAA-MM-DD.")
    P = f"{BASE}/inboundPlans/{a.plano}"
    opcoes = _opcoes_transporte(a.conta, a.plano, est["placement"], est["envio"])
    escolhida = escolher_opcao(opcoes, date.fromisoformat(a.data) if a.data else None, a.mais_cedo)
    if not escolhida:
        raise ErroEnvio("Não há coleta com essa data entre as opções (dia útil, transportadora parceira).")
    d = date.fromisoformat(inicio_coleta(escolhida)[:10])
    custo = ((escolhida.get("quote") or {}).get("cost") or {}).get("amount")
    print(f"Coleta escolhida: {d:%d/%m/%Y} · {escolhida['carrier'].get('name')} · R$ {custo}")
    if not a.confirmar:
        print("SIMULAÇÃO: nada foi agendado. Repita com --confirmar.")
        return
    origem = ler_origem(a.conta)
    s, r = api.post(a.conta, P + "/transportationOptions/confirmation", {"transportationSelections": [
        {"shipmentId": est["envio"], "transportationOptionId": escolhida["transportationOptionId"], "contactInformation": origem["contato"]}]})
    _ok(s, r, "confirmTransportationOptions"); _op(a.conta, r, "confirmTransportationOptions")
    print(f"Coleta agendada para {d:%d/%m/%Y}. Envio {est.get('confirmacao')}. Falta imprimir e colar as etiquetas.")


def cmd_cancelar(a):
    if not a.confirmar:
        print(f"SIMULAÇÃO: cancelaria o plano {a.plano} em {a.conta}. Repita com --confirmar.")
        return
    s, r = api._enviar(a.conta, "PUT", f"{BASE}/inboundPlans/{a.plano}/cancellation")
    _ok(s, r, "cancelInboundPlan"); _op(a.conta, r, "cancelInboundPlan")
    print(f"Plano {a.plano} cancelado.")


def cmd_status(a):
    s, r = api.get(a.conta, f"{BASE}/inboundPlans/{a.plano}"); _ok(s, r, "inboundPlan")
    print(f"Plano {a.plano}: {r.get('status')} · criado {r.get('createdAt')}")
    for sh in r.get("shipments", []):
        s2, d = api.get(a.conta, f"{BASE}/inboundPlans/{a.plano}/shipments/{sh['shipmentId']}")
        if s2 == 200:
            print(f"  Envio {d.get('shipmentConfirmationId')} · {d.get('status')} · destino {(d.get('destination') or {}).get('warehouseId')} · "
                  f"coleta {((d.get('dates') or {}).get('readyToShipWindow') or {}).get('start')}")


NF_BASE = "/tax/invoices/2024-06-19/governmentInvoiceRequests"
NF_TIPO, NF_TRANSACAO = "REMITTANCE", "INBOUND_SHIPMENT"   # valores do exemplo oficial da Amazon (Invoices API, Brasil)


def _nf_status(conta, est):
    """Status da NF do envio. 404 = ainda não existe NF; 403 = o app não tem acesso a essa API."""
    s, r = api.get(conta, NF_BASE, marketplaceId=api.MARKETPLACE_BR, transactionType=NF_TRANSACAO, shipmentId=est["envio"],
                   invoiceType=NF_TIPO, inboundPlanId=est["plano"])
    if s == 404:
        return {"status": "NAO_CRIADA"}
    if s == 403:
        raise ErroEnvio("A Amazon negou acesso à API de NF (403): falta a permissão no app. Gere a NF pelo Seller Central.")
    if s != 200:
        raise api.falha(s, r)
    return r


def cmd_nf(a):
    """NF-e de remessa do envio (Invoices API do Brasil). Sem --confirmar só mostra o status; com --confirmar PEDE A EMISSÃO
    da NF à Amazon (é o mesmo que o botão 'Confirmar e gerar nota fiscal' do Seller Central: não use os dois)."""
    est = _estado(a.plano)
    if not est:
        raise ErroEnvio(f"Plano {a.plano} não foi criado por este comando (rode 'criar' antes).")
    atual = _nf_status(a.conta, est)
    print(f"Envio {est.get('confirmacao')} · NF: {atual.get('status')}"
          + (f" · documento {atual['invoiceExternalDocumentId']}" if atual.get("invoiceExternalDocumentId") else ""))
    for e in atual.get("invoiceErrors") or []:
        print(f"  erro da NF {e.get('errorCode')}: {e.get('description')}")
    if atual.get("status") in ("PROCESSING", "SUCCESS"):
        return
    if not a.confirmar:
        print("SIMULAÇÃO: nenhuma NF foi pedida. Repita com --confirmar para pedir a emissão à Amazon "
              "(se você já clicou em 'Confirmar e gerar nota fiscal' no Seller Central, não repita aqui).")
        return
    s, r = api.post(a.conta, NF_BASE, {"inboundPlanId": est["plano"], "shipmentId": est["envio"], "invoiceType": NF_TIPO,
                                       "transactionType": NF_TRANSACAO, "marketplaceId": api.MARKETPLACE_BR})
    if s not in (200, 202, 204):
        raise api.falha(s, r)
    print("Pedido de emissão enviado. Aguardando a Amazon (até 10 min)...")
    fim = time.time() + 600
    while time.time() < fim:
        time.sleep(15)
        atual = _nf_status(a.conta, est)
        print(f"  {datetime.now():%H:%M:%S} · {atual.get('status')}")
        if atual.get("status") in ("SUCCESS", "ERROR"):
            break
    if atual.get("status") == "SUCCESS":
        print(f"NF emitida. Documento fiscal: {atual.get('invoiceExternalDocumentId')}. Próximo: reservar o agendamento e imprimir as etiquetas.")
    else:
        for e in atual.get("invoiceErrors") or []:
            print(f"  erro da NF {e.get('errorCode')}: {e.get('description')}")
        raise ErroEnvio(f"A NF não foi emitida (status {atual.get('status')}).")


def main(argv=None):
    p = argparse.ArgumentParser(description="Envio FBA (simula por padrão; escreve só com --confirmar)")
    sub = p.add_subparsers(dest="cmd", required=True)
    for nome, fn in (("simular", cmd_simular), ("criar", cmd_criar), ("agendar", cmd_agendar), ("cancelar", cmd_cancelar), ("status", cmd_status), ("nf", cmd_nf)):
        s = sub.add_parser(nome); s.set_defaults(fn=fn); s.add_argument("conta")
        if nome in ("simular", "criar"):
            s.add_argument("--sku"); s.add_argument("--qtd", type=int)
            s.add_argument("--caixas", type=int, required=True); s.add_argument("--por-caixa", type=int, dest="por_caixa")
            s.add_argument("--itens", help="caixa mista: SKU=un_por_caixa,SKU2=un_por_caixa (no lugar de --sku/--por-caixa)")
            s.add_argument("--peso", type=float, required=True, help="kg por caixa"); s.add_argument("--dim", required=True, help="CxLxA em cm")
            s.add_argument("--nome"); s.add_argument("--pronto-em", dest="pronto_em", help="AAAA-MM-DD (padrão: próximo dia útil)")
        else:
            s.add_argument("--plano", required=True)
        if nome == "agendar":
            s.add_argument("--data"); s.add_argument("--mais-cedo", action="store_true", dest="mais_cedo")
        if nome in ("criar", "agendar", "cancelar", "nf"):
            s.add_argument("--confirmar", action="store_true")
    a = p.parse_args(argv)
    a.__dict__.setdefault("confirmar", False)
    try:
        a.fn(a)
    except (ErroEnvio, api.ErroAmazon) as e:
        print(f"ERRO: {api.mascarar(str(e))}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
