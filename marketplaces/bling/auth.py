"""
marketplaces/bling/auth.py — Autoriza o MZ App numa conta Bling (fluxo OAuth2 da API v3)
MZ App

Uso:  .\\venv\\Scripts\\python.exe -m marketplaces.bling.auth bling01

Abre o navegador na página de autorização do Bling e escuta em
http://127.0.0.1:8765/bling/callback. Ao receber o code (válido por 1 minuto),
troca imediatamente pelos tokens e salva em bling_config.json.
"""

import secrets
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlencode, urlparse, parse_qs

from marketplaces.bling import api as bling_api

resultado = {}


def main(conta_id: str):
    conta = bling_api.load_config()["contas"].get(conta_id)
    if not conta:
        sys.exit(f"Conta {conta_id} não existe em bling_config.json")
    state = secrets.token_hex(16)
    url = bling_api.AUTHORIZE_URL + "?" + urlencode(
        {"response_type": "code", "client_id": conta["client_id"], "state": state})

    class Callback(BaseHTTPRequestHandler):
        def do_GET(self):
            q = parse_qs(urlparse(self.path).query)
            if urlparse(self.path).path != "/bling/callback":
                self.send_response(404); self.end_headers(); return
            if q.get("state", [""])[0] != state:
                resultado["erro"] = "state diferente do enviado — autorização ignorada"
            elif "code" not in q:
                resultado["erro"] = f"sem code na resposta: {dict(q)}"
            else:
                resultado.update(bling_api.trocar_code(conta_id, q["code"][0]))
            ok = resultado.get("success")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            msg = ("✅ Bling autorizado! Pode fechar esta aba e voltar ao terminal." if ok
                   else f"❌ Falhou: {resultado.get('erro') or resultado.get('error')}")
            self.wfile.write(f"<h2 style='font-family:sans-serif'>{msg}</h2>".encode("utf-8"))
            threading.Thread(target=self.server.shutdown, daemon=True).start()

        def log_message(self, *args):
            pass

    servidor = HTTPServer(("127.0.0.1", bling_api.REDIRECT_PORTA), Callback)
    print(f"Autorizando {conta_id} ({conta.get('nome', '')}).")
    print("Se o navegador não abrir, acesse:\n" + url)
    webbrowser.open(url)
    timer = threading.Timer(300, servidor.shutdown)  # desiste após 5 minutos
    timer.start()
    servidor.serve_forever()
    timer.cancel()
    if resultado.get("success"):
        print("✅ Tokens salvos em bling_config.json")
    else:
        print("❌ Não autorizado:", resultado.get("erro") or resultado.get("error") or "tempo esgotado (5 min)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "bling01")
