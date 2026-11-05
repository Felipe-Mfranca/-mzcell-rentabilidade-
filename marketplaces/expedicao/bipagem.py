"""
marketplaces/expedicao/bipagem.py — Etiqueta bipada -> pedido no Bling (somente leitura)
MZ App

O leitor manda o código da etiqueta (código de barras = número do envio do ML; o QR do Flex é um JSON com "id").
Procura nos três Blings pelo numeroLoja (número do pedido ou do pacote no marketplace). Se o código for um envio do
ML, descobre o pedido pelo mapa do galpão ou, se não estiver lá, pela API do ML (/shipments/{id}) nas três contas.
"""

import json
import re

from marketplaces.expedicao import bling_checkout, ml

BLINGS = ("bling01", "bling02", "bling03")


def normalizar(codigo: str) -> str:
    t = str(codigo or "").strip()
    if t.startswith("{"):
        try:
            t = str(json.loads(t).get("id") or t)
        except ValueError:
            pass
    return re.sub(r"\s+", "", t)


def _no_bling(chaves: list) -> tuple:
    """(achado, erros): o primeiro pedido dos Blings cujo numeroLoja é uma das chaves."""
    erros = {}
    for b in BLINGS:
        try:
            por_loja = bling_checkout._pedidos(b)
        except Exception as e:
            erros[b] = f"{type(e).__name__}: {str(e)[:80]}"
            continue
        for k in chaves:
            p = por_loja.get(str(k)) if k else None
            if p:
                sit = (p.get("situacao") or {}).get("id")
                return {"bling": b, "bling_n": int(b[-1]), "numero": p.get("numero"), "numero_loja": p.get("numeroLoja"),
                        "situacao": bling_checkout.SITUACOES.get(sit, str(sit)), "loja": (p.get("loja") or {}).get("id")}, erros
    return None, erros


def _envio_ml(codigo: str) -> dict:
    """Pedido e pacote do envio do ML: primeiro no mapa do galpão (cache), depois na API das três contas."""
    for _ts, res in list(ml._cache.values()):           # só o que já está em memória: montar o mapa leva minutos
        for l in res.get("linhas", []):
            if str(l.get("envio")) == codigo:
                return {"conta": l["conta"], "pedido": l.get("pedido"), "pacote": l.get("pacote")}
    if not codigo.isdigit():
        return None
    for conta in ml.CONTAS:
        try:
            cli = ml.ML(conta)
            st, envio = cli.get(f"/shipments/{codigo}")
            if st != 200 or not envio.get("order_id"):
                continue
            st2, pedido = cli.get(f"/orders/{envio['order_id']}")
            return {"conta": conta, "pedido": envio["order_id"], "pacote": (pedido or {}).get("pack_id") if st2 == 200 else None}
        except Exception:
            continue
    return None


def buscar(codigo: str) -> dict:
    cod = normalizar(codigo)
    if not cod:
        return {"encontrado": False, "codigo": cod, "motivo": "código vazio"}
    achado, erros = _no_bling([cod])                     # já é o número do pedido/pacote (ML, Shopee, Amazon...)
    envio = None
    if not achado:
        envio = _envio_ml(cod)
        if envio:
            achado, erros = _no_bling([envio.get("pacote"), envio.get("pedido")])
    res = {"encontrado": bool(achado), "codigo": cod, "envio_ml": envio, "erros_bling": erros}
    if achado:
        res.update(achado)
    else:
        res["motivo"] = ("envio do ML encontrado, mas o pedido não está nos Blings (últimos dias)" if envio
                         else "nenhum pedido com esse código nos três Blings nem envio do ML")
    return res
