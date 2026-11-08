"""
carga_servidor.py — Carga automática do servidor às 22h e às 6h (Brasília)
MZ App

Só leitura nas plataformas. Depois do sync do Meli (refresh_ml_token.loop_sync_diario), renova o que os usuários abrem ao
logar: análise PIVO, oportunidades de rebot e painel da Amazon, uma conta por vez. Uma etapa que falha não derruba as outras.
Só roda onde "carga_noturna" estiver aberto (modulos_locais.json do servidor); nas outras máquinas fica o sync das 6h de sempre.

O resultado da última carga fica em carga_estado.json. Com a carga do dia em dia e sem erro, a tela desliga os botões de
atualização manual; com erro, avisa que os dados podem estar desatualizados e oferece uma carga completa, que roda no servidor
e vale para todos (executar_completa). Expedição e SAC não dependem disto: leem em tempo real.
"""

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone

HORAS_BRT = (6, 22)          # em Brasília (UTC-3)
BRT_OFFSET = 3
ESPERA_SEG = 15
LIMITE_PIVO_SEG = 1800
LIMITE_AMAZON_SEG = 2700
CONTAS_ML = ("meli01", "meli02", "meli03")
CONTAS_AMAZON = ("amazon01", "amazon02")
REBOT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rebot_cache.json")
ESTADO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "carga_estado.json")
BRT = timezone(timedelta(hours=-3))
_rodando = {"desde": None, "origem": None}


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


async def sync_meli(log) -> dict:
    """Sync completo das contas do Meli ativas. {"meli conta": "ok" ou o erro}."""
    from ml_api import load_config
    from sync_service import sincronizar_conta_func
    res = {}
    for conta, dados in load_config()["contas"].items():
        if not dados.get("ativo") or not dados.get("access_token"):
            continue
        log(f"[sync-diario] Iniciando sync {conta}")
        try:
            r = await sincronizar_conta_func(conta)
            if r.get("success") is False:
                raise RuntimeError(r.get("error") or "falhou")
            res[f"meli {conta}"] = "ok"
            log(f"[sync-diario] {conta}: {r.get('total_atualizados', 0)} produtos atualizados")
        except Exception as e:
            res[f"meli {conta}"] = f"{type(e).__name__}: {str(e)[:150]}"
            log(f"[sync-diario] ERRO {conta}: {e}")
    return res


def _agora_brt() -> datetime:
    return datetime.now(BRT).replace(tzinfo=None)


def _gravar_estado(dados: dict):
    with open(ESTADO_PATH, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False)


async def executar_completa(log, origem: str) -> dict:
    """Meli, PIVO, rebot e Amazon; grava o resultado para a tela saber se os dados estão em dia. Uma por vez."""
    if _rodando["desde"]:
        return {}
    _rodando.update(desde=_agora_brt().isoformat(timespec="seconds"), origem=origem)
    inicio = _rodando["desde"]
    try:
        etapas = await sync_meli(log)
        etapas.update(await rodar_extras(log))
    except Exception as e:
        etapas = {"carga": f"{type(e).__name__}: {str(e)[:150]}"}
    finally:
        _rodando.update(desde=None, origem=None)
    erros = {k: v for k, v in etapas.items() if v != "ok"}
    ultima = {"inicio": inicio, "fim": _agora_brt().isoformat(timespec="seconds"), "origem": origem, "ok": not erros, "erros": erros, "etapas": len(etapas)}
    _gravar_estado({"ultima": ultima})
    log(f"[carga-servidor] carga {'completa' if not erros else 'COM ERRO'} ({origem}): {len(etapas) - len(erros)} de {len(etapas)} etapas ok")
    return ultima


def em_dia(ultima: dict, agora: datetime) -> bool:
    """Dados em dia = a carga mais recente terminou sem erro e é a das 6h de hoje ou mais nova
    (antes das 6h30 ainda vale a das 22h de ontem)."""
    if not ultima or not ultima.get("ok"):
        return False
    hoje6 = agora.replace(hour=6, minute=0, second=0, microsecond=0)
    limite = hoje6 if agora >= hoje6 + timedelta(minutes=30) else hoje6 - timedelta(hours=8)
    return datetime.fromisoformat(ultima["inicio"]) >= limite


def estado() -> dict:
    """O que a tela precisa: se a carga do servidor está ligada, se roda agora e se os dados estão em dia."""
    from modulos_liberados import liberado
    if not liberado("carga_noturna"):
        return {"ativo": False}
    try:
        with open(ESTADO_PATH, encoding="utf-8") as f:
            ultima = json.load(f).get("ultima")
    except (FileNotFoundError, ValueError):
        ultima = None
    return {"ativo": True, "rodando": bool(_rodando["desde"]), "rodando_desde": _rodando["desde"], "rodando_origem": _rodando["origem"],
            "ultima": ultima, "em_dia": em_dia(ultima, _agora_brt())}
