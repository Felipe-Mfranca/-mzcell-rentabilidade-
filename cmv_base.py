"""
cmv_base.py — Base central de CMV por SKU (uma só para todas as plataformas)
MZCell Rentabilidade

O arquivo de CMV do Grupo é importado uma vez (qualquer aba com colunas SKU e CMV/Custo, como o
"CMV_Todos_SKUs_MZcell_final.xlsx": lista, clones por produto e componentes) e vira cmv_base.json.
ML, Amazon (e Shopee, depois) consultam essa base pelo SKU, sem diferenciar maiúsculas de minúsculas.
Regra do projeto: o CMV vem da base de custo do Grupo, nunca do Bling.

Aplicar a base aos produtos é um passo separado e explícito (simulação por padrão), para nunca
sobrescrever custos sem conferência.
"""

import io
import json
import os
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(BASE_DIR, "cmv_base.json")
PRODUTOS_PATH = os.path.join(BASE_DIR, "produtos.json")


def _num(v):
    if v is None or str(v).strip() == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    t = str(v).replace("R$", "").strip()
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def _cabecalho(ws):
    """Linha de cabeçalho com uma coluna 'sku...' e uma 'cmv'/'custo'. Devolve (linha, {campo: coluna})."""
    for row in ws.iter_rows(min_row=1, max_row=10):
        m = {}
        for cell in row:
            t = str(cell.value or "").lower().strip()
            if t.startswith("sku") and "sku" not in m:
                m["sku"] = cell.column
            elif ("cmv" in t or "custo" in t) and "cmv" not in m:
                m["cmv"] = cell.column
            elif t.startswith("descri") or t.startswith("produto") or t.startswith("título") or t.startswith("titulo"):
                m.setdefault("desc", cell.column)
            elif t == "tipo":
                m["tipo"] = cell.column
        if "sku" in m and "cmv" in m:
            return row[0].row, m
    return None, {}


def ler_planilha(conteudo: bytes) -> dict:
    """Lê todas as abas e devolve {'itens': {SKU_MAIUSCULO: {...}}, 'conflitos': [...], 'ignorados': [...], 'abas': [...]}.
    Em SKU repetido com CMV diferente vale a primeira aba/linha e o conflito é informado."""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(conteudo), data_only=True)
    itens, conflitos, ignorados, abas = {}, [], [], []
    for ws in wb.worksheets:
        cab, col = _cabecalho(ws)
        if not cab:
            continue
        lidas = 0
        for n, row in enumerate(ws.iter_rows(min_row=cab + 1, values_only=True), start=cab + 1):
            sku = str(row[col["sku"] - 1] or "").strip()
            if not sku:
                continue
            cmv = _num(row[col["cmv"] - 1])
            if cmv is None or cmv < 0:
                ignorados.append({"sku": sku, "aba": ws.title, "motivo": "CMV vazio ou inválido"})
                continue
            chave = sku.upper()
            if chave in itens:
                if abs(itens[chave]["cmv"] - cmv) > 0.001:
                    conflitos.append({"sku": sku, "cmv_mantido": itens[chave]["cmv"], "cmv_ignorado": cmv, "aba": ws.title})
                continue
            itens[chave] = {"sku": sku, "cmv": round(cmv, 4), "aba": ws.title,
                            "tipo": str(row[col["tipo"] - 1] or "") if "tipo" in col else "",
                            "descricao": str(row[col["desc"] - 1] or "") if "desc" in col else ""}
            lidas += 1
        abas.append({"aba": ws.title, "skus_lidos": lidas})
    return {"itens": itens, "conflitos": conflitos, "ignorados": ignorados, "abas": abas}


def salvar(itens: dict, arquivo: str = ""):
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"_atualizado_em": datetime.now().isoformat(timespec="seconds"), "_arquivo": arquivo, "itens": itens},
                  f, ensure_ascii=False, indent=1)
    os.replace(tmp, PATH)


def carregar() -> dict:
    try:
        with open(PATH, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"_atualizado_em": None, "_arquivo": "", "itens": {}}


def cmv_de(sku, base: dict = None):
    """CMV unitário do SKU (ou None)."""
    itens = (base if base is not None else carregar())["itens"]
    x = itens.get(str(sku or "").strip().upper())
    return x["cmv"] if x else None


def importar(conteudo: bytes, arquivo: str = "") -> dict:
    r = ler_planilha(conteudo)
    if not r["itens"]:
        raise ValueError("Nenhuma aba com colunas SKU e CMV/Custo foi encontrada.")
    salvar(r["itens"], arquivo)
    return {"success": True, "skus": len(r["itens"]), "abas": r["abas"], "conflitos": r["conflitos"], "ignorados": r["ignorados"]}


# ─── aplicação às plataformas ────────────────────────────────────────────────

