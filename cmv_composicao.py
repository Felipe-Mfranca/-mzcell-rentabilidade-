"""
cmv_composicao.py — CMV amarrado às composições dos Blings (kits e clones)
MZ App

O CMV é universal por SKU (cmv_base.json, o mesmo nas três empresas). Kit e clone não têm custo próprio:
valem a soma dos componentes do Bling (quantidade × CMV de cada um). Por isso o ajuste é sempre feito no
componente; ao salvar, todo kit/clone que usa aquele componente (direta ou indiretamente) é recalculado.

A composição vem do catálogo do balanço (balanco_catalogo.json, lido dos três Blings e renovado a cada 24 h).
Se o mesmo kit tiver composição diferente entre os Blings, ele não é recalculado e a divergência é avisada.

Página CMV, modal do produto e carga de planilha leem por composicao()/lista() e gravam só por gravar_lote():
mesma validação, mesmo recálculo de kits, mesma publicação na planilha da equipe e mesma propagação aos anúncios.

Carga de planilha pela linha de comando (com o servidor no ar; simula sem --confirmar):
  python -m cmv_composicao arquivo.xlsx --autor zicri [--confirmar]
"""

from datetime import datetime

import cmv_base


# Natal: árvores e kits de 84 e 130 enfeites têm o CMV lançado direto no produto; a composição será derrubada no Bling (decisão de 05/11/2026).
# Enquanto ela existir, valem como produto simples: aceitam valor próprio e nunca são recalculados pela soma. Os demais kits de Natal seguem a composição.
DIRETO_NO_PRODUTO = {
    "ARV-NAT-300G-VERDE", "ARV-NAT-600G-VERDE", "ARV-NAT-800G-VERDE", "ARV1000G-DEC-DR", "ARV1200G-DEC-VM", "ARV600G-DEC-DR",
    "ARV600G-DEC-VERM", "ARVDEC-NEV1.5M", "ARVDEC-SHP-150CMVERDE", "ARVDEC-VRD1.5M", "ARVNAT-SHP-150", "ARVNAT-SHP-180",
    "ARVNAT-SHP-210", "KIT130ENF-SORTIDO", "KIT130ENF-SORTIDO-NOVO", "KIT84ENF-SORTIDO",
}
# Fora da validação entre os Blings, a pedido do usuário (05/11/2026).
IGNORAR_NA_VALIDACAO = {"FONBTHM-LY124-BRANCO", "FONBTHM-LY124ROSA"}


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
            if _chave(kit) not in DIRETO_NO_PRODUTO:
                kits.setdefault(_chave(kit), {})[b] = [(_chave(c), float(q or 1)) for c, q in x.get("comp") or []]
    return {"kits": kits, "gerado_em": cat.get("gerado_em"), "erros": cat.get("erros") or {}}


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


def _no_bling() -> set:
    """SKUs que os Blings conhecem pelo catálogo do balanço: físicos com saldo e kits/clones."""
    from marketplaces.balanco import catalogo
    skus = set()
    for d in (catalogo.ler().get("blings") or {}).values():
        skus |= {_chave(s) for s in (d.get("skus") or {})} | {_chave(s) for s in (d.get("kits") or {})}
    return skus


def irmaos_no_bling(mapa: dict, sku: str, no_bling: set = None) -> list:
    """SKUs do mesmo ASIN que estão no Bling, para um SKU da Amazon que não está. `mapa`: a conta em amazon_asin_skus.json."""
    no_bling = _no_bling() if no_bling is None else no_bling
    if _chave(sku) in no_bling:
        return []
    asin = (mapa.get("sku_asin") or {}).get(sku)
    return [i for i in (mapa.get("asin_skus") or {}).get(asin, []) if i != sku and _chave(i) in no_bling] if asin else []


def valor_sku(sku: str, base: dict = None):
    """CMV do SKU: o da base; sem valor na base, a soma da composição (kit/clone)."""
    s = _chave(sku)
    itens = (base if base is not None else cmv_base.carregar()).get("itens", {})
    valor = (itens.get(s) or {}).get("cmv")
    if valor is not None:
        return valor
    kits = _catalogo()["kits"]
    return cmv_calculado(s, kits, itens) if _comp_unica(kits, s)[0] is not None else None


