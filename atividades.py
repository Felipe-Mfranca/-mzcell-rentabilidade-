"""
atividades.py — Registro de atividades por usuário (base da tela de Produtividade)
MZ App

Cada gravação feita no app (POST, PUT, DELETE) vira um evento com quem, quando, de onde, área, ação e objeto
(anúncio, SKU, pedido, envio). O usuário vem da sessão do login; tela operacional aberta sem login fica "sem login".
O checkout feito no Bling (pedido que vira Verificado) também entra: o Bling não guarda a hora nem quem fez, então
o servidor confere os pedidos a cada VIGIA_SEG e anota a hora em que viu a mudança (precisão de alguns minutos).

Fica em atividades.db (SQLite, fora do git), guardado por RETENCAO_DIAS. Só Zicri e Michel consultam (VEEM).
O vigia do checkout só roda onde "atividades" estiver aberto em modulos_locais.json (o servidor).
"""

import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "atividades.db")
VISTOS_PATH = os.path.join(BASE_DIR, "checkout_vistos.json")
BRT = timezone(timedelta(hours=-3))
RETENCAO_DIAS = 400
VEEM = {"zicri", "michel"}
VIGIA_SEG = 120
VIGIA_HORAS = (6, 22)          # confere o Bling das 6h às 22h, todos os dias (pega hora extra e sábado)
_trava = threading.Lock()

# Área pelo começo do caminho da rota (o primeiro que casar vale)
AREAS = [
    ("/montagem", "Expedição"), ("/checkout", "Expedição"), ("/expedicao", "Expedição"), ("/tv", "Expedição"),
    ("/sac", "SAC"),
    ("/balanco", "Estoque"), ("/compras", "Estoque"), ("/bling", "Estoque"),
    ("/cmv", "Anúncios"), ("/impostos", "Anúncios"), ("/produtos", "Anúncios"), ("/posicoes", "Anúncios"), ("/tags", "Anúncios"),
    ("/anotacoes", "Anúncios"), ("/pivo", "Anúncios"), ("/sync", "Anúncios"), ("/sistema/sync-catalog", "Anúncios"), ("/amazon", "Anúncios"),
    ("/usuarios", "Administração"), ("/usuario", "Administração"), ("/config", "Administração"), ("/auth", "Administração"),
]
# Nome legível das ações mais comuns (rota com o método). O que não estiver aqui aparece pelo caminho.
NOMES = {
    "POST /cmv/sku": "Ajustou CMV", "POST /cmv/carga": "Carregou planilha de CMV", "POST /cmv/aplicar/{conta}": "Aplicou CMV nos anúncios",
    "POST /posicoes/{conta}/{mlb:path}": "Salvou posição", "POST /tags/{conta}/{mlb}": "Marcou tag", "POST /anotacoes/{conta}/{mlb}": "Comentou no Mapa",
    "DELETE /anotacoes/{conta}/{mlb}/{anotacao_id}": "Apagou comentário do Mapa", "POST /anotacoes/{conta}/{mlb}/{anotacao_id}/favorito": "Favoritou comentário",
    "POST /pivo/{conta}/{mlb}/clip": "Marcou clip no PIVO", "POST /pivo/{conta}/analisar": "Pediu análise PIVO", "POST /produtos/custos": "Alterou custos do anúncio",
    "POST /produtos/{mlb}/arquivar": "Arquivou anúncio", "POST /sync/{conta}": "Sincronizou a conta", "POST /sync/{conta}/{mlb}": "Sincronizou o anúncio",
    "POST /montagem/envios": "Criou envio Full", "POST /montagem/envios/{frete}/produtos/{i}/marcar": "Marcou etapa do Full",
    "DELETE /montagem/envios/{frete}/produtos/{i}/marcar/{tag}": "Desmarcou etapa do Full", "POST /montagem/envios/{frete}/produtos/{i}/responsaveis": "Definiu responsáveis do Full",
    "POST /montagem/envios/{frete}/iniciar": "Iniciou montagem do Full", "POST /montagem/envios/{frete}/prazo": "Mudou prazo do Full",
    "POST /checkout/confirmar": "Checkout no app", "POST /checkout/buscar": "Bipou etiqueta", "POST /expedicao/marca": "Marcou pedido na Expedição",
    "POST /usuarios": "Criou usuário", "PUT /usuarios/{login_alvo}": "Alterou usuário", "POST /usuario/senha": "Trocou a própria senha",
}
# Gravações que não são trabalho de alguém (sistema, login e senha têm registro próprio)
IGNORAR = ("/auth/", "/compartilhado/", "/sistema/abrir-link")


