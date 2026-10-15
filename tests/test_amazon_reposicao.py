"""Reposição FBA da Amazon: regras de sugestão e ordenação (sem rede)."""
import unittest

from marketplaces.amazon import reposicao


def item(disp=0, transito=0, asin="B0TESTE", sku="SKU-FBA"):
    return {"asin": asin, "sku": sku, "titulo": "t", "disponivel": disp, "em_transito": transito,
            "estoque_valido": disp + transito}


def vel(v7=0, v30=0, venda7=0, venda30=0):
    ponderada = round(v7 * 0.4 + v30 * 0.6, 3)
    return {"vendido_7d": venda7, "vendido_30d": venda30, "velocidade_7d": v7,
            "velocidade_30d": v30, "velocidade": ponderada}


class Sugerir(unittest.TestCase):
    def test_repor_para_cobrir_a_meta(self):
        # 2 un/dia x 20 dias = 40; tem 10 -> repor 30
        s = reposicao.sugerir(item(disp=10), vel(2, 2), 20)
        self.assertEqual(s["sugestao"], 30)
        self.assertEqual(s["situacao"], "REPOR")
        self.assertEqual(s["cobertura_dias"], 5.0)

    def test_em_transito_conta_no_estoque_e_nao_reenvia(self):
        s = reposicao.sugerir(item(disp=10, transito=30), vel(2, 2), 20)
        self.assertEqual(s["sugestao"], 0)
        self.assertEqual(s["situacao"], "OK")

    def test_estoque_acima_da_meta_nao_sugere(self):
        self.assertEqual(reposicao.sugerir(item(disp=500), vel(1, 1), 20)["sugestao"], 0)

    def test_arredonda_para_cima(self):
        # 1,5/dia x 20 = 30; tem 0 e nada a caminho -> ruptura, repor 30. Com 0,3/dia: 6.
        self.assertEqual(reposicao.sugerir(item(disp=1), vel(0.33, 0.33), 20)["sugestao"], 6)

    def test_ruptura_e_sem_estoque_a_caminho(self):
        self.assertEqual(reposicao.sugerir(item(), vel(1, 1), 20)["situacao"], "RUPTURA")
        self.assertEqual(reposicao.sugerir(item(transito=50), vel(1, 1), 20)["situacao"], "SEM ESTOQUE (A CAMINHO)")

    def test_sem_venda_nao_sugere_e_nao_tem_cobertura(self):
        s = reposicao.sugerir(item(disp=100), vel(), 20)
        self.assertEqual((s["sugestao"], s["situacao"], s["cobertura_dias"]), (0, "SEM VENDA", None))

    def test_tendencia_7d_sobre_30d(self):
        self.assertEqual(reposicao.sugerir(item(disp=1), vel(3, 2), 20)["tendencia_7d_30d"], 1.5)


class Ordenar(unittest.TestCase):
    def test_mais_urgente_primeiro_e_sem_venda_por_ultimo(self):
        linhas = [reposicao.sugerir(item(disp=100, asin="OK"), vel(1, 1), 20),
                  reposicao.sugerir(item(disp=100, asin="PARADO"), vel(), 20),
                  reposicao.sugerir(item(disp=5, asin="BAIXO"), vel(1, 1), 20),
                  reposicao.sugerir(item(asin="RUPTURA"), vel(1, 1), 20)]
        self.assertEqual([l["asin"] for l in reposicao.ordenar(linhas)], ["RUPTURA", "BAIXO", "OK", "PARADO"])


class CompararAmazon(unittest.TestCase):
    def test_soma_enviados_dos_skus_do_asin_e_junta_alertas(self):
        linhas = [reposicao.sugerir(item(disp=1, asin="B1"), vel(1, 1), 20)]
        plan = [{"asin": "B1", "units-shipped-t7": "10", "units-shipped-t30": "20", "alert": "Low traffic"},
                {"asin": "B1", "units-shipped-t7": "5", "units-shipped-t30": "10", "alert": ""}]
        r = reposicao.comparar_amazon(linhas, plan)[0]
        self.assertEqual((r["amazon_enviado_7d"], r["amazon_enviado_30d"], r["amazon_alerta"]), (15, 30, "Low traffic"))

    def test_asin_fora_do_relatorio_fica_vazio(self):
        r = reposicao.comparar_amazon([reposicao.sugerir(item(asin="B2"), vel(1, 1), 20)], [])[0]
        self.assertIsNone(r["amazon_enviado_30d"])

    def test_campos_de_recomendacao_vazios_nao_viram_zero(self):
        # a Amazon deste marketplace devolve recommended-order-quantity vazio: não pode virar "repor 0"
        r = reposicao.comparar_amazon([reposicao.sugerir(item(asin="B3"), vel(1, 1), 20)],
                                      [{"asin": "B3", "recommended-order-quantity": "", "units-shipped-t30": "7"}])[0]
        self.assertNotIn("amazon_sugestao", r)


if __name__ == "__main__":
    unittest.main()
