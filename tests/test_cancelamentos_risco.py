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

    def test_postado_sem_dados_de_devolucao_nao_diz_apenas_cancelar(self):
        risco, acao = c.acao_e_risco("postado", c.SITUACAO_VERIFICADO, False, "shipped", "out_for_delivery")
        self.assertEqual(risco, "postado")
        self.assertIn("NÃO o faz voltar", acao)
        self.assertIn("só quando chegar ao galpão", acao)

    def test_entregue_so_vira_caso_de_sac_se_nao_ha_devolucao(self):
        _, sem = c.acao_e_risco("postado", c.SITUACAO_VERIFICADO, False, "delivered", None)
        self.assertIn("SEM DEVOLUÇÃO REGISTRADA", sem)
        self.assertIn("SAC", sem)

    def test_logistica_reversa_mostra_o_estado_da_devolucao(self):
        def mk(st):
            return {"envio_status": st, "rastreio": "MEL1", "status": "shipped"}
        _, chegou = c.acao_e_risco("postado", 24, False, "delivered", None, mk("delivered"))
        self.assertIn("DEVOLUÇÃO JÁ CHEGOU", chegou)
        self.assertIn("MEL1", chegou)
        _, caminho = c.acao_e_risco("postado", 24, False, "delivered", None, mk("shipped"))
        self.assertIn("DEVOLUÇÃO A CAMINHO", caminho)
        _, criada = c.acao_e_risco("postado", 24, False, "delivered", None, mk("ready_to_ship"))
        self.assertIn("ainda não postou", criada)
        self.assertNotIn("SAC", criada)
        _, falhou = c.acao_e_risco("postado", 24, False, "delivered", None, mk("cancelled"))
        self.assertIn("SAC", falhou)

    def test_envio_nao_entregue_volta_ao_vendedor(self):
        _, a = c.acao_e_risco("postado", 24, False, "not_delivered", "returned")
        self.assertIn("VOLTANDO ao vendedor", a)

    def test_motivo_do_cancelamento(self):
        self.assertIn("mediação", c.motivo_cancelamento({"group": "mediations", "code": "mediations"}))
        self.assertEqual(c.motivo_cancelamento({"group": "shipment", "code": "shipment_not_delivered"}), "envio não entregue")

    def test_resumo_da_devolucao(self):
        r = c.resumo_devolucao({"status": "shipped", "subtype": "return_total", "status_money": "refunded",
                                "shipments": [{"status": "delivered", "tracking_number": "X"}]})
        self.assertEqual((r["envio_status"], r["rastreio"], r["tipo"]), ("delivered", "X", "return_total"))

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
