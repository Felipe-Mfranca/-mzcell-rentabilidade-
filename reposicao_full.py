"""
reposicao_full.py — Protótipo de cobertura e sugestão de reposição do Full
MZCell Rentabilidade

SOMENTE LEITURA: só faz GET na API do Mercado Livre. Não altera o app.
Uso:  .\\venv\\Scripts\\python.exe reposicao_full.py [meli01,meli02,meli03]

Regras (ver memória do módulo / diag_full.py):
- Unidade de cálculo = inventory_id (vários MLBs podem dividir o mesmo estoque Full).
- Estoque considerado = available_quantity + transfer (em transferência entre CDs).
- Vendas = unidades dos pedidos (exclui invalid, cancelled e pack_splitted), fuso -04:00 como o parser.
- Janelas de 7, 15 e 30 dias terminando no último dia fechado (ontem).
- Demanda = 50% média 7d + 30% média 15d + 20% média 30d; em tendência de alta (7d/30d > 1,15) usa a maior média.
- Sugestão = max(0, ceil(META_DIAS × demanda − estoque considerado)).
"""

import asyncio
import math
import os
import sys
import time
import warnings
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import httpx

import ml_api

warnings.filterwarnings("ignore")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
B = ml_api.ML_BASE_URL
ML_TZ = timezone(timedelta(hours=-4))  # mesmo fuso do parser_rentabilidade
META_DIAS = 20
PESOS = {7: 0.5, 15: 0.3, 30: 0.2}
ALTA, QUEDA = 1.15, 0.85
STATUS_FORA = {"invalid", "cancelled"}


class ML:
    def __init__(self, conta: str):
        self.conta = conta
        self.cli = httpx.Client(timeout=30, verify=False)
        self.renovou = False

    def get(self, path: str, **params):
        espera, n429 = 2, 0
        while True:
            r = self.cli.get(B + path, params=params, headers=ml_api._headers(self.conta))
            if r.status_code == 401 and not self.renovou:
                self.renovou = True
                asyncio.run(ml_api.refresh_token(self.conta))
                continue
            if (r.status_code == 429 or r.status_code >= 500) and n429 < 6:
                n429 += 1
                time.sleep(espera)
                espera = min(espera * 2, 30)
                continue
            return r.status_code, (r.json() if r.headers.get("content-type", "").startswith("application/json") else None)


def detalhar(ml: ML, ids: list) -> dict:
    itens = {}
    for i in range(0, len(ids), 20):
        st, lote = ml.get("/items", ids=",".join(ids[i:i + 20]),
                          attributes="id,title,status,shipping,inventory_id,variations,seller_custom_field")
        for e in lote or []:
            if e.get("code") == 200:
                itens[e["body"]["id"]] = e["body"]
    return itens


def anuncios_full(ml: ML, seller_id: str) -> dict:
    ids, offset = [], 0
    while True:
        st, b = ml.get(f"/users/{seller_id}/items/search", status="active", limit=100, offset=offset)
        res = (b or {}).get("results", [])
        ids += res
        offset += len(res)
        if not res or offset >= b["paging"]["total"] or offset >= 1000:
            break
    itens = detalhar(ml, ids)
    return {k: v for k, v in itens.items() if (v.get("shipping") or {}).get("logistic_type") == "fulfillment"}


def mapa_inventario(itens: dict) -> dict:
    """(item_id, variation_id|None) → inventory_id"""
    m = {}
    for it in itens.values():
        if it.get("inventory_id"):
            m[(it["id"], None)] = it["inventory_id"]
        for v in it.get("variations") or []:
            if v.get("inventory_id"):
                m[(it["id"], v.get("id"))] = v["inventory_id"]
    return m