def referencia(conta: str, sku: str) -> dict:
    """Onde se ajusta o CMV de um SKU da Amazon: o irmão do Bling (decisão do usuário, 06/11/2026), ou o próprio SKU se não houver."""
    import json
    try:
        with open(cmv_base.ASIN_PATH, encoding="utf-8") as f:
            mapa = json.load(f).get(conta) or {}
    except (FileNotFoundError, ValueError):
        mapa = {}
    irmaos = irmaos_no_bling(mapa, sku)
    irmao = next((i for i in irmaos if valor_sku(i) is not None), irmaos[0] if irmaos else None)
    return {"sku": _chave(irmao or sku), "irmao": _chave(irmao) if irmao else None}


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


def gravar_lote(valores: dict, autor: str = "", origem: str = "ajuste no MZ App", descricoes: dict = None,
                confirmar: bool = True, arquivo: str = None) -> dict:
    """ÚNICA gravação de CMV: {SKU: valor} de componentes ou produtos simples. Kit/clone é recusado (vale a soma dos
    componentes). Recalcula os kits que usam cada SKU, publica e propaga aos anúncios. confirmar=False só simula."""
    cat = _catalogo()
    kits = cat["kits"]
    base = cmv_base.carregar()
    itens = base.get("itens", {})
    agora = datetime.now().isoformat(timespec="seconds")
    gravados, recusados, mudados = [], [], {}
    for bruto, valor in valores.items():
        sku = _chave(bruto)
        try:
            novo = round(float(valor), 4)
        except (TypeError, ValueError):
            novo = -1
        if not sku or novo < 0:
            recusados.append({"sku": sku, "motivo": "CMV inválido"})
            continue
        if sku in kits:
            recusados.append({"sku": sku, "motivo": "Este SKU é kit/clone no Bling: ajuste o CMV nos componentes."})
            continue
        antes = (itens.get(sku) or {}).get("cmv")
        gravados.append({"sku": sku, "de": antes, "para": novo})
        if antes is None or abs(antes - novo) > 0.00005:
            desc = (descricoes or {}).get(sku, "")
            itens[sku] = dict(itens.get(sku) or {"sku": sku, "tipo": "", "descricao": desc},
                              cmv=novo, aba=origem, atualizado_em=agora, autor=autor[:40])
            mudados[sku] = itens[sku]
    alterados = [g for g in gravados if g["sku"] in mudados]
    de_kits, kits_ok, kits_pendentes = _recalcular(kits, itens, [g["sku"] for g in gravados], agora, autor)
    mudados.update(de_kits)
    propagado = {}
    if confirmar and mudados:
        cmv_base.salvar(itens, base.get("_arquivo", "") if arquivo is None else arquivo)
        cmv_base.publicar(mudados, autor)
        propagado = cmv_base.propagar_ml()
    return {"success": True, "confirmado": bool(confirmar), "gravados": gravados, "alterados": alterados,
            "iguais": len(gravados) - len(alterados), "recusados": recusados, "kits": kits_ok,
            "kits_pendentes": kits_pendentes, "catalogo_erros": cat.get("erros") or {},
            "anuncios_ml": {c: (r.get("mudariam") if isinstance(r, dict) else None) for c, r in propagado.items()}}


def salvar_cmv(sku: str, cmv: float, autor: str = "") -> dict:
    """Grava o CMV de um SKU (página CMV e modal do produto)."""
    r = gravar_lote({sku: cmv}, autor)
    if r["recusados"]:
        raise ValueError(r["recusados"][0]["motivo"])
    return dict(r, sku=r["gravados"][0]["sku"], de=r["gravados"][0]["de"], para=r["gravados"][0]["para"])


