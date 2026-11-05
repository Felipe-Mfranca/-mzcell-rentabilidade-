"""
marketplaces/montagem/armazenamento.py — Envios ao Full em montagem (arquivo montagem_envios.json, fora do git)
MZ App

Etapas (tags) por produto, nesta ordem:  T = estoque transferido no Bling · E = etiquetado ·
OK = pronto (etiquetado e caixa master lacrada). OK só vale depois de T e E.
Quem marca: o ESTOQUISTA do produto ou o LÍDER (o embalador não marca). Cada marca guarda quem, o papel e quando:
isso alimenta a produtividade, que aparece só na gestão. A TV mostra apenas andamento e responsáveis.
Não escreve em nenhuma API: é só controle interno.

Fila: todo envio novo nasce com status "fila" (aguardando início), numa ordem que o líder ou o estoquista ajustam,
com a coleta programada e o "pronto até" podendo ficar pendente. Só o LÍDER inicia (status "montagem"); antes
disso ninguém marca etapas. Envios antigos, sem status, contam como já em montagem.
"""

import json
import os
import threading
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CAMINHO = os.path.join(BASE_DIR, "montagem_envios.json")
TAGS = ("T", "E", "OK")
CANAIS = ("meli", "amazon", "shopee")   # Full do Mercado Livre, FBA da Amazon e Fulfillment da Shopee: mesma fila
_trava = threading.Lock()


class ErroMontagem(Exception):
    def __init__(self, msg, status=400):
        super().__init__(msg)
        self.status = status


def agora() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _ler() -> dict:
    if not os.path.isfile(CAMINHO):
        return {"envios": {}}
    with open(CAMINHO, encoding="utf-8") as f:
        return json.load(f)


def _gravar(dados: dict):
    tmp = CAMINHO + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=1)
    os.replace(tmp, CAMINHO)


def status(envio: dict) -> str:
    return envio.get("status") or "montagem"


def _fila(dados) -> list:
    return sorted((e for e in dados["envios"].values() if status(e) == "fila"), key=lambda e: (e.get("ordem") or 0, e["chave"]))


def listar(incluir_concluidos: bool = True) -> list:
    """Fila primeiro (na ordem programada), depois as montagens por prazo."""
    dados = _ler()
    fila = _fila(dados)
    resto = [e for e in dados["envios"].values() if status(e) != "fila"]
    if not incluir_concluidos:
        resto = [e for e in resto if not e.get("concluido_em")]
    return fila + sorted(resto, key=lambda e: (e.get("prazo") or "", e["chave"]))


def criar(numero: str, conta: str, coleta: str, prazo: str, produtos: list, criado_por: str = "", canal: str = "meli") -> dict:
    """`numero` é o identificador do envio no canal (Frete # no Meli, ID FBA na Amazon, número do inbound na Shopee).
    A chave interna é canal-numero, para números iguais de canais diferentes não colidirem.
    Entra no fim da fila; o "pronto até" (`prazo`) pode ficar pendente até o líder iniciar."""
    numero = str(numero).strip().lstrip("#")
    canal = (canal or "meli").lower()
    if canal not in CANAIS:
        raise ErroMontagem("Canal inválido. Use meli, amazon ou shopee.")
    if not numero:
        raise ErroMontagem("Informe o número do envio.")
    if not produtos:
        raise ErroMontagem("O envio não tem produtos.")
    itens = []
    for p in produtos:
        un = int(p.get("unidades") or 0)
        if un <= 0 or not str(p.get("sku", "")).strip():
            raise ErroMontagem(f"Produto inválido (SKU e unidades obrigatórios): {p.get('sku') or '?'}")
        itens.append({"codigo_ml": p.get("codigo_ml", ""), "ean": p.get("ean", ""), "sku": str(p["sku"]).strip(),
                      "titulo": p.get("titulo", ""), "unidades": un,
                      "estoquista": (p.get("estoquista") or "").strip(), "embalador": (p.get("embalador") or "").strip(),
                      "etapas": {}})
    chave = f"{canal}-{numero}"
    with _trava:
        dados = _ler()
        if chave in dados["envios"]:
            raise ErroMontagem(f"O envio {numero} ({canal}) já foi cadastrado.", 409)
        ordem = max([e.get("ordem") or 0 for e in _fila(dados)], default=0) + 1
        envio = {"chave": chave, "canal": canal, "numero": numero, "frete": chave, "conta": conta, "coleta": coleta,
                 "prazo": prazo or "", "criado_em": agora(), "criado_por": criado_por, "produtos": itens, "concluido_em": None,
                 "status": "fila", "ordem": ordem}
        dados["envios"][chave] = envio
        _gravar(dados)
    return envio