def _con():
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("""CREATE TABLE IF NOT EXISTS eventos (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, usuario TEXT NOT NULL, origem TEXT, area TEXT, acao TEXT,
        objeto TEXT, detalhe TEXT, rota TEXT, status INTEGER)""")
    con.execute("CREATE INDEX IF NOT EXISTS ix_eventos_ts ON eventos (ts)")
    con.execute("CREATE INDEX IF NOT EXISTS ix_eventos_usuario ON eventos (usuario, ts)")
    return con


@contextmanager
def _banco():
    """Conexão que grava e fecha ao sair (o 'with' do sqlite3 só grava, não fecha)."""
    con = _con()
    try:
        with con:
            yield con
    finally:
        con.close()


def agora() -> str:
    return datetime.now(BRT).isoformat(timespec="seconds")


def registrar(usuario: str, area: str, acao: str, objeto: str = "", detalhe: str = "", origem: str = "", rota: str = "",
              status: int = None, ts: str = None) -> None:
    """Grava um evento. Nunca derruba quem chamou: o registro é auxiliar ao trabalho."""
    try:
        with _trava, _banco() as con:
            con.execute("INSERT INTO eventos (ts, usuario, origem, area, acao, objeto, detalhe, rota, status) VALUES (?,?,?,?,?,?,?,?,?)",
                        (ts or agora(), (usuario or "sem login")[:40], origem[:60], area[:30], acao[:80], str(objeto)[:120], str(detalhe)[:300], rota[:120], status))
    except Exception as e:
        print(f"[atividades] não registrou: {type(e).__name__}: {str(e)[:120]}")


def area_da_rota(path: str) -> str:
    return next((a for p, a in AREAS if path.startswith(p)), "Outros")


def registrar_requisicao(usuario: str, origem: str, metodo: str, path: str, rota: str, params: dict, status: int) -> None:
    """Evento de uma gravação feita pela tela. `rota` é o modelo do caminho (ex.: /cmv/sku), `params` os valores dele."""
    if metodo not in ("POST", "PUT", "PATCH", "DELETE") or path.startswith(IGNORAR):
        return
    modelo = f"{metodo} {rota or path}"
    objeto = " ".join(str(v) for k, v in (params or {}).items() if k != "conta")
    detalhe = f"conta {params['conta']}" if (params or {}).get("conta") else ""
    registrar(usuario, area_da_rota(path), NOMES.get(modelo, modelo), objeto, detalhe, origem, modelo, status)


def instalar(app):
    """Middleware: registra toda gravação depois de respondida. O login vem do cookie da sessão (acesso_rede)."""
    @app.middleware("http")
    async def _registro_atividades(request, call_next):
        resposta = await call_next(request)
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            try:
                from marketplaces import acesso_rede
                login = acesso_rede.login_da_sessao(request.cookies.get(acesso_rede.COOKIE))
            except ImportError:
                login = ""
            rota = getattr(request.scope.get("route"), "path", "")
            host = request.client.host if request.client else ""
            threading.Thread(target=registrar_requisicao, daemon=True,
                             args=(login, host, request.method, request.url.path, rota, dict(request.path_params), resposta.status_code)).start()
        return resposta


# ─── consulta (só Zicri e Michel) ─────────────────────────────────────────────

def pode_ver(login: str) -> bool:
    return (login or "").strip().lower() in VEEM


def eventos(de: str, ate: str, usuario: str = None, limite: int = 2000) -> list:
    q, args = "SELECT ts, usuario, origem, area, acao, objeto, detalhe, status FROM eventos WHERE ts >= ? AND ts < ?", [de, ate + "T99"]
    if usuario:
        q += " AND usuario = ?"; args.append(usuario)
    with _banco() as con:
        return [dict(zip(("ts", "usuario", "origem", "area", "acao", "objeto", "detalhe", "status"), r))
                for r in con.execute(q + " ORDER BY ts DESC LIMIT ?", args + [limite])]