def carga_planilha(conteudo: bytes, arquivo: str = "", autor: str = "", confirmar: bool = False) -> dict:
    """Carga de planilha (colunas SKU e CMV): cada linha passa por gravar_lote. Simula se confirmar=False.
    Com o catálogo incompleto a gravação é recusada: um kit do Bling que faltou seria tratado como produto simples."""
    lido = cmv_base.ler_planilha(conteudo)
    if not lido["itens"]:
        raise ValueError("Nenhuma aba com colunas SKU e CMV/Custo foi encontrada.")
    erros = _catalogo().get("erros")
    if confirmar and erros:
        raise ValueError("Catálogo do Bling incompleto (" + ", ".join(sorted(erros)) + "): atualize o catálogo e carregue de novo.")
    r = gravar_lote({k: v["cmv"] for k, v in lido["itens"].items()}, autor, f"carga de planilha ({arquivo or 'sem nome'})"[:60],
                    {k: v.get("descricao") or "" for k, v in lido["itens"].items()}, confirmar, arquivo=arquivo or None)
    return dict(r, skus=len(r["gravados"]), abas=lido["abas"], conflitos=lido["conflitos"], ignorados=lido["ignorados"])


def validar_blings() -> dict:
    """Confere se os Blings têm os mesmos kits e clones, com a mesma composição e quantidade. Só lê o catálogo."""
    from marketplaces.balanco import catalogo
    cat = catalogo.ler()
    blings = cat.get("blings") or {}
    kits = {}
    for b, dados in blings.items():
        for kit, x in (dados.get("kits") or {}).items():
            if _chave(kit) not in DIRETO_NO_PRODUTO | IGNORAR_NA_VALIDACAO:
                kits.setdefault(_chave(kit), {})[b] = sorted((_chave(c), float(q or 1)) for c, q in x.get("comp") or [])
    simples = {b: {_chave(s) for s in (d.get("skus") or {})} for b, d in blings.items()}
    divergentes, parciais, sem_componente, kit_e_simples = [], [], [], []
    for sku in sorted(kits):
        por_bling = kits[sku]
        if len({tuple(c) for c in por_bling.values()}) > 1:
            divergentes.append({"sku": sku, "composicoes": {b: [{"sku": c, "qtd": q} for c, q in v] for b, v in sorted(por_bling.items())}})
        if len(por_bling) < len(blings):
            parciais.append({"sku": sku, "em": sorted(por_bling), "falta_em": sorted(set(blings) - set(por_bling))})
        vazios = sorted(b for b, c in por_bling.items() if not c)
        if vazios:
            sem_componente.append({"sku": sku, "blings": vazios})
        comum = sorted(b for b, s in simples.items() if sku in s)
        if comum:
            kit_e_simples.append({"sku": sku, "kit_em": sorted(por_bling), "simples_em": comum})
    return {"gerado_em": cat.get("gerado_em"), "lidos": sorted(blings), "erros": cat.get("erros") or {},
            "kits": len(kits), "iguais_em_todos": len(kits) - len(divergentes) - sum(1 for p in parciais if p["sku"] not in {d["sku"] for d in divergentes}),
            "divergentes": divergentes, "parciais": parciais, "sem_componente": sem_componente, "kit_e_simples": kit_e_simples}


