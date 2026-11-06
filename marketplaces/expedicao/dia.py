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
POSTADO = ("in_hub", "in_packing_list", "dropped_off", "picked_up")   # igual a postadoML em pagina.html


def _dt(iso):
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=BRT)
    except Exception:
        return None


def nome_canal(linha: dict) -> str:
    return "Places" if linha.get("canal") == "Agência" else linha.get("canal")


def postado(linha: dict) -> bool:
    """Já saiu do galpão (na agência, na lista de postagem ou coletado): a parte da Expedição terminou."""
    return linha.get("status") == "ready_to_ship" and linha.get("substatus") in POSTADO


def estado(linha: dict, marcas: dict) -> tuple:
    """(embalado, checkout) de um pedido do galpão."""
    m = marcas.get(str(linha.get("envio")), {})
    checkout = bool(m.get("checkout")) or bool(linha.get("bling_checkout")) or bool(linha.get("checkout_auto")) or postado(linha)
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


def _sem_checkout(do_dia: list, herdados: list, marcas: dict, agora: datetime) -> list:
    """Pedido a pedido, o que os cartões contam como pendente: do dia e de dias anteriores, a meta mais antiga primeiro."""
    saida = []
    for l, herdado in [(x, False) for x in do_dia if not estado(x, marcas)[1]] + [(x, True) for x in herdados]:
        meta = _dt(l.get("meta"))
        saida.append({"canal": nome_canal(l), "conta": l.get("conta"), "pedido": l.get("pedido"), "pacote": l.get("pacote"),
                      "envio": l.get("envio"), "bling_numero": l.get("bling_numero"), "bling_situacao": l.get("bling_situacao"),
                      "situacao": l.get("situacao") or l.get("status"), "meta": l.get("meta"), "herdado": herdado,
                      "atrasado": bool(meta and meta < agora), "unidades": l.get("unidades"),
                      "itens": [{"sku": i.get("sku"), "qtd": i.get("qtd")} for i in l.get("itens") or []]})
    saida.sort(key=lambda x: x["meta"] or "9")
    return saida


def _resumo_sem_checkout(lista: list) -> list:
    """Só totais, para a TV: a tela é aberta, a lista pedido a pedido fica na Expedição com login."""
    g = {}
    for x in lista:
        t = g.setdefault((x["canal"], x["conta"]), {"canal": x["canal"], "conta": x["conta"], "pedidos": 0, "atrasados": 0})
        t["pedidos"] += 1
        t["atrasados"] += 1 if x["atrasado"] else 0
    return sorted(g.values(), key=lambda t: (-t["pedidos"], t["canal"], t["conta"] or ""))


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
            "status": envio.get("status") or "montagem",      # "fila" = programado, o líder ainda não iniciou
            "produtos": [{"sku": p["sku"], "unidades": p["unidades"], "estoquista": p.get("estoquista", ""),
                          "embalador": p.get("embalador", ""), "etapas": {t: True for t in p["etapas"]}} for p in prods]}


def _full_no_monitor(envio: dict, agora: datetime, dia) -> bool:
    """Concluído: só no dia em que terminou. Iniciado e não concluído: sempre. Na fila: do dia ou com o prazo vencido."""
    if envio.get("concluido_em"):
        return str(envio["concluido_em"])[:10] == dia.isoformat()
    if (envio.get("status") or "montagem") != "fila":
        return True
    prazo = _dt(envio.get("prazo"))
    return (envio.get("coleta") or (envio.get("prazo") or "")[:10]) == dia.isoformat() or bool(prazo and (prazo.date() == dia or prazo < agora))


def _full_coleta(full: list, dia) -> list:
    """Fulls com a coleta marcada para o dia, prontos ou não: é o que o motorista vem buscar."""
    return sorted(({"canal": e.get("canal"), "numero": e.get("numero"), "conta": e.get("conta"), "pronto": bool(e.get("concluido_em"))}
                   for e in full if e.get("coleta") == dia.isoformat()),
                  key=lambda x: (str(x["canal"]), str(x["conta"]), str(x["numero"])))