def vendas_por_item(ml: ML, seller_id: str, ate: datetime.date) -> dict:
    """{(item_id, variation_id): {dia: unidades}} — 30 dias até `ate`, consultando dia a dia."""
    out = defaultdict(lambda: defaultdict(int))
    for d in range(30):
        dia = ate - timedelta(days=d)
        offset = 0
        while True:
            st, b = ml.get("/orders/search", seller=seller_id, limit=50, offset=offset, sort="date_asc",
                           **{"order.date_created.from": f"{dia}T00:00:00.000-04:00",
                              "order.date_created.to": f"{dia}T23:59:59.000-04:00"})
            if st != 200:
                print(f"   [{ml.conta}] orders {dia} offset {offset}: HTTP {st}")
                break
            res = b.get("results", [])
            for o in res:
                if o.get("status") in STATUS_FORA:
                    continue
                if (o.get("cancel_detail") or {}).get("code") == "pack_splitted":
                    continue
                ds = datetime.fromisoformat(o["date_created"]).astimezone(ML_TZ).date()
                for it in o.get("order_items", []):
                    item = it.get("item") or {}
                    out[(item.get("id"), item.get("variation_id"))][ds] += it.get("quantity", 0)
            offset += len(res)
            if not res or offset >= b["paging"]["total"]:
                break
    return out


def estoque(ml: ML, inv: str) -> dict:
    st, b = ml.get(f"/inventories/{inv}/stock/fulfillment")
    if st != 200 or not b:
        return {"erro": st}
    det = {(x.get("status") or "").lower().replace("_", ""): x.get("quantity", 0) for x in b.get("not_available_detail") or []}
    return {"disponivel": b.get("available_quantity", 0), "transferencia": det.get("transfer", 0),
            "outros_indisp": (b.get("not_available_quantity", 0) or 0) - det.get("transfer", 0), "total": b.get("total", 0)}


def calcular(conta: str, ate) -> list:
    cfg = ml_api.load_config()["contas"][conta]
    seller_id = str(cfg["seller_id"])
    ml = ML(conta)
    t0 = time.time()
    full = anuncios_full(ml, seller_id)
    vendas = vendas_por_item(ml, seller_id, ate)
    # MLBs vendidos que não estão entre os Full ativos (ex.: pausados que dividem inventário)
    faltam = sorted({k[0] for k in vendas if k[0] and k[0] not in full})
    extras = {k: v for k, v in detalhar(ml, faltam).items() if v.get("inventory_id") or
              any(x.get("inventory_id") for x in v.get("variations") or [])}
    todos = {**full, **extras}
    mapa = mapa_inventario(todos)

    inv = defaultdict(lambda: {"mlbs": set(), "titulo": "", "sku": "", "dias": defaultdict(int)})
    for (item_id, var_id), invid in mapa.items():
        it = todos[item_id]
        reg = inv[invid]
        reg["mlbs"].add(item_id)
        if not reg["titulo"] or item_id in full:
            reg["titulo"] = it.get("title", "")
            sku = it.get("seller_custom_field") or ""
            for v in it.get("variations") or []:
                if v.get("id") == var_id and v.get("seller_custom_field"):
                    sku = v["seller_custom_field"]
            reg["sku"] = reg["sku"] or sku

    # cada venda vai para o inventário da variação vendida; se não houver, para o do anúncio
    for (item_id, var_id), dias in vendas.items():
        invid = mapa.get((item_id, var_id)) or mapa.get((item_id, None))
        if not invid:
            continue  # venda de anúncio fora do Full
        for d, q in dias.items():
            inv[invid]["dias"][d] += q

    linhas = []
    for invid, reg in inv.items():
        if not any(m in full for m in reg["mlbs"]):
            continue  # inventário sem nenhum anúncio Full ativo
        est = estoque(ml, invid)
        un = {j: sum(q for d, q in reg["dias"].items() if d > ate - timedelta(days=j)) for j in (7, 15, 30)}
        med = {j: un[j] / j for j in un}
        tend = med[7] / med[30] if med[30] > 0 else (math.inf if med[7] > 0 else 1.0)
        demanda = sum(med[j] * p for j, p in PESOS.items())
        if tend > ALTA:
            demanda = max(demanda, *med.values())
        considerado = (est.get("disponivel") or 0) + (est.get("transferencia") or 0)
        cobertura = considerado / demanda if demanda > 0 else math.inf
        sugestao = max(0, math.ceil(META_DIAS * demanda - considerado))
        linhas.append({
            "conta": conta, "inventory_id": invid, "sku": reg["sku"], "titulo": reg["titulo"],
            "mlbs": ", ".join(sorted(reg["mlbs"])), "un_7d": un[7], "un_15d": un[15], "un_30d": un[30],
            "media_7d": round(med[7], 2), "media_15d": round(med[15], 2), "media_30d": round(med[30], 2),
            "tendencia": "alta" if tend > ALTA else "queda" if tend < QUEDA else "estável",
            "tend_7x30": None if math.isinf(tend) else round(tend, 2),
            "demanda_dia": round(demanda, 2), "disponivel": est.get("disponivel"),
            "transferencia": est.get("transferencia"), "outros_indisp": est.get("outros_indisp"),
            "estoque_considerado": considerado,
            "cobertura_dias": None if math.isinf(cobertura) else round(cobertura, 1),
            "sugestao_reposicao": sugestao, "erro_estoque": est.get("erro"),
        })
    print(f"{conta}: {len(full)} anúncios Full, {len(linhas)} inventários, "
          f"{sum(1 for l in linhas if l['sugestao_reposicao'] > 0)} com reposição sugerida ({time.time() - t0:.0f}s)")
    return linhas


