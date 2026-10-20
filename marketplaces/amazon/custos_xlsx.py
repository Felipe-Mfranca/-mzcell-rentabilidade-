"""
marketplaces/amazon/custos_xlsx.py — Modelo e importação de CMV e imposto por SKU (Amazon)
MZCell Rentabilidade

Mesmo fluxo do Mercado Livre: baixa-se um modelo .xlsx pré-preenchido, preenche-se o Preço de Custo e o
Imposto (COM ST ou SEM ST) e importa-se de volta. As alíquotas COM ST e SEM ST ficam em B2 e D2 da planilha
(editáveis). O resultado vai para amazon_custos.json: {"<conta>": {"<SKU>": {"cmv_unit":..., "imposto_pct":...,
"tem_st":...}}}, o mesmo arquivo que rentabilidade.py já lê. Regras: o CMV vem da base de custo do Grupo
(nunca do Bling); a importação mescla com o que já existe (só atualiza os SKUs da planilha).
"""

import io
import json
import os
from datetime import datetime

from marketplaces.amazon import api, rentabilidade

ALIQ_COM_ST_PADRAO = 0.10
ALIQ_SEM_ST_PADRAO = 0.08
HEADERS = ["SKU", "ASIN", "Título", "Preço de Custo", "Imposto"]


def _ler_custos_arquivo() -> dict:
    try:
        with open(rentabilidade.CUSTOS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def _gravar_custos_arquivo(dados: dict):
    tmp = rentabilidade.CUSTOS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)
    os.replace(tmp, rentabilidade.CUSTOS_PATH)


def aliquotas_da_conta(conta: str) -> tuple:
    """(COM ST, SEM ST) em fração, de config.json > impostos > <conta>_com_st / <conta>_sem_st (mesma fonte do ML)."""
    try:
        with open(os.path.join(api.BASE_DIR, "config.json"), encoding="utf-8") as f:
            imp = json.load(f).get("impostos", {})
    except (FileNotFoundError, ValueError):
        imp = {}
    return (imp.get(f"{conta}_com_st", {}).get("aliquota", ALIQ_COM_ST_PADRAO),
            imp.get(f"{conta}_sem_st", {}).get("aliquota", ALIQ_SEM_ST_PADRAO))


def skus_da_conta(conta: str) -> list:
    """[{sku, asin, titulo}] do estoque FBA (um por SKU) mais os SKUs que já têm custo cadastrado."""
    c = api.conta_cfg(conta)
    mk = c.get("marketplace_id") or api.MARKETPLACE_BR
    descont = set(c.get("asins_descontinuados", []))
    achados, pagina = {}, None
    while True:
        params = {"granularityType": "Marketplace", "granularityId": mk, "marketplaceIds": mk, "details": "true"}
        if pagina:
            params["nextToken"] = pagina
        st, corpo = api.get(conta, "/fba/inventory/v1/summaries", **params)
        if st != 200:
            raise api.falha(st, corpo)
        pl = corpo.get("payload", {})
        for s in pl.get("inventorySummaries", []):
            sku = s.get("sellerSku")
            if sku and s.get("asin") not in descont:
                achados[sku] = {"sku": sku, "asin": s.get("asin", ""), "titulo": s.get("productName", "")}
        pagina = (pl.get("pagination") or {}).get("nextToken")
        if not pagina:
            break
    for sku in rentabilidade.carregar_custos(conta):
        achados.setdefault(sku, {"sku": sku, "asin": "", "titulo": ""})
    return sorted(achados.values(), key=lambda x: (x["titulo"] or x["sku"]).lower())


def gerar_modelo(conta: str, skus: list = None) -> bytes:
    """Bytes do .xlsx do modelo, pré-preenchido com os custos atuais."""
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill

    skus = skus if skus is not None else skus_da_conta(conta)
    atuais = rentabilidade.carregar_custos(conta)
    com_st, sem_st = aliquotas_da_conta(conta)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Importação"
    amarelo = PatternFill("solid", fgColor="FFF176")
    escuro = PatternFill("solid", fgColor="1A1A24")
    cinza = PatternFill("solid", fgColor="EEEEEE")

    ws.merge_cells("A1:E1")
    ws["A1"].value = f"Modelo de Importação — MZ Rentabilidade · {conta}"
    ws["A1"].font = Font(bold=True, size=13)
    ws["A1"].alignment = Alignment(horizontal="center", vertical="center")
    ws["A2"].value = "Alíquota COM ST (%):"
    ws["B2"].value = round(com_st * 100, 4)
    ws["C2"].value = "|   Alíquota SEM ST (%):"
    ws["D2"].value = round(sem_st * 100, 4)
    for ref in ("B2", "D2"):
        ws[ref].fill = amarelo
        ws[ref].font = Font(bold=True, size=10)
        ws[ref].alignment = Alignment(horizontal="center")
    ws.merge_cells("A3:E3")
    ws["A3"].value = "  Imposto: COM ST ou SEM ST  ·  Preço de Custo em R$ por unidade (ex: 20,50)  ·  Todos os campos são obrigatórios"
    ws["A3"].fill = cinza
    ws["A3"].font = Font(italic=True, size=9, color="9E9E9E")
    for i, h in enumerate(HEADERS, start=1):
        c = ws.cell(row=4, column=i, value=h)
        c.fill, c.font, c.alignment = escuro, Font(bold=True, color="FFFFFF", size=10), Alignment(horizontal="center")
    for r, x in enumerate(skus, start=5):
        at = atuais.get(x["sku"]) or {}
        ws.cell(row=r, column=1, value=x["sku"])
        ws.cell(row=r, column=2, value=x.get("asin", ""))
        ws.cell(row=r, column=3, value=x.get("titulo", ""))
        ws.cell(row=r, column=4, value=at.get("cmv_unit") if at.get("cmv_unit") is not None else "")
        if at.get("imposto_pct") is not None:
            ws.cell(row=r, column=5, value="COM ST" if at.get("tem_st", at["imposto_pct"] >= com_st - 1e-9) else "SEM ST")
    for letra, larg in zip("ABCDE", (26, 14, 56, 16, 14)):
        ws.column_dimensions[letra].width = larg
    ws.freeze_panes = "A5"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _aliq(v):
    try:
        v = float(v)
        return v / 100 if v > 1 else v
    except (TypeError, ValueError):
        return None