def _hhmm_do_dia(dia, hhmm: str):
    hh, mm = str(hhmm).split(":")
    return datetime(dia.year, dia.month, dia.day, int(hh), int(mm), tzinfo=BRT)


def _marco(rotulo, quando, pedidos, pendentes):
    return {"rotulo": rotulo, "hhmm": quando.strftime("%H:%M"), "iso": quando.isoformat(), "pedidos": pedidos, "pendentes": pendentes}


def _modulo(mid, nome, linhas, marcas, agora, dia, etapas=None, sem_dados=False, plataforma="Mercado Livre"):
    """Cartão do monitor: quantos saem, quantos já estão prontos, quantos faltam e o(s) horário(s) de entrega no local.
    `etapas`: [(rótulo, 'HH:MM', conta_pendentes)] quando o horário vem da operação (DBA, Shopee) e não do prazo de cada pedido."""
    n = len(linhas)
    prontos = sum(1 for l in linhas if estado(l, marcas)[1])
    marcos = []
    if etapas:
        for rotulo, hhmm, kind in etapas:
            quando = _hhmm_do_dia(dia, hhmm)
            if sem_dados:
                # sem pedidos carregados (Shopee): depois do corte zera, em vez de contar como atraso
                pend = 0 if quando < agora else None
            elif kind == "ponto":
                pend = sum(1 for l in linhas if not l.get("no_ponto"))
            else:
                pend = sum(1 for l in linhas if not estado(l, marcas)[1])
            marcos.append(_marco(rotulo, quando, None if sem_dados else n, pend))
    else:
        por = {}
        for l in linhas:
            m = _dt(l.get("meta"))
            if not m:
                continue
            g = por.setdefault(m.replace(second=0, microsecond=0), [0, 0])
            g[0] += 1
            g[1] += 0 if estado(l, marcas)[1] else 1
        for quando in sorted(por):
            marcos.append(_marco("Corte", quando, por[quando][0], por[quando][1]))
    marcos.sort(key=lambda x: x["iso"])
    # o horário em destaque é o primeiro que ainda tem pendência; sem pendência, o último (tudo pronto)
    principal = next((i for i, m in enumerate(marcos) if (m["pendentes"] is None and _dt(m["iso"]) >= agora) or (m["pendentes"] or 0) > 0), None)
    if principal is None and marcos:
        principal = len(marcos) - 1
    atrasados = sum(1 for m in marcos if (m["pendentes"] or 0) > 0 and _dt(m["iso"]) < agora)
    por_conta = {}
    for l in linhas:
        c = por_conta.setdefault(l.get("conta"), {"conta": l.get("conta"), "pendentes": 0, "feitos": 0})
        c["feitos" if estado(l, marcas)[1] else "pendentes"] += 1
    return {"id": mid, "nome": nome, "plataforma": plataforma, "por_conta": sorted(por_conta.values(), key=lambda c: str(c["conta"])),
            "sem_dados": sem_dados, "pedidos": None if sem_dados else n,
            "unidades": None if sem_dados else sum(l.get("unidades") or 0 for l in linhas),
            "prontos": None if sem_dados else prontos, "pendentes": None if sem_dados else n - prontos,
            "percentual": None if sem_dados else (round(100 * prontos / n, 1) if n else 100.0),
            "no_ponto": sum(1 for l in linhas if l.get("no_ponto")) if mid == "dba" else None,
            "atrasados": atrasados, "marcos": marcos, "principal": principal}