def resumo(de: str, ate: str) -> list:
    """Por pessoa e por dia: total, ações por tipo, primeira e última ação e quantas foram fora das 8h às 18h."""
    with _banco() as con:
        linhas = con.execute("SELECT usuario, substr(ts,1,10), area, acao, ts FROM eventos WHERE ts >= ? AND ts < ? AND (status IS NULL OR status < 400)",
                             (de, ate + "T99")).fetchall()
    dias = {}
    for usuario, dia, area, acao, ts in linhas:
        d = dias.setdefault((usuario, dia), {"usuario": usuario, "dia": dia, "total": 0, "acoes": {}, "areas": {}, "primeira": ts, "ultima": ts, "fora_horario": 0})
        d["total"] += 1
        d["acoes"][acao] = d["acoes"].get(acao, 0) + 1
        d["areas"][area] = d["areas"].get(area, 0) + 1
        d["primeira"], d["ultima"] = min(d["primeira"], ts), max(d["ultima"], ts)
        h = ts[11:16]
        if h < "08:00" or h >= "18:00" or datetime.fromisoformat(ts).weekday() >= 5:
            d["fora_horario"] += 1
    return sorted(dias.values(), key=lambda d: (d["dia"], d["usuario"]), reverse=True)


def limpar_antigos() -> None:
    corte = (datetime.now(BRT) - timedelta(days=RETENCAO_DIAS)).isoformat()
    with _trava, _banco() as con:
        con.execute("DELETE FROM eventos WHERE ts < ?", (corte,))


# ─── checkout feito no Bling ──────────────────────────────────────────────────

def _ler_vistos() -> dict:
    try:
        with open(VISTOS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def conferir_checkout(bling: str, pedidos: dict, vistos: dict, agora_iso: str = None) -> list:
    """Compara os pedidos do Bling (numeroLoja -> pedido) com o que já estava Verificado. Devolve os eventos novos e
    atualiza `vistos`. Na primeira leitura de um Bling só guarda a foto: não dá para saber a hora dos que já estavam verificados."""
    from marketplaces.expedicao.bling_checkout import SITUACAO_VERIFICADO
    agora_iso = agora_iso or agora()
    estado = vistos.setdefault(bling, {"verificados": {}, "ultima_leitura": None})
    primeira = estado["ultima_leitura"] is None
    gap = None
    if not primeira:
        gap = round((datetime.fromisoformat(agora_iso) - datetime.fromisoformat(estado["ultima_leitura"])).total_seconds() / 60)
    novos = []
    for loja, p in pedidos.items():
        if (p.get("situacao") or {}).get("id") != SITUACAO_VERIFICADO:
            continue
        pid = str(p.get("id"))
        if pid in estado["verificados"]:
            continue
        estado["verificados"][pid] = agora_iso
        if not primeira:
            novos.append({"ts": agora_iso, "objeto": loja, "detalhe": f"pedido Bling {p.get('numero')}; visto até {gap} min depois do checkout"})
    estado["ultima_leitura"] = agora_iso
    corte = (datetime.fromisoformat(agora_iso) - timedelta(days=15)).isoformat()
    estado["verificados"] = {k: v for k, v in estado["verificados"].items() if v >= corte}
    return novos


def _vigiar():
    from marketplaces.expedicao import bling_checkout
    while True:
        try:
            h = datetime.now(BRT).hour
            if VIGIA_HORAS[0] <= h < VIGIA_HORAS[1]:
                vistos = _ler_vistos()
                for bling in sorted(set(bling_checkout.PARES.values())):
                    try:
                        pedidos = bling_checkout._pedidos(bling)
                    except Exception as e:
                        print(f"[atividades] checkout {bling}: {type(e).__name__}: {str(e)[:120]}")
                        continue
                    for ev in conferir_checkout(bling, pedidos, vistos):
                        registrar("equipe (Bling)", "Expedição", "Checkout no Bling", ev["objeto"], ev["detalhe"], bling, "bling", None, ev["ts"])
                with open(VISTOS_PATH, "w", encoding="utf-8") as f:
                    json.dump(vistos, f, ensure_ascii=False)
            if h == 3:
                limpar_antigos()
        except Exception as e:
            print(f"[atividades] vigia: {type(e).__name__}: {str(e)[:150]}")
        time.sleep(VIGIA_SEG)


def iniciar_vigia():
    threading.Thread(target=_vigiar, daemon=True, name="atividades-checkout").start()
