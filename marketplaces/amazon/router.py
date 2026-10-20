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

from marketplaces.amazon import api, custos_xlsx, diagnostico, painel, rentabilidade, reposicao

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


@router.get("/painel/{conta}")
def painel_conta(conta: str, de: str = None, ate: str = None):
    """Tabela por SKU e DRE do período (e do período anterior), sobre o cache local. Instantâneo."""
    try:
        api.conta_cfg(conta)
        return {**painel.painel(conta, de, ate), "estado": painel.estado(conta)}
    except api.ErroAmazon as e:
        raise _http(e)


@router.get("/painel/{conta}/estado")
def painel_estado(conta: str):
    try:
        api.conta_cfg(conta)
        return painel.estado(conta)
    except api.ErroAmazon as e:
        raise _http(e)


@router.post("/painel/{conta}/atualizar")
def painel_atualizar(conta: str):
    """Busca os últimos 62 dias no Finances em segundo plano (alguns minutos) e regrava o cache local."""
    try:
        return painel.iniciar_atualizacao(conta)
    except api.ErroAmazon as e:
        raise _http(e)


@router.get("/sku/{conta}")
def sku_conta(conta: str, sku: str, de: str = None, ate: str = None):
    """Dados do modal de um SKU: período, período anterior, faixa fixa de 30 dias, custo e alíquotas."""
    try:
        api.conta_cfg(conta)
        return painel.sku_detalhe(conta, sku, de, ate)
    except api.ErroAmazon as e:
        raise _http(e)


@router.post("/custo/{conta}")
def definir_custo(conta: str, corpo: dict):
    """Ajusta o custo de um SKU em amazon_custos.json: {"sku":..., "tem_st": true|false, "cmv_unit": opcional}.
    O ST grava a alíquota da conta no SKU; o CMV, se não vier, continua vindo da base central."""
    try:
        api.conta_cfg(conta)
        sku = str(corpo.get("sku") or "").strip()
        if not sku:
            raise api.ErroAmazon("informe o sku", 400)
        dados = custos_xlsx._ler_custos_arquivo()
        mine = dados.setdefault(conta, {})
        chave = next((k for k in mine if k.upper() == sku.upper()), sku)
        e = dict(mine.get(chave, {}))
        if corpo.get("tem_st") is not None:
            com, sem = custos_xlsx.aliquotas_da_conta(conta)
            e["tem_st"] = bool(corpo["tem_st"])
            e["imposto_pct"] = round(com if e["tem_st"] else sem, 6)
        if corpo.get("cmv_unit") is not None:
            v = float(corpo["cmv_unit"])
            if v < 0:
                raise api.ErroAmazon("cmv_unit negativo", 400)
            e["cmv_unit"] = round(v, 4)
        mine[chave] = e
        custos_xlsx._gravar_custos_arquivo(dados)
        return {"success": True, "sku": chave, "custo": e}
    except (api.ErroAmazon,) as e:
        raise _http(e)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="cmv_unit inválido")


@router.post("/trafego/{conta}")
def trafego_iniciar(conta: str, de: str, ate: str):
    """Pede ao relatório Sales and Traffic as visitas do período e do período anterior (30 a 60 s cada), em segundo plano."""
    try:
        painel.iniciar_trafego(conta, de, ate)
        pa_de, pa_ate = painel._anterior(de, ate)
        painel.iniciar_trafego(conta, pa_de, pa_ate)
        return {"conta": conta, "atual": {"de": de, "ate": ate}, "anterior": {"de": pa_de, "ate": pa_ate}}
    except api.ErroAmazon as e:
        raise _http(e)


@router.get("/trafego/{conta}")
def trafego_do_sku(conta: str, asin: str, de: str, ate: str):
    """Visitas, conversão e Buy Box do ASIN no período e no anterior, se já estiverem em cache."""
    try:
        api.conta_cfg(conta)
        painel._validar_trafego(de, ate)
        return painel.trafego_do_sku(conta, asin, de, ate)
    except api.ErroAmazon as e:
        raise _http(e)
