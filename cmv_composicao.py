"""
cmv_composicao.py — CMV amarrado às composições dos Blings (kits e clones)
MZ App

O CMV é universal por SKU (cmv_base.json, o mesmo nas três empresas). Kit e clone não têm custo próprio:
valem a soma dos componentes do Bling (quantidade × CMV de cada um). Por isso o ajuste é sempre feito no
componente; ao salvar, todo kit/clone que usa aquele componente (direta ou indiretamente) é recalculado.

A composição vem do catálogo do balanço (balanco_catalogo.json, lido dos três Blings e renovado a cada 24 h).
Se o mesmo kit tiver composição diferente entre os Blings, ele não é recalculado e a divergência é avisada.
"""

from datetime import datetime

import cmv_base


def _chave(sku) -> str:
    return str(sku or "").strip().upper()


def _catalogo() -> dict:
    """{KIT: {bling: [(COMPONENTE, qtd), ...]}} e a data da leitura. Dispara a renovação se passou de 24 h."""
    from marketplaces.balanco import catalogo
    try:
        catalogo.atualizar()
    except Exception:
        pass
    cat = catalogo.ler()
    kits = {}
    for b, dados in (cat.get("blings") or {}).items():
        for kit, x in (dados.get("kits") or {}).items():
            kits.setdefault(_chave(kit), {})[b] = [(_chave(c), float(q or 1)) for c, q in x.get("comp") or []]
    return {"kits": kits, "gerado_em": cat.get("gerado_em")}


def _comp_unica(kits: dict, sku: str):
    """(componentes, diverge): a composição do kit; diverge=True quando os Blings não concordam."""
    por_bling = kits.get(sku)
    if not por_bling:
        return None, False
    formas = {tuple(sorted(c)) for c in por_bling.values()}
    return next(iter(por_bling.values())), len(formas) > 1


def cmv_calculado(sku: str, kits: dict, itens: dict, _visitados=()):
    """CMV do SKU: kit/clone = soma dos componentes (recursivo); simples = valor da base. None se faltar algum."""
    comp, diverge = _comp_unica(kits, sku)
    if comp is None or sku in _visitados:
        x = itens.get(sku)
        return x["cmv"] if x else None
    if diverge:
        return None
    total = 0.0
    for c, q in comp:
        v = cmv_calculado(c, kits, itens, _visitados + (sku,))
        if v is None:
            return None
        total += q * v
    return round(total, 4)


def composicao(sku: str) -> dict:
    """Componentes do SKU nos Blings, com o CMV de cada um. Tipo: simples, clone (1 componente × 1) ou kit."""
    sku = _chave(sku)
    cat = _catalogo()
    kits, itens = cat["kits"], cmv_base.carregar().get("itens", {})
    comp, diverge = _comp_unica(kits, sku)
    base_kit = itens.get(sku, {}).get("cmv")
    if comp is None:
        return {"sku": sku, "tipo": "simples", "cmv": base_kit, "componentes": [], "catalogo_em": cat["gerado_em"]}
    linhas = [{"sku": c, "qtd": q, "cmv": cmv_calculado(c, kits, itens), "e_kit": c in kits,
               "descricao": (itens.get(c) or {}).get("descricao", "")} for c, q in comp]
    return {"sku": sku, "tipo": "clone" if len(comp) == 1 and comp[0][1] == 1 else "kit",
            "blings": sorted(kits[sku]), "diverge": diverge,
            "composicoes": {b: [{"sku": c, "qtd": q} for c, q in v] for b, v in kits[sku].items()} if diverge else None,
            "componentes": linhas, "cmv_base_kit": base_kit, "cmv": cmv_calculado(sku, kits, itens),
            "catalogo_em": cat["gerado_em"]}


def _quem_usa(kits: dict, sku: str) -> list:
    """Kits que contêm o SKU, direta ou indiretamente (o mais interno primeiro)."""
    pais = {}
    for kit, por_bling in kits.items():
        for comp in por_bling.values():
            for c, _q in comp:
                pais.setdefault(c, set()).add(kit)
    ordem, fila = [], [sku]
    while fila:
        for k in sorted(pais.get(fila.pop(0), ())):
            if k not in ordem:
                ordem.append(k)
                fila.append(k)
    return ordem


def preencher_kits(confirmar: bool = False, autor: str = "") -> dict:
    """Kits/clones sem CMV na base cujos componentes já têm CMV recebem a soma. Não mexe em kit que já tem valor.
    Simula se confirmar=False."""
    kits = _catalogo()["kits"]
    base = cmv_base.carregar()
    itens = base.get("itens", {})
    agora = datetime.now().isoformat(timespec="seconds")
    novos = {}
    for kit in sorted(kits):
        if kit in itens:
            continue
        v = cmv_calculado(kit, kits, itens)
        if v is not None:
            novos[kit] = {"sku": kit, "cmv": v, "aba": "soma dos componentes", "tipo": "", "descricao": "",
                          "atualizado_em": agora, "autor": autor[:40]}
    if confirmar and novos:
        itens.update(novos)
        cmv_base.salvar(itens, base.get("_arquivo", ""))
        cmv_base.publicar(novos, autor)
        cmv_base.propagar_ml()
    return {"aplicado": bool(confirmar and novos), "kits": [{"sku": k, "cmv": v["cmv"]} for k, v in novos.items()]}