def _achar(dados, frete, idx):
    envio = dados["envios"].get(str(frete))
    if not envio:
        raise ErroMontagem("Envio não encontrado.", 404)
    if not 0 <= idx < len(envio["produtos"]):
        raise ErroMontagem("Produto não encontrado.", 404)
    return envio, envio["produtos"][idx]


def _fechar_se_completo(envio):
    pronto = all("OK" in p["etapas"] for p in envio["produtos"])
    envio["concluido_em"] = (envio.get("concluido_em") or agora()) if pronto else None


PAPEIS = ("estoquista", "lider")


def marcar(frete: str, idx: int, tag: str, por: str, papel: str = "estoquista") -> dict:
    tag = tag.upper()
    papel = (papel or "").lower()
    if tag not in TAGS:
        raise ErroMontagem("Tag inválida. Use T, E ou OK.")
    if papel not in PAPEIS:
        raise ErroMontagem("Só o estoquista do produto ou o líder marcam as etapas.")
    if not str(por).strip():
        raise ErroMontagem("Informe quem está marcando.")
    with _trava:
        dados = _ler()
        envio, prod = _achar(dados, frete, idx)
        if status(envio) == "fila":
            raise ErroMontagem("Este Full ainda está na fila. O líder precisa clicar em Iniciar antes das marcas.", 409)
        if papel == "estoquista" and str(por).strip().lower() != (prod.get("estoquista") or "").strip().lower():
            raise ErroMontagem("Este produto é de outro estoquista. Peça ao líder ou ao responsável para marcar.", 403)
        if tag == "OK" and not ("T" in prod["etapas"] and "E" in prod["etapas"]):
            raise ErroMontagem("OK exige as marcas T (estoque transferido) e E (etiquetado) antes.", 409)
        if tag not in prod["etapas"]:
            prod["etapas"][tag] = {"por": str(por).strip(), "papel": papel, "em": agora()}
        _fechar_se_completo(envio)
        _gravar(dados)
        return envio


def desmarcar(frete: str, idx: int, tag: str) -> dict:
    tag = tag.upper()
    with _trava:
        dados = _ler()
        envio, prod = _achar(dados, frete, idx)
        prod["etapas"].pop(tag, None)
        if tag in ("T", "E"):
            prod["etapas"].pop("OK", None)  # sem T ou E, o OK perde o sentido
        _fechar_se_completo(envio)
        _gravar(dados)
        return envio


def atribuir(frete: str, idx: int, estoquista: str = None, embalador: str = None) -> dict:
    with _trava:
        dados = _ler()
        envio, prod = _achar(dados, frete, idx)
        if estoquista is not None:
            prod["estoquista"] = estoquista.strip()
        if embalador is not None:
            prod["embalador"] = embalador.strip()
        _gravar(dados)
        return envio


def alterar_prazo(frete: str, prazo: str, coleta: str = None) -> dict:
    with _trava:
        dados = _ler()
        envio = dados["envios"].get(str(frete))
        if not envio:
            raise ErroMontagem("Envio não encontrado.", 404)
        if not prazo and status(envio) != "fila":
            raise ErroMontagem('Full em montagem precisa do "pronto até".')
        envio["prazo"] = prazo or ""
        if coleta is not None:
            envio["coleta"] = coleta
        _gravar(dados)
        return envio


