"""
marketplaces/expedicao/shopee_bling.py — Pedidos da Shopee para a Expedição, lidos do Bling (por enquanto)
MZ App

Somente leitura. Usa a lista de pedidos que a Expedição já busca no bling01 e só abre o detalhe de cada pedido uma vez,
para saber a modalidade (o serviço do volume). O Bling não guarda quando o pedido foi verificado: para contar os feitos do
dia, shopee_expedicao.json lembra quais estavam em aberto e registra o dia em que viraram Verificado.
"""

import json
import os
import re
import time
from datetime import datetime, timedelta, timezone

from marketplaces.bling import api as bling
from marketplaces.expedicao import bling_checkout, cortes

BRT = timezone(timedelta(hours=-3))
CONTA_BLING = "bling01"
ABERTOS = (6, 15)
# Contagem começa em 10/11/2026 (pedido do usuário): os pedidos em aberto de antes ficam de fora da Expedição
INICIO = "2026-11-10"
# "Logistica Shopee" = Entrega Direta: leitura pelo prazo curto, a confirmar com o painel da Shopee
SERVICOS = {"shopee xpress": "Shopee Xpress", "logistica shopee": "Entrega Direta"}
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CAMINHO = os.path.join(BASE_DIR, "shopee_expedicao.json")
LOJAS_SEG = 3600
_lojas = {"ts": 0.0, "ids": set()}
_detalhes = {}


def _ids_lojas() -> set:
    if time.time() - _lojas["ts"] >= LOJAS_SEG:
        st, r = bling.get(CONTA_BLING, "/canais-venda", limite=100)
        if st != 200:
            raise RuntimeError(f"canais de venda HTTP {st}")
        _lojas.update(ts=time.time(), ids={c.get("id") for c in r.get("data") or [] if re.search("shopee", str(c.get("descricao")), re.I)})
    return _lojas["ids"]


def _ler_estado() -> dict:
    try:
        with open(CAMINHO, encoding="utf-8") as f:
            d = json.load(f)
        return {"abertos": list(d.get("abertos") or []), "feitos": dict(d.get("feitos") or {})}
    except (OSError, ValueError, AttributeError):
        return {"abertos": [], "feitos": {}}


def _gravar_estado(estado: dict):
    with open(CAMINHO + ".tmp", "w", encoding="utf-8") as f:
        json.dump(estado, f, ensure_ascii=False)
    os.replace(CAMINHO + ".tmp", CAMINHO)


def _detalhe(pid) -> dict:
    """{canal, servico, itens}. A modalidade e os itens não mudam: uma leitura por pedido."""
    if pid not in _detalhes:
        st, r = bling.get(CONTA_BLING, f"/pedidos/vendas/{pid}")
        if st != 200:
            raise RuntimeError(f"pedido {pid} HTTP {st}")
        x = r.get("data") or {}
        vols = (x.get("transporte") or {}).get("volumes") or []
        servico = str((vols[0] if vols else {}).get("servico") or "").strip()
        _detalhes[pid] = {"servico": servico, "canal": SERVICOS.get(servico.lower()),
                          "itens": [{"mlb": None, "sku": i.get("codigo"), "titulo": i.get("descricao"), "qtd": i.get("quantidade")} for i in x.get("itens") or []]}
        time.sleep(0.35)
    return _detalhes[pid]


def _meta(canal: str, hoje) -> str:
    hhmm = next((c["corte"] for c in cortes.carregar() if c.get("plataforma") == "Shopee" and c.get("canal") == canal), "12:00")
    h, m = hhmm.split(":")
    return datetime(hoje.year, hoje.month, hoje.day, int(h), int(m), tzinfo=BRT).isoformat()


def _linha(p: dict, det: dict, hoje) -> dict:
    sit = p["situacao"]["id"]
    return {"conta": "shopee", "canal": det["canal"], "logistica": det["servico"], "pedido": p.get("numeroLoja"), "pacote": None, "envio": p.get("numeroLoja"),
            "unidades": sum(i["qtd"] or 0 for i in det["itens"]), "status": "bling", "substatus": None, "situacao": bling_checkout.SITUACOES.get(sit, str(sit)),
            "meta": _meta(det["canal"], hoje), "prazo_ml": None, "itens": det["itens"], "bling_numero": p.get("numero"),
            "bling_situacao": bling_checkout.SITUACOES.get(sit, str(sit)), "bling_checkout": sit == bling_checkout.SITUACAO_VERIFICADO,
            "fonte": "Bling"}


def linhas(agora: datetime = None) -> tuple:
    """(linhas de hoje, erros): todo pedido em aberto, e os verificados que viraram Verificado hoje."""
    hoje = (agora or datetime.now(BRT)).date()
    erros = {}
    try:
        lojas = _ids_lojas()
        pedidos = [p for p in bling_checkout._pedidos(CONTA_BLING).values() if (p.get("loja") or {}).get("id") in lojas]
    except Exception as e:
        return [], {"shopee": f"Bling: {type(e).__name__}: {str(e)[:100]}"}
    estado = _ler_estado()
    abertos_antes, feitos = set(estado["abertos"]), estado["feitos"]
    abertos, saida, vistos = set(), [], set()
    for p in pedidos:
        pid, sit = str(p["id"]), p["situacao"]["id"]
        vistos.add(pid)
        if str(p.get("data") or "") < INICIO:
            continue
        if sit in ABERTOS:
            abertos.add(pid)
        elif sit == bling_checkout.SITUACAO_VERIFICADO:
            if pid not in feitos:
                feitos[pid] = hoje.isoformat() if pid in abertos_antes else str(p.get("data"))
            if feitos[pid] != hoje.isoformat():
                continue
        else:
            continue
        try:
            det = _detalhe(p["id"])
        except Exception as e:
            erros["shopee"] = f"Bling: {type(e).__name__}: {str(e)[:100]}"
            continue
        if not det["canal"]:
            erros["shopee_modalidade"] = f"modalidade desconhecida: {det['servico'] or 'vazia'}"
            continue
        saida.append(_linha(p, det, hoje))
    novo = {"abertos": sorted(abertos), "feitos": {k: v for k, v in feitos.items() if k in vistos}}
    try:
        _gravar_estado(novo)
    except OSError as e:
        erros["shopee_estado"] = f"{type(e).__name__}: {str(e)[:100]}"
    return saida, erros
