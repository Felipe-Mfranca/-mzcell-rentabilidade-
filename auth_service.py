import json, hashlib, secrets, smtplib, os
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_codigos = {}

USUARIOS_BASE = os.path.join(BASE_DIR, "usuarios.json")
# Instalação em Program Files não deixa o usuário comum gravar: as trocas de senha vão para esta cópia, que tem prioridade.
USUARIOS_LOCAL = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "MZ-app", "usuarios.json")

def load_usuarios():
    caminho = USUARIOS_LOCAL if os.path.exists(USUARIOS_LOCAL) else USUARIOS_BASE
    with open(caminho,encoding="utf-8") as f:
        return json.load(f)

def save_usuarios(u):
    try:
        with open(USUARIOS_BASE,"w",encoding="utf-8") as f:
            json.dump(u,f,indent=2,ensure_ascii=False)
        if os.path.exists(USUARIOS_LOCAL):
            os.remove(USUARIOS_LOCAL)      # a base voltou a ser gravável: a cópia local não pode ficar mais velha
    except OSError:
        os.makedirs(os.path.dirname(USUARIOS_LOCAL), exist_ok=True)
        with open(USUARIOS_LOCAL,"w",encoding="utf-8") as f:
            json.dump(u,f,indent=2,ensure_ascii=False)

def load_email_config():
    with open(os.path.join(BASE_DIR,"email_config.json"),encoding="utf-8") as f:
        return json.load(f)

def h(s): return hashlib.sha256(s.encode()).hexdigest()

def verificar_login(login, senha):
    for u in load_usuarios():
        if u["login"]==login.lower() and u["senha"]==h(senha):
            return u
    return None

def gerar_codigo():
    return str(secrets.randbelow(900000)+100000)

def enviar_codigo(email, nome):
    cfg    = load_email_config()
    codigo = gerar_codigo()
    _codigos[email] = {"codigo":codigo,"expiry":(datetime.now()+timedelta(minutes=10)).isoformat()}
    msg = MIMEMultipart()
    msg["From"]    = cfg["email_remetente"]
    msg["To"]      = email
    msg["Subject"] = "MZ App - Codigo de verificacao"
    body = f"""<html><body style="font-family:Arial,sans-serif;background:#f5f5f5;padding:20px;">
    <div style="max-width:400px;margin:0 auto;background:#fff;border-radius:12px;padding:32px;">
      <h2 style="color:#f6c90e;">MZ App</h2>
      <p>Ola, <strong>{nome}</strong>!</p>
      <p>Seu codigo de verificacao:</p>
      <div style="background:#0a0a0f;color:#f6c90e;font-size:32px;font-weight:700;letter-spacing:8px;text-align:center;padding:20px;border-radius:8px;margin:16px 0;">{codigo}</div>
      <p style="color:#999;font-size:12px;">Expira em 10 minutos.</p>
    </div></body></html>"""
    msg.attach(MIMEText(body,"html"))
    with smtplib.SMTP(cfg["smtp_host"],cfg["smtp_port"]) as s:
        s.starttls()
        s.login(cfg["email_remetente"],cfg["senha_app"])
        s.send_message(msg)
    return True

def verificar_codigo(email, codigo):
    if email not in _codigos: return False
    e = _codigos[email]
    if datetime.now()>datetime.fromisoformat(e["expiry"]):
        del _codigos[email]; return False
    if e["codigo"]!=codigo: return False
    del _codigos[email]; return True

CAMPOS_COMPARTILHADOS = ("login", "nome", "role", "senha", "email", "email_verificado", "primeiro_acesso", "contas", "modulos", "senha_atualizada_em")
SEMENTE_TS = "2000-01-01T00:00:00.000Z"      # usuário nunca alterado: qualquer troca de senha real vale mais


def _agora():
    from datetime import timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def publicar_usuario(u):
    """Sobe o registro do usuário (com o hash da senha) para o banco compartilhado; vale o mais recente."""
    try:
        import shared_store
        dados = {k: u.get(k) for k in CAMPOS_COMPARTILHADOS if k in u}
        shared_store.enviar_async("usuarios", u["login"], dados, u["login"], ts=u.get("senha_atualizada_em") or SEMENTE_TS)
    except Exception:
        pass


def publicar_todos():
    """Semeia a planilha com os usuários locais (carimbo antigo: nunca sobrescreve uma troca de senha mais nova)."""
    usuarios = load_usuarios()
    mudou = False
    for u in usuarios:
        if not u.get("senha_atualizada_em") and not u.get("primeiro_acesso", False):
            # senha já trocada antes desta versão: vale a data do arquivo neste computador
            try:
                ts = os.path.getmtime(USUARIOS_LOCAL if os.path.exists(USUARIOS_LOCAL) else USUARIOS_BASE)
                from datetime import timezone
                u["senha_atualizada_em"] = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
                mudou = True
            except OSError:
                pass
    if mudou:
        save_usuarios(usuarios)
    for u in usuarios:
        publicar_usuario(u)


def aplicar_usuarios(registros):
    """Aplica no usuarios.json local o que veio da planilha. Só entra o registro mais novo que o local."""
    usuarios = load_usuarios()
    mudou = False
    for r in registros:
        try:
            d = json.loads(r["dados"])
            login = d["login"].lower()
        except (ValueError, KeyError, AttributeError):
            continue
        if r.get("removido"):
            continue
        ts_remoto = r.get("atualizado_em") or SEMENTE_TS
        atual = next((u for u in usuarios if u["login"] == login), None)
        if atual is None:
            usuarios.append({k: v for k, v in d.items() if k in CAMPOS_COMPARTILHADOS}); mudou = True
        elif ts_remoto > (atual.get("senha_atualizada_em") or SEMENTE_TS):
            for k in CAMPOS_COMPARTILHADOS:
                if k in d:
                    atual[k] = d[k]
            atual["senha_atualizada_em"] = d.get("senha_atualizada_em") or ts_remoto
            mudou = True
    if mudou:
        save_usuarios(usuarios)


def atualizar_email_senha(login, email, nova_senha):
    usuarios = load_usuarios()
    alvo = None
    for u in usuarios:
        if u["login"]==login:
            u["email"]=email; u["senha"]=h(nova_senha)
            u["email_verificado"]=True; u["primeiro_acesso"]=False
            u["senha_atualizada_em"]=_agora()
            alvo = u
            break
    save_usuarios(usuarios)
    if alvo:
        publicar_usuario(alvo)

def tem_permissao(role, acao):
    p = {"master":["tudo"],"adminplus":["ver","sync","custos","anotacoes","simulador"],"viewer":["ver"],"operador":["ver","anotacoes"]}
    perms = p.get(role,[])
    return "tudo" in perms or acao in perms