"""
carga_servidor.py — Carga automática do servidor às 22h e às 6h (Brasília)
MZ App

Só leitura nas plataformas. Depois do sync do Meli (refresh_ml_token.loop_sync_diario), renova o que os usuários abrem ao
logar: análise PIVO, oportunidades de rebot e painel da Amazon, uma conta por vez. Uma etapa que falha não derruba as outras.
Só roda onde "carga_noturna" estiver aberto (modulos_locais.json do servidor); nas outras máquinas fica o sync das 6h de sempre.
"""

import asyncio
import json
import os
from datetime import datetime, timedelta

HORAS_BRT = (6, 22)          # em Brasília (UTC-3)
BRT_OFFSET = 3
ESPERA_SEG = 15
LIMITE_PIVO_SEG = 1800
LIMITE_AMAZON_SEG = 2700
CONTAS_ML = ("meli01", "meli02", "meli03")
CONTAS_AMAZON = ("amazon01", "amazon02")
REBOT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rebot_cache.json")


def proxima_carga(agora_utc: datetime, horas_brt=HORAS_BRT) -> datetime:
    """O próximo horário de carga, em UTC, depois de agora_utc."""
    candidatos = []
    for h in horas_brt:
        alvo = agora_utc.replace(hour=(h + BRT_OFFSET) % 24, minute=0, second=0, microsecond=0)
        candidatos.append(alvo if alvo > agora_utc else alvo + timedelta(days=1))
    return min(candidatos)


def rebot_montar(conta: str) -> dict:
    """Oportunidades de rebot de uma conta, no mesmo formato do e-mail. Só leitura (nunca escreve no ML)."""
    import rebot_agenda as agenda
    import rebot_visual as rv
    quando = datetime.now().strftime("%d/%m %H:%M")
    ops, baixa = agenda.analisar_conta(conta, True)
    avisadas = set(agenda._json(agenda.ESTADO, {"avisadas": []}).get("avisadas", []))
    for o in ops:
        o["repetida"] = o["chave"] in avisadas
    return {"conta": conta, "quando": quando, "total": len(ops), "total_baixa": len(baixa),
            "texto": rv.texto(ops, {}, 0, quando, [conta], baixa), "html": rv.html(ops, {}, 0, quando, [conta], baixa)}


def rebot_salvo(conta: str):
    try:
        with open(REBOT_PATH, encoding="utf-8") as f:
            return json.load(f).get(conta)
    except (FileNotFoundError, ValueError):
        return None


def _rebot_gravar(conta: str, dados: dict):
    try:
        with open(REBOT_PATH, encoding="utf-8") as f:
            tudo = json.load(f)
    except (FileNotFoundError, ValueError):
        tudo = {}
    tudo[conta] = dados
    with open(REBOT_PATH, "w", encoding="utf-8") as f:   # direto no arquivo: os.replace falha com WinError 32 neste Windows
        json.dump(tudo, f, ensure_ascii=False)


async def _pivo(conta: str):
    import pivo_ml
    from reposicao_full import ML
    pivo_ml.iniciar(conta, ML(conta))
    await _esperar(lambda: pivo_ml._estado.get(conta, {}).get("rodando"), LIMITE_PIVO_SEG)
    erro = pivo_ml._estado.get(conta, {}).get("erro")
    if erro:
        raise RuntimeError(erro)


async def _rebot(conta: str):
    _rebot_gravar(conta, await asyncio.to_thread(rebot_montar, conta))


async def _amazon(conta: str):
    from marketplaces.amazon import painel
    painel.iniciar_atualizacao(conta)
    await _esperar(lambda: painel.estado(conta).get("rodando"), LIMITE_AMAZON_SEG)
    erro = painel.estado(conta).get("erro")
    if erro:
        raise RuntimeError(erro)


async def _esperar(rodando, limite_seg: int):
    """Espera a tarefa de fundo acabar. Passou do limite: segue para a próxima conta, sem insistir."""
    for _ in range(limite_seg // ESPERA_SEG):
        if not rodando():
            return
        await asyncio.sleep(ESPERA_SEG)
    raise TimeoutError(f"passou de {limite_seg // 60} min")


async def rodar_extras(log) -> dict:
    """PIVO, rebot e Amazon, em sequência. Devolve {etapa: 'ok' ou o erro} e registra cada uma no log de atividades."""
    etapas = [("pivo", c, _pivo) for c in CONTAS_ML] + [("rebot", c, _rebot) for c in CONTAS_ML] + [("amazon", c, _amazon) for c in CONTAS_AMAZON]
    resultado = {}
    for nome, conta, func in etapas:
        chave = f"{nome} {conta}"
        try:
            await func(conta)
            resultado[chave] = "ok"
        except Exception as e:
            resultado[chave] = f"{type(e).__name__}: {str(e)[:150]}"
        log(f"[carga-servidor] {chave}: {resultado[chave]}")
    return resultado
