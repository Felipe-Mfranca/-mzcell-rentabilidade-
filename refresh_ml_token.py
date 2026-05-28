"""
refresh_ml_token.py — Renova tokens ML automaticamente
Roda como background task no FastAPI
MZCell Rentabilidade
"""

import asyncio
import json
import os
from datetime import datetime, timedelta
from ml_api import refresh_token, load_config

LOG_PATH = os.path.join(os.path.dirname(__file__), "log_atividades.json")


def log(mensagem: str):
    logs = []
    if os.path.exists(LOG_PATH):
        with open(LOG_PATH, "r", encoding="utf-8") as f:
            try:
                logs = json.load(f)
            except Exception:
                logs = []
    logs.append({"ts": datetime.now().isoformat(), "msg": mensagem})
    logs = logs[-500:]  # Mantém últimas 500 entradas
    with open(LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(logs, f, ensure_ascii=False, indent=2)


async def verificar_e_renovar():
    """Verifica se algum token está próximo de expirar e renova."""
    config = load_config()
    for conta, dados in config["contas"].items():
        if not dados.get("refresh_token"):
            continue
        expires_at = dados.get("token_expires_at", "")
        if not expires_at:
            continue
        try:
            exp = datetime.fromisoformat(expires_at)
            agora = datetime.utcnow()
            minutos_restantes = (exp - agora).total_seconds() / 60
            if minutos_restantes < 60:  # Renova se faltar menos de 1h
                log(f"[{conta}] Token expirando em {minutos_restantes:.0f}min — renovando...")
                resultado = await refresh_token(conta)
                if resultado["success"]:
                    log(f"[{conta}] Token renovado com sucesso")
                else:
                    log(f"[{conta}] ERRO ao renovar token: {resultado['error']}")
        except Exception as e:
            log(f"[{conta}] Erro ao verificar token: {e}")


async def loop_refresh():
    """Loop que verifica tokens a cada 30 minutos."""
    while True:
        await verificar_e_renovar()
        await asyncio.sleep(30 * 60)  # 30 minutos


async def loop_sync_diario():
    """Loop que roda sync completo uma vez por dia às 6h BRT (9h UTC)."""
    from sync_service import sincronizar_conta_func
    while True:
        now = datetime.utcnow()
        target = now.replace(hour=9, minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())
        config = load_config()
        for conta, dados in config["contas"].items():
            if not dados.get("ativo") or not dados.get("access_token"):
                continue
            log(f"[sync-diario] Iniciando sync {conta}")
            try:
                resultado = await sincronizar_conta_func(conta)
                log(f"[sync-diario] {conta}: {resultado.get('total_atualizados', 0)} produtos atualizados")
            except Exception as e:
                log(f"[sync-diario] ERRO {conta}: {e}")


if __name__ == "__main__":
    asyncio.run(verificar_e_renovar())