def aliquotas_da_conta(conta: str) -> tuple:
    """(COM ST, SEM ST) em fração, de config.json > impostos (a mesma fonte do ML e da Amazon)."""
    try:
        with open(os.path.join(BASE_DIR, "config.json"), encoding="utf-8") as f:
            imp = json.load(f).get("impostos", {})
    except (FileNotFoundError, ValueError):
        imp = {}
    return imp.get(f"{conta}_com_st", {}).get("aliquota", 0.10), imp.get(f"{conta}_sem_st", {}).get("aliquota", 0.08)


def aplicar_ml(conta: str, confirmar: bool = False, produtos_path: str = None, imposto: bool = False,
               aliquotas: tuple = None) -> dict:
    """Atualiza cmv_unit dos produtos ML da conta cujo SKU está na base. Com imposto=True aplica também a alíquota
    da conta conforme o produto seja COM ST (padrão) ou SEM ST. Simula se confirmar=False."""
    path = produtos_path or PRODUTOS_PATH
    with open(path, encoding="utf-8") as f:
        produtos = json.load(f)
    base = carregar()
    com, sem = aliquotas or aliquotas_da_conta(conta)
    muda, igual, sem_sku, sem_cmv, imp_muda = [], 0, 0, [], 0
    for mlb, p in produtos.items():
        if p.get("conta") != conta:
            continue
        if imposto:
            tem = p.get("tem_st", True)
            pct = round(com if tem else sem, 6)
            if abs((p.get("imposto_pct") or 0) - pct) > 1e-9 or "tem_st" not in p:
                imp_muda += 1
                if confirmar:
                    p["imposto_pct"], p["tem_st"] = pct, tem
        if not p.get("sku"):
            sem_sku += 1
            continue
        novo = cmv_de(p["sku"], base)
        if novo is None:
            sem_cmv.append({"mlb": mlb, "sku": p["sku"]})
        elif abs((p.get("cmv_unit") or 0) - novo) < 0.0001:
            igual += 1
        else:
            muda.append({"mlb": mlb, "sku": p["sku"], "de": p.get("cmv_unit") or 0, "para": novo})
            if confirmar:
                p["cmv_unit"] = round(novo, 4)
    if confirmar and (muda or imp_muda):
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(produtos, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    return {"conta": conta, "plataforma": "ml", "aplicado": bool(confirmar and (muda or imp_muda)), "mudariam": len(muda),
            "imposto_mudaria": imp_muda, "ja_iguais": igual,
            "sem_sku": sem_sku, "sem_cmv_na_base": len(sem_cmv), "alteracoes": muda[:200], "pendentes_sem_cmv": sem_cmv[:200]}


def aplicar_amazon(conta: str, tem_st_padrao: bool = None, confirmar: bool = False, custos_path: str = None,
                   aliquotas: tuple = None, skus: list = None) -> dict:
    """Grava cmv_unit em amazon_custos.json para os SKUs da Amazon que estão na base. O imposto vem do que já está
    cadastrado; SKU novo precisa de tem_st_padrao (COM ST = True, SEM ST = False) para receber a alíquota da conta."""
    from marketplaces.amazon import custos_xlsx, rentabilidade
    path = custos_path or rentabilidade.CUSTOS_PATH
    try:
        with open(path, encoding="utf-8") as f:
            dados = json.load(f)
    except FileNotFoundError:
        dados = {}
    com, sem = aliquotas or custos_xlsx.aliquotas_da_conta(conta)
    base = carregar()
    lista = skus if skus is not None else [x["sku"] for x in custos_xlsx.skus_da_conta(conta)]
    mine = dados.setdefault(conta, {})
    ind = {k.upper(): k for k in mine}
    muda, sem_imposto, sem_cmv = [], [], []
    for sku in lista:
        novo = cmv_de(sku, base)
        if novo is None:
            sem_cmv.append(sku)
            continue
        chave = ind.get(sku.upper(), sku)
        atual = dict(mine.get(chave) or {})
        if atual.get("imposto_pct") is None:
            if tem_st_padrao is None:
                sem_imposto.append(sku)
                continue
            atual["tem_st"] = bool(tem_st_padrao)
            atual["imposto_pct"] = round(com if tem_st_padrao else sem, 6)
        if atual.get("cmv_unit") != round(novo, 4) or chave not in mine:
            muda.append({"sku": sku, "de": (mine.get(chave) or {}).get("cmv_unit"), "para": novo})
            atual["cmv_unit"] = round(novo, 4)
            mine[chave] = atual
    if confirmar and muda:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(dados, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    return {"conta": conta, "plataforma": "amazon", "aplicado": bool(confirmar and muda), "mudariam": len(muda),
            "sem_imposto_definido": sem_imposto, "sem_cmv_na_base": sem_cmv, "alteracoes": muda[:200]}
