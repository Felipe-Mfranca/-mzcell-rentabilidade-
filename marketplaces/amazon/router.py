"""
marketplaces/amazon/router.py — Endpoints /amazon/* (somente leitura)
MZCell Rentabilidade

Montado no main.py com app.include_router(). Handlers são `def` e não `async def`:
o cliente em api.py usa httpx síncrono (como o do Bling), e o FastAPI roda `def`
numa thread própria sem travar o loop.

Nenhuma rota devolve credencial. Hoje o backend não aplica o role do usuário
(master/adminplus/viewer) a nenhum endpoint — isso vale para estas rotas também.
"""

from fastapi import APIRouter, HTTPException

from marketplaces.amazon import api, diagnostico, rentabilidade

router = APIRouter(prefix="/amazon", tags=["amazon"])


def _http(e: api.ErroAmazon):
    return HTTPException(status_code=e.status, detail=str(e))


@router.get("/contas")
def listar_contas():
    try:
        ids = api.contas()
    except api.ErroAmazon as e:
        raise _http(e)
    return [{"conta": i, "autenticado": api.autenticado(i),
             "sandbox": bool(api.conta_cfg(i).get("sandbox"))} for i in ids]


@router.get("/diagnostico/{conta}")
def diagnosticar(conta: str):
    try:
        return diagnostico.sondar(conta)
    except api.ErroAmazon as e:
        raise _http(e)


@router.get("/estoque/{conta}")
def estoque(conta: str):
    try:
        itens = api.estoque_fba(conta)
    except api.ErroAmazon as e:
        raise _http(e)
    return {"conta": conta, "total_asins": len(itens), "itens": itens}


@router.get("/vendas/{conta}")
def vendas(conta: str, asin: str, rede: str = None):
    try:
        return api.vendas_asin(conta, asin, rede)
    except api.ErroAmazon as e:
        raise _http(e)


@router.get("/rentabilidade/{conta}")
def rentabilidade_conta(conta: str, dias: int = 30):
    """Margem por SKU e DRE da conta (Finances). dias: 1 a 180."""
    if not 1 <= dias <= 180:
        raise HTTPException(status_code=400, detail="dias deve estar entre 1 e 180")
    try:
        return rentabilidade.apurar(conta, dias)
    except api.ErroAmazon as e:
        raise _http(e)
