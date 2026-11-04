"""
marketplaces/expedicao/simulacao.py — Dia simulado da Expedição (dados FICTÍCIOS; não lê nem grava nada real)
MZ App

Gera, de forma determinística para o dia escolhido, pedidos de Flex, Coleta e Places (agência) e envios Full, e avança
o estado conforme o relógio simulado `agora`. Serve para ver o painel funcionando antes do dia real.

Volumes seguem a média dos últimos 30 dias (mix de 03/11): Flex ~50/dia, Coleta ~16, Places ~24; DBA ~10 (fictício).
O Full usa os envios previstos: Mercado Livre #80429611 (390 un.) e Amazon FBA19VTQKGBW (90 un.).
"""

import random
from datetime import datetime, timedelta

from marketplaces.expedicao import ml
from marketplaces.expedicao.dia import BRT

PLANO = (
    # canal exibido, conta, quantidade, logística, hora do prazo no ML (expected_date)
    ("Flex", "meli01", 12, "self_service", (23, 59)),
    ("Flex", "meli02", 7, "self_service", (23, 59)),
    ("Flex", "meli03", 33, "self_service", (23, 59)),
    ("Coleta", "meli01", 16, "cross_docking", (15, 30)),
    ("Agência", "meli02", 8, "xd_drop_off", (14, 0)),
    ("Agência", "meli03", 8, "xd_drop_off", (14, 0)),
    ("Agência", "meli02", 4, "xd_drop_off", (17, 0)),
    ("Agência", "meli03", 4, "xd_drop_off", (17, 0)),
    ("DBA", "amazon01", 4, "easy_ship", (14, 0)),
    ("DBA", "amazon02", 6, "easy_ship", (14, 0)),
)
INICIO_TURNO = (6, 0)


def _hora(dia, h, m):
    return datetime(dia.year, dia.month, dia.day, h, m, tzinfo=BRT)


def pedidos(dia, agora):
    """(linhas, marcas) do dia simulado, no mesmo formato de ml.linha e marcas.ler."""
    rng = random.Random(dia.toordinal())
    linhas, marcas = [], {}
    n = 0
    for canal, conta, qtd, logistica, (h, m) in PLANO:
        for _ in range(qtd):
            n += 1
            if canal == "DBA":   # etiqueta até 11:00; pacote no ponto de coleta até 14:00
                prazo_ml, meta = _hora(dia, 14, 0).isoformat(), _hora(dia, 11, 0).isoformat()
            else:
                prazo_ml, meta = ml.prazos(canal, _hora(dia, h, m).isoformat())
            meta_dt = datetime.fromisoformat(meta)
            unid = rng.choices((1, 2, 3), weights=(80, 15, 5))[0]
            liberado_em = _hora(dia, *INICIO_TURNO) + timedelta(minutes=rng.randint(0, 150))
            if rng.random() < 0.07:                      # ~7% terminam depois da meta (atraso)
                checkout_em = meta_dt + timedelta(minutes=rng.randint(5, 40))
            else:
                checkout_em = meta_dt - timedelta(minutes=rng.randint(8, 210))
            checkout_em = max(checkout_em, liberado_em + timedelta(minutes=20))
            embalado_em = checkout_em - timedelta(minutes=rng.randint(6, 25))
            envio = f"SIM{n:05d}"
            linhas.append({"conta": conta, "canal": canal, "logistica": logistica, "pedido": f"SIMP{n:05d}", "pacote": None,
                           "envio": envio, "unidades": unid, "status": "ready_to_ship", "substatus": "ready_to_print",
                           "situacao": "Etiqueta pronta para impressão", "liberado": agora >= liberado_em,
                           "sla_status": "on_time", "prazo_ml": prazo_ml, "meta": meta, "itens": []})
            if canal == "DBA":   # DBA: o status da Amazon diz se a etiqueta saiu e se o pacote chegou ao ponto
                ponto_em = checkout_em + timedelta(minutes=rng.randint(30, 150))
                linhas[-1].update(checkout_auto=agora >= checkout_em, no_ponto=agora >= ponto_em)
            reg = {}
            if agora >= embalado_em:
                reg["embalado"] = embalado_em.isoformat(timespec="seconds")
            if agora >= checkout_em:
                reg["checkout"] = checkout_em.isoformat(timespec="seconds")
            if reg:
                marcas[envio] = reg
    return linhas, marcas


def _etapas(marcos, agora):
    return {t: {"por": "sim", "papel": "estoquista", "em": em.isoformat(timespec="seconds")}
            for t, em in marcos.items() if agora >= em}


def envios_full(dia, agora):
    """Envios Full previstos para o dia, com as etapas T, E e OK conforme o relógio simulado."""
    d = dia.isoformat()
    previstos = (
        ("meli", "80429611", "meli01", 10, 0, "TORN-BANC-CAT1", "Torneira Gourmet Cozinha Mesa Bancada Pia Flexível Preta", 390,
         "Ana", "Beto", {"T": (7, 20), "E": (8, 50), "OK": (9, 40)}),
        ("amazon", "FBA19VTQKGBW", "amazon01", 8, 30, "KIT2MIC-FBA", "Kit 2 Microfones Sem Fio Preto", 90,
         "Caio", "Davi", {"T": (6, 50), "E": (7, 40), "OK": (8, 15)}),
    )
    saida = []
    for canal, numero, conta, ph, pm, sku, titulo, un, est, emb, marcos in previstos:
        etapas = _etapas({t: _hora(dia, *hm) for t, hm in marcos.items()}, agora)
        saida.append({"chave": f"{canal}-{numero}", "canal": canal, "numero": numero, "conta": conta, "coleta": d,
                      "prazo": _hora(dia, ph, pm).isoformat(timespec="minutes"), "criado_em": d, "criado_por": "simulação",
                      "concluido_em": etapas["OK"]["em"] if "OK" in etapas else None,
                      "produtos": [{"sku": sku, "titulo": titulo, "unidades": un, "estoquista": est, "embalador": emb,
                                    "etapas": etapas}]})
    return saida
