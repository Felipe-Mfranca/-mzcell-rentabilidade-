"""
pivo_ml.py — Análise PIVO dos anúncios do Mercado Livre (somente leitura)
MZCell Rentabilidade

PIVO = Palavras-chave indexadas, Imagens com quebra de objeções, Vídeo, Otimização (promoção e ADS).
Quatro grupos, cada um com suas verificações:
  P  título com pelo menos 60 caracteres + descrição objetiva (mínimo de 150 caracteres; "objetiva" é avaliação
     humana, a tela avisa se for muito longa)
  I  pelo menos 5 imagens em 1200x1200 (ou maior)
  V  clip/vídeo: o endpoint de clips do ML (/marketplace/items/{id}/clips) responde 403 para o nosso app, então vale
     o video_id do anúncio ou a marcação manual do analista
  O  inscrito em campanha de desconto (started ou pending; promoção de banco/PIX não conta) + publicidade ativa
     (Product Ads com status active). O grupo só fica verde com as duas
Anúncio que não passa em todos os grupos ganha um item de atenção com o que falta. Nada é escrito no ML.

O resultado fica em pivo_cache.json (por conta) e as marcações manuais de clip em pivo_manual.json; os dois
no .gitignore. A análise de uma conta roda em segundo plano (centenas de chamadas).
"""

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(BASE_DIR, "pivo_cache.json")
MANUAL_PATH = os.path.join(BASE_DIR, "pivo_manual.json")
PRODUTOS_PATH = os.path.join(BASE_DIR, "produtos.json")

TITULO_MIN = 60
IMAGENS_MIN = 5
IMAGEM_LADO = 1200
DESCRICAO_MIN = 150
DESCRICAO_LONGA = 3000
PROMOS_IGNORADAS = {"BANK"}                 # crédito de banco/PIX não é campanha de desconto do vendedor
_estado = {}
_trava = threading.Lock()


def _lado(max_size: str):
    try:
        a, b = str(max_size).lower().split("x")
        return int(a), int(b)
    except Exception:
        return 0, 0


def avaliar(item: dict, descricao: str, promocoes: list, ads_status, clip_manual=None) -> dict:
    """Função pura: devolve {'checks': [...], 'pontos': n, 'total': 6, 'passa': bool, 'atencao': [...]}."""
    checks = []

    def add(id_, nome, ok, detalhe):
        checks.append({"id": id_, "nome": nome, "ok": ok, "detalhe": detalhe})

    n = len((item.get("title") or "").strip())
    add("P", "Título com 60 caracteres", n >= TITULO_MIN, f"{n} caracteres (mínimo {TITULO_MIN})")

    pics = item.get("pictures") or []
    bons = sum(1 for p in pics if min(_lado(p.get("max_size"))) >= IMAGEM_LADO)
    add("I", "5 imagens em 1200x1200", bons >= IMAGENS_MIN, f"{bons} de {len(pics)} imagens em {IMAGEM_LADO}x{IMAGEM_LADO} ou maior (mínimo {IMAGENS_MIN})")

    if item.get("video_id") or clip_manual is True:
        add("V", "Clip (vídeo)", True, "vídeo no anúncio" if item.get("video_id") else "confirmado manualmente")
    elif clip_manual is False:
        add("V", "Clip (vídeo)", False, "sem clip (marcado manualmente)")
    else:
        add("V", "Clip (vídeo)", None, "não confirmado: a API não mostra clips, marque manualmente")

    d = len((descricao or "").strip())
    longa = " (longa: confira se está objetiva)" if d > DESCRICAO_LONGA else ""
    add("D", "Descrição objetiva", d >= DESCRICAO_MIN, f"{d} caracteres (mínimo {DESCRICAO_MIN}){longa}")

    ativas = [p for p in promocoes or [] if p.get("status") in ("started", "pending") and p.get("type") not in PROMOS_IGNORADAS]
    add("PROMO", "Campanha de desconto", bool(ativas),
        ", ".join(sorted({(p.get("name") or p.get("type") or "").strip() for p in ativas}))[:120] or "fora de campanha de desconto")

    add("ADS", "Publicidade ativa", ads_status == "active", f"Product Ads: {ads_status or 'sem campanha'}")

    por_id = {c["id"]: c for c in checks}

    def grupo(id_, nome, partes):
        oks = [por_id[x]["ok"] for x in partes]
        ok = True if all(o is True for o in oks) else (False if any(o is False for o in oks) else None)
        return {"id": id_, "nome": nome, "ok": ok, "partes": [por_id[x] for x in partes]}

    grupos = [grupo("P", "Palavras-chave indexadas", ["P", "D"]), grupo("I", "Imagens", ["I"]),
              grupo("V", "Vídeo (clip)", ["V"]), grupo("O", "Otimização: promoção e ADS", ["PROMO", "ADS"])]
    pontos = sum(1 for g in grupos if g["ok"] is True)
    return {"checks": checks, "grupos": grupos, "pontos": pontos, "total": len(grupos), "passa": pontos == len(grupos),
            "atencao": [c["nome"] + ": " + c["detalhe"] for c in checks if c["ok"] is not True]}


