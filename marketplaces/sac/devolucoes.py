"""
marketplaces/sac/devolucoes.py — Devoluções em andamento pela logística reversa do Mercado Livre (SAC, somente leitura)
MZ App

Reclamações, mediações e devoluções são informações do SAC. Esta consulta junta:
  - as reclamações abertas dos tipos que geram devolução (returns, mediations);
  - as mediações que cancelaram pedidos nos últimos N dias (a reclamação já pode estar encerrada e a devolução ainda a caminho);
e, para cada uma, o estado da devolução (/post-purchase/v2/claims/{id}/returns): criada, a caminho, chegou ou não concluída.
Nada é marcado, respondido ou alterado no ML.
"""

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from reposicao_full import ML
import ml_api

BRT = timezone(timedelta(hours=-3))
CONTAS = ("meli01", "meli02", "meli03")
TIPOS_COM_DEVOLUCAO = ("returns", "mediations")
ATUALIZA_SEG = 900            # idade a partir da qual uma abertura da tela dispara nova leitura em segundo plano
VALE_DISCO_SEG = 12 * 3600
CAMINHO = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "sac_devolucoes_cache.json")
_trava = threading.Lock()
_estado = {"dados": None, "ts": 0.0, "erro": None, "carregando": False}

FASES = {
    "chegou": "Chegou ao vendedor",
    "a_caminho": "A caminho do vendedor",
    "aguardando_postagem": "Criada: o comprador ainda não postou",
    "nao_concluida": "Não concluída",
    "encerrada": "Encerrada",
}
ORDEM_FASE = {"chegou": 0, "nao_concluida": 1, "a_caminho": 2, "aguardando_postagem": 3, "encerrada": 4}


def fase_da_devolucao(ret: dict) -> str:
    """Fase a partir de /post-purchase/v2/claims/{id}/returns."""
    envios = (ret or {}).get("shipments") or []
    est_envio = envios[0].get("status") if envios else None
    est = est_envio or (ret or {}).get("status")
    if "delivered" in (est_envio, (ret or {}).get("status")):
        return "chegou"
    if "shipped" in (est_envio, (ret or {}).get("status")) or est == "out_for_delivery":
        return "a_caminho"
    if est in ("cancelled", "failed", "not_delivered"):
        return "nao_concluida"
    if est in ("closed", "expired"):
        return "encerrada"
    return "aguardando_postagem"


def acao_do_sac(fase: str, aguarda_voce: bool) -> str:
    base = {"chegou": "Conferir o recebimento no galpão e analisar o produto (apto ou inapto); contestar só se houver dano ou produto diferente.",
            "a_caminho": "Acompanhar o rastreio. Dar entrada no estoque só quando o pacote chegar.",
            "aguardando_postagem": "Acompanhar: o comprador ainda não postou o produto.",
            "nao_concluida": "Devolução não concluída: o produto pode ter ficado com o comprador. Verificar a reclamação antes de qualquer baixa.",
            "encerrada": "Devolução encerrada."}[fase]
    return ("RESPONDER A RECLAMAÇÃO (aguarda você). " if aguarda_voce else "") + base


def _data(iso):
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(BRT)
    except Exception:
        return None


def _item(conta: str, base: dict, ret: dict) -> dict:
    envios = (ret or {}).get("shipments") or []
    e = envios[0] if envios else {}
    fase = fase_da_devolucao(ret)
    return {"conta": conta, "claim_id": base.get("id"), "tipo": base.get("type"), "etapa": base.get("stage"),
            "status": base.get("status"), "motivo": base.get("reason_id"), "pedido": base.get("resource_id"),
            "data": base.get("date_created"), "aguarda_voce": bool(base.get("aguarda_voce")),
            "devolucao": {"status": ret.get("status"), "tipo": ret.get("subtype"), "dinheiro": ret.get("status_money"),
                          "data": ret.get("date_created"), "envio_status": e.get("status"), "rastreio": e.get("tracking_number")},
            "fase": fase, "fase_texto": FASES[fase], "acao": acao_do_sac(fase, bool(base.get("aguarda_voce")))}


def _reclamacoes_abertas(ml, max_paginas=8) -> list:
    saida, off = [], 0
    for _ in range(max_paginas):
        st, d = ml.get("/post-purchase/v1/claims/search", status="opened", limit=50, offset=off, sort="date_created:desc")
        if st != 200:
            raise RuntimeError(f"reclamações HTTP {st}")
        lote = (d or {}).get("data") or []
        for c in lote:
            if c.get("type") not in TIPOS_COM_DEVOLUCAO:
                continue
            vend = next((p for p in c.get("players") or [] if p.get("role") == "respondent"), {})
            c = dict(c, aguarda_voce=bool(vend.get("available_actions")))
            saida.append(c)
        off += len(lote)
        if len(lote) < 50 or off >= ((d or {}).get("paging") or {}).get("total", 0):
            break
    return saida


