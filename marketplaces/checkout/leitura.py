"""
marketplaces/checkout/leitura.py — Interpreta o que o leitor de QR devolve (sem rede)
MZ App

O QR da etiqueta do Mercado Livre carrega um JSON: {"id":"<ID do envio>","sender_id":<seller>,"hash_code":...}.
O leitor do galpão está com o teclado em outro layout e troca " por ^ e : por Ç, então o texto chega assim:
  ^id^Ç^48424630205^,^sender_id^Ç1874506744,^hash_code^Çnull,^security_digit^Çnull{
Também aceita só o número (envio ou pedido) e o rastreio "MEL<envio>FM...".
"""

import re

# Troca de caracteres do layout do leitor: ^ no lugar de ", Ç no lugar de :
_TROCAS = str.maketrans({"^": '"', "Ç": ":", "ç": ":", "´": '"', "¨": '"'})
_ID = re.compile(r'(?<![A-Za-z_])"?id"?\s*:\s*"?(\d{6,})', re.I)
_SENDER = re.compile(r'sender_id"?\s*:\s*"?(\d{4,})', re.I)
_MEL = re.compile(r"\bMEL(\d{8,14})[A-Z]{1,8}\d{0,3}\b", re.I)
_SO_NUMERO = re.compile(r"^\d{8,20}$")


def ler(texto) -> dict | None:
    """Devolve {tipo: 'envio'|'pedido', numero, sender_id, origem} ou None se não der para entender."""
    if not texto:
        return None
    t = str(texto).strip().translate(_TROCAS)
    if not t:
        return None
    m = _ID.search(t)
    if m:
        s = _SENDER.search(t)
        return {"tipo": "envio", "numero": m.group(1), "sender_id": s.group(1) if s else None, "origem": "qr"}
    m = _MEL.search(t)
    if m:
        return {"tipo": "envio", "numero": m.group(1), "sender_id": None, "origem": "rastreio"}
    t = t.strip('"{} ')
    if _SO_NUMERO.match(t):
        # Pedido do ML começa com 2000 e tem 16 dígitos; o ID do envio tem 11.
        if t.startswith("2000") and len(t) >= 15:
            return {"tipo": "pedido", "numero": t, "sender_id": None, "origem": "numero"}
        return {"tipo": "envio", "numero": t, "sender_id": None, "origem": "numero"}
    return None