def _recalcular(kits: dict, itens: dict, skus, agora: str, autor: str):
    """Recalcula, dentro de itens, os kits que usam algum dos SKUs. Devolve (mudados, kits_ok, kits_pendentes)."""
    mudados, kits_ok, kits_pendentes, vistos = {}, [], [], set()
    for sku in skus:
        for kit in _quem_usa(kits, sku):
            if kit in vistos:
                continue
            vistos.add(kit)
            novo = cmv_calculado(kit, kits, itens)
            if novo is None:
                kits_pendentes.append({"sku": kit, "motivo": "composição diferente entre Blings" if _comp_unica(kits, kit)[1]
                                       else "componente sem CMV"})
                continue
            velho = (itens.get(kit) or {}).get("cmv")
            if velho is None or abs(velho - novo) > 0.0001:
                itens[kit] = dict(itens.get(kit) or {"sku": kit, "tipo": "", "descricao": ""},
                                  cmv=novo, aba="soma dos componentes", atualizado_em=agora, autor=autor[:40])
                mudados[kit] = itens[kit]
            kits_ok.append({"sku": kit, "de": velho, "para": novo})
    return mudados, kits_ok, kits_pendentes


def recalcular_kits(skus, autor: str = "") -> dict:
    """Depois de importar CMV de componentes: os kits/clones que os usam passam a valer a soma (quando completa)."""
    kits = _catalogo()["kits"]
    base = cmv_base.carregar()
    itens = base.get("itens", {})
    mudados, kits_ok, pendentes = _recalcular(kits, itens, [_chave(s) for s in skus if _chave(s) not in kits],
                                              datetime.now().isoformat(timespec="seconds"), autor)
    if mudados:
        cmv_base.salvar(itens, base.get("_arquivo", ""))
        cmv_base.publicar(mudados, autor)
        cmv_base.propagar_ml()
    return {"recalculados": len(mudados), "kits_pendentes": len(pendentes)}


def salvar_cmv(sku: str, cmv: float, autor: str = "") -> dict:
    """Grava o CMV do SKU (componente ou produto simples) e recalcula os kits/clones que o usam."""
    sku = _chave(sku)
    if cmv is None or cmv < 0:
        raise ValueError("CMV inválido")
    cat = _catalogo()
    kits = cat["kits"]
    if sku in kits:
        raise ValueError("Este SKU é kit/clone no Bling: ajuste o CMV nos componentes.")
    base = cmv_base.carregar()
    itens = base.get("itens", {})
    agora = datetime.now().isoformat(timespec="seconds")
    antes = (itens.get(sku) or {}).get("cmv")
    itens[sku] = dict(itens.get(sku) or {"sku": sku, "tipo": "", "descricao": ""},
                      cmv=round(float(cmv), 4), aba="ajuste no MZ App", atualizado_em=agora, autor=autor[:40])
    mudados, kits_ok, kits_pendentes = _recalcular(kits, itens, [sku], agora, autor)
    mudados[sku] = itens[sku]
    cmv_base.salvar(itens, base.get("_arquivo", ""))
    cmv_base.publicar(mudados, autor)
    propagado = cmv_base.propagar_ml()
    return {"success": True, "sku": sku, "de": antes, "para": itens[sku]["cmv"], "kits": kits_ok,
            "kits_pendentes": kits_pendentes,
            "anuncios_ml": {c: (r.get("mudariam") if isinstance(r, dict) else None) for c, r in propagado.items()}}


def planilha_carga() -> bytes:
    """Planilha para a carga inicial: aba CMV com todos os SKUs que não são kit (componentes e produtos simples)
    dos três Blings e das plataformas, com o CMV atual para conferir; aba de kits só para consulta."""
    import io
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    from marketplaces.balanco import catalogo
    cat = catalogo.ler()
    kits = _catalogo()["kits"]
    itens = cmv_base.carregar().get("itens", {})
    nomes, usado_em = {}, {}
    for dados in (cat.get("blings") or {}).values():
        for s, x in (dados.get("skus") or {}).items():
            nomes.setdefault(_chave(s), x.get("nome") or "")
    for kit, por_bling in kits.items():
        for comp in por_bling.values():
            for c, _q in comp:
                usado_em.setdefault(c, set()).add(kit)
    try:
        import produtos_store
        for p in produtos_store.ler().values():
            if p.get("sku"):
                nomes.setdefault(_chave(p["sku"]), p.get("titulo") or "")
    except Exception:
        pass
    skus = (set(nomes) | set(usado_em) | set(itens)) - set(kits)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "CMV"
    ws.append(["SKU", "Descrição", "CMV (R$)", "Usado nos kits/clones"])
    for s in sorted(skus, key=lambda s: (s not in usado_em, s)):
        ws.append([s, nomes.get(s) or (itens.get(s) or {}).get("descricao", ""), (itens.get(s) or {}).get("cmv"),
                   ", ".join(sorted(usado_em.get(s, ())))[:500]])
    k = wb.create_sheet("Kits (só consulta)")
    k.append(["Kit/clone", "Blings", "Composição", "Soma dos componentes (R$)", "Valor na base hoje (R$)"])
    for kit in sorted(kits):
        comp, diverge = _comp_unica(kits, kit)
        k.append([kit, ", ".join(sorted(kits[kit])) + (" (composição diverge)" if diverge else ""),
                  " + ".join(f"{q:g}× {c}" for c, q in comp), cmv_calculado(kit, kits, itens), (itens.get(kit) or {}).get("cmv")])
    for aba, larg in ((ws, (26, 50, 12, 60)), (k, (28, 26, 70, 18, 18))):
        for i, w in enumerate(larg):
            aba.column_dimensions[chr(65 + i)].width = w
        for c in aba[1]:
            c.font, c.fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="1A1A24")
        aba.freeze_panes = "A2"
    for row in ws.iter_rows(min_row=2, min_col=3, max_col=3):
        row[0].fill, row[0].number_format = PatternFill("solid", fgColor="FFF176"), "0.00"
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