def importar(conta: str, conteudo: bytes) -> dict:
    """Lê o modelo e grava os custos. Devolve {atualizados, novos, nao_encontrados, aliquotas}."""
    import openpyxl
    try:
        wb = openpyxl.load_workbook(io.BytesIO(conteudo), data_only=True)
    except Exception as e:
        raise api.ErroAmazon(f"Erro ao ler o xlsx: {e}", 400)
    ws = wb[wb.sheetnames[0]]
    com_st = _aliq(ws["B2"].value) if _aliq(ws["B2"].value) is not None else ALIQ_COM_ST_PADRAO
    sem_st = _aliq(ws["D2"].value) if _aliq(ws["D2"].value) is not None else ALIQ_SEM_ST_PADRAO

    cab, col = None, {}
    for row in ws.iter_rows(min_row=1, max_row=10):
        m = {}
        for cell in row:
            v = str(cell.value or "").lower().strip()
            if v == "sku":
                m["sku"] = cell.column
            elif "custo" in v or "cmv" in v:
                m["cmv"] = cell.column
            elif "imposto" in v:
                m["imposto"] = cell.column
        if "sku" in m and "cmv" in m:
            cab, col = cell.row, m
            break
    if not cab:
        raise api.ErroAmazon("Cabeçalho não encontrado (precisa de SKU e Preço de Custo).", 400)

    dados = _ler_custos_arquivo()
    mine = dados.setdefault(conta, {})
    conhecidos = {s.upper(): s for s in mine}
    atualizados, novos, rejeitados = [], [], []
    for n, row in enumerate(ws.iter_rows(min_row=cab + 1, values_only=True), start=cab + 1):
        if not any(v is not None and str(v).strip() != "" for v in row):
            continue
        sku = str(row[col["sku"] - 1] or "").strip()
        if not sku:
            rejeitados.append({"id": f"linha {n}", "motivo": "SKU vazio"})
            continue
        bruto = row[col["cmv"] - 1]
        imp = str(row[col["imposto"] - 1] or "").strip().upper() if "imposto" in col else ""
        faltam = []
        if bruto is None or str(bruto).strip() == "":
            faltam.append("Preço de Custo")
        if imp not in ("COM ST", "SEM ST"):
            faltam.append("Imposto (COM ST ou SEM ST)")
        if faltam:
            rejeitados.append({"id": sku, "motivo": "campo(s) obrigatório(s): " + ", ".join(faltam)})
            continue
        try:
            cmv = float(str(bruto).replace("R$", "").replace(".", "").replace(",", ".")) if isinstance(bruto, str) else float(bruto)
        except ValueError:
            rejeitados.append({"id": sku, "motivo": f"Preço de Custo inválido: {bruto}"})
            continue
        if cmv < 0:
            rejeitados.append({"id": sku, "motivo": "Preço de Custo negativo"})
            continue
        chave = conhecidos.get(sku.upper(), sku)
        (atualizados if chave in mine else novos).append(chave)
        mine[chave] = {"cmv_unit": round(cmv, 4), "imposto_pct": round(com_st if imp == "COM ST" else sem_st, 6), "tem_st": imp == "COM ST"}
    dados.setdefault("_aliquotas", {})[conta] = {"com_st": com_st, "sem_st": sem_st}
    dados["_atualizado_em"] = datetime.now().isoformat(timespec="seconds")
    if atualizados or novos:
        _gravar_custos_arquivo(dados)
    return {"success": True, "conta": conta, "atualizados": len(atualizados), "novos": len(novos),
            "nao_aceitos": rejeitados, "aliquotas": {"com_st": com_st, "sem_st": sem_st}}
