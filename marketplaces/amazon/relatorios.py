"""
marketplaces/amazon/relatorios.py — SP-API Reports (assíncrono), versão síncrona
MZCell Rentabilidade

Fluxo:
    POST /reports/2021-06-30/reports            → reportId
    GET  /reports/2021-06-30/reports/{id}       → status; devolve reportDocumentId
    GET  /reports/2021-06-30/documents/{docId}  → URL pré-assinada + compressão
    GET  <url>                                  → conteúdo (normalmente gzip)
"""

import csv
import gzip
import io
import time

import httpx

from marketplaces.amazon import api

REPORTS_PATH = "/reports/2021-06-30/reports"
DOCUMENTS_PATH = "/reports/2021-06-30/documents"
STATUS_FALHA = {"CANCELLED", "FATAL"}


def criar(conta_id: str, tipo: str, inicio_utc: str, fim_utc: str, opcoes: dict = None) -> str:
    corpo = {"reportType": tipo,
             "marketplaceIds": [api.conta_cfg(conta_id).get("marketplace_id") or api.MARKETPLACE_BR],
             "dataStartTime": inicio_utc, "dataEndTime": fim_utc}
    if opcoes:
        corpo["reportOptions"] = opcoes
    status, resp = api.post(conta_id, REPORTS_PATH, corpo)
    if status not in (200, 202):
        raise api.falha(status, resp)
    rid = resp.get("reportId") if isinstance(resp, dict) else None
    if not rid:
        raise api.ErroAmazon(f"Resposta sem reportId: {api.mascarar(resp)}"[:300], 502)
    return rid


def aguardar(conta_id: str, report_id: str, progresso=None, intervalo: int = 10, tentativas: int = 120) -> str:
    """Polling até o relatório ficar pronto. Devolve o reportDocumentId."""
    for n in range(tentativas):
        status, d = api.get(conta_id, f"{REPORTS_PATH}/{report_id}")
        if status != 200:
            raise api.falha(status, d)
        situacao = (d.get("processingStatus") or "").upper()
        if situacao == "DONE":
            if not d.get("reportDocumentId"):
                raise api.ErroAmazon("Relatório concluído mas sem documento", 502)
            return d["reportDocumentId"]
        if situacao in STATUS_FALHA:
            raise api.ErroAmazon(f"O relatório terminou com status {situacao} "
                                 "(sem dados no período, ou tipo/período não aceito)", 502)
        if progresso:
            progresso(f"  relatório {situacao.lower() or 'na fila'}... ({(n + 1) * intervalo}s)")
        time.sleep(intervalo)
    raise api.ErroAmazon(f"O relatório não ficou pronto em {tentativas * intervalo}s", 502)


def baixar_texto(conta_id: str, doc_id: str) -> str:
    status, doc = api.get(conta_id, f"{DOCUMENTS_PATH}/{doc_id}")
    if status != 200:
        raise api.falha(status, doc)
    url = doc.get("url")
    if not url:
        raise api.ErroAmazon("Documento do relatório sem URL", 502)
    try:
        r = httpx.get(url, timeout=120, follow_redirects=True)   # URL pré-assinada: sem header de auth
    except httpx.HTTPError as e:
        raise api.ErroAmazon(f"Falha ao baixar o relatório: {api.mascarar(e)}", 502)
    if r.status_code != 200:
        raise api.ErroAmazon(f"Falha ao baixar o relatório ({r.status_code})", 502)
    bruto = r.content
    if doc.get("compressionAlgorithm") == "GZIP" or bruto[:2] == b"\x1f\x8b":
        bruto = gzip.decompress(bruto)
    try:
        return bruto.decode("utf-8-sig")
    except UnicodeDecodeError:
        return bruto.decode("cp1252", errors="replace")   # relatórios antigos vêm em Windows-1252


def ler_tsv(texto: str) -> list:
    """Relatório flat file (separado por tab) → lista de dicts com cabeçalho em minúsculas."""
    leitor = csv.DictReader(io.StringIO(texto), delimiter="\t")
    return [{(k or "").strip().lower(): (v or "").strip() for k, v in linha.items()} for linha in leitor]
