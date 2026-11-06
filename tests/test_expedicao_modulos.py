"""Cartões do monitor (Flex, Coleta, Places por conta, DBA, Shopee) e por modalidade (Expedição) — sem rede."""
import unittest
from datetime import date, datetime, timedelta, timezone

from marketplaces.expedicao import cortes, dia as d, simulacao

BRT = timezone(timedelta(hours=-3))
DIA = date(2026, 11, 5)


def em(h, m=0):
    return datetime(2026, 11, 5, h, m, tzinfo=BRT)


def linha(conta, canal, meta, checkout=False, un=1, **extra):
    return dict({"conta": conta, "canal": canal, "envio": f"{conta}-{canal}-{meta}-{checkout}-{un}", "unidades": un, "meta": meta,
                 "prazo_ml": meta, "bling_checkout": checkout, "pedido": 1, "status": "ready_to_ship"}, **extra)


def mods(linhas, agora=None, plat=None, marcas=None):
    r = d.montar(linhas, marcas or {}, [], agora or em(9), DIA, plat if plat is not None else cortes.carregar())
    return {m["id"]: m for m in r["modulos"]}


class TestModulos(unittest.TestCase):
    def test_sete_cartoes_na_ordem_do_monitor(self):
        r = d.montar([], {}, [], em(9), DIA, cortes.carregar())
        self.assertEqual([m["id"] for m in r["modulos"]],
                         ["flex", "coleta", "places_meli02", "places_meli03", "dba", "shopee_xpress", "shopee_direta"])
        self.assertEqual([m["id"] for m in r["modalidades"]], ["flex", "places", "coleta", "dba", "shopee_xpress", "shopee_direta"])
        self.assertEqual(r["modalidades"][-1]["nome"], "Shopee Entrega Direta")

    def test_places_separa_por_conta_com_corte_proprio(self):
        m = mods([linha("meli02", "Agência", "2026-11-05T13:30:00-03:00"), linha("meli02", "Agência", "2026-11-05T13:30:00-03:00", True),
                  linha("meli03", "Agência", "2026-11-05T16:30:00-03:00")])
        self.assertEqual((m["places_meli02"]["pedidos"], m["places_meli02"]["prontos"], m["places_meli02"]["pendentes"]), (2, 1, 1))
        self.assertEqual(m["places_meli02"]["marcos"][0]["hhmm"], "13:30")
        self.assertEqual(m["places_meli03"]["marcos"][0]["hhmm"], "16:30")

    def test_modalidade_places_soma_as_contas_uma_vez_so(self):
        ls = [linha("meli02", "Agência", "2026-11-05T13:30:00-03:00"), linha("meli02", "Agência", "2026-11-05T13:30:00-03:00", True),
              linha("meli03", "Agência", "2026-11-05T16:30:00-03:00")]
        r = d.montar(ls, {}, [], em(9), DIA, cortes.carregar())
        p = [m for m in r["modalidades"] if m["id"].startswith("places")]
        self.assertEqual(len(p), 1)
        self.assertEqual((p[0]["pedidos"], p[0]["prontos"], p[0]["pendentes"], p[0]["plataforma"]), (3, 1, 2, "Mercado Livre"))
        self.assertEqual(p[0]["por_conta"], [{"conta": "meli02", "pendentes": 1, "feitos": 1}, {"conta": "meli03", "pendentes": 1, "feitos": 0}])

    def test_horario_principal_e_o_primeiro_com_pendencia(self):
        m = mods([linha("meli02", "Agência", "2026-11-05T13:30:00-03:00", True), linha("meli02", "Agência", "2026-11-05T16:30:00-03:00")])
        p = m["places_meli02"]
        self.assertEqual(p["marcos"][p["principal"]]["hhmm"], "16:30")     # o das 13:30 já está todo pronto

    def test_tudo_pronto_mantem_o_horario_e_zera_pendencias(self):
        m = mods([linha("meli01", "Coleta", "2026-11-05T13:30:00-03:00", True)])
        c = m["coleta"]
        self.assertEqual((c["pendentes"], c["percentual"]), (0, 100.0))
        self.assertEqual(c["marcos"][c["principal"]]["hhmm"], "13:30")

    def test_atrasado_quando_o_corte_passou_com_pendentes(self):
        m = mods([linha("meli01", "Flex", "2026-11-05T12:00:00-03:00")], agora=em(12, 30))
        self.assertEqual(m["flex"]["atrasados"], 1)

    def test_dba_tem_dois_horarios_etiqueta_e_ponto(self):
        dba = [linha("amazon02", "DBA", "2026-11-05T11:00:00-03:00", False, no_ponto=False),
               linha("amazon02", "DBA", "2026-11-05T11:00:00-03:00", True, no_ponto=False)]
        m = mods(dba)["dba"]
        self.assertEqual([x["hhmm"] for x in m["marcos"]], ["11:00", "14:00"])
        self.assertEqual(m["marcos"][0]["pendentes"], 1)       # etiqueta pendente
        self.assertEqual(m["marcos"][1]["pendentes"], 2)       # nenhum chegou ao ponto ainda
        self.assertEqual(m["no_ponto"], 0)

    def test_shopee_sem_dados_mas_com_horario_de_corte(self):
        m = mods([])
        for k in ("shopee_xpress", "shopee_direta"):
            self.assertTrue(m[k]["sem_dados"])
            self.assertIsNone(m[k]["pedidos"])
            self.assertEqual(m[k]["marcos"][0]["hhmm"], "12:00")
            self.assertIsNotNone(m[k]["principal"])

    def test_shopee_lida_do_bling_separa_por_modalidade(self):
        ls = [linha("shopee", "Shopee Xpress", "2026-11-05T12:00:00-03:00"), linha("shopee", "Shopee Xpress", "2026-11-05T12:00:00-03:00", True, un=2),
              linha("shopee", "Entrega Direta", "2026-11-05T12:00:00-03:00")]
        m = mods(ls)
        self.assertEqual((m["shopee_xpress"]["pendentes"], m["shopee_xpress"]["prontos"], m["shopee_xpress"]["plataforma"]), (1, 1, "Shopee"))
        self.assertEqual((m["shopee_direta"]["pendentes"], m["shopee_direta"]["prontos"], m["shopee_direta"]["sem_dados"]), (1, 0, False))
        self.assertEqual(m["dba"]["plataforma"], "Amazon")

    def test_shopee_com_pedido_em_uma_modalidade_a_outra_mostra_zero(self):
        m = mods([linha("shopee", "Shopee Xpress", "2026-11-05T12:00:00-03:00")])
        self.assertEqual((m["shopee_direta"]["sem_dados"], m["shopee_direta"]["pedidos"]), (False, 0))

    def test_shopee_zera_depois_do_corte(self):
        antes, depois = mods([], agora=em(11)), mods([], agora=em(12, 30))
        for k in ("shopee_xpress", "shopee_direta"):
            self.assertIsNone(antes[k]["marcos"][0]["pendentes"])            # antes do corte: contagem regressiva
            self.assertEqual(depois[k]["marcos"][0]["pendentes"], 0)         # depois: zerado, não atrasado
            self.assertEqual(depois[k]["atrasados"], 0)

    def test_simulacao_alimenta_todos_os_cartoes(self):
        linhas, marc = simulacao.pedidos(DIA, em(5))
        r = d.montar(linhas, marc, [], em(5), DIA, cortes.carregar())
        por = {m["id"]: m for m in r["modulos"]}
        self.assertGreater(por["flex"]["pedidos"], 0)
        self.assertGreater(por["places_meli02"]["pedidos"], 0)
        self.assertGreater(por["dba"]["pedidos"], 0)


if __name__ == "__main__":
    unittest.main()
