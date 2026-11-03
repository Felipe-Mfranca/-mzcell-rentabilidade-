"""
marketplaces/radar/robo.py — Robô do Radar ML: lê a posição dos anúncios na busca pública do Mercado Livre
MZ App

Roda em UMA máquina só (a do Eduardo): radar_config.json com "robo_ativo": true. As demais máquinas não ligam o robô;
só leem o que ele grava (store.py → planilha compartilhada) e pedem "atualizar agora" pela fila.

Por que navegador e não a API: a busca da API do ML (/sites/MLB/search) responde 403 de forma geral. A posição é
lida da página pública de busca (lista.mercadolivre.com.br), numa conta NEUTRA de pesquisa — logado como vendedor
o ML personaliza a busca e os próprios anúncios sobem.

A orquestração (`verificar_keyword`, `executar`) não conhece o Playwright: ela fala com um "navegador" com quatro
métodos (viewer, cards, wids_paginas, fechar). `NavegadorPlaywright` é a implementação real; os testes usam uma falsa.
Instalação só na máquina do robô: pip install -r requirements-radar.txt  (usa o Chrome instalado).
"""

import json
import os
import random
import threading
import time
from datetime import datetime, timedelta, timezone

from marketplaces.radar import parse, store

BASE_DIR = store.BASE_DIR
CONFIG_PATH = os.path.join(BASE_DIR, "radar_config.json")
PADRAO = {"robo_ativo": False, "horarios": ["08:30", "13:30", "19:30"], "viewer": "SM ONLINE", "max_paginas": 20,
          "navegador": "chrome", "headless": False, "perfil": os.path.join(BASE_DIR, "radar_perfil"),
          "limite_execucao_min": 75}
LOTE_PAGINAS = 5
BRT = timezone(timedelta(hours=-3))    # sem horário de verão desde 2019; evita depender de tzdata no Windows
MUDANCA_MIN = 5                        # quem subiu/caiu 5+ posições entra no resumo


class Bloqueio(Exception):
    """Captcha, pedido de login ou conta errada: a execução para e nada é gravado como 'não encontrado'."""


BRAVE_EXE = r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"


def perfil_de(cfg: dict) -> str:
    """Pasta do perfil do navegador do robô. Cada navegador tem a sua (não podem dividir a mesma pasta)."""
    nav = cfg.get("navegador")
    return cfg["perfil"] + f"_{nav}" if nav in ("firefox", "brave") else cfg["perfil"]


def config() -> dict:
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return {**PADRAO, **json.load(f)}
    except (FileNotFoundError, ValueError):
        return dict(PADRAO)


# ─── seleção e verificação (sem navegador real) ──────────────────────────────

def selecionar(escopo: str = "todas", mlb: str = None, keyword: str = None) -> dict:
    """{palavra-chave normalizada: [(conta, mlb), ...]}. Uma busca serve a todos os MLBs do termo.

    escopo: "todas", ou uma conta (ex.: "meli02"). Com `mlb` + `keyword`, só aquele par (mesmo fora da lista).
    """
    if mlb and keyword:
        conta = escopo if escopo not in ("", "todas", None) else (next(
            (w["conta"] for w in store.listar_watch() if w["mlb"] == parse.normalizar_mlb(mlb)), ""))
        return {parse.normalizar_keyword(keyword): [(conta, parse.normalizar_mlb(mlb))]}
    grupos = {}
    for w in store.listar_watch(None if escopo in ("", "todas", None) else escopo, so_ativos=True):
        alvos = grupos.setdefault(w["keyword"], [])
        if (w["conta"], w["mlb"]) not in alvos:
            alvos.append((w["conta"], w["mlb"]))
    return grupos