def salvar_xlsx(linhas: list, caminho: str):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    wb = Workbook()
    ws = wb.active
    ws.title = "Reposição Full"
    cab = ["Conta", "Inventário", "SKU", "Produto", "MLBs", "Un. 7d", "Un. 15d", "Un. 30d",
           "Média/dia 7d", "Média/dia 15d", "Média/dia 30d", "Tendência", "7d ÷ 30d", "Demanda/dia",
           "Disponível", "Em transferência", "Outros indisp.", "Estoque considerado",
           "Cobertura (dias)", f"Sugestão p/ {META_DIAS} dias"]
    chaves = ["conta", "inventory_id", "sku", "titulo", "mlbs", "un_7d", "un_15d", "un_30d",
              "media_7d", "media_15d", "media_30d", "tendencia", "tend_7x30", "demanda_dia",
              "disponivel", "transferencia", "outros_indisp", "estoque_considerado",
              "cobertura_dias", "sugestao_reposicao"]
    ws.append(cab)
    for c in ws[1]:
        c.font = Font(bold=True)
    verm, amar = PatternFill("solid", fgColor="FCEBEB"), PatternFill("solid", fgColor="FDF4DC")
    for l in linhas:
        ws.append([l[k] for k in chaves])
        cob = l["cobertura_dias"]
        if cob is not None and cob < META_DIAS:
            for c in ws[ws.max_row]:
                c.fill = verm if cob < META_DIAS / 2 else amar
    larg = {"Produto": 50, "MLBs": 32}
    for i, h in enumerate(cab, 1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = larg.get(h, 14)
    ws.freeze_panes = "E2"
    wb.save(caminho)


if __name__ == "__main__":
    contas = sys.argv[1].split(",") if len(sys.argv) > 1 else ["meli01", "meli02", "meli03"]
    ate = datetime.now(ML_TZ).date() - timedelta(days=1)  # último dia fechado
    print(f"Janelas terminando em {ate} (fuso -04:00) | meta {META_DIAS} dias\n")
    linhas = []
    for conta in contas:
        linhas += calcular(conta, ate)
    ordem = lambda l: (l["cobertura_dias"] if l["cobertura_dias"] is not None else 1e9)
    linhas.sort(key=ordem)
    caminho = os.path.join(BASE_DIR, f"reposicao_full_{ate:%Y%m%d}.xlsx")
    salvar_xlsx(linhas, caminho)

    print(f"\n{'conta':6} {'inventário':11} {'produto':34} {'7d':>4} {'15d':>4} {'30d':>4} {'tend.':>7} "
          f"{'dem/d':>6} {'disp':>5} {'transf':>6} {'cob(d)':>7} {'repor':>6}")
    for l in linhas:
        if l["sugestao_reposicao"] <= 0:
            continue
        cob = "—" if l["cobertura_dias"] is None else f"{l['cobertura_dias']:.1f}"
        print(f"{l['conta']:6} {l['inventory_id']:11} {l['titulo'][:34]:34} {l['un_7d']:>4} {l['un_15d']:>4} "
              f"{l['un_30d']:>4} {l['tendencia']:>7} {l['demanda_dia']:>6.2f} {l['disponivel'] or 0:>5} "
              f"{l['transferencia'] or 0:>6} {cob:>7} {l['sugestao_reposicao']:>6}")
    print(f"\nPlanilha completa (todos os inventários): {caminho}")