def _mediacoes_de_pedidos_cancelados(ml, sid, dias, max_paginas=10) -> list:
    """Ids de reclamação que aparecem em pedidos cancelados nos últimos `dias` dias."""
    de = (datetime.now(BRT) - timedelta(days=dias)).strftime("%Y-%m-%dT00:00:00.000-03:00")
    ids = []
    for pg in range(max_paginas):
        st, b = ml.get("/orders/search", seller=sid, sort="date_desc", limit=50, offset=pg * 50,
                       **{"order.status": "cancelled", "order.date_created.from": de})
        if st != 200:
            break
        res = b.get("results") or []
        for o in res:
            for m in o.get("mediations") or []:
                ids.append(m.get("id") if isinstance(m, dict) else m)
        if len(res) < 50:
            break
    return ids


def devolucoes(conta: str, dias: int = 15) -> dict:
    ml = ML(conta)
    sid = ml_api.get_seller_id(conta)
    claims = {c["id"]: c for c in _reclamacoes_abertas(ml)}
    for cid in _mediacoes_de_pedidos_cancelados(ml, sid, dias):
        if cid in claims:
            continue
        st, c = ml.get(f"/post-purchase/v1/claims/{cid}")
        if st == 200 and c.get("type") in TIPOS_COM_DEVOLUCAO:
            claims[cid] = dict(c, aguarda_voce=False)
    corte = datetime.now(BRT) - timedelta(days=dias)

    def um(c):
        st, ret = ml.get(f"/post-purchase/v2/claims/{c['id']}/returns")
        if st != 200 or not isinstance(ret, dict):
            return None
        item = _item(conta, c, ret)
        d = _data(ret.get("date_created") or c.get("date_created"))
        # em andamento: some o que já terminou (devolução entregue com a reclamação encerrada) e o que é antigo sem pendência
        if item["fase"] == "encerrada" or (item["fase"] == "chegou" and c.get("status") == "closed"):
            return None
        if d and d < corte and not item["aguarda_voce"] and item["fase"] != "a_caminho":
            return None
        return item

    with ThreadPoolExecutor(max_workers=6) as ex:
        itens = [x for x in ex.map(um, claims.values()) if x]
    itens.sort(key=lambda x: (ORDEM_FASE[x["fase"]], not x["aguarda_voce"], x["data"] or ""))
    return {"success": True, "conta": conta, "dias": dias, "itens": itens, "resumo": _resumo(itens)}


def _resumo(itens: list) -> dict:
    return {f: sum(1 for x in itens if x["fase"] == f) for f in ORDEM_FASE if any(x["fase"] == f for x in itens)}


def todas(dias: int = 15) -> dict:
    """Leitura completa das três contas ao mesmo tempo (lenta: ~1 min). A tela usa `obter()`, que não espera."""
    itens, erros = [], {}

    def uma(c):
        try:
            return c, devolucoes(c, dias)["itens"], None
        except Exception as e:
            return c, [], f"{type(e).__name__}: {str(e)[:120]}"

    with ThreadPoolExecutor(max_workers=len(CONTAS)) as ex:
        for c, ls, erro in ex.map(uma, CONTAS):
            itens += ls
            if erro:
                erros[c] = erro
    itens.sort(key=lambda x: (ORDEM_FASE[x["fase"]], not x["aguarda_voce"], x["data"] or ""))
    return {"success": True, "dias": dias, "itens": itens, "erros": erros, "resumo": _resumo(itens)}


# ── instantâneo em segundo plano: a tela do SAC abre na hora e a leitura se renova sozinha ──
def _ler_disco():
    try:
        with open(CAMINHO, encoding="utf-8") as f:
            d = json.load(f)
        if time.time() - d["ts"] < VALE_DISCO_SEG:
            return d
    except (OSError, ValueError, KeyError):
        pass
    return None


def _atualizar(dias, leitor=None):
    try:
        dados = (leitor or todas)(dias)
        with _trava:
            if dados["erros"] and _estado["dados"] is not None and not dados["itens"]:
                _estado["erro"] = "; ".join(f"{k}: {v}" for k, v in dados["erros"].items())[:300]   # falhou tudo: mantém o último bom
            else:
                _estado.update(dados=dados, ts=time.time(), erro=None)
                try:
                    with open(CAMINHO + ".tmp", "w", encoding="utf-8") as f:
                        json.dump({"ts": _estado["ts"], "dados": dados}, f, ensure_ascii=False)
                    os.replace(CAMINHO + ".tmp", CAMINHO)
                except OSError:
                    pass
    except Exception as e:
        with _trava:
            _estado["erro"] = f"{type(e).__name__}: {str(e)[:200]}"
    finally:
        with _trava:
            _estado["carregando"] = False


def obter(dias: int = 15, forcar: bool = False, leitor=None) -> dict:
    """Último resultado, sem esperar; dispara a renovação em segundo plano quando está velho (ou `forcar`)."""
    with _trava:
        if _estado["dados"] is None:
            d = _ler_disco()
            if d:
                _estado.update(dados=d["dados"], ts=d["ts"])
        velho = time.time() - _estado["ts"] >= ATUALIZA_SEG
        if (velho or forcar) and not _estado["carregando"]:
            _estado["carregando"] = True
            threading.Thread(target=_atualizar, args=(dias, leitor), daemon=True).start()
        ts = _estado["ts"]
        return {"dados": _estado["dados"], "erro": _estado["erro"], "carregando": _estado["carregando"],
                "gerado_em": datetime.fromtimestamp(ts, BRT).isoformat(timespec="seconds") if ts else None,
                "idade_seg": round(time.time() - ts) if ts else None}
