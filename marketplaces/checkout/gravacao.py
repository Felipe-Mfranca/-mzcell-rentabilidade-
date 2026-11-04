"""
marketplaces/checkout/gravacao.py — Único ponto do módulo que ESCREVE no Bling: marca o pedido como Verificado (checkout)
MZ App

Trava em camadas (fase de teste):
  1. simular=True por padrão: só devolve o que seria enviado, não envia nada;
  2. SO_PEDIDO_TESTE: enquanto True, só aceita pedido cuja observação interna contém MARCA_TESTE;
  3. checkout_config.json {"gravacao": true} precisa existir (interruptor; sem ele nada grava);
  4. só grava se o pedido estiver Em aberto (6); a situação anterior fica no registro, para desfazer;
  5. depois de gravar, lê o pedido de novo e confirma que a situação mudou.
O ID de "Verificado" é lido do próprio Bling de cada conta (por nome), não fixo.
"""

import json
import os
import time
from datetime import datetime, timedelta, timezone

import httpx

from marketplaces.bling import api as bling

BRT = timezone(timedelta(hours=-3))
RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG = os.path.join(RAIZ, "checkout_config.json")
LOG = os.path.join(RAIZ, "checkout_gravacoes.jsonl")
MARCA_TESTE = "TESTE CHECKOUT MZ"
SO_PEDIDO_TESTE = True          # só vira False na camada 4, depois do teste aprovado pelo Zicri
SIT_EM_ABERTO = 6
MODULO_VENDAS = 98310


class ErroGravacao(Exception):
    pass


def interruptor_ligado(arquivo: str = None, agora=None) -> bool:
    """Ligado só com {"gravacao": true, "ate": "<data-hora ISO com fuso>"} e dentro da validade. Sem "ate", fica desligado."""
    try:
        with open(arquivo or CONFIG, encoding="utf-8") as f:
            c = json.load(f)
        if c.get("gravacao") is not True:
            return False
        ate = datetime.fromisoformat(c["ate"])
        return (agora or datetime.now(BRT)) < ate
    except (OSError, ValueError, AttributeError, KeyError, TypeError):
        return False


def _nome(s: str) -> str:
    return (s or "").strip().lower().replace("�", "")


def id_situacao(conta: str, nome: str, get=bling.get) -> int:
    st, r = get(conta, f"/situacoes/modulos/{MODULO_VENDAS}")
    if st != 200:
        raise ErroGravacao(f"Não consegui ler as situações do {conta} (HTTP {st}).")
    for s in (r or {}).get("data", []):
        if _nome(s.get("nome")) == _nome(nome):
            return s["id"]
    raise ErroGravacao(f'A situação "{nome}" não existe no {conta}.')


def achar_pedido(conta: str, numero, get=bling.get) -> int:
    """Número do pedido no Bling (o que aparece na lista) -> id interno. Só leitura."""
    st, r = get(conta, "/pedidos/vendas", numero=str(numero), limite=10)
    if st != 200:
        raise ErroGravacao(f"Não consegui consultar o pedido {numero} no {conta} (HTTP {st}).")
    for p in (r or {}).get("data", []):
        if str(p.get("numero")) == str(numero):
            return p["id"]
    raise ErroGravacao(f"Pedido {numero} não encontrado no {conta}.")


def saldos(conta: str, ped: dict, get=bling.get) -> list:
    """Saldo de estoque dos produtos do pedido, por depósito. SOMENTE LEITURA."""
    out = []
    for it in ped.get("itens") or []:
        pid = (it.get("produto") or {}).get("id")
        if not pid:
            continue
        st, r = get(conta, "/estoques/saldos", **{"idsProdutos[]": pid})
        d = ((r or {}).get("data") or [{}])[0] if st == 200 else {}
        out.append({"produto_id": pid, "codigo": it.get("codigo"), "quantidade_pedido": it.get("quantidade"),
                    "fisico": d.get("saldoFisicoTotal"), "virtual": d.get("saldoVirtualTotal"),
                    "depositos": [{"id": x.get("id"), "fisico": x.get("saldoFisico"), "virtual": x.get("saldoVirtual")}
                                  for x in d.get("depositos") or []]})
    return out


