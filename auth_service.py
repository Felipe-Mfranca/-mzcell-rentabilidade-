import json, hashlib, secrets, smtplib, os
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_codigos = {}

def load_usuarios():
    with open(os.path.join(BASE_DIR,"usuarios.json"),encoding="utf-8") as f:
        return json.load(f)

def save_usuarios(u):
    with open(os.path.join(BASE_DIR,"usuarios.json"),"w",encoding="utf-8") as f:
        json.dump(u,f,indent=2,ensure_ascii=False)

def load_email_config():
    with open(os.path.join(BASE_DIR,"email_config.json"),encoding="utf-8") as f:
        return json.load(f)

def h(s): return hashlib.sha256(s.encode()).hexdigest()

def verificar_login(login, senha):
    for u in load_usuarios():
        if u["login"]==login and u["senha"]==h(senha):
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
    msg["Subject"] = "MZCell - Codigo de verificacao"
    body = f"""<html><body style="font-family:Arial,sans-serif;background:#f5f5f5;padding:20px;">
    <div style="max-width:400px;margin:0 auto;background:#fff;border-radius:12px;padding:32px;">
      <h2 style="color:#f6c90e;">MZCell Rentabilidade</h2>
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

def atualizar_email_senha(login, email, nova_senha):
    usuarios = load_usuarios()
    for u in usuarios:
        if u["login"]==login:
            u["email"]=email; u["senha"]=h(nova_senha)
            u["email_verificado"]=True; u["primeiro_acesso"]=False
            break
    save_usuarios(usuarios)

def tem_permissao(role, acao):
    p = {"master":["tudo"],"adminplus":["ver","sync","custos","anotacoes","simulador"],"viewer":["ver"]}
    perms = p.get(role,[])
    return "tudo" in perms or acao in perms