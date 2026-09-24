"""
verificar_dados.py — Valida os arquivos de dados do MZCell Rentabilidade.

Use ANTES de copiar um backup (pendrive, HD externo, maquina antiga) e
ANTES de subir o servidor. Nao imprime senhas nem tokens.

    python verificar_dados.py                    # valida a pasta do projeto
    python verificar_dados.py --dir D:\\backup    # valida o backup sem copiar
    python verificar_dados.py --fix-bom          # remove BOM (so na pasta do projeto)

Com --dir apontando para fora do projeto o script nunca escreve nada:
analisa o backup em modo somente leitura e, se a pasta do projeto ja tiver
dados, compara os dois lados para voce ver o que a copia sobrescreveria.
"""

import json
import os
import sys
from datetime import datetime

PROJETO_DIR = os.path.dirname(os.path.abspath(__file__))

ARQUIVOS_DADOS = ("config.json", "usuarios.json", "produtos.json",
                  "email_config.json", "anotacoes.json", "sync_status.json",
                  "log_atividades.json", "alertas.json", "contas.json",
                  "historico_rentabilidade.json")


def _arg(flag):
    """Le o valor de uma flag no formato --flag VALOR ou --flag=VALOR."""
    for i, a in enumerate(sys.argv):
        if a == flag and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
        if a.startswith(flag + "="):
            return a.split("=", 1)[1]
    return None


if "--help" in sys.argv or "-h" in sys.argv:
    print(__doc__.strip())
    sys.exit(0)

BASE_DIR = os.path.abspath(_arg("--dir") or PROJETO_DIR)
EXTERNO  = os.path.normcase(BASE_DIR) != os.path.normcase(PROJETO_DIR)
FIX_BOM  = "--fix-bom" in sys.argv

if EXTERNO and FIX_BOM:
    print("[ERRO] --fix-bom nao roda junto com --dir: o script nao altera um "
          "backup.\n       Copie os arquivos primeiro e rode --fix-bom na "
          "pasta do projeto.")
    sys.exit(2)

if not os.path.isdir(BASE_DIR):
    print(f"[ERRO] pasta nao encontrada: {BASE_DIR}")
    sys.exit(2)

OK, WARN, ERR = "[ OK ]", "[AVISO]", "[ERRO]"
_problemas = {"erro": 0, "aviso": 0}


def _reg(nivel):
    if nivel == ERR:
        _problemas["erro"] += 1
    elif nivel == WARN:
        _problemas["aviso"] += 1


def diz(nivel, msg):
    _reg(nivel)
    print(f"{nivel} {msg}")


def carregar(nome, obrigatorio=True):
    """Le um JSON checando BOM e sintaxe. Devolve (dados, ok)."""
    caminho = os.path.join(BASE_DIR, nome)

    if not os.path.exists(caminho):
        diz(ERR if obrigatorio else WARN,
            f"{nome}: nao encontrado" + ("" if obrigatorio else " (opcional)"))
        return None, False

    tamanho = os.path.getsize(caminho)
    if tamanho == 0:
        diz(ERR if obrigatorio else WARN, f"{nome}: arquivo vazio (0 bytes)")
        return None, False

    with open(caminho, "rb") as f:
        bruto = f.read()

    tem_bom = bruto.startswith(b"\xef\xbb\xbf")
    if tem_bom:
        if FIX_BOM:
            with open(caminho, "wb") as f:
                f.write(bruto[3:])
            bruto = bruto[3:]
            diz(WARN, f"{nome}: BOM removido — load_json() usa utf-8 puro e quebraria")
        else:
            diz(ERR, f"{nome}: tem BOM UTF-8 — load_json() vai falhar. "
                     f"Rode com --fix-bom")
            return None, False

    try:
        dados = json.loads(bruto.decode("utf-8"))
    except UnicodeDecodeError as e:
        diz(ERR, f"{nome}: nao e UTF-8 valido ({e.reason})")
        return None, False
    except json.JSONDecodeError as e:
        diz(ERR, f"{nome}: JSON invalido na linha {e.lineno}, coluna {e.colno} — {e.msg}")
        return None, False

    tam_fmt = f"{tamanho:,}".replace(",", ".")
    print(f"{OK} {nome}: {tam_fmt} bytes, JSON valido")
    return dados, True


