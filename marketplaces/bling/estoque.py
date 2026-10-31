"""
marketplaces/bling/estoque.py — Estoque por SKU nos três Blings (somente leitura)
MZ App

Saldo = saldo físico do depósito Geral (padrão). Os outros depósitos são canais (Full, Shopee...) e ficam de fora;
reservas de pedidos em aberto não são descontadas, para o estoquista comparar com a montagem.
A tabela mostra o Bling da empresa dona da conta; os outros dois aparecem ao passar o mouse.
"""

import threading
import time

from marketplaces.bling import api

BLINGS = ["bling01", "bling02", "bling03"]
# conta do app -> Bling da mesma empresa (ver empresas-contas-mapeamento)
BLING_DA_CONTA = {"meli01": "bling01", "amazon01": "bling01", "shopee": "bling01", "magalu": "bling01",
                  "meli02": "bling02", "amazon02": "bling02", "meli03": "bling03"}
CACHE_SEG = 600
_cache = {}
_trava = threading.Lock()


def chave(sku) -> str:
    return str(sku or "").strip().upper()


def _deposito_geral(conta_bling: str) -> int:
    st, r = api.get(conta_bling, "/depositos", limite=100)
    if st != 200:
        raise RuntimeError(f"{conta_bling}: depósitos HTTP {st}")
    deps = r.get("data", [])
    for d in deps:
        if d.get("padrao") or str(d.get("descricao", "")).strip().lower() == "geral":
            return d["id"]
    raise RuntimeError(f"{conta_bling}: depósito Geral não encontrado")


def _carregar(conta_bling: str) -> dict:
    """{"saldos": SKU -> saldo físico do depósito Geral, "pais": SKUs de produto com variações}.
    Os demais depósitos são canais e não entram no saldo. O pai de variações não tem saldo próprio:
    anúncio com o SKU do pai precisa usar o SKU da variação."""
    geral = _deposito_geral(conta_bling)
    produtos, ativo, pais = {}, {}, {}
    for pg in range(1, 200):
        st, r = api.get(conta_bling, "/produtos", pagina=pg, limite=100, criterio=1)
        if st != 200:
            raise RuntimeError(f"{conta_bling}: produtos HTTP {st}")
        dados = r.get("data", [])
        for p in dados:
            k = chave(p.get("codigo"))
            if not k:
                continue
            a = p.get("situacao") == "A"
            if k in produtos and ativo[k] and not a:
                continue  # SKU repetido: vale o cadastro ativo
            produtos[k] = p["id"]
            ativo[k] = a
            if p.get("formato") == "V":
                pais[k] = p["id"]
            else:
                pais.pop(k, None)
        if len(dados) < 100:
            break
    sku_do_id = {i: k for k, i in produtos.items()}
    ids = list(sku_do_id)
    saldos, virtuais = {}, {}
    for i in range(0, len(ids), 100):
        st, r = api.get(conta_bling, "/estoques/saldos", **{"idsProdutos[]": ids[i:i + 100]})
        if st != 200:
            raise RuntimeError(f"{conta_bling}: saldos HTTP {st}")
        for x in r.get("data", []):
            k = sku_do_id.get(x["produto"]["id"])
            if k is not None:
                dep = next((d for d in x.get("depositos", []) if d.get("id") == geral), {})
                saldos[k] = dep.get("saldoFisico") or 0
                virtuais[k] = dep.get("saldoVirtual") or 0   # físico menos o reservado por pedidos em aberto
    pais = {k: True for k in pais}  # só marca; as variações são sugeridas pelo prefixo do SKU, sem uma consulta por produto
    return {"saldos": saldos, "pais": pais, "virtuais": virtuais}


_renovando = set()


def _renovar_fundo(conta_bling: str):
    try:
        dados = _carregar(conta_bling)
        _cache[conta_bling] = (time.time(), dados)
    except Exception:
        pass  # mantém o saldo anterior; a próxima consulta tenta de novo
    finally:
        _renovando.discard(conta_bling)


def saldos(conta_bling: str, forcar: bool = False) -> dict:
    """Saldos e pais de variações por SKU. Passado o prazo, devolve o último saldo e renova em segundo plano (a leitura completa leva ~15 s por Bling)."""
    agora = time.time()
    if conta_bling in _cache and not forcar:
        if agora - _cache[conta_bling][0] >= CACHE_SEG and conta_bling not in _renovando:
            _renovando.add(conta_bling)
            threading.Thread(target=_renovar_fundo, args=(conta_bling,), daemon=True).start()
        return _cache[conta_bling][1]
    with _trava:  # primeira leitura: uma por vez, as telas que pedirem juntas esperam a mesma varredura
        if conta_bling in _cache and not forcar:
            return _cache[conta_bling][1]
        dados = _carregar(conta_bling)
        _cache[conta_bling] = (time.time(), dados)
        return dados


def aquecer():
    """Lê os três Blings logo após o servidor subir, para a primeira tela já encontrar o saldo pronto."""
    for b in BLINGS:
        try:
            saldos(b)
        except Exception:
            pass


def por_conta(conta: str, forcar: bool = False) -> dict:
    """{principal, outros, itens: {SKU: [saldo no Bling da conta, {outro bling: saldo}]}, erros}"""
    principal = BLING_DA_CONTA[conta]
    outros = [b for b in BLINGS if b != principal]
    erros, tabelas, pais = {}, {}, {}
    for b in [principal] + outros:
        try:
            d = saldos(b, forcar)
            tabelas[b], pais[b] = d["saldos"], d["pais"]
        except Exception as e:
            erros[b] = f"{type(e).__name__}: {str(e)[:100]}"
    itens = {}
    for sku in set().union(*[set(t) for t in tabelas.values()]):
        itens[sku] = [tabelas.get(principal, {}).get(sku), {b: tabelas[b][sku] for b in outros if b in tabelas and sku in tabelas[b]}]
    return {"principal": principal, "outros": outros, "itens": itens, "pais": pais.get(principal, {}), "erros": erros}
