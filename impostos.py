"""
impostos.py — Alíquotas de imposto por conta (Techseller, MIZI, SELLER INFO), com a base compartilhada
MZ App

Regimes: unica (alíquota única), com_st e sem_st. A tabela fica em impostos.json e é sincronizada
pelo banco compartilhado (coleção impostos). Vale a data mais nova de cada conta/regime.

Planilha de importação (uma aba): Conta | Regime | Alíquota | Data
  Regime: unica, com_st ou sem_st. Alíquota: 11,50 ou 11.5 ou 11,50% (valor maior que 1 vira porcentagem).
"""
import io
import json
import os
import unicodedata
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(BASE_DIR, "impostos.json")
REGIMES = ("unica", "com_st", "sem_st")


def _norm(v) -> str:
    """Minúsculo e sem acento: 'Alíquota' vira 'aliquota'."""
    t = unicodedata.normalize("NFKD", str(v or "")).encode("ascii", "ignore").decode("ascii")
    return t.strip().lower()


def _agora() -> str:
    return datetime.now().isoformat(timespec="seconds")


def carregar() -> dict:
    try:
        with open(PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {"_atualizado_em": None, "itens": {}}


def salvar(itens: dict):
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"_atualizado_em": _agora(), "itens": itens}, f, ensure_ascii=False, indent=1)
    os.replace(tmp, PATH)


# Empresa dona de cada conta (informado pelo usuário em 27/10/2026). Imposto e CMV valem por empresa,
# em todos os canais: a conta herda a alíquota da empresa.
EMPRESAS = {
    "meli01": "Techseller", "amazon01": "Techseller", "shopee": "Techseller", "magalu": "Techseller",
    "meli02": "Seller Info", "amazon02": "Seller Info",
    "meli03": "Mizi",
}


def empresa_da_conta(conta: str):
    return EMPRESAS.get(conta)


def _itens_da_empresa(itens: dict, empresa: str) -> list:
    return [(k, v) for k, v in itens.items() if EMPRESAS.get(v.get("conta", k.split("|")[0])) == empresa]


def aliquotas(conta: str):
    """(COM ST, SEM ST) em fração, ou None. A alíquota é da empresa da conta; sem empresa cadastrada, da própria conta."""
    itens = carregar()["itens"]
    empresa = EMPRESAS.get(conta)
    base = _itens_da_empresa(itens, empresa) if empresa else [(k, v) for k, v in itens.items() if k.startswith(conta + "|")]
    uni = next((v for k, v in base if k.endswith("|unica")), None)
    if uni:
        return uni["aliquota"], uni["aliquota"]
    com = next((v for k, v in base if k.endswith("|com_st")), None)
    sem = next((v for k, v in base if k.endswith("|sem_st")), None)
    if com or sem:
        return (com or {}).get("aliquota", 0.0), (sem or {}).get("aliquota", 0.0)
    return None


def conferir_empresas() -> list:
    """Divergências: contas da mesma empresa com alíquotas diferentes (lista vazia = consistente)."""
    itens = carregar()["itens"]
    problemas = []
    for emp in sorted(set(EMPRESAS.values())):
        vistos = {}
        for conta, e in EMPRESAS.items():
            if e != emp:
                continue
            a = aliquotas(conta)
            if a is None:
                problemas.append(f"{conta} ({emp}): sem alíquota cadastrada")
            else:
                vistos.setdefault(a, []).append(conta)
        if len(vistos) > 1:
            problemas.append(f"{emp}: alíquotas diferentes entre contas {vistos}")
    return problemas


def por_conta(conta: str, cfg_impostos: dict = None, padrao: tuple = (0.10, 0.08)) -> tuple:
    """(COM ST, SEM ST): tabela de impostos; sem cadastro, config.json > impostos; por último o padrão."""
    cadastrada = aliquotas(conta)
    if cadastrada:
        return cadastrada
    imp = cfg_impostos or {}
    return (imp.get(f"{conta}_com_st", {}).get("aliquota", padrao[0]),
            imp.get(f"{conta}_sem_st", {}).get("aliquota", padrao[1]))


def _taxa(v) -> float:
    if isinstance(v, str):
        t = v.replace("%", "").replace(",", ".").strip()
        if not t:
            raise ValueError("alíquota vazia")
        v = float(t)
    v = float(v)
    return v / 100 if v > 1 else v


def _data_iso(v) -> str:
    from datetime import date
    if isinstance(v, datetime):
        return v.isoformat(timespec="seconds")
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day).isoformat(timespec="seconds")
    t = str(v or "").strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(t[:10], fmt).isoformat(timespec="seconds")
        except ValueError:
            continue
    return _agora()


def ler_planilha(conteudo: bytes) -> dict:
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(conteudo), data_only=True)
    itens, erros = {}, []
    for ws in wb.worksheets:
        cab = None
        for row in ws.iter_rows(min_row=1, max_row=10, values_only=True):
            t = [_norm(c) for c in row]
            if "conta" in t and "regime" in t and any(x.startswith("aliq") for x in t):
                cab = {"conta": t.index("conta"), "regime": t.index("regime"),
                       "aliq": next(i for i, x in enumerate(t) if x.startswith("aliq")),
                       "data": next((i for i, x in enumerate(t) if x.startswith("data")), None)}
                break
        if cab is None:
            continue
        for n, row in enumerate(ws.iter_rows(min_row=1, values_only=True), start=1):
            if not row or n <= 1:
                continue
            conta = str(row[cab["conta"]] or "").strip().lower()
            regime = str(row[cab["regime"]] or "").strip().lower()
            if not conta or not regime:
                continue
            try:
                if regime not in REGIMES:
                    raise ValueError(f"regime desconhecido: {regime}")
                aliq = _taxa(row[cab["aliq"]])
                data = _data_iso(row[cab["data"]]) if cab["data"] is not None else _agora()
                itens[f"{conta}|{regime}"] = {"conta": conta, "regime": regime, "aliquota": round(aliq, 6), "atualizado_em": data}
            except (ValueError, TypeError) as e:
                erros.append({"linha": n, "conta": conta, "motivo": str(e)})
    return {"itens": itens, "erros": erros}


def importar(conteudo: bytes, autor: str = "") -> dict:
    r = ler_planilha(conteudo)
    if not r["itens"]:
        raise ValueError("Nenhuma linha válida: use as colunas Conta, Regime, Alíquota e Data.")
    atual = carregar().get("itens", {})
    atual.update(r["itens"])
    salvar(atual)
    publicar(r["itens"], autor)
    return {"success": True, "linhas": len(r["itens"]), "erros": r["erros"]}


def publicar(itens: dict, autor: str = ""):
    """Sobe as alíquotas para o banco compartilhado. Não trava quem chamou."""
    try:
        import shared_store
        shared_store.enviar_lote_async("impostos", [(k, v) for k, v in itens.items()], autor)
    except Exception:
        pass


def aplicar_remoto(registros: list) -> int:
    """Aplica alíquotas vindas de outro computador. Vale a data mais nova de cada conta/regime."""
    base = carregar()
    itens = base.get("itens", {})
    mudou = 0
    for r in registros:
        chave = str(r.get("chave", ""))
        if r.get("removido"):
            if itens.pop(chave, None) is not None:
                mudou += 1
            continue
        try:
            d = json.loads(r["dados"]) if r.get("dados") else {}
        except ValueError:
            continue
        if "aliquota" not in d:
            continue
        atual = itens.get(chave)
        if atual is None or (d.get("atualizado_em") or "") >= (atual.get("atualizado_em") or ""):
            itens[chave] = d
            mudou += 1
    if mudou:
        salvar(itens)
    return mudou
