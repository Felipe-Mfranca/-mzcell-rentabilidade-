"""
marketplaces/amazon/validar.py — Validador de credenciais da SP-API (somente leitura)
MZ App

Uso:  python -m marketplaces.amazon.validar amazon01

Confere, em ordem: (1) credenciais preenchidas, (2) formato, (3) token LWA aceito pela Amazon,
(4) uma chamada leve à SP-API (participação nos marketplaces). Nenhum valor secreto é impresso:
só aprovado/reprovado e o motivo (já mascarado por api.mascarar). Não grava nada, exceto que
api.access_token guarda o refresh token novo se a Amazon rotacionar (comportamento já existente).
"""

import re
import sys

from marketplaces.amazon import api

MARKETPLACE_BR = api.MARKETPLACE_BR

_RE_CLIENT_ID = re.compile(r"^amzn1\.application-oa2-client\.[0-9a-f]{32}$")
_RE_SECRET = re.compile(r"^(amzn1\.oa2-cs\.v1\.)?[0-9a-f]{64}$")
_PROIBIDOS = (" ", "\n", "\t", "\r", '"', "'")


def checar_formato(cfg: dict) -> list:
    """[(nivel, mensagem)], nivel 'erro' ou 'aviso'. Não valida com a Amazon: só o aspecto do texto."""
    saida = []
    for campo in ("refresh_token", "client_id", "client_secret"):
        v = cfg.get(campo)
        if not api._preenchido(v):
            saida.append(("erro", f"{campo} não preenchido"))
            continue
        v = str(v)
        if any(c in v for c in _PROIBIDOS):
            saida.append(("erro", f"{campo} tem espaço, quebra de linha ou aspas; cole só o valor"))
    rt, cid, cs = (str(cfg.get(k) or "") for k in ("refresh_token", "client_id", "client_secret"))
    if api._preenchido(rt) and not rt.startswith("Atzr|"):
        saida.append(("aviso", "refresh_token costuma começar com 'Atzr|'"))
    if api._preenchido(rt) and len(rt) < 100:
        saida.append(("aviso", "refresh_token parece curto demais (costuma ter mais de 300 caracteres)"))
    if api._preenchido(cid) and not _RE_CLIENT_ID.match(cid):
        saida.append(("aviso", "client_id costuma ser 'amzn1.application-oa2-client.' seguido de 32 caracteres hexadecimais"))
    if api._preenchido(cs) and not _RE_SECRET.match(cs):
        saida.append(("aviso", "client_secret costuma ser 'amzn1.oa2-cs.v1.' seguido de 64 caracteres hexadecimais (ou só os 64)"))
    if (api._preenchido(cid) and "oa2-cs" in cid) or (api._preenchido(cs) and "application-oa2-client" in cs):
        saida.append(("erro", "client_id e client_secret parecem trocados"))
    return saida


def validar(conta: str) -> dict:
    """Roda as etapas e devolve {'ok': bool, 'etapas': [(nome, ok, detalhe)]}. Sem segredos."""
    etapas = []

    def reg(nome, ok, detalhe=""):
        etapas.append((nome, ok, detalhe))
        return ok

    try:
        cfg = api.conta_cfg(conta)
    except api.ErroAmazon as e:
        reg("Conta no amazon_config.json", False, str(e))
        return {"ok": False, "etapas": etapas}
    reg("Conta no amazon_config.json", True)

    falta = [k for k in ("refresh_token", "client_id", "client_secret") if not api._preenchido(cfg.get(k))]
    if not reg("Credenciais preenchidas", not falta, ("faltam: " + ", ".join(falta)) if falta else ""):
        return {"ok": False, "etapas": etapas}

    fmt = checar_formato(cfg)
    erros = [m for n, m in fmt if n == "erro"]
    reg("Formato", not erros, "; ".join(m for _, m in fmt))
    if erros:
        return {"ok": False, "etapas": etapas}

    try:
        api.access_token(conta, forcar=True)
        reg("Token LWA aceito pela Amazon", True)
    except api.ErroAmazon as e:
        reg("Token LWA aceito pela Amazon", False, str(e))
        return {"ok": False, "etapas": etapas}

    try:
        st, corpo = api.get(conta, "/sellers/v1/marketplaceParticipations")
    except api.ErroAmazon as e:
        reg("Chamada à SP-API", False, str(e))
        return {"ok": False, "etapas": etapas}
    if st == 429:   # esta rota tem limite muito baixo; o token já foi aceito, então a credencial vale
        reg("Chamada à SP-API", True, "limite de chamadas da Amazon (429): credencial válida; repita em 1 minuto para confirmar o marketplace")
        return {"ok": True, "etapas": etapas}
    if st != 200:
        reg("Chamada à SP-API", False, f"HTTP {st}: {api.mascarar(str(corpo)[:200])}")
        return {"ok": False, "etapas": etapas}
    part = (corpo or {}).get("payload") or []
    ids = {(p.get("marketplace") or {}).get("id") for p in part}
    reg("Chamada à SP-API", True, f"{len(part)} marketplace(s) na conta")
    reg("Marketplace Brasil", MARKETPLACE_BR in ids,
        "" if MARKETPLACE_BR in ids else f"a conta não participa do marketplace {MARKETPLACE_BR}")
    return {"ok": all(ok for _, ok, _ in etapas), "etapas": etapas}


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if len(argv) != 1:
        print("uso: python -m marketplaces.amazon.validar <conta>   (ex.: amazon01)")
        return 2
    r = validar(argv[0])
    print(f"Validação da conta {argv[0]}\n")
    for nome, ok, det in r["etapas"]:
        print(f"  [{'OK ' if ok else 'ERRO'}] {nome}" + (f" — {det}" if det else ""))
    print("\n" + ("Credenciais válidas." if r["ok"] else "Credenciais NÃO validadas: corrija a etapa com erro."))
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