def _ler(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def _gravar(path: str, dados: dict):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False)
    os.replace(tmp, path)


def estado(conta: str) -> dict:
    meta = (_ler(CACHE_PATH).get(conta) or {}).get("_meta", {})
    e = dict(_estado.get(conta, {}))
    e["rodando"] = bool(e.get("rodando"))
    e["gerado_em"] = meta.get("gerado_em")
    return e


def resultado(conta: str) -> dict:
    c = _ler(CACHE_PATH).get(conta) or {}
    itens = {k: v for k, v in c.items() if k != "_meta"}
    manual = _ler(MANUAL_PATH).get(conta) or {}
    for mlb, v in itens.items():            # resultados de versões antigas (sem grupos) são refeitos a partir da entrada guardada
        if "grupos" not in v and v.get("_entrada"):
            e = v["_entrada"]
            novo = avaliar(e["item"], "x" * (e.get("descricao_n") or 0), e["promocoes"], e["ads"], (manual.get(mlb) or {}).get("clip"))
            novo["_entrada"] = e
            itens[mlb] = novo
    gerado = (c.get("_meta") or {}).get("gerado_em") or ""
    for mlb, d in (_ler(REMOTO_PATH).get(conta) or {}).items():     # análises feitas em outros computadores
        local = itens.get(mlb)
        if (not local or gerado < (d.get("gerado_em") or "")) and "grupos" in d:
            itens[mlb] = {**d, "remoto": True}
    fora = sum(1 for v in itens.values() if not v["passa"])
    return {"conta": conta, "estado": estado(conta), "total": len(itens), "fora_do_pivo": fora, "itens": itens}


REMOTO_PATH = os.path.join(BASE_DIR, "pivo_compartilhado.json")


def _publico(item: dict, gerado_em: str) -> dict:
    d = {k: v for k, v in item.items() if k != "_entrada"}
    d["gerado_em"] = gerado_em
    return d


def publicar(conta: str, itens: dict, gerado_em: str, autor: str = ""):
    """Sobe o resultado do PIVO para o banco compartilhado (sem os dados brutos da entrada)."""
    try:
        import shared_store
        shared_store.enviar_lote_async("pivo", [(f"{conta}|{m}", _publico(v, gerado_em)) for m, v in itens.items()], autor)
    except Exception:
        pass


def aplicar_remoto(registros: list):
    """Guarda o que outro computador analisou; resultado() usa quando o item local não existe ou é mais antigo."""
    rem = _ler(REMOTO_PATH)
    for r in registros:
        try:
            conta, mlb = r["chave"].split("|", 1)
            d = json.loads(r["dados"])
        except (ValueError, KeyError):
            continue
        atual = rem.setdefault(conta, {}).get(mlb)
        if r.get("removido"):
            rem[conta].pop(mlb, None)
        elif not atual or (atual.get("gerado_em") or "") <= (d.get("gerado_em") or ""):
            rem[conta][mlb] = d
    _gravar(REMOTO_PATH, rem)


