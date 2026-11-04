"""Cancelamentos no Bling: pacote já postado não se resolve só cancelando — sem rede."""
import unittest

from marketplaces.expedicao import cancelamentos as c


class TestRisco(unittest.TestCase):
    def test_situacao_fisica(self):
        self.assertEqual(c.situacao_fisica("shipped", "out_for_delivery"), "postado")
        self.assertEqual(c.situacao_fisica("delivered", None), "postado")
        self.assertEqual(c.situacao_fisica("ready_to_ship", "in_hub"), "postado")
        self.assertEqual(c.situacao_fisica("ready_to_ship", "in_packing_list"), "postado")
        self.assertEqual(c.situacao_fisica("ready_to_ship", "dropped_off"), "postado")
        self.assertEqual(c.situacao_fisica("ready_to_ship", "printed"), "no_galpao")
        self.assertEqual(c.situacao_fisica("pending", "buffered"), "no_galpao")
        self.assertEqual(c.situacao_fisica("cancelled", None), "envio_cancelado")
        self.assertEqual(c.situacao_fisica(None, None), "no_galpao")

    def test_postado_nao_diz_apenas_cancelar(self):
        risco, acao = c.acao_e_risco("postado", c.SITUACAO_VERIFICADO, False)
        self.assertEqual(risco, "postado")
        self.assertIn("CANCELADO PELO COMPRADOR NO TRAJETO", acao)
        self.assertIn("NÃO o faz voltar", acao)
        self.assertIn("só quando chegar ao galpão", acao)

    def test_entregue_e_devolucao_tem_texto_proprio(self):
        _, ent = c.acao_e_risco("postado", c.SITUACAO_VERIFICADO, False, "delivered", None)
        self.assertIn("O PACOTE FOI ENTREGUE", ent)
        self.assertIn("NÃO traz o produto de volta", ent)
        _, dev = c.acao_e_risco("postado", c.SITUACAO_VERIFICADO, False, "not_delivered", "returned")
        self.assertIn("VOLTANDO ao vendedor", dev)
        self.assertIn("só quando ele chegar", dev)

    def test_postado_com_nf_lembra_da_nf(self):
        _, acao = c.acao_e_risco("postado", 9, False)
        self.assertIn("NF", acao)

    def test_verificado_no_galpao_manda_recolher_o_pacote_antes(self):
        risco, acao = c.acao_e_risco("no_galpao", c.SITUACAO_VERIFICADO, False)
        self.assertEqual(risco, "verificado")
        self.assertIn("devolver o produto ao estoque", acao)

    def test_simples_continua_como_antes(self):
        self.assertEqual(c.acao_e_risco("no_galpao", 6, False), ("simples", "cancelar o pedido no Bling"))
        self.assertEqual(c.acao_e_risco("no_galpao", 9, False)[1], "cancelar a NF e o pedido no Bling")

    def test_pacote_com_outro_pedido_ativo_so_ajusta_itens(self):
        risco, acao = c.acao_e_risco("postado", c.SITUACAO_VERIFICADO, True)
        self.assertEqual(risco, "simples")
        self.assertIn("ajustar os itens", acao)


if __name__ == "__main__":
    unittest.main()
