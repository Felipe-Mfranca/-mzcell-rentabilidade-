"""
marketplaces/radar/parse.py — Regras puras do Radar ML (sem rede e sem navegador)
MZ App

Tudo o que decide "em que posição o anúncio aparece" fica aqui, em funções simples e testáveis. O robô
(robo.py) só entrega os links dos cards lidos na página de busca; quem interpreta é este arquivo.
"""

import re
import unicodedata
from urllib.parse import parse_qs, urlsplit

POR_PAGINA = 50
ORGANICO, PATROCINADO = "organico", "pad"

TEXTOS_CAPTCHA = ("por segurança, complete esta etapa",)
FRAGMENTOS_LOGIN = ("/login", "/gz/account-verification", "registration")


def normalizar_keyword(keyword: str) -> str:
    """Minúsculas e espaços únicos: uma busca serve a todos os anúncios do mesmo termo."""
    return re.sub(r"\s+", " ", str(keyword or "")).strip().lower()


def normalizar_mlb(valor) -> str:
    """'5352112284', 'mlb-5352112284' ou 'MLB5352112284' viram 'MLB5352112284'; inválido vira ''."""
    m = re.fullmatch(r"(?:MLB)?-?(\d{6,})", str(valor or "").strip().upper())
    return f"MLB{m.group(1)}" if m else ""


def slug(keyword: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", normalizar_keyword(keyword)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", sem_acento).strip("-")


# mesmo escopo da opção "em todo Mercado Livre" da barra de busca (a conta neutra é de Negócios; sem isto a
# ordem pode ser outra). A vitrine "em Mercado Livre Negócios" usa /e/negocios/ e nunca é usada pelo robô.
ESCOPO_TODO_ML = "?sb=all_mercadolibre"


def url_busca(keyword: str, pagina: int = 1) -> str:
    base = f"https://lista.mercadolivre.com.br/{slug(keyword)}"
    if pagina <= 1:
        return base + ESCOPO_TODO_ML
    return f"{base}_Desde_{(pagina - 1) * POR_PAGINA + 1}_NoIndex_True{ESCOPO_TODO_ML}"


def detectar_bloqueio(texto: str, url: str = "") -> str:
    """'captcha', 'login' ou '' (sem bloqueio). Dúvida = sem bloqueio; quem chama confere se há cards."""
    t = (texto or "").lower()
    if any(x in t for x in TEXTOS_CAPTCHA):
        return "captcha"
    if any(x in (url or "").lower() for x in FRAGMENTOS_LOGIN):
        return "login"
    return ""


def extrair_viewer(cabecalho: str) -> str:
    """Nome da conta logada no navegador: o texto entre 'Contato' e 'Compras' do cabeçalho do ML."""
    m = re.search(r"Contato\s+(.*?)\s+Compras", cabecalho or "", re.S)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


def _params(href: str) -> dict:
    partes = urlsplit(href or "")
    out = {}
    for trecho in (partes.query, partes.fragment):
        for k, v in parse_qs(trecho).items():
            out.setdefault(k, v[0])
    return out


def parse_link(href: str):
    """{'wid', 'position', 'type'} de um link de card, ou None se o link não traz o `wid` (= MLB)."""
    p = _params(href)
    wid = normalizar_mlb(p.get("wid"))
    if not wid:
        return None
    try:
        posicao = int(p["position"])
    except (KeyError, ValueError):
        posicao = None
    tipo = PATROCINADO if (p.get("type") or "").lower() == "pad" else ORGANICO
    return {"wid": wid, "position": posicao, "type": tipo, "href": href}


def posicao_geral(ordem_organica: int, pagina: int) -> int:
    """Posição geral na busca: cada página tem 50 posições orgânicas (ADS não conta).
    Ex.: 10º orgânico da página 2 = (2-1)*50 + 10 = posição #60."""
    return (pagina - 1) * POR_PAGINA + ordem_organica


def url_limpa(href: str) -> str:
    partes = urlsplit(href or "")
    return f"{partes.scheme}://{partes.netloc}{partes.path}" if partes.scheme else ""


def eh_ads(hrefs: list) -> bool:
    """Card patrocinado (ADS): todos os links com MLB do card são do tipo `pad`. ADS não ocupa posição orgânica."""
    links = [lk for lk in (parse_link(h) for h in hrefs) if lk]
    return bool(links) and all(lk["type"] == PATROCINADO for lk in links)


def resolver(cards: list, mlb: str, pagina: int):
    """Posição orgânica do `mlb` numa página de busca já lida.

    `cards`: lista, na ordem do DOM, de listas de links (hrefs) de cada card.
    A ordem na página conta só os cards orgânicos (os de ADS são pulados); a posição geral é
    (página - 1) * 50 + essa ordem. Anúncio que só aparece como ADS não conta: devolve None.
    Nunca usa o contador `position` do link e nunca inventa posição.
    """
    mlb = normalizar_mlb(mlb)
    ordem = 0
    for hrefs in cards:
        if eh_ads(hrefs):
            continue
        ordem += 1
        for h in hrefs:
            lk = parse_link(h)
            if lk and lk["wid"] == mlb and lk["type"] == ORGANICO:
                return {"pagina": pagina, "posicao": posicao_geral(ordem, pagina), "pagina_pos": ordem,
                        "ad_type": ORGANICO, "url": url_limpa(h)}
    return None


def achar_paginas(wids_por_pagina: dict, mlbs: list) -> dict:
    """{mlb: menor página em que aparece} a partir de {página: [wids]} (descoberta rápida, antes de abrir a página)."""
    achou = {}
    for pagina in sorted(wids_por_pagina):
        presentes = {normalizar_mlb(w) for w in (wids_por_pagina[pagina] or [])}
        for mlb in mlbs:
            if mlb in presentes and mlb not in achou:
                achou[mlb] = pagina
    return achou


def variacao(atual, anterior) -> str:
    """'subiu' / 'caiu' / 'igual' / 'fora' (saiu das páginas verificadas) / 'novo' / ''. Menor posição = melhor."""
    if atual is None and anterior is None:
        return ""
    if atual is None:
        return "fora"
    if anterior is None:
        return "novo"
    if atual < anterior:
        return "subiu"
    return "caiu" if atual > anterior else "igual"