def marcar_clip(conta: str, mlb: str, tem_clip):
    """tem_clip: True, False ou None (limpa a marcação). Recalcula o item no cache sem chamar o ML."""
    manual = _ler(MANUAL_PATH)
    m = manual.setdefault(conta, {})
    if tem_clip is None:
        m.pop(mlb, None)
    else:
        m[mlb] = {"clip": bool(tem_clip)}
    _gravar(MANUAL_PATH, manual)
    cache = _ler(CACHE_PATH)
    it = (cache.get(conta) or {}).get(mlb)
    if it:
        base = it["_entrada"]
        novo = avaliar(base["item"], "x" * base["descricao_n"], base["promocoes"], base["ads"], None if tem_clip is None else bool(tem_clip))
        novo["_entrada"] = base
        cache[conta][mlb] = novo
        _gravar(CACHE_PATH, cache)
        return novo
    return None


def _analisar(conta: str, ml, mlbs: list):
    try:
        manual = (_ler(MANUAL_PATH).get(conta) or {})
        itens = {}
        _estado[conta].update(etapa="lendo anúncios")
        for i in range(0, len(mlbs), 20):
            st, b = ml.get("/items", ids=",".join(mlbs[i:i + 20]), attributes="id,title,pictures,video_id,status")
            for e in b or []:
                if e.get("code") == 200:
                    itens[e["body"]["id"]] = e["body"]

        def um(mlb):
            it = itens.get(mlb)
            if not it:
                return mlb, None
            st, d = ml.get(f"/items/{mlb}/description")
            desc = (d or {}).get("plain_text", "") if st == 200 else ""
            st, pr = ml.get(f"/seller-promotions/items/{mlb}", app_version="v2")
            promos = [{"type": p.get("type"), "status": p.get("status"), "name": p.get("name")} for p in pr] if st == 200 and isinstance(pr, list) else []
            ads = None
            st, a = ml.get(f"/marketplace/advertising/MLB/product_ads/ads/{mlb}")
            if st == 200 and isinstance(a, dict):
                ads = a.get("status")
            slim = {"title": it.get("title"), "pictures": [{"max_size": p.get("max_size")} for p in it.get("pictures") or []],
                    "video_id": it.get("video_id")}
            clip = (manual.get(mlb) or {}).get("clip")
            r = avaliar(slim, desc, promos, ads, clip)
            r["_entrada"] = {"item": slim, "descricao_n": len(desc.strip()), "promocoes": promos, "ads": ads}
            return mlb, r

        _estado[conta].update(etapa=f"analisando {len(mlbs)} anúncios")
        out = {}
        with ThreadPoolExecutor(max_workers=5) as ex:
            for mlb, r in ex.map(um, mlbs):
                if r:
                    out[mlb] = r
        out["_meta"] = {"gerado_em": datetime.now().isoformat(timespec="seconds")}
        cache = _ler(CACHE_PATH)
        cache[conta] = out
        _gravar(CACHE_PATH, cache)
        _estado[conta].update(erro=None, etapa="concluído")
        publicar(conta, {k: v for k, v in out.items() if k != "_meta"}, out["_meta"]["gerado_em"])
    except Exception as e:
        _estado[conta].update(erro=f"{type(e).__name__}: {str(e)[:200]}", etapa="falhou")
    finally:
        _estado[conta]["rodando"] = False


def iniciar(conta: str, ml, produtos_path: str = None) -> dict:
    """Dispara a análise da conta em segundo plano. `ml` é o cliente (reposicao_full.ML)."""
    produtos = _ler(produtos_path or PRODUTOS_PATH)
    mlbs = [m for m, p in produtos.items() if p.get("conta") == conta and not p.get("arquivado")]
    with _trava:
        if _estado.get(conta, {}).get("rodando"):
            return estado(conta)
        _estado[conta] = {"rodando": True, "etapa": "iniciando", "erro": None}
        threading.Thread(target=_analisar, args=(conta, ml, mlbs), daemon=True).start()
    return estado(conta)
