"""
marketplaces/shopee/validar.py — Validador do ambiente Shopee e atalhos de autorização (somente leitura)
MZ App

Uso (na pasta do projeto):
  python -m marketplaces.shopee.validar                          confere o .env e a assinatura do app
  python -m marketplaces.shopee.validar --url <loja>             imprime o link de autorização da loja
  python -m marketplaces.shopee.validar --codigo <loja> <code>   troca o code do redirect por token (shop_id da loja)
  python -m marketplaces.shopee.validar --teste <loja>           chama get_shop_info da loja

Nada secreto é impresso: partner key, tokens e code ficam fora da saída (o link de autorização
leva só a assinatura, não a chave). Só leitura na Shopee.
"""

import re
import sys

import httpx

from marketplaces.shopee import api, lojas
from marketplaces.shopee.lojas import ErroShopee

_RE_ID = re.compile(r"^\d{4,12}$")
_RE_KEY = re.compile(r"^(shpk)?[0-9a-fA-F]{32,128}$")


def checar_formato(env: dict) -> list:
    """[(nivel, mensagem)] com nivel 'erro' ou 'aviso'. Só o aspecto do texto, sem rede."""
    saida = []
    amb = lojas.ambiente(env) if env.get("SHOPEE_ENV", "sandbox").strip().upper() in ("SANDBOX", "PROD") else None
    if amb is None:
        return [("erro", "SHOPEE_ENV deve ser sandbox ou prod")]
    pre = f"SHOPEE_{amb}_"
    for chave in ("BASE_URL", "PARTNER_ID", "PARTNER_KEY"):
        v = env.get(pre + chave, "")
        if not v:
            saida.append(("erro", f"{pre}{chave} não preenchida"))
        elif any(c in v for c in (" ", "\n", "\t", '"', "'")):
            saida.append(("erro", f"{pre}{chave} tem espaço ou aspas; use só o valor"))
    url, pid, key = (env.get(pre + k, "") for k in ("BASE_URL", "PARTNER_ID", "PARTNER_KEY"))
    if url and not url.startswith("https://"):
        saida.append(("erro", "BASE_URL deve começar com https://"))
    if url.endswith("/"):
        saida.append(("aviso", "BASE_URL termina com barra; ela é removida automaticamente"))
    if pid and not _RE_ID.match(pid):
        saida.append(("aviso", "PARTNER_ID costuma ser só números (4 a 12 dígitos)"))
    if key and not _RE_KEY.match(key):
        saida.append(("aviso", "PARTNER_KEY costuma ter 32 a 128 caracteres hexadecimais (às vezes com prefixo shpk)"))
    if not env.get("SHOPEE_REDIRECT_URL"):
        saida.append(("aviso", "SHOPEE_REDIRECT_URL vazia: sem ela não dá para gerar o link de autorização"))
    if amb == "PROD":
        saida.append(("aviso", "ambiente PROD ativo: só use com Go-Live aprovado"))
    return saida


def testar_assinatura(env: dict) -> tuple:
    """Pede a página de autorização assinada: se partner_id, partner key e host são do mesmo ambiente a Shopee
    responde com a página (200/302); se a assinatura não bate, responde JSON com 'error'. Devolve (ok, detalhe)."""
    try:
        url = api.url_autorizacao(env)
    except ErroShopee as e:
        return False, str(e)
    try:
        r = httpx.get(url, timeout=20, follow_redirects=False)
    except httpx.HTTPError as e:
        return False, api.mascarar(f"não alcancei a Shopee (rede/host): {e}", [lojas.cfg("PARTNER_KEY", env)])
    if "json" in r.headers.get("content-type", ""):
        try:
            d = r.json()
        except ValueError:
            d = {}
        if d.get("error"):
            motivo = api.mascarar(f"{d.get('error')}: {d.get('message')}", [lojas.cfg("PARTNER_KEY", env)])
            dica = " (partner ID, chave e host precisam ser do mesmo ambiente)" if "sign" in motivo.lower() else ""
            return False, motivo + dica
    if r.status_code in (200, 301, 302, 303):
        return True, f"a Shopee aceitou a assinatura (HTTP {r.status_code})"
    return False, f"resposta inesperada da Shopee: HTTP {r.status_code}"


def validar() -> dict:
    etapas = []

    def reg(nome, ok, det=""):
        etapas.append((nome, ok, det))
        return ok

    env = lojas.ler_env()
    try:
        amb = lojas.ambiente(env)
    except ErroShopee as e:
        reg("Ambiente", False, str(e))
        return {"ok": False, "etapas": etapas}
    reg("Ambiente", True, amb.lower())
    fmt = checar_formato(env)
    erros = [m for n, m in fmt if n == "erro"]
    reg("Preenchimento e formato", not erros, "; ".join(m for _, m in fmt))
    if erros:
        return {"ok": False, "etapas": etapas}
    ok, det = testar_assinatura(env)
    reg("Assinatura aceita pela Shopee", ok, det)
    if not ok:
        return {"ok": False, "etapas": etapas}
    todas = lojas.lojas(env)
    reg("Lojas cadastradas (shopee_lojas.json)", bool(todas), ", ".join(todas) if todas else "nenhuma: crie o shopee_lojas.json")
    for k, v in todas.items():
        est = api.estado_autorizacao(v["shop_id"], env)
        reg(f"Autorização da loja {k}", est["autorizada"], est["mensagem"])
    return {"ok": all(ok for _, ok, _ in etapas[:3]), "etapas": etapas}


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        if argv[:1] == ["--url"] and len(argv) == 2:
            lojas.shop_id_da(argv[1])
            print(api.url_autorizacao())
            return 0
        if argv[:1] == ["--codigo"] and len(argv) == 3:
            sid = lojas.shop_id_da(argv[1])
            api.trocar_codigo(argv[2], sid)
            e = api.estado_autorizacao(sid)
            print(f"Loja {argv[1]} autorizada. {e['mensagem']} (expira em {e['expira_em']})")
            return 0
        if argv[:1] == ["--teste"] and len(argv) == 2:
            for k, v in api.info_loja(lojas.shop_id_da(argv[1])).items():
                print(f"  {k}: {v}")
            return 0
        if argv:
            print(__doc__)
            return 2
    except ErroShopee as e:
        print(f"ERRO: {e}")
        return 1
    r = validar()
    print("Validação do ambiente Shopee\n")
    for nome, ok, det in r["etapas"]:
        print(f"  [{'OK ' if ok else 'ERRO'}] {nome}" + (f" — {det}" if det else ""))
    print("\n" + ("Ambiente pronto: autorize a loja (--url) e teste (--teste)." if r["ok"] else "Ambiente NÃO validado: corrija a etapa com erro."))
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
