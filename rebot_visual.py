"""
rebot_visual.py — formatação do aviso de rebot (e-mail HTML + texto para Slack/fallback).
Só formata: não fala com o Mercado Livre.

Recomendação por oportunidade (só lado ML, sem CMV/imposto):
  RECOMENDADO     preço não sobe e o ganho compensa
  AVALIAR         ganho compensa, mas o preço sobe (buybox) ou a campanha exige estoque
  BAIXA PRIORIDADE ganho irrelevante ou empate
  Sem vendas em 30 dias NÃO rebaixa: o rebot pode alavancar o anúncio; vale pelo ganho por unidade.
"""

from html import escape

GANHO_MIN_UN = 0.10    # abaixo disso (e impacto < IMPACTO_MIN_MES) o aviso é ruído
IMPACTO_MIN_MES = 5.0

NIVEIS = {
    "OK":    {"emoji": "🟢", "rotulo": "RECOMENDADO",      "cor": "#166534", "fundo": "#dcfce7"},
    "ATENC": {"emoji": "🟡", "rotulo": "AVALIAR",          "cor": "#854d0e", "fundo": "#fef9c3"},
    "BAIXA": {"emoji": "⚪", "rotulo": "BAIXA PRIORIDADE", "cor": "#4b5563", "fundo": "#f3f4f6"},
}


def brl(v: float) -> str:
    s = f"{abs(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{'-' if v < 0 else ''}R$ {s}"


def ganho_fmt(v: float) -> str:
    return f"{'+' if v >= 0 else '-'}{abs(v):.2f}".replace(".", ",")


def _data(iso: str) -> str:
    return "/".join(iso.split("-")[::-1][:2]) if iso else ""


def classificar(o: dict) -> tuple:
    """Devolve (nivel, [motivos])."""
    motivos = []
    if o["delta_preco"] > 0 and o["atual_preco"]:
        motivos.append(f"preço sobe {brl(o['delta_preco'])} ({o['delta_preco'] / o['atual_preco'] * 100:.1f}%) — checar buybox")
    if o["tipo"] == "LIGHTNING" and o.get("estoque"):
        motivos.append(f"exige estoque de {o['estoque'].get('min')} a {o['estoque'].get('max')} un")
    if o["empate"]:
        return "BAIXA", ["mesmo preço e líquido de hoje — a regra da casa prefere a campanha do ML à nossa"]
    if o["un_30d"] == 0:   # sem histórico o impacto é 0, mas o rebot pode ser justamente o que destrava a venda
        if o["ganho_un"] < GANHO_MIN_UN:
            return "BAIXA", [f"ganho pequeno ({brl(o['ganho_un'])}/un)"] + motivos
        motivos.insert(0, "sem vendas nos últimos 30 dias — o rebot pode ajudar a alavancar o anúncio")
        return ("ATENC" if len(motivos) > 1 else "OK"), motivos
    if o["ganho_un"] < GANHO_MIN_UN and o["impacto_mes"] < IMPACTO_MIN_MES:
        return "BAIXA", [f"ganho pequeno ({brl(o['ganho_un'])}/un ≈ {brl(o['impacto_mes'])}/mês)"] + motivos
    return ("ATENC" if motivos else "OK"), motivos


def _origem(lista: list) -> str:
    """' (2 nova(s), 5 já avisada(s))' — só aparece quando há as duas coisas."""
    rep = sum(1 for o in lista if o.get("repetida"))
    if not rep or rep == len(lista):
        return f" ({rep} já avisada(s), seguem abertas)" if rep else ""
    return f" ({len(lista) - rep} nova(s), {rep} já avisada(s))"


def _ordenadas(novas: list) -> list:
    return sorted(novas, key=lambda o: (-o["impacto_mes"], -o["ganho_un"]))


