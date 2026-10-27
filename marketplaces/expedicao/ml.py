"""
marketplaces/expedicao/ml.py — Expedição do galpão, Mercado Livre (somente leitura)
MZ App

Lista os envios do ML que saem do galpão (fora do Full) com o prazo de cada um:
  Flex   (self_service)           meta interna 12h do dia do prazo; o ML aceita até o expected_date
  Coleta (cross_docking, meli01)  preparo termina 30 min antes do expected_date
  Agência(xd_drop_off, meli02/03) preparo termina 30 min antes do expected_date
O prazo (expected_date) vem de GET /shipments/{id}/sla. Nada é escrito no ML.
"""

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import ml_api
from reposicao_full import ML

BRT = timezone(timedelta(hours=-3))
CANAIS = {"self_service": "Flex", "cross_docking": "Coleta", "xd_drop_off": "Agência", "drop_off": "Agência"}
STATUS_ABERTOS = ("ready_to_ship", "pending")
PREPARO_ANTES_MIN = 30
META_FLEX_HORA = 12
DIAS_BUSCA = 6
PAGINAS = 40          # 50 pedidos por página: até 2000 no período (com 20 perdia os mais antigos, ex.: 1422 pedidos em 6 dias)
CONTAS = ["meli01", "meli02", "meli03"]
_cache = {}
CACHE_SEG = 90


def _dt(iso):
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(BRT)
    except Exception:
        return None


def prazos(canal: str, expected_date: str):
    """(prazo_ml, meta_interna) em ISO BRT. meta_interna é onde o aviso de contagem se baseia."""
    ml = _dt(expected_date)
    if not ml:
        return None, None
    if canal == "Flex":
        meta = min(ml, ml.replace(hour=META_FLEX_HORA, minute=0, second=0, microsecond=0))
    else:
        meta = ml - timedelta(minutes=PREPARO_ANTES_MIN)
    return ml.isoformat(), meta.isoformat()


# Situação do envio em português. Ordem do fluxo: pendente -> nota fiscal -> etiqueta -> impresso (em separação)
# -> postado na agência (in_hub) -> despachado -> entregue. Pares não listados caem no status genérico.
STATUS_PT = {"pending": "Pendente", "handling": "Em preparação", "ready_to_ship": "Pronto para despacho",
             "shipped": "Despachado", "delivered": "Entregue", "not_delivered": "Não entregue", "cancelled": "Cancelado"}
SUBSTATUS_PT = {
    ("pending", "buffered"): "Aguardando liberação",
    ("ready_to_ship", "invoice_pending"): "Aguardando nota fiscal",
    ("ready_to_ship", "ready_to_print"): "Etiqueta pronta para impressão",
    ("ready_to_ship", "printed"): "Em separação",
    ("ready_to_ship", "packed"): "Embalado",
    ("ready_to_ship", "in_packing_list"): "Na lista de postagem",
    ("ready_to_ship", "in_hub"): "Postado na agência",
    ("shipped", "out_for_delivery"): "Saiu para entrega",
    ("shipped", "soon_deliver"): "Entrega em breve",
    ("shipped", "at_the_door"): "Na porta",
    ("shipped", "waiting_for_withdrawal"): "Aguardando retirada",
    ("cancelled", "pack_splitted"): "Cancelado (pacote dividido)",
    ("cancelled", "fraudulent"): "Cancelado (fraude)",
    ("cancelled", "unfulfillable"): "Cancelado (não atendível)",
}


def situacao(status: str, sub: str) -> str:
    if (status, sub) in SUBSTATUS_PT:
        return SUBSTATUS_PT[(status, sub)]
    base = STATUS_PT.get(status, status or "-")
    return f"{base} ({sub})" if sub else base


def linha(conta: str, pedido: dict, envio: dict, sla: dict):
    """Monta a linha do mapa; None se o envio não é do galpão ou já saiu."""
    canal = CANAIS.get(envio.get("logistic_type"))
    if not canal or envio.get("status") not in STATUS_ABERTOS:
        return None
    sub = envio.get("substatus")
    prazo_ml, meta = prazos(canal, (sla or {}).get("expected_date"))
    itens = []
    for it in pedido.get("order_items") or []:
        i = it.get("item") or {}
        itens.append({"mlb": i.get("id"), "titulo": i.get("title"), "sku": i.get("seller_sku") or i.get("seller_custom_field"),
                      "qtd": it.get("quantity")})
    return {"conta": conta, "canal": canal, "logistica": envio.get("logistic_type"), "pedido": pedido.get("id"), "envio": envio.get("id"),
            "unidades": sum(i["qtd"] or 0 for i in itens), "status": envio.get("status"), "substatus": sub,
            "situacao": situacao(envio.get("status"), sub),
            "liberado": envio.get("status") == "ready_to_ship",   # pending/buffered = ainda não liberado
            "sla_status": (sla or {}).get("status"), "prazo_ml": prazo_ml, "meta": meta, "itens": itens}


def _conta(conta: str) -> list:
    ml = ML(conta)
    sid = ml_api.get_seller_id(conta)
    de = (datetime.now(BRT) - timedelta(days=DIAS_BUSCA)).strftime("%Y-%m-%dT00:00:00.000-03:00")
    pedidos = []
    for pg in range(PAGINAS):
        st, b = ml.get("/orders/search", seller=sid, sort="date_desc", limit=50, offset=pg * 50,
                       **{"order.status": "paid", "order.date_created.from": de})
        if st != 200:
            raise RuntimeError(f"{conta}: pedidos HTTP {st}")
        res = b.get("results") or []
        pedidos += [o for o in res if (o.get("shipping") or {}).get("id")]
        if len(res) < 50:
            break

    def um(o):
        st, envio = ml.get(f"/shipments/{o['shipping']['id']}")
        if st != 200 or CANAIS.get(envio.get("logistic_type")) is None or envio.get("status") not in STATUS_ABERTOS:
            return None
        _, sla = ml.get(f"/shipments/{envio['id']}/sla")
        return linha(conta, o, envio, sla)

    with ThreadPoolExecutor(max_workers=6) as ex:
        return [x for x in ex.map(um, pedidos) if x]


def mapa(contas=None, forcar=False) -> dict:
    contas = contas or CONTAS
    chave = tuple(contas)
    agora = time.time()
    if not forcar and chave in _cache and agora - _cache[chave][0] < CACHE_SEG:
        return _cache[chave][1]
    linhas, erros = [], {}
    for c in contas:
        try:
            linhas += _conta(c)
        except Exception as e:
            erros[c] = f"{type(e).__name__}: {str(e)[:100]}"
    linhas.sort(key=lambda x: (x["meta"] or "9999", x["conta"]))
    res = {"gerado_em": datetime.now(BRT).isoformat(timespec="seconds"), "linhas": linhas, "erros": erros,
           "regras": {"flex_meta": f"{META_FLEX_HORA}h", "preparo_antes_min": PREPARO_ANTES_MIN,
                      "canais": {"meli01": "Coleta", "meli02": "Agência", "meli03": "Agência"}}}
    if not erros:
        _cache[chave] = (agora, res)
    return res
