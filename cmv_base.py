"""
cmv_base.py — Base central de CMV por SKU (uma só para todas as plataformas)
MZ App

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
            elif t.startswith("data"):
                m.setdefault("data", cell.column)
        if "sku" in m and "cmv" in m:
            return row[0].row, m
    return None, {}


def _data_iso(v) -> str:
    """Data da planilha em ISO (aceita data do Excel, texto dd/mm/aaaa ou ISO). Vazio -> agora."""
    from datetime import date
    if isinstance(v, datetime):
        return v.isoformat(timespec="seconds")
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day).isoformat(timespec="seconds")
    t = str(v or "").strip()
    if not t:
        return datetime.now().isoformat(timespec="seconds")
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(t[:19], fmt).isoformat(timespec="seconds")
        except ValueError:
            continue
    return datetime.now().isoformat(timespec="seconds")


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
                            "descricao": str(row[col["desc"] - 1] or "") if "desc" in col else "",
                            "atualizado_em": _data_iso(row[col["data"] - 1]) if "data" in col else ""}
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


def importar(conteudo: bytes, arquivo: str = "", autor: str = "") -> dict:
    r = ler_planilha(conteudo)
    if not r["itens"]:
        raise ValueError("Nenhuma aba com colunas SKU e CMV/Custo foi encontrada.")
    base = carregar().get("itens", {})
    base.update(r["itens"])            # soma à base: o arquivo atualiza os SKUs que traz e mantém os demais
    salvar(base, arquivo)
    publicar(r["itens"], autor)
    propagado = propagar_ml()          # a planilha já vale nos produtos: nenhum cache de CMV fica para trás
    return {"success": True, "skus": len(r["itens"]), "abas": r["abas"], "conflitos": r["conflitos"],
            "ignorados": r["ignorados"], "propagado_ml": propagado}


CONTAS_ML = ("meli01", "meli02", "meli03")


def propagar_ml() -> dict:
    """Grava o CMV da base em cmv_unit de todos os produtos ML cujo SKU está na base (só CMV; imposto fica como está)."""
    resultado = {}
    for conta in CONTAS_ML:
        try:
            resultado[conta] = aplicar_ml(conta, True)
        except Exception as e:
            resultado[conta] = {"erro": str(e)[:120]}
    return resultado


def publicar(itens: dict, autor: str = ""):
    """Sobe a base de CMV para o banco compartilhado (uma chave por SKU). Não trava quem chamou."""
    try:
        import shared_store
        shared_store.enviar_lote_async("cmv", [(k, v) for k, v in itens.items()], autor)
    except Exception:
        pass


def aplicar_remoto(registros: list):
    """Aplica CMVs vindos do banco compartilhado. Vale a data mais nova de cada SKU."""
    base = carregar()
    itens = base.get("itens", {})
    mudou = 0
    for r in registros:
        if r.get("removido"):
            continue
        try:
            sku = str(r["chave"]).strip().upper()
            d = json.loads(r["dados"]) if r.get("dados") else {}
        except (ValueError, KeyError):
            continue
        if "cmv" not in d:
            continue
        atual = itens.get(sku)
        if atual is None or (d.get("atualizado_em") or "") >= (atual.get("atualizado_em") or ""):
            itens[sku] = d
            mudou += 1
    if mudou:
        salvar(itens, base.get("_arquivo", ""))
    if registros:
        propagar_ml()                  # sempre que a base chega, os produtos e as margens salvas acompanham
    return mudou


# ─── aplicação às plataformas ────────────────────────────────────────────────

def aliquotas_da_conta(conta: str) -> tuple:
    """(COM ST, SEM ST) em fração: tabela de impostos (impostos.json); sem cadastro, config.json > impostos."""
    import impostos
    cadastrada = impostos.aliquotas(conta)
    if cadastrada:
        return cadastrada
    try:
        with open(os.path.join(BASE_DIR, "config.json"), encoding="utf-8") as f:
            imp = json.load(f).get("impostos", {})
    except (FileNotFoundError, ValueError):
        imp = {}
    return imp.get(f"{conta}_com_st", {}).get("aliquota", 0.10), imp.get(f"{conta}_sem_st", {}).get("aliquota", 0.08)


def _refazer_margens_dias(p: dict) -> int:
    """Recalcula CMV e margem de cada dia salvo do produto com o cmv_unit atual (mesma fórmula do sync).
    Assim o modal, a faixa de 30 dias e o período lêem o mesmo CMV, mesmo sem novo sync. Devolve dias alterados."""
    import parser_rentabilidade as parser
    alterados = 0
    for dia, vals in (p.get("dias") or {}).items():
        if dia == "_ads_":
            continue
        m = parser.calcular_margem(
            receita=vals.get("receita", 0.0), cmv_unit=p.get("cmv_unit") or 0.0,
            unidades=vals.get("unidades", 0), imposto_pct=p.get("imposto_pct", 0.10),
            comissao_pct=p.get("comissao_pct", 0.11), frete_unit=p.get("frete_unit", 0.0),
            ads_total=vals.get("ads_total", 0.0), rebot_unit=p.get("rebot_unit", 0.0))
        if any(abs((vals.get(k) or 0) - m[k]) > 0.005 for k in ("cmv_total", "margem_total")):
            alterados += 1
        vals.update({k: m[k] for k in ("cmv_total", "imposto_total", "comissao_total", "frete_total",
                                       "margem_total", "margem_pct", "margem_unit")})
    return alterados


def aplicar_ml(conta: str, confirmar: bool = False, produtos_path: str = None, imposto: bool = False,
               aliquotas: tuple = None) -> dict:
    """Atualiza cmv_unit dos produtos ML da conta cujo SKU está na base. Com imposto=True aplica também a alíquota
    da conta conforme o produto seja COM ST (padrão) ou SEM ST. Simula se confirmar=False."""
    path = produtos_path or PRODUTOS_PATH
    import produtos_store
    produtos = produtos_store.ler() if produtos_path is None else json.load(open(path, encoding="utf-8"))
    tocados = set()
    base = carregar()
    com, sem = aliquotas or aliquotas_da_conta(conta)
    muda, igual, sem_sku, sem_cmv, imp_muda, dias_refeitos = [], 0, 0, [], 0, 0
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
            continue
        if abs((p.get("cmv_unit") or 0) - novo) < 0.0001:
            igual += 1
        else:
            muda.append({"mlb": mlb, "sku": p["sku"], "de": p.get("cmv_unit") or 0, "para": novo})
        if confirmar:
            p["cmv_unit"] = round(novo, 4)
            dias_refeitos += _refazer_margens_dias(p)
            tocados.add(mlb)
    if confirmar and (muda or imp_muda or dias_refeitos):
        if produtos_path is None:      # produtos.json real: grava só os alterados, sob trava
            produtos_store.mesclar({m: produtos[m] for m in tocados})
        else:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(produtos, f, indent=2, ensure_ascii=False)
    return {"conta": conta, "plataforma": "ml", "aplicado": bool(confirmar and (muda or imp_muda or dias_refeitos)), "mudariam": len(muda),
            "imposto_mudaria": imp_muda, "dias_refeitos": dias_refeitos, "ja_iguais": igual,
            "sem_sku": sem_sku, "sem_cmv_na_base": len(sem_cmv), "alteracoes": muda[:200], "pendentes_sem_cmv": sem_cmv[:200]}


ASIN_PATH = os.path.join(BASE_DIR, "amazon_asin_skus.json")


def salvar_asin(conta: str, linhas: list):
    """Grava o mapa SKU -> ASIN e ASIN -> SKUs da conta a partir do relatório de listagens da Amazon."""
    try:
        dados = json.load(open(ASIN_PATH, encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        dados = {}
    sku_asin, asin_skus = {}, {}
    for x in linhas:
        sku, asin = (x.get("seller-sku") or "").strip(), (x.get("product-id") or "").strip()
        if not sku or not asin:
            continue
        sku_asin[sku] = asin
        asin_skus.setdefault(asin, [])
        if sku not in asin_skus[asin]:
            asin_skus[asin].append(sku)
    dados[conta] = {"sku_asin": sku_asin, "asin_skus": asin_skus, "gerado_em": datetime.now().isoformat(timespec="seconds")}
    tmp = ASIN_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=1)
    os.replace(tmp, ASIN_PATH)


def cmv_amazon(conta: str, sku: str, base: dict = None):
    """CMV do SKU da Amazon: o próprio SKU na base; se não tiver, o de outro SKU vinculado ao mesmo ASIN (regra do usuário)."""
    direto = cmv_de(sku, base)
    if direto is not None:
        return direto, "sku"
    try:
        m = json.load(open(ASIN_PATH, encoding="utf-8")).get(conta, {})
    except (FileNotFoundError, ValueError):
        return None, None
    asin = m.get("sku_asin", {}).get(sku)
    for irmao in m.get("asin_skus", {}).get(asin, []) if asin else []:
        if irmao != sku:
            v = cmv_de(irmao, base)
            if v is not None:
                return v, f"asin:{irmao}"
    return None, None


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
        novo, _origem = cmv_amazon(conta, sku, base)
        if novo is None:
            sem_cmv.append(sku)
            continue
        chave = ind.get(sku.upper(), sku)
        atual = dict(mine.get(chave) or {})
        # CMV não depende de ST: sempre grava. A alíquota só é preenchida quando o SKU já tem ST definido
        # ou o padrão foi informado; senão fica para o seletor COM ST / SEM ST do simulador.
        if atual.get("imposto_pct") is None:
            if tem_st_padrao is None:
                sem_imposto.append(sku)
            else:
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
            "sem_st_definido": sem_imposto, "sem_cmv_na_base": sem_cmv, "alteracoes": muda[:200]}
