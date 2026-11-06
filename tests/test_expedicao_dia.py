"""Testes do painel do dia da Expedição e da simulação (dados fictícios, sem rede)."""
import unittest
from datetime import date, datetime, timedelta

from marketplaces.expedicao import dia as d
from marketplaces.expedicao import simulacao as s

DIA = date(2026, 11, 4)


def em(h, m=0):
    return datetime(2026, 11, 4, h, m, tzinfo=d.BRT)


def painel(agora):
    linhas, marcas = s.pedidos(DIA, agora)
    return d.montar(linhas, marcas, s.envios_full(DIA, agora), agora, DIA)


class TestSemCheckout(unittest.TestCase):
    def linha(self, envio, h, **extra):
        return dict({"conta": "meli01", "canal": "Flex", "envio": envio, "pedido": envio, "unidades": 1, "bling_numero": 66341,
                     "bling_situacao": "Em aberto", "meta": em(h).isoformat(), "itens": [{"sku": "KIT", "qtd": 1}]}, **extra)

    def test_lista_bate_com_os_pendentes(self):
        linhas = [self.linha(1, 15), self.linha(2, 12), self.linha(3, 12, bling_checkout=True), self.linha(4, 12)]
        p = d.montar(linhas, {"4": {"checkout": True}}, [], em(13), DIA)
        ls = p["sem_checkout"]
        self.assertEqual([x["envio"] for x in ls], [2, 1])                # meta mais antiga primeiro; com checkout fica de fora
        self.assertEqual(len(ls), p["geral"]["pendentes"])
        self.assertEqual([x["atrasado"] for x in ls], [True, False])
        self.assertEqual(ls[0]["bling_numero"], 66341)
        self.assertEqual(ls[0]["itens"], [{"sku": "KIT", "qtd": 1}])

    def test_dia_anterior_vem_marcado_e_sem_itens_nao_quebra(self):
        ontem = self.linha(9, 12, meta="2026-11-03T12:00:00-03:00", canal="DBA")
        ontem["itens"] = []
        ls = d.montar([ontem], {}, [], em(13), DIA)["sem_checkout"]
        self.assertEqual(len(ls), 1)
        self.assertTrue(ls[0]["herdado"])
        self.assertEqual(ls[0]["itens"], [])

    def test_postado_conclui_mesmo_sem_checkout(self):
        for sub in ("in_hub", "in_packing_list", "dropped_off", "picked_up"):
            p = d.montar([self.linha(1, 12, status="ready_to_ship", substatus=sub)], {}, [], em(13), DIA)
            self.assertEqual(p["sem_checkout"], [], sub)
            self.assertEqual((p["geral"]["pedidos"], p["geral"]["pendentes"], p["geral"]["atrasados"]), (1, 0, 0), sub)

    def test_impresso_segue_pendente(self):
        p = d.montar([self.linha(1, 12, status="ready_to_ship", substatus="printed")], {}, [], em(13), DIA)
        self.assertEqual(len(p["sem_checkout"]), 1)

    def test_resumo_da_tv_so_tem_totais(self):
        linhas = [self.linha(1, 12), self.linha(2, 15), self.linha(3, 15, canal="Coleta", conta="meli02"), self.linha(4, 15, bling_checkout=True)]
        r = d.montar(linhas, {}, [], em(13), DIA)["sem_checkout_resumo"]
        self.assertEqual(r, [{"canal": "Flex", "conta": "meli01", "pedidos": 2, "atrasados": 1},
                             {"canal": "Coleta", "conta": "meli02", "pedidos": 1, "atrasados": 0}])

    def test_tudo_com_checkout_lista_vazia(self):
        self.assertEqual(d.montar([self.linha(1, 12, bling_checkout=True)], {}, [], em(13), DIA)["sem_checkout"], [])


class TestFullNoMonitor(unittest.TestCase):
    def full(self, numero, **extra):
        return dict({"chave": numero, "canal": "meli", "numero": numero, "conta": "meli01", "coleta": DIA.isoformat(), "prazo": em(17).isoformat(),
                     "produtos": [{"sku": "A", "unidades": 1, "etapas": {}}]}, **extra)

    def numeros(self, *envios):
        return [f["numero"] for f in d.montar([], {}, list(envios), em(13), DIA)["full"]]

    def test_concluido_so_no_dia_em_que_terminou(self):
        ontem = (DIA - timedelta(days=1)).isoformat()
        self.assertEqual(self.numeros(self.full("hoje", concluido_em=DIA.isoformat() + "T10:00:00-03:00"),
                                      self.full("ontem", concluido_em=ontem + "T16:00:00-03:00")), ["hoje"])

    def test_iniciado_e_nao_concluido_aparece_mesmo_de_outro_dia(self):
        antigo = (DIA - timedelta(days=3)).isoformat()
        self.assertEqual(self.numeros(self.full("velho", coleta=antigo, prazo=antigo + "T17:00:00-03:00", status="montagem")), ["velho"])

    def test_fila_so_do_dia_ou_vencida(self):
        depois = (DIA + timedelta(days=3)).isoformat()
        antes = (DIA - timedelta(days=1)).isoformat()
        self.assertEqual(self.numeros(self.full("futuro", coleta=depois, prazo=depois + "T17:00:00-03:00", status="fila"),
                                      self.full("vencido", coleta=antes, prazo=antes + "T17:00:00-03:00", status="fila"),
                                      self.full("do_dia", status="fila")), ["vencido", "do_dia"])