def lista() -> dict:
    """Todos os SKUs para a página CMV: base, catálogo dos Blings e anúncios. status: ok, a_preencher (kit sem valor na base,
    mas a soma já fecha) ou sem_cmv. O valor vale o que os anúncios usam, ou seja, o da base."""
    from marketplaces.balanco import catalogo
    cat = _catalogo()
    kits, itens = cat["kits"], cmv_base.carregar().get("itens", {})
    nomes = {}
    for dados in (catalogo.ler().get("blings") or {}).values():
        for s, x in (dados.get("skus") or {}).items():
            nomes.setdefault(_chave(s), x.get("nome") or "")
        for s, x in (dados.get("kits") or {}).items():
            if _chave(s) in DIRETO_NO_PRODUTO:   # fora de kits, mas precisa aparecer para receber o valor
                nomes.setdefault(_chave(s), x.get("nome") or "")
    contas, irmaos = {}, {}
    try:
        import produtos_store
        for p in produtos_store.ler().values():
            s = _chave(p.get("sku"))
            if s and p.get("conta"):
                nomes.setdefault(s, p.get("titulo") or "")
                contas.setdefault(s, {}).setdefault(p["conta"], 0)
                contas[s][p["conta"]] += 1
    except Exception:
        pass
    try:
        import json
        no_bling = _no_bling()
        with open(cmv_base.ASIN_PATH, encoding="utf-8") as f:
            for conta, m in json.load(f).items():
                for s in m.get("sku_asin") or {}:
                    contas.setdefault(_chave(s), {}).setdefault(conta, 0)
                    contas[_chave(s)][conta] += 1
                    ir = irmaos_no_bling(m, s, no_bling)
                    if ir:
                        irmaos.setdefault(_chave(s), _chave(next((i for i in ir if valor_sku(i) is not None), ir[0])))
    except (FileNotFoundError, ValueError, AttributeError):
        pass
    usado_em = {c: _quem_usa(kits, c) for por_bling in kits.values() for comp in por_bling.values() for c, _q in comp}
    linhas = []
    for s in sorted(set(nomes) | set(contas) | set(itens) | set(kits) | set(usado_em)):   # componente sem saldo e sem CMV também aparece
        x = itens.get(s) or {}
        ref = irmaos.get(s) or s        # SKU da Amazon fora do Bling segue o irmão do mesmo ASIN: tipo, composição e valor
        comp, diverge = _comp_unica(kits, ref)
        tipo = "simples" if comp is None else ("clone" if len(comp) == 1 and comp[0][1] == 1 else "kit")
        soma = cmv_calculado(ref, kits, itens) if comp is not None else None
        cmv = (itens.get(ref) or {}).get("cmv")
        if cmv is None and soma is None:
            cmv = x.get("cmv")
        status = "ok" if cmv is not None else ("a_preencher" if soma is not None else "sem_cmv")
        linhas.append({"sku": s, "descricao": x.get("descricao") or nomes.get(s, ""), "tipo": tipo, "cmv": cmv, "soma": soma, "irmao": irmaos.get(s),
                       "usado_em": usado_em.get(ref, []), "diverge": diverge, "status": status, "contas": contas.get(s, {}),
                       "atualizado_em": x.get("atualizado_em") or "", "autor": x.get("autor") or "", "origem": x.get("aba") or ""})
    return {"linhas": linhas, "catalogo_em": cat["gerado_em"], "catalogo_erros": cat.get("erros") or {}}


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


if __name__ == "__main__":
    import argparse
    import json as _json
    import sys
    import httpx
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Carga de CMV por planilha, pelo servidor do MZ App (simula sem --confirmar)")
    ap.add_argument("arquivo")
    ap.add_argument("--autor", required=True)
    ap.add_argument("--confirmar", action="store_true")
    ap.add_argument("--url", default="http://127.0.0.1:8001")
    a = ap.parse_args()
    with open(a.arquivo, "rb") as f:
        resp = httpx.post(a.url + "/cmv/carga", files={"file": (a.arquivo.replace(chr(92), "/").split("/")[-1], f.read())},
                          data={"autor": a.autor, "confirmar": "true" if a.confirmar else "false"}, timeout=600)
    d = resp.json()
    if resp.status_code != 200:
        sys.exit(f"Recusado: {d.get('detail')}")
    print("GRAVADO" if d["confirmado"] else "SIMULAÇÃO (nada foi gravado)")
    print(f"Linhas válidas: {d['skus']} · a alterar: {len(d['alterados'])} · iguais: {d['iguais']} · recusados: {len(d['recusados'])} · "
          f"sem CMV na planilha: {len(d['ignorados'])} · conflitos: {len(d['conflitos'])}")
    for g in d["alterados"]:
        print(f"  {g['sku']}: {g['de']} -> {g['para']}")
    for g in d["recusados"]:
        print(f"  RECUSADO {g['sku']}: {g['motivo']}")
    kits = [k for k in d["kits"] if k["de"] != k["para"]]
    print(f"Kits/clones recalculados: {len(kits)} · ainda pendentes: {len(d['kits_pendentes'])}")
    for k in kits:
        print(f"  {k['sku']}: {k['de']} -> {k['para']}")
    if d["catalogo_erros"]:
        print("ATENÇÃO, catálogo incompleto:", _json.dumps(d["catalogo_erros"], ensure_ascii=False))
