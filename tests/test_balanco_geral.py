"""Relatório de Balanço Geral: lista fixa com zerados, produto novo com ESTOQUE no nome e falha de leitura. Sem rede."""
import unittest
from unittest import mock

from marketplaces.balanco import geral


class TestBalancoGeral(unittest.TestCase):
    def _montar(self, saldos=None, erro=None, cat=None):
        ler = mock.Mock(side_effect=erro) if erro else mock.Mock(return_value={"saldos": saldos or {}})
        with mock.patch("marketplaces.bling.estoque.saldos", ler), mock.patch("marketplaces.balanco.catalogo.ler", lambda: cat or {}):
            return geral.montar("bling02")

    def test_lista_tem_os_de_fora_e_nao_repete_sku(self):
        skus = [x[1] for x in geral.LISTA]
        self.assertEqual(len(skus), 48)
        self.assertEqual(len(set(skus)), 48)
        self.assertIn("IMPRESS-TERMICABRANCA", skus)
        self.assertIn("KIT-ESCOLAR-MENINA", skus)

    def test_saldo_do_bling_pedido_e_zerado_continua_na_folha(self):
        r = self._montar({"GAR-DESF-PRETO": 971, "GANCHO-TRANSPARENTE": -8})
        d = {x["sku"]: x for x in r["linhas"]}
        self.assertEqual(len(r["linhas"]), 48)
        self.assertEqual(d["GAR-DESF-PRETO"]["estoque"], 971)
        self.assertEqual(d["GANCHO-TRANSPARENTE"]["estoque"], -8)
        self.assertIsNone(d["FRUT-ARAMBRC"]["estoque"])
        self.assertEqual(r["total"], 963)

    def test_produto_novo_com_estoque_no_nome_entra_sozinho(self):
        cat = {"blings": {"bling03": {"skus": {"novo-x": {"nome": "NOVO - ESTOQUE", "categoria": "Teste"}, "OUTRO": {"nome": "Sem a palavra"}}}}}
        d = {x["sku"]: x for x in self._montar(cat=cat)["linhas"]}
        self.assertEqual(d["NOVO-X"]["categoria"], "Teste")
        self.assertNotIn("OUTRO", d)

    def test_falha_de_leitura_vira_erro_e_nao_zero(self):
        r = self._montar(erro=RuntimeError("401"))
        self.assertEqual(r["erro"], "401")

    def test_bling_desconhecido_e_recusado(self):
        with self.assertRaises(ValueError):
            geral.montar("bling09")


if __name__ == "__main__":
    unittest.main()
