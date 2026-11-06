"""
marketplaces/balanco/geral.py — Relatório de Balanço Geral por Bling (somente leitura)
MZ App

Mesmo formato do relatório do Bling 1 (categoria, SKU, descrição, estoque físico e coluna em branco do balanço), para
qualquer um dos três Blings. A lista fixa é a do relatório de 06/11/2026 mais os produtos "ESTOQUE" que estavam de fora,
a pedido do usuário: fica fixa porque o catálogo só guarda quem tem saldo, e zerado também entra no relatório.
Produto novo com ESTOQUE no nome entra sozinho quando aparecer no catálogo.
"""

from marketplaces.balanco import catalogo
from marketplaces.bling import estoque

LISTA = [
    ("Afiador", "AFI-FAC3-VARIADO", "AFIADOR - ESTOQUE COR:VARIADO"),
    ("Caixa Organizadora Roupa", "CXORGROUP-CINZA", "CAIXA ORG. - ESTOQUE Cor:Cinza"),
    ("controle ps4", "CONTROLPS4-PRETO", "CONTROLE - ESTOQUE Cor:Preto"),
    ("Escova limpeza", "ESC-FRES-PRETO", "ESCOVA DE CANTO- ESTOQUE COR:Preto"),
    ("Fruteiras", "FRUT-ARAMBRC", "FRUT. ARAMADA BRANCA - ESTOQUE Cor:Branco"),
    ("Fruteiras", "FRUT-ARMAMZ-PRETO", "FRUT. ARAMADA - ESTOQUE"),
    ("Fruteiras", "FRUT-OND-PRETO", "FRUT. CLINK - ESTOQUE Cor:Preto"),
    ("Gancho de Parede", "GANCHO-TRANSPARENTE", "GANCHO TRANSP. - ESTOQUE"),
    ("Gancho de Parede", "SUPGANCHO-PAREDE", "GANCHO INOX - ESTOQUE"),
    ("Impressoras", "IMPRESS-TERMICA", "IMPRESSORA PRETO 127/220v - ESTOQUE"),
    ("Kit facas", "FAC6-Q3151-PRETO", "FACA 7 Q3151 - ESTOQUE Cor:Preto"),
    ("Kit facas", "FACA6BKZ-ESTQ", "FACA BKZ - ESTOQUE"),
    ("Kit facas", "FACAS-6PCS-PRETO", "FACA 9 - ESTOQUE Cor:Preto"),
    ("Kit facas", "KIT-FACA6-AMADEIRADO", "FACA AMADEIRADA - ESTOQUE Cor:Amadeirado"),
    ("Kit facas", "KIT-FACA6-SHP", "FACA SHOPEE - ESTOQUE"),
    ("Lampada Led", "LAMP-LED-H4", "LAMPADA H4 - ESTOQUE"),
    ("Microfone", "KIT2-MIC-SEMFIO-PRETO", "MIC. KIT 2 - ESTOQUE Kit 2 Microfones:Preto"),
    ("Microfone", "UN-MICSEMFIO-PRETO", "MIC. UNITARIO - ESTOQUE Microfones:Preto"),
    ("Sem categoria", "GAR-DESF-PRETO", "GARRA - ESTOQUE COR:PRETO"),
    ("Sem categoria", "TAB-CARN-MADEIRA", "TÁBUA - ESTOQUE COR:MADEIRA"),
    ("Smartwatch", "PROMO-SMTW-BEGE", "RELÓGIO PEJE BEGE - ESTOQUE"),
    ("Smartwatch", "REL-REDO-ZW26-PRATA", "RELÓGIO ZW26 - ESTOQUE COR:PRATA"),
    ("Smartwatch", "REL-REDO-ZW26-PRETO", "RELÓGIO ZW26 - ESTOQUE COR:PRETO"),
    ("Smartwatch", "REL-REDONDO-PRATA", "RELÓGIO PEJE - ESTOQUE Cor:Prata"),
    ("Smartwatch", "REL-REDONDO-PRETO", "RELÓGIO PEJE - ESTOQUE Cor:Preto"),
    ("Smartwatch", "REL-REDONDO-ROSA", "RELÓGIO PEJE - ESTOQUE Cor:Rosa"),
    ("Smartwatch", "REL-ULTRAS9-BLACK", "RELÓGIO S9 - ESTOQUE Cor:Black"),
    ("Smartwatch", "REL-ULTRAS9-TITANIUM", "RELÓGIO S9 - ESTOQUE Cor:Titanium"),
    ("Smartwatch", "S9MILITAR-PRETO", "RELÓGIO MILITAR - ESTOQUE Cor:PRETO"),
    ("Smartwatch", "S9MILITAR-SORTIDO", "RELÓGIO MILITAR - ESTOQUE Cor:SORTIDO"),
    ("Smartwatch", "SMARTMILITAR-BEGE", "RELÓGIO MILITAR BEGE - ESTOQUE"),
    ("Smartwatch", "SMARTMILITAR-VERDE", "RELÓGIO MILITAR VERDE - ESTOQUE"),
    ("Smartwatch", "ZWESTQ-ROSA", "RELÓGIO ZW26 - ESTOQUE"),
    ("Tapete Anti Derrapante", "TAP-BANHEIRO-SORTIDO", "TAPETE - ESTOQUE Cor:Sortido"),
    ("Torneira", "TORBANC-PRETO", "TORN. BANCADA - ESTOQUE Torneira Flexivel:Preta"),
    ("Torneira", "TORNEIRA-FLEX-PRETA", "TORN. PAREDE - ESTOQUE Torneira Flexivel:Preta"),
    ("Torneira", "TORNPRT-PAREDE-PRATA", "TORN. CROMADA - ESTOQUE Torneira Flexivel:Prata"),
    # De fora do relatório de 06/11/2026; entraram a pedido do usuário.
    ("Arvore de Natal", "ARV-VER-150CM-220GALHOS", "Árvore verde 150cm 220 galhos natal ESTOQUE Cor:Verde"),
    ("Arvore de Natal", "ARV1200G-ESTQ-VM", "Arvore De Natal 2.1M Decor 1200Galhos 2026 ESTOQUE Cor:Vermelho"),
    ("Arvore de Natal", "ARV600G-DEC-VERM-ESTQ", "Arvore De Natal 1.8M decor 600Galhos 2026 ESTOQUE Cor:Vermelho"),
    ("Artigos Sazonais > Bandeirinhas Festa Junina", "BAN10M-ESTQ", "Bandeirinha Festa Junina Plástica 10m - ESTOQUE"),
    ("ENFEITES ARVORE DE NATAL", "KIT130ESTQ", "KIT 130 ENFEITES - ESTOQUE"),
    ("Enfeites para Árvores", "KIT20LAÇO-DR", "KIT 20 LAÇO DOURADO ESTOQUE"),
    ("Enfeites para Árvores", "KIT20LAÇO-VM", "KIT 20 LAÇO VM ESTOQUE"),
    ("Fantasias Completas", "CAPAHALLO-ESTQ", "Capa Com Capuz Fantasia Halloween Terror ESTOQUE"),
    ("Impressoras", "IMPRESS-TERMICABRANCA", "IMPRESSORA BRANCA 127/220v - ESTOQUE"),
    ("Papelaria", "KIT-ESCOLAR-MENINA", "ESCOLAR - ESTOQUE Kit Escolar:Menina"),
    ("Papelaria", "KIT-ESCOLAR-MENINO", "ESCOLAR - ESTOQUE Kit Escolar:Menino"),
]


def montar(bling: str) -> dict:
    """Linhas do relatório de um Bling. Leitura de saldo que falha vira erro na tela: nunca aparece como estoque zero."""
    if bling not in estoque.BLINGS:
        raise ValueError("Bling desconhecido")
    cat = catalogo.ler()
    linhas = {sku: {"categoria": c, "sku": sku, "descricao": d} for c, sku, d in LISTA}
    for dados in (cat.get("blings") or {}).values():
        for sku, x in (dados.get("skus") or {}).items():
            chave = sku.strip().upper()
            if "ESTOQUE" in (x.get("nome") or "").upper() and chave not in linhas:
                linhas[chave] = {"categoria": x.get("categoria") or "Sem categoria", "sku": chave, "descricao": x.get("nome") or ""}
    try:
        saldos, erro = {k.strip().upper(): v for k, v in estoque.saldos(bling)["saldos"].items()}, None
    except Exception as e:
        saldos, erro = {}, str(e)[:200]
    saida = [dict(x, estoque=saldos.get(k)) for k, x in linhas.items()]
    saida.sort(key=lambda x: (x["categoria"].lower(), x["sku"]))
    return {"bling": bling, "erro": erro, "linhas": saida, "total": sum(x["estoque"] or 0 for x in saida)}
