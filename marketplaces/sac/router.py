"""
marketplaces/sac/router.py — Endpoints do SAC que vivem fora do main.py (somente leitura)

GET /sac/devolucoes?dias=15            devoluções em andamento (logística reversa) das três contas
GET /sac/{conta}/devolucoes?dias=15    o mesmo para uma conta
Respondem na hora com o último resultado (`carregando: true` só na primeira leitura depois de ligar o servidor).
"""

from fastapi import APIRouter, HTTPException

from marketplaces.sac import devolucoes

router = APIRouter(prefix="/sac", tags=["sac"])


def _dias(d: int) -> int:
    if not 1 <= d <= 60:
        raise HTTPException(status_code=400, detail="dias deve estar entre 1 e 60")
    return d


def _resposta(conta, dias, atualizar):
    snap = devolucoes.obter(_dias(dias), atualizar)
    if snap["dados"] is None:
        return {"success": True, "carregando": True, "erro": snap["erro"], "itens": [], "resumo": {}}
    itens = [x for x in snap["dados"]["itens"] if conta is None or x["conta"] == conta]
    return {"success": True, "carregando": False, "atualizando": snap["carregando"], "gerado_em": snap["gerado_em"],
            "idade_seg": snap["idade_seg"], "dias": snap["dados"].get("dias"), "itens": itens,
            "resumo": devolucoes._resumo(itens), "erros": snap["dados"].get("erros", {}), "erro": snap["erro"]}


@router.get("/devolucoes")
def devolucoes_todas(dias: int = 15, atualizar: bool = False):
    return _resposta(None, dias, atualizar)


@router.get("/{conta}/devolucoes")
def devolucoes_conta(conta: str, dias: int = 15, atualizar: bool = False):
    if conta not in devolucoes.CONTAS:
        raise HTTPException(status_code=404, detail="conta inválida")
    return _resposta(conta, dias, atualizar)