def verificar_keyword(nav, keyword: str, mlbs: list, max_paginas: int = 20) -> dict:
    """{mlb: {encontrado, pagina, posicao, pagina_pos, ad_type, url, max_pagina}} para uma palavra-chave.

    Página 1 lida direto; páginas 2..max sempre (nunca para pelo "total de resultados"): baixa em lotes só para
    descobrir em que página cada MLB está e depois abre essa página e lê a posição no DOM. Só para antes se todos
    foram achados ou se a página vier de fato vazia (confirmado abrindo-a).
    """
    achados, ultima = {}, 1
    cards = nav.cards(keyword, 1)
    if not cards:   # página 1 sem nenhum card = a busca não carregou (bloqueio); nunca vira "não encontrado"
        raise Bloqueio(f"a busca de «{keyword}» não carregou resultados no navegador do robô (possível bloqueio do "
                       "Mercado Livre). Nada foi gravado; abra o navegador do robô e confira")
    for mlb in mlbs:
        r = parse.resolver(cards, mlb, 1)
        if r:
            achados[mlb] = r
    pagina = 2
    while any(m not in achados for m in mlbs) and pagina <= max_paginas:
        lote = list(range(pagina, min(pagina + LOTE_PAGINAS, max_paginas + 1)))
        wids = nav.wids_paginas(keyword, lote)
        pendentes = [m for m in mlbs if m not in achados]
        fim = False
        for p in lote:
            w = wids.get(p)
            if w is None:                 # falha de rede na descoberta: abre a página para decidir
                alvo_mlbs = [m for m in pendentes if m not in achados]
            elif not w:                   # sem nenhum link: só vale como "fim" depois de abrir e conferir
                if not nav.cards(keyword, p):
                    fim = True
                    break
                alvo_mlbs = [m for m in pendentes if m not in achados]
            else:
                # abre toda página em que o MLB aparece: se lá ele estiver só como ADS, segue para a próxima
                presentes = {parse.normalizar_mlb(x) for x in w}
                alvo_mlbs = [m for m in pendentes if m in presentes and m not in achados]
            ultima = p
            if alvo_mlbs:
                cards_p = nav.cards(keyword, p)
                for m in alvo_mlbs:
                    r = parse.resolver(cards_p, m, p)
                    if r:
                        achados[m] = r
            if all(m in achados for m in mlbs):
                break
        if fim or all(m in achados for m in mlbs):
            break
        pagina = lote[-1] + 1
    return {m: ({"encontrado": True, "max_pagina": achados[m]["pagina"], **achados[m]} if m in achados
                else {"encontrado": False, "max_pagina": ultima}) for m in mlbs}


def executar(escopo: str = "todas", mlb: str = None, keyword: str = None, nav=None, pausa=None,
             origem: str = "robo", pedido_atendido: str = "") -> dict:
    """Uma execução completa. Devolve {resumo, verificados, termos, encontrados, fora, mudancas, erro}."""
    cfg = config()
    if not store.iniciar_execucao(pedido_atendido):
        return {"resumo": "já há uma verificação em andamento", "erro": "", "verificados": 0}
    abriu_aqui = nav is None
    verificados = encontrados = termos = 0
    mudancas, erro, prazo = [], "", time.time() + int(cfg["limite_execucao_min"]) * 60
    pausa = pausa or (lambda: time.sleep(random.uniform(3, 8)))
    try:
        grupos = selecionar(escopo, mlb, keyword)
        if not grupos:
            raise Bloqueio("nada para verificar: a lista de acompanhamento está vazia")
        nav = nav or NavegadorPlaywright(cfg)
        esperado, viewer = cfg.get("viewer") or "", nav.viewer()
        if esperado and viewer != esperado:
            raise Bloqueio(f"o navegador do robô está na conta «{viewer or 'desconhecida'}», não em «{esperado}». "
                           "Entre na conta neutra de pesquisa e tente de novo (nada foi gravado)")
        for kw, alvos in grupos.items():
            if time.time() > prazo:
                erro = "interrompido: passou do limite de tempo da execução"
                break
            res = verificar_keyword(nav, kw, sorted({m for _, m in alvos}), int(cfg["max_paginas"]))
            itens = [{"conta": c, "mlb": m, "keyword": kw, "viewer": viewer, **res[m]} for c, m in alvos]
            antes = {(c, m): next((r.get("posicao") for r in store.listar_resultados(c)
                                   if r["mlb"] == m and r["keyword"] == kw), None) for c, m in alvos}
            store.registrar_resultados(itens, origem)
            termos += 1
            for it in itens:
                verificados += 1
                encontrados += 1 if it["encontrado"] else 0
                a, n = antes[(it["conta"], it["mlb"])], it.get("posicao")
                if a is not None and n is not None and abs(n - a) >= MUDANCA_MIN:
                    mudancas.append({"conta": it["conta"], "mlb": it["mlb"], "keyword": kw, "de": a, "para": n})
            pausa()
    except Bloqueio as e:
        erro = str(e)
    except Exception as e:     # nunca derruba o servidor por causa do robô
        erro = f"{type(e).__name__}: {e}"[:300]
    finally:
        if abriu_aqui and nav is not None:
            try:
                nav.fechar()
            except Exception:
                pass
    resumo = (f"{verificados} anúncios verificados em {termos} termos · {encontrados} encontrados · "
              f"{verificados - encontrados} fora das {cfg['max_paginas']} primeiras páginas")
    store.finalizar_execucao(resumo, erro, mudancas)
    return {"resumo": resumo, "verificados": verificados, "termos": termos, "encontrados": encontrados,
            "fora": verificados - encontrados, "mudancas": mudancas, "erro": erro}


