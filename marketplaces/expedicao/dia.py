"""
marketplaces/expedicao/dia.py — Painel do dia da Expedição: Flex, Coleta, Places (agência) e Full (somente leitura)
MZ App

Função pura `montar`: recebe as linhas do mapa do galpão (ml.linha), as marcas do galpão, os envios Full da fila de
montagem e o instante "agora", e devolve os cartões do painel. Não acessa rede nem disco: por isso a mesma conta serve
para o dia real e para a simulação (simulacao.py).

Regras:
  - um pedido está "feito" quando tem checkout (marca do galpão ou situação Verificado no Bling);
  - "embalado" é a etapa anterior (checkout implica embalado);
  - atrasado = meta já passou e ainda não tem checkout;
  - Places = envios de agência (ML: xd_drop_off); Coleta = cross_docking (meli01); Flex = self_service.
"""

from datetime import datetime, timedelta, timezone

BRT = timezone(timedelta(hours=-3))
ORDEM = ("Flex", "Coleta", "Places", "DBA")
ATENCAO_MIN = 60   # faltando menos que isso para a meta e ainda com pendentes: atenção


def _dt(iso):
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=BRT)
    except Exception:
        return None


def nome_canal(linha: dict) -> str:
    return "Places" if linha.get("canal") == "Agência" else linha.get("canal")


def estado(linha: dict, marcas: dict) -> tuple:
    """(embalado, checkout) de um pedido do galpão."""
    m = marcas.get(str(linha.get("envio")), {})
    checkout = bool(m.get("checkout")) or bool(linha.get("bling_checkout")) or bool(linha.get("checkout_auto"))
    return checkout or bool(m.get("embalado")), checkout


def _cartao(canal: str, linhas: list, marcas: dict, agora: datetime) -> dict:
    total = len(linhas)
    unidades = sum(l.get("unidades") or 0 for l in linhas)
    emb = chk = atras = liberados = no_ponto = 0
    pend_meta = []
    horas = {}
    contas = {}
    for l in linhas:
        e, c = estado(l, marcas)
        meta = _dt(l.get("meta"))
        emb += 1 if e else 0
        chk += 1 if c else 0
        liberados += 1 if l.get("liberado") else 0
        no_ponto += 1 if l.get("no_ponto") else 0
        contas[l["conta"]] = contas.get(l["conta"], 0) + 1
        pml = _dt(l.get("prazo_ml"))
        h = horas.setdefault(meta.strftime("%H:%M") if meta else "?",
                             {"total": 0, "feitos": 0, "prazo_ml": pml.strftime("%H:%M") if pml else None,
                              "prazo_dia": pml.strftime("%d/%m") if pml else None})
        h["total"] += 1
        h["feitos"] += 1 if c else 0
        if not c and meta:
            pend_meta.append(meta)
            if meta < agora:
                atras += 1
    prox = min(pend_meta) if pend_meta else None
    return {"canal": canal, "pedidos": total, "unidades": unidades, "liberados": liberados, "embalados": emb,
            "checkout": chk, "pendentes": total - chk, "atrasados": atras,
            "percentual": round(100 * chk / total, 1) if total else 100.0,
            "proxima_meta": prox.isoformat() if prox else None,
            "minutos_ate_meta": round((prox - agora).total_seconds() / 60) if prox else None,
            "por_hora": [dict(hora=k, **v) for k, v in sorted(horas.items())], "por_conta": contas,
            "no_ponto": no_ponto if canal == "DBA" else None}


def _full(envio: dict, agora: datetime) -> dict:
    prods = envio["produtos"]
    total = sum(p["unidades"] for p in prods)
    prontas = sum(p["unidades"] for p in prods if "OK" in p["etapas"])
    prazo = _dt(envio.get("prazo"))
    concl = bool(envio.get("concluido_em"))
    return {"chave": envio.get("chave"), "canal": envio.get("canal"), "numero": envio.get("numero"), "conta": envio.get("conta"),
            "coleta": envio.get("coleta"), "prazo": prazo.isoformat() if prazo else None,
            "unidades_total": total, "unidades_prontas": prontas,
            "percentual": round(100 * prontas / total, 1) if total else 0.0, "concluido": concl,
            "minutos_ate_prazo": round((prazo - agora).total_seconds() / 60) if prazo else None,
            "atrasado": bool(prazo and prazo < agora and not concl),
            "produtos": [{"sku": p["sku"], "unidades": p["unidades"], "estoquista": p.get("estoquista", ""),
                          "embalador": p.get("embalador", ""), "etapas": {t: True for t in p["etapas"]}} for p in prods]}


