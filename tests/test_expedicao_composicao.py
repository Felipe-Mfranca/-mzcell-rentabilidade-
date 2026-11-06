"""Composição dos kits na lista de montagem: catálogo simulado, sem rede."""
import unittest
from unittest import mock

from marketplaces.expedicao import composicao as c


def cat(**blings):
    return {"blings": blings}


class Anexar(unittest.TestCase):
    def rodar(self, sku, conta="meli01", catalogo=None):
        linha = {"conta": conta, "itens": [{"sku": sku, "qtd": 2}]}
        with mock.patch.object(c.catalogo, "ler", lambda: catalogo):
            saida = c.anexar([linha])
        self.assertEqual(linha, {"conta": conta, "itens": [{"sku": sku, "qtd": 2}]})   # não mexe na linha do cache
        return saida[0]["itens"][0]

    def test_kit_traz_o_que_leva(self):
        k = cat(bling01={"kits": {"AFIADOR-GARRA": {"comp": [["AFI", 1], ["GAR", 2]]}}})
        self.assertEqual(self.rodar("afiador-garra", catalogo=k)["comp"], [{"sku": "AFI", "qtd": 1}, {"sku": "GAR", "qtd": 2}])

    def test_usa_o_bling_da_conta_e_na_falta_outro(self):
        k = cat(bling01={"kits": {"K": {"comp": [["A", 2]]}, "SO1": {"comp": [["S", 3]]}}}, bling03={"kits": {"K": {"comp": [["B", 2]]}}})
        self.assertEqual(self.rodar("K", "meli03", k)["comp"][0]["sku"], "B")
        self.assertEqual(self.rodar("SO1", "meli03", k)["comp"][0]["sku"], "S")

    def test_clone_simples_e_unitario_nao_detalham(self):
        k = cat(bling01={"kits": {"CLONE": {"comp": [["A", 1]]}, "KIT130ENF-SORTIDO": {"comp": [["A", 5]]}, "VAZIO": {"comp": []}}})
        for sku in ("CLONE", "KIT130ENF-SORTIDO", "VAZIO", "NAO-EXISTE", None):
            self.assertNotIn("comp", self.rodar(sku, catalogo=k))

    def test_sem_catalogo_a_lista_segue(self):
        linhas = [{"conta": "meli01", "itens": [{"sku": "K", "qtd": 1}]}]
        with mock.patch.object(c.catalogo, "ler", side_effect=OSError("sem arquivo")):
            self.assertIs(c.anexar(linhas), linhas)
        with mock.patch.object(c.catalogo, "ler", lambda: {}):
            self.assertEqual(c.anexar(linhas), linhas)


if __name__ == "__main__":
    unittest.main()
