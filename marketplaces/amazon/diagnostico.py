"""
marketplaces/amazon/diagnostico.py — Mapa de módulos liberados na SP-API
MZ App

Uso:  .\\venv\\Scripts\\python.exe -m marketplaces.amazon.diagnostico amazon02 [--salvar]

Ter a credencial não significa ter o role: só a API responde isso. Este módulo
chama um endpoint de leitura por domínio e traduz o código HTTP:

    200 / 400 / 404 / 429  → LIBERADO  (400 e 404: o role existe, o parâmetro de teste é que não serve;
                                        429: limite de requisições só ocorre depois de autenticar)
    403                    → SEM ROLE
    401                    → TOKEN
"""

import sys
import time
from datetime import datetime, timedelta, timezone

from marketplaces.amazon import api


def modulos(marketplace: str) -> list:
    """(nome, role, caminho, params) — um endpoint de leitura por domínio."""
    hoje = datetime.now(timezone.utc).date()
    semana = hoje - timedelta(days=7)
    return [
        ("Conta / marketplaces", "básico", "/sellers/v1/marketplaceParticipations", {}),
        ("Estoque FBA", "Amazon Fulfillment", "/fba/inventory/v1/summaries",
         {"granularityType": "Marketplace", "granularityId": marketplace,
          "marketplaceIds": marketplace, "details": "false"}),
        ("Vendas (métricas)", "Inventory and Order Tracking", "/sales/v1/orderMetrics",
         {"marketplaceIds": marketplace, "granularity": "Total",
          "interval": f"{semana}T00:00:00-00:00--{hoje - timedelta(days=1)}T00:00:00-00:00"}),
        ("Pedidos", "Inventory and Order Tracking", "/orders/v0/orders",
         {"MarketplaceIds": marketplace, "CreatedAfter": f"{semana}T00:00:00Z", "MaxResultsPerPage": "1"}),
        ("Relatórios / Analytics", "Brand Analytics", "/reports/2021-06-30/reports",
         {"reportTypes": "GET_SALES_AND_TRAFFIC_REPORT", "pageSize": "1"}),
        ("Catálogo (leitura)", "básico", "/catalog/2022-04-01/items",
         {"marketplaceIds": marketplace, "keywords": "teste", "pageSize": "1"}),
        ("Preço", "Pricing", "/products/pricing/v0/price",
         {"MarketplaceId": marketplace, "ItemType": "Asin", "Asins": "B0FNGNQFVJ"}),
        ("Financeiro", "Finance and Accounting", "/finances/v0/financialEvents", {"MaxResultsPerPage": "1"}),
        ("SAC / Mensagens", "Buyer Communication ⚠ restrito", "/messaging/v1/orders/000-0000000-0000000",
         {"marketplaceIds": marketplace}),
        ("Expedição ao cliente", "Direct-to-Consumer Shipping ⚠ restrito", "/mfn/v0/sellerInputs", {}),
    ]


def classificar(status: int) -> str:
    if status in (200, 400, 404, 429):
        return "LIBERADO"
    if status == 403:
        return "SEM ROLE"
    if status == 401:
        return "TOKEN"
    return f"HTTP {status}"


def sondar(conta_id: str, pausa: float = 0.6) -> dict:
    """
    Resultado estruturado, seguro para devolver em API: nenhum campo carrega credencial.
    Levanta ErroAmazon se nem o token sair (credencial inválida ou sem rede).
    """
    c = api.conta_cfg(conta_id)
    mk = c.get("marketplace_id") or api.MARKETPLACE_BR
    api.access_token(conta_id, forcar=True)  # prova a credencial antes de sondar

    linhas = []
    for nome, role, caminho, params in modulos(mk):
        status, corpo = api.get(conta_id, caminho, **params)
        linhas.append({"modulo": nome, "role": role, "caminho": caminho, "http": status,
                       "situacao": classificar(status),
                       "detalhe": "" if status == 200 else api.mascarar(corpo)[:220]})
        time.sleep(pausa)  # respeita o rate limit entre sondagens

    return {"conta": conta_id, "sandbox": bool(c.get("sandbox")), "host": api.base_url(conta_id),
            "liberados": sum(1 for x in linhas if x["situacao"] == "LIBERADO"),
            "total": len(linhas), "modulos": linhas}


def formatar(r: dict) -> str:
    saida = [f"Conta: {r['conta']}  |  {'SANDBOX' if r['sandbox'] else 'PRODUÇÃO'}  |  {r['host']}", "",
             f"  {'MÓDULO':<24}{'ROLE':<38}{'HTTP':>5}  SITUAÇÃO", "  " + "─" * 78]
    for m in r["modulos"]:
        saida.append(f"  {m['modulo']:<24}{m['role']:<38}{m['http']:>5}  {m['situacao']}")
    saida += ["", f"  {r['liberados']} de {r['total']} módulos liberados"]
    bloqueados = [m for m in r["modulos"] if m["situacao"] == "SEM ROLE"]
    if bloqueados:
        saida.append("  Faltam roles para: " + ", ".join(m["modulo"] for m in bloqueados))
        saida.append(f"  Primeiro bloqueio — {bloqueados[0]['modulo']}: {bloqueados[0]['detalhe']}")
    return "\n".join(saida)


def main(argv: list) -> int:
    contas = [a for a in argv if not a.startswith("--")]
    conta_id = contas[0] if contas else "amazon02"
    try:
        texto = formatar(sondar(conta_id))
    except api.ErroAmazon as e:
        texto = f"✗ {e}"
        print(texto)
        return 2 if e.status == 502 else 1  # 2 = não chegou na Amazon; 1 = a Amazon recusou
    print(texto)
    if "--salvar" in argv:
        caminho = f"diagnostico_amazon_{conta_id}.txt"
        with open(caminho, "w", encoding="utf-8") as f:
            f.write(texto + "\n")
        print(f"\nRelatório salvo em {caminho} (credenciais mascaradas)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