def recomendacao(novas: list) -> list:
    """Linhas de recomendação final: o que priorizar, em ordem de impacto."""
    por = {"OK": [], "ATENC": [], "BAIXA": []}
    for o in _ordenadas(novas):
        por[classificar(o)[0]].append(o)
    linhas = []
    if por["OK"]:
        top = por["OK"][:3]
        linhas.append(("OK", "ENTRE PRIMEIRO EM", [
            f"{o['mlb']} · {o['nome']} · {brl(o['ganho_un'])}/un"
            + (f" (≈ {brl(o['impacto_mes'])}/mês)" if o["un_30d"] else "") for o in top]))
    if por["ATENC"]:
        top = por["ATENC"][:3]
        linhas.append(("ATENC", "ANALISE ANTES DE ENTRAR", [
            f"{o['mlb']} · {o['nome']} · {brl(o['ganho_un'])}/un"
            + (f" (≈ {brl(o['impacto_mes'])}/mês)" if o["un_30d"] else "")
            + " — " + "; ".join(classificar(o)[1]) for o in top]))
    if por["BAIXA"]:
        linhas.append(("BAIXA", "PODE IGNORAR", [f"{len(por['BAIXA'])} aviso(s) de baixo impacto (ganho mínimo ou empate)"]))
    if not linhas:
        linhas.append(("BAIXA", "NADA A FAZER", ["nenhuma oportunidade no momento"]))
    return linhas


# ─── TEXTO (Slack / fallback do e-mail) ──────────────────────────────────────

def texto(novas: list, erros: dict, abertas: int, quando: str, contas: list, baixa: list = None) -> str:
    total = sum(o["impacto_mes"] for o in novas if not o["empate"] and o["un_30d"])
    p = [f"🔔 *REBOT · {quando}*", f"*{len(novas)} OPORTUNIDADE(S)*{_origem(novas)} · potencial ≈ *{brl(total)}/mês*", ""]
    for conta in contas:
        do = _ordenadas([o for o in novas if o["conta"] == conta])
        if not do:
            continue
        p += [f"━━━━━━━━━━━━━━━━", f"📦 *{conta.upper()}* · {len(do)} oportunidade(s)", ""]
        for o in do:
            n, mot = classificar(o)
            nv = NIVEIS[n]
            p.append(f"{nv['emoji']} *{nv['rotulo']}* · `{o['mlb']}`" + (" · 🔁 já avisada, segue aberta" if o.get("repetida") else " · 🆕 nova"))
            p.append(f"   {o['titulo'][:48]}")
            p.append(f"   🏷️ *{o['nome']}* ({o['tipo']})")
            p.append(f"   💲 {brl(o['atual_preco'])} → *{brl(o['preco'])}* · rebot {brl(o['atual_rebot'])} → *{brl(o['rebot'])}*")
            if o["empate"]:
                p.append("   ⚖️ *EMPATE* de preço e líquido")
            else:
                imp = f" ≈ *{brl(o['impacto_mes'])}/mês*" if o["un_30d"] else ""
                p.append(f"   📈 *{ganho_fmt(o['ganho_un'])}/un*{imp} · {o['un_30d']} un/30d")
            for m in mot:
                p.append(f"   ⚠️ {m}")
            if o["fim"]:
                p.append(f"   ⏳ até {_data(o['fim'])}")
            p.append("")
    for conta, e in erros.items():
        p += [f"❌ *{conta.upper()}* · erro na verificação ({e}) — conta NÃO analisada nesta rodada.", ""]
    if baixa:
        p += ["━━━━━━━━━━━━━━━━", f"⬇️ *BAIXA PRIORIDADE* · {len(baixa)}", ""]
        for b in baixa:
            p.append(f"   📦 *{b['conta'].upper()}* · `{b['mlb']}` · {b['titulo'][:48]}")
            p.append(f"   🏷️ {b['nome']} · {brl(b['atual_preco'])} → {brl(b['preco'])} · {ganho_fmt(b['ganho_un'])}/un")
            p.append(f"   ⚠️ {b['motivo_baixa']}")
            p.append("")
    p += ["━━━━━━━━━━━━━━━━", "💡 *RECOMENDAÇÃO*", ""]
    for n, titulo, itens in recomendacao(novas):
        p.append(f"{NIVEIS[n]['emoji']} *{titulo}*")
        p += [f"   • {i}" for i in itens]
        p.append("")
    p.append("📌 _Sugestões seguem em todo relatório até a campanha entrar em vigor ou o anúncio ser inscrito._")
    p.append("🔒 _Somente recomendação: nada foi alterado no Mercado Livre._")
    return "\n".join(p)


# ─── HTML (e-mail) ───────────────────────────────────────────────────────────