def iniciar(frete: str, por: str, papel: str) -> dict:
    """Só o líder tira o envio da fila; o "pronto até" precisa estar definido."""
    if (papel or "").lower() != "lider":
        raise ErroMontagem("Só o líder inicia um Full da fila.", 403)
    if not str(por).strip():
        raise ErroMontagem("Informe quem está iniciando.")
    with _trava:
        dados = _ler()
        envio = dados["envios"].get(str(frete))
        if not envio:
            raise ErroMontagem("Envio não encontrado.", 404)
        if status(envio) != "fila":
            raise ErroMontagem("Este Full já foi iniciado.", 409)
        if not envio.get("prazo"):
            raise ErroMontagem('Defina o "pronto até" (data e hora) antes de iniciar.')
        envio.update(status="montagem", ordem=None, iniciado_em=agora(), iniciado_por=str(por).strip())
        _gravar(dados)
        return envio


def mover(frete: str, posicao: int) -> dict:
    """Leva o envio para a posição (1 = o próximo a começar) e renumera a fila."""
    with _trava:
        dados = _ler()
        envio = dados["envios"].get(str(frete))
        if not envio or status(envio) != "fila":
            raise ErroMontagem("Envio não está na fila.", 404)
        fila = [e for e in _fila(dados) if e is not envio]
        fila.insert(max(0, min(int(posicao) - 1, len(fila))), envio)
        for i, e in enumerate(fila, 1):
            e["ordem"] = i
        _gravar(dados)
        return envio


def remover(frete: str):
    """Envio da Amazon excluído fica marcado como dispensado, para a pré-carga automática não trazê-lo de volta."""
    with _trava:
        dados = _ler()
        envio = dados["envios"].pop(str(frete), None)
        if envio is None:
            raise ErroMontagem("Envio não encontrado.", 404)
        if envio.get("canal") == "amazon":
            dados.setdefault("dispensados", []).append(envio["chave"])
        _gravar(dados)


def dispensados() -> set:
    return set(_ler().get("dispensados") or [])


def resumo(envio: dict) -> dict:
    """Progresso para a TV: unidades prontas (produto com OK), produtos por etapa."""
    prods = envio["produtos"]
    total = sum(p["unidades"] for p in prods)
    pronto = sum(p["unidades"] for p in prods if "OK" in p["etapas"])
    return {"unidades_total": total, "unidades_prontas": pronto,
            "produtos_total": len(prods), "produtos_ok": sum(1 for p in prods if "OK" in p["etapas"]),
            "percentual": round(100 * pronto / total, 1) if total else 0.0}


def para_tv(envio: dict) -> dict:
    """Versão do envio para a TV: andamento e responsáveis, sem quem marcou nem horários (produtividade fica na gestão)."""
    e = {k: envio.get(k) for k in ("chave", "canal", "numero", "conta", "coleta", "prazo", "concluido_em", "ordem")}
    e["status"] = status(envio)
    e["produtos"] = [{"sku": p["sku"], "titulo": p["titulo"], "unidades": p["unidades"],
                      "estoquista": p.get("estoquista", ""), "embalador": p.get("embalador", ""),
                      "etapas": {t: True for t in p["etapas"]}} for p in envio["produtos"]]
    e["resumo"] = resumo(envio)
    return e


def produtividade() -> dict:
    """Por pessoa: marcas feitas, unidades liberadas (T/E/OK) e tempo médio entre etapas. Só leitura; uso da gestão."""
    pessoas = {}
    for envio in _ler()["envios"].values():
        for p in envio["produtos"]:
            for tag, m in p["etapas"].items():
                x = pessoas.setdefault(m["por"], {"pessoa": m["por"], "marcas": 0, "unidades": 0, "por_etapa": {}, "tempos_min": []})
                x["marcas"] += 1
                x["unidades"] += p["unidades"]
                x["por_etapa"][tag] = x["por_etapa"].get(tag, 0) + 1
            e = p["etapas"]
            for a, b in (("T", "E"), ("E", "OK")):
                if a in e and b in e:
                    d = (datetime.fromisoformat(e[b]["em"]) - datetime.fromisoformat(e[a]["em"])).total_seconds() / 60
                    pessoas[e[b]["por"]]["tempos_min"].append(round(d, 1))
    for x in pessoas.values():
        t = x.pop("tempos_min")
        x["tempo_medio_entre_etapas_min"] = round(sum(t) / len(t), 1) if t else None
    return {"pessoas": sorted(pessoas.values(), key=lambda x: -x["unidades"])}