def _modulos(do_dia: list, marcas: dict, agora: datetime, dia, plataformas: list) -> list:
    """Cartões do monitor da TV, por corte: Flex, Coleta, Places por conta (cada conta tem corte próprio), Amazon DBA e Shopee."""
    plat = plataformas or []
    mods = [_modulo("flex", "Flex", [l for l in do_dia if l.get("canal") == "Flex"], marcas, agora, dia),
            _modulo("coleta", "Coleta", [l for l in do_dia if l.get("canal") == "Coleta"], marcas, agora, dia)]
    places = [l for l in do_dia if l.get("canal") == "Agência"]
    contas = ["meli02", "meli03"] + sorted({l["conta"] for l in places} - {"meli02", "meli03"})
    for c in contas:
        mods.append(_modulo(f"places_{c}", f"Places Meli {c[-1].lstrip('0') or c}", [l for l in places if l["conta"] == c], marcas, agora, dia))
    dba = [l for l in do_dia if l.get("canal") == "DBA"]
    etapas = [(c.get("etapa", ""), c["corte"], "ponto" if "ponto" in str(c.get("etapa", "")).lower() else "etiqueta")
              for c in plat if c.get("canal") == "DBA"]
    mods.append(_modulo("dba", "Amazon DBA", dba, marcas, agora, dia, etapas or None, plataforma="Amazon"))
    shopee = [l for l in do_dia if l.get("conta") == "shopee"]
    for mid, nome, canal in (("shopee_xpress", "Shopee Xpress", "Shopee Xpress"), ("shopee_direta", "Shopee Entrega Direta (Flex)", "Entrega Direta")):
        et = [(c.get("etapa", "Corte do dia"), c["corte"], "corte") for c in plat if c.get("plataforma") == "Shopee" and c.get("canal") == canal]
        mods.append(_modulo(mid, nome, [l for l in shopee if l.get("canal") == canal], marcas, agora, dia, et, sem_dados=not shopee, plataforma="Shopee"))
    return mods


def _modalidades(modulos: list, do_dia: list, marcas: dict, agora: datetime, dia) -> list:
    """Cartões da Expedição com login, um por modalidade: iguais aos da TV, com Places somando as contas."""
    saida = []
    for m in modulos:
        if m["id"].startswith("places_"):
            if not any(x["id"] == "places" for x in saida):
                saida.append(_modulo("places", "Places", [l for l in do_dia if l.get("canal") == "Agência"], marcas, agora, dia))
        else:
            saida.append(dict(m, nome=m["nome"].replace(" (Flex)", "")))
    return saida


def montar(linhas: list, marcas: dict, full: list, agora: datetime, dia, plataformas: list = None) -> dict:
    """`dia` é um date. Só entram pedidos cuja meta cai nesse dia; o que ficou de dias anteriores vai em `herdados`."""
    do_dia = [l for l in linhas if (m := _dt(l.get("meta"))) and m.date() == dia]
    herdados = [l for l in linhas if (m := _dt(l.get("meta"))) and m.date() < dia and not estado(l, marcas)[1]]
    cartoes = []
    for canal in ORDEM:
        cartoes.append(_cartao(canal, [l for l in do_dia if nome_canal(l) == canal], marcas, agora))
    envios_full = [_full(e, agora) for e in full if _full_no_monitor(e, agora, dia)]
    envios_full.sort(key=lambda f: (f["concluido"], f["prazo"] or ""))          # prontos no fim
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
    sem_checkout = _sem_checkout(do_dia, herdados, marcas, agora)
    modulos = _modulos(do_dia, marcas, agora, dia, plataformas)
    return {"dia": dia.isoformat(), "agora": agora.isoformat(timespec="seconds"), "cartoes": cartoes, "cortes": cortes, "cortes_plataformas": plat, "full": envios_full, "full_coleta": _full_coleta(full, dia),
            "modulos": modulos, "modalidades": _modalidades(modulos, do_dia, marcas, agora, dia),
            "alertas": alertas, "herdados": len(herdados), "sem_checkout": sem_checkout, "sem_checkout_resumo": _resumo_sem_checkout(sem_checkout),
            "geral": {"pedidos": tot, "checkout": feitos, "pendentes": tot - feitos, "percentual": round(100 * feitos / tot, 1) if tot else 100.0,
                      "atrasados": sum(c["atrasados"] for c in cartoes) + len(herdados),
                      "full_envios": len(envios_full), "full_unidades": sum(f["unidades_total"] for f in envios_full),
                      "full_unidades_restantes": sum(f["unidades_total"] - f["unidades_prontas"] for f in envios_full)}}