def montar(linhas: list, marcas: dict, full: list, agora: datetime, dia, plataformas: list = None) -> dict:
    """`dia` é um date. Só entram pedidos cuja meta cai nesse dia; o que ficou de dias anteriores vai em `herdados`."""
    do_dia = [l for l in linhas if (m := _dt(l.get("meta"))) and m.date() == dia]
    herdados = [l for l in linhas if (m := _dt(l.get("meta"))) and m.date() < dia and not estado(l, marcas)[1]]
    cartoes = []
    for canal in ORDEM:
        cartoes.append(_cartao(canal, [l for l in do_dia if nome_canal(l) == canal], marcas, agora))
    envios_full = [_full(e, agora) for e in full
                   if (e.get("coleta") or (e.get("prazo") or "")[:10]) == dia.isoformat()
                   or ((_dt(e.get("prazo")) and _dt(e["prazo"]).date() == dia))]
    envios_full.sort(key=lambda f: f["prazo"] or "")
    alertas = []
    for c in cartoes:
        if c["atrasados"]:
            alertas.append({"nivel": "critico", "texto": f"{c['canal']}: {c['atrasados']} pedido(s) sem checkout com a meta vencida"})
        elif c["pendentes"] and c["minutos_ate_meta"] is not None and c["minutos_ate_meta"] <= ATENCAO_MIN:
            alertas.append({"nivel": "atencao", "texto": f"{c['canal']}: {c['pendentes']} pendente(s) e faltam {max(c['minutos_ate_meta'], 0)} min para a meta"})
    for f in envios_full:
        if f["atrasado"]:
            alertas.append({"nivel": "critico", "texto": f"Full {f['canal']} #{f['numero']}: prazo vencido com {f['percentual']}% pronto"})
        elif not f["concluido"] and f["minutos_ate_prazo"] is not None and f["minutos_ate_prazo"] <= 120 and f["percentual"] < 80:
            alertas.append({"nivel": "atencao", "texto": f"Full {f['canal']} #{f['numero']}: {f['percentual']}% pronto e faltam {f['minutos_ate_prazo']} min"})
    if herdados:
        alertas.append({"nivel": "critico", "texto": f"{len(herdados)} pedido(s) de dias anteriores ainda sem checkout"})
    ordem_nivel = {"critico": 0, "atencao": 1}
    alertas.sort(key=lambda a: ordem_nivel[a["nivel"]])
    cortes = []
    for c in cartoes:
        for h in c["por_hora"]:
            if h["hora"] == "?":
                continue
            hh, mm = h["hora"].split(":")
            corte = datetime(dia.year, dia.month, dia.day, int(hh), int(mm), tzinfo=BRT)
            cortes.append({"canal": c["canal"], "corte": h["hora"], "prazo_ml": h["prazo_ml"], "prazo_dia": h.get("prazo_dia"), "pedidos": h["total"],
                           "pendentes": h["total"] - h["feitos"],
                           "minutos": round((corte - agora).total_seconds() / 60)})
    cortes.sort(key=lambda x: (x["corte"], x["canal"]))
    # Amazon DBA e Shopee: cortes fixos da operação (cortes.py). Ainda sem contagem de pedidos por canal.
    plat = []
    tem_dba = any(c["canal"] == "DBA" and c["pedidos"] for c in cartoes)
    for c in plataformas or []:
        if tem_dba and c.get("canal") == "DBA" and str(c.get("etapa", "")).startswith("Impressão"):
            continue   # o corte das 11:00 já aparece, com os pendentes, na faixa dos cortes
        hh, mm = str(c["corte"]).split(":")
        corte = datetime(dia.year, dia.month, dia.day, int(hh), int(mm), tzinfo=BRT)
        plat.append({"plataforma": c.get("plataforma", ""), "canal": c["canal"], "etapa": c.get("etapa", ""),
                     "corte": c["corte"], "minutos": round((corte - agora).total_seconds() / 60)})
    plat.sort(key=lambda x: (x["corte"], x["canal"]))
    tot = sum(c["pedidos"] for c in cartoes)
    feitos = sum(c["checkout"] for c in cartoes)
    return {"dia": dia.isoformat(), "agora": agora.isoformat(timespec="seconds"), "cartoes": cartoes, "cortes": cortes, "cortes_plataformas": plat, "full": envios_full,
            "alertas": alertas, "herdados": len(herdados),
            "geral": {"pedidos": tot, "checkout": feitos, "pendentes": tot - feitos, "percentual": round(100 * feitos / tot, 1) if tot else 100.0,
                      "atrasados": sum(c["atrasados"] for c in cartoes) + len(herdados),
                      "full_envios": len(envios_full), "full_unidades": sum(f["unidades_total"] for f in envios_full),
                      "full_unidades_restantes": sum(f["unidades_total"] - f["unidades_prontas"] for f in envios_full)}}