def _badge(texto_: str, cor: str, fundo: str) -> str:
    return (f'<span style="display:inline-block;padding:3px 10px;border-radius:12px;background:{fundo};'
            f'color:{cor};font-size:11px;font-weight:700;letter-spacing:.04em;">{escape(texto_)}</span>')


def _card(o: dict) -> str:
    n, mot = classificar(o)
    nv = NIVEIS[n]
    if o["empate"]:
        ganho = '<span style="font-size:18px;font-weight:800;color:#4b5563;">⚖️ EMPATE</span>'
    else:
        ganho = f'<span style="font-size:22px;font-weight:800;color:{nv["cor"] if n != "BAIXA" else "#4b5563"};">{ganho_fmt(o["ganho_un"])}</span><span style="font-size:12px;color:#6b7280;"> /un</span>'
    impacto = (f'<div style="font-size:12px;color:#374151;margin-top:2px;">≈ <b>{brl(o["impacto_mes"])}/mês</b> · {o["un_30d"]} un/30d</div>'
               if o["un_30d"] and not o["empate"] else
               f'<div style="font-size:12px;color:#6b7280;margin-top:2px;">{o["un_30d"]} un/30d</div>')
    avisos = "".join(f'<div style="font-size:12px;color:#854d0e;margin-top:6px;">⚠️ {escape(m)}</div>' for m in mot)
    fim = f'<span style="font-size:12px;color:#6b7280;"> · ⏳ até {_data(o["fim"])}</span>' if o["fim"] else ""
    return f'''
<tr><td style="padding:0 0 12px 0;">
 <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#ffffff;border:1px solid #e5e7eb;border-left:5px solid {nv["cor"]};border-radius:8px;">
  <tr><td style="padding:14px 16px;">
   <div style="margin-bottom:8px;">{_badge(nv["emoji"] + " " + nv["rotulo"], nv["cor"], nv["fundo"])}
     <span style="font-size:12px;color:#6b7280;font-family:Consolas,monospace;"> {o["mlb"]}</span>
     {_badge("🔁 JÁ AVISADA", "#374151", "#e5e7eb") if o.get("repetida") else _badge("🆕 NOVA", "#1d4ed8", "#dbeafe")}</div>
   <div style="font-size:15px;font-weight:700;color:#111827;line-height:1.3;">{escape(o["titulo"][:60])}</div>
   <div style="font-size:13px;color:#166534;font-weight:700;margin-top:6px;">🏷️ {escape(o["nome"])} <span style="font-weight:400;color:#6b7280;">({escape(o["tipo"] or "")})</span>{fim}</div>
   <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:10px;"><tr>
    <td valign="top" style="font-size:13px;color:#374151;line-height:1.7;">
      💲 Preço: {brl(o["atual_preco"])} → <b>{brl(o["preco"])}</b><br>
      🎁 Rebot: {brl(o["atual_rebot"])} → <b>{brl(o["rebot"])}</b>
    </td>
    <td valign="top" align="right">{ganho}{impacto}</td>
   </tr></table>
   {avisos}
  </td></tr>
 </table>
</td></tr>'''