def _enviar(conta: str, metodo: str, caminho: str):
    """PATCH com renovação de token e espera em 429. Usado só por este arquivo."""
    c = bling.load_config()["contas"][conta]
    renovou, espera = False, 1
    for _ in range(6):
        tok = bling.load_config()["contas"][conta]["access_token"]
        r = httpx.request(metodo, bling.API + caminho, timeout=30,
                          headers={"Authorization": f"Bearer {tok}", "Accept": "application/json", "enable-jwt": "1"})
        if r.status_code == 401 and not renovou:
            renovou = True
            if bling.refresh_token(conta, tok)["success"]:
                continue
        if r.status_code == 429:
            time.sleep(espera)
            espera = min(espera * 2, 30)
            continue
        break
    try:
        return r.status_code, r.json() if r.text else {}
    except Exception:
        return r.status_code, r.text[:300]


def _registrar(linha: dict, arquivo: str = None):
    try:
        with open(arquivo or LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(linha, ensure_ascii=False) + "\n")
    except OSError:
        pass


def mudar_situacao(conta: str, pedido_id: int, destino_nome: str, simular: bool = True,
                   get=bling.get, enviar=_enviar, interruptor=interruptor_ligado, arquivo_log: str = None) -> dict:
    """Muda a situação do pedido. Com simular=True (padrão) não envia nada. Levanta ErroGravacao se uma trava barrar."""
    st, r = get(conta, f"/pedidos/vendas/{pedido_id}")
    if st != 200:
        raise ErroGravacao(f"Não achei o pedido {pedido_id} no {conta} (HTTP {st}).")
    ped = r["data"]
    atual = ped["situacao"]["id"]
    if SO_PEDIDO_TESTE and MARCA_TESTE not in (ped.get("observacoesInternas") or ""):
        raise ErroGravacao(f'Trava de teste: só aceito pedido com "{MARCA_TESTE}" na observação interna.')
    destino = id_situacao(conta, destino_nome, get)
    if destino_nome.lower() == "verificado" and atual != SIT_EM_ABERTO:
        raise ErroGravacao(f"O pedido {ped['numero']} não está Em aberto (situação {atual}); não marco como Verificado.")
    antes = saldos(conta, ped, get)
    plano = {"conta": conta, "pedido_id": pedido_id, "numero": ped["numero"], "de": atual, "para": destino,
             "requisicao": f"PATCH /pedidos/vendas/{pedido_id}/situacoes/{destino}", "simulado": simular,
             "contato": (ped.get("contato") or {}).get("nome"), "total": ped.get("total"),
             "itens": [{"codigo": i.get("codigo"), "descricao": i.get("descricao"), "quantidade": i.get("quantidade")}
                       for i in ped.get("itens") or []],
             "estoque_antes": antes}
    if simular:
        return dict(plano, enviado=False, aviso="Simulação: nada foi enviado ao Bling.")
    if not interruptor():
        raise ErroGravacao("Gravação desligada (checkout_config.json sem {\"gravacao\": true}).")
    codigo, resp = enviar(conta, "PATCH", f"/pedidos/vendas/{pedido_id}/situacoes/{destino}")
    ok = codigo in (200, 201, 204)
    depois, ped2 = None, ped
    if ok:
        st2, r2 = get(conta, f"/pedidos/vendas/{pedido_id}")
        if st2 == 200:
            depois, ped2 = r2["data"]["situacao"]["id"], r2["data"]
    res = dict(plano, enviado=True, http=codigo, situacao_depois=depois, confirmado=ok and depois == destino,
               estoque_depois=saldos(conta, ped2, get) if ok else None,
               resposta=None if ok else str(resp)[:300])
    _registrar(dict(res, ts=datetime.now(BRT).isoformat(timespec="seconds")), arquivo_log)
    return res
