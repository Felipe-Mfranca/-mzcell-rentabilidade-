"""
marketplaces/montagem/leitor.py — Lê o PDF "instruções de preparação" do envio ao Full (Mercado Livre)
MZ App

O PDF traz: "Frete #NNN", "Produtos do envio: N | Total de unidades: M" e, por produto,
"Código ML: X  Código universal: EAN  SKU: Y", o título e a quantidade de unidades.
Não traz conta, data de coleta nem responsáveis: o líder informa ao criar o envio.

A leitura é conferida: soma das unidades × total do PDF e número de produtos × "Produtos do envio".
Se algo não bater, `confere` vem False e o líder corrige na prévia antes de gravar.
"""

import io
import re


def texto_do_pdf(conteudo: bytes) -> str:
    try:
        from pypdf import PdfReader
        return "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(conteudo)).pages)
    except ImportError:
        pass
    try:
        import fitz  # PyMuPDF
        return "\n".join(p.get_text("text") for p in fitz.open(stream=conteudo, filetype="pdf"))
    except ImportError:
        raise RuntimeError("Instale o leitor de PDF: .\\venv\\Scripts\\python.exe -m pip install pypdf")


_PRODUTO = re.compile(r"C[óo]digo ML:\s*(\S+)\s*C[óo]digo universal:\s*(\d*)\s*SKU:\s*(\S+)", re.I)


def ler_shopee(texto: str) -> dict:
    """PDF 'Shopee Picking List - Shopee Fulfillment' (FBS): ASN, data de inbound, método de entrega e produtos.
    Devolve o mesmo formato do Meli (`frete` = ASN) mais `canal`, `coleta` e `metodo`."""
    avisos = []
    m = re.search(r"\b(INB[A-Z]+\d+)", texto)   # o PDF cola o rótulo "ID de Envio" no fim do valor
    asn = m.group(1) if m else ""
    if not asn:
        avisos.append("Não achei o ID de envio (ASN).")
    m = re.search(r"Data de Inbound\s+(\d{4}-\d{2}-\d{2})(?:\s+(\d{2}:\d{2}))?", texto)
    coleta, hora = (m.group(1), m.group(2) or "") if m else ("", "")
    m = re.search(r"M[ée]todo de Entrega\s+(\w+)", texto)
    metodo = m.group(1) if m else ""
    m = re.search(r"Total\s+(\d+)", texto)
    total_pdf = int(m.group(1)) if m else None
    # cada produto começa numa linha "sku-do-vendedor <id_variacao>"; a quantidade é o último número isolado do bloco
    achados = list(re.finditer(r"(?m)^([A-Za-z0-9][A-Za-z0-9._-]*)\s+\d{6,}_\d+\s*$", texto))
    produtos = []
    for i, a in enumerate(achados):
        fim = achados[i + 1].start() if i + 1 < len(achados) else len(texto)
        bloco = texto[a.end():fim]
        qtds = [int(x) for x in re.findall(r"(?m)^\s*(\d{1,6})\s*$", bloco)]
        linhas = [l.strip() for l in bloco.splitlines() if l.strip() and not re.fullmatch(r"\d+", l.strip())]
        produtos.append({"codigo_ml": "", "ean": "", "sku": a.group(1), "titulo": " ".join(linhas[:3])[:160],
                         "unidades": qtds[-1] if qtds else None})
    soma = sum(p["unidades"] or 0 for p in produtos)
    if not produtos:
        avisos.append("Não achei nenhum produto na lista da Shopee.")
    if produtos and total_pdf is not None and soma != total_pdf:
        avisos.append(f"A soma das unidades ({soma}) não bate com o total do PDF ({total_pdf}). Confira as quantidades.")
    confere = bool(produtos) and all(p["unidades"] for p in produtos) and (total_pdf is None or soma == total_pdf)
    return {"canal": "shopee", "frete": asn, "coleta": coleta, "hora_coleta": hora, "metodo": metodo,
            "total_unidades_pdf": total_pdf, "produtos": produtos, "confere": confere, "avisos": avisos}


def ler_texto(texto: str) -> dict:
    """Interpreta o texto do PDF. Nunca levanta erro por layout diferente: devolve `avisos`."""
    if "Shopee Picking List" in texto or "Shopee Fulfillment" in texto:
        return ler_shopee(texto)
    avisos = []
    m = re.search(r"Frete\s*#\s*(\d+)", texto)
    frete = m.group(1) if m else ""
    if not frete:
        avisos.append("Não achei o número do frete (\"Frete #...\").")
    m = re.search(r"Produtos do envio:\s*(\d+)\s*\|\s*Total de unidades:\s*(\d+)", texto)
    n_pdf, total_pdf = (int(m.group(1)), int(m.group(2))) if m else (None, None)
    if not m:
        avisos.append("Não achei \"Produtos do envio\" e \"Total de unidades\".")

    achados = list(_PRODUTO.finditer(texto))
    # quantidades: linhas que são só um número pequeno (o EAN tem 13 dígitos e não entra)
    inteiros = [(mm.start(), int(mm.group(1))) for mm in re.finditer(r"(?m)^\s*(\d{1,6})\s*$", texto)]
    produtos = []
    for i, a in enumerate(achados):
        fim = achados[i + 1].start() if i + 1 < len(achados) else len(texto)
        bloco = texto[a.end():fim]
        corte = re.search(r"Etiquetagem|PRODUTO\s+UNIDADES", bloco)
        titulo = " ".join(bloco[:corte.start() if corte else len(bloco)].split())
        qtds = [v for pos, v in inteiros if a.end() <= pos < fim]
        produtos.append({"codigo_ml": a.group(1), "ean": a.group(2), "sku": a.group(3), "titulo": titulo,
                         "unidades": qtds[-1] if qtds else None})
    if len(produtos) > 1 or any(p["unidades"] is None for p in produtos):
        # o cabeçalho da tabela pode vir antes das linhas: se há exatamente uma quantidade por produto, usa em ordem
        todas = [v for _, v in inteiros]
        if len(todas) == len(produtos):
            for p, v in zip(produtos, todas):
                p["unidades"] = v
    soma = sum(p["unidades"] or 0 for p in produtos)
    confere = bool(produtos) and all(p["unidades"] for p in produtos) \
        and (total_pdf is None or soma == total_pdf) and (n_pdf is None or n_pdf == len(produtos))
    if produtos and total_pdf is not None and soma != total_pdf:
        avisos.append(f"A soma das unidades ({soma}) não bate com o total do PDF ({total_pdf}). Confira as quantidades.")
    if n_pdf is not None and n_pdf != len(produtos):
        avisos.append(f"O PDF informa {n_pdf} produto(s), mas li {len(produtos)}.")
    if not produtos:
        avisos.append("Não achei nenhum produto (\"Código ML: ... SKU: ...\").")
    return {"frete": frete, "total_unidades_pdf": total_pdf, "produtos": produtos, "confere": confere, "avisos": avisos}


def ler_pdf(conteudo: bytes) -> dict:
    return ler_texto(texto_do_pdf(conteudo))