# ─── navegador real (Playwright) ─────────────────────────────────────────────

JS_CARDS = """() => Array.from(document.querySelectorAll('li.ui-search-layout__item'))
  .map(li => Array.from(li.querySelectorAll('a[href]')).map(a => a.href))"""

JS_DESCOBRIR = """async (paginas) => Promise.all(paginas.map(async ([pagina, url]) => {
  try {
    const r = await fetch(url, {credentials: 'include'});
    if (!r.ok) return [pagina, null];
    const html = await r.text();
    const wids = new Set(); const re = /wid(?:=|%3D)(MLB\\d+)/g; let m;
    while ((m = re.exec(html)) !== null) wids.add(m[1]);
    return [pagina, Array.from(wids)];
  } catch (e) { return [pagina, null]; }
}))"""


class NavegadorPlaywright:
    """Chrome com perfil próprio (já logado na conta neutra). Abre uma janela; pode ficar minimizada."""

    def __init__(self, cfg: dict):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise Bloqueio("Playwright não instalado nesta máquina (pip install -r requirements-radar.txt)")
        self._pw = sync_playwright().start()
        opcoes = {"headless": bool(cfg["headless"]), "viewport": {"width": 1280, "height": 900}, "locale": "pt-BR"}
        if cfg.get("navegador") != "firefox":
            # minimizada, a página não pode "dormir": sem estas opções o Chromium segura timers e a renderização
            opcoes["args"] = ["--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding",
                              "--disable-background-timer-throttling"]
        if cfg.get("navegador") == "brave":
            # Brave (base Chromium) com perfil só do robô; o login é feito à mão nesse perfil, sem automação
            self._ctx = self._pw.chromium.launch_persistent_context(
                perfil_de(cfg), executable_path=cfg.get("executavel") or BRAVE_EXE, **opcoes)
        elif cfg.get("navegador") == "firefox":
            # Firefox do Playwright (python -m playwright install firefox), com perfil próprio: o login da conta
            # neutra feito nele só vale para ele
            self._ctx = self._pw.firefox.launch_persistent_context(perfil_de(cfg), **opcoes)
        else:
            if cfg.get("navegador"):
                opcoes["channel"] = cfg["navegador"]
            self._ctx = self._pw.chromium.launch_persistent_context(perfil_de(cfg), **opcoes)
        self._page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        self._fechar_abas_extras()          # o navegador reabre as abas da sessão anterior: fica só uma
        if cfg.get("minimizado", True) and not cfg["headless"] and cfg.get("navegador") != "firefox":
            self._minimizar()

    def _fechar_abas_extras(self):
        for pg in list(self._ctx.pages):
            if pg is not self._page:
                try:
                    pg.close()
                except Exception:
                    pass

    def _minimizar(self):
        """Minimiza a janela (Chrome/Brave, via CDP) para não atrapalhar quem usa o PC. Falha aqui não para o robô."""
        try:
            s = self._ctx.new_cdp_session(self._page)
            janela = s.send("Browser.getWindowForTarget")
            s.send("Browser.setWindowBounds", {"windowId": janela["windowId"], "bounds": {"windowState": "minimized"}})
        except Exception:
            pass

    def _abrir(self, keyword: str, pagina: int):
        url = parse.url_busca(keyword, pagina)
        for tentativa in range(3):                       # até 2 recarregamentos
            self._page.goto(url, wait_until="domcontentloaded", timeout=60000)
            texto = self._page.inner_text("body")[:4000]
            bloqueio = parse.detectar_bloqueio(texto, self._page.url)
            if bloqueio == "captcha":
                raise Bloqueio("o Mercado Livre pediu verificação (captcha). Rode o navegador do robô, resolva e tente de novo")
            if bloqueio == "login":
                raise Bloqueio("o Mercado Livre pediu login. Entre na conta neutra de pesquisa no navegador do robô")
            try:
                self._page.wait_for_selector("li.ui-search-layout__item", timeout=40000)
                return
            except Exception:
                continue

    def viewer(self) -> str:
        self._page.goto("https://www.mercadolivre.com.br/", wait_until="domcontentloaded", timeout=60000)
        texto = self._page.evaluate("() => (document.querySelector('header') || document.body).innerText")
        return parse.extrair_viewer(texto)

    def cards(self, keyword: str, pagina: int) -> list:
        self._abrir(keyword, pagina)
        return self._page.evaluate(JS_CARDS)

    def wids_paginas(self, keyword: str, paginas: list) -> dict:
        resp = self._page.evaluate(JS_DESCOBRIR, [[p, parse.url_busca(keyword, p)] for p in paginas])
        return {p: w for p, w in resp}

    def fechar(self):
        """Fecha as abas antes da janela: fica só uma, em branco, e a próxima abertura não acumula abas."""
        try:
            self._fechar_abas_extras()
            self._page.goto("about:blank", timeout=15000)
        except Exception:
            pass
        try:
            self._ctx.close()
        finally:
            self._pw.stop()