class TestSimulacao(unittest.TestCase):
    def test_volumes_do_plano(self):
        p = painel(em(5))
        por = {c["canal"]: c["pedidos"] for c in p["cartoes"]}
        self.assertEqual(por, {"Flex": 52, "Coleta": 16, "Places": 24, "DBA": 10})
        self.assertEqual(p["geral"]["pedidos"], 102)

    def test_determinismo(self):
        self.assertEqual(painel(em(10)), painel(em(10)))

    def test_cedo_nada_feito_e_fim_do_dia_tudo_feito(self):
        cedo, tarde = painel(em(5)), painel(em(22))
        self.assertEqual(cedo["geral"]["checkout"], 0)
        self.assertEqual(tarde["geral"]["checkout"], 102)
        self.assertEqual(tarde["geral"]["atrasados"], 0)

    def test_progresso_nunca_diminui(self):
        anterior = -1
        for h in range(5, 21):
            atual = painel(em(h))["geral"]["checkout"]
            self.assertGreaterEqual(atual, anterior)
            anterior = atual

    def test_atraso_aparece_depois_da_meta(self):
        # ~7% terminam depois da meta: ao meio-dia pode haver Flex atrasado; o alerta crítico acompanha
        p = painel(em(12, 5))
        flex = next(c for c in p["cartoes"] if c["canal"] == "Flex")
        if flex["atrasados"]:
            self.assertTrue(any(a["nivel"] == "critico" and "Flex" in a["texto"] for a in p["alertas"]))

    def test_full_segue_o_relogio(self):
        manha = {f["numero"]: f for f in painel(em(7, 0))["full"]}
        meio = {f["numero"]: f for f in painel(em(9, 0))["full"]}
        fim = {f["numero"]: f for f in painel(em(11, 0))["full"]}
        self.assertEqual(set(manha), {"80429611", "FBA19VTQKGBW"})
        self.assertEqual(manha["80429611"]["percentual"], 0.0)
        self.assertTrue(meio["FBA19VTQKGBW"]["concluido"])      # Amazon fica pronta 08:15
        self.assertFalse(meio["80429611"]["concluido"])
        self.assertTrue(fim["80429611"]["concluido"])
        self.assertFalse(fim["80429611"]["atrasado"])

    def test_nada_e_gravado(self):
        # a simulação só devolve dados em memória; chamar não cria arquivo
        import os
        antes = set(os.listdir("."))
        painel(em(9))
        self.assertEqual(antes, set(os.listdir(".")))


class TestCortes(unittest.TestCase):
    def test_cortes_do_dia(self):
        p = painel(em(11, 30))
        cortes = {(c["canal"], c["corte"]): c for c in p["cortes"]}
        self.assertIn(("Flex", "12:00"), cortes)
        self.assertEqual(cortes[("Flex", "12:00")]["minutos"], 30)
        self.assertIn(("Coleta", "15:00"), cortes)
        self.assertEqual(sum(c["pedidos"] for c in p["cortes"]), p["geral"]["pedidos"])
        self.assertEqual(sum(c["pendentes"] for c in p["cortes"]), p["geral"]["pendentes"])
        self.assertEqual([c["corte"] for c in p["cortes"]], sorted(c["corte"] for c in p["cortes"]))

    def test_full_unidades_restantes(self):
        p = painel(em(7, 0))
        self.assertEqual(p["geral"]["full_unidades_restantes"], 480)


class TestCortesPlataformas(unittest.TestCase):
    def test_padrao_do_zicri(self):
        from marketplaces.expedicao import cortes
        mapa = {(c["canal"], c["etapa"]): c["corte"] for c in cortes.PADRAO}
        self.assertEqual(mapa[("DBA", "Impressão das etiquetas")], "11:00")
        self.assertEqual(mapa[("DBA", "Entrega no ponto de coleta")], "14:00")
        self.assertEqual(mapa[("Entrega Direta", "Corte do dia")], "12:00")
        self.assertEqual(mapa[("Shopee Xpress", "Corte do dia")], "12:00")

    def test_montar_com_plataformas(self):
        from marketplaces.expedicao import cortes
        r = d.montar([], {}, [], em(10, 30), DIA, cortes.carregar())
        por = {(c["canal"], c["etapa"]): c["minutos"] for c in r["cortes_plataformas"]}
        self.assertEqual(por[("DBA", "Impressão das etiquetas")], 30)
        self.assertEqual(por[("DBA", "Entrega no ponto de coleta")], 210)
        self.assertEqual(por[("Shopee Xpress", "Corte do dia")], 90)

    def test_arquivo_invalido_usa_padrao(self):
        import os, tempfile
        from marketplaces.expedicao import cortes
        orig = cortes.ARQUIVO
        with tempfile.TemporaryDirectory() as tmp:
            cortes.ARQUIVO = os.path.join(tmp, "c.json")
            open(cortes.ARQUIVO, "w").write('[{"canal": "X", "corte": "99:99"}]')
            try:
                self.assertEqual(cortes.carregar(), cortes.PADRAO)
            finally:
                cortes.ARQUIVO = orig