def checar_config():
    print("\n--- config.json (credenciais ML) ---")
    dados, ok = carregar("config.json")
    if not ok:
        return

    contas = dados.get("contas")
    if not isinstance(contas, dict) or not contas:
        diz(ERR, "config.json: chave 'contas' ausente ou vazia — "
                 "/config/conta devolve 404 sem ela")
        return

    for esperada in ("meli01", "meli02", "meli03"):
        if esperada not in contas:
            diz(WARN, f"config.json: conta '{esperada}' ausente")

    agora = datetime.now()
    for nome, c in contas.items():
        faltando = [campo for campo in
                    ("client_id", "client_secret", "access_token", "refresh_token", "seller_id")
                    if not c.get(campo)]
        if faltando:
            diz(WARN, f"  {nome}: sem {', '.join(faltando)} — precisa reautorizar")
        else:
            print(f"{OK}   {nome}: seller_id {c['seller_id']}, credenciais completas")

        expira = c.get("token_expires_at", "")
        if expira:
            try:
                dt = datetime.fromisoformat(expira)
                if dt < agora:
                    diz(WARN, f"  {nome}: access_token vencido em {dt:%d/%m/%Y %H:%M} "
                              f"— o refresh no startup deve renovar")
                else:
                    print(f"{OK}   {nome}: token valido ate {dt:%d/%m/%Y %H:%M}")
            except ValueError:
                diz(WARN, f"  {nome}: token_expires_at ilegivel ({expira!r})")


def checar_usuarios():
    print("\n--- usuarios.json (login) ---")
    dados, ok = carregar("usuarios.json")
    if not ok:
        return

    if not isinstance(dados, list) or not dados:
        diz(ERR, "usuarios.json: precisa ser uma lista nao vazia")
        return

    roles_validas = {"master", "adminplus", "viewer"}
    for i, u in enumerate(dados):
        rotulo = u.get("login") or f"indice {i}"
        for campo in ("login", "nome", "senha", "role"):
            if not u.get(campo):
                diz(ERR, f"  {rotulo}: campo '{campo}' ausente — /auth/login quebra")

        login = u.get("login", "")
        if login != login.lower():
            diz(ERR, f"  {rotulo}: login tem maiuscula — verificar_login() compara "
                     f"com .lower() e nunca vai casar")

        senha = u.get("senha", "")
        if senha and (len(senha) != 64 or not all(ch in "0123456789abcdef" for ch in senha.lower())):
            diz(ERR, f"  {rotulo}: 'senha' nao parece SHA-256 (esperado 64 hex, "
                     f"veio {len(senha)} chars) — senha em texto puro nunca autentica")

        role = u.get("role")
        if role and role not in roles_validas:
            diz(WARN, f"  {rotulo}: role '{role}' desconhecida "
                      f"(validas: {', '.join(sorted(roles_validas))})")

    print(f"{OK}   {len(dados)} usuario(s): "
          f"{', '.join(u.get('login', '?') for u in dados)}")


def checar_produtos():
    print("\n--- produtos.json (custos e historico) ---")
    dados, ok = carregar("produtos.json")
    if not ok:
        diz(ERR, "produtos.json e o unico arquivo que o sync do ML NAO reconstroi "
                 "— custos e anotacoes foram digitados a mao")
        return

    if not isinstance(dados, dict):
        diz(ERR, "produtos.json: esperado objeto indexado por MLB")
        return

    total      = len(dados)
    com_custo  = sum(1 for p in dados.values() if p.get("cmv_unit", 0) > 0)
    com_sim    = sum(1 for p in dados.values() if p.get("simulador"))
    arquivados = sum(1 for p in dados.values() if p.get("arquivado"))
    catalog    = sum(1 for p in dados.values() if p.get("catalog_listing"))

    por_conta = {}
    for p in dados.values():
        c = p.get("conta", "(sem conta)")
        por_conta[c] = por_conta.get(c, 0) + 1

    print(f"{OK}   {total} produtos: " +
          ", ".join(f"{c} {n}" for c, n in sorted(por_conta.items())))
    print(f"{OK}   {com_custo} com CMV preenchido, {com_sim} com simulador salvo, "
          f"{arquivados} arquivados")

    if total and com_custo == 0:
        diz(ERR, "nenhum produto tem cmv_unit > 0 — este backup parece anterior "
                 "a importacao de custos")
    elif com_custo < total * 0.5:
        diz(WARN, f"so {com_custo}/{total} produtos tem custo — confira se e o "
                  f"backup mais recente")

    print(f"{OK}   {catalog} com catalog_listing=True — rode 'Sync Catalog' "
          f"apos restaurar (catalog_listing desatualiza em backup)")


def checar_email():
    print("\n--- email_config.json (SMTP) ---")
    dados, ok = carregar("email_config.json", obrigatorio=False)
    if not ok:
        diz(WARN, "sem email_config.json o primeiro acesso e a recuperacao "
                  "de senha nao funcionam (o resto do app roda normal)")
        return

    for campo in ("email_remetente", "smtp_host", "smtp_port", "senha_app"):
        if not dados.get(campo):
            diz(ERR, f"  email_config.json: campo '{campo}' ausente")
    if dados.get("email_remetente"):
        print(f"{OK}   remetente: {dados['email_remetente']}")


def checar_opcionais():
    print("\n--- arquivos opcionais (regeneram sozinhos) ---")
    for nome in ARQUIVOS_DADOS[4:]:
        caminho = os.path.join(BASE_DIR, nome)
        if os.path.exists(caminho):
            carregar(nome, obrigatorio=False)
        else:
            print(f"{OK} {nome}: ausente — sera criado no uso")


