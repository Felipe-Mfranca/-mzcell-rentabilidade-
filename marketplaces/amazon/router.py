"""
marketplaces/amazon/router.py — Endpoints /amazon/* (somente leitura)
MZ App

Montado no main.py com app.include_router(). Handlers são `def` e não `async def`:
o cliente em api.py usa httpx síncrono (como o do Bling), e o FastAPI roda `def`
numa thread própria sem travar o loop.

Nenhuma rota devolve credencial. Hoje o backend não aplica o role do usuário
(master/adminplus/viewer) a nenhum endpoint — isso vale para estas rotas também.
"""

import io
import json
import os

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from marketplaces.amazon import api, custos_xlsx, diagnostico, painel, rentabilidade, reposicao

router = APIRouter(prefix="/amazon", tags=["amazon"])


def _opcional(nome: str):
    """ads e promocoes entram só quando a rota é usada: se a instalação ainda não tiver o arquivo,
    só esta rota avisa e o resto do módulo Amazon continua funcionando."""
    import importlib
    try:
        return importlib.import_module(f"marketplaces.amazon.{nome}")
    except ImportError:
        raise HTTPException(status_code=503, detail=f"recurso indisponível nesta instalação ({nome}); atualize o app")


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


_inventario = {}            # {conta: (instante, itens)}: o inventário FBA da conta vale 30 min no modal
INVENTARIO_VALIDADE_S = 30 * 60


_fotos = {}                       # {asin: (instante, url)}: foto principal, vale 7 dias
FOTOS_VALIDADE_S = 7 * 86400


def _foto_principal(corpo: dict):
    """Maior imagem MAIN do catálogo (variante principal, na maior resolução)."""
    melhor = None
    for bloco in corpo.get("images") or []:
        for img in bloco.get("images") or []:
            if img.get("variant") != "MAIN" or not img.get("link"):
                continue
            if melhor is None or (img.get("width") or 0) > (melhor.get("width") or 0):
                melhor = img
    return melhor.get("link") if melhor else None


FOTOS_SKU_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "amazon_fotos_sku.json")


@router.get("/foto-sku/{conta}")
def foto_sku(conta: str, sku: str, asin: str = ""):
    """Foto do anúncio na Amazon: 1) foto principal do catálogo (pelo ASIN), que é a do próprio anúncio;
    2) reserva: foto do Meli de um SKU igual ou de um irmão do mesmo ASIN (banco amazon_fotos_sku.json)."""
    if asin:
        r = fotos_asins(conta, asin).get(asin)
        if r:
            return {"sku": sku, "foto": r, "origem": "amazon", "meli": None}
    try:
        with open(FOTOS_SKU_PATH, encoding="utf-8") as f:
            banco = json.load(f).get(conta, {})
    except FileNotFoundError:
        banco = {}
    hit = banco.get(sku)
    if hit and hit.get("foto"):
        return {"sku": sku, "foto": hit["foto"], "origem": "meli_" + hit["origem"], "meli": hit.get("meli")}
    return {"sku": sku, "foto": None, "origem": "sem_foto"}


@router.get("/fotos/{conta}")
def fotos_asins(conta: str, asins: str):
    """{asin: url} da foto principal, pelo catálogo da Amazon (includedData=images). Consulta só o que falta no cache."""
    import time
    out, agora = {}, time.time()
    mkt = api.conta_cfg(conta).get("marketplace_id")
    for asin in [a.strip() for a in asins.split(",") if a.strip()][:20]:
        hit = _fotos.get(asin)
        if hit and agora - hit[0] < FOTOS_VALIDADE_S:
            out[asin] = hit[1]
            continue
        try:
            status, corpo = api.get(conta, f"/catalog/2022-04-01/items/{asin}",
                                    marketplaceIds=mkt, includedData="images")
        except api.ErroAmazon:
            continue                       # foto indisponível não derruba o modal
        url = _foto_principal(corpo) if status == 200 else None
        if url:
            _fotos[asin] = (agora, url)
            out[asin] = url
    return out


@router.get("/full/{conta}")
def full_asin(conta: str, asin: str):
    """Equivalente do aviso de Full do Meli para um ASIN: saldo FBA (disponível + a caminho), média dos
    últimos 7 dias e cobertura, com o mesmo nível do Meli (zerado / crítico < 10 dias / atenção 10 a 15)."""
    import time
    import full_alerta
    try:
        quando, itens = _inventario.get(conta, (0, None))
        if itens is None or time.time() - quando > INVENTARIO_VALIDADE_S:
            itens = api.estoque_fba(conta)
            _inventario[conta] = (time.time(), itens)
        item = next((i for i in itens if i.get("asin") == asin), None)
        if item is None:
            return {"asin": asin, "no_fba": False}
        vel = api.vendas_asin(conta, asin)
    except api.ErroAmazon as e:
        raise _http(e)
    saldo = item.get("estoque_valido", 0) or 0
    media = round((vel.get("vendido_7d") or 0) / 7, 2)
    nivel, dias = full_alerta.classificar(saldo, media, True, (vel.get("vendido_30d") or 0) > 0)
    return {"asin": asin, "no_fba": True, "saldo": saldo, "disponivel": item.get("disponivel", 0),
            "em_transito": item.get("em_transito", 0), "media_7d": media, "dias_cobertura": dias, "nivel": nivel}


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


@router.post("/reposicao/{conta}/caixa")
def reposicao_caixa(conta: str, body: dict):
    """Salva a caixa master de um ASIN (caixa vazia ou 0 remove o ajuste)."""
    asin = str(body.get("asin", "")).strip()
    caixa = body.get("caixa")
    if not asin:
        raise HTTPException(status_code=400, detail="asin obrigatório")
    if caixa not in (None, "") and (not str(caixa).isdigit() or int(caixa) > 100000):
        raise HTTPException(status_code=400, detail="caixa deve ser um número inteiro de unidades")
    return {"asin": asin, "caixa": reposicao.salvar_caixa(conta, asin, caixa or None).get(asin)}


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


@router.get("/ads/{conta}")
def ads_do_periodo(conta: str, de: str, ate: str):
    """Investimento em Ads (Sponsored Products) por ASIN no período, pelo Data Kiosk. Somente leitura."""
    try:
        api.conta_cfg(conta)
        painel._validar(de, ate)
        return {"conta": conta, "periodo": {"de": de, "ate": ate}, "por_asin": _opcional("ads").ads_por_asin(conta, de, ate)}
    except api.ErroAmazon as e:
        raise _http(e)


@router.get("/promocoes/{conta}")
def promocoes_do_periodo(conta: str, de: str, ate: str):
    """Descontos de promoção, frete e preços que mudaram no período (relatório de pedidos). Somente leitura."""
    try:
        api.conta_cfg(conta)
        painel._validar(de, ate)
        return _opcional("promocoes").promocoes_do_periodo(conta, de, ate)
    except api.ErroAmazon as e:
        raise _http(e)