# ─── agendador e fila (thread da máquina do robô) ────────────────────────────

def _agendada_pendente(cfg: dict, agora: datetime):
    """Rótulo do horário de hoje mais recente já vencido e ainda não executado, ou ''.
    Se o robô ficou desligado, roda uma vez só (o horário mais recente), não uma para cada horário perdido."""
    ultimo = (store.status().get("execucao") or {}).get("agendado_ultimo") or ""
    vencidos = [h for h in sorted(cfg["horarios"]) if f"{agora:%H:%M}" >= h]
    rotulo = f"{agora:%Y-%m-%d} {vencidos[-1]}" if vencidos else ""
    return rotulo if rotulo > ultimo else ""


def tique(agora: datetime = None, **kw):
    """Um passo do laço: atende pedido da fila ou o horário agendado. Devolve o resultado da execução ou None."""
    agora = agora or datetime.now(BRT)
    cfg = config()
    if store.estado() == "running":
        return None
    ped = store.status().get("pedido") or {}
    if store.estado() == "queued":
        return executar(ped.get("escopo") or "todas", ped.get("mlb") or None, ped.get("keyword") or None,
                        pedido_atendido=ped["pedido_em"], **kw)
    rotulo = _agendada_pendente(cfg, agora)
    if rotulo:
        store.marcar_agendada(rotulo)
        return executar("todas", origem="robo", **kw)
    return None


def iniciar_robo():
    """Liga o laço do robô se radar_config.json tiver "robo_ativo": true. Devolve a thread ou None."""
    if not config().get("robo_ativo"):
        return None

    def laco():
        while True:
            try:
                tique()
            except Exception:
                pass
            time.sleep(30)

    t = threading.Thread(target=laco, daemon=True, name="radar-robo")
    t.start()
    return t