def checar_codigo_no_backup():
    """Avisa se o backup tem .py/.html/.js — copiar por cima reverte o codigo."""
    if not EXTERNO:
        return
    print("\n--- codigo dentro do backup ---")

    achados = []
    for nome in sorted(os.listdir(BASE_DIR)):
        if nome.lower().endswith((".py", ".html", ".js")) and nome != "verificar_dados.py":
            achados.append(nome)

    if not achados:
        print(f"{OK} backup so tem dados, nenhum arquivo de codigo — copia segura")
        return

    mostrar = ", ".join(achados[:6]) + (" ..." if len(achados) > 6 else "")
    diz(WARN, f"o backup contem {len(achados)} arquivo(s) de codigo: {mostrar}")
    diz(WARN, "NAO copie esses por cima do clone do git — sao versoes antigas. "
              "O codigo vem do repositorio, so os .json vem do backup.")


def _resumo_produtos(caminho):
    """(total, com_custo) de um produtos.json, ou None se ilegivel."""
    try:
        with open(caminho, "rb") as f:
            bruto = f.read()
        if bruto.startswith(b"\xef\xbb\xbf"):
            bruto = bruto[3:]
        dados = json.loads(bruto.decode("utf-8"))
        if not isinstance(dados, dict):
            return None
        return len(dados), sum(1 for v in dados.values()
                               if isinstance(v, dict) and v.get("cmv_unit", 0) > 0)
    except Exception:
        return None


def comparar_com_destino():
    """Mostra o que a copia sobrescreveria na pasta do projeto."""
    if not EXTERNO:
        return

    pares = []
    for nome in ARQUIVOS_DADOS:
        org = os.path.join(BASE_DIR, nome)
        dst = os.path.join(PROJETO_DIR, nome)
        if os.path.exists(org) and os.path.exists(dst):
            pares.append((nome, org, dst))

    if not pares:
        print("\n--- comparacao com o destino ---")
        print(f"{OK} a pasta do projeto nao tem dados — a copia nao sobrescreve nada")
        return

    print("\n--- comparacao com o destino (o que a copia sobrescreveria) ---")
    diz(WARN, f"{len(pares)} arquivo(s) ja existem em {PROJETO_DIR}")

    for nome, org, dst in pares:
        t_org, t_dst = os.path.getsize(org), os.path.getsize(dst)
        m_org = datetime.fromtimestamp(os.path.getmtime(org))
        m_dst = datetime.fromtimestamp(os.path.getmtime(dst))
        print(f"\n  {nome}")
        print(f"    backup : {t_org:>10,} bytes  {m_org:%d/%m/%Y %H:%M}".replace(",", "."))
        print(f"    destino: {t_dst:>10,} bytes  {m_dst:%d/%m/%Y %H:%M}".replace(",", "."))

        if m_dst > m_org:
            diz(WARN, f"    o destino e MAIS NOVO — a copia joga fora a versao recente")

        if nome == "produtos.json":
            r_org, r_dst = _resumo_produtos(org), _resumo_produtos(dst)
            if r_org and r_dst:
                print(f"    backup : {r_org[0]} produtos, {r_org[1]} com custo")
                print(f"    destino: {r_dst[0]} produtos, {r_dst[1]} com custo")
                if r_dst[1] > r_org[1]:
                    diz(ERR, f"    o destino tem MAIS custos preenchidos "
                             f"({r_dst[1]} vs {r_org[1]}) — copiar perde trabalho manual")


def main():
    print("=" * 66)
    print("  MZCell Rentabilidade — verificacao dos arquivos de dados")
    print(f"  Origem: {BASE_DIR}" + ("  (backup externo, somente leitura)" if EXTERNO else ""))
    if EXTERNO:
        print(f"  Projeto: {PROJETO_DIR}")
    if FIX_BOM:
        print("  Modo: --fix-bom (BOM sera removido dos arquivos afetados)")
    print("=" * 66)

    checar_config()
    checar_usuarios()
    checar_produtos()
    checar_email()
    checar_opcionais()
    checar_codigo_no_backup()
    comparar_com_destino()

    print("\n" + "=" * 66)
    erros, avisos = _problemas["erro"], _problemas["aviso"]
    if erros:
        print(f"  {erros} erro(s) e {avisos} aviso(s) — CORRIJA os erros antes de subir")
    elif avisos:
        print(f"  Nenhum erro, {avisos} aviso(s) — pode subir, mas leia os avisos")
    else:
        print("  Tudo certo — " + ("pode copiar os .json para o projeto"
                                        if EXTERNO else "pode subir o servidor"))
    print("=" * 66)
    return 1 if erros else 0


if __name__ == "__main__":
    sys.exit(main())