class TestDBA(unittest.TestCase):
    BASE = {"AmazonOrderId": "702-1", "OrderStatus": "Unshipped", "FulfillmentChannel": "MFN",
            "PurchaseDate": "2026-11-04T10:00:00Z", "NumberOfItemsUnshipped": 2, "ShipServiceLevel": "Std EZ Regional"}

    def test_corte_do_pedido(self):
        from marketplaces.expedicao import amazon_dba as a
        antes = datetime(2026, 11, 3, 9, 30, tzinfo=d.BRT)
        depois = datetime(2026, 11, 3, 11, 19, tzinfo=d.BRT)
        self.assertEqual(a.corte_do_pedido(antes)[0].isoformat(), "2026-11-03T11:00:00-03:00")
        self.assertEqual(a.corte_do_pedido(depois)[0].isoformat(), "2026-11-04T11:00:00-03:00")
        self.assertEqual(a.corte_do_pedido(depois)[1].isoformat(), "2026-11-04T14:00:00-03:00")

    def test_linha_dba_estados(self):
        from marketplaces.expedicao import amazon_dba as a
        pend = a.linha("amazon02", dict(self.BASE, EasyShipShipmentStatus="PendingSchedule"))
        self.assertFalse(pend["checkout_auto"])
        self.assertFalse(pend["no_ponto"])
        self.assertEqual(pend["unidades"], 2)
        etiq = a.linha("amazon02", dict(self.BASE, EasyShipShipmentStatus="PendingDropOff"))
        self.assertTrue(etiq["checkout_auto"])
        self.assertFalse(etiq["no_ponto"])
        ponto = a.linha("amazon02", dict(self.BASE, EasyShipShipmentStatus="DroppedOff"))
        self.assertTrue(ponto["checkout_auto"] and ponto["no_ponto"])
        self.assertIsNone(a.linha("amazon02", dict(self.BASE, OrderStatus="Canceled")))
        self.assertIsNone(a.linha("amazon02", dict(self.BASE, FulfillmentChannel="AFN")))

    def test_monitor_conta_dba(self):
        from marketplaces.expedicao import amazon_dba as a
        o = dict(self.BASE, EasyShipShipmentStatus="PendingSchedule")
        r = d.montar([a.linha("amazon01", o)], {}, [], em(9), DIA)
        dba = next(c for c in r["cartoes"] if c["canal"] == "DBA")
        self.assertEqual((dba["pedidos"], dba["pendentes"], dba["no_ponto"]), (1, 1, 0))
        self.assertEqual(dba["minutos_ate_meta"], 120)

    def test_chip_fixo_do_dba_some_quando_ha_pedidos(self):
        from marketplaces.expedicao import amazon_dba as a, cortes
        o = dict(self.BASE, EasyShipShipmentStatus="PendingSchedule")
        com = d.montar([a.linha("amazon01", o)], {}, [], em(9), DIA, cortes.carregar())
        sem = d.montar([], {}, [], em(9), DIA, cortes.carregar())
        etapas = lambda r: [c["etapa"] for c in r["cortes_plataformas"]]  # noqa: E731
        self.assertNotIn("Impressão das etiquetas", etapas(com))
        self.assertIn("Impressão das etiquetas", etapas(sem))
        self.assertIn("Entrega no ponto de coleta", etapas(com))


class TestMontar(unittest.TestCase):
    def test_pedido_de_outro_dia_nao_entra_e_herdado_alerta(self):
        linhas = [{"conta": "meli01", "canal": "Flex", "envio": "1", "unidades": 1, "liberado": True, "bling_checkout": False,
                   "meta": "2026-11-03T12:00:00-03:00"}]
        r = d.montar(linhas, {}, [], em(8), DIA)
        self.assertEqual(r["geral"]["pedidos"], 0)
        self.assertEqual(r["herdados"], 1)
        self.assertTrue(any("anteriores" in a["texto"] for a in r["alertas"]))

    def test_checkout_pelo_bling_conta_como_feito(self):
        linhas = [{"conta": "meli01", "canal": "Agência", "envio": "9", "unidades": 2, "liberado": True, "bling_checkout": True,
                   "meta": "2026-11-04T16:30:00-03:00"}]
        r = d.montar(linhas, {}, [], em(8), DIA)
        places = next(c for c in r["cartoes"] if c["canal"] == "Places")
        self.assertEqual((places["pedidos"], places["checkout"], places["embalados"]), (1, 1, 1))


if __name__ == "__main__":
    unittest.main()
