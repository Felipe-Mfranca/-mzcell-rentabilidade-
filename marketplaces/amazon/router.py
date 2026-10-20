"""
marketplaces/amazon/router.py — Endpoints /amazon/* (somente leitura)
MZCell Rentabilidade

Montado no main.py com app.include_router(). Handlers são `def` e não `async def`:
o cliente em api.py usa httpx síncrono (como o do Bling), e o FastAPI roda `def`
numa thread própria sem travar o loop.

Nenhuma rota devolve credencial. Hoje o backend não aplica o role do usuário
(master/adminplus/viewer) a nenhum endpoint — isso vale para estas rotas também.
"""

import io

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from marketplaces.amazon import api, custos_xlsx, diagnostico, rentabilidade, reposicao

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


@router.get("/reposicao/{conta}")
def reposicao_conta(conta: str, meta_dias: float = None, comparar_amazon: bool = True):
    """Sugestão de reposição FBA por ASIN. Demora (1 chamada de vendas por ASIN + relatório)."""
    if meta_dias is not None and not 1 <= meta_dias <= 120:
        raise HTTPException(status_code=400, detail="meta_dias deve estar entre 1 e 120")
    try:
        return reposicao.apurar(conta, meta_dias, comparar_amazon)
    except api.ErroAmazon as e:
        raise _http(e)


@router.get("/modelo-custos/{conta}")
def modelo_custos(conta: str):
    """Modelo .xlsx de importação de CMV e imposto por SKU, pré-preenchido (mesmo fluxo do ML)."""
    try:
        conteudo = custos_xlsx.gerar_modelo(conta)
    except api.ErroAmazon as e:
        raise _http(e)
    return StreamingResponse(io.BytesIO(conteudo),
                             media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": f"attachment; filename=modelo_custos_{conta}.xlsx"})


@router.post("/importar-custos/{conta}")
def importar_custos(conta: str, file: UploadFile = File(...)):
    """Importa o modelo preenchido e grava CMV e imposto por SKU em amazon_custos.json."""
    try:
        api.conta_cfg(conta)
        return custos_xlsx.importar(conta, file.file.read())
    except api.ErroAmazon as e:
        raise _http(e)
