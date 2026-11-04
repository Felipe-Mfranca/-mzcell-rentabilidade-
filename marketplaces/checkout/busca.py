"""
marketplaces/checkout/busca.py — QR da etiqueta -> envio no ML -> pedido no Bling (somente leitura)
MZ App

O número da etiqueta (ID do envio) não existe no pedido do Bling. A ponte é o ML:
  envio -> order_id (e pack_id) -> numeroLoja do Bling.
Nada é escrito no ML nem no Bling.
"""

from marketplaces.checkout import leitura

PARES = {"meli01": "bling01", "meli02": "bling02", "meli03": "bling03"}
CANAIS = {"self_service": "Flex", "cross_docking": "Coleta", "xd_drop_off": "Agência", "drop_off": "Agência",
          "fulfillment": "Full", "default": "Correios/Places"}
SITUACOES = {6: "Em aberto", 9: "Atendido", 12: "Cancelado", 15: "Em andamento", 21: "Em digitação",
             24: "Verificado", 753338: "Pagamento aprovado", 753339: "Em devolução"}
SIT_VERIFICADO, SIT_CANCELADO, SIT_ATENDIDO, SIT_DEVOLUCAO = 24, 12, 9, 753339


def _padrao_ml(conta):
    from reposicao_full import ML
    return ML(conta)


def _padrao_seller(conta):
    import ml_api
    return str(ml_api.get_seller_id(conta))


def _padrao_pedidos_bling(conta_bling):
    from marketplaces.expedicao import bling_checkout
    return bling_checkout._pedidos(conta_bling)


def _padrao_detalhe_bling(conta_bling, pedido_id):
    from marketplaces.bling import api as bling
    st, r = bling.get(conta_bling, f"/pedidos/vendas/{pedido_id}")
    return (r or {}).get("data") if st == 200 else None


def _achar_no_ml(lei, ml, seller):
    """Procura o envio (ou pedido) nas contas do ML. Devolve (conta, envio, pedido) ou (None, None, None)."""
    contas = list(PARES)
    if lei.get("sender_id"):
        donas = []
        for c in contas:
            try:
                if seller(c) == str(lei["sender_id"]):
                    donas.append(c)
            except Exception:
                pass
        contas = donas or contas
    for c in contas:
        api = ml(c)
        if lei["tipo"] == "envio":
            st, envio = api.get(f"/shipments/{lei['numero']}")
            if st != 200 or not isinstance(envio, dict):
                continue
            st2, pedido = api.get(f"/orders/{envio.get('order_id')}")
            return c, envio, pedido if st2 == 200 else {}
        st, pedido = api.get(f"/orders/{lei['numero']}")
        if st != 200 or not isinstance(pedido, dict):
            continue
        sid = (pedido.get("shipping") or {}).get("id")
        st2, envio = api.get(f"/shipments/{sid}") if sid else (0, {})
        return c, envio if st2 == 200 else {}, pedido
    return None, None, None


def _itens_ml(pedido):
    out = []
    for i in (pedido or {}).get("order_items") or []:
        it = i.get("item") or {}
        out.append({"titulo": it.get("title"), "sku": it.get("seller_sku") or it.get("seller_custom_field"),
                    "quantidade": i.get("quantity")})
    return out


def _veredito(envio, sit_bling, achou_bling, bling_erro):
    """(nivel, mensagem): ok = pode seguir; aviso = olhar antes; bloqueio = não despachar."""
    if (envio or {}).get("status") == "cancelled":
        return "bloqueio", "Envio CANCELADO no Mercado Livre. Não despachar."
    if (envio or {}).get("logistic_type") == "fulfillment":
        return "aviso", "Envio do Full: não sai do galpão."
    if bling_erro:
        return "aviso", f"Não consegui consultar o Bling ({bling_erro}). Confira o pedido à mão."
    if not achou_bling:
        return "aviso", "Pedido NÃO encontrado no Bling (últimos 8 dias). Pode faltar lançar o pedido."
    if sit_bling == SIT_CANCELADO:
        return "bloqueio", "Pedido CANCELADO no Bling. Não despachar."
    if sit_bling == SIT_DEVOLUCAO:
        return "bloqueio", "Pedido em devolução no Bling."
    if sit_bling == SIT_VERIFICADO:
        return "aviso", "Checkout já feito (Verificado). Etiqueta lida de novo?"
    if sit_bling == SIT_ATENDIDO:
        return "ok", "Pedido atendido (NF emitida). Pode despachar."
    return "ok", "Pedido encontrado no Bling. Pode seguir o checkout."


def buscar(codigo, ml=_padrao_ml, seller=_padrao_seller, pedidos_bling=_padrao_pedidos_bling,
           detalhe_bling=_padrao_detalhe_bling) -> dict:
    lei = leitura.ler(codigo)
    if not lei:
        return {"ok": False, "nivel": "bloqueio", "mensagem": "Não entendi esse código. Bipe a etiqueta de novo.",
                "lido": str(codigo or "")[:80]}
    conta, envio, pedido = _achar_no_ml(lei, ml, seller)
    if not conta:
        return {"ok": False, "nivel": "bloqueio", "lido": lei["numero"],
                "mensagem": f"{lei['tipo'].capitalize()} {lei['numero']} não encontrado nas contas meli01/02/03."}
    envio, pedido = envio or {}, pedido or {}
    order_id, pack = str(pedido.get("id") or envio.get("order_id") or ""), pedido.get("pack_id")

    bling_conta, achou, erro, p, det = PARES[conta], False, None, None, None
    try:
        por_loja = pedidos_bling(bling_conta)
        p = por_loja.get(order_id) or (por_loja.get(str(pack)) if pack else None)
        achou = p is not None
        if achou:
            det = detalhe_bling(bling_conta, p["id"])
    except Exception as e:
        erro = f"{type(e).__name__}: {str(e)[:80]}"

    sit = (p or {}).get("situacao", {}).get("id") if achou else None
    vol = (((det or {}).get("transporte") or {}).get("volumes") or [{}])[0]
    rastreio_bling = (vol.get("codigoRastreamento") or "").strip()
    rastreio_ml = (envio.get("tracking_number") or "").strip()
    nivel, msg = _veredito(envio, sit, achou, erro)
    return {
        "ok": nivel != "bloqueio", "nivel": nivel, "mensagem": msg, "origem": lei["origem"],
        "conta": conta, "bling_conta": bling_conta,
        "ml": {"envio": envio.get("id") or (lei["numero"] if lei["tipo"] == "envio" else None), "pedido": order_id or None,
               "pacote": pack, "canal": CANAIS.get(envio.get("logistic_type"), envio.get("logistic_type")),
               "status": envio.get("status"), "substatus": envio.get("substatus"), "rastreio": rastreio_ml or None,
               "itens": _itens_ml(pedido)},
        "bling": ({"numero": p.get("numero"), "situacao": SITUACOES.get(sit, str(sit)), "situacao_id": sit,
                   "numeroLoja": p.get("numeroLoja"), "rastreio": rastreio_bling or None,
                   "itens": [{"titulo": i.get("descricao"), "sku": i.get("codigo"), "quantidade": i.get("quantidade")}
                             for i in (det or {}).get("itens") or []]} if achou else None),
        # True = iguais; False = diferentes; None = um dos lados ainda está vazio (normal em pedido novo)
        "rastreio_confere": (rastreio_ml == rastreio_bling) if (rastreio_ml and rastreio_bling) else None,
    }