def html(novas: list, erros: dict, abertas: int, quando: str, contas: list, baixa: list = None) -> str:
    total = sum(o["impacto_mes"] for o in novas if not o["empate"] and o["un_30d"])
    contagem = {k: 0 for k in NIVEIS}
    for o in novas:
        contagem[classificar(o)[0]] += 1

    def kpi(valor, rotulo, cor):
        return (f'<td align="center" style="padding:12px 6px;"><div style="font-size:24px;font-weight:800;color:{cor};">{valor}</div>'
                f'<div style="font-size:11px;color:#6b7280;letter-spacing:.06em;text-transform:uppercase;">{rotulo}</div></td>')

    corpo = []
    for conta in contas:
        do = _ordenadas([o for o in novas if o["conta"] == conta])
        if not do:
            continue
        corpo.append(f'<tr><td style="padding:18px 0 10px 0;"><div style="font-size:13px;font-weight:800;letter-spacing:.12em;color:#1f2937;'
                     f'border-bottom:2px solid #1f2937;padding-bottom:6px;">📦 {conta.upper()} <span style="font-weight:400;color:#6b7280;">· {len(do)} oportunidade(s)</span></div></td></tr>')
        corpo += [_card(o) for o in do]
    for conta, e in erros.items():
        corpo.append(f'<tr><td style="padding:10px 14px;background:#fee2e2;border-radius:8px;color:#991b1b;font-size:13px;">❌ <b>{conta.upper()}</b> — erro na verificação ({escape(e)}). Conta <b>NÃO</b> analisada nesta rodada.</td></tr>')

    if baixa:
        lis = "".join(f'<li style="margin:4px 0;"><b>{escape(b["conta"].upper())}</b> · <span style="font-family:Consolas,monospace;">{escape(b["mlb"])}</span> · '
                      f'{escape(b["titulo"][:60])} · {escape(b["nome"])} · {escape(brl(b["atual_preco"]))} → {escape(brl(b["preco"]))} · '
                      f'{escape(ganho_fmt(b["ganho_un"]))}/un · <b>{escape(b["motivo_baixa"])}</b></li>' for b in baixa)
        corpo.append(f'<tr><td style="padding:18px 0 10px 0;"><div style="font-size:13px;font-weight:800;letter-spacing:.12em;color:#6b7280;'
                     f'border-bottom:2px solid #9ca3af;padding-bottom:6px;">⬇️ BAIXA PRIORIDADE <span style="font-weight:400;">· {len(baixa)}</span></div>'
                     f'<ul style="margin:8px 0 0 0;padding-left:18px;font-size:13px;color:#374151;">{lis}</ul></td></tr>')
    rec = []
    for n, titulo, itens in recomendacao(novas):
        nv = NIVEIS[n]
        lis = "".join(f'<li style="margin:4px 0;">{escape(i)}</li>' for i in itens)
        rec.append(f'<div style="background:{nv["fundo"]};border-radius:8px;padding:12px 16px;margin-bottom:10px;">'
                   f'<div style="font-size:12px;font-weight:800;letter-spacing:.08em;color:{nv["cor"]};">{nv["emoji"]} {titulo}</div>'
                   f'<ul style="margin:6px 0 0 0;padding-left:18px;font-size:13px;color:#1f2937;">{lis}</ul></div>')

    abertas_txt = ('<div style="font-size:12px;color:#6b7280;margin-top:10px;">📌 Sugestões seguem em todo relatório até a campanha '
                   'entrar em vigor ou o anúncio ser inscrito.</div>')
    return f'''<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:'Segoe UI',Arial,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:20px 10px;">
 <table role="presentation" width="640" cellpadding="0" cellspacing="0" style="max-width:640px;width:100%;">
  <tr><td style="background:#1f2937;border-radius:10px 10px 0 0;padding:22px 24px;">
    <div style="font-size:11px;letter-spacing:.18em;color:#9ca3af;font-weight:700;">MZ APP · RENTABILIDADE</div>
    <div style="font-size:24px;font-weight:800;color:#ffffff;margin-top:4px;">🔔 REBOT DISPONÍVEL</div>
    <div style="font-size:13px;color:#d1d5db;margin-top:4px;">{escape(quando)}</div>
  </td></tr>
  <tr><td style="background:#ffffff;border:1px solid #e5e7eb;border-top:0;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
      {kpi(len(novas), "oportunidades" + _origem(novas), "#111827")}
      {kpi(brl(total).replace("R$ ", "R$&nbsp;"), "potencial / mês", "#166534")}
      {kpi(contagem["OK"], "🟢 recomendadas", "#166534")}
      {kpi(contagem["ATENC"], "🟡 a avaliar", "#854d0e")}
    </tr></table>
  </td></tr>
  <tr><td style="background:#f3f4f6;padding:4px 0 0 0;">
   <table role="presentation" width="100%" cellpadding="0" cellspacing="0">{"".join(corpo)}</table>
  </td></tr>
  <tr><td style="background:#ffffff;border:1px solid #e5e7eb;border-radius:10px;padding:18px 20px;margin-top:10px;">
    <div style="font-size:14px;font-weight:800;letter-spacing:.1em;color:#1f2937;margin-bottom:10px;">💡 RECOMENDAÇÃO</div>
    {"".join(rec)}
    {abertas_txt}
  </td></tr>
  <tr><td align="center" style="padding:14px 0;font-size:11px;color:#9ca3af;">🔒 Somente recomendação: nada foi alterado no Mercado Livre.</td></tr>
 </table>
</td></tr></table></body></html>'''
